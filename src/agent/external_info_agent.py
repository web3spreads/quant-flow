"""
外部信息收集 Agent
负责使用 Exa 搜索 API 收集市场信息，并汇总生成报告
"""

import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from jinja2 import Environment, FileSystemLoader

try:
    from exa_py import Exa
    EXA_AVAILABLE = True
except ImportError:
    EXA_AVAILABLE = False
    Exa = None

from src.agent.market_info_store import MarketInfoStore, TimePeriod
from src.utils.logger import TradingLogger


class ExternalInfoAgent:
    """
    外部信息收集 Agent
    使用 Exa 搜索 API 收集加密货币市场信息，生成结构化报告
    """

    # 搜索查询模板（针对不同主题）
    SEARCH_QUERIES = {
        "market_news": [
            "cryptocurrency market news {date_range}",
            "crypto Bitcoin Ethereum price analysis {date_range}",
            "加密货币市场分析 {date_range}",
        ],
        "regulatory": [
            "cryptocurrency regulation policy {date_range}",
            "crypto SEC regulation news {date_range}",
            "加密货币监管政策 {date_range}",
        ],
        "macro": [
            "Federal Reserve interest rate crypto impact {date_range}",
            "inflation economic data cryptocurrency {date_range}",
            "美联储利率 加密货币影响 {date_range}",
        ],
        "industry": [
            "blockchain technology upgrade news {date_range}",
            "crypto exchange news {date_range}",
            "DeFi protocol news {date_range}",
        ],
        "sentiment": [
            "crypto fear greed index {date_range}",
            "Bitcoin whale activity {date_range}",
            "cryptocurrency institutional investment {date_range}",
        ]
    }

    # 时间周期配置
    PERIOD_CONFIG = {
        TimePeriod.DAILY: {
            "hours": 24,
            "description": "过去24小时",
            "queries_per_topic": 1,
            "results_per_query": 5
        },
        TimePeriod.WEEKLY: {
            "hours": 168,  # 7 * 24
            "description": "过去一周",
            "queries_per_topic": 2,
            "results_per_query": 5
        },
        TimePeriod.BIWEEKLY: {
            "hours": 336,  # 14 * 24
            "description": "过去两周",
            "queries_per_topic": 2,
            "results_per_query": 5
        },
        TimePeriod.MONTHLY: {
            "hours": 720,  # 30 * 24
            "description": "过去一个月",
            "queries_per_topic": 2,
            "results_per_query": 8
        }
    }

    def __init__(
        self,
        logger: TradingLogger,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str,
        exa_api_key: Optional[str] = None,
        temperature: float = 0.1,
        symbols: Optional[List[str]] = None,
        store_dir: str = "data/market_info",
        prompts_dir: str = "prompts"
    ):
        """
        初始化外部信息收集 Agent

        Args:
            logger: 日志记录器
            openai_api_base: OpenAI API 基础 URL
            openai_api_key: OpenAI API 密钥
            openai_model: OpenAI 模型名称
            exa_api_key: Exa API 密钥（如未提供则从环境变量获取）
            temperature: LLM 温度参数
            symbols: 关注的币种列表
            store_dir: 市场信息存储目录
            prompts_dir: Prompt 模板目录
        """
        self.logger = logger
        self.symbols = symbols or ["BTC", "ETH"]
        self.store = MarketInfoStore(store_dir)

        # 初始化 Exa 客户端
        self.exa_api_key = exa_api_key or os.getenv("EXA_API_KEY")
        self.exa_client = None

        if EXA_AVAILABLE and self.exa_api_key:
            try:
                self.exa_client = Exa(api_key=self.exa_api_key)
                self.logger.print_info("✅ Exa 搜索客户端初始化成功")
            except Exception as e:
                self.logger.print_warning(f"Exa 客户端初始化失败: {e}")
        elif not EXA_AVAILABLE:
            self.logger.print_warning("exa_py 未安装，外部信息收集功能将受限")
        else:
            self.logger.print_warning("未配置 EXA_API_KEY，外部信息收集功能将受限")

        # 初始化 LLM
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
            model_kwargs={"response_format": {"type": "json_object"}}
        )

        # 加载 Prompt 模板
        self.prompts_dir = Path(prompts_dir)
        self._load_prompts()

    def _load_prompts(self):
        """加载 Prompt 模板"""
        # 初始化 Jinja2 环境
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # 加载系统提示
        system_prompt_path = self.prompts_dir / "default" / "research_system_prompt.md"
        if system_prompt_path.exists():
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt = f.read()
        else:
            self.system_prompt = self._get_default_system_prompt()

        # 加载研究提示模板
        template_path = self.prompts_dir / "default" / "research_prompt_template.md"
        if template_path.exists():
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()
                self.research_template = self.jinja_env.from_string(template_content)
        else:
            self.research_template = self.jinja_env.from_string(
                self._get_default_template()
            )

    def _get_default_system_prompt(self) -> str:
        """获取默认系统提示"""
        return """你是一名专业的加密货币市场研究分析师，负责收集和汇总市场信息，为交易决策提供参考。
请保持客观中立，聚焦于可能影响市场的重要信息。输出需要是结构化的 JSON 格式。"""

    def _get_default_template(self) -> str:
        """获取默认研究模板"""
        return """# 市场信息研究任务

当前时间: {{ current_time }}
研究周期: {{ time_period }}（{{ period_description }}）

搜索结果:
{{ search_results }}

请基于上述搜索结果，生成一份结构化的市场信息报告（JSON 格式）。"""

    def _search_exa(
        self,
        query: str,
        num_results: int = 5,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        使用 Exa 执行搜索

        Args:
            query: 搜索查询
            num_results: 返回结果数量
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            搜索结果列表
        """
        if not self.exa_client:
            return []

        try:
            # 构建搜索参数
            search_params = {
                "query": query,
                "num_results": num_results,
                "use_autoprompt": True,
                "type": "auto"
            }

            # 添加日期过滤
            if start_date:
                search_params["start_published_date"] = start_date.strftime("%Y-%m-%d")
            if end_date:
                search_params["end_published_date"] = end_date.strftime("%Y-%m-%d")

            # 执行搜索并获取内容
            results = self.exa_client.search_and_contents(
                **search_params,
                text={"max_characters": 1000}
            )

            # 解析结果
            parsed_results = []
            for result in results.results:
                parsed_results.append({
                    "title": result.title,
                    "url": result.url,
                    "text": result.text[:500] if result.text else "",
                    "published_date": getattr(result, "published_date", None),
                    "author": getattr(result, "author", None)
                })

            return parsed_results

        except Exception as e:
            self.logger.print_warning(f"Exa 搜索失败: {e}")
            return []

    def _collect_search_results(
        self,
        period: TimePeriod
    ) -> str:
        """
        收集指定时间周期的搜索结果

        Args:
            period: 时间周期

        Returns:
            格式化的搜索结果文本
        """
        config = self.PERIOD_CONFIG[period]
        end_date = datetime.now()
        start_date = end_date - timedelta(hours=config["hours"])

        all_results = []
        date_range = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"

        # 对每个主题执行搜索
        for topic, queries in self.SEARCH_QUERIES.items():
            topic_results = []

            # 选择要执行的查询数量
            selected_queries = queries[:config["queries_per_topic"]]

            for query_template in selected_queries:
                query = query_template.format(date_range=date_range)

                # 添加币种关键词
                if self.symbols:
                    for symbol in self.symbols[:2]:  # 只添加前两个币种
                        enhanced_query = f"{query} {symbol}"
                        results = self._search_exa(
                            enhanced_query,
                            num_results=config["results_per_query"],
                            start_date=start_date,
                            end_date=end_date
                        )
                        topic_results.extend(results)
                else:
                    results = self._search_exa(
                        query,
                        num_results=config["results_per_query"],
                        start_date=start_date,
                        end_date=end_date
                    )
                    topic_results.extend(results)

            if topic_results:
                all_results.append({
                    "topic": topic,
                    "results": topic_results
                })

        # 格式化搜索结果
        return self._format_search_results(all_results)

    def _format_search_results(self, results: List[Dict[str, Any]]) -> str:
        """
        格式化搜索结果为文本

        Args:
            results: 搜索结果列表

        Returns:
            格式化的文本
        """
        if not results:
            return "未找到相关搜索结果。"

        topic_labels = {
            "market_news": "市场新闻",
            "regulatory": "监管政策",
            "macro": "宏观经济",
            "industry": "行业动态",
            "sentiment": "市场情绪"
        }

        formatted_parts = []

        for topic_data in results:
            topic = topic_data["topic"]
            topic_results = topic_data["results"]
            label = topic_labels.get(topic, topic)

            formatted_parts.append(f"\n## {label}\n")

            for i, result in enumerate(topic_results[:5], 1):  # 每个主题最多5条
                title = result.get("title", "无标题")
                text = result.get("text", "")[:300]  # 限制文本长度
                url = result.get("url", "")
                pub_date = result.get("published_date", "")

                formatted_parts.append(f"""
### {i}. {title}
- **来源**: {url}
- **发布时间**: {pub_date or '未知'}
- **摘要**: {text}
""")

        return "".join(formatted_parts)

    def _generate_report(
        self,
        period: TimePeriod,
        search_results: str
    ) -> Dict[str, Any]:
        """
        使用 LLM 生成研究报告

        Args:
            period: 时间周期
            search_results: 搜索结果文本

        Returns:
            结构化的报告数据
        """
        config = self.PERIOD_CONFIG[period]
        now = datetime.now()
        start_time = now - timedelta(hours=config["hours"])

        # 渲染 Prompt
        context = {
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "time_period": period.value,
            "period_description": config["description"],
            "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "symbols": self.symbols,
            "search_results": search_results
        }

        prompt = self.research_template.render(context)

        # 调用 LLM
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=prompt)
        ]

        try:
            response = self.llm.invoke(messages)
            raw_text = response.content if isinstance(response.content, str) else str(response.content)

            # 解析 JSON 响应
            report = self._parse_json_response(raw_text)

            if report:
                return report
            else:
                # 如果解析失败，返回基本结构
                return {
                    "period": period.value,
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

        except Exception as e:
            self.logger.print_error(f"LLM 调用失败: {e}")
            return {
                "period": period.value,
                "generated_at": now.isoformat(),
                "error": str(e)
            }

    def _parse_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        """
        解析 LLM 的 JSON 响应

        Args:
            text: LLM 响应文本

        Returns:
            解析后的字典，失败返回 None
        """
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 块
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # 尝试提取 ```json 代码块
        code_block_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1))
            except json.JSONDecodeError:
                pass

        return None

    def collect_and_save(
        self,
        periods: Optional[List[TimePeriod]] = None
    ) -> Dict[str, str]:
        """
        收集市场信息并保存报告

        Args:
            periods: 要收集的时间周期列表（默认为所有周期）

        Returns:
            各周期保存的文件路径
        """
        if periods is None:
            periods = list(TimePeriod)

        saved_files = {}

        for period in periods:
            try:
                self.logger.print_section(
                    f"📡 收集 {self.PERIOD_CONFIG[period]['description']} 市场信息",
                    style="bold cyan"
                )

                # 收集搜索结果
                search_results = self._collect_search_results(period)

                if not search_results or search_results == "未找到相关搜索结果。":
                    self.logger.print_warning(f"{period.value} 周期未找到搜索结果")
                    continue

                # 生成报告
                self.logger.print_info("正在使用 AI 生成报告...")
                report = self._generate_report(period, search_results)

                # 保存报告
                file_path = self.store.save_report(period, report)
                saved_files[period.value] = file_path
                self.logger.print_info(f"✅ 报告已保存: {file_path}")

            except Exception as e:
                self.logger.print_error(f"{period.value} 周期收集失败: {e}")

        return saved_files

    async def collect_and_save_async(
        self,
        periods: Optional[List[TimePeriod]] = None
    ) -> Dict[str, str]:
        """
        异步收集市场信息并保存报告

        Args:
            periods: 要收集的时间周期列表

        Returns:
            各周期保存的文件路径
        """
        # 在后台线程中运行同步方法
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.collect_and_save,
            periods
        )

    def get_latest_summary(
        self,
        symbols: Optional[List[str]] = None,
        max_length: int = 2000
    ) -> str:
        """
        获取最新的市场信息摘要

        Args:
            symbols: 关注的币种列表
            max_length: 最大长度

        Returns:
            格式化的摘要文本
        """
        return self.store.get_combined_summary(
            symbols=symbols or self.symbols,
            max_length=max_length
        )

    def get_report_status(self) -> Dict[str, Dict[str, Any]]:
        """
        获取报告状态

        Returns:
            各周期报告的状态信息
        """
        return self.store.get_report_status()


class ExternalInfoScheduler:
    """
    外部信息收集调度器
    负责定时运行外部信息收集任务
    """

    def __init__(
        self,
        agent: ExternalInfoAgent,
        interval_hours: float = 3.0,
        logger: Optional[TradingLogger] = None
    ):
        """
        初始化调度器

        Args:
            agent: 外部信息收集 Agent
            interval_hours: 收集间隔（小时）
            logger: 日志记录器
        """
        self.agent = agent
        self.interval_hours = interval_hours
        self.logger = logger
        self._running = False
        self._task = None

    async def _run_collection_loop(self):
        """运行收集循环"""
        while self._running:
            try:
                if self.logger:
                    self.logger.print_section(
                        "🔄 开始定时市场信息收集",
                        style="bold blue"
                    )

                # 执行收集
                await self.agent.collect_and_save_async()

                if self.logger:
                    self.logger.print_info(
                        f"✅ 市场信息收集完成，下次收集在 {self.interval_hours} 小时后"
                    )

            except Exception as e:
                if self.logger:
                    self.logger.print_error(f"市场信息收集失败: {e}")

            # 等待下一次收集
            await asyncio.sleep(self.interval_hours * 3600)

    async def start(self):
        """启动调度器"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_collection_loop())

        if self.logger:
            self.logger.print_info(
                f"📡 外部信息收集调度器已启动，间隔: {self.interval_hours} 小时"
            )

    async def stop(self):
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self.logger:
            self.logger.print_info("📡 外部信息收集调度器已停止")

    def run_once(self) -> Dict[str, str]:
        """
        立即执行一次收集（同步方法）

        Returns:
            保存的文件路径
        """
        return self.agent.collect_and_save()


def get_external_info_agent(
    logger: TradingLogger,
    openai_api_base: str,
    openai_api_key: str,
    openai_model: str,
    exa_api_key: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    store_dir: str = "data/market_info"
) -> ExternalInfoAgent:
    """
    获取外部信息收集 Agent 实例

    Args:
        logger: 日志记录器
        openai_api_base: OpenAI API 基础 URL
        openai_api_key: OpenAI API 密钥
        openai_model: OpenAI 模型名称
        exa_api_key: Exa API 密钥
        symbols: 关注的币种列表
        store_dir: 存储目录

    Returns:
        ExternalInfoAgent 实例
    """
    return ExternalInfoAgent(
        logger=logger,
        openai_api_base=openai_api_base,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        exa_api_key=exa_api_key,
        symbols=symbols,
        store_dir=store_dir
    )
