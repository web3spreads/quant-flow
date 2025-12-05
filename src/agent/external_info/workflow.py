"""
LangGraph 工作流定义
用于外部信息收集和报告生成
"""

from typing import Dict, Any
from datetime import datetime, timedelta

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser

from src.agent.external_info.state import ResearchState
from src.agent.external_info.tools import (
    search_crypto_market_news,
    search_crypto_regulatory_news,
    search_crypto_macro_news,
    create_period_search_queries
)


class ExternalInfoWorkflow:
    """外部信息收集工作流"""
    
    # 时间周期配置
    PERIOD_CONFIG = {
        "daily": {"hours": 24, "description": "过去24小时"},
        "weekly": {"hours": 168, "description": "过去一周"},
        "biweekly": {"hours": 336, "description": "过去两周"},
        "monthly": {"hours": 720, "description": "过去一个月"}
    }
    
    def __init__(
        self,
        llm: ChatOpenAI,
        system_prompt: str,
        research_template: str,
        exa_api_key: str
    ):
        """
        初始化工作流
        
        Args:
            llm: LangChain LLM 实例
            system_prompt: 系统提示
            research_template: 研究模板
            exa_api_key: Exa API 密钥
        """
        self.llm = llm
        self.system_prompt = system_prompt
        self.research_template = research_template
        self.exa_api_key = exa_api_key
        self.parser = JsonOutputParser()
        
        # 构建工作流图
        self.app = self._build_workflow()
    
    def _build_workflow(self) -> StateGraph:
        """构建工作流图"""
        workflow = StateGraph(ResearchState)
        
        # 添加节点
        workflow.add_node("prepare_queries", self._prepare_queries_node)
        workflow.add_node("execute_searches", self._execute_searches_node)
        workflow.add_node("format_results", self._format_results_node)
        workflow.add_node("generate_report", self._generate_report_node)
        
        # 设置入口点
        workflow.set_entry_point("prepare_queries")
        
        # 添加边
        workflow.add_edge("prepare_queries", "execute_searches")
        workflow.add_edge("execute_searches", "format_results")
        workflow.add_edge("format_results", "generate_report")
        workflow.add_edge("generate_report", END)
        
        return workflow.compile()
    
    def _prepare_queries_node(self, state: ResearchState) -> Dict[str, Any]:
        """准备搜索查询"""
        period = state["period"]
        symbols = state["symbols"]
        
        period_hours = self.PERIOD_CONFIG.get(period, {}).get("hours", 24)
        
        # 创建查询
        queries = create_period_search_queries(symbols, period_hours)
        
        return {"search_queries": queries}
    
    def _execute_searches_node(self, state: ResearchState) -> Dict[str, Any]:
        """执行搜索"""
        queries = state["search_queries"]
        results = {}
        errors = []
        
        # 对每个主题执行搜索
        for topic, topic_queries in queries.items():
            topic_results = []
            
            for query_config in topic_queries:
                try:
                    # 添加 exa_api_key 到查询配置
                    query_config_with_key = {**query_config, "exa_api_key": self.exa_api_key}
                    
                    # 根据主题选择合适的工具
                    if topic == "regulatory":
                        search_results = search_crypto_regulatory_news.invoke(query_config_with_key)
                    elif topic == "macro":
                        search_results = search_crypto_macro_news.invoke(query_config_with_key)
                    else:
                        search_results = search_crypto_market_news.invoke(query_config_with_key)
                    
                    topic_results.extend(search_results)
                except Exception as e:
                    errors.append(f"{topic} 搜索失败: {str(e)}")
            
            if topic_results:
                results[topic] = topic_results
        
        return {
            "search_results": results,
            "errors": errors
        }
    
    def _format_results_node(self, state: ResearchState) -> Dict[str, Any]:
        """格式化搜索结果"""
        results = state["search_results"]
        
        if not results:
            return {"formatted_results": "未找到相关搜索结果。"}
        
        topic_labels = {
            "market_news": "市场新闻",
            "regulatory": "监管政策",
            "macro": "宏观经济",
            "industry": "行业动态",
            "sentiment": "市场情绪"
        }
        
        formatted_parts = []
        
        for topic, topic_results in results.items():
            label = topic_labels.get(topic, topic)
            formatted_parts.append(f"\n## {label}\n")
            
            # 每个主题最多显示5条结果
            for i, result in enumerate(topic_results[:5], 1):
                formatted_parts.append(f"\n### {i}. 搜索结果\n{result}\n")
        
        formatted_text = "\n".join(formatted_parts)
        
        return {"formatted_results": formatted_text}
    
    def _generate_report_node(self, state: ResearchState) -> Dict[str, Any]:
        """生成研究报告"""
        period = state["period"]
        formatted_results = state["formatted_results"]
        symbols = state["symbols"]
        
        period_config = self.PERIOD_CONFIG.get(period, {})
        now = datetime.now()
        start_time = now - timedelta(hours=period_config.get("hours", 24))
        
        # 渲染 Prompt
        prompt_text = self.research_template.format(
            current_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            time_period=period,
            period_description=period_config.get("description", period),
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            symbols=symbols,
            search_results=formatted_results
        )
        
        # 调用 LLM
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=prompt_text)
        ]
        
        try:
            response = self.llm.invoke(messages)
            raw_text = response.content if isinstance(response.content, str) else str(response.content)
            
            # 尝试解析 JSON
            try:
                report = self.parser.parse(raw_text)
            except Exception:
                # 如果解析失败，返回基本结构
                report = {
                    "period": period,
                    "generated_at": now.isoformat(),
                    "market_overview": {
                        "summary": "报告生成失败",
                        "trend": "未知",
                        "sentiment": "中性"
                    },
                    "key_events": [],
                    "regulatory_updates": [],
                    "industry_news": [],
                    "market_sentiment": {},
                    "risk_alerts": [],
                    "trading_implications": {},
                    "raw_response": raw_text[:500]
                }
            
            return {"report": report}
            
        except Exception as e:
            return {
                "report": {
                    "period": period,
                    "generated_at": now.isoformat(),
                    "error": str(e)
                }
            }
    
    def run(
        self,
        period: str,
        symbols: list[str]
    ) -> Dict[str, Any]:
        """
        运行工作流
        
        Args:
            period: 时间周期
            symbols: 关注的币种列表
        
        Returns:
            包含报告的状态字典
        """
        initial_state = {
            "period": period,
            "symbols": symbols,
            "search_queries": {},
            "search_results": {},
            "formatted_results": "",
            "report": None,
            "errors": []
        }
        
        final_state = self.app.invoke(initial_state)
        return final_state
