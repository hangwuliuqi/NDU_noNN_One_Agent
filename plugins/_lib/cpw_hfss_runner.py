"""Independent PyAEDT runner for one Worker payload."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from pyhocon import ConfigFactory

LOGGER = logging.getLogger(__name__)
OPTIMIZATION_COLUMNS = ["a", "b", "L1", "L2", "L3", "S1", "S2", "S3", "f_l"]

def _plain(value: Any) -> Any:
    if hasattr(value, "items"): return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_plain(v) for v in value]
    return value

def config_path() -> Path:
    for path in (Path(__file__).resolve().parents[2] / "project.conf", Path.cwd() / "project.conf"):
        if path.is_file(): return path
    raise FileNotFoundError("project.conf not found")

@lru_cache(maxsize=1)
def load_config() -> tuple[Path, dict[str, Any]]:
    path = config_path()
    return path, _plain(ConfigFactory.parse_file(str(path)))

def hfss_config() -> dict[str, Any]: return load_config()[1]["hfss"]
def configured_simulation_type() -> str: return str(load_config()[1]["remote"]["simulation_type"])
def project_directory() -> Path:
    path = Path(str(hfss_config().get("project_dir", "./hfss_remote")))
    if not path.is_absolute(): path = load_config()[0].parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()

def safe_task_tag(context: dict[str, Any]) -> str:
    raw = f"{context.get('task_id', 'task')}_attempt_{context.get('attempt', 0)}"
    return "".join(c if c.isascii() and (c.isalnum() or c == "_") else "_" for c in raw)[:56]

def hfss_project_stem(context: dict[str, Any]) -> str: return f"{configured_simulation_type()}_{safe_task_tag(context)}"

def add_design_variables(hfss: Any, sample: dict[str, float]) -> None:
    model = load_config()[1]["model"]
    for name, value in {"h_sub": model["h_sub_mm"], "S": model["s_mm"], "G": model["g_mm"], "t": model["copper_thickness_mm"], "wave_length": model["wavelength_mm"]}.items(): hfss[name] = f"{value}mm"
    for name in OPTIMIZATION_COLUMNS: hfss[name] = f"{float(sample[name])}mm"
    hfss["port_w"] = "S + 2*G + 12mm"; hfss["port_h"] = "h_sub + 10mm"

def create_slot_pair(hfss: Any, prefix: str, x0: str, y0: str, size_x: str, size_y: str) -> list[Any]:
    right_y0 = f"-({y0}) - ({size_y})"
    return [hfss.modeler.create_box([x0, y0, "0mm"], [size_x, size_y, "t"], name=f"{prefix}_left", material="copper"), hfss.modeler.create_box([x0, right_y0, "0mm"], [size_x, size_y, "t"], name=f"{prefix}_right", material="copper")]

def create_geometry(hfss: Any) -> None:
    model = load_config()[1]["model"]; cfg = hfss_config(); hfss.modeler.model_units = "mm"
    material = hfss.materials.add_material("FR4_Chen2006"); material.permittivity = str(model["substrate_permittivity"]); material.dielectric_loss_tangent = str(model["substrate_loss_tangent"])
    substrate = hfss.modeler.create_box(["-b/2", "-a/2", "-h_sub"], ["b", "a", "h_sub"], name="FR4_substrate", material="FR4_Chen2006")
    substrate.transparency = 0.8
    patch = hfss.modeler.create_box(["-b/2", "-a/2", "0mm"], ["b", "a", "t"], name="patch", material="copper")
    tools=[]
    tools += create_slot_pair(hfss,"slot_square","(b/2-f_l-S3-S2-S1)","(-S/2-L2)","(S1+S2+S3)","L2")
    tools += create_slot_pair(hfss,"slot_L1","(b/2-f_l-S3-S2-S1)","(-S/2-L1)","S1","L1")
    tools += create_slot_pair(hfss,"slot_L3","(b/2-f_l-S3)","(-S/2-L3)","S3","L3")
    tools += create_slot_pair(hfss,"slot_G","(b/2-f_l-S3-S2-S1)","(-S/2-G)","S1+S2+S3+f_l","G")
    hfss.modeler.subtract(blank_list=[patch], tool_list=tools, keep_originals=False)
    air = hfss.modeler.create_box(["-b/2-wave_length/4-10mm","-a/2-wave_length/4-10mm","-h_sub-wave_length/4"],["b+wave_length/4+10mm","a+wave_length/2+20mm","h_sub+wave_length/2+t"],name="Radiation_airbox",material="vacuum")
    hfss.assign_radiation_boundary_to_objects(air,"Radiation")
    sheet=hfss.modeler.create_rectangle("YZ",["b/2","-port_w/2","-h_sub"],["port_w","port_h"],name="CPW_wave_port_sheet",is_covered=True)
    hfss.wave_port(sheet,integration_line=hfss.axis_directions.ZNeg,impedance=50,name="P1_CPW"); hfss.modeler.fit_all()

def create_setup(hfss: Any) -> None:
    c=hfss_config(); hfss.create_setup(name=str(c["setup_name"]),Frequency=f"{c['setup_frequency_ghz']}GHz",MaximumPasses=int(c["max_passes"]),MinimumPasses=int(c["min_passes"]),MinimumConvergedPasses=int(c["min_converged_passes"]),MaxDeltaS=float(c["max_delta_s"])); hfss.create_linear_step_sweep(setup=str(c["setup_name"]),unit="GHz",start_frequency=float(c["sweep_start_ghz"]),stop_frequency=float(c["sweep_stop_ghz"]),step_size=float(c["sweep_step_ghz"]),name=str(c["sweep_name"]),save_fields=False,sweep_type="Discrete")

def export_s11(hfss: Any) -> tuple[list[float], list[float]]:
    c=hfss_config(); expr=str(c["s11_expression"]); data=hfss.post.get_solution_data(expressions=expr,setup_sweep_name=str(c["sweep_name"])); values=data.get_expression_data(expr); return [float(v) for v in values[0]],[float(v) for v in values[1]]

def run_hfss_simulation(sample: dict[str, float], *, context: dict[str, Any]) -> dict[str, list[float]]:
    from ansys.aedt.core import Hfss
    c=hfss_config(); stem=hfss_project_stem(context); hfss=None
    try:
        hfss=Hfss(project=str(project_directory()/f"{stem}.aedt"),design=stem,solution_type="DrivenModal",version=str(c["version"]),non_graphical=bool(c["non_graphical"]),new_desktop=True,close_on_exit=False,remove_lock=True)
        add_design_variables(hfss,sample); create_geometry(hfss); create_setup(hfss); hfss.save_project(); hfss.analyze_setup(str(c["setup_name"])); hfss.save_project(); frequency,s11=export_s11(hfss); return {"frequency_data":frequency,"s11_data":s11}
    finally:
        if hfss is not None:
            try: hfss.close_project()
            except Exception: LOGGER.exception("HFSS project close failed")
            try: hfss.release_desktop(close_projects=True,close_desktop=True)
            except Exception: LOGGER.exception("HFSS Desktop release failed")
