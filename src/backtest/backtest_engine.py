"""
回测引擎核心模块
负责时间推进、交易执行、盈亏跟踪等核心逻辑
"""

import pandas as pd
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path

from .mock_client import MockHyperliquidClient
from .mock_order_manager import MockOrderManager
from .report_generator import BacktestReportGenerator
from src.data.indicators import TechnicalIndicators
from src.agent.single_symbol_agent import SingleSymbolAgent
from src.agent.summary_agent_v2 import SummaryAgentV2, DecisionHistory
from src.utils.logger import TradingLogger
from src.prompt_manager import PromptManager
from src.config import FEE_RATE_PER_SIDE


class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        symbol: str,
        historical_data: pd.DataFrame,
        initial_balance: float = 10000.0,
        config: Any = None,
        logger: Optional[TradingLogger] = None,
        prompt_manager: Optional[PromptManager] = None
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
        """
        self.symbol = symbol
        self.historical_data = historical_data.copy()
        self.historical_data = self.historical_data.sort_values('timestamp').reset_index(drop=True)
        self.initial_balance = initial_balance
        self.config = config
        self.logger = logger or TradingLogger()
        self.prompt_manager = prompt_manager

        # 初始化模拟客户端和订单管理器
        self.client = MockHyperliquidClient(historical_data, initial_balance)
        self.order_manager = MockOrderManager(
            client=self.client,
            take_profit_ratio=config.take_profit_ratio if config else 0.05,
            stop_loss_ratio=config.stop_loss_ratio if config else 0.02,
            default_leverage=config.max_leverage if config else 10
        )

        # 初始化Agent（需要配置信息）
        if config:
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
                prompt_manager=prompt_manager
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
                max_context_tokens=2000
            )
        else:
            self.summary_agent = None

        # 交易记录
        self.trades: List[Dict[str, Any]] = []
        self.closed_trades: List[Dict[str, Any]] = []

        # 手续费率
        self.fee_rate = FEE_RATE_PER_SIDE

        # 实时报告
        self.live_report_path: Optional[Path] = None
        self.live_report_interval: int = 1
        self._last_live_report_index: int = -1
        self._total_decision_points: int = 0

        print(f"✅ 回测引擎初始化完成")
        print(f"   交易对: {symbol}")
        print(f"   初始余额: ${initial_balance:.2f}")
        print(f"   数据点数: {len(historical_data)}")

    def run(
        self,
        decision_interval_minutes: int = 15,
        live_report_path: Optional[str] = None,
        live_report_interval: int = 1
    ) -> Dict[str, Any]:
        """
        运行回测
        
        Args:
            decision_interval_minutes: 决策间隔（分钟）
            
        Returns:
            回测结果字典
        """
        print(f"\n🚀 开始回测...")
        print(f"   决策间隔: {decision_interval_minutes} 分钟")

        if not self.agent:
            raise ValueError("Agent未初始化，无法运行回测")

        # 配置实时报告
        if live_report_path:
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
                    'fast': self.config.macd_fast,
                    'slow': self.config.macd_slow,
                    'signal': self.config.macd_signal
                },
                bollinger_params={
                    'period': self.config.bollinger_period,
                    'std_dev': self.config.bollinger_std
                }
            )
        else:
            df = TechnicalIndicators.calculate_all_indicators(
                self.historical_data.copy()
            )

        # 按决策间隔选择时间点
        decision_timestamps = self._get_decision_timestamps(df, decision_interval_minutes)
        self._total_decision_points = len(decision_timestamps)

        print(f"   决策点数量: {len(decision_timestamps)}")
        self._maybe_write_live_report(
            processed_decisions=0,
            total_decisions=self._total_decision_points,
            force=True,
            status="initializing"
        )

        # 遍历每个决策点
        for i, timestamp in enumerate(decision_timestamps):
            try:
                # 设置当前时间
                self.client.set_current_time(timestamp)

                # 获取当前市场数据
                market_data = self._get_market_data_at_time(df, timestamp)
                if not market_data:
                    continue

                # 获取当前持仓和余额
                current_positions = self.order_manager.get_current_positions()
                balance_info = self.order_manager.get_available_balance_info()

                # 检查止盈止损
                self._check_take_profit_stop_loss(timestamp, df)

                # 调整交易金额
                suggestion = self.order_manager.calculate_suggested_trade_amount(
                    desired_amount=self.config.max_trade_amount if self.config else 100.0,
                    min_trade_amount=10.0,
                    balance_info=balance_info
                )

                if not suggestion['can_trade'] and len(current_positions) == 0:
                    # 无持仓且余额不足，跳过
                    continue

                adjusted_amount = suggestion['suggested_amount'] if suggestion['can_trade'] else 0
                self.agent.trade_amount = adjusted_amount

                # 生成历史汇总
                historical_summary = None
                history_count = self.decision_history.get_history_count(self.symbol)

                if history_count >= 20 and self.summary_agent:
                    recent_10 = self.decision_history.get_recent_decisions(self.symbol, 10)
                    recent_10_20 = self.decision_history.get_decisions_range(self.symbol, 10, 20)
                    historical_summary = self.summary_agent.create_compressed_summary(
                        symbol=self.symbol,
                        recent_records=recent_10,
                        older_records=recent_10_20
                    )
                elif history_count >= 10 and self.summary_agent:
                    recent = self.decision_history.get_recent_decisions(self.symbol, 10)
                    historical_summary = self.summary_agent.create_compressed_summary(
                        symbol=self.symbol,
                        recent_records=recent,
                        older_records=None
                    )

                # 获取多周期趋势（简化版本，回测中只使用当前周期）
                multi_timeframe_trends = {'15m': 'NEUTRAL'}  # 简化处理

                # 调用Agent做出决策
                decision, details = self.agent.make_decision(
                    market_data=market_data,
                    multi_timeframe_trends=multi_timeframe_trends,
                    current_positions=current_positions,
                    max_positions=self.config.max_positions if self.config else 2,
                    historical_summary=historical_summary,
                    enriched_data={}
                )

                # 记录决策历史
                self.decision_history.add_decision(
                    symbol=self.symbol,
                    decision=decision,
                    market_data=market_data,
                    reason=details.get('output', '')[:200],
                    action_details=details
                )

                # 执行决策（Agent的工具回调会自动调用order_manager）
                # 对于平仓决策，需要检测持仓变化并记录交易
                positions_after_decision = self.order_manager.get_current_positions()
                
                # 检查是否有持仓被平掉
                for pos_before in current_positions:
                    if pos_before.get('coin') == self.symbol:
                        # 检查这个持仓是否还存在
                        pos_after = next((p for p in positions_after_decision if p.get('coin') == self.symbol), None)
                        if not pos_after:
                            # 持仓被平掉，记录交易
                            current_price = self.client.get_current_price(self.symbol)
                            if current_price:
                                self._close_position(self.symbol, current_price, f"Agent决策: {decision}")

                # 更新持仓盈亏
                self._update_positions_pnl(timestamp, df)

                # 更新账户价值
                self._update_account_value()
                self._maybe_write_live_report(
                    processed_decisions=i + 1,
                    total_decisions=self._total_decision_points
                )

            except Exception as e:
                print(f"⚠️ 决策点 {i+1}/{len(decision_timestamps)} 处理失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        # 平掉所有剩余持仓
        self._close_all_positions(df.iloc[-1]['timestamp'], df.iloc[-1]['close'])

        # 生成回测结果
        result = self._generate_result()
        result['status'] = 'completed'

        print(f"\n✅ 回测完成")
        print(f"   总交易数: {len(self.closed_trades)}")
        print(f"   最终余额: ${result['final_balance']:.2f}")
        print(f"   总收益率: {result['total_return']*100:.2f}%")

        self._maybe_write_live_report(
            processed_decisions=self._total_decision_points,
            total_decisions=self._total_decision_points,
            force=True,
            status="completed",
            base_result=result
        )

        return result

    def _get_decision_timestamps(
        self,
        df: pd.DataFrame,
        interval_minutes: int
    ) -> List[datetime]:
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
        start_time = df.iloc[0]['timestamp']
        end_time = df.iloc[-1]['timestamp']

        current = start_time
        while current <= end_time:
            # 找到最接近的时间点
            time_diffs = (df['timestamp'] - current).abs()
            closest_idx = time_diffs.idxmin()
            closest_time = df.iloc[closest_idx]['timestamp']

            if closest_time not in timestamps:
                timestamps.append(closest_time)

            current += timedelta(minutes=interval_minutes)

        return timestamps

    def _get_market_data_at_time(
        self,
        df: pd.DataFrame,
        timestamp: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        获取指定时间点的市场数据
        
        Args:
            df: 数据DataFrame
            timestamp: 时间戳
            
        Returns:
            市场数据字典
        """
        # 找到最接近的时间点
        time_diffs = (df['timestamp'] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        row = df.iloc[closest_idx]

        # 获取技术指标
        return TechnicalIndicators.get_latest_indicators(df.iloc[:closest_idx+1])

    def _check_take_profit_stop_loss(
        self,
        timestamp: datetime,
        df: pd.DataFrame
    ):
        """
        检查止盈止损
        
        Args:
            timestamp: 当前时间
            df: 数据DataFrame
        """
        # 找到当前时间点的价格
        time_diffs = (df['timestamp'] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        current_price = df.iloc[closest_idx]['close']
        high_price = df.iloc[closest_idx]['high']
        low_price = df.iloc[closest_idx]['low']

        positions = self.client.get_positions()
        for position in positions[:]:  # 使用切片复制，避免修改时出错
            symbol = position.get('coin')
            if symbol != self.symbol:
                continue

            is_long = position.get('is_long', True)
            tp_price = position.get('take_profit_price')
            sl_price = position.get('stop_loss_price')

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
                self._close_position(symbol, current_price, close_reason)

    def _close_position(
        self,
        symbol: str,
        price: float,
        reason: str = "手动平仓"
    ):
        """
        平仓
        
        Args:
            symbol: 交易对符号
            price: 平仓价格
            reason: 平仓原因
        """
        positions = self.client.get_positions()
        position = next((p for p in positions if p.get('coin') == symbol), None)
        if not position:
            return

        entry_price = float(position.get('entryPx', 0))
        size = abs(float(position.get('szi', 0)))
        is_long = position.get('is_long', True)
        leverage = position.get('leverage', {}).get('value', 1)

        # 计算盈亏
        if is_long:
            pnl = (price - entry_price) * size
        else:
            pnl = (entry_price - price) * size

        # 计算手续费
        entry_fee = entry_price * size * self.fee_rate
        exit_fee = price * size * self.fee_rate
        total_fee = entry_fee + exit_fee

        # 净盈亏
        net_pnl = pnl - total_fee

        # 获取当前时间戳
        current_timestamp = self.client.historical_data.iloc[self.client.current_index]['timestamp']
        
        # 记录交易
        trade = {
            'symbol': symbol,
            'entry_time': position.get('entry_time', current_timestamp),
            'exit_time': current_timestamp,
            'entry_price': entry_price,
            'exit_price': price,
            'size': size,
            'leverage': leverage,
            'is_long': is_long,
            'pnl': pnl,
            'fee': total_fee,
            'net_pnl': net_pnl,
            'return_pct': (net_pnl / (entry_price * size / leverage)) * 100 if leverage > 0 else 0,
            'reason': reason
        }

        self.closed_trades.append(trade)

        # 更新账户余额
        margin_used = entry_price * size / leverage
        new_account_value = self.client.account_value + net_pnl
        new_margin_used = max(0, self.client.total_margin_used - margin_used)
        
        self.client.update_account_value(new_account_value, new_margin_used)

        # 移除持仓
        self.client.remove_position(symbol)

    def _update_positions_pnl(self, timestamp: datetime, df: pd.DataFrame):
        """
        更新持仓的未实现盈亏
        
        Args:
            timestamp: 当前时间
            df: 数据DataFrame
        """
        # 找到当前价格
        time_diffs = (df['timestamp'] - timestamp).abs()
        closest_idx = time_diffs.idxmin()
        current_price = df.iloc[closest_idx]['close']

        positions = self.client.get_positions()
        for position in positions:
            symbol = position.get('coin')
            if symbol != self.symbol:
                continue

            entry_price = float(position.get('entryPx', 0))
            size = abs(float(position.get('szi', 0)))
            is_long = position.get('is_long', True)

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
            unrealized_pnl += float(position.get('unrealizedPnl', 0))

        # 账户总价值 = 原始余额 + 已实现盈亏 + 未实现盈亏
        # 这里简化处理，使用当前账户价值
        total_pnl = sum(t.get('net_pnl', 0) for t in self.closed_trades)
        account_value = self.initial_balance + total_pnl + unrealized_pnl

        # 计算已用保证金
        margin_used = 0
        for position in positions:
            entry_price = float(position.get('entryPx', 0))
            size = abs(float(position.get('szi', 0)))
            leverage = position.get('leverage', {}).get('value', 1)
            margin_used += entry_price * size / leverage

        self.client.update_account_value(account_value, margin_used)

    def _close_all_positions(self, timestamp: datetime, price: float):
        """
        平掉所有剩余持仓
        
        Args:
            timestamp: 时间戳
            price: 平仓价格
        """
        positions = self.client.get_positions()
        for position in positions[:]:
            symbol = position.get('coin')
            self._close_position(symbol, price, "回测结束平仓")

    def _generate_result(self) -> Dict[str, Any]:
        """
        生成回测结果
        
        Returns:
            结果字典
        """
        # 计算统计指标
        total_trades = len(self.closed_trades)
        if total_trades == 0:
            return {
                'symbol': self.symbol,
                'total_trades': 0,
                'profitable_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'total_pnl': 0.0,
                'total_fee': 0.0,
                'initial_balance': self.initial_balance,
                'final_balance': self.initial_balance,
                'total_return': 0.0,
                'max_drawdown': 0.0,
                'avg_profit': 0.0,
                'avg_loss': 0.0,
                'trades': []
            }

        # 盈利和亏损交易
        profitable_trades = [t for t in self.closed_trades if t['net_pnl'] > 0]
        losing_trades = [t for t in self.closed_trades if t['net_pnl'] < 0]

        # 胜率
        win_rate = len(profitable_trades) / total_trades if total_trades > 0 else 0

        # 盈亏比
        avg_profit = sum(t['net_pnl'] for t in profitable_trades) / len(profitable_trades) if profitable_trades else 0
        avg_loss = abs(sum(t['net_pnl'] for t in losing_trades) / len(losing_trades)) if losing_trades else 1
        profit_factor = avg_profit / avg_loss if avg_loss > 0 else 0

        # 总盈亏
        total_pnl = sum(t['net_pnl'] for t in self.closed_trades)
        final_balance = self.initial_balance + total_pnl
        total_return = total_pnl / self.initial_balance

        # 最大回撤
        max_drawdown = self._calculate_max_drawdown()

        return {
            'symbol': self.symbol,
            'total_trades': total_trades,
            'profitable_trades': len(profitable_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'total_pnl': total_pnl,
            'total_fee': sum(t['fee'] for t in self.closed_trades),
            'initial_balance': self.initial_balance,
            'final_balance': final_balance,
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'trades': self.closed_trades
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
            balance += trade['net_pnl']
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
        base_result: Optional[Dict[str, Any]] = None
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
            base_result=base_result
        )

        generator = BacktestReportGenerator(snapshot)
        generator.save_partial(
            file_path=str(self.live_report_path),
            quiet=not force
        )
        self._last_live_report_index = processed_decisions

    def _build_live_result_snapshot(
        self,
        processed_decisions: int,
        total_decisions: int,
        status: str,
        base_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """构建实时报告数据"""
        result = dict(base_result) if base_result else self._generate_result()
        result['symbol'] = self.symbol
        result['status'] = status
        result['progress'] = {
            'processed_decisions': processed_decisions,
            'total_decisions': total_decisions,
            'percentage': round((processed_decisions / total_decisions) * 100, 2) if total_decisions else 100.0
        }
        result['open_positions'] = self._get_open_positions_snapshot()

        balance_info = self.order_manager.get_available_balance_info()
        if balance_info:
            result['current_balance'] = {
                'status': balance_info.get('status'),
                'total': balance_info.get('total'),
                'occupied': balance_info.get('occupied'),
                'available': balance_info.get('available'),
                'unrealized_pnl': balance_info.get('unrealized_pnl'),
                'message': balance_info.get('message')
            }

        last_decision = self.decision_history.get_recent_decisions(self.symbol, 1)
        if last_decision:
            result['last_decision'] = last_decision[0]

        result['updated_at'] = datetime.utcnow().isoformat()
        return result

    def _get_open_positions_snapshot(self) -> List[Dict[str, Any]]:
        """序列化当前持仓，便于实时报告展示"""
        positions_snapshot: List[Dict[str, Any]] = []
        current_price = self.client.get_current_price(self.symbol)

        for position in self.client.get_positions():
            if position.get('coin') != self.symbol:
                continue

            entry_time = position.get('entry_time')
            if isinstance(entry_time, datetime):
                entry_time = entry_time.isoformat()

            size_value = self._safe_float(position.get('szi', 0.0))
            entry_price = self._safe_float(position.get('entryPx', 0.0))
            leverage = position.get('leverage', {}).get('value', 1)
            unrealized = self._safe_float(position.get('unrealizedPnl', 0.0))

            positions_snapshot.append({
                'symbol': position.get('coin'),
                'size': size_value,
                'entry_price': entry_price,
                'current_price': current_price,
                'leverage': leverage,
                'is_long': position.get('is_long', True),
                'unrealized_pnl': unrealized,
                'take_profit_price': position.get('take_profit_price'),
                'stop_loss_price': position.get('stop_loss_price'),
                'entry_time': entry_time
            })

        return positions_snapshot

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """安全转换为float"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
