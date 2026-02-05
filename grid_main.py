#!/usr/bin/env python3
"""
Grid Flow - 动态 AI 天地单 (测试网)
"""

import sys, os, time
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import get_config
from src.utils.logger import get_logger
from src.data.market_data import MarketDataFetcher
from src.data.indicators import TechnicalIndicators
from src.trading.client import HyperliquidClient
from src.trading.order_manager import OrderManager
from src.agent.grid_agent import GridAgent
from src.trading.grid_manager import GridManager
from src.notification.notifier import Notifier

class GridFlowBot:
    def __init__(self):
        self.config = get_config("config.grid.yaml", env_file=".env.grid")
        self.logger = get_logger(log_level="INFO")
        
        # 0. 初始化通知系统
        self.notifier = Notifier(self.config.notifications, is_testnet=True)
        
        # 1. 强制代理与客户端初始化
        testnet_mode = self.config.hyperliquid_testnet
        self.hyperliquid_client = HyperliquidClient(
            private_key=self.config.hyperliquid_private_key,
            account_address=self.config.hyperliquid_account_address,
            testnet=testnet_mode
        )
        
        self.market_fetcher = MarketDataFetcher(testnet=testnet_mode)
        self.order_manager = OrderManager(self.hyperliquid_client)
        self.grid_manager = GridManager(self.order_manager, self.logger, notifier=self.notifier)
        
        self.agent = GridAgent(
            symbol=self.config.symbols[0],
            order_manager=self.order_manager,
            logger=self.logger,
            openai_api_base=self.config.openai_api_base,
            openai_api_key=self.config.openai_api_key,
            openai_model=self.config.openai_model,
            trade_amount=self.config.config_data['trading'].get('max_total_investment', 100.0)
        )
        
        self.notifier.notify_system_startup(version="Grid-v1.0", symbols=self.config.symbols)

    def run_cycle(self):
        symbol = self.config.symbols[0]
        self.logger.print_header(f"🌀 网格巡航周期 - {datetime.now().strftime('%H:%M:%S')}")
        
        try:
            # 数据
            df = self.market_fetcher.fetch_ohlcv(symbol, timeframe="1h", limit=50)
            df = TechnicalIndicators.calculate_all_indicators(df)
            market_data = TechnicalIndicators.get_latest_indicators(df)
            trends = TechnicalIndicators.get_multi_timeframe_trend(self.market_fetcher, symbol)
            
            # 当前网格简报
            summary = self.grid_manager.get_grid_summary(symbol)
            
            # AI 决策
            decision = self.agent.make_decision(market_data, trends, summary)
            
            # 同步执行
            self.grid_manager.sync_grid(symbol, decision)
            
        except Exception as e:
            self.logger.print_error(f"周期执行失败: {e}")

    def start(self):
        self.run_cycle()
        scheduler = BlockingScheduler()
        scheduler.add_job(self.run_cycle, trigger=IntervalTrigger(minutes=10), id="grid_sync")
        scheduler.start()

if __name__ == "__main__":
    bot = GridFlowBot()
    bot.start()
