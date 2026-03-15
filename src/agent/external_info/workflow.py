"""
外部信息收集 LangGraph 工作流

使用 StateGraph 实现的外部信息收集工作流，
包含查询准备、搜索执行、结果格式化和报告生成四个阶段。
"""

from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from src.agent.external_info.state import ResearchState
from src.agent.external_info.tools import (
    create_period_search_queries,
    search_crypto_macro_news,
    search_crypto_market_news,
    search_crypto_regulatory_news,
)


class ExternalInfoWorkflow:
    """
    外部信息收集工作流

    使用 LangGraph StateGraph 实现的四阶段工作流：
    1. prepare_queries - 准备搜索查询
    2. execute_searches - 执行 Exa 搜索
    3. format_results - 格式化搜索结果
    4. generate_report - 使用 LLM 生成结构化报告
    """

    def __init__(
        self,
        llm: ChatOpenAI,
        system_prompt: str,
        research_template: str,
        exa_api_key: str,
        logger=None,
    ):
        """
        初始化工作流

        Args:
            llm: LangChain LLM 实例
            system_prompt: 系统提示
            research_template: 研究模板（Jinja2 格式字符串）
            exa_api_key: Exa API 密钥
            logger: 日志记录器
        """
        self.llm = llm
        self.system_prompt = system_prompt
        self.logger = logger

        # 将模板字符串转换为 Jinja2 Template 对象
        from jinja2 import Template

        self.research_template = Template(research_template)

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

    def _prepare_queries_node(self, state: ResearchState) -> dict[str, Any]:
        """准备搜索查询"""
        interval_hours = state["interval_hours"]
        symbols = state["symbols"]

        if self.logger:
            self.logger.print_info(f"📝 准备搜索查询 (间隔: {interval_hours} 小时)")

        # 创建查询
        queries = create_period_search_queries(symbols, interval_hours)

        if self.logger:
            total_queries = sum(len(q) for q in queries.values())
            self.logger.print_info(f"✅ 生成 {total_queries} 个搜索查询")

        return {"search_queries": queries}

    def _execute_searches_node(self, state: ResearchState) -> dict[str, Any]:
        """执行搜索"""
        queries = state["search_queries"]
        results = {}
        errors = []

        if self.logger:
            self.logger.print_info("🔍 开始执行 Exa 搜索...")

        # 对每个主题执行搜索
        for topic, topic_queries in queries.items():
            topic_results = []

            for query_config in topic_queries:
                try:
                    # 添加 exa_api_key 到查询配置
                    query_config_with_key = {**query_config, "exa_api_key": self.exa_api_key}

                    # 记录查询
                    if self.logger:
                        self.logger.print_info(
                            f"  📤 查询 [{topic}]: {query_config.get('query', '')[:80]}..."
                        )

                    # 根据主题选择合适的工具
                    if topic == "regulatory":
                        search_results = search_crypto_regulatory_news.invoke(query_config_with_key)
                    elif topic == "macro":
                        search_results = search_crypto_macro_news.invoke(query_config_with_key)
                    else:
                        search_results = search_crypto_market_news.invoke(query_config_with_key)

                    # 记录结果
                    if self.logger:
                        result_count = (
                            len(search_results) if isinstance(search_results, list) else 1
                        )
                        self.logger.print_info(f"  📥 收到 {result_count} 条结果")

                    topic_results.extend(search_results)
                except Exception as e:
                    error_msg = f"{topic} 搜索失败: {str(e)}"
                    errors.append(error_msg)
                    if self.logger:
                        self.logger.print_warning(f"  ⚠️  {error_msg}")

            if topic_results:
                results[topic] = topic_results

        if self.logger:
            total_results = sum(len(r) for r in results.values())
            self.logger.print_info(f"✅ 搜索完成，共收到 {total_results} 条结果")

        return {"search_results": results, "errors": errors}

    def _format_results_node(self, state: ResearchState) -> dict[str, Any]:
        """格式化搜索结果"""
        results = state["search_results"]

        if not results:
            return {"formatted_results": "未找到相关搜索结果。"}

        topic_labels = {
            "market_news": "市场新闻",
            "regulatory": "监管政策",
            "macro": "宏观经济",
            "industry": "行业动态",
            "sentiment": "市场情绪",
        }

        formatted_parts = []

        for topic, topic_results in results.items():
            label = topic_labels.get(topic, topic)
            formatted_parts.append(f"\n## {label}\n")

            # 每个主题最多显示5条结果
            for i, result in enumerate(topic_results[:5], 1):
                formatted_parts.append(f"\n### {i}. 搜索结果\n{result}\n")

        formatted_text = "\n".join(formatted_parts)

        if self.logger:
            self.logger.print_info(f"✅ 格式化完成，内容长度: {len(formatted_text)} 字符")

        return {"formatted_results": formatted_text}

    def _generate_report_node(self, state: ResearchState) -> dict[str, Any]:
        """生成研究报告"""
        interval_hours = state["interval_hours"]
        formatted_results = state["formatted_results"]
        symbols = state["symbols"]
        start_time = state["start_time"]
        end_time = state["end_time"]

        if self.logger:
            self.logger.print_info("🤖 使用 LLM 生成报告...")

        # 渲染 Prompt（使用 Jinja2）
        prompt_text = self.research_template.render(
            current_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
            interval_hours=interval_hours,
            interval_description=f"过去 {interval_hours} 小时",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            symbols=symbols,
            search_results=formatted_results,
        )

        # 调用 LLM
        messages = [SystemMessage(content=self.system_prompt), HumanMessage(content=prompt_text)]

        try:
            response = self.llm.invoke(messages)
            raw_text = (
                response.content if isinstance(response.content, str) else str(response.content)
            )

            # 尝试解析 JSON
            try:
                report = self.parser.parse(raw_text)
                if self.logger:
                    self.logger.print_info("✅ 报告生成成功")
            except Exception as e:
                # 如果解析失败，返回基本结构
                if self.logger:
                    self.logger.print_warning(f"⚠️  JSON 解析失败: {e}")

                report = {
                    "interval_hours": interval_hours,
                    "generated_at": end_time.isoformat(),
                    "market_overview": {
                        "summary": "报告生成失败",
                        "trend": "未知",
                        "sentiment": "中性",
                    },
                    "key_events": [],
                    "regulatory_updates": [],
                    "industry_news": [],
                    "market_sentiment": {},
                    "risk_alerts": [],
                    "trading_implications": {},
                    "raw_response": raw_text[:500],
                }

            return {"report": report}

        except Exception as e:
            if self.logger:
                self.logger.print_error(f"❌ LLM 调用失败: {e}")

            return {
                "report": {
                    "interval_hours": interval_hours,
                    "generated_at": end_time.isoformat(),
                    "error": str(e),
                }
            }

    def run(
        self, interval_hours: float, symbols: list[str], start_time: datetime, end_time: datetime
    ) -> dict[str, Any]:
        """
        运行工作流

        Args:
            interval_hours: 时间间隔（小时）
            symbols: 关注的币种列表
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            包含报告的状态字典
        """
        initial_state = {
            "interval_hours": interval_hours,
            "symbols": symbols,
            "start_time": start_time,
            "end_time": end_time,
            "search_queries": {},
            "search_results": {},
            "formatted_results": "",
            "report": None,
            "errors": [],
        }

        final_state = self.app.invoke(initial_state)
        return final_state
