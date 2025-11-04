#!/usr/bin/env python3
"""
Quant Flow - AI 驱动的加密货币自动交易机器人 (Hyperliquid 版本)

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
from src.trading.client import HyperliquidClient
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
        ║      🤖 Quant Flow Trading Bot (Hyperliquid) 🤖         ║
        ║                                                           ║
        ║      AI-Powered Perpetual Futures Trading System         ║
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
            testnet=self.config.hyperliquid_testnet
        )
        
        # 2. Hyperliquid 交易客户端
        self.logger.print_info("初始化 Hyperliquid 交易客户端...")
        self.hyperliquid_client = HyperliquidClient(
            private_key=self.config.hyperliquid_private_key,
            account_address=self.config.hyperliquid_account_address or None,
            testnet=self.config.hyperliquid_testnet
        )
        
        # 3. 订单管理器
        self.logger.print_info("初始化订单管理器...")
        self.order_manager = OrderManager(
            client=self.hyperliquid_client,
            take_profit_ratio=self.config.take_profit_ratio,
            stop_loss_ratio=self.config.stop_loss_ratio,
            default_leverage=self.config.default_leverage
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
            
            balance_info = self.order_manager.get_available_balance_info()
            
            if balance_info['status'] == 'ok':
                self.logger.print_info(f"总价值: ${balance_info['total']:.2f}")
                self.logger.print_info(f"已占用: ${balance_info['occupied']:.2f}")
                self.logger.print_info(f"可用余额: ${balance_info['available']:.2f}")
                
                # 计算建议交易金额
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
            
            # 第二步：获取余额信息
            balance_info = self.order_manager.get_available_balance_info()
            
            if balance_info['status'] == 'ok':
                self.logger.print_info(f"可用余额: ${balance_info['available']:.2f}")
                
                # 第三步：计算建议的交易金额
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
                    
                    self.logger.print_info(f"本次交易金额: ${adjusted_amount:.2f}")
            else:
                self.logger.print_error(f"❌ {balance_info['message']}")
                self.logger.print_warning("跳过本次交易周期")
                return
            
            # 显示持仓信息
            self.logger.print_info(f"当前持仓数量: {len(current_positions)}/{self.config.max_positions}")
            for pos in current_positions:
                symbol = pos['coin']
                size = float(pos['szi'])
                side = "多头" if size > 0 else "空头"
                pnl = float(pos.get('unrealizedPnl', 0))
                self.logger.print_info(f"  {symbol}: {side} {abs(size)} 张，未实现盈亏: ${pnl:.2f}")
            
            # 第四步：批量获取所有交易对的市场数据和多周期趋势
            self.logger.print_section("📊 批量获取市场数据", style="bold cyan")
            symbols_data = []
            
            for symbol in self.config.symbols:
                try:
                    self.logger.print_info(f"获取 {symbol} 数据...")
                    
                    # 1. 获取K线数据
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
                    
                    # 4. 获取多时间周期趋势
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
            
            # 第五步：批量 AI 决策（一次性分析所有交易对）
            if not symbols_data:
                self.logger.print_warning("没有可用的市场数据，跳过本轮决策")
                return

            # 📝 添加详细日志：验证 symbols_data 结构
            self.logger.logger.info(f"准备批量决策 - symbols_data 数量: {len(symbols_data)}")
            for i, data in enumerate(symbols_data):
                if isinstance(data, dict):
                    symbol = data.get('symbol', 'UNKNOWN')
                    has_market_data = 'market_data' in data
                    has_trends = 'multi_timeframe_trends' in data
                    self.logger.logger.info(f"  #{i}: symbol={symbol}, has_market_data={has_market_data}, has_trends={has_trends}")
                else:
                    self.logger.logger.error(f"  #{i}: 错误的数据类型 {type(data)}")

            self.logger.print_section("🤖 AI 批量决策分析", style="bold magenta")

            # 使用第一个 agent 进行批量决策（所有 agent 共享相同的配置）
            agent = list(self.agents.values())[0]
            batch_decisions = agent.make_batch_decision(
                symbols_data=symbols_data,
                current_positions=current_positions,
                max_positions=self.config.max_positions
            )
            
            # 第六步：处理每个决策并记录日志
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
