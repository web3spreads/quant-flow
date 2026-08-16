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


def _check_writable_dirs() -> bool:
    """启动前检查 logs/ 与 data/ 可写，给出可执行的中文修复指引。

    Docker 场景高发：挂载卷属主是 root 而容器进程是 app（UID 1000）时，
    直接启动会 PermissionError 崩溃循环且报错难懂。此处提前拦截并说清怎么修。
    """
    import uuid

    ok = True
    for d in ("logs", "data"):
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, f".write_probe_{uuid.uuid4().hex[:8]}")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.unlink(probe)
        except OSError as e:
            ok = False
            print(f"❌ 目录 {d}/ 不可写: {e}")
            print(
                f"   Docker 部署请在宿主机执行: sudo chown -R 1000:1000 ./{d}"
                f"（容器内进程以 app 用户/UID 1000 运行）"
            )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Quant Flow - AI 加密货币自动交易系统")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--env-file", default=None, help=".env 文件路径")
    args = parser.parse_args()

    if not _check_writable_dirs():
        return 1

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
