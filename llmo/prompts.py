"""Render runtime configuration and evaluated samples into the sampling prompt."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from pyhocon import ConfigTree

from .models import VariableSpec


def format_number(value: float, decimal_places: int = 2) -> str:
    """按照配置的小数位数生成提示词中的数值文本。"""
    return f"{float(value):.{decimal_places}f}"


def variable_ranges_text(
    specs: dict[str, VariableSpec], decimal_places: int
) -> str:
    """把配置树中的变量范围和英文物理含义展开为提示词列表。"""
    return "\n".join(
        f"- `{name}`: [{format_number(spec.minimum, decimal_places)}, "
        f"{format_number(spec.maximum, decimal_places)}] {spec.unit}; {spec.meaning}."
        for name, spec in specs.items()
    )


def _finite_number(value: object, field_name: str) -> float:
    """把提示词字段转换为有限浮点数，并在 LLM 调用前暴露坏数据。"""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"sample field {field_name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"sample field {field_name} must be finite")
    return result


def _resonance_count(sample: dict[str, Any]) -> int:
    """读取摘要中的权威谐振计数。"""
    raw = sample.get("resonance_count")
    if raw is None:
        raise ValueError("sample is missing required resonance_count")
    count = int(raw)
    if count not in {0, 1, 2}:
        raise ValueError(
            f"sample resonance_count must be 0, 1, or 2; received {count}"
        )
    return count


def compact_sample_line(
    sample: dict[str, Any],
    specs: dict[str, VariableSpec],
    conf: ConfigTree,
) -> str:
    """把内部双槽位摘要转换为供 LLM 使用的紧凑、动态样本行。"""
    decimal_places = conf.get_int("optimization.variable_decimal_places")
    low_target = conf.get_float("antenna.targets.low_frequency_ghz")
    high_target = conf.get_float("antenna.targets.high_frequency_ghz")
    raw_variables = sample.get("variables")
    if not isinstance(raw_variables, dict) or set(raw_variables) != set(specs):
        received = sorted(raw_variables) if isinstance(raw_variables, dict) else []
        raise ValueError(
            f"sample variables must be exactly {sorted(specs)}; received {received}"
        )

    # 变量只发送实际数值，不使用程序预先划分的 low/mid/high 标签。
    geometry_items: list[str] = []
    for name in specs:
        item = raw_variables[name]
        raw_value = item
        value = _finite_number(raw_value, f"variables.{name}")
        spec = specs[name]
        if value < spec.minimum or value > spec.maximum:
            raise ValueError(
                f"sample variable {name}={value} is outside [{spec.minimum}, {spec.maximum}]"
            )
        geometry_items.append(f"{name}:{format_number(value, decimal_places)}")

    count = _resonance_count(sample)
    resonances: list[tuple[float, float]] = []
    if count >= 1:
        first_frequency = _finite_number(
            sample.get("low_resonance_point"), "low_resonance_point"
        )
        first_depth = _finite_number(
            sample.get("s11_at_low_resonance"), "s11_at_low_resonance"
        )
        if first_frequency <= 0.0:
            raise ValueError("a real resonance frequency must be greater than zero")
        resonances.append((first_frequency, first_depth))
    if count == 2:
        second_frequency = _finite_number(
            sample.get("high_resonance_point"), "high_resonance_point"
        )
        second_depth = _finite_number(
            sample.get("s11_at_high_resonance"), "s11_at_high_resonance"
        )
        if second_frequency <= 0.0:
            raise ValueError("a real resonance frequency must be greater than zero")
        resonances.append((second_frequency, second_depth))
        if resonances[0][0] > resonances[1][0]:
            raise ValueError("two resonances must be ordered by increasing frequency")
    if len(resonances) != count:
        raise ValueError("resonance_count must equal the number of real resonance pairs")

    resonance_text = "[" + ",".join(
        f"[{frequency:.2f},{depth:.2f}]" for frequency, depth in resonances
    ) + "]"
    low_s11 = _finite_number(sample.get("s11_at_2_45"), "s11_at_2_45")
    high_s11 = _finite_number(sample.get("s11_at_4_50"), "s11_at_4_50")
    score = _finite_number(sample.get("score"), "score")

    # LLMs only need the ranking position to compare evidence. Persistent IDs,
    # provenance and iteration numbers remain internal for logs and pools.
    rank = "none" if sample.get("rank") is None else str(int(sample.get("rank", 0)))
    metadata = [f"rank:{rank}"]

    return " + ".join(
        [
            "{" + ",".join(metadata) + "}",
            "{geom:{" + ",".join(geometry_items) + "}}",
            f"{{n_res:{count}}}",
            f"{{res:{resonance_text}}}",
            (
                f"{{target_s11:{{{low_target:.2f}:{low_s11:.2f},"
                f"{high_target:.2f}:{high_s11:.2f}}}}}"
            ),
            f"{{score:{score:.6f}}}",
        ]
    )


def compact_samples_text(
    samples: list[dict[str, Any]],
    specs: dict[str, VariableSpec],
    conf: ConfigTree,
) -> str:
    """把一组真实样本渲染为每样本一行的稳定紧凑视图。"""
    if not samples:
        return "none"
    return "\n".join(compact_sample_line(sample, specs, conf) for sample in samples)


def compact_sample_contract(conf: ConfigTree) -> str:
    """Explain every compact sample field and the exact real-score semantics."""
    low_target = conf.get_float("antenna.targets.low_frequency_ghz")
    high_target = conf.get_float("antenna.targets.high_frequency_ghz")
    low_weight = conf.get_float("score.low_band_weight")
    high_weight = conf.get_float("score.high_band_weight")
    total_weight = low_weight + high_weight
    low_weight /= total_weight
    high_weight /= total_weight
    excellent = conf.get_float("score.excellent_s11_db")
    threshold = conf.get_float("antenna.targets.s11_threshold_db")
    frequency_weight = conf.get_float("score.frequency_weight")
    s11_weight = conf.get_float("score.s11_weight")
    ranking_total = frequency_weight + s11_weight
    frequency_weight /= ranking_total
    s11_weight /= ranking_total
    scale = conf.get_float("score.resonance_error_scale_ghz")
    return f"""compact sample contract:
- one complete evaluated sample occupies one line. `+` only separates sections; it is not arithmetic addition.
- `rank:n` is the sample's position after sorting the available evaluated samples by ranking score `R` from highest to lowest. `rank:1` is the highest-ranked sample, `rank:2` is the second highest, and a smaller rank number is better. rank is comparative, not a persistent sample identifier.
- `geom={{a:value,b:value,l1:value,l2:value,l3:value,s1:value,s2:value,s3:value,f_l:value}}` is the evaluated antenna geometry in mm: `a`=substrate length, `b`=substrate width, `l1`=upper slot length, `l2`=middle slot length, `l3`=lower slot length, `s1`=upper slot width, `s2`=middle slot width, `s3`=lower slot width, and `f_l`=feed-line length. every value is continuous; no low/mid/high category is implied.
- `n_res` is the number of valid resonances detected from the complete measured s11 curve after the configured prominence, separation, and saddle checks. it is exactly 0, 1, or 2 and must equal the number of pairs in `res`.
- `res` contains the detected actual resonance valleys. each pair is `[resonance_frequency_ghz,s11_at_that_resonance_db]`. a more-negative second value means a deeper resonance valley. when two pairs exist they are ordered by increasing frequency.
- the valid resonance forms are `{{n_res:0}} + {{res:[]}}`, `{{n_res:1}} + {{res:[[f1,s11_1]]}}`, and `{{n_res:2}} + {{res:[[f1,s11_1],[f2,s11_2]]}}`. for `n_res:1`, the sole resonance is not automatically assigned to the low band; compare its frequency with both {low_target:.2f} ghz and {high_target:.2f} ghz. absent resonances are omitted, not represented by physical zero values, and must not be invented.
- `target_s11={{{low_target:.2f}:s_low,{high_target:.2f}:s_high}}` contains s11 linearly interpolated at the two fixed target frequencies. these values drive the numerical score and are distinct from resonance-valley depths in `res`. at either target, a more-negative value is better; a value at or below {threshold:.2f} db satisfies that band's matching threshold.
- `score:R` is used only for ranking. The detector stores the first and second frequency-ordered resonance slots as `r1` and `r2`; when two resonances exist, `r1` is the lower-frequency detected valley and `r2` is the higher-frequency detected valley. A missing slot is stored internally as `0.00 GHz` only so the ranking formula can be evaluated; this placeholder is not a measured or physical resonance and is omitted from `res`. The frequency quality is `q_r1=exp(-abs(r1-{low_target:.2f})/{scale:.2f})`, `q_r2=exp(-abs(r2-{high_target:.2f})/{scale:.2f})`, and `q_f=(q_r1+q_r2)/2`. If there is only one detected resonance, do not assume it belongs to the low band: `r1` is simply the one detected slot, `r2` is the missing-slot placeholder, and `n_res` plus `res` remain the authoritative evidence about what was detected. The target-frequency S11 quality is `q_s={low_weight:.6f}*q_band(s_low)+{high_weight:.6f}*q_band(s_high)`, where `q_band(s)=clip((-s)/abs({excellent:.2f}),0,1)`. Here `s_low` and `s_high` are S11 values interpolated at the fixed target frequencies, not at the actual resonance minima. Finally `R={frequency_weight:.6f}*q_f+{s11_weight:.6f}*q_s`. R is not a success condition.
- complete example: `{{rank:1}} + {{geom:{{a:125.00,b:60.00,l1:30.00,l2:10.00,l3:30.00,s1:1.00,s2:5.00,s3:0.90,f_l:10.00}}}} + {{n_res:2}} + {{res:[[2.48,-15.40],[4.47,-15.70]]}} + {{target_s11:{{{low_target:.2f}:-12.80,{high_target:.2f}:-13.90}}}} + {{score:0.667500}}`."""


def render_template(
    path: Path,
    replacements: dict[str, str] | None = None,
) -> str:
    """将配置值填入模板，并拒绝任何未解析占位符。"""
    template = path.read_text(encoding="utf-8")

    values = replacements or {}
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)

    # 如果模板新增占位符但调用方忘记提供值，应在调用 LLM 前立即失败，
    # 而不是让模型看到 {{name}} 这类无意义文本。
    unresolved = sorted(set(re.findall(r"\{\{[a-z0-9_]+\}\}", template)))
    if unresolved:
        raise ValueError(
            f"prompt template {path} has unresolved runtime placeholders: {unresolved}"
        )
    return template
