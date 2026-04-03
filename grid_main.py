#!/usr/bin/env python3
"""
Grid Flow - 动态 AI 天地单
"""

import argparse
import sys
import traceback
from datetime import datetime
from decimal import Decimal

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.agent.grid_agent import GridAgent
from src.config import get_config
from src.data.indicators import TechnicalIndicators
from src.data.market_data import MarketDataFetcher
from src.llm import LLMClientManager
from src.notification.notifier import Notifier
from src.trading.client import HyperliquidClient
from src.trading.grid_barrier import TripleBarrierConfig
from src.trading.grid_manager import GridManager
from src.trading.order_manager import OrderManager
from src.utils.cloud_logger import get_cloud_logger, init_cloud_logger
from src.utils.logger import get_logger
from src.utils.precision import to_decimal


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

        # 初始化云端日志（aepipe-sdk 0.1.1，支持 D1 payload 完整日志）
        if self.config.cloud_logging_enabled:
            cloud = init_cloud_logger(
                base_url=self.config.cloud_logging_base_url,
                token=self.config.cloud_logging_token,
                project=self.config.cloud_logging_project,
                logstore=self.config.cloud_logging_logstore,
                flush_interval=self.config.cloud_logging_flush_interval,
                payload_ttl=self.config.cloud_logging_payload_ttl,
            )
            cloud.send_system_event(
                "startup",
                details={
                    "config_path": config_path,
                    "symbols": self.config.symbols,
                    "run_mode": "grid",
                },
            )

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
        # 从配置中构建 Triple Barrier 风控参数（from_config 内部做 Decimal 类型转换）
        risk_cfg = self.config.config_data.get("risk_management", {})
        barrier_config = TripleBarrierConfig.from_config(risk_cfg)

        self.grid_manager = GridManager(
            self.order_manager,
            self.logger,
            notifier=self.notifier,
            state_file="data/grid_state.json",
            grid_limit_order_take_profit_enabled=self.config.grid_limit_order_take_profit_enabled,
            grid_limit_order_stop_loss_enabled=self.config.grid_limit_order_stop_loss_enabled,
            grid_reduce_only_exit_orders_enabled=self.config.grid_reduce_only_exit_orders_enabled,
            barrier_config=barrier_config,
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
        symbol = self.config.symbols[0] if self.config.symbols else "UNKNOWN"
        cloud = get_cloud_logger()

        try:
            self.logger.print_header(
                f"🔄 网格交易周期开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            if cloud:
                cloud.send_cycle_event(symbol=symbol, phase="start")

            df = self.market_fetcher.fetch_ohlcv(
                symbol=symbol,
                timeframe=self.config.timeframe,
                limit=100,
            )

            if df is None or df.empty:
                self.logger.print_error("无法获取市场数据")
                if cloud:
                    cloud.send_cycle_event(
                        symbol=symbol,
                        phase="skip",
                        details={"reason": "无法获取市场数据"},
                        level="warn",
                    )
                return

            df = TechnicalIndicators.calculate_all_indicators(df)
            market_data = TechnicalIndicators.get_latest_indicators(df)
            self.logger.print_market_data(symbol, market_data)

            multi_timeframe_trends = TechnicalIndicators.get_multi_timeframe_trend(
                self.market_fetcher,
                symbol,
                cached_ohlcv={self.config.timeframe: df},
            )
            current_grid_summary = self.grid_manager.get_grid_summary(symbol)

            ai_decision = self.agent.make_decision(
                market_data,
                multi_timeframe_trends,
                current_grid_summary,
            )

            # 记录网格决策到本地日志 + 云端
            action = ai_decision.get("action", "UNKNOWN")
            reason = ai_decision.get("reason", "")
            confidence = float(ai_decision.get("confidence", 0.0))
            self.logger.log_decision(
                symbol=symbol,
                market_data=market_data,
                prompt="[GridAgent]",
                ai_response=reason,
                decision=action,
                action_details=ai_decision,
                status="SUCCESS" if action != "ERROR" else "ERROR",
                confidence=confidence,
            )

            self.grid_manager.sync_grid(symbol, ai_decision)
            self.logger.print_header("✅ 网格交易周期完成")

            # 记录周期结束事件，附带网格 PnL 摘要
            if cloud:
                grid_summary = self.grid_manager.get_grid_summary(symbol)
                pnl_data = {}
                tracker = self.grid_manager.pnl_trackers.get(symbol)
                levels = self.grid_manager.grid_levels.get(symbol)
                if tracker and levels:
                    try:
                        cp = self.grid_manager.order_manager.client.get_current_price(symbol)
                        if cp and cp > 0:
                            total_inv = sum((lv.amount for lv in levels), Decimal("0"))
                            pnl_data = tracker.get_summary(levels, to_decimal(cp), total_inv)
                            # Decimal 转 float 以便 JSON 序列化
                            pnl_data = {
                                k: float(v) if isinstance(v, Decimal) else v
                                for k, v in pnl_data.items()
                            }
                    except Exception as e:
                        self.logger.print_warning(f"PnL 摘要计算失败: {e}")
                cloud.send_cycle_event(
                    symbol=symbol,
                    phase="end",
                    details={
                        "action": action,
                        "confidence": confidence,
                        "grid_summary": grid_summary,
                        **pnl_data,
                    },
                )

        except Exception as e:
            self.logger.print_error(f"网格周期执行异常: {e}\n{traceback.format_exc()}")
            # 记录网格周期异常到云端
            if cloud:
                cloud.send_alert(
                    symbol=symbol,
                    alert_type="grid_cycle_error",
                    severity="high",
                    message=str(e),
                    details={"traceback": traceback.format_exc()},
                )

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
