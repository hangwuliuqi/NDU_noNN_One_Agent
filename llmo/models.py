"""Typed in-memory contracts exchanged by the optimizer, LLM agents, and HFSS layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class VariableSpec:
    """单个设计变量的合法闭区间、单位和面向 LLM 的英文物理含义。"""
    name: str
    minimum: float
    maximum: float
    unit: str
    meaning: str


@dataclass
class CurveData:
    """一条完整仿真曲线；两个列表按相同索引一一对应。"""
    # 原始 HFSS 通常以 0.01 GHz 保存；完整曲线作为真实证据原样保留。
    frequency_ghz: list[float]
    s11_db: list[float]


@dataclass
class AntennaMetrics:
    """从完整曲线提取的目标频率值、0/1/2 个谐振槽位及排名分数。"""
    # score 是由真实 HFSS 曲线计算的 ranking score R，不是成功布尔条件。
    s11_at_2_45: float
    s11_at_4_50: float
    resonance_count: int
    is_dual_resonance: bool
    low_resonance_point: float
    s11_at_low_resonance: float
    high_resonance_point: float
    s11_at_high_resonance: float
    score: float


@dataclass
class SampleRecord:
    """完整信息池中的一条真实或 mock 仿真样本记录。"""
    sample_id: str
    variables: dict[str, float]
    curve: CurveData
    metrics: AntennaMetrics
    iteration: int
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateProposal:
    """采样 LLM 候选及其理由。"""
    variables: dict[str, float]
    rationale: str
    # 审计字段：记录 LLM 原始候选编号、扰动前变量和是否因重复而被代码扰动。
    candidate_index: int | None = None
    original_variables: dict[str, float] = field(default_factory=dict)
    duplicate_with_history_or_round: bool = False
    perturbation_random_numbers: dict[str, float] = field(default_factory=dict)
