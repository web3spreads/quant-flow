"""
外部信息收集 Agent
负责使用 Exa 搜索 API 收集市场信息，并汇总生成报告
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path

from langchain_openai import ChatOpenAI
from jinja2 import Environment, FileSystemLoader

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
        prompt_manager=None
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
            prompt_manager: Prompt 管理器（用于加载提示词）
        """
        self.logger = logger
        self.symbols = symbols or ["BTC", "ETH"]
        self.store = MarketInfoStore(store_dir)
        self.prompt_manager = prompt_manager

        # 保存 Exa API 密钥（必须通过参数传入）
        if not exa_api_key:
            raise ValueError(
                "exa_api_key 参数不能为空。"
                "请在配置文件中设置 external_info_agent.exa_api_key"
            )
        self.exa_api_key = exa_api_key

        # 初始化 LLM
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
            model_kwargs={"response_format": {"type": "json_object"}}
        )

        # 加载 Prompt 模板
        self._load_prompts()
        
        # 初始化 LangChain 工作流
        self.workflow = None
        try:
            from src.agent.external_info.workflow import ExternalInfoWorkflow
            self.workflow = ExternalInfoWorkflow(
                llm=self.llm,
                system_prompt=self.system_prompt,
                research_template=self.research_template.template,
                exa_api_key=self.exa_api_key
            )
            self.logger.print_info("✅ LangChain 工作流初始化成功")
        except ImportError as e:
            self.logger.print_error(f"❌ LangChain 工作流初始化失败: {e}")
            self.logger.print_error("请确保已安装 langchain-exa: pip install langchain-exa")
            raise

    def _load_prompts(self):
        """加载 Prompt 模板"""
        if not self.prompt_manager:
            raise ValueError(
                "未提供 PromptManager，无法加载提示词。"
                "请在初始化 ExternalInfoAgent 时传入 prompt_manager 参数"
            )
        
        # 使用 PromptManager 加载提示词
        try:
            self.system_prompt = self.prompt_manager.get_research_system_prompt()
            research_template_content = self.prompt_manager.get_research_prompt_template()
            
            # 初始化 Jinja2 环境用于渲染模板
            from jinja2 import Template
            self.research_template = Template(
                research_template_content,
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True
            )
            
            self.logger.print_info("✅ 已从 PromptManager 加载研究提示词")
        except Exception as e:
            self.logger.print_error(f"从 PromptManager 加载提示词失败: {e}")
            raise

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

        if not self.workflow:
            self.logger.print_error("❌ 工作流未初始化，无法收集信息")
            return {}

        saved_files = {}

        for period in periods:
            try:
                self.logger.print_section(
                    f"📡 收集 {self.PERIOD_CONFIG[period]['description']} 市场信息",
                    style="bold cyan"
                )

                # 运行工作流
                final_state = self.workflow.run(
                    period=period.value,
                    symbols=self.symbols
                )

                # 检查是否有错误
                if final_state.get("errors"):
                    for error in final_state["errors"]:
                        self.logger.print_warning(error)

                # 获取报告
                report = final_state.get("report")
                if not report:
                    self.logger.print_warning(f"{period.value} 周期未生成报告")
                    continue

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
