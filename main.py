#!/usr/bin/env python3
"""
Quant Flow - AI 驱动的加密货币自动交易机器人

主程序入口，负责初始化所有组件并启动调度器
"""

import sys
import time
import signal
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import get_config
from src.utils.logger import get_logger
from src.data.market_data import MarketDataFetcher
from src.data.indicators import TechnicalIndicators
from src.trading.bitget_client import BitgetClient
from src.trading.order_manager import OrderManager
from src.agent.trading_agent import TradingAgent


class QuantFlowBot:
    """Quant Flow 交易机器人主类"""

    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化机器人

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        self.config = get_config(config_path)

        # 初始化日志
        self.logger = get_logger(
            log_level=self.config.log_level,
            console_color=self.config.console_color,
            decision_log_format=self.config.decision_log_format
        )

        # 打印启动信息
        self._print_startup_banner()

        # 初始化组件
        self._initialize_components()

        # 调度器
        self.scheduler = None
        self.is_running = False

    def _print_startup_banner(self):
        """打印启动横幅"""
        banner = """
        ╔═══════════════════════════════════════════════════════════╗
        ║                                                           ║
        ║               🤖 Quant Flow Trading Bot 🤖               ║
        ║                                                           ║
        ║          AI-Powered Cryptocurrency Trading System         ║
        ║                                                           ║
        ╚═══════════════════════════════════════════════════════════╝
        """
        self.logger.console.print(banner, style="bold cyan")
        self.logger.console.print(self.config, style="cyan")

    def _initialize_components(self):
        """初始化所有组件"""
        self.logger.print_section("🔧 初始化组件", style="bold yellow")

        # 1. 市场数据获取器
        self.logger.print_info("初始化市场数据获取器...")
        self.market_fetcher = MarketDataFetcher(
            exchange_id="bitget",
            api_key=self.config.bitget_api_key,
            api_secret=self.config.bitget_api_secret,
            password=self.config.bitget_passphrase,
            demo_trading=self.config.demo_trading
        )

        # 2. Bitget 交易客户端（现货）
        self.logger.print_info("初始化 Bitget 交易客户端...")
        self.bitget_client = BitgetClient(
            api_key=self.config.bitget_api_key or "test",
            api_secret=self.config.bitget_api_secret or "test",
            passphrase=self.config.bitget_passphrase or "test",
            demo_trading=self.config.demo_trading
        )

        # 2.5. Bitget 合约客户端（用于做空）
        self.logger.print_info("初始化 Bitget 合约客户端（用于做空）...")
        from src.trading.bitget_contract_client import BitgetContractClient
        self.contract_client = BitgetContractClient(
            api_key=self.config.bitget_api_key or "test",
            api_secret=self.config.bitget_api_secret or "test",
            passphrase=self.config.bitget_passphrase or "test",
            demo_trading=self.config.demo_trading,
            product_type="USDT-FUTURES"
        )

        # 3. 订单管理器（默认使用合约做空）
        self.logger.print_info("初始化订单管理器...")
        self.order_manager = OrderManager(
            client=self.bitget_client,
            contract_client=self.contract_client,  # 传递合约客户端
            use_contract_for_short=True,  # 默认使用合约做空
            leverage=10,  # 10倍杠杆
            take_profit_ratio=self.config.take_profit_ratio,
            stop_loss_ratio=self.config.stop_loss_ratio
        )

        # 4. 交易 Agent（为每个交易对创建独立的 Agent）
        self.logger.print_info("初始化 AI Trading Agent...")
        self.agents = {}
        for symbol in self.config.symbols:
            self.agents[symbol] = TradingAgent(
                order_manager=self.order_manager,
                logger=self.logger,
                openai_api_base=self.config.openai_api_base,
                openai_api_key=self.config.openai_api_key,
                openai_model=self.config.openai_model,
                temperature=self.config.agent_temperature,
                max_iterations=self.config.agent_max_iterations,
                max_token_limit=self.config.memory_max_token_limit,
                trade_amount=self.config.trade_amount,
                current_symbol=symbol
            )

        self.logger.print_info(f"✅ 所有组件初始化完成！")

        # 启动时检查账户余额
        self._check_and_display_balance()

    def _check_and_display_balance(self):
        """检查并显示账户余额信息"""
        try:
            self.logger.print_section("💰 账户余额检查", style="bold green")

            balance_info = self.order_manager.get_available_balance_info('USDT')

            if balance_info['status'] == 'ok':
                self.logger.print_info(f"总余额: {balance_info['total']:.2f} USDT")
                self.logger.print_info(f"占用资金: {balance_info['occupied']:.2f} USDT")
                self.logger.print_info(f"可用余额: {balance_info['available']:.2f} USDT")

                # 计算建议交易金额（传入余额信息，避免重复查询）
                suggestion = self.order_manager.calculate_suggested_trade_amount(
                    desired_amount=self.config.trade_amount,
                    min_trade_amount=10.0,
                    balance_info=balance_info
                )

                if suggestion['can_trade']:
                    self.logger.print_info(f"✅ {suggestion['reason']}")
                else:
                    self.logger.print_warning(f"⚠️ {suggestion['reason']}")
            else:
                self.logger.print_error(f"❌ {balance_info['message']}")

        except Exception as e:
            self.logger.print_error(f"余额检查失败: {e}")

    def trading_cycle(self):
        """执行一轮交易决策循环（批量处理模式）"""
        try:
            self.logger.print_header(f"🔄 交易周期开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # 第一步：获取当前持仓（一次性查询，后续复用）
            self.logger.print_section("💰 检查账户余额", style="bold green")
            current_positions = self.order_manager.get_current_positions()

            # 第二步：获取余额信息（传入持仓信息，避免重复查询）
            balance_info = self.order_manager.get_available_balance_info(
                currency='USDT',
                current_positions=current_positions
            )

            if balance_info['status'] == 'ok':
                self.logger.print_info(f"可用余额: {balance_info['available']:.2f} USDT")

                # 第三步：计算建议的交易金额（传入余额信息，避免重复查询）
                suggestion = self.order_manager.calculate_suggested_trade_amount(
                    desired_amount=self.config.trade_amount,
                    min_trade_amount=10.0,
                    balance_info=balance_info
                )

                if not suggestion['can_trade']:
                    self.logger.print_warning(f"⚠️ {suggestion['reason']}")
                    self.logger.print_warning("跳过本次交易周期")
                    return
                else:
                    # 动态调整交易金额
                    adjusted_amount = suggestion['suggested_amount']
                    if adjusted_amount != self.config.trade_amount:
                        self.logger.print_warning(f"⚠️ {suggestion['reason']}")

                    # 更新所有 Agent 的交易金额
                    for agent in self.agents.values():
                        agent.trade_amount = adjusted_amount

                    self.logger.print_info(f"本次交易金额: {adjusted_amount:.2f} USDT")
            else:
                self.logger.print_error(f"❌ {balance_info['message']}")
                self.logger.print_warning("跳过本次交易周期")
                return

            # 显示持仓信息（复用已查询的数据）
            self.logger.print_info(f"当前持仓数量: {len(current_positions)}/{self.config.max_positions}")

            # 第一步：批量获取所有交易对的市场数据和多周期趋势
            self.logger.print_section("📊 批量获取市场数据", style="bold cyan")
            symbols_data = []

            for symbol in self.config.symbols:
                try:
                    self.logger.print_info(f"获取 {symbol} 数据...")

                    # 1. 获取15分钟K线数据
                    df = self.market_fetcher.fetch_ohlcv(
                        symbol=symbol,
                        timeframe=self.config.timeframe,
                        limit=self.config.candles_limit
                    )

                    if df is None or df.empty:
                        self.logger.print_warning(f"无法获取 {symbol} 的市场数据，跳过")
                        continue

                    # 2. 计算技术指标
                    df = TechnicalIndicators.calculate_all_indicators(
                        df,
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

                    # 3. 获取最新指标
                    market_data = TechnicalIndicators.get_latest_indicators(df)

                    # 4. 熔断检查（可选）
                    if self.config.circuit_breaker_enabled:
                        if self._check_circuit_breaker(df):
                            self.logger.print_warning(f"🔴 {symbol} 触发熔断机制！跳过")
                            continue

                    # 5. 获取多时间周期趋势
                    self.logger.print_info(f"分析 {symbol} 多周期趋势...")
                    multi_timeframe_trends = TechnicalIndicators.get_multi_timeframe_trend(
                        self.market_fetcher, symbol
                    )

                    # 显示市场数据
                    self.logger.print_market_data(symbol, market_data)

                    # 显示多周期趋势
                    trend_info = " | ".join([f"{tf}: {trend}" for tf, trend in multi_timeframe_trends.items()])
                    self.logger.print_info(f"多周期趋势: {trend_info}")

                    # 添加到批量数据列表
                    symbols_data.append({
                        'symbol': symbol,
                        'market_data': market_data,
                        'multi_timeframe_trends': multi_timeframe_trends
                    })

                except Exception as e:
                    self.logger.print_error(f"获取 {symbol} 数据时出错: {e}")
                    self.logger.logger.exception(e)

            # 第二步：批量 AI 决策（一次性分析所有交易对）
            if not symbols_data:
                self.logger.print_warning("没有可用的市场数据，跳过本轮决策")
                return

            self.logger.print_section("🤖 AI 批量决策分析", style="bold magenta")

            # 使用第一个 agent 进行批量决策（所有 agent 共享相同的配置）
            agent = list(self.agents.values())[0]
            batch_decisions = agent.make_batch_decision(
                symbols_data=symbols_data,
                current_positions=current_positions,
                max_positions=self.config.max_positions
            )

            # 第三步：处理每个决策并记录日志
            self.logger.print_section("📝 处理决策结果", style="bold yellow")

            for symbol, decision, details in batch_decisions:
                try:
                    # 找到对应的市场数据
                    symbol_info = next((d for d in symbols_data if d['symbol'] == symbol), None)
                    if not symbol_info:
                        continue

                    market_data = symbol_info['market_data']

                    # 显示决策
                    self.logger.print_info(f"{symbol}: {decision}")

                    # 记录决策日志
                    self.logger.log_decision(
                        symbol=symbol,
                        market_data=market_data,
                        prompt="批量决策",
                        ai_response=details.get('output', ''),
                        decision=decision,
                        action_details=details,
                        status='SUCCESS'
                    )

                except Exception as e:
                    self.logger.print_error(f"处理 {symbol} 决策时出错: {e}")
                    self.logger.logger.exception(e)

            self.logger.print_header(f"✅ 交易周期完成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except Exception as e:
            self.logger.print_error(f"交易周期异常: {e}")
            self.logger.logger.exception(e)

    def _check_circuit_breaker(self, df) -> bool:
        """
        检查熔断机制

        Args:
            df: K线数据

        Returns:
            是否触发熔断
        """
        try:
            # 计算最近 N 根 K 线的价格波动
            window = self.config.circuit_breaker_window
            if len(df) < window:
                return False

            recent_data = df.tail(window)
            price_change = (
                (recent_data['close'].iloc[-1] - recent_data['close'].iloc[0])
                / recent_data['close'].iloc[0]
            )

            # 如果波动超过阈值，触发熔断
            if abs(price_change) > self.config.circuit_breaker_threshold:
                self.logger.print_warning(
                    f"价格波动过大: {price_change*100:.2f}%，超过阈值 {self.config.circuit_breaker_threshold*100:.2f}%"
                )
                return True

            return False

        except Exception as e:
            self.logger.logger.error(f"熔断检查异常: {e}")
            return False

    def start(self):
        """启动机器人"""
        try:
            self.logger.print_section("🚀 启动 Quant Flow 交易机器人", style="bold green")

            # 创建调度器
            self.scheduler = BlockingScheduler()

            # 添加定时任务
            self.scheduler.add_job(
                self.trading_cycle,
                trigger=IntervalTrigger(minutes=self.config.interval_minutes),
                id='trading_cycle',
                name='交易决策循环',
                replace_existing=True
            )

            # 如果配置了立即执行，先执行一次
            if self.config.run_immediately:
                self.logger.print_info("立即执行第一次交易循环...")
                self.trading_cycle()

            # 显示下次执行时间
            next_run = datetime.now().replace(second=0, microsecond=0)
            next_run = next_run.replace(minute=next_run.minute + self.config.interval_minutes)
            self.logger.print_info(f"下次执行时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.print_info(f"执行间隔: {self.config.interval_minutes} 分钟")

            # 启动调度器
            self.is_running = True
            self.logger.print_section("✅ 机器人已启动，按 Ctrl+C 停止", style="bold green")
            self.scheduler.start()

        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            self.logger.print_error(f"启动失败: {e}")
            self.logger.logger.exception(e)
            sys.exit(1)

    def stop(self):
        """停止机器人"""
        self.logger.print_section("🛑 停止 Quant Flow 交易机器人", style="bold red")
        self.is_running = False

        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()

        self.logger.print_info("机器人已停止")


def signal_handler(signum, frame):
    """信号处理器"""
    print("\n\n收到停止信号，正在关闭...")
    sys.exit(0)


def main():
    """主函数"""
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # 创建并启动机器人
        bot = QuantFlowBot(config_path="config.yaml")
        bot.start()

    except FileNotFoundError as e:
        print(f"\n❌ 错误: {e}")
        print("\n请确保:")
        print("1. 已将 config.yaml.example 复制为 config.yaml")
        print("2. 已将 .env.example 复制为 .env")
        print("3. 已在 .env 中配置 API Key\n")
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
