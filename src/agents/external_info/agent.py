"""
外部信息收集 Agent

负责使用 Exa 搜索 API 收集市场信息，并汇总生成报告。
支持定时收集和异步执行。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from src.agents.common.utils import MarketInfoStore
from src.llm import LLMClientManager


class ExternalInfoAgent:
    """
    外部信息收集 Agent

    使用 Exa 搜索 API 收集加密货币市场信息，生成结构化报告。
    """

    def __init__(
        self,
        logger,
        llm_manager: LLMClientManager,
        exa_api_key: str | None = None,
        temperature: float = 0.1,
        symbols: list[str] | None = None,
        store_dir: str = "data/market_info",
        prompt_manager=None,
        interval_hours: float = 3.0,
    ):
        """
        初始化外部信息收集 Agent

        Args:
            logger: 日志记录器
            llm_manager: LLM 客户端管理器
            exa_api_key: Exa API 密钥（必须提供）
            temperature: LLM 温度参数
            symbols: 关注的币种列表
            store_dir: 市场信息存储目录
            prompt_manager: Prompt 管理器（用于加载提示词）
            interval_hours: 收集间隔（小时）
        """
        self.logger = logger
        self.llm_manager = llm_manager
        self.symbols = symbols or ["BTC", "ETH"]
        self.store = MarketInfoStore(store_dir)
        self.prompt_manager = prompt_manager
        self.interval_hours = interval_hours

        # 验证 Exa API 密钥
        if not exa_api_key:
            raise ValueError(
                "exa_api_key 参数不能为空。" "请在配置文件中设置 external_info_agent.exa_api_key"
            )
        self.exa_api_key = exa_api_key

        # 初始化 LLM
        self.llm = self.llm_manager.get_client(json_mode=True, temperature=temperature)

        # 加载 Prompt 模板
        self._load_prompts()

        # 初始化工作流
        self.workflow = None
        self._init_workflow()

    def _load_prompts(self):
        """加载 Prompt 模板"""
        if not self.prompt_manager:
            raise ValueError(
                "未提供 PromptManager，无法加载提示词。"
                "请在初始化 ExternalInfoAgent 时传入 prompt_manager 参数"
            )

        try:
            self.system_prompt = self.prompt_manager.get_research_system_prompt()
            research_template_content = self.prompt_manager.get_research_prompt_template()

            # 保存模板源字符串（用于传递给 workflow）
            self.research_template_source = research_template_content

            # 初始化 Jinja2 环境用于渲染模板
            from jinja2 import Template

            self.research_template = Template(
                research_template_content, autoescape=False, trim_blocks=True, lstrip_blocks=True
            )

            self.logger.print_info("✅ 已从 PromptManager 加载研究提示词")
        except Exception as e:
            self.logger.print_error(f"从 PromptManager 加载提示词失败: {e}")
            raise

    def _init_workflow(self):
        """初始化 LangGraph 工作流"""
        try:
            from src.agents.external_info.workflow import ExternalInfoWorkflow

            self.workflow = ExternalInfoWorkflow(
                llm=self.llm,
                system_prompt=self.system_prompt,
                research_template=self.research_template_source,
                exa_api_key=self.exa_api_key,
                logger=self.logger,
            )
            self.logger.print_info("✅ LangGraph 工作流初始化成功")
        except ImportError as e:
            self.logger.print_error(f"❌ LangGraph 工作流初始化失败: {e}")
            self.logger.print_error("请确保已安装 langchain-exa: pip install langchain-exa")
            raise

    def collect_and_save(self) -> str | None:
        """
        收集市场信息并保存报告

        Returns:
            保存的文件路径，失败返回 None
        """
        if not self.workflow:
            self.logger.print_error("❌ 工作流未初始化，无法收集信息")
            return None

        try:
            # 计算时间范围
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=self.interval_hours)

            self.logger.print_section(
                f"📡 收集市场信息 ({self.interval_hours} 小时)", style="bold cyan"
            )
            self.logger.print_info(
                f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M')} "
                f"至 {end_time.strftime('%Y-%m-%d %H:%M')}"
            )

            # 运行工作流
            final_state = self.workflow.run(
                interval_hours=self.interval_hours,
                symbols=self.symbols,
                start_time=start_time,
                end_time=end_time,
            )

            # 检查是否有错误
            if final_state.get("errors"):
                for error in final_state["errors"]:
                    self.logger.print_warning(error)

            # 获取报告
            report = final_state.get("report")
            if not report:
                self.logger.print_warning("未生成报告")
                return None

            # 保存报告
            file_path = self.store.save_report(report, start_time, end_time)
            self.logger.print_info(f"✅ 报告已保存: {file_path}")

            return file_path

        except Exception as e:
            self.logger.print_error(f"收集失败: {e}")
            import traceback

            traceback.print_exc()
            return None

    async def collect_and_save_async(self) -> str | None:
        """
        异步收集市场信息并保存报告

        Returns:
            保存的文件路径
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.collect_and_save)

    def get_latest_summary(self, symbols: list[str] | None = None, max_length: int = 2000) -> str:
        """
        获取最新的市场信息摘要

        Args:
            symbols: 关注的币种列表
            max_length: 最大长度

        Returns:
            格式化的摘要文本
        """
        return self.store.get_combined_summary(
            symbols=symbols or self.symbols, max_length=max_length
        )

    def get_report_status(self) -> dict[str, Any]:
        """获取报告状态"""
        return self.store.get_report_status()

    def get_latest_report_content(self) -> dict[str, Any] | None:
        """获取最新报告的完整内容"""
        report = self.store.load_latest_report()
        if not report:
            return None
        return report.get("data", {})


class ExternalInfoScheduler:
    """
    外部信息收集调度器

    负责定时运行外部信息收集任务。
    """

    def __init__(self, agent: ExternalInfoAgent, interval_hours: float = 3.0, logger=None):
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
                    self.logger.print_section("🔄 开始定时市场信息收集", style="bold blue")

                await self.agent.collect_and_save_async()

                if self.logger:
                    self.logger.print_info(
                        f"✅ 市场信息收集完成，下次收集在 {self.interval_hours} 小时后"
                    )

            except Exception as e:
                if self.logger:
                    self.logger.print_error(f"市场信息收集失败: {e}")

            await asyncio.sleep(self.interval_hours * 3600)

    async def start(self):
        """启动调度器"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_collection_loop())

        if self.logger:
            self.logger.print_info(f"📡 外部信息收集调度器已启动，间隔: {self.interval_hours} 小时")

    async def stop(self):
        """停止调度器"""
        import contextlib

        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        if self.logger:
            self.logger.print_info("📡 外部信息收集调度器已停止")

    def run_once(self) -> str | None:
        """立即执行一次收集"""
        return self.agent.collect_and_save()


def get_external_info_agent(
    logger,
    openai_api_base: str,
    openai_api_key: str,
    openai_model: str,
    exa_api_key: str | None = None,
    symbols: list[str] | None = None,
    store_dir: str = "data/market_info",
    prompt_manager=None,
    interval_hours: float = 3.0,
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
        prompt_manager: Prompt 管理器
        interval_hours: 收集间隔（小时）

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
        store_dir=store_dir,
        prompt_manager=prompt_manager,
        interval_hours=interval_hours,
    )
