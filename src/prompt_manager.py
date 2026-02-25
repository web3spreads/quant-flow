"""
Prompt 管理模块
负责加载和管理可配置的 Prompt 模板
支持 Jinja2 模板引擎，可根据不同币种自定义 Prompt
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, Template

from src.config import FEE_RATE_PER_SIDE, MAKER_FEE_RATE_PER_SIDE
from src.fees import FeeRates
from src.i18n import get_text
from src.qlib_engine.model.predictor import SignalDirection


class PromptManager:
    """Prompt 管理器 - 支持从配置文件加载和切换不同的 Prompt 集合"""

    def format_position_details(
        self, symbol: str, current_positions: list, current_price: float
    ) -> dict[str, Any]:
        """
        格式化当前币种的持仓详情

        Args:
            symbol: 交易对
            current_positions: 所有持仓列表
            current_price: 当前价格

        Returns:
            包含持仓详情的字典，包括：
            - has_position: 是否有持仓
            - position_side: 持仓方向 (long/short/none)
            - entry_price: 入场价格
            - position_size: 持仓数量（绝对值）
            - position_value: 持仓价值
            - leverage: 杠杆倍数
            - unrealized_pnl: 未实现盈亏（USD）
            - unrealized_pnl_percent: 未实现盈亏百分比
            - margin_used: 使用的保证金
            - liquidation_price: 清算价格（如果有）
            - price_change_percent: 价格变化百分比
            - distance_from_entry: 距离入场价的距离百分比
            - distance_to_liquidation: 距离清算价的距离百分比
            - position_text: 格式化的持仓信息文本
        """
        # 查找当前币种的持仓
        position = None
        for pos in current_positions:
            if pos.get("coin") == symbol:
                position = pos
                break

        # 如果没有持仓，返回空信息
        if not position:
            no_position_text = get_text(self.language, "no_position")
            position_status_label = get_text(self.language, "position_details")
            return {
                "has_position": False,
                "position_side": "none",
                "entry_price": 0,
                "position_size": 0,
                "position_value": 0,
                "leverage": 0,
                "unrealized_pnl": 0,
                "unrealized_pnl_percent": 0,
                "margin_used": 0,
                "liquidation_price": 0,
                "price_change_percent": 0,
                "distance_from_entry": 0,
                "distance_to_liquidation": 0,
                "position_text": f"**{symbol} {position_status_label}**: {no_position_text}",
            }

        # 提取持仓数据
        szi = float(position.get("szi", 0))
        entry_price = float(position.get("entryPx", 0))
        position_value = abs(float(position.get("positionValue", 0)))
        unrealized_pnl = float(position.get("unrealizedPnl", 0))

        # 确定方向
        if szi == 0:
            position_side = "none"
        elif szi > 0:
            position_side = "long"
        else:
            position_side = "short"
        position_size = abs(szi)

        # 获取杠杆信息
        leverage_info = position.get("leverage", {})
        if isinstance(leverage_info, dict):
            leverage = int(leverage_info.get("value", 1))
        else:
            leverage = int(leverage_info) if leverage_info else 1

        # 计算盈亏百分比（防止除零错误）
        if entry_price > 0 and current_price > 0:
            if position_side == "long":
                # 多头：(当前价 - 入场价) / 入场价 * 杠杆
                price_change_percent = ((current_price - entry_price) / entry_price) * 100
                unrealized_pnl_percent = price_change_percent * leverage
            else:
                # 空头：(入场价 - 当前价) / 入场价 * 杠杆
                price_change_percent = ((entry_price - current_price) / entry_price) * 100
                unrealized_pnl_percent = price_change_percent * leverage

            distance_from_entry = abs(price_change_percent)
        else:
            # 如果价格为0，所有百分比计算都重置为0
            price_change_percent = 0
            unrealized_pnl_percent = 0
            distance_from_entry = 0

        # 计算保证金（仓位价值 / 杠杆）
        margin_used = position_value / leverage if leverage > 0 else position_value

        # 获取清算价格（如果有）
        liquidation_price = float(position.get("liquidationPx", 0))

        # 计算距离清算价的距离
        if liquidation_price > 0 and current_price > 0:
            if position_side == "long":
                distance_to_liquidation = (
                    (current_price - liquidation_price) / current_price
                ) * 100
            else:
                distance_to_liquidation = (
                    (liquidation_price - current_price) / current_price
                ) * 100
        else:
            distance_to_liquidation = 0

        # 格式化持仓信息文本
        side_emoji = "📈" if position_side == "long" else "📉"
        pnl_emoji = "✅" if unrealized_pnl > 0 else ("❌" if unrealized_pnl < 0 else "➖")

        # 获取本地化文本
        def t(key, **kwargs):
            return get_text(self.language, key, **kwargs)

        position_side_text = t("long") if position_side == "long" else t("short")

        position_text = f"""**{symbol} {t("position_details")}** {side_emoji}:

**{t("basic_info")}**:
- {t("position_side")}: {position_side_text} {side_emoji}
- {t("position_size")}: {position_size:.4f} {symbol}
- {t("entry_price")}: ${entry_price:.2f}
- {t("current_price")}: ${current_price:.2f}
- {t("leverage")}: {leverage}x

**{t("pnl_status")}** {pnl_emoji}:
- {t("price_change")}: {price_change_percent:+.2f}% ({t("distance_from_entry")})
- {t("unrealized_pnl")}: ${unrealized_pnl:+.2f} ({unrealized_pnl_percent:+.2f}%) {pnl_emoji}
- {t("position_value")}: ${position_value:.2f}
- {t("margin_used")}: ${margin_used:.2f}"""

        if liquidation_price > 0:
            position_text += f"""
- {t("liquidation_price")}: ${liquidation_price:.2f}
- {t("distance_to_liquidation")}: {distance_to_liquidation:.2f}%"""

        # 确定盈亏状态
        if unrealized_pnl > 0:
            status = t("profit_status")
        elif unrealized_pnl == 0:
            status = t("flat_status")
        else:
            status = t("loss_status")

        risk_level = t("risk_high") if leverage >= 10 else t("risk_moderate")

        position_text += f"""

**{t("important_notice")}**:
- {t("current_status_notice", status=status)}
- {t("leverage_risk", leverage=leverage, risk_level=risk_level)}
- {t("watch_price_notice")}"""

        return {
            "has_position": True,
            "position_side": position_side,
            "entry_price": entry_price,
            "position_size": position_size,
            "position_value": position_value,
            "leverage": leverage,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_percent": unrealized_pnl_percent,
            "margin_used": margin_used,
            "liquidation_price": liquidation_price,
            "price_change_percent": price_change_percent,
            "distance_from_entry": distance_from_entry,
            "distance_to_liquidation": (distance_to_liquidation if liquidation_price > 0 else 0),
            "position_text": position_text,
        }

    def format_recent_trades_text(
        self,
        symbol: str,
        recent_trades: list[dict[str, Any]],
    ) -> str:
        """
        格式化最近1小时操作记录文本

        Args:
            symbol: 交易对
            recent_trades: 最近1小时的交易记录列表，每条记录包含:
                - time: 时间戳（毫秒）
                - side: 方向 ('B' 表示买入, 'A' 表示卖出)
                - dir: 开平方向 (Open Long/Close Long/Open Short/Close Short)
                - px: 成交价格
                - sz: 成交数量
                - closedPnl: 已实现盈亏

        Returns:
            格式化后的操作记录文本，如果没有记录则返回空字符串
        """
        # 如果没有记录，返回空字符串（不注入任何信息）
        if not recent_trades:
            return ""

        # 获取国际化文本的快捷方法
        def t(key, **kwargs):
            return get_text(self.language, key, **kwargs)

        # 计算统计信息
        total_pnl = sum(float(trade.get("closedPnl", 0)) for trade in recent_trades)
        trade_count = len(recent_trades)

        # 构建操作记录文本
        lines = []
        lines.append(f"## {t('recent_trades_title')}")
        lines.append("")
        lines.append(f"- {t('recent_trades_count')}: {trade_count}")
        lines.append(f"- {t('recent_trades_total_pnl')}: ${total_pnl:+.2f}")
        lines.append("")
        lines.append(f"**{t('recent_trades_list_header')}:**")

        # 按时间顺序排列（从旧到新）
        sorted_trades = sorted(recent_trades, key=lambda x: x.get("time", 0))

        for trade in sorted_trades:
            # 解析时间
            time_ms = trade.get("time", 0)
            trade_time = datetime.fromtimestamp(time_ms / 1000).strftime("%H:%M:%S")

            # 解析方向
            dir_text = trade.get("dir", "")
            if dir_text == "Open Long":
                dir_display = t("recent_trades_open_long")
            elif dir_text == "Close Long":
                dir_display = t("recent_trades_close_long")
            elif dir_text == "Open Short":
                dir_display = t("recent_trades_open_short")
            elif dir_text == "Close Short":
                dir_display = t("recent_trades_close_short")
            else:
                # 如果没有 dir，根据 side 显示
                side = trade.get("side", "")
                dir_display = t("recent_trades_buy") if side == "B" else t("recent_trades_sell")

            # 价格和数量
            price = float(trade.get("px", 0))
            size = abs(float(trade.get("sz", 0)))
            pnl = float(trade.get("closedPnl", 0))

            # 格式化单条记录
            pnl_text = f"{t('recent_trades_pnl')}: ${pnl:+.2f}" if pnl != 0 else ""
            line = f"- {trade_time}: {dir_display} {size:.4f} @ ${price:.2f}"
            if pnl_text:
                line += f" ({pnl_text})"
            lines.append(line)

        lines.append("")
        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def __init__(
        self,
        config_file: str = "prompts/prompts.yaml",
        prompt_set: str = "default",
        fee_rates_perp: FeeRates | None = None,
    ):
        """
        初始化 Prompt 管理器

        Args:
            config_file: Prompt 配置文件路径
            prompt_set: 使用的 Prompt 集合名称
            fee_rates_perp: 永续合约的 maker/taker 费率（如果为 None 使用默认常量）
        """
        self.config_file = Path(config_file)
        self.prompt_set_name = prompt_set
        self.prompts_dir = self.config_file.parent
        self.fee_rates_perp = fee_rates_perp or FeeRates(
            maker_rate=MAKER_FEE_RATE_PER_SIDE, taker_rate=FEE_RATE_PER_SIDE
        )

        # 初始化 Jinja2 环境
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # 加载 Prompt 配置
        self.config = self._load_config()
        self.prompt_set = self._get_prompt_set(prompt_set)

        # 获取语言设置，默认为中文
        self.language = self.prompt_set.get("language", "zh")

        # 加载 Prompt 内容（作为 Jinja2 模板）
        self.system_prompt = self._load_prompt_file(self.prompt_set["system_prompt_file"])
        self.spot_system_prompt = self._load_prompt_file(self.prompt_set["spot_system_prompt_file"])
        self.trading_prompt_template = self._load_prompt_template(
            self.prompt_set["trading_prompt_template_file"]
        )
        self.spot_prompt_template = self._load_prompt_template(
            self.prompt_set["spot_prompt_template_file"]
        )

        # Review prompts: 优先使用当前 prompt set 的配置，如果没有则 fallback 到 default
        review_system_file = self.prompt_set.get(
            "review_system_prompt_file", "default/review_system_prompt.md"
        )
        review_template_file = self.prompt_set.get(
            "review_prompt_template_file", "default/review_prompt_template.md"
        )

        self.review_system_prompt = self._load_prompt_file(review_system_file)
        self.review_prompt_template = self._load_prompt_template(review_template_file)

        # Research prompts: 优先使用当前 prompt set 的配置，如果没有则 fallback 到 default
        research_system_file = self.prompt_set.get(
            "research_system_prompt_file", "default/research_system_prompt.md"
        )
        research_template_file = self.prompt_set.get(
            "research_prompt_template_file", "default/research_prompt_template.md"
        )

        self.research_system_prompt = self._load_prompt_file(research_system_file)
        self.research_prompt_template_content = self._load_prompt_file(research_template_file)

        print(
            f"✅ 已加载 Prompt 集合: {self.prompt_set['name']} - {self.prompt_set['description']}"
        )

    def _load_config(self) -> dict[str, Any]:
        """加载 Prompt 配置文件"""
        if not self.config_file.exists():
            raise FileNotFoundError(
                f"Prompt 配置文件不存在: {self.config_file}\n请确保 prompts/prompts.yaml 文件存在"
            )

        with open(self.config_file, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _get_prompt_set(self, set_name: str) -> dict[str, Any]:
        """获取指定的 Prompt 集合配置"""
        prompt_sets = self.config.get("prompt_sets", {})

        if set_name not in prompt_sets:
            available_sets = list(prompt_sets.keys())
            raise ValueError(
                f"Prompt 集合 '{set_name}' 不存在\n可用的集合: {', '.join(available_sets)}"
            )

        return prompt_sets[set_name]

    def _load_prompt_file(self, relative_path: str) -> str:
        """
        加载 Prompt 文件内容（用于简单的系统 Prompt，不需要模板功能）

        Args:
            relative_path: 相对于 prompts 目录的文件路径

        Returns:
            Prompt 文件内容
        """
        file_path = self.prompts_dir / relative_path

        if not file_path.exists():
            raise FileNotFoundError(
                f"Prompt 文件不存在: {file_path}\n请确保文件存在或检查 prompts.yaml 配置"
            )

        with open(file_path, encoding="utf-8") as f:
            return f.read()

    def _load_prompt_template(self, relative_path: str) -> Template:
        """
        加载 Prompt 模板文件（作为 Jinja2 模板）

        Args:
            relative_path: 相对于 prompts 目录的文件路径

        Returns:
            Jinja2 Template 对象
        """
        file_path = self.prompts_dir / relative_path

        if not file_path.exists():
            raise FileNotFoundError(
                f"Prompt 文件不存在: {file_path}\n请确保文件存在或检查 prompts.yaml 配置"
            )

        with open(file_path, encoding="utf-8") as f:
            template_content = f.read()
            return self.jinja_env.from_string(template_content)

    def _load_optional_prompt_file(self, relative_path: str | None, default: str) -> str:
        """加载可选 Prompt 文件，不存在时使用默认内容"""
        if not relative_path:
            return default
        try:
            return self._load_prompt_file(relative_path)
        except FileNotFoundError:
            return default

    def _load_optional_prompt_template(
        self, relative_path: str | None, default_template: str
    ) -> Template:
        """加载可选 Prompt 模板，不存在时使用默认模板"""
        if not relative_path:
            return self.jinja_env.from_string(default_template)
        try:
            return self._load_prompt_template(relative_path)
        except FileNotFoundError:
            return self.jinja_env.from_string(default_template)

    def get_system_prompt(self) -> str:
        """获取系统 Prompt"""
        return self.system_prompt

    def get_spot_system_prompt(self) -> str:
        """获取现货 Agent 系统 Prompt"""
        return self.spot_system_prompt

    def get_review_system_prompt(self) -> str:
        """
        获取复盘 Agent 的系统 Prompt。

        Returns:
            复盘 Agent 的系统 Prompt 字符串，用于指导复盘 Agent 的行为。
        """
        return self.review_system_prompt

    def get_research_system_prompt(self) -> str:
        """
        获取外部信息收集 Agent 的系统 Prompt。

        Returns:
            研究 Agent 的系统 Prompt 字符串。
        """
        return self.research_system_prompt

    def get_research_prompt_template(self) -> str:
        """
        获取外部信息收集 Agent 的 Prompt 模板内容。

        Returns:
            研究 Prompt 模板的字符串内容（非 Jinja2 Template 对象）。
        """
        return self.research_prompt_template_content

    def format_trading_prompt(
        self,
        symbol: str,
        market_data: dict[str, Any],
        multi_timeframe_trends: dict[str, str],
        current_positions: list,
        max_positions: int,
        max_trade_amount: float,
        max_leverage: int,
        take_profit_ratio: float,
        stop_loss_ratio: float,
        historical_summary: str | None = None,
        balance_info: dict[str, float] | None = None,
        enriched_data: dict[str, Any] | None = None,
        limit_order_enabled: bool = False,
        open_limit_orders: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        格式化交易决策 Prompt

        Args:
            symbol: 交易对
            market_data: 市场数据
            multi_timeframe_trends: 多时间周期趋势
            current_positions: 当前持仓
            max_positions: 最大持仓数
            max_trade_amount: 单笔交易金额上限
            max_leverage: 最大杠杆倍数
            take_profit_ratio: 止盈比例（如 0.05 = 5%）
            stop_loss_ratio: 止损比例（如 0.02 = 2%）
            historical_summary: 历史决策汇总
            balance_info: 账户余额信息 {'total': float, 'occupied': float, 'available': float}

        Returns:
            格式化后的 Prompt
        """
        # 提取市场数据
        current_price = market_data.get("current_price", 0)
        rsi = market_data.get("rsi", 0)
        macd = market_data.get("macd", 0)
        macd_signal = market_data.get("macd_signal", 0)
        macd_hist = market_data.get("macd_hist", 0)
        ma_7 = market_data.get("ma_7", 0)
        ma_25 = market_data.get("ma_25", 0)
        ma_99 = market_data.get("ma_99", 0)
        bb_upper = market_data.get("bb_upper", 0)
        bb_middle = market_data.get("bb_middle", 0)
        bb_lower = market_data.get("bb_lower", 0)
        bb_position = market_data.get("bb_position", 0.5)
        volume_change = market_data.get("volume_change", 0)

        # 获取详细持仓信息
        position_details = self.format_position_details(symbol, current_positions, current_price)

        # 判断持仓状态（保持向后兼容）
        has_long = position_details["has_position"] and position_details["position_side"] == "long"
        has_short = (
            position_details["has_position"] and position_details["position_side"] == "short"
        )
        position_count = len(current_positions)

        # 格式化多周期趋势
        def t(key, **kwargs):
            return get_text(self.language, key, **kwargs)

        timeframes = [
            ("daily", "日线"),
            ("4h", "4小时"),
            ("1h", "1小时"),
            ("15m", "15分钟"),
            ("1m", "1分钟"),
        ]
        trends_text = ""
        for tf_key, tf_zh in timeframes:
            # 对于中文prompt使用中文时间周期，英文prompt使用英文时间周期
            timeframe_display = t(tf_key) if self.language == "en" else tf_zh
            timeframe_key = tf_zh  # multi_timeframe_trends 使用中文键
            trend = multi_timeframe_trends.get(timeframe_key, t("unknown"))
            trends_text += f"- {timeframe_display}: {trend}\n"

        # 格式化历史汇总
        historical_text = ""
        if historical_summary:
            summary_title = t("historical_summary")
            summary_hint = t("historical_hint")
            hint_label = t("hint_label")
            historical_text = f"""
## 📜 {summary_title}

{historical_summary}

**{hint_label}:** {summary_hint}
"""

        # 格式化账户余额信息
        balance_text = ""
        if balance_info:
            total = balance_info.get("total", 0)
            occupied = balance_info.get("occupied", 0)
            available = balance_info.get("available", 0)
            unrealized_pnl = balance_info.get("unrealized_pnl", 0)
            pnl_emoji = "📈" if unrealized_pnl > 0 else ("📉" if unrealized_pnl < 0 else "➖")
            balance_text = f"""
## 💰 {t("account_balance")}

- **{t("total_value")}**: ${total:.2f}
- **{t("occupied_margin")}**: ${occupied:.2f}
- **{t("available_balance")}**: ${available:.2f}
- **{t("unrealized_pnl_total")}**: ${unrealized_pnl:+.2f} {pnl_emoji}

**{t("balance_notice_title")}**:
- {t("balance_check_notice")}
- {t("insufficient_balance_notice")}
- {t("large_loss_notice")}
"""

        # 计算手续费相关的值（防止除零错误）
        fee_rate_per_side = self.fee_rates_perp.taker_rate
        total_fee_rate = fee_rate_per_side * 2
        position_value = max_trade_amount * max_leverage
        open_fee = position_value * fee_rate_per_side
        close_fee = position_value * fee_rate_per_side
        total_fee = open_fee + close_fee

        # 防止除零错误
        if max_trade_amount > 0:
            breakeven_percent = f"{(total_fee / max_trade_amount) * 100:.2f}%"
        else:
            breakeven_percent = "0.00%"

        if position_value > 0:
            price_move_percent = f"{(total_fee / position_value) * 100:.3f}%"
        else:
            price_move_percent = "0.000%"

        profit_to_fee_ratio = take_profit_ratio / total_fee_rate

        # 准备模板上下文（使用 Jinja2 渲染）
        context = {
            # 基础信息
            "symbol": symbol,
            "coin": symbol,  # 币种别名
            # 币种类型判断（用于条件逻辑）
            "is_BTC": symbol == "BTC",
            "is_ETH": symbol == "ETH",
            "is_SOL": symbol == "SOL",
            "is_major_coin": symbol in ["BTC", "ETH"],  # 主流币
            "is_altcoin": symbol not in ["BTC", "ETH"],  # 山寨币
            # 市场数据
            "current_price": f"{current_price:.2f}",
            "current_price_raw": current_price,
            "rsi": f"{rsi:.2f}",
            "rsi_raw": rsi,
            "macd": f"{macd:.4f}",
            "macd_raw": macd,
            "macd_signal": f"{macd_signal:.4f}",
            "macd_signal_raw": macd_signal,
            "macd_hist": f"{macd_hist:.4f}",
            "macd_hist_raw": macd_hist,
            "ma_7": f"{ma_7:.2f}",
            "ma_7_raw": ma_7,
            "ma_25": f"{ma_25:.2f}",
            "ma_25_raw": ma_25,
            "ma_99": f"{ma_99:.2f}",
            "ma_99_raw": ma_99,
            "bb_upper": f"{bb_upper:.2f}",
            "bb_upper_raw": bb_upper,
            "bb_middle": f"{bb_middle:.2f}",
            "bb_middle_raw": bb_middle,
            "bb_lower": f"{bb_lower:.2f}",
            "bb_lower_raw": bb_lower,
            "bb_position": f"{bb_position:.2%}",
            "bb_position_raw": bb_position,
            "volume_change": f"{volume_change:.2f}",
            "volume_change_raw": volume_change,
            "multi_timeframe_trends": trends_text,
            # 持仓信息（基础）
            "position_count": position_count,
            "max_positions": max_positions,
            "has_long": t("yes") if has_long else t("no"),
            "has_long_bool": has_long,
            "has_short": t("yes") if has_short else t("no"),
            "has_short_bool": has_short,
            # 当前币种详细持仓信息
            "has_position": position_details["has_position"],
            "position_side": position_details["position_side"],
            "entry_price": f"{position_details['entry_price']:.2f}",
            "entry_price_raw": position_details["entry_price"],
            "position_size": f"{position_details['position_size']:.4f}",
            "position_size_raw": position_details["position_size"],
            "position_value": f"{position_details['position_value']:.2f}",
            "position_value_raw": position_details["position_value"],
            "position_leverage": position_details["leverage"],
            "position_unrealized_pnl": f"{position_details['unrealized_pnl']:+.2f}",
            "position_unrealized_pnl_raw": position_details["unrealized_pnl"],
            "position_unrealized_pnl_percent": f"{position_details['unrealized_pnl_percent']:+.2f}",
            "position_unrealized_pnl_percent_raw": position_details["unrealized_pnl_percent"],
            "position_margin_used": f"{position_details['margin_used']:.2f}",
            "position_margin_used_raw": position_details["margin_used"],
            "position_liquidation_price": f"{position_details['liquidation_price']:.2f}",
            "position_liquidation_price_raw": position_details["liquidation_price"],
            "position_price_change_percent": f"{position_details['price_change_percent']:+.2f}",
            "position_price_change_percent_raw": position_details["price_change_percent"],
            "position_distance_from_entry": f"{position_details['distance_from_entry']:.2f}",
            "position_distance_from_entry_raw": position_details["distance_from_entry"],
            "position_distance_to_liquidation": f"{position_details['distance_to_liquidation']:.2f}",
            "position_distance_to_liquidation_raw": position_details["distance_to_liquidation"],
            "position_details_text": position_details["position_text"],
            # 交易参数
            "max_trade_amount": f"{max_trade_amount:.2f}",
            "max_trade_amount_raw": max_trade_amount,
            "max_leverage": max_leverage,
            "take_profit_ratio": f"{take_profit_ratio:.1%}",
            "take_profit_ratio_raw": take_profit_ratio,
            "stop_loss_ratio": f"{stop_loss_ratio:.1%}",
            "stop_loss_ratio_raw": stop_loss_ratio,
            # 费用计算
            "fee_position_value": f"{position_value:.2f}",
            "fee_position_value_raw": position_value,
            "fee_rate_per_side": f"{fee_rate_per_side * 100:.3f}%",
            "fee_rate_per_side_raw": fee_rate_per_side,
            "total_fee_rate": f"{total_fee_rate * 100:.3f}%",
            "total_fee_rate_raw": total_fee_rate,
            "open_fee": f"{open_fee:.2f}",
            "open_fee_raw": open_fee,
            "close_fee": f"{close_fee:.2f}",
            "close_fee_raw": close_fee,
            "total_fee": f"{total_fee:.2f}",
            "total_fee_raw": total_fee,
            "breakeven_percent": breakeven_percent,
            "price_move_percent": price_move_percent,
            "profit_to_fee_ratio": (
                f"{profit_to_fee_ratio:.1f}x" if profit_to_fee_ratio != float("inf") else "∞"
            ),
            "profit_to_fee_ratio_raw": profit_to_fee_ratio,
            # 历史和余额信息
            "historical_summary": historical_text,
            "balance_info": balance_text,
            # 限价单信息
            "limit_order_enabled": limit_order_enabled,
            "open_limit_orders": open_limit_orders or [],
        }
        
        # 格式化限价单信息文本（根据语言选择）
        def _generate_limit_orders_text(orders, lang):
            if lang == "en":
                strings = {
                    "header": "\n## 📋 Pending Limit Orders\n\n",
                    "no_orders": "No pending limit orders\n",
                    "order_fmt": "- **Order #{order_id}** {side_emoji} {side_text}\n"
                                 "  - Limit Price: ${limit_price:.2f}\n"
                                 "  - Current Price: ${current_price:.2f}\n"
                                 "  - Price Gap: {price_diff_str}\n"
                                 "  - Size: {size:.6f}\n\n",
                    "side_text": {"buy": "Limit Long", "sell": "Limit Short"},
                }
            else:
                strings = {
                    "header": "\n## 📋 待处理限价单\n\n",
                    "no_orders": "暂无待处理的限价单\n",
                    "order_fmt": "- **订单 #{order_id}** {side_emoji} {side_text}\n"
                                 "  - 限价: ${limit_price:.2f}\n"
                                 "  - 当前价: ${current_price:.2f}\n"
                                 "  - 价格差距: {price_diff_str}\n"
                                 "  - 数量: {size:.6f}\n\n",
                    "side_text": {"buy": "限价开多", "sell": "限价开空"},
                }
            text = strings["header"]
            if orders:
                for order in orders:
                    order_id = order.get('order_id', 0)
                    side = order.get('side', 'unknown')
                    limit_price = order.get('limit_price', 0)
                    size = order.get('size', 0)
                    current_price = order.get('current_price', 0)
                    price_diff = order.get('price_diff_percent', 0)
                    side_emoji = "📈" if side == 'buy' else "📉"
                    side_text = strings["side_text"].get(side, side)
                    price_diff_str = f"{price_diff:+.2f}%"
                    text += strings["order_fmt"].format(
                        order_id=order_id,
                        side_emoji=side_emoji,
                        side_text=side_text,
                        limit_price=limit_price,
                        current_price=current_price,
                        price_diff_str=price_diff_str,
                        size=size,
                    )
            else:
                text += strings["no_orders"]
            return text

        if limit_order_enabled:
            context["limit_orders_text"] = _generate_limit_orders_text(open_limit_orders, self.language)
        else:
            context["limit_orders_text"] = ""

        # 格式化限价单信息文本（根据语言选择）
        def _generate_limit_orders_text(orders, lang):
            if lang == "en":
                strings = {
                    "header": "\n## 📋 Pending Limit Orders\n\n",
                    "no_orders": "No pending limit orders\n",
                    "order_fmt": "- **Order #{order_id}** {side_emoji} {side_text}\n"
                    "  - Limit Price: ${limit_price:.2f}\n"
                    "  - Current Price: ${current_price:.2f}\n"
                    "  - Price Gap: {price_diff_str}\n"
                    "  - Size: {size:.6f}\n\n",
                    "side_text": {"buy": "Limit Long", "sell": "Limit Short"},
                }
            else:
                strings = {
                    "header": "\n## 📋 待处理限价单\n\n",
                    "no_orders": "暂无待处理的限价单\n",
                    "order_fmt": "- **订单 #{order_id}** {side_emoji} {side_text}\n"
                    "  - 限价: ${limit_price:.2f}\n"
                    "  - 当前价: ${current_price:.2f}\n"
                    "  - 价格差距: {price_diff_str}\n"
                    "  - 数量: {size:.6f}\n\n",
                    "side_text": {"buy": "限价开多", "sell": "限价开空"},
                }
            text = strings["header"]
            if orders:
                for order in orders:
                    order_id = order.get("order_id", 0)
                    side = order.get("side", "unknown")
                    limit_price = order.get("limit_price", 0)
                    size = order.get("size", 0)
                    current_price = order.get("current_price", 0)
                    price_diff = order.get("price_diff_percent", 0)
                    side_emoji = "📈" if side == "buy" else "📉"
                    side_text = strings["side_text"].get(side, side)
                    price_diff_str = f"{price_diff:+.2f}%"
                    text += strings["order_fmt"].format(
                        order_id=order_id,
                        side_emoji=side_emoji,
                        side_text=side_text,
                        limit_price=limit_price,
                        current_price=current_price,
                        price_diff_str=price_diff_str,
                        size=size,
                    )
            else:
                text += strings["no_orders"]
            return text

        if limit_order_enabled:
            context["limit_orders_text"] = _generate_limit_orders_text(
                open_limit_orders, self.language
            )
        else:
            context["limit_orders_text"] = ""

        # 添加enriched_data中的额外字段（用于nof1和nof1-improved prompts）
        if enriched_data:
            # 直接合并所有enriched_data字段
            context.update(enriched_data)

        # 确保关键字段有默认值（无论 enriched_data 是否存在）
        self._set_enriched_defaults(context, current_price, rsi, balance_info, current_positions)

        # QLib 量化信号（如果可用）
        self._set_qlib_signal_text(context, enriched_data)

        # 使用 Jinja2 渲染模板
        prompt = self.trading_prompt_template.render(context)

        return prompt

    @staticmethod
    def _set_enriched_defaults(
        context: dict,
        current_price: float,
        rsi: float,
        balance_info: dict | None,
        current_positions: list,
    ) -> None:
        """设置 enriched_data 相关字段的默认值，避免 Jinja2 UndefinedError"""
        context.setdefault("elapsed_minutes", 0)
        context.setdefault("mid_prices", [])
        context.setdefault("ema_indicators", [])
        context.setdefault("macd_indicators", [])
        context.setdefault("rsi_7_indicators", [])
        context.setdefault("rsi_14_indicators", [])
        context.setdefault("current_ema20", current_price)
        context.setdefault("current_rsi", rsi)
        context.setdefault("oi_latest", 0)
        context.setdefault("oi_average", 0)
        context.setdefault("funding_rate", 0)
        context.setdefault("ema_20_4h", current_price)
        context.setdefault("ema_50_4h", current_price)
        context.setdefault("atr_3_4h", 0)
        context.setdefault("atr_14_4h", 0)
        context.setdefault("current_volume", 0)
        context.setdefault("avg_volume", 0)
        context.setdefault("macd_4h_indicators", [])
        context.setdefault("rsi_14_4h_indicators", [])
        context.setdefault("total_return_pct", 0)
        context.setdefault("account_value", 10000)
        context.setdefault(
            "available_cash",
            balance_info.get("available", 0) if balance_info else 0,
        )
        context.setdefault("sharpe_ratio", 0)
        context.setdefault("current_positions", str(current_positions))
        context.setdefault("recent_trades_text", "")

    @staticmethod
    def _set_qlib_signal_text(
        context: dict,
        enriched_data: dict | None,
    ) -> None:
        """根据 enriched_data 中的 QLib 信号生成提示词文本"""
        qlib_enabled = (enriched_data or {}).get("qlib_enabled", False)
        qlib_signal = (enriched_data or {}).get("qlib_signal", {})
        context["qlib_enabled"] = qlib_enabled
        context["qlib_signal"] = qlib_signal

        if not (qlib_enabled and qlib_signal):
            context["qlib_signal_text"] = ""
            return

        # 格式化 QLib 信号文本
        direction = qlib_signal.get("direction", "中性")
        strength = qlib_signal.get("strength", 0)
        confidence = qlib_signal.get("confidence", 0)
        raw_score = qlib_signal.get("raw_score", 0)
        normalized_score = qlib_signal.get("normalized_score", 0)
        percentile = qlib_signal.get("percentile", 0.5)
        model_type = qlib_signal.get("model_type", "未知")
        is_actionable = qlib_signal.get("is_actionable", False)

        # 根据方向生成建议（使用 SignalDirection 枚举）
        direction_emoji = {
            SignalDirection.STRONG_LONG.value: "🟢🟢",
            SignalDirection.LONG.value: "🟢",
            SignalDirection.WEAK_LONG.value: "🟡↑",
            SignalDirection.NEUTRAL.value: "⚪",
            SignalDirection.WEAK_SHORT.value: "🟡↓",
            SignalDirection.SHORT.value: "🔴",
            SignalDirection.STRONG_SHORT.value: "🔴🔴",
        }.get(direction, "⚪")

        # 信号强度等级（使用与 predictor 一致的阈值）
        if strength >= 0.7:
            strength_text = "强"
        elif strength >= 0.4:
            strength_text = "中等"
        else:
            strength_text = "弱"

        context["qlib_signal_text"] = f"""
## 🧠 QLib 量化模型信号

**模型预测（{model_type} 模型）:**
- 信号方向: {direction_emoji} **{direction}**
- 信号强度: {strength:.1%}（{strength_text}）
- 模型置信度: {confidence:.1%}
- 标准化分数: {normalized_score:+.4f}（原始: {raw_score:+.6f}）
- 历史分位数: {percentile:.0%}
- 是否可执行: {"是" if is_actionable else "否（强度不足）"}

**QLib 信号解读:**
- 该信号来自基于历史数据训练的机器学习模型，预测未来价格趋势方向
- 信号强度 > 40% 且方向明确时，应作为重要参考
- 信号与技术指标方向一致时，增加决策信心
- 信号与技术指标矛盾时，建议保守或观望

**结合技术指标的建议:**
- ✅ QLib 信号 + 技术指标一致 → 可增大仓位/杠杆
- ⚠️ QLib 信号 + 技术指标矛盾 → 建议观望或小仓位试探
- ❌ QLib 信号弱或中性 → 主要依据技术指标判断
"""

    def format_review_prompt(
        self,
        symbol: str,
        decision_digest: list[dict[str, Any]],
        stats: dict[str, Any],
        existing_lessons: list[dict[str, Any]],
        fills_summary: dict[str, Any] | None = None,
        context_features: dict[str, Any] | None = None,
    ) -> str:
        """格式化复盘 Prompt"""
        fills_context = fills_summary or {"total_fills": 0, "total_pnl": 0.0}
        context = {
            "symbol": symbol,
            "decision_digest": decision_digest,
            "stats": stats,
            "existing_lessons": existing_lessons,
            "fills_summary": fills_context,
            "context_features": context_features or {},
            "context_features_json": json.dumps(context_features or {}, ensure_ascii=False),
        }
        return self.review_prompt_template.render(context)

    def format_spot_prompt(
        self,
        symbol: str,
        market_data: dict[str, Any],
        multi_timeframe_trends: dict[str, str],
        recommendation: dict[str, Any],
        current_spot_holdings: list,
        max_trade_amount: float,
        balance_info: dict[str, float] | None = None,
    ) -> str:
        """
        格式化现货定投决策 Prompt

        Args:
            symbol: 交易对
            market_data: 市场数据
            multi_timeframe_trends: 多时间周期趋势
            recommendation: 推荐信息
            current_spot_holdings: 当前现货持仓
            max_trade_amount: 单笔定投金额上限
            balance_info: 账户余额信息 {'total': float, 'occupied': float, 'available': float}

        Returns:
            格式化后的 Prompt
        """
        # 提取市场数据
        current_price = market_data.get("current_price", 0)
        rsi = market_data.get("rsi", 0)
        macd_hist = market_data.get("macd_hist", 0)
        ma_7 = market_data.get("ma_7", 0)
        ma_25 = market_data.get("ma_25", 0)
        ma_99 = market_data.get("ma_99", 0)
        bb_position = market_data.get("bb_position", 0.5)
        volume_change = market_data.get("volume_change", 0)

        # 检查是否已持有该现货
        has_spot = any(h.get("symbol") == symbol for h in current_spot_holdings)

        # 格式化多周期趋势
        def t(key, **kwargs):
            return get_text(self.language, key, **kwargs)

        timeframes = [
            ("daily", "日线"),
            ("4h", "4小时"),
            ("1h", "1小时"),
            ("15m", "15分钟"),
            ("1m", "1分钟"),
        ]
        trends_text = ""
        for tf_key, tf_zh in timeframes:
            timeframe_display = t(tf_key) if self.language == "en" else tf_zh
            timeframe_key = tf_zh
            trend = multi_timeframe_trends.get(timeframe_key, t("unknown"))
            trends_text += f"- {timeframe_display}: {trend}\n"

        # 格式化账户余额信息
        balance_text = ""
        if balance_info:
            total = balance_info.get("total", 0)
            occupied = balance_info.get("occupied", 0)
            available = balance_info.get("available", 0)
            unrealized_pnl = balance_info.get("unrealized_pnl", 0)
            pnl_emoji = "📈" if unrealized_pnl > 0 else ("📉" if unrealized_pnl < 0 else "➖")
            balance_text = f"""
## 💰 {t("account_balance")}

- **{t("total_value")}**: ${total:.2f}
- **{t("occupied_margin")}**: ${occupied:.2f}
- **{t("available_balance")}**: ${available:.2f}
- **{t("unrealized_pnl_total")}**: ${unrealized_pnl:+.2f} {pnl_emoji}

**{t("balance_notice_title")}**:
- {t("balance_check_for_dca")}
- {t("insufficient_balance_dca")}
- {t("large_loss_notice")}
"""

        # 准备模板上下文（使用 Jinja2 渲染）
        context = {
            # 基础信息
            "symbol": symbol,
            "coin": symbol,
            # 币种类型判断
            "is_BTC": symbol == "BTC",
            "is_ETH": symbol == "ETH",
            "is_SOL": symbol == "SOL",
            "is_major_coin": symbol in ["BTC", "ETH"],
            "is_altcoin": symbol not in ["BTC", "ETH"],
            # 推荐信息
            "recommendation_reason": recommendation.get(
                "reason", t("recommendation_reason_default")
            ),
            "recommendation_timestamp": recommendation.get(
                "timestamp", t("recommendation_timestamp_default")
            ),
            # 市场数据
            "current_price": f"{current_price:.2f}",
            "current_price_raw": current_price,
            "has_spot": t("has_spot") if has_spot else t("no_spot"),
            "has_spot_bool": has_spot,
            "rsi": f"{rsi:.2f}",
            "rsi_raw": rsi,
            "macd_hist": f"{macd_hist:.4f}",
            "macd_hist_raw": macd_hist,
            "ma_7": f"{ma_7:.2f}",
            "ma_7_raw": ma_7,
            "ma_25": f"{ma_25:.2f}",
            "ma_25_raw": ma_25,
            "ma_99": f"{ma_99:.2f}",
            "ma_99_raw": ma_99,
            "bb_position": f"{bb_position:.2%}",
            "bb_position_raw": bb_position,
            "volume_change": f"{volume_change:.2f}",
            "volume_change_raw": volume_change,
            "multi_timeframe_trends": trends_text,
            # 交易参数
            "max_trade_amount": f"{max_trade_amount:.2f}",
            "max_trade_amount_raw": max_trade_amount,
            # 余额信息
            "balance_info": balance_text,
        }

        # 使用 Jinja2 渲染模板
        prompt = self.spot_prompt_template.render(context)

        return prompt

    def get_prompt_set_info(self) -> dict[str, str]:
        """获取当前 Prompt 集合的信息"""
        return {
            "name": self.prompt_set["name"],
            "description": self.prompt_set["description"],
            "set_name": self.prompt_set_name,
        }


def get_prompt_manager(
    config_file: str = "prompts/prompts.yaml", prompt_set: str = "default"
) -> PromptManager:
    """
    获取 Prompt 管理器实例

    Args:
        config_file: Prompt 配置文件路径
        prompt_set: 使用的 Prompt 集合名称

    Returns:
        PromptManager 实例
    """
    return PromptManager(config_file, prompt_set)


if __name__ == "__main__":
    # 测试 Prompt 管理器
    try:
        manager = get_prompt_manager()
        print(f"\n当前 Prompt 集合: {manager.get_prompt_set_info()}")
        print(f"\n系统 Prompt 长度: {len(manager.get_system_prompt())} 字符")
        print(f"现货系统 Prompt 长度: {len(manager.get_spot_system_prompt())} 字符")
        print(f"交易 Prompt 模板长度: {len(manager.trading_prompt_template)} 字符")
        print(f"现货 Prompt 模板长度: {len(manager.spot_prompt_template)} 字符")
    except Exception as e:
        print(f"❌ Prompt 管理器加载失败: {e}")
