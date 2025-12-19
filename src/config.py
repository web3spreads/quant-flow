"""
配置管理模块
负责加载和管理配置文件（config.yaml）和环境变量（.env）
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, List
from dotenv import load_dotenv

from src.fees import default_perp_fee_rates

# Trading fee constants (Hyperliquid, per doc Tier 0: taker 0.045% / maker 0.015%)
DEFAULT_PERP_FEE_RATES = default_perp_fee_rates()
FEE_RATE_PER_SIDE = DEFAULT_PERP_FEE_RATES.taker_rate
MAKER_FEE_RATE_PER_SIDE = DEFAULT_PERP_FEE_RATES.maker_rate


class Config:
    """配置管理类"""

    def __init__(
        self,
        config_path: str = "config.yaml",
        require_api_credentials: bool = True,
        env_file: str = None,
    ):
        """
        初始化配置

        Args:
            config_path: 配置文件路径
            require_api_credentials: 是否强制要求 OpenAI/Hyperliquid 凭证
            env_file: 环境变量文件路径（默认: .env）
        """
        # 加载环境变量
        if env_file:
            load_dotenv(dotenv_path=env_file)
            self._env_file = env_file
        else:
            # 优先检查环境变量 DOTENV_PATH，否则使用默认 .env
            env_path = os.getenv('DOTENV_PATH', '.env')
            load_dotenv(dotenv_path=env_path)
            self._env_file = env_path

        # 加载 YAML 配置
        self.config_path = Path(config_path)
        self.require_api_credentials = require_api_credentials
        self.config_data = self._load_yaml_config()

        # 初始化各配置项
        self._init_llm_config()
        self._init_openai_config()
        self._init_hyperliquid_config()
        self._init_trading_config()
        self._init_scheduler_config()
        self._init_data_config()
        self._init_indicators_config()
        self._init_agent_config()
        self._init_prompt_config()
        self._init_review_agent_config()
        self._init_external_info_agent_config()
        self._init_risk_config()
        self._init_logging_config()
        self._init_notifications_config()

    def _load_yaml_config(self) -> Dict[str, Any]:
        """加载 YAML 配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {self.config_path}\n"
                f"请将 config.yaml.example 复制为 config.yaml 并根据需要修改配置"
            )

        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _init_llm_config(self):
        """初始化 LLM 客户端配置"""
        llm_config = self.config_data.get("llm", {})
        
        # 客户端类型：优先从 YAML 配置读取，如果没有则从环境变量读取
        self.llm_client_type = llm_config.get("client_type") or os.getenv("LLM_CLIENT_TYPE", "langchain_openai")
        
        # 模型名称：优先从 YAML 配置读取，如果没有则从环境变量读取
        self.llm_model = llm_config.get("model") or os.getenv("OPENAI_MODEL", "deepseek-chat")
        
        # 基础参数（可选）
        self.llm_temperature = llm_config.get("temperature")
        self.llm_top_p = llm_config.get("top_p")
        self.llm_max_tokens = llm_config.get("max_tokens")
        
        # 额外参数（可选）
        self.llm_extra_body = llm_config.get("extra_body")
        
        # OpenAI / OpenAI-compatible 配置
        self.llm_openai_api_base = os.getenv("OPENAI_API_BASE")
        self.llm_openai_api_key = os.getenv("OPENAI_API_KEY")
        
        # Cloudflare 配置
        self.llm_cloudflare_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        self.llm_cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        
        # Google 配置
        self.llm_google_api_key = os.getenv("GOOGLE_API_KEY")
        
        # LiteLLM 配置
        self.llm_litellm_api_base = os.getenv("LITELLM_API_BASE")
        self.llm_litellm_api_key = os.getenv("LITELLM_API_KEY")
        
        # NVIDIA 配置
        self.llm_nvidia_api_key = os.getenv("NVIDIA_API_KEY")

    def _init_openai_config(self):
        """初始化 OpenAI API 配置"""
        self.openai_api_base = os.getenv(
            "OPENAI_API_BASE", "https://api.deepseek.com/v1"
        )
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.openai_model = os.getenv("OPENAI_MODEL", "deepseek-chat")

        if not self.openai_api_key and self.require_api_credentials:
            raise ValueError(
                "未设置 OPENAI_API_KEY 环境变量！\n"
                "请在 .env 文件中设置或使用环境变量"
            )

    def _init_hyperliquid_config(self):
        """初始化 Hyperliquid 配置"""
        self.hyperliquid_private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
        self.hyperliquid_account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")
        self.hyperliquid_testnet = (
            os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"
        )

        # 检查私钥配置
        if not self.hyperliquid_private_key and self.require_api_credentials:
            raise ValueError(
                "未设置 HYPERLIQUID_PRIVATE_KEY 环境变量！\n"
                "请在 .env 文件中设置钱包私钥"
            )

    def _init_trading_config(self):
        """初始化交易配置"""
        trading = self.config_data.get("trading", {})
        self.symbols: List[str] = trading.get("symbols", ["BTC", "ETH"])

        # 单笔交易金额上限（AI可自主决定实际金额，但不超过此上限）
        # 向后兼容：支持旧字段名 trade_amount
        self.max_trade_amount: float = float(
            trading.get("max_trade_amount", trading.get("trade_amount", 100))
        )
        # 保留旧字段名用于兼容性
        self.trade_amount: float = self.max_trade_amount

        self.take_profit_ratio: float = float(trading.get("take_profit_ratio", 0.05))
        self.stop_loss_ratio: float = float(trading.get("stop_loss_ratio", 0.02))
        self.max_positions: int = int(trading.get("max_positions", 2))
        self.limit_order_enabled: bool = trading.get("limit_order_enabled", False)

        # 最大杠杆倍数（AI可自主选择1到此上限之间的任何杠杆）
        # 向后兼容：支持旧字段名 default_leverage
        self.max_leverage: int = int(
            trading.get("max_leverage", trading.get("default_leverage", 10))
        )
        # 保留旧字段名用于兼容性
        self.default_leverage: int = self.max_leverage

    def _init_scheduler_config(self):
        """初始化调度器配置"""
        scheduler = self.config_data.get("scheduler", {})
        self.interval_minutes: int = int(scheduler.get("interval_minutes", 3))
        self.run_immediately: bool = scheduler.get("run_immediately", True)

    def _init_data_config(self):
        """初始化数据配置"""
        data = self.config_data.get("data", {})
        self.timeframe: str = data.get("timeframe", "15m")
        self.candles_limit: int = int(data.get("candles_limit", 100))

    def _init_indicators_config(self):
        """初始化技术指标配置"""
        indicators = self.config_data.get("indicators", {})
        self.ma_periods: List[int] = indicators.get("ma_periods", [7, 25, 99])
        self.rsi_period: int = int(indicators.get("rsi_period", 14))

        macd_params = indicators.get("macd_params", {})
        self.macd_fast: int = int(macd_params.get("fast", 12))
        self.macd_slow: int = int(macd_params.get("slow", 26))
        self.macd_signal: int = int(macd_params.get("signal", 9))

        bollinger_params = indicators.get("bollinger_params", {})
        self.bollinger_period: int = int(bollinger_params.get("period", 20))
        self.bollinger_std: float = float(bollinger_params.get("std_dev", 2))

    def _init_agent_config(self):
        """初始化 Agent 配置"""
        agent = self.config_data.get("agent", {})

        memory = agent.get("memory", {})
        self.memory_max_token_limit: int = int(memory.get("max_token_limit", 2000))
        self.memory_max_messages: int = int(memory.get("max_messages", 10))

        self.agent_temperature: float = float(agent.get("temperature", 0.1))
        self.agent_max_iterations: int = int(agent.get("max_iterations", 5))
        self.agent_timeout: int = int(agent.get("timeout", 60))

    def _init_prompt_config(self):
        """初始化 Prompt 配置"""
        prompt = self.config_data.get("prompt", {})
        self.prompt_set: str = prompt.get("set", "default")
        self.prompt_config_file: str = prompt.get("config_file", "prompts/prompts.yaml")

    def _init_review_agent_config(self):
        """初始化复盘 Agent 配置"""
        review = self.config_data.get("review_agent", {})
        self.review_enabled: bool = review.get("enabled", False)
        self.review_run_every_cycles: int = int(review.get("run_every_cycles", 3))
        self.review_lookback_decisions: int = int(review.get("lookback_decisions", 12))
        self.review_temperature: float = float(review.get("temperature", 0.05))
        # 如果配置文件中指定了 model，使用它；否则使用默认的 openai_model
        self.review_model: str = review.get("model", None)
        self.review_memory_file: str = review.get(
            "memory_file", "logs/review_memory.json"
        )
        # 每日日志目录（用于 LoRA 训练数据收集）
        self.review_daily_log_dir: str = review.get(
            "daily_log_dir", "logs/review_daily"
        )
        self.review_max_lessons: int = int(review.get("max_lessons", 30))
        self.review_min_confidence: float = float(review.get("min_confidence", 0.35))
        self.review_similarity_threshold: float = float(
            review.get("similarity_threshold", 0.5)
        )
        self.review_similarity_weights: Dict[str, float] = review.get(
            "similarity_weights", {}
        )
        self.review_confidence_decay_factor: float = float(
            review.get("confidence_decay_factor", 0.6)
        )
        self.review_similarity_method: str = review.get(
            "similarity_method", "cosine"
        )

    def _init_external_info_agent_config(self):
        """初始化外部信息收集 Agent 配置"""
        external_info = self.config_data.get("external_info_agent", {})
        self.external_info_enabled: bool = external_info.get("enabled", False)
        self.external_info_interval_hours: float = float(
            external_info.get("interval_hours", 3.0)
        )
        self.external_info_store_dir: str = external_info.get(
            "store_dir", "data/market_info"
        )
        # 从环境变量读取 Exa API 密钥
        self.external_info_exa_api_key: str = os.getenv("EXA_API_KEY")
        
        self.external_info_temperature: float = float(
            external_info.get("temperature", 0.1)
        )
        self.external_info_max_summary_length: int = int(
            external_info.get("max_summary_length", 2000)
        )
        self.external_info_periods: List[str] = external_info.get(
            "periods", ["daily", "weekly", "biweekly", "monthly"]
        )
        self.external_info_cleanup_days: int = int(
            external_info.get("cleanup_days", 30)
        )

    def _init_risk_config(self):
        """初始化风控配置"""
        risk = self.config_data.get("risk_management", {})
        self.circuit_breaker_enabled: bool = risk.get("circuit_breaker_enabled", True)
        self.circuit_breaker_threshold: float = float(
            risk.get("circuit_breaker_threshold", 0.1)
        )
        self.circuit_breaker_window: int = int(risk.get("circuit_breaker_window", 5))
        self.circuit_breaker_pause: int = int(risk.get("circuit_breaker_pause", 30))

    def _init_logging_config(self):
        """初始化日志配置"""
        logging_config = self.config_data.get("logging", {})
        self.console_color: bool = logging_config.get("console_color", True)
        self.show_full_prompt: bool = logging_config.get("show_full_prompt", True)
        self.show_chain_of_thought: bool = logging_config.get(
            "show_chain_of_thought", True
        )
        self.decision_log_format: str = logging_config.get(
            "decision_log_format", "json"
        )

        # 日志级别
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def _init_notifications_config(self):
        """初始化通知配置"""
        self.notifications = self.config_data.get("notifications", {"enabled": False})

    def validate(self):
        """验证配置的有效性"""
        errors = []

        # 验证交易金额上限
        if self.max_trade_amount <= 0:
            errors.append("max_trade_amount 必须大于 0")

        # 验证止盈止损比例
        if self.take_profit_ratio <= 0:
            errors.append("take_profit_ratio 必须大于 0")
        if self.stop_loss_ratio <= 0:
            errors.append("stop_loss_ratio 必须大于 0")

        # 验证交易对
        if not self.symbols:
            errors.append("至少需要配置一个交易对")

        # 验证时间周期
        valid_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
        if self.timeframe not in valid_timeframes:
            errors.append(f"timeframe 必须是以下之一: {valid_timeframes}")

        if errors:
            raise ValueError(
                "配置验证失败:\n" + "\n".join(f"- {err}" for err in errors)
            )

    def get_llm_client_config(self):
        """
        创建 LLM 客户端配置对象
        
        Returns:
            LLMClientConfig: LLM 客户端配置
        """
        from src.llm import LLMClientType, LLMClientConfig
        
        return LLMClientConfig(
            client_type=LLMClientType(self.llm_client_type),
            model=self.llm_model,
            temperature=self.llm_temperature,
            top_p=self.llm_top_p,
            max_tokens=self.llm_max_tokens,
            extra_body=self.llm_extra_body,
            openai_api_base=self.llm_openai_api_base,
            openai_api_key=self.llm_openai_api_key,
            cloudflare_account_id=self.llm_cloudflare_account_id,
            cloudflare_api_token=self.llm_cloudflare_api_token,
            google_api_key=self.llm_google_api_key,
            litellm_api_base=self.llm_litellm_api_base,
            litellm_api_key=self.llm_litellm_api_key,
            nvidia_api_key=self.llm_nvidia_api_key,
        )

    def __str__(self) -> str:
        """返回配置摘要（不包含敏感信息）"""
        # 确定运行模式
        mode = (
            "Hyperliquid 测试网 🧪"
            if self.hyperliquid_testnet
            else "Hyperliquid 主网 ⚠️"
        )

        return f"""
        === Quant Flow 配置摘要 ===
        LLM 客户端: {self.llm_client_type}
        模型: {self.llm_model}
        交易平台: Hyperliquid（永续合约）
        运行模式: {mode}
        交易对: {', '.join(self.symbols)}
        单笔交易金额上限: {self.max_trade_amount} USD
        最大杠杆倍数: {self.max_leverage}x
        止盈比例: {self.take_profit_ratio * 100}%
        止损比例: {self.stop_loss_ratio * 100}%
        决策间隔: {self.interval_minutes} 分钟
        K线周期: {self.timeframe}
        """


# 全局配置实例
_config: Config = None


def get_config(
    config_path: str = "config.yaml",
    require_api_credentials: bool = True,
    force_reload: bool = False,
    env_file: str = None,
) -> Config:
    """
    获取全局配置实例（单例模式）

    Args:
        config_path: 配置文件路径
        require_api_credentials: 是否强制要求 API 凭证
        force_reload: 是否强制重新加载配置
        env_file: 环境变量文件路径（默认: .env）

    Returns:
        Config 实例
    """
    global _config
    requested_path = Path(config_path)
    requested_env_file = env_file or os.getenv('DOTENV_PATH', '.env')
    if (
        _config is None
        or force_reload
        or requested_path != _config.config_path
        or _config.require_api_credentials != require_api_credentials
        or _config._env_file != requested_env_file
    ):
        _config = Config(
            config_path,
            require_api_credentials=require_api_credentials,
            env_file=env_file,
        )
        _config.validate()
    return _config


if __name__ == "__main__":
    # 测试配置加载
    try:
        config = get_config()
        print(config)
    except Exception as e:
        print(f"配置加载失败: {e}")
