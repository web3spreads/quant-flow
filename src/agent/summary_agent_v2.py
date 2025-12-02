"""
汇总 Agent 模块 v2 - 使用 LangChain 上下文压缩技术
负责对历史决策和市场走势进行分层汇总，生成压缩的上下文摘要
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.messages.utils import trim_messages, count_tokens_approximately
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from src.utils.logger import TradingLogger


# 结构化的市场走势汇总
class MarketTrendSummary(BaseModel):
    """市场走势汇总结构"""
    price_trend: str = Field(description="价格趋势：上涨/下跌/震荡")
    price_range: str = Field(description="价格区间：如 $60000-$62000")
    key_levels: List[float] = Field(description="关键价位列表")
    rsi_status: str = Field(description="RSI 状态：超买/超卖/中性")
    volume_pattern: str = Field(description="成交量模式：放大/缩小/正常")
    overall_sentiment: str = Field(description="整体市场情绪")


# 结构化的决策汇总
class DecisionSummary(BaseModel):
    """决策汇总结构"""
    total_decisions: int = Field(description="决策总数")
    buy_count: int = Field(description="买入次数")
    sell_count: int = Field(description="卖出次数")
    do_nothing_count: int = Field(description="观望次数")
    key_reasons: List[str] = Field(description="主要决策理由")
    strategy_pattern: str = Field(description="策略模式描述")
    risk_events: List[str] = Field(description="风险事件列表")


class SummaryAgentV2:
    """增强版汇总 Agent - 使用上下文压缩技术"""

    def __init__(
        self,
        logger: TradingLogger,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str,
        temperature: float = 0.1,
        max_context_tokens: int = 2000,
    ):
        """
        初始化增强版汇总 Agent

        Args:
            logger: 日志记录器
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            openai_model: 模型名称
            temperature: 温度参数
            max_context_tokens: 最大上下文 token 数
        """
        self.logger = logger
        self.max_context_tokens = max_context_tokens
        
        # 初始化主 LLM
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )
        
        # 初始化压缩用的快速 LLM (使用更便宜的模型)
        self.compression_llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=0.1,
        )

    def compress_market_history(
        self,
        symbol: str,
        market_records: List[Dict[str, Any]]
    ) -> MarketTrendSummary:
        """
        压缩市场历史数据为结构化汇总

        Args:
            symbol: 交易对
            market_records: 市场数据记录列表

        Returns:
            结构化的市场走势汇总
        """
        if not market_records:
            return MarketTrendSummary(
                price_trend="未知",
                price_range="无数据",
                key_levels=[],
                rsi_status="未知",
                volume_pattern="无数据",
                overall_sentiment="无数据"
            )

        try:
            # 提取关键市场数据
            prices = [r.get('market_data', {}).get('current_price', 0) for r in market_records]
            rsi_values = [r.get('market_data', {}).get('rsi', 50) for r in market_records]
            volumes = [r.get('market_data', {}).get('volume_change', 0) for r in market_records]
            
            # 统计分析
            price_min = min(prices) if prices else 0
            price_max = max(prices) if prices else 0
            avg_rsi = sum(rsi_values) / len(rsi_values) if rsi_values else 50
            avg_volume = sum(volumes) / len(volumes) if volumes else 0
            
            # 构建压缩提示词
            prompt = f"""分析以下 {symbol} 的市场数据，提取关键信息：

价格范围: ${price_min:.2f} - ${price_max:.2f}
平均 RSI: {avg_rsi:.1f}
平均成交量变化: {avg_volume:.1f}%

记录数量: {len(market_records)}
时间跨度: {market_records[0].get('timestamp', '未知')} 到 {market_records[-1].get('timestamp', '未知')}

请用简洁的方式总结：
1. 价格趋势（上涨/下跌/震荡）
2. RSI 状态（超买/超卖/中性）
3. 成交量模式
4. 整体市场情绪

要求：每点不超过10个字。"""

            # 使用快速模型进行压缩
            messages = [
                SystemMessage(content="你是市场数据分析专家，善于提取关键信息。"),
                HumanMessage(content=prompt)
            ]
            
            response = self.compression_llm.invoke(messages)
            content = response.content.strip()
            
            # 解析响应（简单版本，实际可以使用结构化输出）
            price_trend = "震荡"
            if "上涨" in content:
                price_trend = "上涨"
            elif "下跌" in content:
                price_trend = "下跌"
            
            rsi_status = "中性"
            if avg_rsi > 70:
                rsi_status = "超买"
            elif avg_rsi < 30:
                rsi_status = "超卖"
            
            volume_pattern = "正常"
            if avg_volume > 20:
                volume_pattern = "放大"
            elif avg_volume < -20:
                volume_pattern = "缩小"
            
            return MarketTrendSummary(
                price_trend=price_trend,
                price_range=f"${price_min:.2f}-${price_max:.2f}",
                key_levels=[price_min, (price_min + price_max) / 2, price_max],
                rsi_status=rsi_status,
                volume_pattern=volume_pattern,
                overall_sentiment=content[:50]  # 取前50字符
            )
            
        except Exception as e:
            self.logger.logger.error(f"市场数据压缩失败: {e}")
            return MarketTrendSummary(
                price_trend="未知",
                price_range="处理失败",
                key_levels=[],
                rsi_status="未知",
                volume_pattern="未知",
                overall_sentiment=str(e)[:50]
            )

    def compress_decision_history(
        self,
        symbol: str,
        decision_records: List[Dict[str, Any]]
    ) -> DecisionSummary:
        """
        压缩决策历史为结构化汇总

        Args:
            symbol: 交易对
            decision_records: 决策记录列表

        Returns:
            结构化的决策汇总
        """
        if not decision_records:
            return DecisionSummary(
                total_decisions=0,
                buy_count=0,
                sell_count=0,
                do_nothing_count=0,
                key_reasons=[],
                strategy_pattern="无决策历史",
                risk_events=[]
            )

        try:
            # 统计决策类型
            buy_count = sum(1 for r in decision_records if 'BUY' in r.get('decision', ''))
            sell_count = sum(1 for r in decision_records if 'SELL' in r.get('decision', '') and 'BUY' not in r.get('decision', ''))
            do_nothing_count = sum(1 for r in decision_records if 'DO_NOTHING' in r.get('decision', ''))
            
            # 提取决策理由（使用 LLM 压缩）
            reasons = [r.get('reason', '')[:100] for r in decision_records if r.get('reason')]
            
            if len(reasons) > 5:
                # 使用 LLM 压缩理由
                reasons_text = "\n".join([f"{i+1}. {r}" for i, r in enumerate(reasons)])
                
                prompt = f"""总结以下 {symbol} 的决策理由，提取3-5个关键要点：

{reasons_text}

要求：每个要点不超过15个字，只列出最重要的决策依据。"""

                messages = [
                    SystemMessage(content="你是决策分析专家，善于提炼核心逻辑。"),
                    HumanMessage(content=prompt)
                ]
                
                response = self.compression_llm.invoke(messages)
                key_reasons = [line.strip() for line in response.content.strip().split('\n') if line.strip()][:5]
            else:
                key_reasons = reasons
            
            # 识别策略模式
            strategy_pattern = "观望为主"
            if buy_count > sell_count and buy_count > do_nothing_count:
                strategy_pattern = "积极做多"
            elif sell_count > buy_count:
                strategy_pattern = "谨慎平仓"
            
            # 识别风险事件（如果有）
            risk_events = []
            for record in decision_records:
                reason = record.get('reason', '').lower()
                if any(keyword in reason for keyword in ['止损', '风险', '警告', '异常']):
                    risk_events.append(record.get('reason', '')[:50])
            
            return DecisionSummary(
                total_decisions=len(decision_records),
                buy_count=buy_count,
                sell_count=sell_count,
                do_nothing_count=do_nothing_count,
                key_reasons=key_reasons,
                strategy_pattern=strategy_pattern,
                risk_events=risk_events[:3]  # 最多保留3个
            )
            
        except Exception as e:
            self.logger.logger.error(f"决策历史压缩失败: {e}")
            return DecisionSummary(
                total_decisions=len(decision_records),
                buy_count=0,
                sell_count=0,
                do_nothing_count=0,
                key_reasons=[],
                strategy_pattern=f"压缩失败: {str(e)[:30]}",
                risk_events=[]
            )

    def create_compressed_summary(
        self,
        symbol: str,
        recent_records: List[Dict[str, Any]],
        older_records: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        创建压缩的上下文汇总（分别汇总市场走势和决策历史）

        Args:
            symbol: 交易对
            recent_records: 最近的记录（前10次）
            older_records: 较早的记录（前10-20次，可选）

        Returns:
            压缩的汇总文本
        """
        try:
            self.logger.logger.info(f"[汇总Agent V2] 开始压缩 {symbol} 的历史信息...")
            
            # 1. 压缩最近的市场走势
            recent_market = self.compress_market_history(symbol, recent_records)
            
            # 2. 压缩最近的决策历史
            recent_decisions = self.compress_decision_history(symbol, recent_records)
            
            # 3. 构建汇总文本
            summary_parts = [
                f"## {symbol} 压缩历史汇总\n",
                "### 📊 最近市场走势（前10次）",
                f"- 趋势: {recent_market.price_trend}",
                f"- 价格: {recent_market.price_range}",
                f"- RSI: {recent_market.rsi_status}",
                f"- 成交量: {recent_market.volume_pattern}",
                f"- 情绪: {recent_market.overall_sentiment}\n",
                
                "### 🎯 最近决策记录（前10次）",
                f"- 总决策: {recent_decisions.total_decisions}次",
                f"- 买入: {recent_decisions.buy_count}次 | 卖出: {recent_decisions.sell_count}次 | 观望: {recent_decisions.do_nothing_count}次",
                f"- 策略: {recent_decisions.strategy_pattern}",
            ]
            
            if recent_decisions.key_reasons:
                summary_parts.append("- 关键理由:")
                for reason in recent_decisions.key_reasons[:3]:
                    summary_parts.append(f"  * {reason}")
            
            if recent_decisions.risk_events:
                summary_parts.append("- ⚠️ 风险事件:")
                for event in recent_decisions.risk_events:
                    summary_parts.append(f"  * {event}")
            
            # 4. 如果有较早记录，添加对比
            if older_records and len(older_records) >= 5:
                older_market = self.compress_market_history(symbol, older_records)
                older_decisions = self.compress_decision_history(symbol, older_records)
                
                summary_parts.extend([
                    "\n### 📈 之前市场走势（前10-20次）",
                    f"- 趋势: {older_market.price_trend}",
                    f"- 价格: {older_market.price_range}",
                    f"- RSI: {older_market.rsi_status}\n",
                    
                    "### 🔄 之前决策记录（前10-20次）",
                    f"- 总决策: {older_decisions.total_decisions}次",
                    f"- 策略: {older_decisions.strategy_pattern}\n",
                    
                    "### 💡 趋势演变",
                    f"- 市场: {older_market.price_trend} → {recent_market.price_trend}",
                    f"- 策略: {older_decisions.strategy_pattern} → {recent_decisions.strategy_pattern}",
                ])
            
            summary = "\n".join(summary_parts)
            
            # 5. 检查 token 数量，如果超限则进一步压缩
            token_count = count_tokens_approximately(summary)
            
            if token_count > self.max_context_tokens:
                self.logger.logger.warning(f"汇总超出 token 限制 ({token_count} > {self.max_context_tokens})，进行二次压缩...")
                
                # 使用 LLM 进行二次压缩
                compress_prompt = f"""将以下汇总进一步压缩到 {self.max_context_tokens // 4} token 以内，保留最关键信息：

{summary}

要求：
1. 保留市场趋势和决策策略
2. 保留关键数字
3. 删除次要细节
4. 使用更简洁的表达"""

                messages = [
                    SystemMessage(content="你是信息压缩专家，善于在保留核心信息的同时大幅减少文本长度。"),
                    HumanMessage(content=compress_prompt)
                ]
                
                response = self.compression_llm.invoke(messages)
                summary = response.content.strip()
                
                new_token_count = count_tokens_approximately(summary)
                self.logger.logger.info(f"二次压缩完成: {token_count} → {new_token_count} tokens")
            
            self.logger.logger.info(f"[汇总Agent V2] {symbol} 压缩完成，最终 token 数: {count_tokens_approximately(summary)}")
            
            return summary
            
        except Exception as e:
            self.logger.logger.error(f"创建压缩汇总失败: {e}")
            return f"## {symbol} 历史汇总\n\n汇总生成失败: {str(e)}"


class DecisionHistory:
    """决策历史管理器 - 为每个交易对维护独立的决策历史（保持不变）"""

    def __init__(self, max_history: int = 50):
        """
        初始化决策历史管理器

        Args:
            max_history: 每个交易对保存的最大历史记录数
        """
        # 为每个交易对维护独立的历史记录 {symbol: [records]}
        self.histories: Dict[str, List[Dict[str, Any]]] = {}
        self.max_history = max_history

    def add_decision(
        self,
        symbol: str,
        decision: str,
        market_data: Dict[str, Any],
        reason: str = "",
        action_details: Optional[Dict[str, Any]] = None
    ):
        """
        添加决策记录

        Args:
            symbol: 交易对
            decision: 决策类型
            market_data: 市场数据
            reason: 决策原因
            action_details: 操作详情
        """
        if symbol not in self.histories:
            self.histories[symbol] = []

        data_ts = market_data.get('timestamp') if isinstance(market_data, dict) else None
        record = {
            # 使用数据时间而不是当前时间，便于回测报告准确反映交易时间
            'timestamp': data_ts if data_ts is not None else datetime.now(),
            'decision': decision,
            'market_data': market_data,
            'reason': reason,
            'action_details': action_details
        }
        
        self.histories[symbol].append(record)
        
        # 保持历史记录数量限制
        if len(self.histories[symbol]) > self.max_history:
            self.histories[symbol] = self.histories[symbol][-self.max_history:]

    def get_recent_decisions(self, symbol: str, count: int = 10) -> List[Dict[str, Any]]:
        """
        获取最近的N次决策

        Args:
            symbol: 交易对
            count: 记录数量

        Returns:
            决策记录列表（倒序，最新的在前）
        """
        if symbol not in self.histories:
            return []
        
        return list(reversed(self.histories[symbol][-count:]))

    def get_decisions_range(
        self,
        symbol: str,
        start_index: int,
        end_index: int
    ) -> List[Dict[str, Any]]:
        """
        获取指定范围的决策记录

        Args:
            symbol: 交易对
            start_index: 起始索引（从最新往前数）
            end_index: 结束索引（从最新往前数）

        Returns:
            决策记录列表（倒序，最新的在前）
        """
        if symbol not in self.histories:
            return []
        
        history = self.histories[symbol]
        
        # 从后往前取
        if end_index > len(history):
            end_index = len(history)
        
        if start_index >= end_index:
            return []
        
        # 倒序返回
        return list(reversed(history[-end_index:-start_index])) if start_index > 0 else list(reversed(history[-end_index:]))

    def get_history_count(self, symbol: str) -> int:
        """
        获取历史记录数量

        Args:
            symbol: 交易对

        Returns:
            记录数量
        """
        return len(self.histories.get(symbol, []))
