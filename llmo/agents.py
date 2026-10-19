"""LLM-facing sampling agent for the closed-loop optimizer."""

from __future__ import annotations

import json
import logging
import inspect
from pathlib import Path
from typing import Any

import numpy as np
from pyhocon import ConfigTree

from .intervals import repair_candidate_variables
from .llm_client import LlmClient, LlmConnectionError
from .models import CandidateProposal, VariableSpec
from .prompts import (
    compact_sample_contract,
    compact_samples_text,
    format_number,
    render_template,
    variable_ranges_text,
)


class SamplingOutputError(ValueError):
    """A parsed sampling response that does not satisfy the candidate contract."""

    def __init__(self, kind: str, message: str, audits: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.audits = audits or []


class SamplingGuidanceAgent:
    """把配置和 TOP-K 真实样本组装成采样 LLM 请求。"""

    def __init__(
        self,
        conf: ConfigTree,
        specs: dict[str, VariableSpec],
        client: LlmClient,
        root_dir: Path,
        logger: logging.Logger,
    ) -> None:
        self.conf = conf
        self.specs = specs
        self.client = client
        self.root_dir = root_dir
        # Conversation history is deliberately scoped to one propose() call
        # (one optimization iteration).  It is never carried into a later
        # iteration, because an old correction conversation is not evidence for
        # a new iteration.
        self._history_messages: list[dict[str, str]] = []
        self._last_prompt = ""
        self._last_response: dict[str, Any] | None = None
        self.logger = logger
        self.last_raw_candidates: list[dict[str, Any]] = []

    def propose(
        self,
        top_samples: list[dict[str, Any]],
        iteration: int,
    ) -> list[CandidateProposal]:
        """请求固定数量的候选，并在返回前完成字段、范围和小数精度检查。"""

        # 这些值全部来自配置树，保证提示词中的 N、目标频率、阈值和程序实际逻辑一致。
        count = self.conf.get_int("optimization.proposals_per_iteration")
        decimal_places = self.conf.get_int("optimization.variable_decimal_places")
        low_target = self.conf.get_float("antenna.targets.low_frequency_ghz")
        high_target = self.conf.get_float("antenna.targets.high_frequency_ghz")
        target_depth_db = abs(
            self.conf.get_float("antenna.targets.s11_threshold_db")
        )
        resonance_tolerance = self.conf.get_float(
            "antenna.targets.resonance_tolerance_ghz"
        )
        if resonance_tolerance == 0:
            resonance_frequency_goal_text = (
                "The target design should have two actual resonance frequencies: "
                f"one at {low_target:.2f} GHz and one at {high_target:.2f} GHz."
            )
        else:
            resonance_frequency_goal_text = (
                "The target design should have two actual resonance frequencies: "
                f"one between {low_target - resonance_tolerance:.2f} and "
                f"{low_target + resonance_tolerance:.2f} GHz, and the other between "
                f"{high_target - resonance_tolerance:.2f} and "
                f"{high_target + resonance_tolerance:.2f} GHz."
            )
        template = self.root_dir / self.conf.get_string("llm.sampling.prompt_template")
        cold_start = not self.conf.get_bool("project.use_initial_parquet")
        if cold_start:
            template = template.with_name(f"{template.stem}_zero{template.suffix}")
        configured_top_k = self.conf.get_int("optimization.top_k")
        prompt_top_k = min(configured_top_k, len(top_samples)) if cold_start else configured_top_k

        prompt = render_template(
            template,
            {
                "proposal_count": str(count),
                "top_k": str(prompt_top_k),
                "cold_start_evidence_guidance": (
                    "No evaluated samples are available yet. Generate exploratory designs from electromagnetic design knowledge and the legal variable ranges; do not claim any measured evidence."
                    if not top_samples else
                    f"Exactly {len(top_samples)} evaluated sample(s) are available. Use all of them, and avoid claiming a measured trend when comparisons are insufficient."
                ) if cold_start else "",
                "target_resonance_depth_db": format_number(target_depth_db, 2),
                "resonance_frequency_goal_text": resonance_frequency_goal_text,
                "low_target_ghz": format_number(low_target, 2),
                "high_target_ghz": format_number(high_target, 2),
                "variable_ranges_text": variable_ranges_text(
                    self.specs, decimal_places
                ),
                "compact_sample_contract": compact_sample_contract(self.conf),
                "top_k_samples_compact": compact_samples_text(
                    top_samples,
                    self.specs,
                    self.conf,
                ),
            },
        )
        # 使用“全局种子 + 当前轮次”产生确定性的修复随机流，既满足越界随机替换要求，
        # 又能在输入和配置相同的情况下复现具体替换值。
        rng = np.random.default_rng(
            self.conf.get_int("project.seed")
            + iteration
            * self.conf.get_int(
                "optimization.candidate_repair_seed_iteration_stride"
            )
        )
        history: list[dict[str, str]] = []
        current_prompt = prompt
        max_correction_attempts = self.conf.get_int("llm.max_connection_attempts")
        if max_correction_attempts < 1:
            raise ValueError("llm.max_connection_attempts must be positive")
        configured_top_k = prompt_top_k
        self.last_raw_candidates = []
        self._history_messages = []

        for repair_attempt in range(1, max_correction_attempts + 1):
            response: dict[str, Any] | None = None
            try:
                response = self._complete_json_turn(
                    current_prompt,
                    iteration,
                    top_samples,
                    count,
                    history,
                )
            except LlmConnectionError as exc:
                # With attempt_limit=1, a raw non-JSON response is returned by
                # the client through last_response_text and can be corrected in
                # the same conversation. A transport failure without content is
                # retried with the same prompt; it must not be mistaken for a
                # schema failure or recorded as an assistant turn.
                raw_text = getattr(self.client, "last_response_text", None)
                if raw_text is None:
                    if repair_attempt >= max_correction_attempts:
                        raise
                    self.logger.warning(
                        "sampling LLM transport request failed; retrying unchanged turn "
                        "attempt=%d/%d error=%s",
                        repair_attempt,
                        max_correction_attempts,
                        exc,
                    )
                    continue
                error = str(getattr(self.client, "last_error", exc))
                self._append_history(history, current_prompt, raw_text)
                self._history_messages = list(history)
                if repair_attempt >= max_correction_attempts:
                    raise ValueError(
                        f"sampling LLM returned unusable JSON after {repair_attempt} correction attempts: {error}"
                    ) from exc
                current_prompt = self._sampling_correction_prompt(
                    "format_error",
                    count,
                    configured_top_k,
                    error,
                )
                continue

            try:
                proposals, audits = self._parse_proposals(
                    response,
                    count,
                    decimal_places,
                    rng,
                )
            except SamplingOutputError as exc:
                self.last_raw_candidates = exc.audits
                self._append_history(
                    history,
                    current_prompt,
                    self._response_text_for_history(response),
                )
                self._history_messages = list(history)
                if repair_attempt >= max_correction_attempts:
                    raise ValueError(
                        f"sampling LLM output remained invalid after {repair_attempt} correction attempts: {exc}"
                    ) from exc
                current_prompt = self._sampling_correction_prompt(
                    exc.kind,
                    count,
                    configured_top_k,
                    str(exc),
                )
                continue

            self.last_raw_candidates = audits
            self._last_prompt = current_prompt
            self._last_response = response
            self._history_messages = []
            return proposals

        raise RuntimeError("sampling correction loop exited unexpectedly")

    def _complete_json_turn(
        self,
        prompt: str,
        iteration: int,
        top_samples: list[dict[str, Any]],
        count: int,
        history: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Send one semantic turn; outer propose() owns schema correction."""
        kwargs: dict[str, Any] = {
            "mock_factory": lambda: self._mock_response(top_samples, count, iteration),
        }
        try:
            signature = inspect.signature(self.client.complete_json)
            parameters = signature.parameters
        except (TypeError, ValueError):
            # Some SDK adapters expose a built-in callable with no inspectable
            # signature.  Calling it with the historical optional arguments is
            # unsafe, so fall back to its minimal compatible interface.
            parameters = {}
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if history and ("history_messages" in parameters or accepts_kwargs):
            kwargs["history_messages"] = list(history)
        # One transport call per semantic turn is intentional.  It prevents
        # the low-level client from silently retrying the same invalid answer
        # without the correction prompt and accumulated conversation history.
        if "attempt_limit" in parameters or accepts_kwargs:
            kwargs["attempt_limit"] = 1
        return self.client.complete_json(prompt, iteration, **kwargs)

    @staticmethod
    def _response_text_for_history(response: object) -> str:
        """Serialize exactly what the failed assistant turn returned.

        Real ``LlmClient`` responses are parsed dictionaries, while lightweight
        test clients may return a raw string to model a malformed response.
        Keeping strings verbatim makes the next request's chat history faithful
        to the failed turn and avoids adding another layer of JSON quoting.
        """
        if isinstance(response, str):
            return response
        try:
            return json.dumps(response, ensure_ascii=False)
        except (TypeError, ValueError):
            return repr(response)

    @staticmethod
    def _append_history(
        history: list[dict[str, str]],
        prompt: str,
        response: str,
    ) -> None:
        # Keep the exact pair ordering expected by the chat API. Candidate JSON
        # responses are small, but cap pathological model output before it makes
        # later correction prompts exceed the configured context budget.
        history.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response[:30000]},
            ]
        )

    def _parse_proposals(
        self,
        response: object,
        count: int,
        decimal_places: int,
        rng: np.random.Generator,
    ) -> tuple[list[CandidateProposal], list[dict[str, Any]]]:
        if not isinstance(response, dict):
            raise SamplingOutputError(
                "format_error",
                "the response root must be one JSON object",
            )
        proposals_raw = response.get("proposals")
        if not isinstance(proposals_raw, list):
            raise SamplingOutputError(
                "format_error",
                "the response field 'proposals' must be a JSON array",
            )
        if len(proposals_raw) < count:
            raise SamplingOutputError(
                "count_too_few",
                f"the response contains {len(proposals_raw)} proposals, but exactly {count} are required",
                self._basic_audits(proposals_raw, "candidate count too small"),
            )
        if len(proposals_raw) > count:
            raise SamplingOutputError(
                "count_too_many",
                f"the response contains {len(proposals_raw)} proposals, but exactly {count} are required",
                self._basic_audits(proposals_raw, "candidate count too large"),
            )

        proposals: list[CandidateProposal] = []
        audits: list[dict[str, Any]] = []
        errors: list[str] = []
        for proposal_index, raw in enumerate(proposals_raw, start=1):
            audit = {
                "candidate_index": proposal_index,
                "raw": raw,
                "valid": False,
                "out_of_bounds": False,
            }
            audits.append(audit)
            if not isinstance(raw, dict):
                message = f"candidate {proposal_index} must be a JSON object"
                audit["error"] = message
                errors.append(message)
                continue
            try:
                variables_raw = raw.get("variables", {})
                if not isinstance(variables_raw, dict):
                    raise TypeError("variables must be an object")
                lowered_raw = {
                    str(name).lower(): value for name, value in variables_raw.items()
                }
                for name, spec in self.specs.items():
                    if name not in lowered_raw:
                        continue
                    try:
                        numeric = float(lowered_raw[name])
                    except (TypeError, ValueError):
                        audit["out_of_bounds"] = True
                        continue
                    if (
                        not np.isfinite(numeric)
                        or numeric < spec.minimum
                        or numeric > spec.maximum
                    ):
                        audit["out_of_bounds"] = True
                variables = repair_candidate_variables(
                    variables_raw,
                    self.specs,
                    rng,
                    decimal_places,
                    on_repair=lambda name, old, new, index=proposal_index: self.logger.warning(
                        "proposal %d variable %s=%r is invalid; randomly replaced with %.2f",
                        index,
                        name,
                        old,
                        new,
                    ),
                )
                audit["valid"] = True
                audit["repaired_variables"] = dict(variables)
            except (TypeError, ValueError) as exc:
                message = f"candidate {proposal_index} variables are invalid: {exc}"
                audit["error"] = message
                errors.append(message)
                continue

            rationale = " ".join(str(raw.get("rationale", "")).split())
            if not rationale:
                message = f"candidate {proposal_index} must contain a non-empty rationale"
                audit["error"] = message
                errors.append(message)
                continue
            proposals.append(
                CandidateProposal(
                    variables=variables,
                    rationale=rationale,
                    candidate_index=proposal_index,
                    original_variables=dict(variables),
                )
            )

        if errors or len(proposals) != count:
            if not errors:
                errors.append(
                    f"only {len(proposals)} candidates remained valid after schema validation"
                )
            raise SamplingOutputError(
                "invalid_candidate_schema",
                "; ".join(errors),
                audits,
            )
        return proposals, audits

    @staticmethod
    def _basic_audits(
        proposals_raw: list[Any],
        error: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "candidate_index": index,
                "raw": raw,
                "valid": False,
                "out_of_bounds": False,
                "error": error,
            }
            for index, raw in enumerate(proposals_raw, start=1)
        ]

    def _sampling_correction_prompt(
        self,
        kind: str,
        count: int,
        top_k: int,
        details: str,
    ) -> str:
        low_target = self.conf.get_float("antenna.targets.low_frequency_ghz")
        high_target = self.conf.get_float("antenna.targets.high_frequency_ghz")
        if kind == "count_too_few":
            issue = (
                f"The previous response contained fewer candidates than required "
                f"({details}). You must add the missing candidates."
            )
        elif kind == "count_too_many":
            issue = (
                f"The previous response contained more candidates than required "
                f"({details}). You must remove the extra candidates."
            )
        elif kind == "format_error":
            issue = (
                "The previous response was not one usable JSON object with a "
                "proposals array. Return parseable JSON only."
            )
        else:
            issue = (
                "The response had the requested array length, but one or more "
                f"candidate objects violated the schema: {details}"
            )
        evidence = (
            "the available evaluated samples already present in the conversation"
            if top_k else "the legal variable ranges already present in the conversation"
        ) if not self.conf.get_bool("project.use_initial_parquet") else f"the TOP-{top_k} evaluated samples already present in the conversation"
        return (
            "This is a sampling correction request in the same iteration. "
            "The previous sampling response was rejected. "
            f"{issue} Correct this specific problem and return exactly {count} complete candidate designs. "
            f"Using electromagnetic design knowledge together with {evidence}, "
            f"generate exactly {count} candidate designs again. "
            f"The targets remain {low_target:.2f} GHz and {high_target:.2f} GHz. "
            "Return only one JSON object with a proposals array. The array length must be exactly the requested number; "
            "every item must contain all nine legal variables, values inside their configured ranges, "
            "and a non-empty rationale. Do not repeat the long evidence section because it is already in the conversation."
        )

    def _mock_response(self, top_samples: list[dict[str, Any]], count: int, iteration: int) -> dict[str, Any]:
        """生成仅用于端到端测试的候选，不代表 LLM 推理或真实天线设计知识。"""
        rng = np.random.default_rng(
            self.conf.get_int("project.seed")
            + iteration
            * self.conf.get_int("optimization.mock_sampling_seed_iteration_stride")
        )
        local_std_fraction = self.conf.get_float(
            "optimization.mock_sampling_local_std_fraction"
        )
        global_count = self.conf.get_int(
            "optimization.mock_sampling_global_proposal_count"
        )
        if top_samples:
            base = {
                name: float(raw["value"] if isinstance(raw, dict) else raw)
                for name, raw in top_samples[0]["variables"].items()
            }
        else:
            base = {name: 0.5 * (spec.minimum + spec.maximum) for name, spec in self.specs.items()}
        proposals = []
        for index in range(count):
            variables = {}
            for name, spec in self.specs.items():
                if index >= count - global_count:
                    value = rng.uniform(spec.minimum, spec.maximum)
                else:
                    value = base[name] + rng.normal(
                        0.0,
                        local_std_fraction * (spec.maximum - spec.minimum),
                    )
                variables[name] = float(np.clip(value, spec.minimum, spec.maximum))
            proposals.append(
                {
                    "variables": variables,
                    "rationale": "This workflow-test candidate stays near the strongest real design while applying a controlled coordinated perturbation; the remote Worker and HFSS must verify the outcome.",
                }
            )
        return {"proposals": proposals}
