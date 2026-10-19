"""Convert complete S11 curves into resonance metrics and ranking scores."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
from pyhocon import ConfigTree

from find_resonance_points import find_two_resonances

from .models import AntennaMetrics


def _resonance_detection_kwargs(conf: ConfigTree) -> dict[str, float | int]:
    """Read every tunable resonance-detector coefficient from project.conf."""
    prefix = "antenna.resonance_detection"
    return {
        "min_separation_ghz": conf.get_float(f"{prefix}.min_separation_ghz"),
        "min_prominence_db": conf.get_float(f"{prefix}.min_prominence_db"),
        "min_saddle_rise_db": conf.get_float(f"{prefix}.min_saddle_rise_db"),
        "smoothing_window_points": conf.get_int(
            f"{prefix}.smoothing_window_points"
        ),
        "peak_prominence_scale": conf.get_float(
            f"{prefix}.peak_prominence_scale"
        ),
        "peak_prominence_floor_db": conf.get_float(
            f"{prefix}.peak_prominence_floor_db"
        ),
        "refinement_half_window_points": conf.get_int(
            f"{prefix}.refinement_half_window_points"
        ),
        "result_decimal_places": conf.get_int(
            f"{prefix}.result_decimal_places"
        ),
    }


def s11_at_frequency(frequency_ghz: Iterable[float], s11_db: Iterable[float], target_ghz: float) -> float:
    """在线性插值后读取固定目标频率处的 S11，而不是直接取最近离散点。"""
    frequency = np.asarray(list(frequency_ghz), dtype=float)
    values = np.asarray(list(s11_db), dtype=float)
    if len(frequency) != len(values) or len(frequency) < 2:
        raise ValueError("frequency_ghz and s11_db must have the same length >= 2")
    order = np.argsort(frequency)
    frequency = frequency[order]
    values = values[order]
    if target_ghz < frequency[0] or target_ghz > frequency[-1]:
        raise ValueError(f"target frequency {target_ghz} GHz is outside the simulated frequency range")
    return float(np.interp(target_ghz, frequency, values))


def band_depth_score(s11_db: float, excellent_s11_db: float) -> float:
    """把单个目标频率 S11 映射到 [0,1]；达到 excellent 值后得分封顶。"""
    if excellent_s11_db >= 0:
        raise ValueError("excellent_s11_db must be negative")
    return float(np.clip((-s11_db) / abs(excellent_s11_db), 0.0, 1.0))


def calculate_target_s11_quality(
    s11_low_db: float,
    s11_high_db: float,
    conf: ConfigTree,
) -> float:
    """Return ``q_s``: target-frequency S11 quality in ``[0, 1]``.

    This is deliberately separate from the optimization success predicate.
    ``s11_low_db`` and ``s11_high_db`` are measured at the fixed target
    frequencies, not at the detected resonance minima.
    """
    # q_s 只描述两个固定目标频率处的 S11 质量；实际谐振位置由 q_f 单独描述。
    low_weight = conf.get_float("score.low_band_weight")
    high_weight = conf.get_float("score.high_band_weight")
    if low_weight < 0 or high_weight < 0 or low_weight + high_weight <= 0:
        raise ValueError("score weights must be non-negative and have a positive sum")
    total_weight = low_weight + high_weight
    low_weight /= total_weight
    high_weight /= total_weight
    excellent = conf.get_float("score.excellent_s11_db")
    return float(
        low_weight * band_depth_score(s11_low_db, excellent)
        + high_weight * band_depth_score(s11_high_db, excellent)
    )


def calculate_real_sample_score(
    s11_low_db: float,
    s11_high_db: float,
    conf: ConfigTree,
) -> float:
    """Backward-compatible alias for the target-frequency S11 quality ``q_s``."""
    return calculate_target_s11_quality(s11_low_db, s11_high_db, conf)


def calculate_ranking_score(
    s11_low_db: float,
    s11_high_db: float,
    low_resonance_point: float,
    high_resonance_point: float,
    conf: ConfigTree,
) -> float:
    """Calculate the ranking-only score from frequency and S11 quality.

    ``q_f`` rewards detected resonance locations near the configured target
    frequencies.  ``q_s`` rewards S11 at those fixed target frequencies.
    A configurable penalty is subtracted when the detector does not provide
    two independent resonances. The penalty uses the same higher-is-better
    score units as ``R`` and preserves the existing ranking API.
    This score is used for ranking and LLM evidence selection only; success is
    checked independently by :func:`meets_design_goal`.
    """
    low_target = conf.get_float("antenna.targets.low_frequency_ghz")
    high_target = conf.get_float("antenna.targets.high_frequency_ghz")
    scale = conf.get_float("score.resonance_error_scale_ghz")
    frequency_weight = conf.get_float("score.frequency_weight")
    s11_weight = conf.get_float("score.s11_weight")
    if scale <= 0:
        raise ValueError("score.resonance_error_scale_ghz must be positive")
    if frequency_weight < 0 or s11_weight < 0 or frequency_weight + s11_weight <= 0:
        raise ValueError("ranking score weights must be non-negative and have a positive sum")
    total_weight = frequency_weight + s11_weight
    frequency_weight /= total_weight
    s11_weight /= total_weight
    q_f_low = float(np.exp(-abs(low_resonance_point - low_target) / scale))
    q_f_high = float(np.exp(-abs(high_resonance_point - high_target) / scale))
    q_f = (q_f_low + q_f_high) / 2.0
    q_s = calculate_target_s11_quality(s11_low_db, s11_high_db, conf)
    base_score = frequency_weight * q_f + s11_weight * q_s
    non_dual_penalty = conf.get_float("score.non_dual_resonance_penalty")
    if non_dual_penalty < 0:
        raise ValueError("score.non_dual_resonance_penalty must be non-negative")
    # Missing second resonance is stored as the 0.00 sentinel by evaluate_curve.
    if high_resonance_point <= 0.0:
        base_score -= non_dual_penalty
    return float(base_score)



def evaluate_curve(frequency_ghz: Iterable[float], s11_db: Iterable[float], conf: ConfigTree) -> AntennaMetrics:
    """从完整 S11 曲线提取目标频率 S11、0/1/2 个谐振和综合分数。"""
    frequency = list(frequency_ghz)
    values = list(s11_db)
    low_target = conf.get_float("antenna.targets.low_frequency_ghz")
    high_target = conf.get_float("antenna.targets.high_frequency_ghz")
    low_s11 = s11_at_frequency(frequency, values, low_target)
    high_s11 = s11_at_frequency(frequency, values, high_target)
    # 调用更新后的上传脚本逻辑；程序只负责传入配置，并读取其 0/1/2 分类结果。
    raw = find_two_resonances(
        frequency,
        values,
        **_resonance_detection_kwargs(conf),
    )
    resonance_count = int(raw["resonance_count"])
    if resonance_count not in {0, 1, 2}:
        raise ValueError(f"invalid resonance_count={resonance_count}; expected 0, 1, or 2")

    # 统一使用两个固定槽位保存谐振结果：单谐振只占用第一个槽位，无谐振则两个槽位均为 0。
    first_resonance = float(raw["resonance_freq_1_ghz"]) if resonance_count >= 1 else 0.0
    first_depth = float(raw["resonance_s11_1_db"]) if resonance_count >= 1 else 0.0
    second_resonance = float(raw["resonance_freq_2_ghz"]) if resonance_count == 2 else 0.0
    second_depth = float(raw["resonance_s11_2_db"]) if resonance_count == 2 else 0.0
    is_dual = resonance_count == 2
    return AntennaMetrics(
        s11_at_2_45=low_s11,
        s11_at_4_50=high_s11,
        resonance_count=resonance_count,
        is_dual_resonance=is_dual,
        low_resonance_point=first_resonance,
        s11_at_low_resonance=first_depth,
        high_resonance_point=second_resonance,
        s11_at_high_resonance=second_depth,
        score=calculate_ranking_score(
            low_s11,
            high_s11,
            first_resonance,
            second_resonance,
            conf,
        ),
    )


def meets_design_goal(metrics: AntennaMetrics, conf: ConfigTree) -> bool:
    """Check the physical success conditions independently of ranking score."""
    if metrics.resonance_count != 2 or not metrics.is_dual_resonance:
        return False
    tolerance = conf.get_float("antenna.targets.resonance_tolerance_ghz")
    low_target = conf.get_float("antenna.targets.low_frequency_ghz")
    high_target = conf.get_float("antenna.targets.high_frequency_ghz")
    threshold = conf.get_float("antenna.targets.s11_threshold_db")
    return bool(
        abs(metrics.low_resonance_point - low_target) <= tolerance
        and abs(metrics.high_resonance_point - high_target) <= tolerance
        and metrics.s11_at_2_45 <= threshold
        and metrics.s11_at_4_50 <= threshold
    )
