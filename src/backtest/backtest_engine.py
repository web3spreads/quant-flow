"""
回测引擎核心模块
负责时间推进、交易执行、盈亏跟踪等核心逻辑
"""

from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.agent.grid_agent import GridAgent
from src.agent.review_agent import ReviewAgent
from src.agent.review_memory import ReviewMemoryStore
from src.agent.single_symbol_agent import SingleSymbolAgent
from src.agent.summary_agent_v2 import DecisionHistory, SummaryAgentV2
from src.config import FEE_RATE_PER_SIDE
from src.data.indicators import TechnicalIndicators
from src.i18n import get_text
from src.prompt_manager import PromptManager
from src.trading.grid_manager import GridManager
from src.utils.logger import TradingLogger

from .mock_client import MockHyperliquidClient
from .mock_order_manager import MockOrderManager
from .report_generator import BacktestReportGenerator


class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        symbol: str,
        historical_data: pd.DataFrame,
        initial_balance: float = 1000.0,
        config: Any = None,
        logger: TradingLogger | None = None,
        prompt_manager: PromptManager | None = None,
        strategy: str = "single",
        grid_state_file: str | None = None,
    ):
        """
        初始化回测引擎

        Args:
            symbol: 交易对符号
            historical_data: 历史K线数据
            initial_balance: 初始余额
            config: 配置对象
            logger: 日志记录器
            prompt_manager: Prompt管理器
            strategy: 回测策略（single/grid）
            grid_state_file: 网格状态文件路径（仅 grid 策略使用）
        """
        self.symbol = symbol
        self.historical_data = historical_data.copy()
        self.historical_data = self.historical_data.sort_values("timestamp").reset_index(drop=True)
        self.initial_balance = initial_balance
        self.config = config
        self.logger = logger or TradingLogger()
        self.prompt_manager = prompt_manager
        self.strategy = str(strategy or "single").lower()
        self.grid_state_file = grid_state_file or "grid_state.backtest.json"

        # 初始化模拟客户端和订单管理器
        self.client = MockHyperliquidClient(historical_data, initial_balance)
        self.order_manager = MockOrderManager(
            client=self.client,
            take_profit_ratio=config.take_profit_ratio if config else 0.05,
            stop_loss_ratio=config.stop_loss_ratio if config else 0.02,
            default_leverage=config.max_leverage if config else 10,
        )

        # 初始化Agent（需要配置信息）
        self.grid_manager: GridManager | None = None
        if config:
            if self.strategy == "grid":
                self.agent = GridAgent(
                    symbol=symbol,
                    order_manager=self.order_manager,
                    logger=self.logger,
                    openai_api_base=config.openai_api_base,
                    openai_api_key=config.openai_api_key,
                    openai_model=config.openai_model,
                    trade_amount=config.max_trade_amount,
                    width_pct_min=config.grid_width_min_pct,
                    width_pct_max=config.grid_width_max_pct,
                    width_pct_fallback=config.grid_width_fallback_pct,
                    ai_width_blend_weight=config.grid_ai_blend_weight,
                )
                self.grid_manager = GridManager(
                    order_manager=self.order_manager,
                    logger=self.logger,
                    state_file=self.grid_state_file,
                    notifier=None,
                )
            else:
                self.agent = SingleSymbolAgent(
                    symbol=symbol,
                    order_manager=self.order_manager,
                    logger=self.logger,
                    openai_api_base=config.openai_api_base,
                    openai_api_key=config.openai_api_key,
                    openai_model=config.openai_model,
                    temperature=config.agent_temperature,
                    max_iterations=config.agent_max_iterations,
                    trade_amount=config.max_trade_amount,
                    max_leverage=config.max_leverage,
                    take_profit_ratio=config.take_profit_ratio,
                    stop_loss_ratio=config.stop_loss_ratio,
                    notifier=None,  # 回测不需要通知
                    prompt_manager=prompt_manager,
                    limit_order_enabled=config.limit_order_enabled
                    if hasattr(config, "limit_order_enabled")
                    else False,
                )
        else:
            self.agent = None

        # 决策历史
        self.decision_history = DecisionHistory(max_history=50)

        # 汇总Agent（用于历史汇总）
        if config:
            self.summary_agent = SummaryAgentV2(
                logger=self.logger,
                openai_api_base=config.openai_api_base,
                openai_api_key=config.openai_api_key,
                openai_model=config.openai_model,
                temperature=0.1,
                max_context_tokens=2000,
            )
        else:
            self.summary_agent = None

        # 复盘Agent（如果启用）
        self.review_agent = None
        self.review_memory_store = None
        self.cycle_counter = 0
        if config and config.review_enabled and prompt_manager:
            try:
                self.logger.print_info("初始化复盘 Agent...")
                self.review_memory_store = ReviewMemoryStore(
                    path=config.review_memory_file,
                    max_lessons=config.review_max_lessons,
                )
                # 使用 review_model 如果存在，否则使用 openai_model
                review_model = (
                    config.review_model
                    if hasattr(config, "review_model") and config.review_model
                    else config.openai_model
                )

                self.review_agent = ReviewAgent(
                    logger=self.logger,
                    prompt_manager=prompt_manager,
                    openai_api_base=config.openai_api_base,
                    openai_api_key=config.openai_api_key,
                    model=review_model,
                    temperature=config.review_temperature,
                    lookback_decisions=config.review_lookback_decisions,
                    memory_store=self.review_memory_store,
                    min_confidence=config.review_min_confidence,
                    similarity_threshold=config.review_similarity_threshold,
                    similarity_weights=config.review_similarity_weights,
                    confidence_decay_factor=config.review_confidence_decay_factor,
                    similarity_method=config.review_similarity_method,
                )
                self.logger.print_info("✅ 复盘 Agent 初始化完成")
            except Exception as e:
                self.logger.print_warning(f"复盘 Agent 初始化失败: {e}")

        # 交易记录
        self.trades: list[dict[str, Any]] = []
        self.closed_trades: list[dict[str, Any]] = []

        # 手续费率
        self.fee_rate = FEE_RATE_PER_SIDE

        # 实时报告
        self.live_report_path: Path | None = None
        self.live_report_interval: int = 1
        self._last_live_report_index: int = -1
        self._total_decision_points: int = 0

        # 数据增强相关
        self.start_time = datetime.now()  # 用于计算elapsed_minutes
        self.language = "zh"  # 默认中文，可以从config读取

        print("✅ 回测引擎初始化完成")
        print(f"   策略模式: {self.strategy}")
        print(f"   交易对: {symbol}")
        print(f"   初始余额: ${initial_balance:.2f}")
        print(f"   数据点数: {len(historical_data)}")

    def _restore_from_live_report(
        self, resume_info: dict[str, Any], decision_timestamps: list[datetime]
    ) -> int:
        """
        从 live.json 文件恢复状态

        Args:
            resume_info: 从 live.json 加载的恢复信息
            decision_timestamps: 决策时间戳列表

        Returns:
            已处理的决策点索引（从该索引继续执行）
        """
        print("\n🔄 恢复回测状态...")

        try:
            # 验证恢复信息
            if not resume_info:
                print("   ⚠️ 恢复信息为空，将从开始执行")
                return 0

            # 验证交易对匹配
            resume_symbol = resume_info.get("symbol")
            if resume_symbol and resume_symbol.upper() != self.symbol.upper():
                print(
                    f"   ⚠️ 恢复文件中的交易对 ({resume_symbol}) 与当前交易对 ({self.symbol}) 不匹配"
                )
                print("   将从头开始回测")
                return 0

            # 1. 恢复账户余额
            current_balance = resume_info.get("current_balance", {})
            account_value = current_balance.get("total", self.initial_balance)
            margin_used = current_balance.get("occupied", 0.0)

            # 验证余额合理性
            if account_value < 0:
                print(f"   ⚠️ 恢复的账户余额无效: ${account_value:.2f}，使用初始余额")
                account_value = self.initial_balance
            if margin_used < 0:
                margin_used = 0.0

            self.client.update_account_value(account_value, margin_used)
            print(f"   ✅ 账户余额已恢复: ${account_value:.2f} (已用保证金: ${margin_used:.2f})")

            # 2. 恢复已完成的交易记录
            trades = resume_info.get("trades", [])
            restored_trades = 0
            for trade in trades:
                try:
                    # 转换时间戳格式
                    if isinstance(trade.get("entry_time"), str):
                        trade["entry_time"] = pd.to_datetime(trade["entry_time"])
                    if isinstance(trade.get("exit_time"), str):
                        trade["exit_time"] = pd.to_datetime(trade["exit_time"])

                    # 验证交易数据完整性
                    if not all(
                        k in trade for k in ["entry_price", "exit_price", "size", "net_pnl"]
                    ):
                        print(f"   ⚠️ 跳过不完整的交易记录: {trade.get('entry_time')}")
                        continue

                    self.closed_trades.append(trade)
                    restored_trades += 1
                except Exception as e:
                    print(f"   ⚠️ 恢复交易记录时出错: {e}，跳过该记录")
                    continue

            if restored_trades > 0:
                print(f"   ✅ 已恢复 {restored_trades} 笔已完成交易")

            # 3. 恢复当前持仓
            open_positions = resume_info.get("open_positions", [])
            restored_positions = 0
            for pos_data in open_positions:
                try:
                    if pos_data.get("symbol") != self.symbol:
                        continue

                    # 恢复持仓
                    entry_price = pos_data.get("entry_price", 0.0)
                    size = pos_data.get("size", 0.0)
                    leverage = pos_data.get("leverage", 1)
                    is_long = pos_data.get("is_long", True)
                    take_profit_price = pos_data.get("take_profit_price")
                    stop_loss_price = pos_data.get("stop_loss_price")

                    # 验证持仓数据
                    if entry_price <= 0 or size <= 0 or leverage <= 0:
                        print(
                            f"   ⚠️ 跳过无效的持仓数据: entry_price={entry_price}, size={size}, leverage={leverage}"
                        )
                        continue

                    # 转换 entry_time
                    entry_time = pos_data.get("entry_time")
                    if isinstance(entry_time, str):
                        entry_time = pd.to_datetime(entry_time)

                    # 添加持仓到客户端
                    self.client.add_position(
                        symbol=self.symbol,
                        size=size,
                        entry_price=entry_price,
                        leverage=leverage,
                        is_long=is_long,
                        take_profit_price=take_profit_price,
                        stop_loss_price=stop_loss_price,
                    )

                    # 更新持仓的 entry_time（如果提供了）
                    if entry_time:
                        for pos in self.client.positions:
                            if pos.get("coin") == self.symbol:
                                pos["entry_time"] = entry_time
                                break

                    # 更新未实现盈亏
                    current_price = pos_data.get("current_price")
                    if current_price and current_price > 0:
                        if is_long:
                            unrealized_pnl = (current_price - entry_price) * size
                        else:
                            unrealized_pnl = (entry_price - current_price) * size
                        self.client.update_position_pnl(self.symbol, unrealized_pnl)

                    restored_positions += 1
                except Exception as e:
                    print(f"   ⚠️ 恢复持仓时出错: {e}，跳过该持仓")
                    continue

            if restored_positions > 0:
                print(f"   ✅ 已恢复 {restored_positions} 个持仓")

            # 4. 恢复决策历史（至少最后一次）
            last_decision = resume_info.get("last_decision")
            timestamp = None
            if last_decision:
                try:
                    market_data = last_decision.get("market_data", {})
                    decision = last_decision.get("decision", "DO_NOTHING")
                    reason = last_decision.get("reason", "")
                    action_details = last_decision.get("action_details", {})

                    # 转换时间戳
                    timestamp = last_decision.get("timestamp")
                    if isinstance(timestamp, str):
                        timestamp = pd.to_datetime(timestamp)
                    if timestamp and isinstance(market_data, dict):
                        market_data["timestamp"] = timestamp

                    self.decision_history.add_decision(
                        symbol=self.symbol,
                        decision=decision,
                        market_data=market_data,
                        reason=reason,
                        action_details=action_details,
                    )
                    print(f"   ✅ 已恢复最后一次决策: {decision}")
                except Exception as e:
                    print(f"   ⚠️ 恢复决策历史时出错: {e}")

            # 5. 找到已处理的决策点索引
            processed_count = resume_info.get("progress", {}).get("processed_decisions", 0)
            total_count = resume_info.get("progress", {}).get(
                "total_decisions", len(decision_timestamps)
            )

            # 验证决策点数量是否匹配
            if total_count != len(decision_timestamps):
                print(
                    f"   ⚠️ 恢复文件中的总决策点数 ({total_count}) 与当前不匹配 ({len(decision_timestamps)})"
                )
                print("   将尝试从已处理数量继续")

            if processed_count > 0:
                # 尝试从最后一个决策时间戳找到对应的索引
                if last_decision and timestamp:
                    try:
                        # 找到最接近的时间戳索引
                        time_diffs = [
                            (ts - timestamp).total_seconds() for ts in decision_timestamps
                        ]
                        closest_idx = min(range(len(time_diffs)), key=lambda i: abs(time_diffs[i]))

                        # 如果最接近的时间戳差异太大（超过1小时），使用处理数量
                        if abs(time_diffs[closest_idx]) > 3600:
                            print(
                                f"   ⚠️ 时间戳差异较大 ({time_diffs[closest_idx]:.0f}秒)，使用处理数量"
                            )
                            resume_index = min(processed_count, len(decision_timestamps) - 1)
                        else:
                            # 确保索引在有效范围内，并且是已处理的下一个
                            resume_index = min(closest_idx + 1, len(decision_timestamps) - 1)
                    except Exception as e:
                        print(f"   ⚠️ 查找时间戳索引时出错: {e}，使用处理数量")
                        resume_index = min(processed_count, len(decision_timestamps) - 1)
                else:
                    # 如果没有时间戳，使用处理数量作为索引
                    resume_index = min(processed_count, len(decision_timestamps) - 1)

                # 确保索引有效
                if resume_index < 0:
                    resume_index = 0
                if resume_index >= len(decision_timestamps):
                    resume_index = len(decision_timestamps) - 1

                print(f"   ✅ 将从决策点 {resume_index + 1}/{len(decision_timestamps)} 继续执行")
                return resume_index
            else:
                print("   ⚠️ 未找到已处理的决策点，将从开始执行")
                return 0

        except Exception as e:
            print(f"   ⚠️ 状态恢复过程中出现错误: {e}")
            print("   将从头开始回测")
            import traceback

            traceback.print_exc()
            return 0

    def run(
        self,
        decision_interval_minutes: int = 15,
        live_report_path: str | None = None,
        live_report_interval: int = 1,
        resume_from: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        运行回测

        Args:
            decision_interval_minutes: 决策间隔（分钟）

        Returns:
            回测结果字典
        """
        print("\n🚀 开始回测...")
        print(f"   决策间隔: {decision_interval_minutes} 分钟")

        if not self.agent:
            raise ValueError("Agent未初始化，无法运行回测")

        if self.strategy == "grid":
            return self._run_grid_backtest(
                decision_interval_minutes=decision_interval_minutes,
                live_report_path=live_report_path,
                live_report_interval=live_report_interval,
                resume_from=resume_from,
            )

        # 配置实时报告
        if resume_from and resume_from.get("resume_file"):
            # 如果从恢复文件继续，使用恢复文件作为实时报告路径
            self.live_report_path = Path(resume_from["resume_file"])
            self.live_report_interval = max(1, live_report_interval)
            self._last_live_report_index = -1
        elif live_report_path:
            self.live_report_path = Path(live_report_path)
            self.live_report_interval = max(1, live_report_interval)
            self._last_live_report_index = -1
        else:
            self.live_report_path = None

        # 计算技术指标
        if self.config:
            df = TechnicalIndicators.calculate_all_indicators(
                self.historical_data.copy(),
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
        else:
            df = TechnicalIndicators.calculate_all_indicators(self.historical_data.copy())

        # 预计算完整 4h 数据，避免每个决策点重复 resample 和指标计算
        precomputed_4h = self._prepare_4h_dataframe(df)

        # 按决策间隔选择时间点
        decision_timestamps = self._get_decision_timestamps(df, decision_interval_minutes)
        self._total_decision_points = len(decision_timestamps)

        print(f"   决策点数量: {len(decision_timestamps)}")

        # 如果提供了恢复信息，恢复状态
        start_index = 0
        if resume_from:
            start_index = self._restore_from_live_report(resume_from, decision_timestamps)
            # 更新实时报告索引，确保与恢复的决策点一致
            if start_index > 0:
                self._last_live_report_index = start_index - 1
            else:
                self._last_live_report_index = -1

        self._maybe_write_live_report(
            processed_decisions=start_index,
            total_decisions=self._total_decision_points,
            force=True,
            status="running" if start_index > 0 else "initializing",
        )

        # 遍历每个决策点（从恢复的索引开始）
        for i in range(start_index, len(decision_timestamps)):
            timestamp = decision_timestamps[i]
            try:
                # 在到达决策点之前，检查上一个决策点到当前决策点之间的所有K线
                # 以更真实地模拟实际交易中的实时监控
                if i > 0:
                    prev_timestamp = decision_timestamps[i - 1]
                    self._check_tpsl_between_decisions(prev_timestamp, timestamp, df)

                # 设置当前时间
                self.client.set_current_time(timestamp)

                # 获取当前市场数据
                market_data = self._get_market_data_at_time(df, timestamp)
                if not market_data:
                    continue

                # 获取当前持仓和余额
                current_positions = self.order_manager.get_current_positions()
                balance_info = self.order_manager.get_available_balance_info()

                # 在决策点也检查止盈止损（作为最后一道检查）
                self._check_take_profit_stop_loss(timestamp, df)

                # 调整交易金额
                suggestion = self.order_manager.calculate_suggested_trade_amount(
                    desired_amount=self.config.max_trade_amount if self.config else 100.0,
                    min_trade_amount=10.0,
                    balance_info=balance_info,
                )

                if not suggestion["can_trade"] and len(current_positions) == 0:
                    # 无持仓且余额不足，跳过
                    continue

                adjusted_amount = suggestion["suggested_amount"] if suggestion["can_trade"] else 0
                self.agent.trade_amount = adjusted_amount

                # 生成历史汇总
                historical_summary = None
                history_count = self.decision_history.get_history_count(self.symbol)

                if history_count >= 20 and self.summary_agent:
                    recent_10 = self.decision_history.get_recent_decisions(self.symbol, 10)
                    recent_10_20 = self.decision_history.get_decisions_range(self.symbol, 10, 20)
                    historical_summary = self.summary_agent.create_compressed_summary(
                        symbol=self.symbol, recent_records=recent_10, older_records=recent_10_20
                    )
                elif history_count >= 10 and self.summary_agent:
                    recent = self.decision_history.get_recent_decisions(self.symbol, 10)
                    historical_summary = self.summary_agent.create_compressed_summary(
                        symbol=self.symbol, recent_records=recent, older_records=None
                    )

                # 获取多周期趋势（简化版本，回测中只使用当前周期）
                multi_timeframe_trends = {"15m": "NEUTRAL"}  # 简化处理

                # 生成增强数据（为nof1策略提供额外指标）
                enriched_data = self._enrich_market_data_for_backtest(
                    df=df,
                    timestamp=timestamp,
                    market_data=market_data,
                    balance_info=balance_info,
                    precomputed_4h=precomputed_4h,
                )

                # 调用Agent做出决策
                decision, details = self.agent.make_decision(
                    market_data=market_data,
                    multi_timeframe_trends=multi_timeframe_trends,
                    current_positions=current_positions,
                    max_positions=self.config.max_positions if self.config else 2,
                    historical_summary=historical_summary,
                    enriched_data=enriched_data,
                )

                # 记录决策历史
                self.decision_history.add_decision(
                    symbol=self.symbol,
                    decision=decision,
                    market_data=market_data,
                    reason=details.get("output", "")[:200],
                    action_details=self._sanitize_action_details(details),
                )

                # 更新周期计数器并运行复盘 Agent（如果启用）
                self.cycle_counter += 1
                if self.review_agent and self.config:
                    # 检查是否有足够的决策记录
                    decision_count = self.decision_history.get_history_count(self.symbol)
                    if decision_count >= self.config.review_lookback_decisions:
                        if self.cycle_counter % max(1, self.config.review_run_every_cycles) == 0:
                            self._run_review_agent()

                # 执行决策（Agent的工具回调会自动调用order_manager）
                # 对于平仓决策，需要检测持仓变化并记录交易
                positions_after_decision = self.order_manager.get_current_positions()

                # 检查是否有持仓被平掉
                for pos_before in current_positions:
                    if pos_before.get("coin") == self.symbol:
                        # 检查这个持仓是否还存在
                        pos_after = next(
                            (p for p in positions_after_decision if p.get("coin") == self.symbol),
                            None,
                        )
                        if not pos_after:
                            # 持仓被平掉，记录交易
                            current_price = self.client.get_current_price(self.symbol)
                            if current_price:
                                self._close_position(
                                    self.symbol, current_price, f"Agent决策: {decision}"
                                )

                # 更新持仓盈亏
                self._update_positions_pnl(timestamp, df)

                # 更新账户价值
                self._update_account_value()
                self._maybe_write_live_report(
                    processed_decisions=i + 1, total_decisions=self._total_decision_points
                )

            except Exception as e:
                print(f"⚠️ 决策点 {i + 1}/{len(decision_timestamps)} 处理失败: {e}")
                import traceback

                traceback.print_exc()
                continue

        # 平掉所有剩余持仓
        self._close_all_positions(df.iloc[-1]["timestamp"], df.iloc[-1]["close"])

        # 生成回测结果
        result = self._generate_result()
        result["status"] = "completed"

        print("\n✅ 回测完成")
        print(f"   总交易数: {len(self.closed_trades)}")
        print(f"   最终余额: ${result['final_balance']:.2f}")
        print(f"   总收益率: {result['total_return'] * 100:.2f}%")

        self._maybe_write_live_report(
            processed_decisions=self._total_decision_points,
            total_decisions=self._total_decision_points,
            force=True,
            status="completed",
            base_result=result,
        )

        return result

    def _run_grid_backtest(
        self,
        decision_interval_minutes: int = 15,
        live_report_path: str | None = None,
        live_report_interval: int = 1,
        resume_from: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        运行网格策略回测
        """
        if not self.grid_manager:
            raise ValueError("GridManager未初始化，无法运行网格回测")

        # 配置实时报告
        if resume_from and resume_from.get("resume_file"):
            self.live_report_path = Path(resume_from["resume_file"])
            self.live_report_interval = max(1, live_report_interval)
            self._last_live_report_index = -1
        elif live_report_path:
            self.live_report_path = Path(live_report_path)
            self.live_report_interval = max(1, live_report_interval)
            self._last_live_report_index = -1
        else:
            self.live_report_path = None

        # 计算技术指标
        if self.config:
            df = TechnicalIndicators.calculate_all_indicators(
                self.historical_data.copy(),
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
        else:
            df = TechnicalIndicators.calculate_all_indicators(self.historical_data.copy())

        decision_timestamps = self._get_decision_timestamps(df, decision_interval_minutes)
        self._total_decision_points = len(decision_timestamps)
        print(f"   决策点数量: {len(decision_timestamps)}")

        start_index = 0
        if resume_from:
            start_index = self._restore_from_live_report(resume_from, decision_timestamps)
            self._last_live_report_index = start_index - 1 if start_index > 0 else -1

        self._maybe_write_live_report(
            processed_decisions=start_index,
            total_decisions=self._total_decision_points,
            force=True,
            status="running" if start_index > 0 else "initializing",
        )

        for i in range(start_index, len(decision_timestamps)):
            timestamp = decision_timestamps[i]
            try:
                if i > 0:
                    prev_timestamp = decision_timestamps[i - 1]
                    self._process_grid_fills_between_decisions(prev_timestamp, timestamp, df)

                self.client.set_current_time(timestamp)
                market_data = self._get_market_data_at_time(df, timestamp)
                if not market_data:
                    continue

                # 决策点也撮合一次，避免边界遗漏
                self._process_grid_fills_for_candle(df, timestamp)

                trends = {"15m": "NEUTRAL"}
                summary = self.grid_manager.get_grid_summary(self.symbol)
                ai_config = self.agent.make_decision(market_data, trends, summary) or {}
                if not isinstance(ai_config, dict):
                    ai_config = {
                        "action": "KEEP_GRID",
                        "reason": f"AI返回异常类型: {type(ai_config).__name__}",
                    }
                self.grid_manager.sync_grid(self.symbol, ai_config)

                self.decision_history.add_decision(
                    symbol=self.symbol,
                    decision=ai_config.get("action", "KEEP_GRID"),
                    market_data=market_data,
                    reason=str(ai_config.get("reason", "")),
                    action_details=self._sanitize_action_details(ai_config),
                )

                self._update_positions_pnl(timestamp, df)
                self._update_account_value()
                self._maybe_write_live_report(
                    processed_decisions=i + 1, total_decisions=self._total_decision_points
                )
            except Exception as e:
                print(f"⚠️ 决策点 {i + 1}/{len(decision_timestamps)} 处理失败: {e}")
                import traceback

                traceback.print_exc()
                continue

        # 回测结束：先撤掉残余网格挂单，再平仓
        with suppress(Exception):
            self.grid_manager._cancel_all_orders(self.symbol)
        self._close_all_positions(df.iloc[-1]["timestamp"], df.iloc[-1]["close"])

        result = self._generate_result()
        result["status"] = "completed"
        print("\n✅ 回测完成")
        print(f"   总交易数: {len(self.closed_trades)}")
        print(f"   最终余额: ${result['final_balance']:.2f}")
        print(f"   总收益率: {result['total_return'] * 100:.2f}%")

        self._maybe_write_live_report(
            processed_decisions=self._total_decision_points,
            total_decisions=self._total_decision_points,
            force=True,
            status="completed",
            base_result=result,
        )
        return result

    def _process_grid_fills_between_decisions(
        self,
        start_timestamp: datetime,
        end_timestamp: datetime,
        df: pd.DataFrame,
    ):
        """
        在两个决策点之间按K线撮合网格挂单
        """
        mask = (df["timestamp"] > start_timestamp) & (df["timestamp"] <= end_timestamp)
        between_candles = df[mask].sort_values("timestamp")
        for _, row in between_candles.iterrows():
            candle_timestamp = row["timestamp"]
            self.client.set_current_time(candle_timestamp)
            self._process_grid_fills_for_candle(df, candle_timestamp)
            self._update_positions_pnl(candle_timestamp, df)

    def _process_grid_fills_for_candle(self, df: pd.DataFrame, timestamp: datetime):
        """
        在指定K线上撮合限价挂单并执行成交
        """
        time_diffs = (df["timestamp"] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        row = df.iloc[closest_idx]
        candle_low = float(row["low"])
        candle_high = float(row["high"])

        filled_orders = self.client.match_limit_orders(
            symbol=self.symbol,
            candle_low=candle_low,
            candle_high=candle_high,
        )
        if not filled_orders:
            return

        for order in filled_orders:
            self._handle_filled_grid_order(order)

        # 成交后也检查一次止盈止损，模拟同一根K线内触发风控
        self._check_take_profit_stop_loss(timestamp, df)
        self._update_account_value()

    def _handle_filled_grid_order(self, order: dict[str, Any]):
        """
        处理网格挂单成交后的持仓与交易记录
        """
        side = str(order.get("side", "")).upper()
        is_buy = side in {"B", "BUY", "BID"}
        reduce_only = bool(order.get("reduceOnly", False))
        try:
            size = abs(float(order.get("sz", 0)))
            fill_price = float(order.get("limitPx", 0))
        except (TypeError, ValueError):
            return

        if size <= 0 or fill_price <= 0:
            return

        if reduce_only:
            self._handle_reduce_only_fill(
                is_buy=is_buy,
                fill_size=size,
                fill_price=fill_price,
            )
            return

        leverage = int(order.get("leverage", self.order_manager.default_leverage))
        tp_price = order.get("tp_price")
        sl_price = order.get("sl_price")
        self.client.add_position(
            symbol=self.symbol,
            size=size,
            entry_price=fill_price,
            leverage=max(leverage, 1),
            is_long=is_buy,
            take_profit_price=tp_price,
            stop_loss_price=sl_price,
        )
        margin_add = fill_price * size / max(leverage, 1)
        self.client.update_account_value(
            account_value=self.client.account_value,
            margin_used=self.client.total_margin_used + margin_add,
        )

    def _handle_reduce_only_fill(self, is_buy: bool, fill_size: float, fill_price: float):
        """
        执行 reduce_only 成交：只对冲已有反向持仓，不开新仓
        """
        remaining = fill_size
        positions = self.client.get_positions()
        if is_buy:
            candidates = [
                p for p in positions if p.get("coin") == self.symbol and float(p.get("szi", 0)) < 0
            ]
        else:
            candidates = [
                p for p in positions if p.get("coin") == self.symbol and float(p.get("szi", 0)) > 0
            ]

        # FIFO 关闭
        candidates.sort(key=lambda p: str(p.get("entry_time") or ""))
        for position in candidates:
            if remaining <= 0:
                break
            pos_size = abs(float(position.get("szi", 0)))
            if pos_size <= 0:
                continue

            close_size = min(pos_size, remaining)
            self._close_position(
                symbol=self.symbol,
                price=fill_price,
                reason="reduce_only 成交平仓",
                size=close_size,
                position_id=position.get("position_id"),
            )
            remaining -= close_size

    def _get_decision_timestamps(self, df: pd.DataFrame, interval_minutes: int) -> list[datetime]:
        """
        获取决策时间点列表

        Args:
            df: 数据DataFrame
            interval_minutes: 决策间隔（分钟）

        Returns:
            时间戳列表
        """
        if len(df) == 0:
            return []

        timestamps = []
        start_time = df.iloc[0]["timestamp"]
        end_time = df.iloc[-1]["timestamp"]

        current = start_time
        while current <= end_time:
            # 找到最接近的时间点
            time_diffs = (df["timestamp"] - current).abs()
            closest_idx = time_diffs.idxmin()
            closest_time = df.iloc[closest_idx]["timestamp"]

            if closest_time not in timestamps:
                timestamps.append(closest_time)

            current += timedelta(minutes=interval_minutes)

        return timestamps

    def _get_market_data_at_time(
        self, df: pd.DataFrame, timestamp: datetime
    ) -> dict[str, Any] | None:
        """
        获取指定时间点的市场数据

        Args:
            df: 数据DataFrame
            timestamp: 时间戳

        Returns:
            市场数据字典
        """
        # 找到最接近的时间点
        time_diffs = (df["timestamp"] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        row = df.iloc[closest_idx]

        # 获取技术指标
        indicators = TechnicalIndicators.get_latest_indicators(df.iloc[: closest_idx + 1])
        # 使用蜡烛的真实时间戳，而不是回测运行时的当前时间
        indicators["timestamp"] = row["timestamp"]
        return indicators

    def _check_tpsl_between_decisions(
        self, start_timestamp: datetime, end_timestamp: datetime, df: pd.DataFrame
    ):
        """
        在两个决策点之间的所有K线上检查止盈止损

        Args:
            start_timestamp: 起始时间（上一个决策点）
            end_timestamp: 结束时间（当前决策点）
            df: 数据DataFrame
        """
        # 获取两个决策点之间的所有K线数据
        mask = (df["timestamp"] >= start_timestamp) & (df["timestamp"] <= end_timestamp)
        between_candles = df[mask]

        if len(between_candles) == 0:
            return

        # 在循环开始前检查一次持仓状态
        positions = self.client.get_positions()
        has_position = any(p.get("coin") == self.symbol for p in positions)

        if not has_position:
            # 没有持仓，直接返回
            return

        # 遍历每个K线检查止盈止损
        for _idx, row in between_candles.iterrows():
            # 如果之前已经平仓，提前退出
            if not has_position:
                break

            # 设置当前时间到该K线的时间点
            candle_timestamp = row["timestamp"]
            self.client.set_current_time(candle_timestamp)

            # 检查止盈止损，并返回是否发生了平仓
            position_closed = self._check_take_profit_stop_loss(candle_timestamp, df)

            # 如果持仓被平掉，设置标志
            if position_closed:
                has_position = any(
                    p.get("coin") == self.symbol for p in self.client.get_positions()
                )
                if not has_position:
                    break

            if has_position:
                # 更新持仓盈亏
                self._update_positions_pnl(candle_timestamp, df)

    def _check_take_profit_stop_loss(self, timestamp: datetime, df: pd.DataFrame) -> bool:
        """
        检查止盈止损

        Args:
            timestamp: 当前时间
            df: 数据DataFrame

        Returns:
            bool: 是否发生了平仓
        """
        # 找到当前时间点的价格
        time_diffs = (df["timestamp"] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        current_price = df.iloc[closest_idx]["close"]
        high_price = df.iloc[closest_idx]["high"]
        low_price = df.iloc[closest_idx]["low"]

        position_closed = False
        positions = self.client.get_positions()
        for position in positions[:]:  # 使用切片复制，避免修改时出错
            symbol = position.get("coin")
            if symbol != self.symbol:
                continue

            is_long = position.get("is_long", True)
            tp_price = position.get("take_profit_price")
            sl_price = position.get("stop_loss_price")

            # 检查止盈止损
            should_close = False
            close_reason = ""

            if is_long:
                # 多仓：价格上涨触发止盈，价格下跌触发止损
                if tp_price and high_price >= tp_price:
                    should_close = True
                    close_reason = "止盈"
                elif sl_price and low_price <= sl_price:
                    should_close = True
                    close_reason = "止损"
            else:
                # 空仓：价格下跌触发止盈，价格上涨触发止损
                if tp_price and low_price <= tp_price:
                    should_close = True
                    close_reason = "止盈"
                elif sl_price and high_price >= sl_price:
                    should_close = True
                    close_reason = "止损"

            if should_close:
                # 平仓
                self._close_position(
                    symbol=symbol,
                    price=current_price,
                    reason=close_reason,
                    position_id=position.get("position_id"),
                )
                position_closed = True

        return position_closed

    def _close_position(
        self,
        symbol: str,
        price: float,
        reason: str = "手动平仓",
        size: float | None = None,
        position_id: int | None = None,
    ):
        """
        平仓

        Args:
            symbol: 交易对符号
            price: 平仓价格
            reason: 平仓原因
            size: 平仓数量（None=全平）
            position_id: 指定持仓ID（None=按symbol取第一笔）
        """
        positions = self.client.get_positions()
        if position_id is not None:
            position = next(
                (
                    p
                    for p in positions
                    if p.get("coin") == symbol and p.get("position_id") == position_id
                ),
                None,
            )
        else:
            position = next((p for p in positions if p.get("coin") == symbol), None)
        if not position:
            return

        entry_price = float(position.get("entryPx", 0))
        full_size = abs(float(position.get("szi", 0)))
        close_size = full_size if size is None else min(abs(float(size)), full_size)
        if close_size <= 0:
            return
        is_long = position.get("is_long", True)
        leverage_data = position.get("leverage", {})
        leverage = (
            leverage_data.get("value", 1) if isinstance(leverage_data, dict) else leverage_data
        )
        leverage = max(int(leverage), 1)

        # 计算盈亏
        if is_long:
            pnl = (price - entry_price) * close_size
        else:
            pnl = (entry_price - price) * close_size

        # 计算手续费
        entry_fee = entry_price * close_size * self.fee_rate
        exit_fee = price * close_size * self.fee_rate
        total_fee = entry_fee + exit_fee

        # 净盈亏
        net_pnl = pnl - total_fee

        # 获取当前时间戳
        current_timestamp = self.client.historical_data.iloc[self.client.current_index]["timestamp"]

        # 记录交易
        trade = {
            "symbol": symbol,
            "entry_time": position.get("entry_time", current_timestamp),
            "exit_time": current_timestamp,
            "entry_price": entry_price,
            "exit_price": price,
            "size": close_size,
            "leverage": leverage,
            "is_long": is_long,
            "pnl": pnl,
            "fee": total_fee,
            "net_pnl": net_pnl,
            "return_pct": (net_pnl / (entry_price * close_size / leverage)) * 100
            if leverage > 0
            else 0,
            "reason": reason,
        }

        self.closed_trades.append(trade)

        # 更新账户余额
        margin_used = entry_price * close_size / leverage
        new_account_value = self.client.account_value + net_pnl
        new_margin_used = max(0, self.client.total_margin_used - margin_used)

        self.client.update_account_value(new_account_value, new_margin_used)

        # 更新或移除持仓
        if close_size >= full_size - 1e-12:
            self.client.remove_position(symbol, position_id=position.get("position_id"))
            return

        remaining_size = full_size - close_size
        direction = 1 if float(position.get("szi", 0)) >= 0 else -1
        target_id = position.get("position_id")
        for open_pos in self.client.positions:
            if open_pos.get("coin") != symbol:
                continue
            if target_id is not None and open_pos.get("position_id") != target_id:
                continue
            open_pos["szi"] = str(direction * remaining_size)
            open_pos["positionValue"] = str(abs(remaining_size * entry_price))
            break

    def _update_positions_pnl(self, timestamp: datetime, df: pd.DataFrame):
        """
        更新持仓的未实现盈亏

        Args:
            timestamp: 当前时间
            df: 数据DataFrame
        """
        # 找到当前价格
        time_diffs = (df["timestamp"] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        current_price = df.iloc[closest_idx]["close"]

        positions = self.client.get_positions()
        for position in positions:
            symbol = position.get("coin")
            if symbol != self.symbol:
                continue

            entry_price = float(position.get("entryPx", 0))
            size = abs(float(position.get("szi", 0)))
            is_long = position.get("is_long", True)

            # 计算未实现盈亏
            if is_long:
                unrealized_pnl = (current_price - entry_price) * size
            else:
                unrealized_pnl = (entry_price - current_price) * size

            self.client.update_position_pnl(symbol, unrealized_pnl)

    def _update_account_value(self):
        """更新账户总价值"""
        balance = self.client.get_balance()
        if not balance:
            return

        # 计算未实现盈亏总和
        unrealized_pnl = 0
        positions = self.client.get_positions()
        for position in positions:
            unrealized_pnl += float(position.get("unrealizedPnl", 0))

        # 账户总价值 = 原始余额 + 已实现盈亏 + 未实现盈亏
        # 这里简化处理，使用当前账户价值
        total_pnl = sum(t.get("net_pnl", 0) for t in self.closed_trades)
        account_value = self.initial_balance + total_pnl + unrealized_pnl

        # 计算已用保证金
        margin_used = 0
        for position in positions:
            entry_price = float(position.get("entryPx", 0))
            size = abs(float(position.get("szi", 0)))
            leverage = position.get("leverage", {}).get("value", 1)
            margin_used += entry_price * size / leverage

        self.client.update_account_value(account_value, margin_used)

    def _run_review_agent(self):
        """运行复盘 Agent"""
        if not self.review_agent:
            return

        try:
            self.logger.print_section("🧠 运行复盘 Agent", style="bold white")

            # 获取最近的决策记录
            decision_count = self.decision_history.get_history_count(self.symbol)
            if decision_count < self.config.review_lookback_decisions:
                self.logger.print_info(
                    f"决策记录不足 ({decision_count} < {self.config.review_lookback_decisions})，跳过复盘"
                )
                return

            recent_decisions = self.decision_history.get_recent_decisions(
                self.symbol, self.config.review_lookback_decisions
            )

            if len(recent_decisions) < 3:
                self.logger.print_info("有效决策记录不足，跳过复盘")
                return

            # 获取已存在的经验
            existing_lessons = (
                self.review_memory_store.get_lessons(self.symbol)
                if self.review_memory_store
                else []
            )

            # 运行复盘
            review_result = self.review_agent.review(
                symbol=self.symbol,
                decision_records=recent_decisions,
                fills_summary=None,  # 回测中不提供成交汇总
                existing_lessons=existing_lessons,
            )

            # 保存经验到 memory store
            if self.review_memory_store and review_result.get("lessons"):
                accepted_lessons = self.review_memory_store.add_lessons(
                    symbol=self.symbol,
                    lessons=review_result.get("lessons", []),
                    min_confidence=self.config.review_min_confidence if self.config else 0.35,
                )
                if accepted_lessons:
                    self.logger.print_info(f"✅ 复盘完成，已保存 {len(accepted_lessons)} 条经验")
                else:
                    self.logger.print_info("✅ 复盘完成，但未生成符合条件的新经验")
            else:
                if self.review_memory_store:
                    self.review_memory_store.save()
                self.logger.print_info("✅ 复盘完成")

        except Exception as e:
            self.logger.print_warning(f"复盘 Agent 运行失败: {e}")
            import traceback

            traceback.print_exc()

    def _close_all_positions(self, timestamp: datetime, price: float):
        """
        平掉所有剩余持仓

        Args:
            timestamp: 时间戳
            price: 平仓价格
        """
        positions = self.client.get_positions()
        for position in positions[:]:
            symbol = position.get("coin")
            self._close_position(
                symbol=symbol,
                price=price,
                reason="回测结束平仓",
                position_id=position.get("position_id"),
            )

    def _generate_result(self) -> dict[str, Any]:
        """
        生成回测结果

        Returns:
            结果字典
        """
        # 计算统计指标
        total_trades = len(self.closed_trades)
        if total_trades == 0:
            return {
                "symbol": self.symbol,
                "strategy": self.strategy,
                "total_trades": 0,
                "profitable_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "total_fee": 0.0,
                "initial_balance": self.initial_balance,
                "final_balance": self.initial_balance,
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "avg_profit": 0.0,
                "avg_loss": 0.0,
                "trades": [],
            }

        # 盈利和亏损交易
        profitable_trades = [t for t in self.closed_trades if t["net_pnl"] > 0]
        losing_trades = [t for t in self.closed_trades if t["net_pnl"] < 0]

        # 胜率
        win_rate = len(profitable_trades) / total_trades if total_trades > 0 else 0

        # 盈亏比
        avg_profit = (
            sum(t["net_pnl"] for t in profitable_trades) / len(profitable_trades)
            if profitable_trades
            else 0
        )
        avg_loss = (
            abs(sum(t["net_pnl"] for t in losing_trades) / len(losing_trades))
            if losing_trades
            else 1
        )
        profit_factor = avg_profit / avg_loss if avg_loss > 0 else 0

        # 总盈亏
        total_pnl = sum(t["net_pnl"] for t in self.closed_trades)
        final_balance = self.initial_balance + total_pnl
        total_return = total_pnl / self.initial_balance

        # 最大回撤
        max_drawdown = self._calculate_max_drawdown()

        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "total_trades": total_trades,
            "profitable_trades": len(profitable_trades),
            "losing_trades": len(losing_trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "total_fee": sum(t["fee"] for t in self.closed_trades),
            "initial_balance": self.initial_balance,
            "final_balance": final_balance,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "avg_profit": avg_profit,
            "avg_loss": avg_loss,
            "trades": self.closed_trades,
        }

    def _calculate_max_drawdown(self) -> float:
        """
        计算最大回撤

        Returns:
            最大回撤百分比
        """
        if not self.closed_trades:
            return 0.0

        # 计算累计余额曲线
        balance = self.initial_balance
        balances = [balance]

        for trade in self.closed_trades:
            balance += trade["net_pnl"]
            balances.append(balance)

        # 计算最大回撤
        peak = balances[0]
        max_dd = 0.0

        for balance in balances:
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def _maybe_write_live_report(
        self,
        processed_decisions: int,
        total_decisions: int,
        force: bool = False,
        status: str = "running",
        base_result: dict[str, Any] | None = None,
    ):
        """根据设置刷新实时报告"""
        if not self.live_report_path:
            return

        if not force:
            if self._last_live_report_index >= 0:
                if (processed_decisions - self._last_live_report_index) < self.live_report_interval:
                    return

        snapshot = self._build_live_result_snapshot(
            processed_decisions=processed_decisions,
            total_decisions=total_decisions,
            status=status,
            base_result=base_result,
        )

        generator = BacktestReportGenerator(snapshot)
        generator.save_partial(file_path=str(self.live_report_path), quiet=not force)
        self._last_live_report_index = processed_decisions

    def _build_live_result_snapshot(
        self,
        processed_decisions: int,
        total_decisions: int,
        status: str,
        base_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建实时报告数据"""
        result = dict(base_result) if base_result else self._generate_result()
        result["symbol"] = self.symbol
        result["status"] = status
        result["progress"] = {
            "processed_decisions": processed_decisions,
            "total_decisions": total_decisions,
            "percentage": round((processed_decisions / total_decisions) * 100, 2)
            if total_decisions
            else 100.0,
        }
        result["open_positions"] = self._get_open_positions_snapshot()

        balance_info = self.order_manager.get_available_balance_info()
        if balance_info:
            result["current_balance"] = {
                "status": balance_info.get("status"),
                "total": balance_info.get("total"),
                "occupied": balance_info.get("occupied"),
                "available": balance_info.get("available"),
                "unrealized_pnl": balance_info.get("unrealized_pnl"),
                "message": balance_info.get("message"),
            }

        last_decision = self.decision_history.get_recent_decisions(self.symbol, 1)
        if last_decision:
            result["last_decision"] = last_decision[0]
            # 将更新时间对齐到最后一次决策对应的数据时间
            ts = last_decision[0].get("timestamp")
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            result["updated_at"] = ts or datetime.utcnow().isoformat()
        else:
            result["updated_at"] = datetime.utcnow().isoformat()
        return result

    def _get_open_positions_snapshot(self) -> list[dict[str, Any]]:
        """序列化当前持仓，便于实时报告展示"""
        positions_snapshot: list[dict[str, Any]] = []
        current_price = self.client.get_current_price(self.symbol)

        for position in self.client.get_positions():
            if position.get("coin") != self.symbol:
                continue

            entry_time = position.get("entry_time")
            if isinstance(entry_time, datetime):
                entry_time = entry_time.isoformat()

            size_value = self._safe_float(position.get("szi", 0.0))
            entry_price = self._safe_float(position.get("entryPx", 0.0))
            leverage = position.get("leverage", {}).get("value", 1)
            unrealized = self._safe_float(position.get("unrealizedPnl", 0.0))

            positions_snapshot.append(
                {
                    "symbol": position.get("coin"),
                    "size": size_value,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "leverage": leverage,
                    "is_long": position.get("is_long", True),
                    "unrealized_pnl": unrealized,
                    "take_profit_price": position.get("take_profit_price"),
                    "stop_loss_price": position.get("stop_loss_price"),
                    "entry_time": entry_time,
                }
            )

        return positions_snapshot

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """安全转换为float"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sanitize_action_details(details: dict[str, Any] | None) -> dict[str, Any]:
        """
        精简决策详情，去掉对话/事件等大对象，避免回测JSON过大
        """
        if not isinstance(details, dict):
            return {}

        clean = details.copy()
        # 去掉流式事件等对话信息
        clean.pop("events", None)

        # 控制输出长度，防止长文本
        output = clean.get("output")
        if isinstance(output, str) and len(output) > 800:
            clean["output"] = output[:800] + "...[truncated]"

        return clean

    def _enrich_market_data_for_backtest(
        self,
        df: pd.DataFrame,
        timestamp: datetime,
        market_data: dict[str, Any],
        balance_info: dict[str, float] | None = None,
        precomputed_4h: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        """
        为回测环境生成增强的市场数据（用于nof1策略）

        Args:
            df: 15分钟K线数据（已计算指标）
            timestamp: 当前时间戳
            market_data: 基础市场数据
            balance_info: 账户余额信息

        Returns:
            增强后的市场数据字典
        """
        enriched = {}

        # 1. 计算程序运行时长（从回测开始时间计算）
        elapsed = timestamp - self.historical_data.iloc[0]["timestamp"]
        enriched["elapsed_minutes"] = int(elapsed.total_seconds() / 60)

        # 2. 找到当前时间点的数据索引
        time_diffs = (df["timestamp"] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        current_data = df.iloc[: closest_idx + 1]

        # 3. 提取历史序列数据（最近10个数据点）
        period = min(10, len(current_data))
        if period > 0:
            recent_df = current_data.tail(period)

            # 中间价格序列
            enriched["mid_prices_raw"] = recent_df["close"].tolist()
            enriched["mid_prices"] = [f"{p:.2f}" for p in enriched["mid_prices_raw"]]

            # EMA(20)序列
            if "ema_20" in recent_df.columns:
                ema_values = recent_df["ema_20"].fillna(recent_df["close"]).tolist()
                enriched["ema_indicators_raw"] = ema_values
                enriched["ema_indicators"] = [f"{v:.2f}" for v in ema_values]
            else:
                enriched["ema_indicators_raw"] = recent_df["close"].tolist()
                enriched["ema_indicators"] = enriched["mid_prices"]

            # MACD序列
            if "macd" in recent_df.columns:
                macd_values = recent_df["macd"].fillna(0).tolist()
                enriched["macd_indicators_raw"] = macd_values
                enriched["macd_indicators"] = [f"{v:.4f}" for v in macd_values]
            else:
                enriched["macd_indicators_raw"] = [0.0] * period
                enriched["macd_indicators"] = ["0.0000"] * period

            # RSI序列（使用相同的rsi列）
            if "rsi" in recent_df.columns:
                rsi_values = recent_df["rsi"].fillna(50).tolist()
                enriched["rsi_7_indicators_raw"] = rsi_values
                enriched["rsi_7_indicators"] = [f"{v:.2f}" for v in rsi_values]
                enriched["rsi_14_indicators_raw"] = rsi_values
                enriched["rsi_14_indicators"] = [f"{v:.2f}" for v in rsi_values]
            else:
                enriched["rsi_7_indicators_raw"] = [50.0] * period
                enriched["rsi_7_indicators"] = ["50.00"] * period
                enriched["rsi_14_indicators_raw"] = [50.0] * period
                enriched["rsi_14_indicators"] = ["50.00"] * period
        else:
            # 没有足够数据，提供空序列
            enriched.update(
                {
                    "mid_prices": [],
                    "mid_prices_raw": [],
                    "ema_indicators": [],
                    "ema_indicators_raw": [],
                    "macd_indicators": [],
                    "macd_indicators_raw": [],
                    "rsi_7_indicators": [],
                    "rsi_7_indicators_raw": [],
                    "rsi_14_indicators": [],
                    "rsi_14_indicators_raw": [],
                }
            )

        # 4. 添加当前时刻指标
        current_price = market_data.get("current_price", 0)
        enriched["current_ema20"] = market_data.get("ema_20", current_price)
        enriched["current_rsi"] = market_data.get("rsi", 50)
        enriched["current_macd"] = market_data.get("macd", 0)

        # 5. 生成4小时K线数据
        if precomputed_4h is not None and not precomputed_4h.empty:
            df_4h = precomputed_4h[precomputed_4h["timestamp"] <= timestamp].copy()
        else:
            # 回退路径：未传入预计算数据时，按当前窗口生成
            df_4h = self._prepare_4h_dataframe(df.iloc[: closest_idx + 1])

        if df_4h is not None and not df_4h.empty:
            # 提取4小时数据
            latest_4h = df_4h.iloc[-1]
            recent_4h = df_4h.tail(10)

            enriched["ema_20_4h"] = latest_4h.get("ema_20", latest_4h["close"])
            enriched["ema_50_4h"] = latest_4h.get("ema_50", latest_4h["close"])
            enriched["atr_3_4h"] = latest_4h.get("atr_3", 0)
            enriched["atr_14_4h"] = latest_4h.get("atr_14", 0)
            enriched["current_volume"] = latest_4h["volume"]
            enriched["avg_volume"] = df_4h["volume"].mean()

            # MACD和RSI序列
            if "macd" in recent_4h.columns:
                enriched["macd_4h_indicators"] = recent_4h["macd"].fillna(0).tolist()
            else:
                enriched["macd_4h_indicators"] = [0.0] * 10

            if "rsi" in recent_4h.columns:
                enriched["rsi_14_4h_indicators"] = recent_4h["rsi"].fillna(50).tolist()
            else:
                enriched["rsi_14_4h_indicators"] = [50.0] * 10
        else:
            # 没有4小时数据，提供默认值
            enriched.update(
                {
                    "ema_20_4h": current_price,
                    "ema_50_4h": current_price,
                    "atr_3_4h": 0,
                    "atr_14_4h": 0,
                    "current_volume": 0,
                    "avg_volume": 0,
                    "macd_4h_indicators": [0.0] * 10,
                    "rsi_14_4h_indicators": [50.0] * 10,
                }
            )
            df_4h = None

        # 6. 添加持仓量和资金费率（回测中设为0）
        enriched["oi_latest"] = 0
        enriched["oi_average"] = 0
        enriched["funding_rate"] = 0

        # 7. 生成指标分析文本
        analysis = self._analyze_indicators_for_backtest(enriched, current_data, df_4h)
        enriched.update(analysis)

        # 8. 添加账户数据
        if balance_info:
            total = balance_info.get("total", self.initial_balance)
            available = balance_info.get("available", 0)

            if self.initial_balance > 0:
                total_return_pct = ((total - self.initial_balance) / self.initial_balance) * 100
            else:
                total_return_pct = 0.0

            enriched.update(
                {
                    "total_return_pct": total_return_pct,
                    "available_cash": available,
                    "account_value": total,
                    "sharpe_ratio": 0,  # 回测中暂不计算
                }
            )
        else:
            enriched.update(
                {
                    "total_return_pct": 0,
                    "available_cash": 0,
                    "account_value": self.initial_balance,
                    "sharpe_ratio": 0,
                }
            )

        # 9. 格式化数据为模板友好的字符串格式
        enriched = self._format_enriched_data(enriched)

        return enriched

    def _generate_4h_dataframe(self, df_15m: pd.DataFrame) -> pd.DataFrame | None:
        """
        从15分钟K线数据生成4小时K线数据

        Args:
            df_15m: 15分钟K线数据

        Returns:
            4小时K线DataFrame
        """
        if df_15m.empty:
            return None

        # 按4小时分组
        df_15m = df_15m.copy()
        df_15m["timestamp_4h"] = df_15m["timestamp"].dt.floor("4H")

        # 聚合为4小时K线
        df_4h = (
            df_15m.groupby("timestamp_4h")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .reset_index()
        )

        df_4h.rename(columns={"timestamp_4h": "timestamp"}, inplace=True)

        return df_4h

    def _prepare_4h_dataframe(self, df_15m: pd.DataFrame) -> pd.DataFrame | None:
        """生成并计算 4h 指标，供回测循环复用。"""
        df_4h = self._generate_4h_dataframe(df_15m)
        if df_4h is None or df_4h.empty:
            return None

        if self.config:
            return TechnicalIndicators.calculate_all_indicators(
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

        return TechnicalIndicators.calculate_all_indicators(
            df_4h, ema_periods=[20, 50], atr_periods=[3, 14]
        )

    def _analyze_indicators_for_backtest(
        self, enriched: dict[str, Any], df_15m: pd.DataFrame, df_4h: pd.DataFrame | None = None
    ) -> dict[str, str]:
        """
        分析指标数据，生成文本性结论（回测版本）
        """
        analysis = {}

        def t(key: str, **kwargs: Any) -> str:
            return get_text(self.language, key, **kwargs)

        # 1. 分析价格趋势
        if "mid_prices_raw" in enriched and len(enriched["mid_prices_raw"]) >= 5:
            prices = enriched["mid_prices_raw"]
            if prices[0] != 0:
                price_change_pct = ((prices[-1] - prices[0]) / prices[0]) * 100
                if price_change_pct > 1.0:
                    trend = f"{t('trend_rising')}(+{price_change_pct:.2f}%)"
                elif price_change_pct < -1.0:
                    trend = f"{t('trend_falling')}({price_change_pct:.2f}%)"
                else:
                    trend = f"{t('trend_sideways')}({price_change_pct:.2f}%)"
                analysis["price_trend_analysis"] = trend
            else:
                analysis["price_trend_analysis"] = t("price_data_error_zero")
        else:
            analysis["price_trend_analysis"] = f"{t('trend_sideways')}(0.00%)"

        # 2. 分析MACD
        current_macd = enriched.get("current_macd", 0)
        macd_signal = df_15m.iloc[-1].get("macd_signal", 0) if not df_15m.empty else 0

        if current_macd > macd_signal and current_macd > 0:
            macd_status = t("macd_golden_cross_above_zero")
        elif current_macd > macd_signal and current_macd <= 0:
            macd_status = t("macd_golden_cross_below_zero")
        elif current_macd < macd_signal and current_macd < 0:
            macd_status = t("macd_death_cross_below_zero")
        elif current_macd < macd_signal and current_macd >= 0:
            macd_status = t("macd_death_cross_above_zero")
        else:
            macd_status = f"MACD={current_macd:.4f}"

        analysis["macd_analysis"] = macd_status

        # 3. 分析RSI
        current_rsi = enriched.get("current_rsi", 50)
        if current_rsi >= 70:
            rsi_status = f"{t('rsi_overbought')}({current_rsi:.1f})"
        elif current_rsi <= 30:
            rsi_status = f"{t('rsi_oversold')}({current_rsi:.1f})"
        elif current_rsi >= 60:
            rsi_status = f"{t('rsi_strong')}({current_rsi:.1f})"
        elif current_rsi <= 40:
            rsi_status = f"{t('rsi_weak')}({current_rsi:.1f})"
        else:
            rsi_status = f"{t('rsi_neutral')}({current_rsi:.1f})"

        analysis["rsi_analysis"] = rsi_status

        # 4. 分析EMA关系
        current_price = df_15m.iloc[-1]["close"] if not df_15m.empty else 0
        current_ema20 = enriched.get("current_ema20", current_price)

        if current_ema20 != 0 and current_price > 0:
            if current_price > current_ema20 * 1.01:
                ema_status = (
                    f"{t('price_above_ema20')}({((current_price / current_ema20 - 1) * 100):.2f}%)"
                )
            elif current_price < current_ema20 * 0.99:
                ema_status = (
                    f"{t('price_below_ema20')}({((current_price / current_ema20 - 1) * 100):.2f}%)"
                )
            else:
                ema_status = t("price_near_ema20")
        else:
            ema_status = t("ema_data_error")

        analysis["ema_analysis"] = ema_status

        # 5. 分析成交量
        if not df_15m.empty and "volume" in df_15m.columns:
            current_volume = df_15m.iloc[-1]["volume"]
            avg_volume = df_15m["volume"].tail(20).mean()

            if avg_volume != 0:
                times_unit = t("times_unit")
                if current_volume > avg_volume * 1.5:
                    volume_status = (
                        f"{t('volume_surge')}({(current_volume / avg_volume):.1f}{times_unit})"
                    )
                elif current_volume > avg_volume * 1.2:
                    volume_status = (
                        f"{t('volume_increase')}({(current_volume / avg_volume):.1f}{times_unit})"
                    )
                elif current_volume < avg_volume * 0.5:
                    volume_status = (
                        f"{t('volume_decline')}({(current_volume / avg_volume):.1f}{times_unit})"
                    )
                else:
                    volume_status = (
                        f"{t('volume_normal')}({(current_volume / avg_volume):.1f}{times_unit})"
                    )
            else:
                volume_status = t("volume_data_error")

            analysis["volume_analysis"] = volume_status
        else:
            analysis["volume_analysis"] = f"{t('volume_normal')}(1.0{t('times_unit')})"

        # 6. 分析4小时趋势
        if df_4h is not None and not df_4h.empty:
            h4_price = df_4h.iloc[-1]["close"]
            h4_ema20 = enriched.get("ema_20_4h", h4_price)
            h4_ema50 = enriched.get("ema_50_4h", h4_price)

            if h4_price > h4_ema20 and h4_ema20 > h4_ema50:
                h4_trend = t("h4_bullish_alignment")
            elif h4_price < h4_ema20 and h4_ema20 < h4_ema50:
                h4_trend = t("h4_bearish_alignment")
            elif h4_price > h4_ema20:
                h4_trend = t("h4_bullish")
            elif h4_price < h4_ema20:
                h4_trend = t("h4_bearish")
            else:
                h4_trend = t("h4_ranging")

            analysis["h4_trend_analysis"] = h4_trend
        else:
            analysis["h4_trend_analysis"] = t("h4_ranging")

        # 7. 综合分析
        signals = []
        current_macd = enriched.get("current_macd", 0)
        macd_signal = df_15m.iloc[-1].get("macd_signal", 0) if not df_15m.empty else 0

        if current_macd > macd_signal:
            signals.append(t("signal_macd_bullish"))
        elif current_macd < macd_signal:
            signals.append(t("signal_macd_bearish"))

        if current_rsi >= 70:
            signals.append(t("signal_rsi_overbought"))
        elif current_rsi <= 30:
            signals.append(t("signal_rsi_oversold"))

        if current_price > current_ema20:
            signals.append(t("signal_price_above_ema"))
        else:
            signals.append(t("signal_price_below_ema"))

        analysis["composite_signal"] = ", ".join(signals) if signals else t("signal_none")

        return analysis

    def _format_enriched_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """格式化数据为模板友好的字符串格式"""
        formatted = data.copy()

        # 格式化序列为逗号分隔字符串
        list_fields = [
            "mid_prices",
            "ema_indicators",
            "macd_indicators",
            "rsi_7_indicators",
            "rsi_14_indicators",
            "macd_4h_indicators",
            "rsi_14_4h_indicators",
        ]

        for field in list_fields:
            if field in formatted and isinstance(formatted[field], list):
                formatted[field] = ", ".join(map(str, formatted[field]))

        return formatted
