"""Import the initial real-HFSS Parquet dataset into the persistent sample pools."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from pyhocon import ConfigTree

from .intervals import validate_variables
from .metrics import evaluate_curve
from .models import CurveData, SampleRecord, VariableSpec
from .parquet_package import (
    CURVE_VECTOR_COLUMNS as PARQUET_VECTOR_COLUMNS,
    DESIGN_VARIABLE_COLUMNS as PARQUET_VARIABLE_COLUMNS,
    finite_float_vector,
)
from .pools import SamplePools


def import_initial_parquet(
    parquet_path: Path,
    conf: ConfigTree,
    specs: dict[str, VariableSpec],
    pools: SamplePools,
) -> int:
    """从每行包含几何参数和完整曲线向量的 Parquet 导入初始样本。

    Parquet 中可能已有旧的谐振/评分字段，但这里刻意不读取它们；所有指标
    都通过 ``evaluate_curve`` 重新计算，保证初始样本与后续仿真样本一致。
    """
    if not parquet_path.exists():
        if pools.records:
            return 0
        raise FileNotFoundError(f"initial parquet not found: {parquet_path}")

    frame = pd.read_parquet(parquet_path)
    columns_by_lower = {str(column).lower(): column for column in frame.columns}
    missing = [name for name in PARQUET_VARIABLE_COLUMNS if name not in columns_by_lower]
    missing.extend(name for name in PARQUET_VECTOR_COLUMNS if name.lower() not in columns_by_lower)
    if missing:
        raise ValueError(f"{parquet_path.name} is missing required columns: {missing}")

    staged: list[SampleRecord] = []
    staged_ids: set[str] = set()
    for row_number, (_, row) in enumerate(frame.iterrows(), start=1):
        sample_id = f"initial_{row_number:04d}"
        if sample_id in staged_ids:
            raise ValueError(f"duplicate Parquet sample_id: {sample_id}")
        variables = {
            name: float(row[columns_by_lower[name]]) for name in PARQUET_VARIABLE_COLUMNS
        }
        validate_variables(variables, specs)
        frequency = finite_float_vector(row[columns_by_lower["frequency_ghz"]], "Frequency_GHz")
        s11 = finite_float_vector(row[columns_by_lower["s11_db"]], "S11_dB")
        if len(frequency) != len(s11) or len(frequency) < 2:
            raise ValueError(f"Parquet row {row_number} vectors must have equal length >= 2")
        order = np.argsort(frequency)
        frequency = [frequency[index] for index in order]
        s11 = [s11[index] for index in order]
        curve = CurveData(frequency_ghz=frequency, s11_db=s11)
        metrics = evaluate_curve(curve.frequency_ghz, curve.s11_db, conf)
        metadata = {
            "imported_from_parquet": str(parquet_path),
            "parquet_row": row_number,
            "parquet_metadata": {
                str(column): _json_safe(row[column])
                for column in frame.columns
                if str(column).lower() not in {*PARQUET_VARIABLE_COLUMNS, "frequency_ghz", "s11_db"}
            },
        }
        staged.append(
            SampleRecord(
                sample_id=sample_id,
                variables=variables,
                curve=curve,
                metrics=metrics,
                iteration=0,
                source="initial_parquet",
                metadata=metadata,
            )
        )
        staged_ids.add(sample_id)

    if pools.records:
        _validate_resumed_initial_samples(staged, pools.records)
        return 0

    pools.add_many(staged)
    if not pools.records:
        raise ValueError("the initial Parquet contains no samples and the information pools are empty")
    return len(staged)


def _validate_resumed_initial_samples(
    expected: list[SampleRecord],
    persisted: list[SampleRecord],
) -> None:
    """确认恢复目录仍对应同一份初始数据，且绝不删除后续候选。"""
    expected_by_id = {record.sample_id: record for record in expected}
    persisted_by_id = {
        record.sample_id: record
        for record in persisted
        if record.source == "initial_parquet"
    }
    if set(expected_by_id) != set(persisted_by_id):
        raise ValueError(
            "the initial Parquet does not match the resumed sample pool; "
            "use a new --output-dir instead of mixing datasets"
        )

    for sample_id, expected_record in expected_by_id.items():
        persisted_record = persisted_by_id[sample_id]
        if (
            persisted_record.variables != expected_record.variables
            or persisted_record.curve.frequency_ghz
            != expected_record.curve.frequency_ghz
            or persisted_record.curve.s11_db != expected_record.curve.s11_db
        ):
            raise ValueError(
                "the initial Parquet does not match the resumed sample pool "
                f"at {sample_id}; use a new --output-dir instead of mixing datasets"
            )


def _json_safe(value: object) -> object:
    """Convert numpy scalar metadata to a JSON-serializable Python value."""
    if isinstance(value, np.generic):
        return value.item()
    return value
