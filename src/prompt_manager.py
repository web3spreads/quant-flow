"""
Prompt 管理模块
负责加载和管理可配置的 Prompt 模板
支持 Jinja2 模板引擎，可根据不同币种自定义 Prompt
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, Template


class PromptManager:
    """Prompt 管理器 - 支持从配置文件加载和切换不同的 Prompt 集合"""

    @staticmethod
    def format_position_details(symbol: str, current_positions: list, current_price: float) -> Dict[str, Any]:
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
            - position_text: 格式化的持仓信息文本
        """
        # 查找当前币种的持仓
        position = None
        for pos in current_positions:
            if pos.get('coin') == symbol:
                position = pos
                break

        # 如果没有持仓，返回空信息
        if not position:
            return {
                'has_position': False,
                'position_side': 'none',
                'entry_price': 0,
                'position_size': 0,
                'position_value': 0,
                'leverage': 0,
                'unrealized_pnl': 0,
                'unrealized_pnl_percent': 0,
                'margin_used': 0,
                'liquidation_price': 0,
                'price_change_percent': 0,
                'distance_from_entry': 0,
                'position_text': f'**{symbol} 持仓状态**: 无持仓 ❌'
            }

        # 提取持仓数据
        szi = float(position.get('szi', 0))
        entry_price = float(position.get('entryPx', 0))
        position_value = abs(float(position.get('positionValue', 0)))
        unrealized_pnl = float(position.get('unrealizedPnl', 0))

        # 确定方向
        position_side = 'long' if szi > 0 else 'short'
        position_size = abs(szi)

        # 获取杠杆信息
        leverage_info = position.get('leverage', {})
        if isinstance(leverage_info, dict):
            leverage = int(leverage_info.get('value', 1))
        else:
            leverage = int(leverage_info) if leverage_info else 1

        # 计算盈亏百分比
        if entry_price > 0:
            if position_side == 'long':
                # 多头：(当前价 - 入场价) / 入场价 * 杠杆
                price_change_percent = ((current_price - entry_price) / entry_price) * 100
                unrealized_pnl_percent = price_change_percent * leverage
            else:
                # 空头：(入场价 - 当前价) / 入场价 * 杠杆
                price_change_percent = ((entry_price - current_price) / entry_price) * 100
                unrealized_pnl_percent = price_change_percent * leverage

            distance_from_entry = abs(price_change_percent)
        else:
            price_change_percent = 0
            unrealized_pnl_percent = 0
            distance_from_entry = 0

        # 计算保证金（仓位价值 / 杠杆）
        margin_used = position_value / leverage if leverage > 0 else position_value

        # 获取清算价格（如果有）
        liquidation_price = float(position.get('liquidationPx', 0))

        # 计算距离清算价的距离
        if liquidation_price > 0 and current_price > 0:
            if position_side == 'long':
                distance_to_liquidation = ((current_price - liquidation_price) / current_price) * 100
            else:
                distance_to_liquidation = ((liquidation_price - current_price) / current_price) * 100
        else:
            distance_to_liquidation = 0

        # 格式化持仓信息文本
        side_emoji = "📈" if position_side == 'long' else "📉"
        pnl_emoji = "✅" if unrealized_pnl > 0 else ("❌" if unrealized_pnl < 0 else "➖")

        position_text = f"""**{symbol} 持仓详情** {side_emoji}:

**基础信息**:
- 持仓方向: {'做多 (Long)' if position_side == 'long' else '做空 (Short)'} {side_emoji}
- 持仓数量: {position_size:.4f} {symbol}
- 入场价格: ${entry_price:.2f}
- 当前价格: ${current_price:.2f}
- 杠杆倍数: {leverage}x

**盈亏状况** {pnl_emoji}:
- 价格变化: {price_change_percent:+.2f}% (距离入场价)
- 未实现盈亏: ${unrealized_pnl:+.2f} ({unrealized_pnl_percent:+.2f}%) {pnl_emoji}
- 持仓价值: ${position_value:.2f}
- 使用保证金: ${margin_used:.2f}"""

        if liquidation_price > 0:
            position_text += f"""
- 清算价格: ${liquidation_price:.2f}
- 距离清算: {distance_to_liquidation:.2f}%"""

        position_text += f"""

**重要提示**:
- 当前{'盈利' if unrealized_pnl > 0 else '亏损'}状态，请根据市场情况决定是否止盈/止损
- 杠杆倍数为 {leverage}x，风险{'较高' if leverage >= 10 else '适中'}
- 请关注价格走势，及时调整策略"""

        return {
            'has_position': True,
            'position_side': position_side,
            'entry_price': entry_price,
            'position_size': position_size,
            'position_value': position_value,
            'leverage': leverage,
            'unrealized_pnl': unrealized_pnl,
            'unrealized_pnl_percent': unrealized_pnl_percent,
            'margin_used': margin_used,
            'liquidation_price': liquidation_price,
            'price_change_percent': price_change_percent,
            'distance_from_entry': distance_from_entry,
            'distance_to_liquidation': distance_to_liquidation if liquidation_price > 0 else 0,
            'position_text': position_text
        }

    def __init__(self, config_file: str = "prompts/prompts.yaml", prompt_set: str = "default"):
        """
        初始化 Prompt 管理器

        Args:
            config_file: Prompt 配置文件路径
            prompt_set: 使用的 Prompt 集合名称
        """
        self.config_file = Path(config_file)
        self.prompt_set_name = prompt_set
        self.prompts_dir = self.config_file.parent

        # 初始化 Jinja2 环境
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True
        )

        # 加载 Prompt 配置
        self.config = self._load_config()
        self.prompt_set = self._get_prompt_set(prompt_set)

        # 加载 Prompt 内容（作为 Jinja2 模板）
        self.system_prompt = self._load_prompt_file(self.prompt_set["system_prompt_file"])
        self.spot_system_prompt = self._load_prompt_file(self.prompt_set["spot_system_prompt_file"])
        self.trading_prompt_template = self._load_prompt_template(self.prompt_set["trading_prompt_template_file"])
        self.spot_prompt_template = self._load_prompt_template(self.prompt_set["spot_prompt_template_file"])

        print(f"✅ 已加载 Prompt 集合: {self.prompt_set['name']} - {self.prompt_set['description']}")

    def _load_config(self) -> Dict[str, Any]:
        """加载 Prompt 配置文件"""
        if not self.config_file.exists():
            raise FileNotFoundError(
                f"Prompt 配置文件不存在: {self.config_file}\n"
                f"请确保 prompts/prompts.yaml 文件存在"
            )

        with open(self.config_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _get_prompt_set(self, set_name: str) -> Dict[str, Any]:
        """获取指定的 Prompt 集合配置"""
        prompt_sets = self.config.get("prompt_sets", {})

        if set_name not in prompt_sets:
            available_sets = list(prompt_sets.keys())
            raise ValueError(
                f"Prompt 集合 '{set_name}' 不存在\n"
                f"可用的集合: {', '.join(available_sets)}"
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
                f"Prompt 文件不存在: {file_path}\n"
                f"请确保文件存在或检查 prompts.yaml 配置"
            )

        with open(file_path, "r", encoding="utf-8") as f:
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
                f"Prompt 文件不存在: {file_path}\n"
                f"请确保文件存在或检查 prompts.yaml 配置"
            )

        with open(file_path, "r", encoding="utf-8") as f:
            template_content = f.read()
            return self.jinja_env.from_string(template_content)

    def get_system_prompt(self) -> str:
        """获取系统 Prompt"""
        return self.system_prompt

    def get_spot_system_prompt(self) -> str:
        """获取现货 Agent 系统 Prompt"""
        return self.spot_system_prompt

    def format_trading_prompt(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        multi_timeframe_trends: Dict[str, str],
        current_positions: list,
        max_positions: int,
        max_trade_amount: float,
        max_leverage: int,
        take_profit_ratio: float,
        stop_loss_ratio: float,
        historical_summary: Optional[str] = None,
        balance_info: Optional[Dict[str, float]] = None,
        enriched_data: Optional[Dict[str, Any]] = None
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
        current_price = market_data.get('current_price', 0)
        rsi = market_data.get('rsi', 0)
        macd = market_data.get('macd', 0)
        macd_signal = market_data.get('macd_signal', 0)
        macd_hist = market_data.get('macd_hist', 0)
        ma_7 = market_data.get('ma_7', 0)
        ma_25 = market_data.get('ma_25', 0)
        ma_99 = market_data.get('ma_99', 0)
        bb_upper = market_data.get('bb_upper', 0)
        bb_middle = market_data.get('bb_middle', 0)
        bb_lower = market_data.get('bb_lower', 0)
        bb_position = market_data.get('bb_position', 0.5)
        volume_change = market_data.get('volume_change', 0)

        # 获取详细持仓信息
        position_details = self.format_position_details(symbol, current_positions, current_price)

        # 判断持仓状态（保持向后兼容）
        has_long = position_details['has_position'] and position_details['position_side'] == 'long'
        has_short = position_details['has_position'] and position_details['position_side'] == 'short'
        position_count = len(current_positions)

        # 格式化多周期趋势
        trends_text = ""
        for timeframe in ['日线', '4小时', '1小时', '15分钟', '1分钟']:
            trend = multi_timeframe_trends.get(timeframe, '未知')
            trends_text += f"- {timeframe}: {trend}\n"

        # 格式化历史汇总
        historical_text = ""
        if historical_summary:
            historical_text = f"""
## 📜 历史决策汇总

{historical_summary}

**提示:** 以上是你过去的决策记录汇总，可以帮助你理解市场演变和之前的策略。但请基于当前市场数据做出独立判断。
"""

        # 格式化账户余额信息
        balance_text = ""
        if balance_info:
            total = balance_info.get('total', 0)
            occupied = balance_info.get('occupied', 0)
            available = balance_info.get('available', 0)
            unrealized_pnl = balance_info.get('unrealized_pnl', 0)
            pnl_emoji = "📈" if unrealized_pnl > 0 else ("📉" if unrealized_pnl < 0 else "➖")
            balance_text = f"""
## 💰 账户余额（实时）

- **账户总价值**: ${total:.2f}
- **已占用保证金**: ${occupied:.2f}
- **可用余额**: ${available:.2f}
- **未实现盈亏**: ${unrealized_pnl:+.2f} {pnl_emoji}

**重要提示**:
- 你必须根据可用余额决定是否开仓
- 如果可用余额不足以支持交易，必须选择 do_nothing
- 关注未实现盈亏，如果亏损较大应更谨慎
"""

        # 计算手续费相关的值
        position_value = max_trade_amount * max_leverage
        open_fee = position_value * 0.00035
        close_fee = position_value * 0.00035
        total_fee = open_fee + close_fee
        breakeven_percent = f"{(total_fee / max_trade_amount) * 100:.2f}%"
        price_move_percent = f"{0.07 / max_leverage:.3f}%"

        # 准备模板上下文（使用 Jinja2 渲染）
        context = {
            # 基础信息
            'symbol': symbol,
            'coin': symbol,  # 币种别名

            # 币种类型判断（用于条件逻辑）
            'is_BTC': symbol == 'BTC',
            'is_ETH': symbol == 'ETH',
            'is_SOL': symbol == 'SOL',
            'is_major_coin': symbol in ['BTC', 'ETH'],  # 主流币
            'is_altcoin': symbol not in ['BTC', 'ETH'],  # 山寨币

            # 市场数据
            'current_price': f"{current_price:.2f}",
            'current_price_raw': current_price,
            'rsi': f"{rsi:.2f}",
            'rsi_raw': rsi,
            'macd': f"{macd:.4f}",
            'macd_raw': macd,
            'macd_signal': f"{macd_signal:.4f}",
            'macd_signal_raw': macd_signal,
            'macd_hist': f"{macd_hist:.4f}",
            'macd_hist_raw': macd_hist,
            'ma_7': f"{ma_7:.2f}",
            'ma_7_raw': ma_7,
            'ma_25': f"{ma_25:.2f}",
            'ma_25_raw': ma_25,
            'ma_99': f"{ma_99:.2f}",
            'ma_99_raw': ma_99,
            'bb_upper': f"{bb_upper:.2f}",
            'bb_upper_raw': bb_upper,
            'bb_middle': f"{bb_middle:.2f}",
            'bb_middle_raw': bb_middle,
            'bb_lower': f"{bb_lower:.2f}",
            'bb_lower_raw': bb_lower,
            'bb_position': f"{bb_position:.2%}",
            'bb_position_raw': bb_position,
            'volume_change': f"{volume_change:.2f}",
            'volume_change_raw': volume_change,
            'multi_timeframe_trends': trends_text,

            # 持仓信息（基础）
            'position_count': position_count,
            'max_positions': max_positions,
            'has_long': "是 ✅" if has_long else "否 ❌",
            'has_long_bool': has_long,
            'has_short': "是 ✅" if has_short else "否 ❌",
            'has_short_bool': has_short,

            # 当前币种详细持仓信息
            'has_position': position_details['has_position'],
            'position_side': position_details['position_side'],
            'entry_price': f"{position_details['entry_price']:.2f}",
            'entry_price_raw': position_details['entry_price'],
            'position_size': f"{position_details['position_size']:.4f}",
            'position_size_raw': position_details['position_size'],
            'position_value': f"{position_details['position_value']:.2f}",
            'position_value_raw': position_details['position_value'],
            'position_leverage': position_details['leverage'],
            'position_unrealized_pnl': f"{position_details['unrealized_pnl']:+.2f}",
            'position_unrealized_pnl_raw': position_details['unrealized_pnl'],
            'position_unrealized_pnl_percent': f"{position_details['unrealized_pnl_percent']:+.2f}",
            'position_unrealized_pnl_percent_raw': position_details['unrealized_pnl_percent'],
            'position_margin_used': f"{position_details['margin_used']:.2f}",
            'position_margin_used_raw': position_details['margin_used'],
            'position_liquidation_price': f"{position_details['liquidation_price']:.2f}",
            'position_liquidation_price_raw': position_details['liquidation_price'],
            'position_price_change_percent': f"{position_details['price_change_percent']:+.2f}",
            'position_price_change_percent_raw': position_details['price_change_percent'],
            'position_distance_from_entry': f"{position_details['distance_from_entry']:.2f}",
            'position_distance_from_entry_raw': position_details['distance_from_entry'],
            'position_distance_to_liquidation': f"{position_details['distance_to_liquidation']:.2f}",
            'position_distance_to_liquidation_raw': position_details['distance_to_liquidation'],
            'position_details_text': position_details['position_text'],

            # 交易参数
            'max_trade_amount': f"{max_trade_amount:.2f}",
            'max_trade_amount_raw': max_trade_amount,
            'max_leverage': max_leverage,
            'take_profit_ratio': f"{take_profit_ratio:.1%}",
            'take_profit_ratio_raw': take_profit_ratio,
            'stop_loss_ratio': f"{stop_loss_ratio:.1%}",
            'stop_loss_ratio_raw': stop_loss_ratio,

            # 费用计算
            'position_value': f"{position_value:.2f}",
            'position_value_raw': position_value,
            'open_fee': f"{open_fee:.2f}",
            'open_fee_raw': open_fee,
            'close_fee': f"{close_fee:.2f}",
            'close_fee_raw': close_fee,
            'total_fee': f"{total_fee:.2f}",
            'total_fee_raw': total_fee,
            'breakeven_percent': breakeven_percent,
            'price_move_percent': price_move_percent,

            # 历史和余额信息
            'historical_summary': historical_text,
            'balance_info': balance_text,
        }

        # 添加enriched_data中的额外字段（用于nof1和nof1-improved prompts）
        if enriched_data:
            # 直接合并所有enriched_data字段
            context.update(enriched_data)

            # 确保关键字段有默认值
            context.setdefault('elapsed_minutes', 0)
            context.setdefault('mid_prices', [])
            context.setdefault('ema_indicators', [])
            context.setdefault('macd_indicators', [])
            context.setdefault('rsi_7_indicators', [])
            context.setdefault('rsi_14_indicators', [])
            context.setdefault('current_ema20', current_price)
            context.setdefault('current_rsi', rsi)
            context.setdefault('oi_latest', 0)
            context.setdefault('oi_average', 0)
            context.setdefault('funding_rate', 0)
            context.setdefault('ema_20_4h', current_price)
            context.setdefault('ema_50_4h', current_price)
            context.setdefault('atr_3_4h', 0)
            context.setdefault('atr_14_4h', 0)
            context.setdefault('current_volume', 0)
            context.setdefault('avg_volume', 0)
            context.setdefault('macd_4h_indicators', [])
            context.setdefault('rsi_14_4h_indicators', [])
            context.setdefault('total_return_pct', 0)
            context.setdefault('account_value', 10000)
            context.setdefault('available_cash', balance_info.get('available', 0) if balance_info else 0)
            context.setdefault('sharpe_ratio', 0)
            context.setdefault('current_positions', str(current_positions))

        # 使用 Jinja2 渲染模板
        prompt = self.trading_prompt_template.render(context)

        return prompt

    def format_spot_prompt(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        multi_timeframe_trends: Dict[str, str],
        recommendation: Dict[str, Any],
        current_spot_holdings: list,
        max_trade_amount: float,
        balance_info: Optional[Dict[str, float]] = None
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
        current_price = market_data.get('current_price', 0)
        rsi = market_data.get('rsi', 0)
        macd_hist = market_data.get('macd_hist', 0)
        ma_7 = market_data.get('ma_7', 0)
        ma_25 = market_data.get('ma_25', 0)
        ma_99 = market_data.get('ma_99', 0)
        bb_position = market_data.get('bb_position', 0.5)
        volume_change = market_data.get('volume_change', 0)

        # 检查是否已持有该现货
        has_spot = any(h.get('symbol') == symbol for h in current_spot_holdings)

        # 格式化多周期趋势
        trends_text = ""
        for timeframe in ['日线', '4小时', '1小时', '15分钟', '1分钟']:
            trend = multi_timeframe_trends.get(timeframe, '未知')
            trends_text += f"- {timeframe}: {trend}\n"

        # 格式化账户余额信息
        balance_text = ""
        if balance_info:
            total = balance_info.get('total', 0)
            occupied = balance_info.get('occupied', 0)
            available = balance_info.get('available', 0)
            unrealized_pnl = balance_info.get('unrealized_pnl', 0)
            pnl_emoji = "📈" if unrealized_pnl > 0 else ("📉" if unrealized_pnl < 0 else "➖")
            balance_text = f"""
## 💰 账户余额（实时）

- **账户总价值**: ${total:.2f}
- **已占用保证金**: ${occupied:.2f}
- **可用余额**: ${available:.2f}
- **未实现盈亏**: ${unrealized_pnl:+.2f} {pnl_emoji}

**重要提示**:
- 你必须根据可用余额决定是否定投
- 如果可用余额不足，必须选择 do_nothing
- 关注未实现盈亏，如果亏损较大应更谨慎
"""

        # 准备模板上下文（使用 Jinja2 渲染）
        context = {
            # 基础信息
            'symbol': symbol,
            'coin': symbol,

            # 币种类型判断
            'is_BTC': symbol == 'BTC',
            'is_ETH': symbol == 'ETH',
            'is_SOL': symbol == 'SOL',
            'is_major_coin': symbol in ['BTC', 'ETH'],
            'is_altcoin': symbol not in ['BTC', 'ETH'],

            # 推荐信息
            'recommendation_reason': recommendation.get('reason', '未提供原因'),
            'recommendation_timestamp': recommendation.get('timestamp', '未知时间'),

            # 市场数据
            'current_price': f"{current_price:.2f}",
            'current_price_raw': current_price,
            'has_spot': "已持有 ✅" if has_spot else "未持有 ❌",
            'has_spot_bool': has_spot,
            'rsi': f"{rsi:.2f}",
            'rsi_raw': rsi,
            'macd_hist': f"{macd_hist:.4f}",
            'macd_hist_raw': macd_hist,
            'ma_7': f"{ma_7:.2f}",
            'ma_7_raw': ma_7,
            'ma_25': f"{ma_25:.2f}",
            'ma_25_raw': ma_25,
            'ma_99': f"{ma_99:.2f}",
            'ma_99_raw': ma_99,
            'bb_position': f"{bb_position:.2%}",
            'bb_position_raw': bb_position,
            'volume_change': f"{volume_change:.2f}",
            'volume_change_raw': volume_change,
            'multi_timeframe_trends': trends_text,

            # 交易参数
            'max_trade_amount': f"{max_trade_amount:.2f}",
            'max_trade_amount_raw': max_trade_amount,

            # 余额信息
            'balance_info': balance_text,
        }

        # 使用 Jinja2 渲染模板
        prompt = self.spot_prompt_template.render(context)

        return prompt

    def get_prompt_set_info(self) -> Dict[str, str]:
        """获取当前 Prompt 集合的信息"""
        return {
            "name": self.prompt_set["name"],
            "description": self.prompt_set["description"],
            "set_name": self.prompt_set_name
        }


def get_prompt_manager(config_file: str = "prompts/prompts.yaml", prompt_set: str = "default") -> PromptManager:
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
