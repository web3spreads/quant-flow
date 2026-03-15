#!/usr/bin/env python3
"""
Grid Flow - 动态 AI 天地单
"""

import argparse
import sys
import traceback
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.agent.grid_agent import GridAgent
from src.config import get_config
from src.data.indicators import TechnicalIndicators
from src.data.market_data import MarketDataFetcher
from src.llm import LLMClientManager
from src.notification.notifier import Notifier
from src.trading.client import HyperliquidClient
from src.trading.grid_manager import GridManager
from src.trading.order_manager import OrderManager
from src.utils.logger import get_logger


class GridFlowBot:
    def __init__(self, config_path="config.grid.yaml", env_file=".env.grid"):
        """
        初始化机器人
        Args:
            config_path: 配置文件路径
            env_file: 环境变量文件路径
        """
        self.config = get_config(config_path, env_file=env_file)
        self.logger = get_logger(log_level="INFO")

        is_testnet = self.config.hyperliquid_testnet
        self.notifier = Notifier(self.config.notifications, is_testnet=is_testnet)

        self.hyperliquid_client = HyperliquidClient(
            private_key=self.config.hyperliquid_private_key,
            account_address=self.config.hyperliquid_account_address,
            testnet=is_testnet,
            api_urls=getattr(self.config, "hyperliquid_api_urls", None),
        )

        self.market_fetcher = MarketDataFetcher(testnet=is_testnet)
        self.order_manager = OrderManager(
            client=self.hyperliquid_client,
            take_profit_ratio=self.config.take_profit_ratio,
            stop_loss_ratio=self.config.stop_loss_ratio,
            default_leverage=self.config.default_leverage,
        )
        self.grid_manager = GridManager(
            self.order_manager,
            self.logger,
            notifier=self.notifier,
            grid_limit_order_take_profit_enabled=self.config.grid_limit_order_take_profit_enabled,
            grid_limit_order_stop_loss_enabled=self.config.grid_limit_order_stop_loss_enabled,
            grid_reduce_only_exit_orders_enabled=self.config.grid_reduce_only_exit_orders_enabled,
        )

        self.logger.print_info("初始化 LLM 客户端管理器...")
        llm_client_config = self.config.get_llm_client_config()
        self.llm_manager = LLMClientManager.get_instance(llm_client_config)
        self.logger.print_info(f"✅ LLM 客户端类型: {self.config.llm_client_type}")
        self.logger.print_info(f"✅ LLM 模型: {self.config.llm_model}")

        self.agent = GridAgent(
            symbol=self.config.symbols[0],
            order_manager=self.order_manager,
            logger=self.logger,
            llm_manager=self.llm_manager,
            trade_amount=self.config.max_trade_amount,
            width_pct_min=self.config.grid_width_min_pct,
            width_pct_max=self.config.grid_width_max_pct,
            width_pct_fallback=self.config.grid_width_fallback_pct,
            ai_width_blend_weight=self.config.grid_ai_blend_weight,
        )

        self.scheduler = BlockingScheduler()

    def run_cycle(self):
        """执行一个网格交易周期"""
        try:
            self.logger.print_header(
                f"🔄 网格交易周期开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            df = self.market_fetcher.fetch_ohlcv(
                symbol=self.config.symbols[0],
                timeframe=self.config.timeframe,
                limit=100,
            )

            if df is None or df.empty:
                self.logger.print_error("无法获取市场数据")
                return

            df = TechnicalIndicators.calculate_all_indicators(df)
            market_data = TechnicalIndicators.get_latest_indicators(df)
            self.logger.print_market_data(self.config.symbols[0], market_data)

            multi_timeframe_trends = TechnicalIndicators.get_multi_timeframe_trend(
                self.market_fetcher,
                self.config.symbols[0],
                cached_ohlcv={self.config.timeframe: df},
            )
            current_grid_summary = self.grid_manager.get_grid_summary(self.config.symbols[0])

            ai_decision = self.agent.make_decision(
                market_data,
                multi_timeframe_trends,
                current_grid_summary,
            )

            self.grid_manager.sync_grid(self.config.symbols[0], ai_decision)
            self.logger.print_header("✅ 网格交易周期完成")

        except Exception as e:
            self.logger.print_error(f"网格周期执行异常: {e}\n{traceback.format_exc()}")

    def run(self):
        """启动机器人"""
        self.scheduler.add_job(
            self.run_cycle,
            trigger=IntervalTrigger(minutes=self.config.interval_minutes),
            id="grid_cycle",
            name="AI网格决策循环",
            replace_existing=True,
        )

        if self.config.run_immediately:
            self.run_cycle()

        self.logger.print_info(f"🚀 AI网格机器人已启动 (间隔: {self.config.interval_minutes} 分钟)")
        self.scheduler.start()


def main():
    parser = argparse.ArgumentParser(description="Grid Flow - AI驱动的动态天地单")
    parser.add_argument(
        "--config",
        type=str,
        default="config.grid.yaml",
        help="配置文件路径 (默认: config.grid.yaml)",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=".env",
        help="环境变量文件路径 (默认: .env)",
    )
    args = parser.parse_args()

    try:
        bot = GridFlowBot(config_path=args.config, env_file=args.env_file)
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 用户手动停止。")
    except Exception as e:
        print(f"\n❌ 启动失败: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
