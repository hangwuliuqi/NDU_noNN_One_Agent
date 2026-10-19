"""Command-line entry point for the closed-loop dual-band antenna optimizer.

It loads one HOCON configuration, creates run-scoped logs, and delegates all
optimization work to :class:`llmo.optimizer.OptimizationEngine`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from llmo.config import build_argument_parser, load_config, resolve_run_directory
from llmo.llm_client import LlmConnectionError
from llmo.optimizer import OptimizationEngine
from llmo.run_logging import configure_logging


def main() -> int:
    # 命令行只负责选择配置文件和临时覆盖项；后续模块统一从配置树读取参数，
    # 避免在不同 Python 文件中重复写死同一个运行变量。
    args = build_argument_parser().parse_args()
    conf = load_config(args.config, args.set)

    # 所有相对路径都以配置文件所在目录为项目根目录进行解析，而不是依赖启动命令的当前目录。
    root_dir = Path(conf.get_string("project.root_dir"))
    runtime_base = Path(conf.get_string("project.runtime_dir"))
    runtime_dir, is_resuming = resolve_run_directory(
        root_dir, runtime_base, args.output_dir, args.resume_dir
    )
    # Dependent paths are injected because HOCON interpolation happened before
    # the user-selected/timestamped directory was allocated.
    conf.put("project.runtime_dir", str(runtime_dir))
    conf.put("simulation.request_dir", str(runtime_dir / "hfss_requests"))
    conf.put("simulation.response_dir", str(runtime_dir / "hfss_responses"))
    logger, interactions, run_dir = configure_logging(conf, runtime_dir)
    logger.info("configuration loaded from %s", conf.get_string("project.config_path"))
    logger.info("ordered run logs: %s", run_dir)
    logger.info("run output directory: %s", runtime_dir)
    if is_resuming:
        logger.info("resume mode enabled; existing sample pools and completed iterations will be reused")
    try:
        engine = OptimizationEngine(conf, root_dir, runtime_dir, interactions, logger)
        if args.validate_only:
            # 仅校验模式按启动配置准备样本池，不调用 LLM 或 HFSS。
            engine.validate_and_bootstrap()
            logger.info("configuration and sample-pool validation passed")
            return 0
        result = engine.run()
        return 0 if result is not None else 2
    except LlmConnectionError as exc:
        # 采样 LLM 使用配置中的重试上限；耗尽后用退出码 5 标识连接/响应故障。
        logger.critical("LLM connection aborted after configured attempts: %s", exc)
        return 5
    except Exception:
        logger.exception("optimization terminated because of an unrecoverable error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
