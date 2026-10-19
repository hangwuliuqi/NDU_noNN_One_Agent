"""Bridge one selected geometry to PyAEDT, manual, command, or mock simulation backends."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from pyhocon import ConfigTree

from .models import CurveData, VariableSpec
from .parquet_package import read_evaluated_sample_package, write_evaluated_sample_package


class HfssSimulator:
    """统一封装内置 PyAEDT、人工交换、外部命令和 mock 仿真模式。"""

    def __init__(
        self,
        conf: ConfigTree,
        runtime_dir: Path,
        specs: dict[str, VariableSpec],
        logger: logging.Logger,
    ) -> None:
        self.conf = conf
        self.runtime_dir = runtime_dir
        self.specs = specs
        self.logger = logger
        self.root_dir = Path(conf.get_string("project.root_dir"))
        self.mode = conf.get_string("simulation.mode").lower()
        # Request and response directories are persistent exchange points. They
        # make manual and command backends use exactly the same contract as PyAEDT.
        request_raw = Path(conf.get_string("simulation.request_dir"))
        response_raw = Path(conf.get_string("simulation.response_dir"))
        self.request_dir = request_raw if request_raw.is_absolute() else self.root_dir / request_raw
        self.response_dir = response_raw if response_raw.is_absolute() else self.root_dir / response_raw
        self.request_dir.mkdir(parents=True, exist_ok=True)
        self.response_dir.mkdir(parents=True, exist_ok=True)

    def simulate(self, variables: dict[str, float], iteration: int) -> CurveData:
        """写出本轮请求，按配置模式取得响应 Parquet 包，再返回完整 S11 曲线。"""
        stem = f"iteration_{iteration:04d}"
        request_path = self.request_dir / f"{stem}.json"
        output_path = self.response_dir / f"{stem}.parquet"
        # 请求 JSON 是优化器与 HFSS 自动化脚本之间的稳定接口，明确给出输入变量和预期输出位置。
        request_path.write_text(
            json.dumps(
                {
                    "iteration": iteration,
                    "variables": variables,
                    "expected_output_parquet": str(output_path.resolve()),
                    "columns": [
                        "a", "b", "l1", "l2", "l3", "s1", "s2", "s3", "f_l",
                        "Frequency_GHz", "S11_dB",
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if self.mode == "pyaedt":
            # 内置后端读取同一请求 JSON，把九个变量注入用户已调通的 HFSS 建模脚本，
            # 自动求解并把完整变量和 S11 曲线导出到本轮 response Parquet 包。
            self._run_pyaedt(request_path, output_path, iteration)
        elif self.mode == "manual":
            # 人工模式不会自动启动 HFSS，而是在指定目录等待用户或外部程序写入 Parquet。
            self.logger.info("HFSS request written to %s", request_path)
            self.logger.info("waiting for HFSS response Parquet at %s", output_path)
            self._wait_for_file(output_path)
        elif self.mode == "command":
            # 命令模式把请求路径、输出路径和迭代号填入 command_template 后执行外部程序。
            self._run_command(request_path, output_path, iteration)
        elif self.mode == "mock":
            # mock 曲线仅用于检查闭环代码能否运行，绝不能作为真实 HFSS 证据。
            self.logger.warning("simulation.mode=mock: generated curves are for workflow testing only, not HFSS evidence")
            self._write_mock_curve(variables, output_path, iteration)
        else:
            raise ValueError(f"unsupported simulation.mode={self.mode!r}")
        # Validate both the Parquet schema and returned geometry before any curve
        # reaches metrics or the persistent sample pool.
        _, curve = read_evaluated_sample_package(output_path, variables)
        return curve

    def _build_pyaedt_command(
        self,
        request_path: Path,
        output_path: Path,
    ) -> list[str]:
        """构造内置真实 HFSS 后端命令，不在优化进程内直接持有 AEDT 会话。"""
        cfg = self.conf.get_config("simulation.pyaedt")
        python_raw = cfg.get_string("python_executable").strip()
        python_executable = python_raw or sys.executable
        project_raw = Path(cfg.get_string("project_dir"))
        project_dir = project_raw if project_raw.is_absolute() else self.root_dir / project_raw
        project_dir.mkdir(parents=True, exist_ok=True)

        command = [
            python_executable,
            "-m",
            "llmo.hfss_runner",
            "--request-json",
            str(request_path.resolve()),
            "--output-parquet",
            str(output_path.resolve()),
            "--project-dir",
            str(project_dir.resolve()),
            "--version",
            cfg.get_string("aedt_version"),
        ]
        if cfg.get_bool("non_graphical"):
            command.append("--non-graphical")
        return command

    def _run_pyaedt(self, request_path: Path, output_path: Path, iteration: int) -> None:
        """启动随项目提供的 PyAEDT 建模器并等待本轮真实 Parquet 包。"""
        command = self._build_pyaedt_command(request_path, output_path)
        self.logger.info("starting built-in PyAEDT HFSS model for iteration %d", iteration)
        result = subprocess.run(
            command,
            cwd=self.root_dir,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
        if result.stdout:
            self.logger.info("PyAEDT HFSS stdout:\n%s", result.stdout.rstrip())
        if result.stderr:
            self.logger.warning("PyAEDT HFSS stderr:\n%s", result.stderr.rstrip())
        if result.returncode != 0:
            raise RuntimeError(
                f"built-in PyAEDT HFSS runner failed with exit code {result.returncode}"
            )
        if not output_path.exists():
            raise FileNotFoundError(
                f"PyAEDT HFSS solve completed but did not create {output_path}"
            )

    def _wait_for_file(self, path: Path) -> None:
        """按照配置的轮询间隔等待非空 HFSS 响应文件，超时后明确报错。"""
        timeout = self.conf.get_float("simulation.response_timeout_seconds")
        interval = self.conf.get_float("simulation.poll_interval_seconds")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size > 0:
                return
            time.sleep(interval)
        raise TimeoutError(f"HFSS response did not arrive within {timeout} seconds: {path}")

    def _run_command(self, request_path: Path, output_path: Path, iteration: int) -> None:
        """渲染并执行外部 HFSS 命令，同时把标准输出和错误输出写入运行日志。"""
        template = self.conf.get_string("simulation.command_template").strip()
        if not template:
            raise ValueError("simulation.command_template is empty while simulation.mode=command")
        rendered = template.format(
            request_json=str(request_path.resolve()),
            output_parquet=str(output_path.resolve()),
            iteration=iteration,
        )
        command = shlex.split(rendered, posix=os.name != "nt")
        self.logger.info("starting configured HFSS command for iteration %d", iteration)
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.stdout:
            self.logger.info("HFSS command stdout:\n%s", result.stdout.rstrip())
        if result.stderr:
            self.logger.warning("HFSS command stderr:\n%s", result.stderr.rstrip())
        if result.returncode != 0:
            raise RuntimeError(f"HFSS command failed with exit code {result.returncode}")
        if not output_path.exists():
            raise FileNotFoundError(f"HFSS command completed but did not create {output_path}")

    def _write_mock_curve(self, variables: dict[str, float], path: Path, iteration: int) -> None:
        """生成包含两个可控谐振谷的测试曲线，专门用于无 HFSS 环境下的流程验证。"""
        cfg = self.conf.get_config("simulation.mock")
        frequency = np.linspace(
            cfg.get_float("frequency_start_ghz"),
            cfg.get_float("frequency_end_ghz"),
            cfg.get_int("frequency_points"),
        )
        # mock 方程先归一化变量，避免不同毫米范围导致某个变量在测试函数中被无意放大。
        normalized = {
            name: (variables[name] - spec.minimum) / (spec.maximum - spec.minimum)
            for name, spec in self.specs.items()
        }
        # This is intentionally synthetic: it supplies reproducible workflow
        # coverage only and must never be interpreted as an electromagnetic model.
        low_resonance = self.conf.get_float("antenna.targets.low_frequency_ghz")
        low_resonance += cfg.get_float("low_l1_shift_ghz") * (
            normalized["l1"] - cfg.get_float("low_l1_center")
        )
        low_resonance += cfg.get_float("low_feed_shift_ghz") * (
            normalized["f_l"] - cfg.get_float("low_feed_center")
        )
        high_resonance = self.conf.get_float("antenna.targets.high_frequency_ghz")
        high_resonance += cfg.get_float("high_l3_shift_ghz") * (
            normalized["l3"] - cfg.get_float("high_l3_center")
        )
        high_resonance += cfg.get_float("high_l2_shift_ghz") * (
            normalized["l2"] - cfg.get_float("high_l2_center")
        )
        low_alignment = abs(
            normalized["s1"] - cfg.get_float("low_s1_alignment_center")
        ) + cfg.get_float("low_b_alignment_weight") * abs(
            normalized["b"] - cfg.get_float("low_b_alignment_center")
        )
        high_alignment = abs(
            normalized["s3"] - cfg.get_float("high_s3_alignment_center")
        ) + cfg.get_float("high_s2_alignment_weight") * abs(
            normalized["s2"] - cfg.get_float("high_s2_alignment_center")
        )
        maximum_depth = cfg.get_float("maximum_resonance_depth_db")
        depth_reduction = cfg.get_float("depth_reduction_per_alignment_db")
        alignment_cap = cfg.get_float("alignment_cap")
        low_depth = maximum_depth - depth_reduction * min(
            low_alignment, alignment_cap
        )
        high_depth = maximum_depth - depth_reduction * min(
            high_alignment, alignment_cap
        )
        baseline = cfg.get_float("baseline_s11_db")
        low_linewidth = cfg.get_float("low_linewidth_ghz")
        high_linewidth = cfg.get_float("high_linewidth_ghz")
        s11 = baseline - low_depth / (
            1.0 + ((frequency - low_resonance) / low_linewidth) ** 2
        )
        s11 -= high_depth / (
            1.0 + ((frequency - high_resonance) / high_linewidth) ** 2
        )
        noise_std = cfg.get_float("noise_std_db")
        if noise_std > 0:
            rng = np.random.default_rng(
                self.conf.get_int("project.seed")
                + iteration * cfg.get_int("noise_seed_iteration_stride")
            )
            s11 += rng.normal(0.0, noise_std, size=len(s11))
        write_evaluated_sample_package(path, variables, frequency, s11)
