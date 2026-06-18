#!/usr/bin/env python3
"""
Quant Flow - AI-Powered Cryptocurrency Auto Trading Bot
Multi-Agent Architecture: Maintains independent context for each trading pair, with aggregation agents
"""

import argparse
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.agent.enhanced_single_symbol_agent import EnhancedSingleSymbolAgent, create_enhanced_agent
from src.agent.external_info_agent import ExternalInfoAgent
from src.agent.grid_agent import GridAgent
from src.agent.helpers import send_error_notification
from src.agent.market_info_store import MarketInfoStore
from src.agent.review_agent import ReviewAgent
from src.agent.review_daily_logger import ReviewDailyLogger
from src.agent.review_memory import ReviewMemoryStore
from src.agent.single_symbol_agent import SingleSymbolAgent

# 改进1: 双粒度反思（延迟导入在初始化时使用）
# RiskParameters 用于增强型 Agent 配置，由 create_enhanced_agent 内部处理
from src.agent.summary_agent_v2 import DecisionHistory, SummaryAgentV2
from src.config import DEFAULT_PERP_FEE_RATES, get_config
from src.data.data_enricher import MarketDataEnricher
from src.data.indicators import TechnicalIndicators
from src.data.market_data import MarketDataFetcher
from src.data.market_monitor import MarketMonitor, MonitorConfig, VolatilityAlert
from src.llm import LLMClientManager
from src.notification import Notifier
from src.plugins.protections import ProtectionAction, ProtectionContext, ProtectionManager
from src.prompt_manager import PromptManager
from src.trading.client import HyperliquidClient
from src.trading.grid_barrier import TripleBarrierConfig
from src.trading.grid_manager import GridManager
from src.trading.order_manager import OrderManager
from src.utils.banner import print_startup_banner
from src.utils.candle_align import next_candle_close_ts
from src.utils.cloud_logger import get_cloud_logger, init_cloud_logger
from src.utils.logger import get_logger
from src.utils.precision import to_decimal


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
            cloud.send_system_event("startup", details={
                "config_path": config_path,
                "symbols": self.config.symbols,
                "run_mode": "main",
            })

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
        # 立即执行的网格周期线程引用（停机时 join，避免腰斩首次布单序列）
        self._grid_immediate_thread = None

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

        # 7.5 改进1: 双粒度反思组件初始化
        self.instant_reflector = None
        self.weekly_reflector = None
        self.prompt_meta_reflector = None
        self._weekly_reflection_last_run = None

        if getattr(self.config, "review_instant_reflection_enabled", False):
            try:
                from src.agent.context_extractor import ContextExtractor
                from src.agent.instant_reflection import InstantReflector
                from src.agent.similarity_scorer import SimilarityScorer

                self.instant_reflector = InstantReflector(
                    memory_store=self.review_memory_store,
                    similarity_scorer=SimilarityScorer(
                        weights=self.config.review_similarity_weights,
                        method=self.config.review_similarity_method,
                    ),
                    context_extractor=ContextExtractor(),
                    logger_instance=self.logger,
                )
                self.logger.print_info("✅ 即时反思器初始化完成")
            except Exception as e:
                self.logger.print_warning(f"即时反思器初始化失败: {e}")

        if getattr(self.config, "review_weekly_reflection_enabled", False) and self.prompt_manager:
            try:
                from src.agent.weekly_reflection import WeeklyReflector

                self.weekly_reflector = WeeklyReflector(
                    llm_manager=self.llm_manager,
                    prompt_manager=self.prompt_manager,
                    memory_store=self.review_memory_store,
                    logger_instance=self.logger,
                    notifier=self.notifier,
                    weekly_day=self.config.review_weekly_reflection_day,
                    weekly_hour=self.config.review_weekly_reflection_hour,
                )
                self.logger.print_info("✅ 每周反思器初始化完成")
            except Exception as e:
                self.logger.print_warning(f"每周反思器初始化失败: {e}")

        if getattr(self.config, "review_prompt_meta_reflection_enabled", False) and self.prompt_manager:
            try:
                from src.agent.prompt_meta_reflection import PromptMetaReflector

                self.prompt_meta_reflector = PromptMetaReflector(
                    llm_manager=self.llm_manager,
                    prompt_manager=self.prompt_manager,
                    memory_store=self.review_memory_store,
                    logger_instance=self.logger,
                    output_dir=self.config.review_prompt_optimization_dir,
                )
                self.logger.print_info("✅ Prompt 元反思器初始化完成")
            except Exception as e:
                self.logger.print_warning(f"Prompt 元反思器初始化失败: {e}")

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

        # 9. 外部信息收集 Agent
        self.external_info_agent = None
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

                self.logger.print_info("✅ 外部信息收集 Agent 初始化完成")
            except Exception as e:
                self.logger.print_warning(f"外部信息收集 Agent 初始化失败: {e}")
                self.external_info_agent = None
        else:
            # 即使未启用 Agent，也创建存储实例以便读取已有的报告
            store_dir = getattr(self.config, "external_info_store_dir", "data/market_info")
            self.market_info_store = MarketInfoStore(base_dir=store_dir)

        # 11. 市场主动监控器
        self.market_monitor = None
        self._pending_alerts: dict[str, VolatilityAlert] = {}  # 待处理的波动告警
        self._alert_lock = threading.Lock()

        if self.config.market_monitor_enabled:
            self.logger.print_info("初始化市场主动监控器...")
            monitor_config = MonitorConfig(
                enabled=True,
                check_interval_seconds=self.config.market_monitor_check_interval_seconds,
                alert_threshold_pct=self.config.market_monitor_alert_threshold_pct,
                elevated_threshold_pct=self.config.market_monitor_elevated_threshold_pct,
                extreme_threshold_pct=self.config.market_monitor_extreme_threshold_pct,
                cooldown_minutes=self.config.market_monitor_cooldown_minutes,
                reference_window_minutes=self.config.market_monitor_reference_window_minutes,
            )
            self.market_monitor = MarketMonitor(
                symbols=self.config.symbols,
                testnet=self.config.hyperliquid_testnet,
                config=monitor_config,
                on_alert_callback=self._on_market_alert,
                logger=self.logger,
            )
            self.logger.print_info(
                f"✅ 市场监控器初始化完成 | 波动阈值: {monitor_config.alert_threshold_pct}% | "
                f"检查间隔: {monitor_config.check_interval_seconds}s"
            )

        # 12. 保护插件管理器
        self.protection_manager = None
        if self.config.protections_config:
            self.logger.print_info("初始化保护插件管理器...")
            self.protection_manager = ProtectionManager(
                protections_config=self.config.protections_config,
                on_protection_triggered=self._on_protection_triggered,
            )
            plugin_names = [p.name for p in self.protection_manager.plugins]
            if plugin_names:
                self.logger.print_info(
                    f"保护插件管理器初始化完成 | 已加载: {', '.join(plugin_names)}"
                )
            else:
                self.logger.print_warning(
                    "⚠️ protections 配置非空但未加载任何有效保护插件（插件名未知或全部 enabled=false），"
                    "账户风控已全部关闭"
                )
        else:
            self.logger.print_warning(
                "⚠️ 未配置任何风控保护插件（protections 为空），账户风控已全部关闭。"
                "如需启用，请在 config.yaml 的 protections 段添加插件（参考 config.yaml.example）"
            )

        self.logger.print_info("✅ 多 Agent 架构初始化完成！")
        self.logger.print_info(f"  - {len(self.symbol_agents)} 个单币 Agent")
        self.logger.print_info("  - 1 个汇总 Agent")
        if self.review_agent:
            self.logger.print_info("  - 1 个复盘 Agent")
        if self.external_info_agent:
            self.logger.print_info("  - 1 个外部信息收集 Agent")
        if self.market_monitor:
            self.logger.print_info("  - 1 个市场主动监控器")
        if self.protection_manager:
            self.logger.print_info("  - 1 个保护插件管理器 (ProtectionManager)")

        # 13. 初始化网格交易组件
        self.grid_manager = None
        self.grid_agent = None
        if self.config.grid_enabled:
            self.logger.print_info("初始化网格交易组件...")
            try:
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

                self.grid_agent = GridAgent(
                    symbol=self.config.symbols[0],
                    order_manager=self.order_manager,
                    logger=self.logger,
                    llm_manager=self.llm_manager,
                    trade_amount=self.config.max_trade_amount,
                    width_pct_min=self.config.grid_width_min_pct,
                    width_pct_max=self.config.grid_width_max_pct,
                    width_pct_fallback=self.config.grid_width_fallback_pct,
                    ai_width_blend_weight=self.config.grid_ai_blend_weight,
                    force_neutral_mode=self.config.grid_force_neutral_mode,
                )
                self.logger.print_info("✅ 网格交易组件初始化完成")
            except Exception as e:
                self.logger.print_warning(f"网格交易组件初始化失败: {e}")
                self.grid_manager = None
                self.grid_agent = None

        if self.grid_manager:
            self.logger.print_info("  - 1 个网格交易管理器 (GridManager)")

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

    def _on_protection_triggered(self, reason: str):
        """保护插件触发时的回调：发送通知 + 云端告警"""
        self.logger.print_warning(f"[风控] {reason}")

        if self.notifier and self.notifier.enabled:
            self.notifier.notify_error(
                title="风控保护触发",
                error_message=reason,
                context="保护插件检测到风险条件，已自动采取保护措施",
            )

        cloud = get_cloud_logger()
        if cloud:
            cloud.send_alert(
                symbol="ALL",
                alert_type="account_protection",
                severity="extreme",
                message=reason,
                details={},
            )

    def _on_market_alert(self, alert: VolatilityAlert):
        """
        市场监控告警回调（在监控线程中执行）。
        将告警存入待处理队列，然后触发一次异步决策循环。
        """
        self.logger.print_warning(
            f"🚨 [市场监控] 检测到异常波动: {alert.message} [{alert.level.value}]"
        )

        # 记录异常波动告警到云端
        cloud = get_cloud_logger()
        if cloud:
            cloud.send_alert(
                symbol=alert.symbol,
                alert_type="volatility",
                severity=alert.level.value,
                message=alert.message,
                details={
                    "change_pct": alert.change_pct,
                    "current_price": alert.current_price,
                    "reference_price": alert.reference_price,
                },
            )

        # 存储告警信息（供决策周期读取）
        with self._alert_lock:
            self._pending_alerts[alert.symbol] = alert

        # 在独立线程中触发决策循环（避免阻塞监控线程）
        trigger_thread = threading.Thread(
            target=self._alert_triggered_cycle,
            args=(alert,),
            name=f"alert-cycle-{alert.symbol}",
            daemon=True,
        )
        trigger_thread.start()

    def _alert_triggered_cycle(self, alert: VolatilityAlert):
        """由异常波动告警触发的决策循环"""
        self.logger.print_header(
            f"⚡ 异常波动触发决策: {alert.symbol} {alert.change_pct:+.2f}% "
            f"[{alert.level.value}] - {datetime.now().strftime('%H:%M:%S')}"
        )
        # 复用常规的 trading_cycle，告警信息通过 _pending_alerts 传递
        self.trading_cycle(triggered_by_alert=True)

    def _consume_pending_alert(self, symbol: str) -> VolatilityAlert | None:
        """消费并清除某个交易对的待处理告警"""
        with self._alert_lock:
            return self._pending_alerts.pop(symbol, None)

    def trading_cycle(self, triggered_by_alert: bool = False):
        """执行一轮交易决策循环（多 Agent 独立决策模式）"""
        # 尝试获取锁，如果正在执行则跳过
        if not self._trading_lock.acquire(blocking=False):
            self._skipped_cycles += 1
            trigger_info = "（由异常波动触发）" if triggered_by_alert else ""
            self.logger.print_warning(
                f"⏭️ 上一个交易周期仍在运行，跳过本次调度{trigger_info} "
                f"(累计跳过: {self._skipped_cycles} 次)"
            )
            return

        try:
            self.cycle_counter += 1
            trigger_label = "⚡ 异常波动触发" if triggered_by_alert else "🔄 定时"
            self.logger.print_header(
                f"{trigger_label} 多 Agent 交易周期开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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

            # 记录账户快照到云端
            cloud = get_cloud_logger()
            if cloud:
                cloud.send_account_snapshot(
                    balance=balance_info.get("total", 0),
                    equity=balance_info.get("equity", balance_info.get("total", 0)),
                    unrealized_pnl=balance_info.get("unrealized_pnl", 0),
                    positions=[
                        {"symbol": p.get("symbol", ""), "size": p.get("size", 0),
                         "entry_price": p.get("entry_price", 0),
                         "unrealized_pnl": p.get("unrealized_pnl", 0)}
                        for p in current_positions
                    ] if current_positions else [],
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
            # ── 风控检查（保护插件链）──
            if self.protection_manager:
                context = ProtectionContext(
                    balance=balance_info.get("available", 0),
                    equity=balance_info.get("total", 0),
                    unrealized_pnl=balance_info.get("unrealized_pnl", 0),
                    margin_used=balance_info.get("occupied", 0),
                    current_positions=current_positions or [],
                )
                results = self.protection_manager.check_all(context)
                action = ProtectionManager.get_most_severe_action(results)

                # 各插件内部已上报精确的 risk_event（含专有字段），此处仅打日志
                # _on_protection_triggered 回调会发送一次 send_alert 总览
                for r in results:
                    self.logger.print_warning(f"[风控]{r.reason}")

                # 回撤触发全部平仓
                if action == ProtectionAction.CLOSE_ALL_POSITIONS:
                    self.logger.print_warning("[风控]回撤保护触发，执行全部平仓")
                    for pos in (current_positions or []):
                        sym = pos.get("coin", "")
                        if sym:
                            try:
                                self.order_manager.close_position(sym)
                                # 强平属于风控主动行为：清理持仓状态记录（如超时记录），
                                # 不向连续亏损插件上报虚假 pnl
                                self.protection_manager.on_position_dropped(sym)
                                self.logger.print_info(f"[风控]已平仓: {sym}")
                            except Exception as e:
                                self.logger.print_error(f"[风控]平仓失败 {sym}: {e}")
                    return

                # 暂停新开仓
                if action == ProtectionAction.PAUSE_NEW_TRADES:
                    can_open_new_positions = False
                    for agent in self.symbol_agents.values():
                        agent.trade_amount = 0
                    self.logger.print_warning("[风控]保护插件已暂停新开仓，仅管理现有持仓")

                # 超时持仓自动平仓（直接从 check_all 结果中取，避免重复扫描）
                # 注意：超时强平属于风控主动行为，不向连续亏损插件上报 pnl，
                # 仅通知 position_timeout 插件清理记录
                timeout_symbols: list[str] = []
                for r in results:
                    if r.plugin_name == "position_timeout" and r.affected_symbols:
                        timeout_symbols.extend(r.affected_symbols)

                for ts in timeout_symbols:
                    self.logger.print_warning(f"[风控]持仓超时: {ts}，执行平仓")
                    try:
                        self.order_manager.close_position(ts)
                        # 超时强平属于风控主动行为：仅清理 position_timeout 记录，
                        # 不向连续亏损插件上报虚假 pnl
                        self.protection_manager.on_position_dropped(ts)
                    except Exception as e:
                        self.logger.print_error(f"[风控]超时平仓失败 {ts}: {e}")

            # 第二步：为每个交易对独立决策
            self.logger.print_section("🤖 多 Agent 独立决策", style="bold magenta")

            for symbol in self.config.symbols:
                try:
                    self.logger.print_section(f"📊 {symbol} - 独立 Agent 分析", style="bold cyan")

                    # 检查交易对级锁定
                    if self.protection_manager:
                        locked, lock_reason = self.protection_manager.is_symbol_locked(symbol)
                        if locked:
                            self.logger.print_warning(f"[风控]{symbol} 已锁定: {lock_reason}")
                            if symbol in self.symbol_agents:
                                self.symbol_agents[symbol].trade_amount = 0

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

                    # 注入异常波动告警上下文（如果有）
                    pending_alert = self._consume_pending_alert(symbol)
                    if pending_alert and self.market_monitor:
                        alert_context = self.market_monitor.format_alert_context(pending_alert)
                        enriched_data["volatility_alert"] = alert_context
                        self.logger.print_warning(
                            f"⚡ {symbol} 注入异常波动上下文: {pending_alert.message}"
                        )

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
                        # 改进2: 获取当前 Regime 用于 VFT 注入
                        current_regime = None
                        if getattr(self.config, "review_regime_aware_enabled", False):
                            current_regime = self._get_current_regime(enriched_data)

                        vft_section = self.review_memory_store.get_verbal_finetuning_section(
                            symbol,
                            limit=5,
                            current_regime=current_regime,
                            trending_subjective_boost=getattr(
                                self.config, "review_trending_subjective_boost", 1.3
                            ),
                            ranging_factual_boost=getattr(
                                self.config, "review_ranging_factual_boost", 1.3
                            ),
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

                    # 判定真实决策状态：Agent 内部异常时返回 decision=="ERROR" 或 details 含 error 字段
                    decision_failed = decision == "ERROR" or bool(details.get("error"))
                    decision_status = "ERROR" if decision_failed else "SUCCESS"
                    decision_error = details.get("error") if decision_failed else None

                    # 记录决策历史（ERROR 决策不写入，避免污染历史压缩与复盘记忆）
                    if not decision_failed:
                        self.decision_history.add_decision(
                            symbol=symbol,
                            decision=decision,
                            market_data=market_data,
                            reason=details.get("output", "")[:200],  # 截取前200字符
                            action_details=details,
                        )

                    # 记录决策日志（按真实状态记录，不再硬编码 SUCCESS）
                    self.logger.log_decision(
                        symbol=symbol,
                        market_data=market_data,
                        prompt=details.get("prompt", ""),
                        ai_response=details.get("output", ""),
                        decision=decision,
                        action_details=details,
                        status=decision_status,
                        error_message=decision_error,
                    )

                    # 改进1a: 即时反思（平仓类型时触发）
                    if (
                        self.instant_reflector
                        and decision in ("SELL", "BUY_TO_COVER")
                    ):
                        try:
                            decision_record = {
                                "decision": decision,
                                "timestamp": datetime.now().isoformat(),
                                "market_data": market_data,
                                "action_details": details,
                                "reason": details.get("output", ""),
                            }
                            trade_result = {
                                "pnl": details.get("pnl", 0) or details.get("closed_pnl", 0),
                                "status": details.get("status", ""),
                                "timestamp": datetime.now().isoformat(),
                            }
                            self.instant_reflector.reflect_on_close(
                                symbol=symbol,
                                decision_record=decision_record,
                                trade_result=trade_result,
                                market_data=market_data,
                            )
                        except Exception as e:
                            self.logger.print_warning(f"[{symbol}] 即时反思失败: {e}")

                    # 保护插件：记录开平仓事件
                    # 仅当工具回调真实执行成功（size > 0）时上报，避免执行失败也误触发风控
                    if self.protection_manager and details.get("size", 0) > 0:
                        try:
                            if decision in ("BUY", "SELL_SHORT"):
                                self.protection_manager.on_trade_open(
                                    symbol=symbol,
                                    entry_price=float(details.get("entry_price", 0)),
                                    size=float(details["size"]),
                                    is_long=(decision == "BUY"),
                                    leverage=int(details.get("leverage", 1)),
                                )
                            elif decision in ("SELL", "BUY_TO_COVER"):
                                self.protection_manager.on_trade_close(
                                    symbol=symbol, pnl=float(details.get("pnl", 0))
                                )
                        except Exception as e:
                            self.logger.print_warning(
                                f"[{symbol}] 保护插件记录失败: {e}"
                            )

                except Exception as e:
                    self.logger.print_error(f"{symbol} Agent 决策异常: {e}")
                    self.logger.logger.exception(e)
                    # 记录决策异常到云端
                    cloud = get_cloud_logger()
                    if cloud:
                        cloud.send_alert(
                            symbol=symbol,
                            alert_type="decision_error",
                            severity="high",
                            message=str(e),
                            details={"traceback": traceback.format_exc(), "cycle": self.cycle_counter},
                        )
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

            # 第三步：按需运行复盘 Agent
            self._maybe_run_review_cycle()

            self.logger.print_header(
                f"✅ 多 Agent 交易周期完成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # 通知市场监控器决策周期已完成（重置价格基准）
            if self.market_monitor:
                self.market_monitor.notify_cycle_completed()

        except Exception as e:
            self.logger.print_error(f"交易周期异常: {e}")
            self.logger.logger.exception(e)

            # 记录周期异常到云端
            cloud = get_cloud_logger()
            if cloud:
                cloud.send_alert(
                    symbol="ALL",
                    alert_type="cycle_error",
                    severity="extreme",
                    message=str(e),
                    details={"traceback": traceback.format_exc(), "cycle": self.cycle_counter},
                )

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
        """启动机器人（K 线节拍驱动主循环）"""
        try:
            self.logger.print_section("启动多 Agent 交易机器人", style="bold green")

            # 记录启动时间
            self.statistics["start_time"] = datetime.now()

            # 创建后台调度器（仅用于非决策任务）
            self.scheduler = BackgroundScheduler()

            # 外部信息收集定时任务
            if self.external_info_agent:
                interval_hours = getattr(self.config, "external_info_interval_hours", 3.0)
                interval_minutes = int(interval_hours * 60)

                self.scheduler.add_job(
                    self._run_external_info_collection,
                    trigger=IntervalTrigger(minutes=interval_minutes),
                    id="external_info_collection",
                    name="外部信息收集任务",
                    replace_existing=True,
                )
                self.logger.print_info(f"外部信息收集任务已添加，间隔: {interval_hours} 小时")

                self.logger.print_info("立即执行首次外部信息收集...")
                self._run_external_info_collection()

            # 网格交易定时任务
            if self.grid_manager and self.grid_agent:
                self.scheduler.add_job(
                    self.grid_cycle,
                    trigger=IntervalTrigger(minutes=self.config.interval_minutes),
                    id="grid_cycle",
                    name="AI网格决策循环",
                    replace_existing=True,
                )
                self.logger.print_info(f"网格交易决策任务已添加，间隔: {self.config.interval_minutes} 分钟")

                # 如果配置了立即执行，在后台线程中立即运行一次网格循环
                if self.config.run_immediately:
                    self.logger.print_info("立即执行首次网格交易循环...")
                    self._grid_immediate_thread = threading.Thread(
                        target=self.grid_cycle, daemon=True,
                        name="grid-immediate",
                    )
                    self._grid_immediate_thread.start()

            # 启动后台调度器
            self.scheduler.start()

            # 启动市场主动监控线程
            if self.market_monitor:
                self.market_monitor.start()
                self.logger.print_info(
                    f"市场主动监控已启动 | "
                    f"波动阈值: {self.config.market_monitor_alert_threshold_pct}%"
                )

            self.is_running = True
            self.start_time = datetime.now()

            # 永续合约主流程
            if self.config.perp_enabled:
                # 如果配置了立即执行，先执行一次
                if self.config.run_immediately:
                    self.logger.print_info("立即执行第一次交易循环...")
                    self.trading_cycle()

                self.logger.print_section(
                    f"K 线节拍驱动已启动 | "
                    f"周期: {self.config.timeframe} | "
                    f"偏移: {self.config.timeframe_offset}s",
                    style="bold green",
                )

                # 主循环：K 线节拍驱动
                while self.is_running:
                    self._wait_next_candle()
                    if self.is_running:
                        self.trading_cycle()
            else:
                self.logger.print_section(
                    "永续合约交易已禁用。程序运行在纯网格模式下。",
                    style="bold green",
                )
                # 主循环：保持程序存活
                while self.is_running:
                    time.sleep(1)

        except KeyboardInterrupt:
            self.logger.print_info("收到 Ctrl+C，触发优雅停机")
        except Exception as e:
            self.logger.print_error(f"启动失败: {e}")
            self.logger.logger.exception(e)
            raise
        finally:
            # 优雅停机：循环退出（停止信号 / Ctrl+C / 异常）后统一清理。stop() 内部对各
            # 组件做了存在性守卫，初始化中途失败调用也安全；scheduler.shutdown(wait=True)
            # 会等待进行中的网格周期跑完，避免腰斩撤单/布单序列而留下无保护裸仓。
            try:
                self.stop("收到停止信号或异常退出")
            except Exception as cleanup_err:
                self.logger.print_error(f"停机清理异常: {cleanup_err}")

    def _wait_next_candle(self):
        """等待到下一根 K 线收盘后 offset 秒（分段 sleep 以便快速响应停止信号）"""
        target = next_candle_close_ts(self.config.timeframe) + self.config.timeframe_offset
        sleep_duration = max(target - time.time(), self.config.min_throttle_secs)

        target_str = time.strftime("%H:%M:%S", time.gmtime(target))
        self.logger.print_info(
            f"[节拍] 等待下一根 {self.config.timeframe} K 线 | "
            f"目标: {target_str} UTC | 等待: {sleep_duration:.0f}s"
        )

        end_time = time.time() + sleep_duration
        while time.time() < end_time and self.is_running:
            time.sleep(max(0.0, min(1.0, end_time - time.time())))

    def stop(self, reason: str = "用户手动停止"):
        """停止机器人"""
        self.logger.print_section("🛑 停止多 Agent 交易机器人", style="bold red")
        self.is_running = False

        # 停止市场监控线程
        if self.market_monitor:
            self.market_monitor.stop()

        # 等待「立即执行的网格周期」完成（首次布单可能仍在运行中）。
        # 该线程由 start() 直接派生、不走调度器，scheduler.shutdown(wait=True) 不会等它，
        # 故单独 join，避免腰斩布单/撤单序列而留下无保护裸仓。
        if (
            getattr(self, "_grid_immediate_thread", None) is not None
            and self._grid_immediate_thread.is_alive()
        ):
            self.logger.print_info("等待进行中的网格周期完成（最多 30s）...")
            self._grid_immediate_thread.join(timeout=30)

        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()

        # 发送关闭通知
        self._send_shutdown_notification(reason)

        # 记录关闭事件到云端并刷新日志
        cloud = get_cloud_logger()
        if cloud:
            cloud.send_system_event("shutdown", details={
                "reason": reason,
                "cycle_count": self.cycle_counter,
                "statistics": self.statistics,
            })
            cloud.shutdown()

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
                # 改进2: 传递 current_regime
                current_regime = None
                if getattr(self.config, "review_regime_aware_enabled", False):
                    current_regime = self._get_current_regime(None)

                added = self.review_memory_store.add_lessons(
                    symbol=symbol,
                    lessons=lessons,
                    min_confidence=self.config.review_min_confidence,
                    current_regime=current_regime,
                    max_positive_ratio=getattr(
                        self.config, "review_max_positive_ratio", 0.7
                    ),
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
                },
                status="SUCCESS",
            )

        # 改进1b: 每周反思（在复盘周期末尾触发）
        self._maybe_run_weekly_reflection()

    def _maybe_run_weekly_reflection(self):
        """改进1b: 触发每周策略级反思"""
        if not getattr(self, "weekly_reflector", None):
            return

        if not self.weekly_reflector.should_run(self._weekly_reflection_last_run):
            return

        try:
            report = self.weekly_reflector.run_weekly_reflection(
                symbols=self.config.symbols,
                decision_history=self.decision_history,
            )

            if report.get("status") == "completed":
                self._weekly_reflection_last_run = datetime.now()
                self.logger.print_info("每周策略级复盘完成")

                # 改进5: Prompt 元反思（在每周反思后触发）
                if getattr(self, "prompt_meta_reflector", None):
                    try:
                        # 聚合本周所有记录
                        all_records = []
                        for symbol in self.config.symbols:
                            records = self.decision_history.get_recent_decisions(symbol, limit=100)
                            all_records.extend(records)

                        all_lessons = []
                        if self.review_memory_store:
                            for symbol in self.config.symbols:
                                all_lessons.extend(self.review_memory_store.get_lessons(symbol))

                        effectiveness_report = self.prompt_meta_reflector.evaluate_prompt_effectiveness(
                            all_records, all_lessons
                        )
                        suggestions = self.prompt_meta_reflector.generate_optimization_suggestions(
                            effectiveness_report
                        )
                        self.prompt_meta_reflector.save_report(effectiveness_report, suggestions)
                        self.logger.print_info(
                            f"Prompt 效果评估完成 | 综合评分: {effectiveness_report.get('overall_score', 0):.1%} | "
                            f"优化建议: {len(suggestions)} 条"
                        )
                    except Exception as e:
                        self.logger.print_warning(f"Prompt 元反思失败: {e}")

        except Exception as e:
            self.logger.print_warning(f"每周反思失败: {e}")

    def _get_current_regime(self, enriched_data: dict[str, Any] | None) -> str | None:
        """
        改进2: 获取当前市场 Regime

        从 enriched_data 中提取 regime_hint 或从 market_state 推导
        """
        if enriched_data:
            regime_hint = enriched_data.get("regime_hint", "")
            if "趋势" in str(regime_hint) or "trending" in str(regime_hint).lower():
                return "trending"
            if "震荡" in str(regime_hint) or "ranging" in str(regime_hint).lower():
                return "ranging"
            if "波动" in str(regime_hint) or "volatile" in str(regime_hint).lower():
                return "volatile"

        return None

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

    def _grid_protection_triggered(self, symbol: str) -> bool:
        """
        网格周期的账户级风控熔断检查。

        网格此前仅有 per-grid Triple Barrier（单网格止盈止损），缺少账户级回撤 /
        连亏 / 单日亏损熔断。此处复用永续同款 ``ProtectionManager.check_all``，在
        布单前拦截：

        - ``CLOSE_ALL_POSITIONS``：平掉全部持仓 + 撤销该 symbol 的网格挂单（含
          trigger）并清理网格状态，跳过本轮布单。
        - ``PAUSE_NEW_TRADES``：仅跳过本轮布单（不新增挂单）。

        余额/持仓查询失败时不误熔断（返回 False），避免网络抖动把网格误停。

        Returns:
            True 表示已熔断、本轮网格应跳过布单；False 表示正常继续。
        """
        # 自包含守卫：未配置风控时不熔断（当前调用方虽已先判 protection_manager，
        # 但此处再判一次可避免未来其他调用路径触发 None.check_all 崩溃）
        if not self.protection_manager:
            return False

        try:
            balance_info = self.order_manager.get_available_balance_info()
        except Exception as e:
            self.logger.print_warning(f"[网格风控] 获取余额失败，跳过风控检查: {e}")
            return False

        if balance_info.get("status") != "ok":
            self.logger.print_warning(
                f"[网格风控] 余额查询异常({balance_info.get('message')})，跳过风控检查"
            )
            return False

        try:
            current_positions = self.order_manager.get_current_positions()
        except Exception as e:
            self.logger.print_warning(f"[网格风控] 获取持仓失败，跳过风控检查: {e}")
            return False

        context = ProtectionContext(
            balance=balance_info.get("available", 0),
            equity=balance_info.get("total", 0),
            unrealized_pnl=balance_info.get("unrealized_pnl", 0),
            margin_used=balance_info.get("occupied", 0),
            current_positions=current_positions or [],
        )
        results = self.protection_manager.check_all(context)
        action = ProtectionManager.get_most_severe_action(results)
        for r in results:
            self.logger.print_warning(f"[网格风控]{r.reason}")

        if action == ProtectionAction.CLOSE_ALL_POSITIONS:
            self.logger.print_warning("[网格风控]账户熔断触发，平掉全部持仓并撤销网格挂单")
            for pos in (current_positions or []):
                sym = pos.get("coin", "")
                if sym:
                    try:
                        self.order_manager.close_position(sym)
                        # 强平属风控主动行为：清理持仓记录，不向连亏插件上报虚假 pnl
                        self.protection_manager.on_position_dropped(sym)
                    except Exception as e:
                        self.logger.print_error(f"[网格风控]平仓失败 {sym}: {e}")
            # 撤销该 symbol 的全部网格挂单（含 trigger）并清理本地网格状态，
            # 避免熔断期间挂单成交新增敞口
            if self.grid_manager:
                try:
                    self.grid_manager.cancel_all_orders(symbol)
                    self.logger.print_info(f"[网格风控]已撤销 {symbol} 的全部网格挂单")
                except Exception as e:
                    self.logger.print_error(f"[网格风控]撤销网格挂单失败: {e}")
            return True

        if action == ProtectionAction.PAUSE_NEW_TRADES:
            self.logger.print_warning("[网格风控]账户风控暂停新开仓，本轮跳过网格布单")
            return True

        return False

    def grid_cycle(self):
        """执行一个网格交易周期"""
        symbol = self.config.symbols[0] if self.config.symbols else "UNKNOWN"
        cloud = get_cloud_logger()

        # 与永续 trading_cycle 共享交易锁：RUN_MODE=all 时两者会并发操作同一 symbol，
        # 不加锁会踩乱持仓/挂单状态。冲突时跳过本轮（网格下一周期再布）。
        if not self._trading_lock.acquire(blocking=False):
            self.logger.print_warning(
                f"⏭️ 交易周期运行中，跳过本次网格周期 ({symbol})"
            )
            return

        try:
            self.logger.print_header(
                f"🔄 网格交易周期开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            if cloud:
                cloud.send_cycle_event(symbol=symbol, phase="start")

            # 账户级风控熔断（回撤/连亏/单日亏损）：网格此前只有 per-grid Triple Barrier，
            # 此处复用永续同款 ProtectionManager，在布单前拦截。熔断时已平仓/撤单并跳过布单。
            if self.protection_manager and self._grid_protection_triggered(symbol):
                if cloud:
                    cloud.send_cycle_event(
                        symbol=symbol,
                        phase="skip",
                        details={"reason": "账户级风控熔断"},
                        level="warn",
                    )
                return

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

            ai_decision = self.grid_agent.make_decision(
                market_data,
                multi_timeframe_trends,
                current_grid_summary,
            )

            # 记录网格决策到本地日志 + 云端
            action = ai_decision.get("action", "UNKNOWN")
            reason = ai_decision.get("reason", "")
            confidence = float(ai_decision.get("confidence", 0.0))
            decision_ok = action in ("UPDATE_GRID", "KEEP_GRID")
            self.logger.log_decision(
                symbol=symbol,
                market_data=market_data,
                prompt="[GridAgent]",
                ai_response=reason,
                decision=action,
                action_details=ai_decision,
                status="SUCCESS" if decision_ok else "ERROR",
                error_message=None if decision_ok else reason,
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
            self.logger.print_error(f"网格周期执行异常: {e}")
            self.logger.logger.exception(e)
            if cloud:
                cloud.send_alert(
                    symbol=symbol,
                    alert_type="grid_cycle_error",
                    severity="high",
                    message=str(e),
                    details={"traceback": traceback.format_exc()},
                )
        finally:
            # 释放交易锁，确保永续/网格下一周期可正常获取
            self._trading_lock.release()


# 模块级引用：信号处理器需访问当前 bot 实例以触发优雅停机
_running_bot: "QuantFlowBot | None" = None
_signal_count = 0


def signal_handler(signum, frame):
    """
    信号处理器：触发优雅停机（覆盖永续 + 网格两条路径）。

    首次收到 SIGINT/SIGTERM：仅置停止标志并立即返回，不调用 sys.exit —— 让主循环
    退出、进行中的永续/网格周期自然跑完（grid_manager 的撤单/布单序列不会被腰斩，
    避免留下无保护裸仓）。统一的 stop() 清理由 start() 的 finally 负责。

    第二次收到信号：用户要求强制退出，立即 sys.exit。
    """
    global _signal_count
    _signal_count += 1
    if _signal_count >= 2:
        print("\n\n收到第二次停止信号，强制退出。")
        sys.exit(1)

    print(
        f"\n\n收到停止信号({signum})，正在优雅停机"
        "（等待当前周期完成；再次按 Ctrl+C 强制退出）..."
    )
    if _running_bot is not None:
        _running_bot.is_running = False
    else:
        # bot 尚未创建完成，无状态可清理，直接退出
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

    global _running_bot
    try:
        # 创建并启动机器人
        bot = QuantFlowBot(config_path=args.config, env_file=args.env_file)
        _running_bot = bot  # 供信号处理器触发优雅停机
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
