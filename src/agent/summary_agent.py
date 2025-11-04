"""
汇总 Agent 模块
负责对历史决策进行分层汇总，生成上下文摘要
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from src.utils.logger import TradingLogger


class SummaryAgent:
    """汇总 Agent - 对历史决策进行分层汇总"""

    def __init__(
        self,
        logger: TradingLogger,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str,
        temperature: float = 0.1,
    ):
        """
        初始化汇总 Agent

        Args:
            logger: 日志记录器
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            openai_model: 模型名称
            temperature: 温度参数
        """
        self.logger = logger
        
        # 初始化 LLM (使用更快的模型进行汇总)
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )
        
        # 系统提示词
        self.system_message = SystemMessage(content="""你是一位专业的交易记录分析专家。
你的任务是分析一系列交易决策记录，并生成简洁而有价值的汇总报告。

汇总要点:
1. 识别主要的市场趋势和价格变化
2. 总结执行的交易操作及其结果
3. 指出关键的技术指标变化
4. 提取重要的决策理由和市场信号
5. 保持简洁，突出重点

汇总风格:
- 客观、数据驱动
- 简洁明了，避免冗余
- 关注市场状态变化和交易逻辑
- 不超过200字""")

    def summarize_recent_decisions(
        self,
        symbol: str,
        decisions: List[Dict[str, Any]],
        time_range: str = "最近10次"
    ) -> str:
        """
        汇总最近的决策记录

        Args:
            symbol: 交易对
            decisions: 决策记录列表，每项包含 {timestamp, decision, market_data, reason}
            time_range: 时间范围描述

        Returns:
            汇总文本
        """
        if not decisions:
            return f"{symbol} 在{time_range}暂无决策记录"

        # 构建汇总提示词
        prompt = f"""请汇总以下 {symbol} 的交易决策记录（{time_range}）：

"""
        for idx, record in enumerate(decisions, 1):
            timestamp = record.get('timestamp', '未知时间')
            decision = record.get('decision', '未知决策')
            reason = record.get('reason', '无原因')
            market_data = record.get('market_data', {})
            
            price = market_data.get('current_price', 0)
            rsi = market_data.get('rsi', 0)
            
            prompt += f"""
{idx}. [{timestamp}]
   决策: {decision}
   价格: ${price:.2f} | RSI: {rsi:.1f}
   原因: {reason}
"""

        prompt += f"""

请从以下角度进行汇总:
1. 价格趋势: {symbol} 价格的整体走势
2. 操作统计: 开仓、平仓、观望的次数
3. 关键信号: 触发交易的主要技术指标
4. 市场状态: 当前市场处于什么阶段（上涨、下跌、震荡）
5. 决策逻辑: 主要的交易策略和思路

要求: 简洁明了，控制在150字以内。"""

        try:
            # 调用 LLM 进行汇总
            messages = [self.system_message, HumanMessage(content=prompt)]
            response = self.llm.invoke(messages)
            
            summary = response.content.strip()
            self.logger.logger.info(f"[汇总Agent] {symbol} {time_range}汇总完成")
            
            return summary
            
        except Exception as e:
            self.logger.logger.error(f"汇总失败: {e}")
            return f"{symbol} 汇总失败: {str(e)}"

    def create_hierarchical_summary(
        self,
        symbol: str,
        recent_10: List[Dict[str, Any]],
        recent_10_20: List[Dict[str, Any]]
    ) -> str:
        """
        创建分层汇总（前10次 + 前10-20次 → 整体汇总）

        Args:
            symbol: 交易对
            recent_10: 最近10次决策记录
            recent_10_20: 第10-20次决策记录

        Returns:
            分层汇总文本
        """
        # 第一层：汇总前10次
        summary_1 = self.summarize_recent_decisions(
            symbol=symbol,
            decisions=recent_10,
            time_range="最近10次决策"
        )
        
        # 第二层：汇总前10-20次
        summary_2 = self.summarize_recent_decisions(
            symbol=symbol,
            decisions=recent_10_20,
            time_range="第10-20次决策"
        )
        
        # 第三层：对两个汇总进行整合
        if summary_1 and summary_2 and "汇总失败" not in summary_1 and "汇总失败" not in summary_2:
            integration_prompt = f"""请整合以下两个时间段的交易汇总，生成一个综合性的市场分析：

【最近10次决策汇总】
{summary_1}

【第10-20次决策汇总】
{summary_2}

请从以下角度进行整合分析:
1. 趋势演变: 从20次前到现在，市场趋势如何演变
2. 策略调整: 交易策略是否发生变化
3. 关键转折: 是否存在重要的市场转折点
4. 当前态势: 综合两个阶段，当前应采取什么策略

要求: 简洁有力，控制在200字以内。"""

            try:
                messages = [self.system_message, HumanMessage(content=integration_prompt)]
                response = self.llm.invoke(messages)
                
                integrated_summary = response.content.strip()
                self.logger.logger.info(f"[汇总Agent] {symbol} 分层汇总完成")
                
                return f"""
## {symbol} 历史决策分层汇总

### 📊 最近10次决策
{summary_1}

### 📈 第10-20次决策
{summary_2}

### 🎯 综合分析
{integrated_summary}
"""
            except Exception as e:
                self.logger.logger.error(f"整合汇总失败: {e}")
                # 返回基础汇总
                return f"""
## {symbol} 历史决策汇总

### 📊 最近10次决策
{summary_1}

### 📈 第10-20次决策
{summary_2}
"""
        else:
            # 如果其中一个汇总失败，返回可用的汇总
            return f"""
## {symbol} 历史决策汇总

### 📊 最近10次决策
{summary_1}

### 📈 第10-20次决策
{summary_2}
"""


class DecisionHistory:
    """决策历史管理器 - 为每个交易对维护独立的决策历史"""

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
        
        record = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
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
