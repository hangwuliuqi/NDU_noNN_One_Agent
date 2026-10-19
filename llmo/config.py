"""Load HOCON configuration and apply non-persistent command-line overrides."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from pyhocon import ConfigFactory, ConfigTree


def parse_scalar(value: str) -> Any:
    """按照 HOCON 标量语义解析一个命令行覆盖值。"""
    wrapper = ConfigFactory.parse_string(f"value = {value}")
    return wrapper.get("value")


def load_config(path: str | Path, overrides: list[str] | None = None) -> ConfigTree:
    """读取主配置并把重复出现的 ``--set key=value`` 写入同一棵配置树。"""
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"configuration file not found: {config_path}")
    conf = ConfigFactory.parse_file(config_path)
    for override in overrides or []:
        if "=" not in override:
            raise ValueError(f"invalid --set value {override!r}; expected key=value")
        key, raw_value = override.split("=", 1)
        # 例如 --set optimization.top_k=5 会覆盖文件值，但不会修改 project.conf 本身。
        conf.put(key.strip(), parse_scalar(raw_value.strip()))

    # 这两个路径由程序注入，供其他模块统一解析相对路径，不要求用户在配置文件中填写。
    conf.put("project.config_path", str(config_path))
    conf.put("project.root_dir", str(config_path.parent))
    return conf


def allocate_run_directory(base_dir: Path, now: datetime | None = None) -> Path:
    """Create a unique ``<base-name>_<MMDD_HHMM>`` directory for one run.

    The minute timestamp is the human-readable run identifier. A numeric suffix
    is added only when the same minute is launched more than once, so no prior
    run's pools, model, requests, or logs can be overwritten.
    """
    timestamp = (now or datetime.now()).strftime("%m%d_%H%M")
    candidate = base_dir.parent / f"{base_dir.name}_{timestamp}"
    suffix = 1
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = base_dir.parent / f"{base_dir.name}_{timestamp}_{suffix:02d}"
            suffix += 1


def resolve_run_directory(
    root_dir: Path,
    configured_base: Path,
    output_dir: str | None,
    resume_dir: str | None,
) -> tuple[Path, bool]:
    """Resolve one new or resumed output directory without silent overwrites."""
    if output_dir and resume_dir:
        raise ValueError("--output-dir and --resume-dir cannot be used together")

    def resolve_user_path(value: str) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (root_dir / path).resolve()

    if resume_dir:
        directory = resolve_user_path(resume_dir)
        if not directory.is_dir():
            raise FileNotFoundError(f"resume directory does not exist: {directory}")
        return directory, True
    if output_dir:
        directory = resolve_user_path(output_dir)
        if directory.exists():
            raise FileExistsError(
                f"output directory already exists: {directory}. Use --resume-dir to continue it."
            )
        directory.mkdir(parents=True, exist_ok=False)
        return directory, False

    base_dir = configured_base if configured_base.is_absolute() else root_dir / configured_base
    return allocate_run_directory(base_dir), False


def build_argument_parser() -> argparse.ArgumentParser:
    """定义主程序的配置文件、配置覆盖和只校验三个命令行入口。"""
    parser = argparse.ArgumentParser(description="LLM-assisted dual-band antenna optimizer")
    parser.add_argument("--config", default="project.conf", help="path to the HOCON configuration file")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="key=value",
        help="override a configuration-tree value; may be repeated",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate configuration and input data without starting optimization",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--output-dir",
        help="new output directory name/path; it must not already exist",
    )
    output_group.add_argument(
        "--resume-dir",
        help="existing output directory name/path to resume from",
    )
    return parser
