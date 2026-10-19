"""Create per-run logs and append every LLM request/response in call order."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyhocon import ConfigTree

from .json_utils import dumps_readable


class InteractionLogger:
    """把所有 LLM 交互按严格调用顺序写入带缩进的 JSON 数组。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sequence = self._load_sequence()

    def _load_sequence(self) -> int:
        """Resume numbering from the current human-readable JSON array."""
        if not self.path.exists():
            return 0
        text = self.path.read_text(encoding="utf-8").strip()
        if not text:
            return 0
        entries = json.loads(text)
        if not isinstance(entries, list):
            raise TypeError(f"{self.path.name} must contain a JSON array")
        return int(entries[-1].get("sequence", 0)) if entries else 0

    def write(
        self,
        *,
        iteration: int,
        module: str,
        provider: str,
        model_id: str,
        attempt: int,
        prompt: str,
        response: str | None,
        status: str,
        error: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        # sequence 是单次程序运行内的单调序号，比仅依赖时间戳更适合检查重试和模块先后顺序。
        self.sequence += 1
        entry: dict[str, Any] = {
            "sequence": self.sequence,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "iteration": iteration,
            "module": module,
            "provider": provider,
            "model_id": model_id,
            "attempt": attempt,
            "status": status,
            "prompt": prompt,
            "response": response,
            "error": error,
        }
        if diagnostics is not None:
            entry["diagnostics"] = diagnostics
        entries: list[dict[str, Any]] = []
        if self.path.exists():
            text = self.path.read_text(encoding="utf-8").strip()
            if text:
                entries = json.loads(text)
                if not isinstance(entries, list):
                    raise TypeError(f"{self.path.name} must contain a JSON array")
        entries.append(entry)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            dumps_readable(entries) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def configure_logging(conf: ConfigTree, runtime_dir: Path) -> tuple[logging.Logger, InteractionLogger, Path]:
    """为每次启动创建独立日志目录，避免不同优化运行的记录相互穿插。"""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_dir = runtime_dir / "logs" / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, conf.get_string("logging.level").upper(), logging.INFO)
    handlers: list[logging.Handler] = [
        logging.FileHandler(run_dir / conf.get_string("logging.run_log_name"), encoding="utf-8")
    ]
    if conf.get_bool("logging.console"):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    logger = logging.getLogger("llmo")
    interactions = InteractionLogger(run_dir / conf.get_string("logging.interaction_log_name"))
    return logger, interactions, run_dir
