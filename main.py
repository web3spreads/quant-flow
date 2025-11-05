#!/usr/bin/env python3
"""
Quant Flow - AI 驱动的加密货币自动交易机器人
多 Agent 架构：为每个交易对维护独立上下文，配合汇总 Agent 和现货 Agent
"""

import sys
import time
import signal
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import get_config
from src.utils.logger import get_logger
from src.utils.banner import print_startup_banner
from src.data.market_data import MarketDataFetcher
from src.data.indicators import TechnicalIndicators
from src.trading.client import HyperliquidClient
from src.trading.order_manager import OrderManager
from src.agent.single_symbol_agent import SingleSymbolAgent
from src.agent.spot_agent import SpotAgent
from src.agent.summary_agent_v2 import SummaryAgentV2, DecisionHistory
from src.notification import Notifier


class QuantFlowBot:
    """Quant Flow 交易机器人 - 多 Agent 架构"""

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
        print_startup_banner(config=self.config, console=self.logger.console)
        
        # 初始化组件
        self._initialize_components()
        
        # 调度器
        self.scheduler = None
        self.is_running = False

    def _initialize_components(self):
        """初始化所有组件"""
        self.logger.print_section("🔧 初始化多 Agent 架构", style="bold yellow")

        # 0. 通知系统（优先初始化，以便其他组件可以使用）
        self.logger.print_info("初始化通知系统...")
        notifications_config = getattr(self.config, 'notifications', {'enabled': False})
        self.notifier = Notifier(notifications_config)

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
        
        # 4. 决策历史管理器
        self.logger.print_info("初始化决策历史管理器...")
        self.decision_history = DecisionHistory(max_history=50)
        
        # 5. 汇总 Agent (V2 - 使用上下文压缩)
        self.logger.print_info("初始化汇总 Agent V2 (使用上下文压缩)...")
        self.summary_agent = SummaryAgentV2(
            logger=self.logger,
            openai_api_base=self.config.openai_api_base,
            openai_api_key=self.config.openai_api_key,
            openai_model=self.config.openai_model,
            temperature=0.1,
            max_context_tokens=2000  # 限制汇总长度
        )
        
        # 6. 为每个交易对创建独立的单币 Agent
        self.logger.print_info("为每个交易对创建独立 Agent...")
        self.symbol_agents = {}
        for symbol in self.config.symbols:
            self.symbol_agents[symbol] = SingleSymbolAgent(
                symbol=symbol,
                order_manager=self.order_manager,
                logger=self.logger,
                openai_api_base=self.config.openai_api_base,
                openai_api_key=self.config.openai_api_key,
                openai_model=self.config.openai_model,
                temperature=self.config.agent_temperature,
                max_iterations=self.config.agent_max_iterations,
                trade_amount=self.config.trade_amount,
                notifier=self.notifier
            )
            self.logger.print_info(f"  ✅ {symbol} Agent 创建完成")
        
        # 7. 现货定投 Agent
        self.logger.print_info("初始化现货定投 Agent...")
        self.spot_agent = SpotAgent(
            order_manager=self.order_manager,
            logger=self.logger,
            openai_api_base=self.config.openai_api_base,
            openai_api_key=self.config.openai_api_key,
            openai_model=self.config.openai_model,
            temperature=0.05,  # 更保守
            trade_amount=self.config.trade_amount,
            notifier=self.notifier
        )
        
        self.logger.print_info(f"✅ 多 Agent 架构初始化完成！")
        self.logger.print_info(f"  - {len(self.symbol_agents)} 个单币 Agent")
        self.logger.print_info(f"  - 1 个汇总 Agent")
        self.logger.print_info(f"  - 1 个现货定投 Agent")
        
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
        """执行一轮交易决策循环（多 Agent 独立决策模式）"""
        try:
            self.logger.print_header(f"🔄 多 Agent 交易周期开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 第一步：获取当前持仓和余额
            self.logger.print_section("💰 检查账户状态", style="bold green")
            current_positions = self.order_manager.get_current_positions()
            balance_info = self.order_manager.get_available_balance_info()
            
            if balance_info['status'] != 'ok':
                self.logger.print_error(f"❌ {balance_info['message']}")
                self.logger.print_warning("跳过本次交易周期")
                return
            
            self.logger.print_info(f"可用余额: ${balance_info['available']:.2f}")
            self.logger.print_info(f"当前持仓数量: {len(current_positions)}/{self.config.max_positions}")
            
            # 调整交易金额
            suggestion = self.order_manager.calculate_suggested_trade_amount(
                desired_amount=self.config.trade_amount,
                min_trade_amount=10.0,
                balance_info=balance_info
            )
            
            if not suggestion['can_trade']:
                self.logger.print_warning(f"⚠️ {suggestion['reason']}")
                self.logger.print_warning("跳过本次交易周期")
                return
            
            adjusted_amount = suggestion['suggested_amount']
            if adjusted_amount != self.config.trade_amount:
                self.logger.print_warning(f"⚠️ {suggestion['reason']}")
            
            # 更新所有 Agent 的交易金额
            for agent in self.symbol_agents.values():
                agent.trade_amount = adjusted_amount
            self.spot_agent.trade_amount = adjusted_amount
            
            self.logger.print_info(f"本次交易金额: ${adjusted_amount:.2f}")
            
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
                        limit=self.config.candles_limit
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
                            'fast': self.config.macd_fast,
                            'slow': self.config.macd_slow,
                            'signal': self.config.macd_signal
                        },
                        bollinger_params={
                            'period': self.config.bollinger_period,
                            'std_dev': self.config.bollinger_std
                        }
                    )
                    
                    market_data = TechnicalIndicators.get_latest_indicators(df)
                    
                    # 获取多周期趋势
                    multi_timeframe_trends = TechnicalIndicators.get_multi_timeframe_trend(
                        self.market_fetcher, symbol
                    )
                    
                    # 显示市场数据
                    self.logger.print_market_data(symbol, market_data)
                    trend_info = " | ".join([f"{tf}: {trend}" for tf, trend in multi_timeframe_trends.items()])
                    self.logger.print_info(f"多周期趋势: {trend_info}")
                    
                    # 生成历史汇总（如果有足够的历史记录）
                    historical_summary = None
                    history_count = self.decision_history.get_history_count(symbol)
                    
                    if history_count >= 20:
                        # 有足够历史，生成压缩汇总（分离市场走势和决策历史）
                        self.logger.print_info(f"生成 {symbol} 压缩汇总（共 {history_count} 条记录）...")
                        recent_10 = self.decision_history.get_recent_decisions(symbol, 10)
                        recent_10_20 = self.decision_history.get_decisions_range(symbol, 10, 20)
                        
                        # 使用 V2 压缩方法
                        historical_summary = self.summary_agent.create_compressed_summary(
                            symbol=symbol,
                            recent_records=recent_10,
                            older_records=recent_10_20
                        )
                    elif history_count >= 10:
                        # 只有 10-19 条记录，生成简单压缩汇总
                        self.logger.print_info(f"生成 {symbol} 简单压缩汇总（共 {history_count} 条记录）...")
                        recent = self.decision_history.get_recent_decisions(symbol, 10)
                        
                        # 使用 V2 压缩方法
                        historical_summary = self.summary_agent.create_compressed_summary(
                            symbol=symbol,
                            recent_records=recent,
                            older_records=None
                        )
                    else:
                        self.logger.print_info(f"{symbol} 历史记录不足（{history_count} < 10），跳过汇总")
                    
                    # 调用单币 Agent 决策
                    agent = self.symbol_agents[symbol]
                    decision, details = agent.make_decision(
                        market_data=market_data,
                        multi_timeframe_trends=multi_timeframe_trends,
                        current_positions=current_positions,
                        max_positions=self.config.max_positions,
                        historical_summary=historical_summary
                    )
                    
                    # 显示决策
                    self.logger.print_info(f"[{symbol}Agent] 决策: {decision}")
                    
                    # 记录决策历史
                    self.decision_history.add_decision(
                        symbol=symbol,
                        decision=decision,
                        market_data=market_data,
                        reason=details.get('output', '')[:200],  # 截取前200字符
                        action_details=details
                    )
                    
                    # 记录决策日志
                    self.logger.log_decision(
                        symbol=symbol,
                        market_data=market_data,
                        prompt=details.get('prompt', ''),
                        ai_response=details.get('output', ''),
                        decision=decision,
                        action_details=details,
                        status='SUCCESS'
                    )
                    
                    # 如果是现货定投推荐，收集起来
                    if decision == "BUY_SPOT_RECOMMEND":
                        spot_recommendations.append({
                            'symbol': symbol,
                            'market_data': market_data,
                            'multi_timeframe_trends': multi_timeframe_trends,
                            'reason': details.get('output', ''),
                            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                    
                except Exception as e:
                    self.logger.print_error(f"{symbol} Agent 决策异常: {e}")
                    self.logger.logger.exception(e)
            
            # 第三步：处理现货定投推荐
            if spot_recommendations:
                self.logger.print_section("💎 现货定投 Agent 评估", style="bold blue")
                
                # 获取当前现货持仓（从持仓中筛选现货）
                # TODO: 需要在 order_manager 中添加区分现货和合约的方法
                current_spot_holdings = []  # 暂时为空，后续实现
                
                for recommendation in spot_recommendations:
                    try:
                        symbol = recommendation['symbol']
                        self.logger.print_info(f"评估 {symbol} 的现货定投推荐...")
                        
                        # 调用现货 Agent 评估
                        spot_decision, spot_details = self.spot_agent.evaluate_spot_recommendation(
                            symbol=symbol,
                            market_data=recommendation['market_data'],
                            multi_timeframe_trends=recommendation['multi_timeframe_trends'],
                            recommendation=recommendation,
                            current_spot_holdings=current_spot_holdings
                        )
                        
                        self.logger.print_info(f"[现货Agent] {symbol} 决策: {spot_decision}")
                        
                        # 记录现货决策日志
                        self.logger.log_decision(
                            symbol=f"{symbol}_SPOT",
                            market_data=recommendation['market_data'],
                            prompt=spot_details.get('prompt', ''),
                            ai_response=spot_details.get('output', ''),
                            decision=spot_decision,
                            action_details=spot_details,
                            status='SUCCESS'
                        )
                        
                    except Exception as e:
                        self.logger.print_error(f"现货 Agent 评估 {symbol} 异常: {e}")
                        self.logger.logger.exception(e)
            
            self.logger.print_header(f"✅ 多 Agent 交易周期完成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
        except Exception as e:
            self.logger.print_error(f"交易周期异常: {e}")
            self.logger.logger.exception(e)

            # 发送错误通知
            if self.notifier:
                self.notifier.notify_error(
                    title="交易周期异常",
                    error_message=str(e),
                    context="交易决策循环执行时发生错误"
                )

    def start(self):
        """启动机器人"""
        try:
            self.logger.print_section("🚀 启动多 Agent 交易机器人", style="bold green")
            
            # 创建调度器
            self.scheduler = BlockingScheduler()
            
            # 添加定时任务
            self.scheduler.add_job(
                self.trading_cycle,
                trigger=IntervalTrigger(minutes=self.config.interval_minutes),
                id='trading_cycle',
                name='多 Agent 交易决策循环',
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
            self.logger.print_section("✅ 多 Agent 机器人已启动，按 Ctrl+C 停止", style="bold green")
            self.scheduler.start()
            
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            self.logger.print_error(f"启动失败: {e}")
            self.logger.logger.exception(e)
            sys.exit(1)

    def stop(self):
        """停止机器人"""
        self.logger.print_section("🛑 停止多 Agent 交易机器人", style="bold red")
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
