"""Define antenna-variable bounds, normalization, validation, and LLM-value repair."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np
from pyhocon import ConfigTree

from .models import VariableSpec


def variable_specs_from_config(conf: ConfigTree) -> dict[str, VariableSpec]:
    """从配置树读取九个设计变量，并统一把变量名转换为小写。"""
    variables = conf.get_config("antenna.variables")
    specs: dict[str, VariableSpec] = {}
    for name, raw in variables.items():
        lowered = str(name).lower()
        specs[lowered] = VariableSpec(
            name=lowered,
            minimum=float(raw.get("min")),
            maximum=float(raw.get("max")),
            unit=str(raw.get("unit")),
            meaning=str(raw.get("meaning")),
        )
    return specs


def annotate_variables(
    variables: Mapping[str, float],
    specs: Mapping[str, VariableSpec],
    decimal_places: int = 2,
) -> dict[str, float]:
    """为样本摘要写入两位小数变量值，不再进行固定三等分。"""
    validate_variables(variables, specs)
    return {
        name: round(float(variables[name]), decimal_places)
        for name in specs
    }


def repair_candidate_variables(
    raw_variables: Mapping[str, object],
    specs: Mapping[str, VariableSpec],
    rng: np.random.Generator,
    decimal_places: int,
    on_repair: Callable[[str, object, float], None] | None = None,
) -> dict[str, float]:
    """保留合法值并取两位小数；随机替换非有限值、非数值和越界值。"""
    lowered = {str(name).lower(): value for name, value in raw_variables.items()}

    # 缺变量或多变量通常表示 LLM 输出结构错误，不能靠随机值补成整个设计，因此直接拒绝该候选。
    if set(lowered) != set(specs):
        raise ValueError(
            f"design variables must be exactly {sorted(specs)}; received {sorted(lowered)}"
        )
    repaired: dict[str, float] = {}
    for name, spec in specs.items():
        raw_value = lowered[name]
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = float("nan")
        if not np.isfinite(value) or value < spec.minimum or value > spec.maximum:
            # 仅对已存在但非法的单个数值，在该变量自己的闭区间内均匀随机重采样。
            replacement = round(
                float(rng.uniform(spec.minimum, spec.maximum)), decimal_places
            )
            replacement = float(np.clip(replacement, spec.minimum, spec.maximum))
            if on_repair is not None:
                on_repair(name, raw_value, replacement)
            value = replacement
        repaired[name] = round(value, decimal_places)
    validate_variables(repaired, specs)
    return repaired


def validate_variables(variables: Mapping[str, float], specs: Mapping[str, VariableSpec]) -> None:
    """检查变量集合是否完整且所有数值都位于各自闭区间内。"""
    expected = set(specs)
    actual = {str(name).lower() for name in variables}
    if actual != expected:
        raise ValueError(f"design variables must be exactly {sorted(expected)}; received {sorted(actual)}")
    for name, spec in specs.items():
        value = float(variables[name])
        if not spec.minimum <= value <= spec.maximum:
            raise ValueError(f"{name}={value} is outside [{spec.minimum}, {spec.maximum}]")


def normalized_vector(variables: Mapping[str, float], specs: Mapping[str, VariableSpec]) -> list[float]:
    """把不同量纲和范围的变量线性映射到 [0,1]，供重复检测使用。"""
    validate_variables(variables, specs)
    return [
        (float(variables[name]) - spec.minimum) / (spec.maximum - spec.minimum)
        for name, spec in specs.items()
    ]
