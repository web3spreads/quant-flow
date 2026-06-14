"""
外部信息收集 LangGraph 工作流 -> 原生 Python 工作流优化版

使用 Pydantic AI 和原生 Python 顺序管道实现外部信息收集，
包含查询准备、搜索执行、结果格式化和报告生成四个阶段。
"""

from datetime import datetime
from typing import Any

from pydantic_ai import Agent

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

    使用原生 Python 实现的四阶段顺序工作流，消除 LangGraph 框架开销：
    1. prepare_queries - 准备搜索查询
    2. execute_searches - 执行 Exa 搜索
    3. format_results - 格式化搜索结果
    4. generate_report - 使用 Pydantic AI 生成结构化报告
    """

    def __init__(
        self,
        llm,
        system_prompt: str,
        research_template: str,
        exa_api_key: str,
        logger=None,
    ):
        """
        初始化工作流

        Args:
            llm: Pydantic AI Model 实例
            system_prompt: 系统提示
            research_template: 研究模板（Jinja2 格式字符串）
            exa_api_key: Exa API 密钥
            logger: 日志记录器
        """
        from src.llm.llm_client import wrap_llm_client

        self.llm = wrap_llm_client(llm)
        self.system_prompt = system_prompt
        self.logger = logger

        # 将模板字符串转换为 Jinja2 Template 对象
        from jinja2 import Template

        self.research_template = Template(research_template)

        self.exa_api_key = exa_api_key

    def _prepare_queries_node(self, state: ResearchState) -> dict[str, Any]:
        """准备搜索查询"""
        interval_hours = state["interval_hours"]
        symbols = state["symbols"]

        if self.logger:
            self.logger.print_info(f"📝 准备搜索查询 (间隔: {interval_hours} 小时)")

        # 创建查询
        queries = create_period_search_queries(symbols, int(interval_hours))

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
                    # 记录查询
                    query_text = query_config.get("query", "")
                    if self.logger:
                        self.logger.print_info(f"  📤 查询 [{topic}]: {query_text[:80]}...")

                    # 根据主题选择合适的工具
                    if topic == "regulatory":
                        search_results = search_crypto_regulatory_news(
                            query=query_text,
                            exa_api_key=self.exa_api_key,
                            num_results=5,
                            start_date=query_config.get("start_date"),
                            end_date=query_config.get("end_date"),
                        )
                    elif topic == "macro":
                        search_results = search_crypto_macro_news(
                            query=query_text,
                            exa_api_key=self.exa_api_key,
                            num_results=5,
                            start_date=query_config.get("start_date"),
                            end_date=query_config.get("end_date"),
                        )
                    else:
                        search_results = search_crypto_market_news(
                            query=query_text,
                            exa_api_key=self.exa_api_key,
                            num_results=5,
                            start_date=query_config.get("start_date"),
                            end_date=query_config.get("end_date"),
                        )

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
            self.logger.print_info("🤖 使用 Pydantic AI 生成报告...")

        # 渲染 Prompt（使用 Jinja2）
        prompt_text = self.research_template.render(
            current_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
            interval_hours=interval_hours,
            interval_description=f"过去 {interval_hours} 小时",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            symbols=symbols,
            search_results=formatted_results,
        )

        try:
            # 使用 Pydantic AI 运行报告 Agent
            agent = Agent(self.llm, system_prompt=self.system_prompt)
            response = agent.run_sync(prompt_text)
            # response.output 理论上可能为 None（模型返回空），需防空以免下游解析抛错
            raw_text = response.output
            if not isinstance(raw_text, str):
                raw_text = "" if raw_text is None else str(raw_text)

            # 尝试解析 JSON
            from src.agent.helpers import extract_json_from_text

            report = extract_json_from_text(raw_text)

            if not report:
                raise ValueError("未提取到有效的 JSON 报告结构")

            if self.logger:
                self.logger.print_info("✅ 报告生成成功")

            return {"report": report}

        except Exception as e:
            if self.logger:
                self.logger.print_error(f"❌ LLM 报告生成或解析失败: {e}")

            # 异常时回退到默认基本格式
            report = {
                "interval_hours": interval_hours,
                "generated_at": end_time.isoformat(),
                "market_overview": {
                    "summary": f"报告生成失败: {e}",
                    "trend": "未知",
                    "sentiment": "中性",
                },
                "key_events": [],
                "regulatory_updates": [],
                "industry_news": [],
                "market_sentiment": {},
                "risk_alerts": [],
                "trading_implications": {},
                "raw_response": raw_text[:500] if "raw_text" in locals() else "",
            }
            return {"report": report}

    def run(
        self, interval_hours: float, symbols: list[str], start_time: datetime, end_time: datetime
    ) -> dict[str, Any]:
        """
        运行工作流 (Python 原生管道)
        """
        state = {
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

        # Step 1: Prepare queries
        queries_res = self._prepare_queries_node(state)
        state.update(queries_res)

        # Step 2: Execute searches
        searches_res = self._execute_searches_node(state)
        state.update(searches_res)

        # Step 3: Format results
        format_res = self._format_results_node(state)
        state.update(format_res)

        # Step 4: Generate report
        report_res = self._generate_report_node(state)
        state.update(report_res)

        return state
