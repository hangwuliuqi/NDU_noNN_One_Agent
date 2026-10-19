"""Detect zero, one, or two physically separate resonances from one S11 curve.

This module is called by ``llmo.metrics`` for every imported or newly simulated
curve; it does not read files or run HFSS itself.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
from scipy.signal import find_peaks


def infer_frequency_step(freq_ghz: Iterable[float]) -> float:
    """Infer the median frequency step from a frequency vector."""
    freq = np.asarray(list(freq_ghz), dtype=float)
    unique_freq = np.sort(np.unique(freq))
    steps = np.diff(unique_freq)
    return float(np.median(steps)) if len(steps) else 0.0


def smooth_vector(values: np.ndarray, window: int = 3) -> np.ndarray:
    """Apply a short moving average to suppress isolated noise spikes."""
    if window <= 1 or len(values) < window:
        return values.copy()
    kernel = np.ones(window, dtype=float) / window
    padded = np.pad(values, (window // 2, window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def find_two_resonances(
    freq_ghz: Iterable[float],
    s11_db: Iterable[float],
    min_separation_ghz: float = 0.25,
    min_prominence_db: float = 1.0,
    min_saddle_rise_db: float = 1.5,
    smoothing_window_points: int = 3,
    peak_prominence_scale: float = 0.5,
    peak_prominence_floor_db: float = 0.4,
    refinement_half_window_points: int = 1,
    result_decimal_places: int = 2,
) -> dict[str, float | bool | int | str]:
    """
    Detect whether an S11 curve contains zero, one, or two resonances.

    The detector looks for local minima in \\(S_{11}(f)\\). One valid minimum
    is returned as a single resonance. When at least two valid minima satisfy
    the separation and saddle criteria, the two lowest-frequency minima are
    returned as the dual-resonance pair.
    """
    freq = np.asarray(list(freq_ghz), dtype=float)
    s11 = np.asarray(list(s11_db), dtype=float)
    if len(freq) != len(s11) or len(freq) < 2:
        raise ValueError("Frequency and S11 vectors must have the same length >= 2")
    if smoothing_window_points < 1 or smoothing_window_points % 2 == 0:
        raise ValueError("smoothing_window_points must be a positive odd integer")
    if peak_prominence_scale < 0 or peak_prominence_floor_db < 0:
        raise ValueError("peak prominence scale and floor must be non-negative")
    if refinement_half_window_points < 0:
        raise ValueError("refinement_half_window_points must be non-negative")
    if result_decimal_places < 0:
        raise ValueError("result_decimal_places must be non-negative")

    frequency_step_ghz = infer_frequency_step(freq)
    if min_separation_ghz < 0:
        raise ValueError("min_separation_ghz must be non-negative")
    if min_prominence_db < 0 or min_saddle_rise_db < 0:
        raise ValueError("prominence and saddle-rise thresholds must be non-negative")

    def empty_result() -> dict[str, float | bool | int | str]:
        return {
            "resonance_count": 0,
            "is_dual_resonance": False,
            "resonance_freq_1_ghz": 0.0,
            "resonance_s11_1_db": 0.0,
            "resonance_freq_2_ghz": 0.0,
            "resonance_s11_2_db": 0.0,
        }

    def single_result(
        candidate: dict[str, float | int],
    ) -> dict[str, float | bool | int | str]:
        """把唯一有效谐振写入第一个槽位，第二个槽位使用 0 作为缺失标记。"""
        return {
            "resonance_count": 1,
            "is_dual_resonance": False,
            "resonance_freq_1_ghz": round(
                float(candidate["freq"]), result_decimal_places
            ),
            "resonance_s11_1_db": round(
                float(candidate["s11"]), result_decimal_places
            ),
            "resonance_freq_2_ghz": 0.0,
            "resonance_s11_2_db": 0.0,
        }

    # 先用很短的三点移动平均抑制孤立噪声，再把 GHz 间隔换算成离散采样点间隔。
    smooth_s11 = smooth_vector(s11, window=smoothing_window_points)
    if frequency_step_ghz > 0:
        min_distance_points = max(1, int(np.ceil(min_separation_ghz / frequency_step_ghz)))
    else:
        min_distance_points = 1

    # 对 -S11 寻峰等价于在 S11 曲线上寻找局部最低点；prominence 用于排除不明显的小波动。
    peak_indices, peak_properties = find_peaks(
        -smooth_s11,
        prominence=max(
            peak_prominence_scale * min_prominence_db,
            peak_prominence_floor_db,
        ),
        distance=min_distance_points,
    )

    # 除 scipy 检出的峰外，再加入原始曲线上的直接局部最低点，避免平滑过程漏掉窄谐振谷。
    candidate_seed_indices = {int(idx) for idx in peak_indices}
    for idx in range(1, len(s11) - 1):
        if s11[idx] <= s11[idx - 1] and s11[idx] <= s11[idx + 1]:
            candidate_seed_indices.add(idx)

    candidates: list[dict[str, float | int]] = []
    prominence_by_peak = {
        int(peak_idx): float(peak_properties["prominences"][peak_order])
        for peak_order, peak_idx in enumerate(peak_indices)
    }
    for seed_idx in sorted(candidate_seed_indices):
        # 在种子点左右一个采样点内回到原始 S11 曲线细化谷底，最终深度不取平滑后的近似值。
        refine_left = max(1, seed_idx - refinement_half_window_points)
        refine_right = min(
            len(s11) - 2,
            seed_idx + refinement_half_window_points,
        )
        refined_idx = refine_left + int(np.argmin(s11[refine_left : refine_right + 1]))
        depth = float(s11[refined_idx])
        if refined_idx <= 0 or refined_idx >= len(s11) - 1:
            continue

        left_peak = float(np.max(s11[:refined_idx])) if refined_idx > 0 else float(s11[refined_idx])
        right_peak = float(np.max(s11[refined_idx + 1 :])) if refined_idx + 1 < len(s11) else float(s11[refined_idx])
        left_prominence = left_peak - depth
        right_prominence = right_peak - depth
        raw_prominence = max(left_prominence, right_prominence)
        balanced_prominence = min(left_prominence, right_prominence)
        peak_prominence = prominence_by_peak.get(seed_idx, 0.0)
        prominence = max(float(raw_prominence), float(peak_prominence))
        if prominence < min_prominence_db:
            continue

        candidates.append(
            {
                "idx": refined_idx,
                "freq": float(freq[refined_idx]),
                "s11": depth,
                "prominence": prominence,
                "balanced_prominence": float(balanced_prominence),
            }
        )

    # 多个种子可能细化到同一个离散谷底，因此先按索引去重并保留更深的候选。
    unique_candidates: dict[int, dict[str, float | int]] = {}
    for candidate in candidates:
        idx = int(candidate["idx"])
        if idx not in unique_candidates or float(candidate["s11"]) < float(unique_candidates[idx]["s11"]):
            unique_candidates[idx] = candidate
    candidates = list(unique_candidates.values())

    # 严格沿用原脚本语义：按频率升序排列有效候选，并把最低频的两个独立谷作为双谐振对。
    candidates = sorted(candidates, key=lambda item: float(item["freq"]))
    if not candidates:
        return empty_result()
    if len(candidates) == 1:
        return single_result(candidates[0])

    first = candidates[0]
    second = candidates[1]
    if abs(float(first["freq"]) - float(second["freq"])) < min_separation_ghz:
        return single_result(first)

    lo = min(int(first["idx"]), int(second["idx"]))
    hi = max(int(first["idx"]), int(second["idx"]))
    if hi - lo <= 1:
        return single_result(first)

    # 最后检查两谷之间是否有足够明显的曲线上升（鞍部），避免同一宽谷被拆成两个谐振点。
    shallower_valley_db = max(float(first["s11"]), float(second["s11"]))
    saddle_db = float(np.max(s11[lo + 1 : hi]))
    saddle_rise_db = saddle_db - shallower_valley_db
    if saddle_rise_db < min_saddle_rise_db:
        return single_result(first)
    if min(float(first["prominence"]), float(second["prominence"])) < min_prominence_db:
        return single_result(first)

    return {
        "resonance_count": 2,
        "is_dual_resonance": True,
        "resonance_freq_1_ghz": round(
            float(first["freq"]), result_decimal_places
        ),
        "resonance_s11_1_db": round(
            float(first["s11"]), result_decimal_places
        ),
        "resonance_freq_2_ghz": round(
            float(second["freq"]), result_decimal_places
        ),
        "resonance_s11_2_db": round(
            float(second["s11"]), result_decimal_places
        ),
    }
