"""Build, solve, and export one CPW dual-band slot candidate with PyAEDT.

Only the nine optimized dimensions come from the optimizer request. Every
material, substrate, CPW, conductor, air-box, port, setup, and sweep setting is
fixed here to the values used by the validated PSO ``cpw_hfss_runner.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .parquet_package import write_evaluated_sample_package


LOGGER = logging.getLogger(__name__)

OPTIMIZED_VARIABLE_NAMES = (
    "a",
    "b",
    "l1",
    "l2",
    "l3",
    "s1",
    "s2",
    "s3",
    "f_l",
)

# Fixed model values copied from PSO/pso.conf and its cpw_hfss_runner.py.
SUBSTRATE_THICKNESS_MM = 1.6
CPW_SIGNAL_WIDTH_MM = 3.0
CPW_GAP_MM = 0.42
COPPER_THICKNESS_MM = 0.035
AIRBOX_REFERENCE_WAVELENGTH_MM = 150.0
SUBSTRATE_RELATIVE_PERMITTIVITY = 4.3
SUBSTRATE_LOSS_TANGENT = 0.02
PORT_EXTRA_WIDTH_MM = 12.0
PORT_EXTRA_HEIGHT_MM = 10.0
PORT_REFERENCE_IMPEDANCE_OHM = 50.0

MATERIAL_NAME = "FR4_Chen2006"
CONDUCTOR_MATERIAL = "copper"
RADIATION_REGION_MATERIAL = "vacuum"
RADIATION_BOUNDARY_NAME = "Radiation"
PORT_NAME = "P1_CPW"

SETUP_NAME = "Setup_PSO"
SWEEP_NAME = "Sweep_PSO"
SETUP_FREQUENCY_GHZ = 4.0
MAXIMUM_PASSES = 30
MINIMUM_PASSES = 2
MINIMUM_CONVERGED_PASSES = 2
MAX_DELTA_S = 0.02
SWEEP_START_GHZ = 2.0
SWEEP_STOP_GHZ = 6.0
SWEEP_STEP_GHZ = 0.01
S11_EXPRESSION = "dB(S(P1_CPW,P1_CPW))"


def get_timestamp_tag() -> str:
    """Return a collision-resistant ASCII tag for one isolated AEDT project."""
    return datetime.now().strftime("%m%d_%H%M%S_%f")


def get_project_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent if script_dir.name.lower() in {"code", "llmo"} else script_dir


def load_optimization_request(
    request_json: Path,
) -> tuple[int, dict[str, float], Path | None]:
    """Read and strictly validate the optimizer-to-HFSS request contract."""
    raw = json.loads(request_json.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("HFSS request JSON must contain an object")
    variables_raw = raw.get("variables")
    if not isinstance(variables_raw, dict):
        raise TypeError("HFSS request field 'variables' must be an object")
    if set(variables_raw) != set(OPTIMIZED_VARIABLE_NAMES):
        raise ValueError(
            "HFSS request variables must contain exactly "
            f"{list(OPTIMIZED_VARIABLE_NAMES)}"
        )
    variables: dict[str, float] = {}
    for name in OPTIMIZED_VARIABLE_NAMES:
        value = float(variables_raw[name])
        if not math.isfinite(value):
            raise ValueError(f"HFSS request variable {name} must be finite")
        variables[name] = value
    iteration = int(raw.get("iteration", 0))
    output_raw = raw.get("expected_output_parquet")
    expected_output = Path(str(output_raw)).expanduser() if output_raw else None
    return iteration, variables, expected_output


def add_design_variables(hfss: Any, optimized_variables: dict[str, float]) -> None:
    """Inject nine optimized dimensions plus the fixed PSO model constants."""
    if set(optimized_variables) != set(OPTIMIZED_VARIABLE_NAMES):
        raise ValueError(
            "optimized variables must contain exactly "
            f"{list(OPTIMIZED_VARIABLE_NAMES)}"
        )

    fixed = {
        "h_sub": f"{SUBSTRATE_THICKNESS_MM}mm",
        "S": f"{CPW_SIGNAL_WIDTH_MM}mm",
        "G": f"{CPW_GAP_MM}mm",
        "t": f"{COPPER_THICKNESS_MM}mm",
        "wave_length": f"{AIRBOX_REFERENCE_WAVELENGTH_MM}mm",
    }
    for name, value in fixed.items():
        hfss[name] = value

    # Optimizer names remain lowercase; HFSS keeps the validated L1/S1 casing.
    for name in ("a", "b", "f_l"):
        hfss[name] = f"{float(optimized_variables[name]):.2f}mm"
    for optimizer_name, hfss_name in (
        ("l1", "L1"),
        ("l2", "L2"),
        ("l3", "L3"),
        ("s1", "S1"),
        ("s2", "S2"),
        ("s3", "S3"),
    ):
        hfss[hfss_name] = f"{float(optimized_variables[optimizer_name]):.2f}mm"

    hfss["port_w"] = f"S + 2*G + {PORT_EXTRA_WIDTH_MM:g}mm"
    hfss["port_h"] = f"h_sub + {PORT_EXTRA_HEIGHT_MM:g}mm"


def create_slot_pair(
    hfss: Any,
    prefix: str,
    x0: str,
    y0: str,
    size_x: str,
    size_y: str,
) -> list[Any]:
    right_y0 = f"-({y0}) - ({size_y})"
    left = hfss.modeler.create_box(
        [x0, y0, "0mm"],
        [size_x, size_y, "t"],
        name=f"{prefix}_left",
        material=CONDUCTOR_MATERIAL,
    )
    right = hfss.modeler.create_box(
        [x0, right_y0, "0mm"],
        [size_x, size_y, "t"],
        name=f"{prefix}_right",
        material=CONDUCTOR_MATERIAL,
    )
    return [left, right]


def create_geometry(hfss: Any) -> None:
    """Build the same substrate, slotted patch, air box, and port as PSO."""
    hfss.modeler.model_units = "mm"

    material = hfss.materials.add_material(MATERIAL_NAME)
    material.permittivity = str(SUBSTRATE_RELATIVE_PERMITTIVITY)
    material.dielectric_loss_tangent = str(SUBSTRATE_LOSS_TANGENT)

    substrate = hfss.modeler.create_box(
        ["-b/2", "-a/2", "-h_sub"],
        ["b", "a", "h_sub"],
        name="FR4_substrate",
        material=MATERIAL_NAME,
    )
    substrate.color = (111, 208, 154)
    substrate.transparency = 0.8

    patch = hfss.modeler.create_box(
        ["-b/2", "-a/2", "0mm"],
        ["b", "a", "t"],
        name="patch",
        material=CONDUCTOR_MATERIAL,
    )

    tools: list[Any] = []
    tools.extend(
        create_slot_pair(
            hfss,
            "slot_square",
            "(b/2-f_l-S3-S2-S1)",
            "(-S/2-L2)",
            "(S1+S2+S3)",
            "L2",
        )
    )
    tools.extend(
        create_slot_pair(
            hfss,
            "slot_L1",
            "(b/2-f_l-S3-S2-S1)",
            "(-S/2-L1)",
            "S1",
            "L1",
        )
    )
    tools.extend(
        create_slot_pair(
            hfss,
            "slot_L3",
            "(b/2-f_l-S3)",
            "(-S/2-L3)",
            "S3",
            "L3",
        )
    )
    tools.extend(
        create_slot_pair(
            hfss,
            "slot_G",
            "(b/2-f_l-S3-S2-S1)",
            "(-S/2-G)",
            "S1+S2+S3+f_l",
            "G",
        )
    )
    hfss.modeler.subtract(
        blank_list=[patch], tool_list=tools, keep_originals=False
    )
    patch.color = (255, 0, 0)
    patch.transparency = 0.5

    air_box = hfss.modeler.create_box(
        [
            "-b/2-wave_length/4-10mm",
            "-a/2-wave_length/4-10mm",
            "-h_sub-wave_length/4",
        ],
        [
            "b+wave_length/4+10mm",
            "a+wave_length/4+10mm+wave_length/4+10mm",
            "h_sub+wave_length/4+t+wave_length/4",
        ],
        name="Radiation_airbox",
        material=RADIATION_REGION_MATERIAL,
    )
    hfss.assign_radiation_boundary_to_objects(
        air_box, RADIATION_BOUNDARY_NAME
    )

    port_sheet = hfss.modeler.create_rectangle(
        "YZ",
        ["b/2", "-port_w/2", "-h_sub"],
        ["port_w", "port_h"],
        name="CPW_wave_port_sheet",
        is_covered=True,
    )
    hfss.wave_port(
        port_sheet,
        integration_line=hfss.axis_directions.ZNeg,
        impedance=PORT_REFERENCE_IMPEDANCE_OHM,
        name=PORT_NAME,
    )
    hfss.modeler.fit_all()


def create_setup(hfss: Any) -> None:
    """Create the fixed PSO adaptive setup and discrete sweep."""
    hfss.create_setup(
        name=SETUP_NAME,
        Frequency=f"{SETUP_FREQUENCY_GHZ}GHz",
        MaximumPasses=MAXIMUM_PASSES,
        MinimumPasses=MINIMUM_PASSES,
        MinimumConvergedPasses=MINIMUM_CONVERGED_PASSES,
        MaxDeltaS=MAX_DELTA_S,
    )
    hfss.create_linear_step_sweep(
        setup=SETUP_NAME,
        unit="GHz",
        start_frequency=SWEEP_START_GHZ,
        stop_frequency=SWEEP_STOP_GHZ,
        step_size=SWEEP_STEP_GHZ,
        name=SWEEP_NAME,
        save_fields=False,
        sweep_type="Discrete",
    )


def export_s11(hfss: Any) -> tuple[list[float], list[float]]:
    """Extract S11 with the same bare sweep-name call as the PSO runner."""
    solution_data = hfss.post.get_solution_data(
        expressions=S11_EXPRESSION,
        setup_sweep_name=SWEEP_NAME,
    )
    values = solution_data.get_expression_data(S11_EXPRESSION)
    if len(values) < 2 or len(values[0]) < 2 or len(values[0]) != len(values[1]):
        raise RuntimeError("HFSS returned empty or malformed S11 solution data")
    return [float(value) for value in values[0]], [float(value) for value in values[1]]


def _load_hfss_class():
    try:
        from ansys.aedt.core import Hfss
    except ImportError as exc:  # pragma: no cover - requires local PyAEDT.
        raise RuntimeError(
            "PyAEDT is required for the real HFSS runner. Install "
            "requirements-hfss.txt in the Python environment used by the runner."
        ) from exc
    return Hfss


def _release(hfss: Any) -> None:
    """Close the active project, then release Desktop, matching the PSO runner."""
    if hfss is None:
        return
    try:
        hfss.close_project()
    except Exception:
        LOGGER.warning("HFSS project close failed; continuing Desktop release", exc_info=True)
    try:
        hfss.release_desktop(close_projects=True, close_desktop=True)
    except Exception:
        LOGGER.warning("HFSS Desktop release failed", exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="2023.2")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--output-parquet", required=True)
    parser.add_argument("--non-graphical", action="store_true")
    args = parser.parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    request_path = Path(args.request_json).expanduser().resolve()
    result_path = Path(args.output_parquet).expanduser().resolve()
    iteration, optimized_variables, expected_output = load_optimization_request(
        request_path
    )
    if expected_output is not None and result_path != expected_output.resolve():
        raise ValueError("--output-parquet does not match request expected_output_parquet")

    tag = get_timestamp_tag()
    project_stem = f"NN_Direct_update_{tag}_i{iteration:04d}"
    project_path = project_dir / f"{project_stem}.aedt"
    hfss = None
    frequency: list[float] | None = None
    s11: list[float] | None = None
    try:
        Hfss = _load_hfss_class()
        hfss = Hfss(
            project=str(project_path),
            design=project_stem,
            solution_type="DrivenModal",
            version=args.version,
            non_graphical=args.non_graphical,
            new_desktop=True,
            close_on_exit=False,
            remove_lock=True,
        )
        add_design_variables(hfss, optimized_variables)
        create_geometry(hfss)
        create_setup(hfss)
        hfss.save_project()
        hfss.analyze_setup(SETUP_NAME)
        hfss.save_project()
        frequency, s11 = export_s11(hfss)
    finally:
        _release(hfss)
    if frequency is None or s11 is None:
        raise RuntimeError("HFSS completed without an S11 curve")
    # The HFSS session is already released here. Packaging is project I/O, not
    # part of the electromagnetic solve lifecycle copied from the PSO runner.
    write_evaluated_sample_package(
        result_path, optimized_variables, frequency, s11
    )


if __name__ == "__main__":
    main()
