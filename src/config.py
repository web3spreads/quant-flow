"""
配置模块：从 config.yaml 与环境变量加载运行配置。

设计原则：
- 配置能省则省——所有键都有安全默认值，最小可运行配置只需交易对与 LLM 端点；
- 敏感信息（私钥、API Key）只从环境变量读取，绝不写入 YAML；
- 配置对象为不可变 dataclass，加载后全程只读，避免运行期被意外篡改。

环境变量：
    HYPERLIQUID_PRIVATE_KEY      钱包私钥（必填）
    HYPERLIQUID_ACCOUNT_ADDRESS  主钱包地址（API 钱包模式选填）
    HYPERLIQUID_TESTNET          是否测试网（默认 true，主网需显式设 false）
    LLM_API_KEY                  LLM API 密钥（兼容 OPENAI_API_KEY）
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 未配置 protections 段时的默认保护链（显式配置 protections: [] 可全部关闭）
DEFAULT_PROTECTIONS: list[dict[str, Any]] = [
    {"name": "max_drawdown", "max_drawdown_pct": 0.10, "pause_hours": 4},
    {"name": "daily_loss", "max_daily_loss_pct": 0.05, "pause_hours": 4},
    {"name": "consecutive_loss", "max_consecutive_losses": 5, "per_symbol": True, "pause_hours": 4},
    {"name": "position_timeout", "max_position_hours": 48},
]


@dataclass(frozen=True)
class LLMConfig:
    """LLM 配置：任意 OpenAI 兼容端点（DeepSeek/OpenAI/本地部署等）。"""

    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    temperature: float = 0.2
    timeout: float = 120.0
    api_key: str = ""


@dataclass(frozen=True)
class ExchangeConfig:
    """交易所配置：全部来自环境变量。"""

    private_key: str = ""
    account_address: str | None = None
    testnet: bool = True


@dataclass(frozen=True)
class TradingConfig:
    """交易配置：永续与网格共用的账户级参数。"""

    symbols: tuple[str, ...] = ("BTC",)
    perp_enabled: bool = True
    grid_enabled: bool = False
    max_trade_amount: float = 100.0  # 单笔投入上限（USD）
    max_leverage: int = 5
    max_positions: int = 3
    take_profit_ratio: float = 0.05  # 止盈比例（开仓价 ±5%）
    stop_loss_ratio: float = 0.02  # 止损比例（开仓价 ∓2%）
    min_confidence: float = 0.6  # 永续开仓最低置信度
    timeframe: str = "1h"  # 决策 K 线周期
    candles_limit: int = 100  # 单次拉取 K 线数量
    timeframe_offset: float = 2.0  # K 线收盘后等待秒数（确保数据可取）
    min_throttle_secs: float = 30.0  # 两次决策最小间隔
    run_immediately: bool = True  # 启动时立即执行一轮


@dataclass(frozen=True)
class GridConfig:
    """网格配置：安全机制全部默认启用，仅暴露必要的数值旋钮。"""

    interval_minutes: int = 5  # 网格决策周期（分钟）
    width_min_pct: float = 0.02  # 网格宽度下限
    width_max_pct: float = 0.15  # 网格宽度上限
    width_fallback_pct: float = 0.05  # 数据异常时的回退宽度
    ai_blend_weight: float = 0.35  # AI 宽度与市场数据的融合权重
    force_neutral: bool = True  # 强制中性网格（忽略 AI 方向，消除反手亏损）
    min_grid_num: int = 3  # 自适应仓位最少格数
    max_position_notional_usd: float = 0.0  # 库存硬上限（USD 名义额，0=关闭）
    halt_below_usd: float = 0.0  # 净值停机线（低于此值且无持仓跳过周期，0=关闭）
    trend_filter_enabled: bool = True  # 多周期强势一致时暂停加仓
    trend_filter_min_votes: int = 3  # 强势周期票数阈值
    trend_filter_timeframes: tuple[str, ...] = ("15m", "1h", "4h", "1d")
    trend_confirm_cycles: int = 2  # 连续 N 周期同向确认才暂停（迟滞去抖）
    flatten_adverse: bool = True  # 强趋势中减掉逆势库存
    flatten_min_cycles: int = 3  # 平逆势库存需更多连续确认（暂停先行、平仓靠后）
    llm_failure_alert_cycles: int = 6  # LLM 连续失败 N 周期告警（0=关闭）
    llm_fallback_rebuild_cycles: int = 12  # 空转 N 周期后纯市场数据兜底重建（0=关闭）
    barrier: dict[str, Any] = field(default_factory=dict)  # Triple Barrier 覆盖项


@dataclass(frozen=True)
class Config:
    """聚合配置根对象。"""

    llm: LLMConfig
    exchange: ExchangeConfig
    trading: TradingConfig
    grid: GridConfig
    protections: list[dict[str, Any]]

    @classmethod
    def load(cls, config_path: str = "config.yaml", env_file: str | None = None) -> "Config":
        """
        加载配置：.env → 环境变量 → config.yaml（可缺省，缺省即全默认值）。

        Args:
            config_path: YAML 配置文件路径，不存在时使用全部默认值
            env_file: .env 文件路径，None 时按 dotenv 默认规则查找

        Returns:
            只读 Config 实例

        Raises:
            ValueError: 缺少必要环境变量（如 HYPERLIQUID_PRIVATE_KEY）
        """
        load_dotenv(env_file, override=False)

        data: dict[str, Any] = {}
        path = Path(config_path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        llm_data = data.get("llm") or {}
        trading_data = data.get("trading") or {}
        grid_data = data.get("grid") or {}

        private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "").strip()
        if not private_key:
            raise ValueError("缺少环境变量 HYPERLIQUID_PRIVATE_KEY，请在 .env 中配置")

        llm = LLMConfig(
            base_url=str(llm_data.get("base_url", LLMConfig.base_url)).rstrip("/"),
            model=str(llm_data.get("model", LLMConfig.model)),
            temperature=float(llm_data.get("temperature", LLMConfig.temperature)),
            timeout=float(llm_data.get("timeout", LLMConfig.timeout)),
            api_key=os.getenv("LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""),
        )

        exchange = ExchangeConfig(
            private_key=private_key,
            account_address=os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "").strip() or None,
            testnet=_env_bool("HYPERLIQUID_TESTNET", default=True),
        )

        symbols = tuple(str(s).upper() for s in trading_data.get("symbols", ["BTC"]))
        if not symbols:
            raise ValueError("trading.symbols 不能为空")

        trading = TradingConfig(
            symbols=symbols,
            perp_enabled=bool(trading_data.get("perp_enabled", TradingConfig.perp_enabled)),
            grid_enabled=bool(trading_data.get("grid_enabled", TradingConfig.grid_enabled)),
            max_trade_amount=float(
                trading_data.get("max_trade_amount", TradingConfig.max_trade_amount)
            ),
            max_leverage=int(trading_data.get("max_leverage", TradingConfig.max_leverage)),
            max_positions=int(trading_data.get("max_positions", TradingConfig.max_positions)),
            take_profit_ratio=float(
                trading_data.get("take_profit_ratio", TradingConfig.take_profit_ratio)
            ),
            stop_loss_ratio=float(
                trading_data.get("stop_loss_ratio", TradingConfig.stop_loss_ratio)
            ),
            min_confidence=float(trading_data.get("min_confidence", TradingConfig.min_confidence)),
            timeframe=str(trading_data.get("timeframe", TradingConfig.timeframe)),
            candles_limit=int(trading_data.get("candles_limit", TradingConfig.candles_limit)),
            timeframe_offset=float(
                trading_data.get("timeframe_offset", TradingConfig.timeframe_offset)
            ),
            min_throttle_secs=float(
                trading_data.get("min_throttle_secs", TradingConfig.min_throttle_secs)
            ),
            run_immediately=bool(
                trading_data.get("run_immediately", TradingConfig.run_immediately)
            ),
        )

        grid = GridConfig(
            interval_minutes=int(grid_data.get("interval_minutes", GridConfig.interval_minutes)),
            width_min_pct=float(grid_data.get("width_min_pct", GridConfig.width_min_pct)),
            width_max_pct=float(grid_data.get("width_max_pct", GridConfig.width_max_pct)),
            width_fallback_pct=float(
                grid_data.get("width_fallback_pct", GridConfig.width_fallback_pct)
            ),
            ai_blend_weight=float(grid_data.get("ai_blend_weight", GridConfig.ai_blend_weight)),
            force_neutral=bool(grid_data.get("force_neutral", GridConfig.force_neutral)),
            min_grid_num=int(grid_data.get("min_grid_num", GridConfig.min_grid_num)),
            max_position_notional_usd=float(
                grid_data.get("max_position_notional_usd", GridConfig.max_position_notional_usd)
            ),
            halt_below_usd=float(grid_data.get("halt_below_usd", GridConfig.halt_below_usd)),
            trend_filter_enabled=bool(
                grid_data.get("trend_filter_enabled", GridConfig.trend_filter_enabled)
            ),
            trend_filter_min_votes=int(
                grid_data.get("trend_filter_min_votes", GridConfig.trend_filter_min_votes)
            ),
            trend_filter_timeframes=tuple(
                grid_data.get("trend_filter_timeframes", GridConfig.trend_filter_timeframes)
            ),
            trend_confirm_cycles=int(
                grid_data.get("trend_confirm_cycles", GridConfig.trend_confirm_cycles)
            ),
            flatten_adverse=bool(grid_data.get("flatten_adverse", GridConfig.flatten_adverse)),
            flatten_min_cycles=int(
                grid_data.get("flatten_min_cycles", GridConfig.flatten_min_cycles)
            ),
            llm_failure_alert_cycles=int(
                grid_data.get("llm_failure_alert_cycles", GridConfig.llm_failure_alert_cycles)
            ),
            llm_fallback_rebuild_cycles=int(
                grid_data.get("llm_fallback_rebuild_cycles", GridConfig.llm_fallback_rebuild_cycles)
            ),
            barrier=dict(grid_data.get("barrier") or {}),
        )

        protections = data.get("protections")
        if protections is None:
            protections = [dict(p) for p in DEFAULT_PROTECTIONS]

        return cls(llm=llm, exchange=exchange, trading=trading, grid=grid, protections=protections)


def _env_bool(name: str, default: bool) -> bool:
    """解析布尔环境变量：true/1/yes 为真（大小写不敏感），未设置时用默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("true", "1", "yes")
