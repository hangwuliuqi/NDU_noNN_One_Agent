"""Read and write the strict one-row Parquet contract for evaluated HFSS samples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from .models import CurveData


# Column order is part of the optimizer ↔ HFSS file contract; do not reorder it
# without updating both the writer and every external manual/command backend.
DESIGN_VARIABLE_COLUMNS = ("a", "b", "l1", "l2", "l3", "s1", "s2", "s3", "f_l")
# These two cells contain Arrow/Python list vectors, not one scalar per frequency row.
CURVE_VECTOR_COLUMNS = ("Frequency_GHz", "S11_dB")
EVALUATED_SAMPLE_COLUMNS = DESIGN_VARIABLE_COLUMNS + CURVE_VECTOR_COLUMNS


def finite_float_vector(value: object, column_name: str) -> list[float]:
    """Convert a Parquet vector cell to a non-empty finite float list."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{column_name} must be an array, not a string") from exc
    values = np.asarray(value, dtype=float).reshape(-1)
    if len(values) < 2 or not np.all(np.isfinite(values)):
        raise ValueError(f"{column_name} must contain at least two finite values")
    return [float(item) for item in values]


def write_evaluated_sample_package(
    path: Path,
    variables: Mapping[str, float],
    frequency_ghz: object,
    s11_db: object,
) -> None:
    """Write one evaluated design using the canonical eleven-column Parquet schema."""
    if set(variables) != set(DESIGN_VARIABLE_COLUMNS):
        raise ValueError(
            "evaluated sample variables must contain exactly "
            f"{list(DESIGN_VARIABLE_COLUMNS)}"
        )
    frequency = finite_float_vector(frequency_ghz, "Frequency_GHz")
    s11 = finite_float_vector(s11_db, "S11_dB")
    if len(frequency) != len(s11):
        raise ValueError("Frequency_GHz and S11_dB must have equal lengths")

    # One row binds the exact solved geometry to its entire curve and prevents
    # accidental mismatch between a separate geometry file and curve file.
    row = {name: [float(variables[name])] for name in DESIGN_VARIABLE_COLUMNS}
    row["Frequency_GHz"] = [frequency]
    row["S11_dB"] = [s11]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(row, columns=EVALUATED_SAMPLE_COLUMNS).to_parquet(path, index=False)


def read_evaluated_sample_package(
    path: Path,
    expected_variables: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], CurveData]:
    """Read and validate one evaluated-design Parquet package."""
    if not path.exists():
        raise FileNotFoundError(f"simulation result not found: {path}")
    frame = pd.read_parquet(path)
    if tuple(frame.columns) != EVALUATED_SAMPLE_COLUMNS:
        raise ValueError(
            f"{path.name} must use exactly this ordered schema: "
            f"{list(EVALUATED_SAMPLE_COLUMNS)}; found {list(frame.columns)}"
        )
    if len(frame) != 1:
        raise ValueError(f"{path.name} must contain exactly one evaluated sample row")

    row = frame.iloc[0]
    variables = {name: float(row[name]) for name in DESIGN_VARIABLE_COLUMNS}
    if not all(np.isfinite(value) for value in variables.values()):
        raise ValueError(f"{path.name} contains a non-finite geometry value")
    if expected_variables is not None:
        # The optimizer verifies that an external/manual backend did not return
        # a stale result belonging to a different requested geometry.
        if set(expected_variables) != set(DESIGN_VARIABLE_COLUMNS):
            raise ValueError("expected variables do not match the evaluated sample schema")
        mismatched = [
            name
            for name in DESIGN_VARIABLE_COLUMNS
            if not np.isclose(variables[name], float(expected_variables[name]), rtol=0.0, atol=1e-9)
        ]
        if mismatched:
            raise ValueError(
                f"{path.name} geometry does not match this iteration request: {mismatched}"
            )

    frequency = finite_float_vector(row["Frequency_GHz"], "Frequency_GHz")
    s11 = finite_float_vector(row["S11_dB"], "S11_dB")
    if len(frequency) != len(s11):
        raise ValueError(f"{path.name} Frequency_GHz and S11_dB vectors must have equal lengths")
    order = np.argsort(frequency)
    return variables, CurveData(
        frequency_ghz=[frequency[index] for index in order],
        s11_db=[s11[index] for index in order],
    )
