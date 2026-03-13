#!/usr/bin/env python3
"""
Quant Flow - AI-Powered Cryptocurrency Auto Trading Bot
Multi-Agent Architecture: Maintains independent context for each trading pair, with aggregation agents and spot agents
"""

import argparse
import signal
import sys
import threading
from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.agent.enhanced_single_symbol_agent import EnhancedSingleSymbolAgent, create_enhanced_agent
from src.agent.external_info_agent import ExternalInfoAgent, ExternalInfoScheduler
from src.agent.market_info_store import MarketInfoStore
from src.agent.review_agent import ReviewAgent
from src.agent.review_daily_logger import ReviewDailyLogger
from src.agent.review_memory import ReviewMemoryStore
from src.agent.single_symbol_agent import SingleSymbolAgent

# RiskParameters 用于增强型 Agent 配置，由 create_enhanced_agent 内部处理
from src.agent.spot_agent import SpotAgent
from src.agent.summary_agent_v2 import DecisionHistory, SummaryAgentV2
from src.agents.common.utils.helpers import send_error_notification
from src.config import DEFAULT_PERP_FEE_RATES, get_config
from src.data.data_enricher import MarketDataEnricher
from src.data.indicators import TechnicalIndicators
from src.data.market_data import MarketDataFetcher
from src.llm import LLMClientManager
from src.notification import Notifier
from src.prompt_manager import PromptManager
from src.trading.client import HyperliquidClient
from src.trading.order_manager import OrderManager
from src.utils.banner import print_startup_banner
from src.utils.logger import get_logger


class QuantFlowBot:
    """Quant Flow 交易机器人 - 多 Agent 架构"""

    def __init__(self, config_path: str = "config.yaml", env_file: str = None):
        """
        初始化机器人

        Args:
            config_path: 配置文件路径
            env_file: 环境变量文件路径（默认: .env）
        """
        # 加载配置
        self.config = get_config(config_path, env_file=env_file)

        # 记录程序启动时间（用于数据增强器）
        self.start_time = datetime.now()

        # Prompt 管理器在组件初始化过程中会使用，先占位避免属性不存在
        self.prompt_manager = None

        # 初始化日志
        self.logger = get_logger(
            log_level=self.config.log_level,
            console_color=self.config.console_color,
            decision_log_format=self.config.decision_log_format,
        )

        # 打印启动信息
        print_startup_banner(config=self.config, console=self.logger.console)

        # 交易周期锁（防止并发执行）
        self._trading_lock = threading.Lock()

        # 初始化组件
        self._initialize_components()

        # 调度器
        self.scheduler = None
        self.is_running = False
        self._skipped_cycles = 0  # 跳过的周期计数

        # 交易统计
        self.statistics = {
            "total_trades": 0,
            "profitable_trades": 0,
            "total_pnl": 0.0,
            "start_time": None,
        }
        self.cycle_counter = 0

    def _initialize_components(self):
        """初始化所有组件"""
        self.logger.print_section("🔧 初始化多 Agent 架构", style="bold yellow")

        # 0. 通知系统（优先初始化，以便其他组件可以使用）
        self.logger.print_info("初始化通知系统...")
        notifications_config = getattr(self.config, "notifications", {"enabled": False})
        self.notifier = Notifier(notifications_config, is_testnet=self.config.hyperliquid_testnet)

        # 1. 市场数据获取器
        self.logger.print_info("初始化市场数据获取器...")
        self.market_fetcher = MarketDataFetcher(testnet=self.config.hyperliquid_testnet)

        # 1.5 数据增强器（为nof1和nof1-improved prompts提供额外数据）
        self.logger.print_info("初始化数据增强器...")
        # 从 prompt_manager 获取语言设置，如果没有则默认为中文
        language = self.prompt_manager.language if self.prompt_manager else "zh"
        self.data_enricher = MarketDataEnricher(
            market_fetcher=self.market_fetcher, start_time=self.start_time, language=language
        )

        # 2. Hyperliquid 交易客户端
        self.logger.print_info("初始化 Hyperliquid 交易客户端...")
        self.hyperliquid_client = HyperliquidClient(
            private_key=self.config.hyperliquid_private_key,
            account_address=self.config.hyperliquid_account_address or None,
            testnet=self.config.hyperliquid_testnet,
            api_urls=getattr(self.config, "hyperliquid_api_urls", None),
        )

        # 2.5 动态手续费（基于 userFees）
        self.fee_rates = self._init_fee_rates()

        # 3. 订单管理器
        self.logger.print_info("初始化订单管理器...")
        self.order_manager = OrderManager(
            client=self.hyperliquid_client,
            take_profit_ratio=self.config.take_profit_ratio,
            stop_loss_ratio=self.config.stop_loss_ratio,
            default_leverage=self.config.default_leverage,
        )

        # 3.5 Prompt 管理器（需要费率）
        self.logger.print_info("初始化 Prompt 管理器...")
        try:
            self.prompt_manager = PromptManager(
                config_file=getattr(self.config, "prompt_config_file", "prompts/prompts.yaml"),
                prompt_set=getattr(self.config, "prompt_set", "default"),
                fee_rates_perp=self.fee_rates,
            )
        except Exception as e:
            self.logger.print_warning(f"Prompt 管理器初始化失败，将使用硬编码 Prompt: {e}")
            self.prompt_manager = None

        # 4. LLM 客户端管理器（单例）
        self.logger.print_info("初始化 LLM 客户端管理器...")
        llm_client_config = self.config.get_llm_client_config()
        self.llm_manager = LLMClientManager.get_instance(llm_client_config)
        self.logger.print_info(f"✅ LLM 客户端类型: {self.config.llm_client_type}")
        self.logger.print_info(f"✅ LLM 模型: {self.config.llm_model}")

        # 5. 决策历史管理器
        self.logger.print_info("初始化决策历史管理器...")
        self.decision_history = DecisionHistory(max_history=50)

        # 6. 汇总 Agent (V2 - 使用上下文压缩)
        self.logger.print_info("初始化汇总 Agent V2 (使用上下文压缩)...")
        self.summary_agent = SummaryAgentV2(
            logger=self.logger,
            llm_manager=self.llm_manager,
            temperature=0.1,
            max_context_tokens=2000,  # 限制汇总长度
        )

        # 7. 复盘经验存储与复盘 Agent
        self.logger.print_info("初始化复盘经验存储...")
        self.review_memory_store = ReviewMemoryStore(
            path=self.config.review_memory_file,
            max_lessons=self.config.review_max_lessons,
        )

        if self.config.review_enabled:
            if not self.prompt_manager:
                self.logger.print_warning("Prompt 管理器不可用，复盘 Agent 已禁用")
                self.review_agent = None
            else:
                # 初始化每日日志记录器（用于 LoRA 训练数据收集）
                review_daily_logger = ReviewDailyLogger(
                    base_dir=self.config.review_daily_log_dir,
                    logger=self.logger,
                )
                self.logger.print_info(f"复盘每日日志目录: {self.config.review_daily_log_dir}")

                self.logger.print_info("初始化复盘 Agent...")
                self.review_agent = ReviewAgent(
                    logger=self.logger,
                    prompt_manager=self.prompt_manager,
                    llm_manager=self.llm_manager,
                    temperature=self.config.review_temperature,
                    lookback_decisions=self.config.review_lookback_decisions,
                    memory_store=self.review_memory_store,
                    min_confidence=self.config.review_min_confidence,
                    similarity_threshold=self.config.review_similarity_threshold,
                    similarity_weights=self.config.review_similarity_weights,
                    confidence_decay_factor=self.config.review_confidence_decay_factor,
                    similarity_method=self.config.review_similarity_method,
                    notifier=self.notifier,
                    daily_logger=review_daily_logger,
                )
        else:
            self.review_agent = None

        # 8. 为每个交易对创建独立的单币 Agent
        self.logger.print_info("为每个交易对创建独立 Agent...")
        self.symbol_agents = {}

        # 检查是否启用增强分析
        use_enhanced = getattr(self.config, "enhanced_analysis_enabled", True)

        if use_enhanced:
            self.logger.print_info("✅ 使用增强型交易分析系统")
            # 构建增强配置
            enhanced_config = {
                "agent_temperature": self.config.agent_temperature,
                "agent_max_iterations": self.config.agent_max_iterations,
                "max_trade_amount": self.config.max_trade_amount,
                "max_leverage": self.config.max_leverage,
                "take_profit_ratio": self.config.take_profit_ratio,
                "stop_loss_ratio": self.config.stop_loss_ratio,
                "limit_order_enabled": self.config.limit_order_enabled,
                "enhanced_analysis": {
                    "enabled": True,
                    "min_signal_quality": getattr(
                        self.config, "enhanced_min_signal_quality", "fair"
                    ),
                    "min_confidence": getattr(self.config, "enhanced_min_confidence", 0.4),
                    "enable_risk_filter": getattr(self.config, "enhanced_enable_risk_filter", True),
                    "enable_timing_filter": getattr(
                        self.config, "enhanced_enable_timing_filter", True
                    ),
                    "risk": {
                        "max_risk_per_trade": getattr(
                            self.config, "enhanced_max_risk_per_trade", 0.02
                        ),
                        "max_total_exposure": getattr(
                            self.config, "enhanced_max_total_exposure", 0.5
                        ),
                        "atr_sl_multiplier": getattr(
                            self.config, "enhanced_atr_sl_multiplier", 1.5
                        ),
                        "atr_tp_multiplier": getattr(
                            self.config, "enhanced_atr_tp_multiplier", 3.0
                        ),
                        "trailing_stop_enabled": getattr(
                            self.config, "enhanced_trailing_stop_enabled", True
                        ),
                        "volatility_adjustment": getattr(
                            self.config, "enhanced_volatility_adjustment", True
                        ),
                    },
                },
                "debate": self.config.config_data.get("debate", {}),
                "regime_adaptive": self.config.config_data.get("regime_adaptive", {}),
            }

            for symbol in self.config.symbols:
                self.symbol_agents[symbol] = create_enhanced_agent(
                    symbol=symbol,
                    order_manager=self.order_manager,
                    logger=self.logger,
                    llm_manager=self.llm_manager,
                    config=enhanced_config,
                    notifier=self.notifier,
                    prompt_manager=self.prompt_manager,
                    fee_rates=self.fee_rates,
                )
                self.logger.print_info(f"  ✅ {symbol} 增强型 Agent 创建完成")
        else:
            self.logger.print_info("使用标准交易分析系统")
            for symbol in self.config.symbols:
                self.symbol_agents[symbol] = SingleSymbolAgent(
                    symbol=symbol,
                    order_manager=self.order_manager,
                    logger=self.logger,
                    llm_manager=self.llm_manager,
                    temperature=self.config.agent_temperature,
                    max_iterations=self.config.agent_max_iterations,
                    trade_amount=self.config.max_trade_amount,
                    max_leverage=self.config.max_leverage,
                    take_profit_ratio=self.config.take_profit_ratio,
                    stop_loss_ratio=self.config.stop_loss_ratio,
                    notifier=self.notifier,
                    prompt_manager=self.prompt_manager,
                    fee_rates=self.fee_rates,
                    limit_order_enabled=self.config.limit_order_enabled,
                )
                self.logger.print_info(f"  ✅ {symbol} Agent 创建完成")

        # 9. 现货定投 Agent
        self.logger.print_info("初始化现货定投 Agent...")
        self.spot_agent = SpotAgent(
            order_manager=self.order_manager,
            logger=self.logger,
            llm_manager=self.llm_manager,
            temperature=0.05,  # 更保守
            trade_amount=self.config.max_trade_amount,
            notifier=self.notifier,
            prompt_manager=self.prompt_manager,
        )

        # 10. 外部信息收集 Agent
        self.external_info_agent = None
        self.external_info_scheduler = None
        self.market_info_store = None

        if getattr(self.config, "external_info_enabled", False):
            self.logger.print_info("初始化外部信息收集 Agent...")
            try:
                # 从配置读取 Exa API 密钥（配置已从环境变量加载）
                exa_api_key = self.config.external_info_exa_api_key
                if not exa_api_key:
                    raise ValueError(
                        f"未设置 EXA_API_KEY 环境变量。"
                        f"请在 .env 文件中设置 EXA_API_KEY\n"
                        f"当前读取到的值: {repr(exa_api_key)}"
                    )

                self.external_info_agent = ExternalInfoAgent(
                    logger=self.logger,
                    llm_manager=self.llm_manager,
                    exa_api_key=exa_api_key,
                    temperature=getattr(self.config, "external_info_temperature", 0.1),
                    symbols=self.config.symbols,
                    store_dir=getattr(self.config, "external_info_store_dir", "data/market_info"),
                    prompt_manager=self.prompt_manager,
                    interval_hours=getattr(self.config, "external_info_interval_hours", 3.0),
                )

                # 创建市场信息存储实例（用于读取）
                self.market_info_store = MarketInfoStore(
                    base_dir=getattr(self.config, "external_info_store_dir", "data/market_info")
                )

                # 创建调度器
                self.external_info_scheduler = ExternalInfoScheduler(
                    agent=self.external_info_agent,
                    interval_hours=getattr(self.config, "external_info_interval_hours", 3.0),
                    logger=self.logger,
                )

                self.logger.print_info("✅ 外部信息收集 Agent 初始化完成")
            except Exception as e:
                self.logger.print_warning(f"外部信息收集 Agent 初始化失败: {e}")
                self.external_info_agent = None
                self.external_info_scheduler = None
        else:
            # 即使未启用 Agent，也创建存储实例以便读取已有的报告
            store_dir = getattr(self.config, "external_info_store_dir", "data/market_info")
            self.market_info_store = MarketInfoStore(base_dir=store_dir)

        self.logger.print_info("✅ 多 Agent 架构初始化完成！")
        self.logger.print_info(f"  - {len(self.symbol_agents)} 个单币 Agent")
        self.logger.print_info("  - 1 个汇总 Agent")
        self.logger.print_info("  - 1 个现货定投 Agent")
        if self.review_agent:
            self.logger.print_info("  - 1 个复盘 Agent")
        if self.external_info_agent:
            self.logger.print_info("  - 1 个外部信息收集 Agent")

        # 启动时检查账户余额
        self._check_and_display_balance()

        # 发送启动通知
        self._send_startup_notification()

    def _init_fee_rates(self):
        """
        从 Hyperliquid userFees 拉取最新的用户费率，失败时回退到默认 Tier0。
        """
        try:
            fee_rates = self.hyperliquid_client.fetch_user_fee_rates()
            self.logger.print_info(
                f"当前费率 (自动注入): taker {fee_rates.taker_rate * 100:.3f}% / maker {fee_rates.maker_rate * 100:.3f}%"
            )
            return fee_rates
        except Exception as e:
            self.logger.print_warning(
                f"获取动态费率失败，使用默认值: {DEFAULT_PERP_FEE_RATES}，原因: {e}"
            )
            return DEFAULT_PERP_FEE_RATES

    def _check_and_display_balance(self):
        """检查并显示账户余额信息"""
        try:
            self.logger.print_section("💰 账户余额检查", style="bold green")

            balance_info = self.order_manager.get_available_balance_info()

            if balance_info["status"] == "ok":
                self.logger.print_info(f"总价值: ${balance_info['total']:.2f}")
                self.logger.print_info(f"已占用: ${balance_info['occupied']:.2f}")
                self.logger.print_info(f"可用余额: ${balance_info['available']:.2f}")

                suggestion = self.order_manager.calculate_suggested_trade_amount(
                    desired_amount=self.config.trade_amount,
                    min_trade_amount=10.0,
                    balance_info=balance_info,
                )

                if suggestion["can_trade"]:
                    self.logger.print_info(f"✅ {suggestion['reason']}")
                else:
                    self.logger.print_warning(f"⚠️ {suggestion['reason']}")
            else:
                self.logger.print_error(f"❌ {balance_info['message']}")

        except Exception as e:
            self.logger.print_error(f"余额检查失败: {e}")

    def _send_startup_notification(self):
        """发送系统启动通知"""
        try:
            if self.notifier and self.notifier.enabled:
                self.logger.print_info("📤 发送启动通知...")

                # 获取余额信息
                balance_info = self.order_manager.get_available_balance_info()

                # 准备配置信息
                config_info = {
                    "trade_amount": self.config.trade_amount,
                    "max_positions": self.config.max_positions,
                    "leverage": self.order_manager.default_leverage,
                    "check_interval": self.config.interval_minutes,
                }

                # 如果有余额信息，添加到配置
                if balance_info["status"] == "ok":
                    config_info["available_balance"] = balance_info["available"]

                self.notifier.notify_system_startup(
                    version="v1.0.0",
                    symbols=self.config.symbols,
                    config_info=config_info,
                )
                self.logger.print_info("✅ 启动通知已发送")
        except Exception as e:
            self.logger.print_error(f"发送启动通知失败: {e}")

    def trading_cycle(self):
        """执行一轮交易决策循环（多 Agent 独立决策模式）"""
        # 尝试获取锁，如果正在执行则跳过
        if not self._trading_lock.acquire(blocking=False):
            self._skipped_cycles += 1
            self.logger.print_warning(
                f"⏭️ 上一个交易周期仍在运行，跳过本次调度 (累计跳过: {self._skipped_cycles} 次)"
            )
            return

        try:
            self.cycle_counter += 1
            self.logger.print_header(
                f"🔄 多 Agent 交易周期开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # 第一步：获取当前持仓和余额
            self.logger.print_section("💰 检查账户状态", style="bold green")
            current_positions = self.order_manager.get_current_positions()
            balance_info = self.order_manager.get_available_balance_info()

            if balance_info["status"] != "ok":
                self.logger.print_error(f"❌ {balance_info['message']}")
                self.logger.print_warning("跳过本次交易周期")
                return

            self.logger.print_info(f"可用余额: ${balance_info['available']:.2f}")
            self.logger.print_info(
                f"当前持仓数量: {len(current_positions)}/{self.config.max_positions}"
            )

            # 调整交易金额
            suggestion = self.order_manager.calculate_suggested_trade_amount(
                desired_amount=self.config.trade_amount,
                min_trade_amount=10.0,
                balance_info=balance_info,
            )

            can_open_new_positions = suggestion["can_trade"]

            # 如果余额不足开新仓，但有现有持仓需要管理
            if not can_open_new_positions:
                self.logger.print_warning(f"⚠️ {suggestion['reason']}")

                # 如果没有任何持仓，则跳过整个周期
                if len(current_positions) == 0:
                    self.logger.print_warning("⚠️ 无持仓且余额不足，跳过本次交易周期")
                    return

                # 有持仓时继续执行，但禁止开新仓
                self.logger.print_info("✅ 检测到现有持仓，继续分析以管理持仓（止盈/止损）")
                adjusted_amount = 0  # 设为 0 表示不能开新仓
            else:
                # 余额充足，可以开新仓
                adjusted_amount = suggestion["suggested_amount"]
                if adjusted_amount != self.config.trade_amount:
                    self.logger.print_warning(f"⚠️ {suggestion['reason']}")
                self.logger.print_info(f"本次交易金额: ${adjusted_amount:.2f}")

            # 更新所有 Agent 的交易金额
            for agent in self.symbol_agents.values():
                agent.trade_amount = adjusted_amount
            self.spot_agent.trade_amount = adjusted_amount

            # 第二步：为每个交易对独立决策
            self.logger.print_section("🤖 多 Agent 独立决策", style="bold magenta")

            spot_recommendations = []  # 收集现货定投推荐

            for symbol in self.config.symbols:
                try:
                    self.logger.print_section(f"📊 {symbol} - 独立 Agent 分析", style="bold cyan")

                    # 获取市场数据
                    df = self.market_fetcher.fetch_ohlcv(
                        symbol=symbol,
                        timeframe=self.config.timeframe,
                        limit=self.config.candles_limit,
                    )

                    if df is None or df.empty:
                        self.logger.print_warning(f"无法获取 {symbol} 的市场数据，跳过")
                        continue

                    # 计算技术指标
                    df = TechnicalIndicators.calculate_all_indicators(
                        df,
                        ma_periods=self.config.ma_periods,
                        rsi_period=self.config.rsi_period,
                        macd_params={
                            "fast": self.config.macd_fast,
                            "slow": self.config.macd_slow,
                            "signal": self.config.macd_signal,
                        },
                        bollinger_params={
                            "period": self.config.bollinger_period,
                            "std_dev": self.config.bollinger_std,
                        },
                    )

                    market_data = TechnicalIndicators.get_latest_indicators(df)

                    # 获取多周期趋势
                    multi_timeframe_trends = TechnicalIndicators.get_multi_timeframe_trend(
                        self.market_fetcher, symbol
                    )

                    # 显示市场数据
                    self.logger.print_market_data(symbol, market_data)
                    trend_info = " | ".join(
                        [f"{tf}: {trend}" for tf, trend in multi_timeframe_trends.items()]
                    )
                    self.logger.print_info(f"多周期趋势: {trend_info}")

                    # 获取4小时数据（用于数据增强）
                    df_4h = self.market_fetcher.fetch_ohlcv(
                        symbol=symbol, timeframe="4h", limit=100
                    )

                    if df_4h is not None and not df_4h.empty:
                        # 计算4小时数据的指标（包括EMA和ATR）
                        df_4h = TechnicalIndicators.calculate_all_indicators(
                            df_4h,
                            ema_periods=[20, 50],
                            atr_periods=[3, 14],
                            ma_periods=self.config.ma_periods,
                            rsi_period=self.config.rsi_period,
                            macd_params={
                                "fast": self.config.macd_fast,
                                "slow": self.config.macd_slow,
                                "signal": self.config.macd_signal,
                            },
                            bollinger_params={
                                "period": self.config.bollinger_period,
                                "std_dev": self.config.bollinger_std,
                            },
                        )

                    # 增强市场数据（添加额外字段供nof1/nof1-improved prompts使用）
                    enriched_data = self.data_enricher.enrich_market_data(
                        symbol=symbol, market_data=market_data, df_15m=df, df_4h=df_4h
                    )

                    # 增强账户数据
                    initial_balance = getattr(self.config, "initial_balance", 10000.0)
                    account_enriched = self.data_enricher.enrich_account_data(
                        balance_info=(balance_info if balance_info["status"] == "ok" else None),
                        initial_balance=initial_balance,
                    )
                    enriched_data.update(account_enriched)

                    # 获取最近1小时的操作记录并注入到 enriched_data
                    recent_fills = self._get_recent_fills_for_symbol(symbol, hours=1)
                    if recent_fills and self.prompt_manager:
                        recent_trades_text = self.prompt_manager.format_recent_trades_text(
                            symbol=symbol,
                            recent_trades=recent_fills,
                        )
                        enriched_data["recent_trades_text"] = recent_trades_text
                    else:
                        enriched_data["recent_trades_text"] = ""

                    # 生成历史汇总（如果有足够的历史记录）
                    historical_summary = None
                    history_count = self.decision_history.get_history_count(symbol)

                    if history_count >= 20:
                        # 有足够历史，生成压缩汇总（分离市场走势和决策历史）
                        self.logger.print_info(
                            f"生成 {symbol} 压缩汇总（共 {history_count} 条记录）..."
                        )
                        recent_10 = self.decision_history.get_recent_decisions(symbol, 10)
                        recent_10_20 = self.decision_history.get_decisions_range(symbol, 10, 20)

                        # 使用 V2 压缩方法
                        historical_summary = self.summary_agent.create_compressed_summary(
                            symbol=symbol,
                            recent_records=recent_10,
                            older_records=recent_10_20,
                        )
                    elif history_count >= 10:
                        # 只有 10-19 条记录，生成简单压缩汇总
                        self.logger.print_info(
                            f"生成 {symbol} 简单压缩汇总（共 {history_count} 条记录）..."
                        )
                        recent = self.decision_history.get_recent_decisions(symbol, 10)

                        # 使用 V2 压缩方法
                        historical_summary = self.summary_agent.create_compressed_summary(
                            symbol=symbol, recent_records=recent, older_records=None
                        )
                    else:
                        self.logger.print_info(
                            f"{symbol} 历史记录不足（{history_count} < 10），跳过汇总"
                        )

                    # 注入 Verbal Fine-tuning 段落（高优先级，独立于历史汇总）
                    # 参考 arXiv:2510.08068，将复盘经验以结构化方式注入决策上下文
                    if self.config.review_enabled and self.review_memory_store:
                        vft_section = self.review_memory_store.get_verbal_finetuning_section(
                            symbol, limit=5
                        )
                        if vft_section and enriched_data is not None:
                            enriched_data["verbal_finetuning_section"] = vft_section
                        elif vft_section:
                            # 降级：enriched_data 不可用时追加到历史汇总
                            historical_summary = (
                                f"{historical_summary}\n\n{vft_section}"
                                if historical_summary
                                else vft_section
                            )

                    # 追加外部市场信息，帮助 Agent 基于市场环境做决策（仅在外部信息功能启用时）
                    if (
                        getattr(self.config, "external_info_enabled", False)
                        and self.market_info_store
                    ):
                        max_summary_length = getattr(
                            self.config, "external_info_max_summary_length", 2000
                        )
                        market_info_summary = self.market_info_store.get_combined_summary(
                            symbols=[symbol], max_length=max_summary_length
                        )
                        if market_info_summary:
                            external_info_header = "\n\n## 📰 外部市场信息\n"
                            historical_summary = (
                                f"{historical_summary}{external_info_header}{market_info_summary}"
                                if historical_summary
                                else f"{external_info_header}{market_info_summary}"
                            )

                    # 调用单币 Agent 决策（LLM 决策）
                    agent = self.symbol_agents[symbol]

                    # 如果是增强型Agent，使用增强决策方法
                    if (
                        isinstance(agent, EnhancedSingleSymbolAgent)
                        and agent.enable_enhanced_analysis
                    ):
                        decision, details = agent.make_decision_with_enhanced_analysis(
                            market_data=market_data,
                            multi_timeframe_trends=multi_timeframe_trends,
                            current_positions=current_positions,
                            max_positions=self.config.max_positions,
                            historical_summary=historical_summary,
                            enriched_data=enriched_data,
                            df=df,  # 传入DataFrame用于增强分析
                            account_balance=balance_info.get("available", 0),
                        )

                        # 如果有增强决策信息，记录到日志
                        enhanced_decision = agent.get_last_enhanced_decision()
                        if enhanced_decision:
                            self.logger.print_info(
                                f"  增强分析: 状态={enhanced_decision.market_analysis.state.value}, "
                                f"信号={enhanced_decision.trading_signal.signal_type.value}, "
                                f"置信度={enhanced_decision.overall_confidence:.0%}"
                            )
                    else:
                        # 标准Agent决策
                        decision, details = agent.make_decision(
                            market_data=market_data,
                            multi_timeframe_trends=multi_timeframe_trends,
                            current_positions=current_positions,
                            max_positions=self.config.max_positions,
                            historical_summary=historical_summary,
                            enriched_data=enriched_data,
                        )

                    # 显示决策
                    self.logger.print_info(f"[{symbol}Agent] 决策: {decision}")

                    # 记录决策历史
                    self.decision_history.add_decision(
                        symbol=symbol,
                        decision=decision,
                        market_data=market_data,
                        reason=details.get("output", "")[:200],  # 截取前200字符
                        action_details=details,
                    )

                    # 记录决策日志
                    self.logger.log_decision(
                        symbol=symbol,
                        market_data=market_data,
                        prompt=details.get("prompt", ""),
                        ai_response=details.get("output", ""),
                        decision=decision,
                        action_details=details,
                        status="SUCCESS",
                    )

                    # 如果是现货定投推荐，收集起来
                    if decision == "BUY_SPOT_RECOMMEND":
                        spot_recommendations.append(
                            {
                                "symbol": symbol,
                                "market_data": market_data,
                                "multi_timeframe_trends": multi_timeframe_trends,
                                "reason": details.get("output", ""),
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            }
                        )

                except Exception as e:
                    self.logger.print_error(f"{symbol} Agent 决策异常: {e}")
                    self.logger.logger.exception(e)
                    send_error_notification(
                        notifier=self.notifier,
                        exception=e,
                        title=f"{symbol} Agent 决策异常",
                        context_details={
                            "交易对": symbol,
                            "阶段": "交易周期单币种决策",
                            "说明": "该币种本轮决策失败，其他币种不受影响",
                        },
                    )

            # 第三步：处理现货定投推荐
            if spot_recommendations:
                self.logger.print_section("💎 现货定投 Agent 评估", style="bold blue")

                # 获取当前现货持仓
                current_spot_holdings = self.order_manager.get_spot_holdings()

                for recommendation in spot_recommendations:
                    try:
                        symbol = recommendation["symbol"]
                        self.logger.print_info(f"评估 {symbol} 的现货定投推荐...")

                        # 调用现货 Agent 评估
                        spot_decision, spot_details = self.spot_agent.evaluate_spot_recommendation(
                            symbol=symbol,
                            market_data=recommendation["market_data"],
                            multi_timeframe_trends=recommendation["multi_timeframe_trends"],
                            recommendation=recommendation,
                            current_spot_holdings=current_spot_holdings,
                        )

                        self.logger.print_info(f"[现货Agent] {symbol} 决策: {spot_decision}")

                        # 记录现货决策日志
                        self.logger.log_decision(
                            symbol=f"{symbol}_SPOT",
                            market_data=recommendation["market_data"],
                            prompt=spot_details.get("prompt", ""),
                            ai_response=spot_details.get("output", ""),
                            decision=spot_decision,
                            action_details=spot_details,
                            status="SUCCESS",
                        )

                    except Exception as e:
                        self.logger.print_error(f"现货 Agent 评估 {symbol} 异常: {e}")
                        self.logger.logger.exception(e)

            # 第四步：按需运行复盘 Agent
            self._maybe_run_review_cycle()

            self.logger.print_header(
                f"✅ 多 Agent 交易周期完成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

        except Exception as e:
            self.logger.print_error(f"交易周期异常: {e}")
            self.logger.logger.exception(e)

            # 发送错误通知
            if self.notifier:
                self.notifier.notify_error(
                    title="交易周期异常",
                    error_message=str(e),
                    context="交易决策循环执行时发生错误",
                )
        finally:
            # 无论成功或失败，都释放锁
            self._trading_lock.release()

    def start(self):
        """启动机器人"""
        try:
            self.logger.print_section("🚀 启动多 Agent 交易机器人", style="bold green")

            # 记录启动时间
            self.statistics["start_time"] = datetime.now()

            # 创建调度器
            self.scheduler = BlockingScheduler()

            # 添加定时任务
            self.scheduler.add_job(
                self.trading_cycle,
                trigger=IntervalTrigger(minutes=self.config.interval_minutes),
                id="trading_cycle",
                name="多 Agent 交易决策循环",
                replace_existing=True,
            )

            # 添加外部信息收集定时任务
            if self.external_info_agent:
                interval_hours = getattr(self.config, "external_info_interval_hours", 3.0)
                # 将小时转换为分钟
                interval_minutes = int(interval_hours * 60)

                self.scheduler.add_job(
                    self._run_external_info_collection,
                    trigger=IntervalTrigger(minutes=interval_minutes),
                    id="external_info_collection",
                    name="外部信息收集任务",
                    replace_existing=True,
                )
                self.logger.print_info(f"📡 外部信息收集任务已添加，间隔: {interval_hours} 小时")

                # 首次启动时立即执行一次外部信息收集
                self.logger.print_info("立即执行首次外部信息收集...")
                self._run_external_info_collection()

            # 如果配置了立即执行，先执行一次
            if self.config.run_immediately:
                self.logger.print_info("立即执行第一次交易循环...")
                self.trading_cycle()

            # 显示下次执行时间
            next_run = datetime.now().replace(second=0, microsecond=0)
            next_run = next_run + timedelta(minutes=self.config.interval_minutes)
            self.logger.print_info(f"下次执行时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.print_info(f"执行间隔: {self.config.interval_minutes} 分钟")

            # 启动调度器
            self.is_running = True
            self.start_time = datetime.now()  # 记录启动时间
            self.logger.print_section(
                "✅ 多 Agent 机器人已启动，按 Ctrl+C 停止", style="bold green"
            )
            self.scheduler.start()

        except KeyboardInterrupt:
            self.stop("用户手动停止 (Ctrl+C)")
        except Exception as e:
            self.logger.print_error(f"启动失败: {e}")
            self.logger.logger.exception(e)
            sys.exit(1)

    def stop(self, reason: str = "用户手动停止"):
        """停止机器人"""
        self.logger.print_section("🛑 停止多 Agent 交易机器人", style="bold red")
        self.is_running = False

        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()

        # 发送关闭通知
        self._send_shutdown_notification(reason)

        self.logger.print_info("机器人已停止")

    def _run_external_info_collection(self):
        """执行外部信息收集任务"""
        if not self.external_info_agent:
            return

        try:
            self.logger.print_section("📡 开始外部信息收集任务", style="bold blue")

            # 执行收集（使用配置的间隔时间）
            saved_file = self.external_info_agent.collect_and_save()

            if saved_file:
                self.logger.print_info("✅ 外部信息收集完成")
                self.logger.print_info(f"  报告文件: {saved_file}")

                # 发送通知（包含报告内容）
                if self.notifier and self.notifier.enabled:
                    # 获取报告摘要
                    summary = self.external_info_agent.get_latest_summary(
                        symbols=self.config.symbols, max_length=2000
                    )

                    if summary:
                        self.notifier.notify_external_info_summary(
                            summary=summary, file_path=saved_file
                        )
            else:
                self.logger.print_warning("⚠️ 外部信息收集未生成任何报告")
            # 清理过期报告
            cleanup_days = getattr(self.config, "external_info_cleanup_days", 30)
            if self.market_info_store:
                self.market_info_store.cleanup_old_reports(days_to_keep=cleanup_days)

        except Exception as e:
            self.logger.print_error(f"外部信息收集任务异常: {e}")
            self.logger.logger.exception(e)

    def _maybe_run_review_cycle(self):
        """根据配置触发复盘 Agent"""
        if not getattr(self.config, "review_enabled", False):
            return
        if not getattr(self, "review_agent", None) or not self.review_agent:
            return
        if self.cycle_counter % max(1, self.config.review_run_every_cycles) != 0:
            return

        self.logger.print_section("🧠 运行复盘 Agent", style="bold white")
        stats_snapshot = self._gather_statistics()
        fills_summary = {
            "total_fills": stats_snapshot.get("total_trades", 0),
            "total_pnl": stats_snapshot.get("total_pnl", 0.0),
        }

        for symbol in self.config.symbols:
            recent_decisions = self.decision_history.get_recent_decisions(
                symbol, self.config.review_lookback_decisions
            )
            # Require at least a minimum number of decisions for review.
            # By default, this is max(3, half the lookback window), but can be overridden in config as review_min_required_decisions.
            min_required_decisions = getattr(
                self.config,
                "review_min_required_decisions",
                max(3, self.config.review_lookback_decisions // 2),
            )
            if len(recent_decisions) < min_required_decisions:
                self.logger.print_info(f"{symbol} 复盘数据不足，跳过")
                continue

            existing_lessons = []
            if hasattr(self, "review_memory_store") and self.review_memory_store:
                existing_lessons = self.review_memory_store.get_lessons(symbol)

            review_result = self.review_agent.review(
                symbol=symbol,
                decision_records=recent_decisions,
                fills_summary=fills_summary,
                existing_lessons=existing_lessons,
            )

            lessons = review_result.get("lessons", [])
            if not lessons:
                self.logger.print_info(f"{symbol} 复盘未生成新经验")
                continue

            added = []
            if self.review_memory_store:
                added = self.review_memory_store.add_lessons(
                    symbol=symbol,
                    lessons=lessons,
                    min_confidence=self.config.review_min_confidence,
                )

            if not added:
                self.logger.print_info(f"{symbol} 复盘经验未满足置信度要求")
                continue

            self.logger.print_info(f"{symbol} 复盘采纳 {len(added)} 条经验")
            self.logger.log_decision(
                symbol=f"{symbol}_REVIEW",
                market_data={"timestamp": datetime.now().isoformat()},
                prompt=review_result.get("prompt", ""),
                ai_response=review_result.get("raw_output", ""),
                decision="REVIEW",
                action_details={
                    "lessons": added,
                    "summary": review_result.get("summary", ""),
                    "spot_checks": review_result.get("spot_checks", []),
                },
                status="SUCCESS",
            )

    def _get_recent_fills_for_symbol(self, symbol: str, hours: int = 1) -> list[dict[str, Any]]:
        """
        获取指定币种最近N小时的交易记录

        Args:
            symbol: 币种名称（如 BTC, ETH）
            hours: 回溯时间（小时），默认1小时

        Returns:
            该币种最近N小时的交易记录列表，按时间排序（从旧到新）
        """
        try:
            user_address = self.hyperliquid_client.address
            fills = self.hyperliquid_client.info.user_fills(user_address)

            if not fills:
                return []

            # 计算N小时前的时间戳（毫秒）
            cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)

            # 过滤出该币种最近N小时的交易
            recent_fills = [
                f for f in fills if f.get("coin") == symbol and f.get("time", 0) >= cutoff_time
            ]

            # 按时间排序（从旧到新）
            recent_fills.sort(key=lambda x: x.get("time", 0))

            return recent_fills

        except Exception as e:
            self.logger.print_warning(f"获取 {symbol} 最近交易记录失败: {e}")
            return []

    def _gather_statistics(self) -> dict[str, Any]:
        """收集交易统计信息"""
        try:
            # 获取用户的交易填充历史
            user_address = self.hyperliquid_client.address
            fills = self.hyperliquid_client.info.user_fills(user_address)

            if not fills:
                return self.statistics

            # 过滤出本次运行期间的交易
            if self.statistics["start_time"]:
                start_timestamp = int(self.statistics["start_time"].timestamp() * 1000)
                recent_fills = [f for f in fills if f.get("time", 0) >= start_timestamp]
            else:
                recent_fills = fills

            # 统计交易信息
            total_trades = len(recent_fills)
            total_pnl = sum(float(f.get("closedPnl", 0)) for f in recent_fills)
            profitable_trades = sum(1 for f in recent_fills if float(f.get("closedPnl", 0)) > 0)

            self.statistics["total_trades"] = total_trades
            self.statistics["total_pnl"] = total_pnl
            self.statistics["profitable_trades"] = profitable_trades

            return self.statistics

        except Exception as e:
            self.logger.print_warning(f"收集统计信息失败: {e}")
            return self.statistics

    def _send_shutdown_notification(self, reason: str = "正常关闭"):
        """发送系统关闭通知"""
        try:
            if self.notifier and self.notifier.enabled:
                self.logger.print_info("📤 发送关闭通知...")

                # 计算运行时长
                if hasattr(self, "start_time"):
                    from datetime import datetime

                    runtime_seconds = (datetime.now() - self.start_time).total_seconds()
                    hours = int(runtime_seconds // 3600)
                    minutes = int((runtime_seconds % 3600) // 60)
                    runtime = f"{hours}小时{minutes}分钟"
                else:
                    runtime = None

                # 收集交易统计信息
                statistics = self._gather_statistics()

                self.notifier.notify_system_shutdown(
                    reason=reason,
                    runtime=runtime,
                    statistics=statistics if statistics["total_trades"] > 0 else None,
                )
                self.logger.print_info("✅ 关闭通知已发送")
        except Exception as e:
            self.logger.print_error(f"发送关闭通知失败: {e}")


def signal_handler(signum, frame):
    """信号处理器"""
    print("\n\n收到停止信号，正在关闭...")
    sys.exit(0)


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description="Quant Flow - AI驱动的加密货币自动交易机器人",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认配置
  python main.py

  # 指定环境变量文件
  python main.py --env-file .env.testnet

  # 指定配置文件和环境变量文件
  python main.py --config config.yaml --env-file .env.testnet
        """,
    )
    parser.add_argument(
        "--config", type=str, default="config.yaml", help="配置文件路径（默认: config.yaml）"
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="环境变量文件路径（默认: .env，可通过环境变量 DOTENV_PATH 覆盖）",
    )
    args = parser.parse_args()

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # 创建并启动机器人
        bot = QuantFlowBot(config_path=args.config, env_file=args.env_file)
        bot.start()

    except FileNotFoundError as e:
        print(f"\n❌ 错误: {e}")
        print("\n请确保:")
        print("1. 已将 config.yaml.example 复制为 config.yaml")
        print("2. 已将 .env.example 复制为 .env")
        print("3. 已在 .env 中配置 Hyperliquid 私钥\n")
        sys.exit(1)

    except ValueError as e:
        print(f"\n❌ 配置错误: {e}\n")
        sys.exit(1)

    except Exception as e:
        print(f"\n❌ 启动失败: {e}\n")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
