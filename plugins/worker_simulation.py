"""Standalone ele_sim Worker plugin for remote CPW HFSS jobs."""
from __future__ import annotations

import math
from pathlib import Path
import sys
from typing import Any

from ele_sim_worker import SimulatorPlugin

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
from _lib.cpw_hfss_runner import OPTIMIZATION_COLUMNS, configured_simulation_type, hfss_config, run_hfss_simulation

class CpwS11HfssPlugin(SimulatorPlugin):
    simulation_type = configured_simulation_type()
    version = "1.0-llmo"
    max_runtime_seconds = float(hfss_config().get("max_runtime_seconds", 7200))

    def check_environment(self) -> None:
        from ansys.aedt.core import Hfss  # noqa: F401
        if not math.isfinite(self.max_runtime_seconds) or self.max_runtime_seconds <= 0:
            raise ValueError("hfss.max_runtime_seconds must be positive")

    def validate(self, payload: dict[str, Any]) -> None:
        if set(payload) != set(OPTIMIZATION_COLUMNS):
            raise ValueError(f"payload fields mismatch: expected {OPTIMIZATION_COLUMNS}")
        for name in OPTIMIZATION_COLUMNS:
            if isinstance(payload[name], bool) or not isinstance(payload[name], (int, float)) or not math.isfinite(float(payload[name])):
                raise ValueError(f"payload.{name} must be a finite real number")

    def run(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        self.validate(payload)
        return run_hfss_simulation({name: float(payload[name]) for name in OPTIMIZATION_COLUMNS}, context=context)

    def cleanup(self, context: dict[str, Any]) -> None:
        return None
