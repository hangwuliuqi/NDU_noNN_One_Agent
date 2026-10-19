"""Persist full evaluated curves and compact ranked summaries for later iterations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .intervals import annotate_variables
from .json_utils import dumps_readable
from .models import AntennaMetrics, CurveData, SampleRecord, VariableSpec


class SamplePools:
    """持久化管理完整信息池和按分数排序的样本摘要池。"""

    def __init__(
        self,
        runtime_dir: Path,
        specs: dict[str, VariableSpec],
        decimal_places: int = 2,
        summary_pool_size: int = 40,
    ) -> None:
        if summary_pool_size < 1:
            raise ValueError("summary_pool_size must be positive")
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        # 完整曲线与摘要都使用标准、带缩进的 JSON 数组，方便直接人工阅读。
        self.full_path = runtime_dir / "complete_information_pool.json"
        self.summary_path = runtime_dir / "sample_summary_pool.json"
        self.specs = specs
        self.decimal_places = decimal_places
        self.summary_pool_size = summary_pool_size
        self._records: list[SampleRecord] = []
        self._load()

    @property
    def records(self) -> list[SampleRecord]:
        return list(self._records)

    def refresh_ranking_scores(
        self,
        scorer: Callable[[AntennaMetrics], float],
    ) -> int:
        """Recompute persisted ranking scores after a scoring-formula change.

        Older runtime directories may contain the previous S11-only score.
        Refreshing at resume time keeps historical samples comparable with new
        samples without changing their measured curves or physical metrics.
        """
        changed = 0
        for record in self._records:
            refreshed = float(scorer(record.metrics))
            if record.metrics.score != refreshed:
                record.metrics.score = refreshed
                changed += 1
        if changed:
            self._records = self._ordered(self._records)
            self._persist(self._records)
        return changed

    def _load(self) -> None:
        """从完整信息池恢复历史样本，使优化可以从中断位置继续。"""
        if not self.full_path.exists():
            return
        text = self.full_path.read_text(encoding="utf-8").strip()
        if not text:
            return
        raw_records = json.loads(text)
        if not isinstance(raw_records, list):
            raise TypeError("complete_information_pool.json must contain a JSON array")
        for raw in raw_records:
            metrics_raw = dict(raw["metrics"])

            if "resonance_count" not in metrics_raw:
                raise ValueError(
                    f"sample {raw['sample_id']} is missing required resonance_count"
                )
            resonance_count = int(metrics_raw["resonance_count"])
            if resonance_count not in {0, 1, 2}:
                raise ValueError(
                    f"sample {raw['sample_id']} has invalid resonance_count={resonance_count}"
                )
            metrics_raw["is_dual_resonance"] = resonance_count == 2
            for field_name in (
                "low_resonance_point",
                "s11_at_low_resonance",
                "high_resonance_point",
                "s11_at_high_resonance",
            ):
                if metrics_raw.get(field_name) is None:
                    metrics_raw[field_name] = 0.0
            # 按当前数据契约统一缺失槽位的 0 哨兵。
            if resonance_count == 0:
                metrics_raw["low_resonance_point"] = 0.0
                metrics_raw["s11_at_low_resonance"] = 0.0
            if resonance_count < 2:
                metrics_raw["high_resonance_point"] = 0.0
                metrics_raw["s11_at_high_resonance"] = 0.0
            self._records.append(
                SampleRecord(
                    sample_id=raw["sample_id"],
                    variables={name: float(value) for name, value in raw["variables"].items()},
                    curve=CurveData(**raw["curve"]),
                    metrics=AntennaMetrics(**metrics_raw),
                    iteration=int(raw["iteration"]),
                    source=raw["source"],
                    metadata=dict(raw.get("metadata", {})),
                )
            )
        # 恢复完成后立即规范化完整池顺序并重建摘要池。这样旧运行目录
        # 也会采用与新写入一致的“分数降序、样本 ID 升序”持久化顺序。
        if self._records:
            self._records = self._ordered(self._records)
            self._persist(self._records)

    def add(self, record: SampleRecord) -> None:
        """追加一条完整样本记录，并立即重建按分数排序的摘要池。"""
        self.add_many([record])

    def reset(self) -> None:
        """清空持久化样本池，用于切换初始数据集时避免混入旧数据。"""
        self._records = []
        for path in (self.full_path, self.summary_path):
            if path.exists():
                path.unlink()

    def add_many(self, records: list[SampleRecord]) -> None:
        """完整校验一批样本后一次写入完整池和摘要池，避免半批导入。"""
        if not records:
            return
        existing_ids = {item.sample_id for item in self._records}
        incoming_ids = [item.sample_id for item in records]
        if len(incoming_ids) != len(set(incoming_ids)):
            raise ValueError("duplicate sample_id inside sample batch")
        duplicates = sorted(existing_ids.intersection(incoming_ids))
        if duplicates:
            raise ValueError(f"duplicate sample_id: {duplicates[0]}")

        staged = self._ordered(self._records + list(records))
        # 在触碰磁盘前构造所有摘要，保证坏记录不会只进入其中一个池。
        self._ranked_records(staged, limit=self.summary_pool_size)
        self._persist(staged)
        self._records = staged

    def _summary(self, record: SampleRecord, rank: int | None = None) -> dict[str, Any]:
        """构造供排序和 LLM 选取的摘要，不复制完整曲线或导入元数据。"""
        summary: dict[str, Any] = {
            "sample_id": record.sample_id,
            "source": record.source,
            "variables": annotate_variables(
                record.variables, self.specs, self.decimal_places
            ),
            "score": record.metrics.score,
            "s11_at_2_45": record.metrics.s11_at_2_45,
            "s11_at_4_50": record.metrics.s11_at_4_50,
            "resonance_count": record.metrics.resonance_count,
            "is_dual_resonance": record.metrics.is_dual_resonance,
            "low_resonance_point": record.metrics.low_resonance_point,
            "s11_at_low_resonance": record.metrics.s11_at_low_resonance,
            "high_resonance_point": record.metrics.high_resonance_point,
            "s11_at_high_resonance": record.metrics.s11_at_high_resonance,
            "iteration": record.iteration,
        }
        if rank is not None:
            summary = {"rank": rank, **summary}
        return summary

    def summary_for(
        self, record: SampleRecord, include_iteration: bool = True
    ) -> dict[str, Any]:
        """返回单个样本摘要，并允许调用方按模块需要删除产生轮次。"""
        summary = self._summary(record)
        if not include_iteration:
            summary.pop("iteration", None)
        return summary

    def ranked(self, limit: int | None = None, exclude_iteration: bool = False) -> list[dict[str, Any]]:
        """按分数降序、样本 ID 升序稳定排序，并可截取 TOP-K/TOP-Q。"""
        ordered = self._ordered(self._records)
        if limit is not None:
            ordered = ordered[:limit]
        result = [self._summary(record, rank=index) for index, record in enumerate(ordered, start=1)]
        if exclude_iteration:
            for item in result:
                item.pop("iteration", None)
        return result

    def ranked_summaries(
        self, limit: int | None = None, exclude_iteration: bool = False
    ) -> list[dict[str, Any]]:
        """Read ranked LLM evidence from the persisted summary-pool file.

        Complete curves remain authoritative for durable storage, but LLM input
        is deliberately sourced from this compact JSON view so frequency/S11
        vectors and metadata cannot enter a prompt accidentally.
        """
        if not self.summary_path.exists():
            return []
        raw = json.loads(self.summary_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError("sample_summary_pool.json must contain a JSON array")
        result = [dict(item) for item in raw]
        if limit is not None:
            result = result[:limit]
        if exclude_iteration:
            for item in result:
                item.pop("iteration", None)
        return result

    def _ranked_records(
        self,
        records: list[SampleRecord],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        ordered = self._ordered(records)
        if limit is not None:
            ordered = ordered[:limit]
        return [
            self._summary(record, rank=index)
            for index, record in enumerate(ordered, start=1)
        ]

    @staticmethod
    def _ranking_key(record: SampleRecord) -> tuple[float, str]:
        return (-record.metrics.score, record.sample_id)

    def _ordered(self, records: list[SampleRecord]) -> list[SampleRecord]:
        return sorted(records, key=self._ranking_key)

    def _persist(self, records: list[SampleRecord]) -> None:
        """原子更新完整池和容量受限的摘要池。"""
        full_tmp = self.full_path.with_suffix(self.full_path.suffix + ".tmp")
        summary_tmp = self.summary_path.with_suffix(self.summary_path.suffix + ".tmp")
        full_tmp.write_text(
            dumps_readable([item.to_dict() for item in records]) + "\n",
            encoding="utf-8",
        )
        summary_tmp.write_text(
            json.dumps(
                self._ranked_records(records, limit=self.summary_pool_size),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        full_tmp.replace(self.full_path)
        summary_tmp.replace(self.summary_path)

    def _write_summary(self) -> None:
        """把容量受限的最新排名写回摘要池。"""
        payload = self._ranked_records(
            self._records,
            limit=self.summary_pool_size,
        )
        self.summary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
