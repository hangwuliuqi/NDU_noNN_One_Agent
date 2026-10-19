"""Orchestrate the sampling LLM → remote Client/Worker HFSS loop."""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path

import numpy as np
from pyhocon import ConfigTree

from .agents import SamplingGuidanceAgent
from .initial_data import import_initial_parquet
from .intervals import normalized_vector, variable_specs_from_config
from .json_utils import dumps_readable
from .llm_client import LlmClient
from .metrics import calculate_ranking_score, evaluate_curve, meets_design_goal
from .models import CandidateProposal, SampleRecord
from .pools import SamplePools
from .run_logging import InteractionLogger
from client_submitter import normalize_job_result, run_simulation_batch


class OptimizationEngine:
    """组织“TOP-K 采样—真实仿真—样本池更新”的完整闭环。"""

    def __init__(
        self,
        conf: ConfigTree,
        root_dir: Path,
        runtime_dir: Path,
        interactions: InteractionLogger,
        logger: logging.Logger,
    ) -> None:
        self.conf = conf
        self.root_dir = root_dir
        self.runtime_dir = runtime_dir
        self.logger = logger

        # 变量范围只从配置树构建一次，随后由样本池、采样 agent 和 HFSS 请求共享。
        self.specs = variable_specs_from_config(conf)

        # 样本池负责持久化完整曲线与按分数排序的摘要。
        self.pools = SamplePools(
            runtime_dir,
            self.specs,
            conf.get_int("optimization.variable_decimal_places"),
            conf.get_int("optimization.summary_pool_size"),
        )
        self.sampling_client = LlmClient("sampling", conf, interactions, logger)
        self.sampling_agent = SamplingGuidanceAgent(conf, self.specs, self.sampling_client, root_dir, logger)

        refreshed = self.pools.refresh_ranking_scores(
            lambda metrics: calculate_ranking_score(
                metrics.s11_at_2_45,
                metrics.s11_at_4_50,
                metrics.low_resonance_point,
                metrics.high_resonance_point,
                self.conf,
            )
        )
        if refreshed:
            self.logger.info("refreshed ranking scores for %d persisted samples", refreshed)

        # Remote Client/Worker is the only evaluator in the production loop.
        # No surrogate model is trained or consulted.

    def validate_and_bootstrap(self) -> int:
        """校验配置，并按启动模式准备样本池。"""
        self._validate_config()
        if not self.conf.get_bool("project.use_initial_parquet"):
            if any(record.source == "initial_parquet" for record in self.pools.records):
                raise ValueError("zero-sample mode cannot resume a pool containing initial Parquet samples; use a new --output-dir")
            self.logger.info("zero-sample mode: skipping initial Parquet; evaluated sample pool has %d resumed samples", len(self.pools.records))
            return 0
        if self.pools.records and not any(record.source == "initial_parquet" for record in self.pools.records):
            raise ValueError("Parquet mode cannot resume a zero-sample pool; use a new --output-dir")
        parquet_raw = Path(self.conf.get_string("project.initial_parquet"))
        parquet = parquet_raw if parquet_raw.is_absolute() else self.root_dir / parquet_raw
        imported = import_initial_parquet(parquet, self.conf, self.specs, self.pools)
        self.logger.info("evaluated sample pool ready: total=%d, newly imported=%d", len(self.pools.records), imported)
        return imported

    def run(self) -> SampleRecord | None:
        """执行优化主循环；成功时返回达标样本，耗尽轮数时返回 ``None``。"""
        self.validate_and_bootstrap()
        self.logger.info("sampling LLM: %s", self.sampling_client.describe())
        self.logger.info("running one-time sampling LLM preflight")
        self.sampling_client.preflight_once(iteration=0)
        max_iterations = self.conf.get_int("optimization.max_iterations")
        completed_iterations = [item.iteration for item in self.pools.records if item.iteration > 0]
        start_iteration = max(completed_iterations, default=0) + 1
        if start_iteration > max_iterations:
            self.logger.warning(
                "no new iteration started because the pool already contains iteration %d and max_iterations=%d",
                start_iteration - 1,
                max_iterations,
            )
            if self.pools.records:
                best = max(self.pools.records, key=lambda item: item.metrics.score)
                self._write_result(best, "max_iterations_reached")
            return None
        for iteration in range(start_iteration, max_iterations + 1):
            self.logger.info("iteration %d/%d started", iteration, max_iterations)

            # 初始数据已经进入完整池和摘要池；每轮只把 TOP-K 摘要发送给采样 LLM。
            top_k = min(self.conf.get_int("optimization.top_k"), len(self.pools.records))
            sampling_input = self.pools.ranked_summaries(
                limit=top_k, exclude_iteration=True
            )
            self.logger.info("sampling guidance input uses top-%d evaluated samples", top_k)
            try:
                proposals = self.sampling_agent.propose(
                    sampling_input,
                    iteration,
                )
            except Exception:
                # Even a malformed LLM response is useful for audit/debugging.
                self._write_candidate_audit(iteration, [])
                raise

            # 第二步：重复候选不再丢弃，而是由代码按变量范围加入随机扰动后继续评估。
            proposals = self._repair_duplicate_proposals(proposals, iteration)
            if not proposals:
                raise RuntimeError("no valid LLM proposals remain after duplicate perturbation")
            # Every repaired LLM candidate is submitted. Client preserves order;
            # Worker returns raw frequency/S11 curves for each task.
            self._write_candidate_audit(iteration, proposals)
            database_url = self.conf.get_string("remote.database_url") or os.getenv(self.conf.get_string("remote.database_url_env"), "")
            if not database_url:
                raise ValueError("remote database URL is empty; configure remote.database_url or its environment variable")
            pending_path = self.runtime_dir / "pending_remote_batch.json"
            pending_payload = {
                "iteration": iteration,
                "simulation_type": self.conf.get_string("remote.simulation_type"),
                "candidates": [proposal.variables for proposal in proposals],
            }
            def journal(task_ids: list[str]) -> None:
                pending_payload["task_ids"] = list(task_ids)
                pending_path.write_text(json.dumps(pending_payload, indent=2), encoding="utf-8")
            task_ids, remote_results = run_simulation_batch(
                database_url,
                [proposal.variables for proposal in proposals],
                simulation_type=self.conf.get_string("remote.simulation_type"),
                priority=self.conf.get_int("remote.priority"),
                max_retries=self.conf.get_int("remote.max_retries"),
                task_timeout_seconds=self.conf.get_int("remote.task_timeout_seconds"),
                wait_timeout_seconds=self.conf.get_float("remote.wait_timeout_seconds"),
                poll_interval_seconds=self.conf.get_float("remote.poll_interval_seconds"),
                progress_interval_seconds=self.conf.get_float("remote.progress_interval_seconds"),
                batch_id=f"llmo-iteration-{iteration:04d}",
                on_submitted=journal,
            )
            if pending_path.exists():
                pending_path.unlink()
            records: list[SampleRecord] = []
            for index, (proposal, task_id) in enumerate(zip(proposals, task_ids), start=1):
                remote = normalize_job_result(remote_results[task_id])
                from .models import CurveData
                curve = CurveData(remote["Frequency_GHz"], remote["S11_dB"])
                metrics = evaluate_curve(curve.frequency_ghz, curve.s11_db, self.conf)
                records.append(SampleRecord(
                    sample_id=f"iteration_{iteration:04d}_{index:02d}",
                    variables=proposal.variables,
                    curve=curve,
                    metrics=metrics,
                    iteration=iteration,
                    source="remote_hfss",
                    metadata={
                        "remote": remote,
                        "candidate_index": proposal.candidate_index,
                        "rationale": proposal.rationale,
                    },
                ))
            self.pools.add_many(records)
            successful = [record for record in records if meets_design_goal(record.metrics, self.conf)]
            if successful:
                best_success = max(successful, key=lambda item: item.metrics.score)
                self.logger.info("design goal satisfied at iteration %d", iteration)
                self._write_result(best_success, "success")
                return best_success
            record = max(records, key=lambda item: item.metrics.score)
            self.logger.info("iteration %d/%d completed; continuing optimization", iteration, max_iterations)
        self.logger.warning("maximum iterations reached without satisfying all stop conditions")
        best = max(self.pools.records, key=lambda item: item.metrics.score)
        self._write_result(best, "max_iterations_reached")
        return None

    def _repair_duplicate_proposals(
        self,
        proposals: list[CandidateProposal],
        iteration: int,
    ) -> list[CandidateProposal]:
        """Keep every candidate and perturb duplicates into a new legal design.

        For each duplicate variable, the code samples ``r ~ U(-1, 1)`` and uses
        ``new = old + r * alpha * (max - min)``. The configurable ``alpha`` is
        ``optimization.duplicate_perturbation_alpha``. Values are rounded and
        clipped to the variable's legal interval, then retried if still duplicate.
        """
        tolerance = self.conf.get_float("optimization.duplicate_tolerance")
        perturbation_alpha = self.conf.get_float(
            "optimization.duplicate_perturbation_alpha"
        )
        max_attempts = self.conf.get_int(
            "optimization.duplicate_perturbation_max_attempts"
        )
        existing = [np.asarray(normalized_vector(item.variables, self.specs)) for item in self.pools.records]
        accepted: list[CandidateProposal] = []
        accepted_vectors: list[np.ndarray] = []
        rng = np.random.default_rng(
            self.conf.get_int("project.seed")
            + iteration
            * self.conf.get_int(
                "optimization.duplicate_perturbation_seed_iteration_stride"
            )
        )
        for proposal in proposals:
            original = dict(proposal.variables)
            proposal.original_variables = original
            vector = np.asarray(normalized_vector(original, self.specs))
            duplicate = any(
                np.max(np.abs(vector - other)) <= tolerance
                for other in existing + accepted_vectors
            )
            proposal.duplicate_with_history_or_round = duplicate
            if duplicate:
                for _ in range(max_attempts):
                    random_numbers = rng.uniform(-1.0, 1.0, size=len(self.specs))
                    perturbed = {
                        name: round(
                            float(
                                np.clip(
                                    original[name]
                                    + random_numbers[index]
                                    * perturbation_alpha
                                    * (spec.maximum - spec.minimum),
                                    spec.minimum,
                                    spec.maximum,
                                )
                            ),
                            self.conf.get_int("optimization.variable_decimal_places"),
                        )
                        for index, (name, spec) in enumerate(self.specs.items())
                    }
                    candidate_vector = np.asarray(normalized_vector(perturbed, self.specs))
                    if not any(
                        np.max(np.abs(candidate_vector - other)) <= tolerance
                        for other in existing + accepted_vectors
                    ):
                        proposal.variables = perturbed
                        proposal.perturbation_random_numbers = {
                            name: float(random_numbers[index])
                            for index, name in enumerate(self.specs)
                        }
                        vector = candidate_vector
                        self.logger.warning(
                            "perturbed duplicate proposal %s into %s",
                            original,
                            perturbed,
                        )
                        break
                else:
                    raise RuntimeError(
                        "could not perturb duplicate proposal after "
                        f"{max_attempts} attempts: {original}"
                    )
            accepted.append(proposal)
            accepted_vectors.append(vector)
        return accepted

    def _write_candidate_audit(
        self,
        iteration: int,
        proposals: list[CandidateProposal],
    ) -> None:
        """Append a visualization/audit record of raw and repaired candidates.

        ``candidate_proposals.json`` is deliberately separate from the real
        sample pool: it may contain invalid or unverified LLM proposals and
        must never be treated as evaluated HFSS evidence.
        """
        path = self.runtime_dir / "candidate_proposals.json"
        raw_by_index = {
            int(item["candidate_index"]): item for item in self.sampling_agent.last_raw_candidates
        }
        rows = []
        for index, raw_audit in raw_by_index.items():
            matched = next((item for item in proposals if item.candidate_index == index), None)
            variables = matched.variables if matched else raw_audit.get("repaired_variables")
            record = {
                "iteration": iteration,
                "candidate_index": index,
                "raw": raw_audit.get("raw"),
                "original_variables": matched.original_variables if matched else None,
                "variables": variables,
                "valid_after_repair": bool(raw_audit.get("valid", False)),
                "out_of_bounds": bool(raw_audit.get("out_of_bounds", False)),
                "duplicate_with_history_or_round": bool(matched.duplicate_with_history_or_round) if matched else False,
                "perturbation_applied": bool(matched and matched.perturbation_random_numbers),
                "perturbation_random_numbers": dict(matched.perturbation_random_numbers) if matched else {},
                "rationale": str(raw_audit.get("raw", {}).get("rationale", "")) if isinstance(raw_audit.get("raw"), dict) else "",
                "error": raw_audit.get("error"),
            }
            rows.append(record)
        history: list[dict[str, object]] = []
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise TypeError("candidate_proposals.json must contain a JSON array")
            history = loaded
        history.extend(rows)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(dumps_readable(history) + "\n", encoding="utf-8")
        temp_path.replace(path)

    def _validate_config(self) -> None:
        """集中检查会破坏框架语义的关键配置，而不只是检查 HOCON 能否解析。"""
        expected = {"a", "b", "l1", "l2", "l3", "s1", "s2", "s3", "f_l"}
        if set(self.specs) != expected:
            raise ValueError(f"antenna.variables must contain exactly {sorted(expected)}")
        if self.conf.get_int("llm.max_connection_attempts") < 1:
            raise ValueError("llm.max_connection_attempts must be positive")
        if self.conf.get_int("optimization.proposals_per_iteration") < 1:
            raise ValueError("optimization.proposals_per_iteration must be at least 1")
        if self.conf.get_int("optimization.variable_decimal_places") != 2:
            raise ValueError(
                "optimization.variable_decimal_places must be 2 for this project"
            )
        perturbation_alpha = self.conf.get_float(
            "optimization.duplicate_perturbation_alpha"
        )
        if not 0.0 < perturbation_alpha <= 1.0:
            raise ValueError(
                "optimization.duplicate_perturbation_alpha must be in (0, 1]"
            )
        if self.conf.get_int("optimization.duplicate_perturbation_max_attempts") < 1:
            raise ValueError(
                "optimization.duplicate_perturbation_max_attempts must be positive"
            )
        if self.conf.get_float("optimization.duplicate_tolerance") < 0:
            raise ValueError("optimization.duplicate_tolerance must be non-negative")
        global_mock_count = self.conf.get_int(
            "optimization.mock_sampling_global_proposal_count"
        )
        if not 0 <= global_mock_count <= self.conf.get_int(
            "optimization.proposals_per_iteration"
        ):
            raise ValueError(
                "optimization.mock_sampling_global_proposal_count must be between "
                "0 and optimization.proposals_per_iteration"
            )
        if self.conf.get_float("optimization.mock_sampling_local_std_fraction") < 0:
            raise ValueError(
                "optimization.mock_sampling_local_std_fraction must be non-negative"
            )
        for key in (
            "candidate_repair_seed_iteration_stride",
            "duplicate_perturbation_seed_iteration_stride",
            "mock_sampling_seed_iteration_stride",
        ):
            if self.conf.get_int(f"optimization.{key}") < 1:
                raise ValueError(f"optimization.{key} must be positive")
        if self.conf.get_int("optimization.top_k") < 1:
            raise ValueError("optimization.top_k must be positive")
        summary_pool_size = self.conf.get_int("optimization.summary_pool_size")
        if summary_pool_size < 1:
            raise ValueError("optimization.summary_pool_size must be positive")
        if summary_pool_size < self.conf.get_int("optimization.top_k"):
            raise ValueError(
                "optimization.summary_pool_size must be at least optimization.top_k "
            )
        simulation_mode = self.conf.get_string("simulation.mode").lower()
        if simulation_mode not in {"remote", "pyaedt", "manual", "command", "mock"}:
            raise ValueError(f"unsupported simulation.mode={simulation_mode!r}")
        if simulation_mode == "pyaedt":
            if not self.conf.get_string("simulation.pyaedt.aedt_version").strip():
                raise ValueError("simulation.pyaedt.aedt_version must be non-empty")
        provider = self.conf.get_string("llm.sampling.provider").lower()
        if provider not in {"openai", "deepseek", "qwen", "mock"}:
            raise ValueError(f"unsupported llm.sampling.provider={provider!r}")
        if provider != "mock" and not self.conf.get_string("llm.sampling.base_url").strip():
            raise ValueError("llm.sampling.base_url must be non-empty")
        if self.conf.get_float("score.excellent_s11_db") >= 0:
            raise ValueError("score.excellent_s11_db must be negative")
        if self.conf.get_float("score.low_band_weight") < 0 or self.conf.get_float(
            "score.high_band_weight"
        ) < 0:
            raise ValueError("score band weights must be non-negative")
        if self.conf.get_float("score.low_band_weight") + self.conf.get_float(
            "score.high_band_weight"
        ) <= 0:
            raise ValueError("at least one score band weight must be positive")
        if self.conf.get_float("score.frequency_weight") < 0 or self.conf.get_float("score.s11_weight") < 0:
            raise ValueError("ranking score weights must be non-negative")
        if self.conf.get_float("score.frequency_weight") + self.conf.get_float("score.s11_weight") <= 0:
            raise ValueError("at least one ranking score weight must be positive")
        if self.conf.get_float("score.resonance_error_scale_ghz") <= 0:
            raise ValueError("score.resonance_error_scale_ghz must be positive")
        if self.conf.get_float("score.non_dual_resonance_penalty") < 0:
            raise ValueError("score.non_dual_resonance_penalty must be non-negative")
        if not self.conf.get_string("remote.simulation_type").strip():
            raise ValueError("remote.simulation_type must be non-empty")
        if self.conf.get_int("remote.max_retries") < 0 or self.conf.get_int("remote.task_timeout_seconds") <= 0:
            raise ValueError("remote retry and timeout settings are invalid")
        if self.conf.get_int("antenna.resonance_detection.smoothing_window_points") < 1 or (
            self.conf.get_int("antenna.resonance_detection.smoothing_window_points") % 2 == 0
        ):
            raise ValueError(
                "antenna.resonance_detection.smoothing_window_points must be a positive odd integer"
            )
        for key in ("min_separation_ghz", "min_prominence_db", "min_saddle_rise_db"):
            if self.conf.get_float(f"antenna.resonance_detection.{key}") < 0:
                raise ValueError(
                    f"antenna.resonance_detection.{key} must be non-negative"
                )
        if self.conf.get_float("antenna.resonance_detection.peak_prominence_scale") < 0:
            raise ValueError(
                "antenna.resonance_detection.peak_prominence_scale must be non-negative"
            )
        if self.conf.get_float("antenna.resonance_detection.peak_prominence_floor_db") < 0:
            raise ValueError(
                "antenna.resonance_detection.peak_prominence_floor_db must be non-negative"
            )
        if self.conf.get_int("antenna.resonance_detection.refinement_half_window_points") < 0:
            raise ValueError(
                "antenna.resonance_detection.refinement_half_window_points must be non-negative"
            )
        if self.conf.get_int("antenna.resonance_detection.result_decimal_places") < 0:
            raise ValueError(
                "antenna.resonance_detection.result_decimal_places must be non-negative"
            )
        if self.conf.get_int("llm.sdk_max_retries") < 0:
            raise ValueError("llm.sdk_max_retries must be non-negative")
        if not self.conf.get_string("llm.system_prompt").strip():
            raise ValueError("llm.system_prompt must be non-empty")
        preflight = self.conf.get_config("llm.preflight")
        if preflight.get_float("timeout_seconds") <= 0:
            raise ValueError("llm.preflight.timeout_seconds must be positive")
        if preflight.get_int("max_output_tokens") < 1:
            raise ValueError("llm.preflight.max_output_tokens must be positive")
        if not preflight.get_string("system_prompt").strip() or not preflight.get_string(
            "user_prompt"
        ).strip():
            raise ValueError("llm.preflight prompts must be non-empty")
        mock = self.conf.get_config("simulation.mock")
        if mock.get_float("frequency_start_ghz") >= mock.get_float(
            "frequency_end_ghz"
        ):
            raise ValueError("simulation.mock frequency start must be below end")
        if mock.get_int("frequency_points") < 2:
            raise ValueError("simulation.mock.frequency_points must be at least 2")
        for key in (
            "noise_std_db",
            "alignment_cap",
            "maximum_resonance_depth_db",
            "depth_reduction_per_alignment_db",
            "low_linewidth_ghz",
            "high_linewidth_ghz",
        ):
            if mock.get_float(key) < 0:
                raise ValueError(f"simulation.mock.{key} must be non-negative")
        if mock.get_float("low_linewidth_ghz") == 0 or mock.get_float(
            "high_linewidth_ghz"
        ) == 0:
            raise ValueError("simulation.mock linewidths must be positive")
        if mock.get_int("noise_seed_iteration_stride") < 1:
            raise ValueError(
                "simulation.mock.noise_seed_iteration_stride must be positive"
            )
        for key in (
            "low_l1_center",
            "low_feed_center",
            "high_l3_center",
            "high_l2_center",
            "low_s1_alignment_center",
            "low_b_alignment_center",
            "high_s3_alignment_center",
            "high_s2_alignment_center",
        ):
            value = mock.get_float(key)
            if not 0 <= value <= 1:
                raise ValueError(f"simulation.mock.{key} must be between 0 and 1")
        for key in ("low_b_alignment_weight", "high_s2_alignment_weight"):
            if mock.get_float(key) < 0:
                raise ValueError(f"simulation.mock.{key} must be non-negative")
        for name, spec in self.specs.items():
            if spec.minimum >= spec.maximum:
                raise ValueError(f"antenna variable {name} must have min < max")

    def _write_result(self, record: SampleRecord, status: str) -> None:
        """保存最终状态及当前最佳样本的完整信息，供后续复现实验或继续优化。"""
        result = {"status": status, "best_sample": record.to_dict()}
        (self.runtime_dir / "optimization_result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
