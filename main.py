#!/usr/bin/env python3
"""
Quant Flow 入口：加载配置 → 启动引擎。

永续与网格两种策略由 config.yaml 的 trading.perp_enabled / grid_enabled
开关控制，可独立或并行运行。所有编排逻辑在 src/engine.py。
"""

import argparse
import os
import signal
import sys

from src.config import Config
from src.engine import Engine
from src.utils.logger import get_logger


def main() -> int:
    parser = argparse.ArgumentParser(description="Quant Flow - AI 加密货币自动交易系统")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--env-file", default=None, help=".env 文件路径")
    args = parser.parse_args()

    logger = get_logger(os.getenv("LOG_LEVEL", "INFO"))
    try:
        config = Config.load(config_path=args.config, env_file=args.env_file)
    except ValueError as e:
        logger.print_error(f"配置加载失败: {e}")
        return 1

    engine = Engine(config)

    def handle_signal(signum, frame):
        if engine.is_running:
            logger.print_info("收到停止信号，触发优雅停机（再按一次强制退出）")
            engine.is_running = False
        else:
            logger.print_warning("强制退出")
            sys.exit(1)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
