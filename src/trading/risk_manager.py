"""
增强的风险管理模块
提供动态止盈止损、仓位管理、风险评估和资金管理功能

核心功能：
1. 动态止盈止损（基于ATR和波动性）
2. 智能仓位管理（基于风险评估和账户状态）
3. 风险暴露监控
4. 资金分配策略
5. 追踪止损机制
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    """风险级别"""

    VERY_LOW = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    VERY_HIGH = 5
    EXTREME = 6


class PositionSizingMethod(Enum):
    """仓位计算方法"""

    FIXED_AMOUNT = "fixed_amount"  # 固定金额
    FIXED_PERCENTAGE = "fixed_percentage"  # 固定账户比例
    KELLY_CRITERION = "kelly_criterion"  # 凯利公式
    ATR_BASED = "atr_based"  # 基于ATR
    VOLATILITY_ADJUSTED = "volatility_adjusted"  # 波动性调整


@dataclass
class RiskParameters:
    """风险参数配置"""

    # 基础风险参数
    max_risk_per_trade: float = 0.02  # 单笔最大风险（账户百分比）
    max_total_exposure: float = 0.5  # 最大总敞口（账户百分比）
    max_single_position: float = 0.2  # 单个仓位最大占比
    max_correlated_exposure: float = 0.3  # 相关资产最大敞口

    # 止损止盈参数
    default_stop_loss_pct: float = 0.02  # 默认止损百分比
    default_take_profit_pct: float = 0.05  # 默认止盈百分比
    min_risk_reward_ratio: float = 1.5  # 最小风险回报比

    # ATR相关参数
    atr_stop_loss_multiplier: float = 1.5  # ATR止损倍数
    atr_take_profit_multiplier: float = 3.0  # ATR止盈倍数

    # 追踪止损参数
    trailing_stop_enabled: bool = True
    trailing_stop_distance_pct: float = 0.015  # 追踪止损距离
    trailing_stop_activation_pct: float = 0.01  # 激活追踪止损的盈利百分比

    # 波动性调整参数
    volatility_adjustment_enabled: bool = True
    low_volatility_multiplier: float = 0.7  # 低波动时止损收紧
    high_volatility_multiplier: float = 1.5  # 高波动时止损放宽

    # 时间相关参数
    max_holding_periods: int = 100  # 最大持仓周期数


@dataclass
class StopLossResult:
    """止损计算结果"""

    stop_loss_price: float
    stop_loss_pct: float
    method: str  # 计算方法描述
    atr_multiplier: float
    volatility_adjusted: bool
    distance_from_entry: float  # 距离入场价的距离


@dataclass
class TakeProfitResult:
    """止盈计算结果"""

    take_profit_price: float
    take_profit_pct: float
    method: str
    atr_multiplier: float
    risk_reward_ratio: float
    partial_targets: list[tuple[float, float]] = field(default_factory=list)  # [(价格, 平仓比例)]


@dataclass
class PositionSizeResult:
    """仓位大小计算结果"""

    position_size: float  # 建议仓位大小（USDT）
    position_pct: float  # 占账户比例
    leverage: int  # 建议杠杆
    risk_amount: float  # 风险金额
    method: str  # 计算方法
    adjustments: list[str] = field(default_factory=list)  # 调整说明


@dataclass
class RiskAssessment:
    """风险评估结果"""

    risk_level: RiskLevel
    risk_score: float  # 0-100
    factors: dict[str, float]  # 各因素的风险贡献
    warnings: list[str]
    recommendations: list[str]
    can_trade: bool
    max_suggested_position: float


@dataclass
class TrailingStopState:
    """追踪止损状态"""

    is_active: bool
    activation_price: float
    current_stop_price: float
    highest_price: float  # 多头用
    lowest_price: float  # 空头用
    last_update_time: str


class RiskManager:
    """风险管理器"""

    def __init__(
        self,
        risk_params: RiskParameters | None = None,
        position_sizing_method: PositionSizingMethod = PositionSizingMethod.VOLATILITY_ADJUSTED,
    ):
        """
        初始化风险管理器

        Args:
            risk_params: 风险参数配置
            position_sizing_method: 仓位计算方法
        """
        self.params = risk_params or RiskParameters()
        self.sizing_method = position_sizing_method
        self._trailing_stops: dict[str, TrailingStopState] = {}

    def calculate_dynamic_stop_loss(
        self,
        entry_price: float,
        is_long: bool,
        current_atr: float,
        volatility_state: str = "normal",
        support_level: float | None = None,
        resistance_level: float | None = None,
    ) -> StopLossResult:
        """
        计算动态止损价格

        Args:
            entry_price: 入场价格
            is_long: 是否做多
            current_atr: 当前ATR值
            volatility_state: 波动性状态 ("low", "normal", "high", "extreme")
            support_level: 支撑位（用于多头止损参考）
            resistance_level: 阻力位（用于空头止损参考）

        Returns:
            StopLossResult: 止损计算结果
        """
        # 价格守卫：entry_price 为 0/负（行情缺失或数据损坏）会在 L236 触发除零，
        # 抛出明确异常由上层 except 捕获 → 安全跳过本轮，而非崩溃整个决策循环
        if entry_price <= 0:
            raise ValueError(
                f"entry_price 必须为正数，实际为 {entry_price}（疑似行情数据缺失/损坏）"
            )

        # 基础ATR倍数
        atr_multiplier = self.params.atr_stop_loss_multiplier

        # 波动性调整
        volatility_adjusted = False
        if self.params.volatility_adjustment_enabled:
            if volatility_state == "low":
                atr_multiplier *= self.params.low_volatility_multiplier
                volatility_adjusted = True
            elif volatility_state in ["high", "extreme"]:
                atr_multiplier *= self.params.high_volatility_multiplier
                volatility_adjusted = True

        # 计算基于ATR的止损距离
        atr_stop_distance = current_atr * atr_multiplier

        if is_long:
            # 多头止损在入场价下方
            atr_stop_price = entry_price - atr_stop_distance

            # 如果有支撑位，考虑将止损放在支撑位下方
            if support_level and support_level < entry_price:
                support_stop = support_level * 0.995  # 支撑位下方0.5%
                # 选择更保守的止损（距离入场价更近的）
                stop_price = max(atr_stop_price, support_stop)
                method = "ATR + 支撑位融合"
            else:
                stop_price = atr_stop_price
                method = "ATR动态止损"
        else:
            # 空头止损在入场价上方
            atr_stop_price = entry_price + atr_stop_distance

            # 如果有阻力位，考虑将止损放在阻力位上方
            if resistance_level and resistance_level > entry_price:
                resistance_stop = resistance_level * 1.005  # 阻力位上方0.5%
                stop_price = min(atr_stop_price, resistance_stop)
                method = "ATR + 阻力位融合"
            else:
                stop_price = atr_stop_price
                method = "ATR动态止损"

        # 确保止损不会太近或太远
        min_stop_distance = entry_price * 0.005  # 最小0.5%
        max_stop_distance = entry_price * 0.15  # 最大15%

        actual_distance = abs(entry_price - stop_price)
        if actual_distance < min_stop_distance:
            if is_long:
                stop_price = entry_price - min_stop_distance
            else:
                stop_price = entry_price + min_stop_distance
            method += " (调整为最小距离)"
        elif actual_distance > max_stop_distance:
            if is_long:
                stop_price = entry_price - max_stop_distance
            else:
                stop_price = entry_price + max_stop_distance
            method += " (调整为最大距离)"

        stop_loss_pct = abs(entry_price - stop_price) / entry_price

        return StopLossResult(
            stop_loss_price=stop_price,
            stop_loss_pct=stop_loss_pct,
            method=method,
            atr_multiplier=atr_multiplier,
            volatility_adjusted=volatility_adjusted,
            distance_from_entry=abs(entry_price - stop_price),
        )

    def calculate_dynamic_take_profit(
        self,
        entry_price: float,
        stop_loss_price: float,
        is_long: bool,
        current_atr: float,
        resistance_level: float | None = None,
        support_level: float | None = None,
        min_risk_reward: float | None = None,
    ) -> TakeProfitResult:
        """
        计算动态止盈价格

        Args:
            entry_price: 入场价格
            stop_loss_price: 止损价格
            is_long: 是否做多
            current_atr: 当前ATR值
            resistance_level: 阻力位（多头止盈参考）
            support_level: 支撑位（空头止盈参考）
            min_risk_reward: 最小风险回报比

        Returns:
            TakeProfitResult: 止盈计算结果
        """
        # 价格守卫：entry_price 为 0/负会在 L332 触发除零
        if entry_price <= 0:
            raise ValueError(
                f"entry_price 必须为正数，实际为 {entry_price}（疑似行情数据缺失/损坏）"
            )

        min_rr = min_risk_reward or self.params.min_risk_reward_ratio
        atr_multiplier = self.params.atr_take_profit_multiplier

        # 计算风险金额
        risk_amount = abs(entry_price - stop_loss_price)

        # 基于风险回报比的最小止盈距离
        min_profit_distance = risk_amount * min_rr

        # 基于ATR的止盈距离
        atr_profit_distance = current_atr * atr_multiplier

        # 取较大值
        profit_distance = max(min_profit_distance, atr_profit_distance)

        if is_long:
            atr_tp_price = entry_price + profit_distance

            # 如果有阻力位，考虑在阻力位附近止盈
            if resistance_level and resistance_level > entry_price:
                resistance_tp = resistance_level * 0.995  # 阻力位下方0.5%
                # 如果阻力位更近且满足最小风险回报比
                if resistance_tp < atr_tp_price:
                    potential_rr = (resistance_tp - entry_price) / risk_amount
                    if potential_rr >= min_rr:
                        tp_price = resistance_tp
                        method = "阻力位止盈"
                    else:
                        tp_price = atr_tp_price
                        method = "ATR动态止盈"
                else:
                    tp_price = atr_tp_price
                    method = "ATR动态止盈"
            else:
                tp_price = atr_tp_price
                method = "ATR动态止盈"
        else:
            atr_tp_price = entry_price - profit_distance

            if support_level and support_level < entry_price:
                support_tp = support_level * 1.005
                if support_tp > atr_tp_price:
                    potential_rr = (entry_price - support_tp) / risk_amount
                    if potential_rr >= min_rr:
                        tp_price = support_tp
                        method = "支撑位止盈"
                    else:
                        tp_price = atr_tp_price
                        method = "ATR动态止盈"
                else:
                    tp_price = atr_tp_price
                    method = "ATR动态止盈"
            else:
                tp_price = atr_tp_price
                method = "ATR动态止盈"

        # 计算实际风险回报比
        actual_profit = abs(tp_price - entry_price)
        risk_reward_ratio = actual_profit / risk_amount if risk_amount > 0 else 1.0

        take_profit_pct = abs(tp_price - entry_price) / entry_price

        # 生成分批止盈目标
        partial_targets = self._generate_partial_targets(entry_price, tp_price, is_long)

        return TakeProfitResult(
            take_profit_price=tp_price,
            take_profit_pct=take_profit_pct,
            method=method,
            atr_multiplier=atr_multiplier,
            risk_reward_ratio=risk_reward_ratio,
            partial_targets=partial_targets,
        )

    def _generate_partial_targets(
        self, entry_price: float, final_tp: float, is_long: bool
    ) -> list[tuple[float, float]]:
        """
        生成分批止盈目标

        Args:
            entry_price: 入场价格
            final_tp: 最终止盈价格
            is_long: 是否做多

        Returns:
            [(价格, 平仓比例), ...]
        """
        total_distance = abs(final_tp - entry_price)
        targets = []

        # 第一目标：33%距离，平仓30%
        tp1_distance = total_distance * 0.33
        if is_long:
            tp1 = entry_price + tp1_distance
        else:
            tp1 = entry_price - tp1_distance
        targets.append((tp1, 0.3))

        # 第二目标：66%距离，平仓40%
        tp2_distance = total_distance * 0.66
        if is_long:
            tp2 = entry_price + tp2_distance
        else:
            tp2 = entry_price - tp2_distance
        targets.append((tp2, 0.4))

        # 最终目标：平仓剩余30%
        targets.append((final_tp, 0.3))

        return targets

    def calculate_position_size(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss_price: float,
        leverage: int,
        current_atr: float | None = None,
        volatility_state: str = "normal",
        win_rate: float | None = None,
        avg_win_loss_ratio: float | None = None,
    ) -> PositionSizeResult:
        """
        计算建议仓位大小

        Args:
            account_balance: 账户余额
            entry_price: 入场价格
            stop_loss_price: 止损价格
            leverage: 杠杆倍数
            current_atr: 当前ATR（用于波动性调整）
            volatility_state: 波动性状态
            win_rate: 胜率（用于凯利公式）
            avg_win_loss_ratio: 平均盈亏比（用于凯利公式）

        Returns:
            PositionSizeResult: 仓位计算结果
        """
        adjustments = []

        # 价格守卫：entry_price 为 0/负会触发除零（L414 及后续 position_size 计算）
        if entry_price <= 0:
            raise ValueError(
                f"entry_price 必须为正数，实际为 {entry_price}（疑似行情数据缺失/损坏）"
            )
        # 杠杆守卫：非法杠杆会导致 position_size 除零或负仓位
        if leverage <= 0:
            leverage = 1
            adjustments.append("杠杆非法，回退为 1x")

        # 计算每单位风险
        risk_per_unit = abs(entry_price - stop_loss_price) / entry_price
        # 止损距离为 0（入场价==止损价，异常信号）会使后续 risk_amount/(risk_per_unit*leverage)
        # 触发除零；回退到最小风险距离 0.5%（与 calculate_dynamic_stop_loss 的最小止损一致）
        if risk_per_unit <= 0:
            risk_per_unit = 0.005
            adjustments.append("止损距离为 0，回退最小风险 0.5% 以避免除零")

        if self.sizing_method == PositionSizingMethod.FIXED_AMOUNT:
            # 固定金额法
            position_size = account_balance * self.params.max_single_position
            method = "固定金额法"

        elif self.sizing_method == PositionSizingMethod.FIXED_PERCENTAGE:
            # 固定风险百分比法
            risk_amount = account_balance * self.params.max_risk_per_trade
            # 考虑杠杆后的实际风险
            position_size = risk_amount / (risk_per_unit * leverage)
            method = "固定风险百分比法"

        elif self.sizing_method == PositionSizingMethod.KELLY_CRITERION:
            # 凯利公式
            # 参数合法性校验：胜率须在 (0,1]，盈亏比须为正，否则凯利公式无意义
            # （负盈亏比会翻转符号、win_rate 越界会产出错误仓位），非法时回退固定风险法
            if win_rate and avg_win_loss_ratio and 0 < win_rate <= 1 and avg_win_loss_ratio > 0:
                # Kelly % = W - (1-W)/R
                kelly_pct = win_rate - (1 - win_rate) / avg_win_loss_ratio
                # 使用半凯利降低风险
                kelly_pct = max(0, kelly_pct * 0.5)
                position_size = account_balance * kelly_pct
                method = f"凯利公式 (半凯利={kelly_pct:.2%})"
            else:
                # 无历史数据，回退到固定风险法
                risk_amount = account_balance * self.params.max_risk_per_trade
                position_size = risk_amount / (risk_per_unit * leverage)
                method = "固定风险百分比法 (无凯利数据)"
                adjustments.append("缺少历史胜率数据")

        elif self.sizing_method == PositionSizingMethod.ATR_BASED:
            # 基于ATR的仓位计算
            if current_atr:
                # 风险金额 = 账户 * 最大风险比例
                risk_amount = account_balance * self.params.max_risk_per_trade
                # ATR风险 = ATR * 止损倍数
                atr_risk = current_atr * self.params.atr_stop_loss_multiplier
                atr_risk_pct = atr_risk / entry_price
                # 仓位 = 风险金额 / ATR风险
                position_size = risk_amount / (atr_risk_pct * leverage)
                method = "ATR风险调整法"
            else:
                risk_amount = account_balance * self.params.max_risk_per_trade
                position_size = risk_amount / (risk_per_unit * leverage)
                method = "固定风险百分比法 (无ATR数据)"
                adjustments.append("缺少ATR数据")

        else:  # VOLATILITY_ADJUSTED
            # 波动性调整法
            risk_amount = account_balance * self.params.max_risk_per_trade
            base_position = risk_amount / (risk_per_unit * leverage)

            # 根据波动性调整
            vol_multiplier = 1.0
            if volatility_state == "low":
                vol_multiplier = 1.2  # 低波动可以略微增加仓位
                adjustments.append("低波动性: 仓位+20%")
            elif volatility_state == "high":
                vol_multiplier = 0.7  # 高波动减少仓位
                adjustments.append("高波动性: 仓位-30%")
            elif volatility_state == "extreme":
                vol_multiplier = 0.5  # 极端波动大幅减少
                adjustments.append("极端波动: 仓位-50%")

            position_size = base_position * vol_multiplier
            method = "波动性调整法"

        # 应用上限约束
        max_position = account_balance * self.params.max_single_position
        if position_size > max_position:
            position_size = max_position
            adjustments.append(f"达到单仓位上限 ({self.params.max_single_position:.0%})")

        # 确保不超过账户余额
        if position_size > account_balance:
            position_size = account_balance * 0.8
            adjustments.append("调整为账户余额的80%")

        # 计算实际风险金额
        actual_risk = position_size * risk_per_unit * leverage
        position_pct = position_size / account_balance

        # 建议杠杆调整
        suggested_leverage = leverage
        if volatility_state == "extreme" and leverage > 3:
            suggested_leverage = min(leverage, 3)
            adjustments.append(f"极端波动: 建议降低杠杆至{suggested_leverage}x")
        elif volatility_state == "high" and leverage > 5:
            suggested_leverage = min(leverage, 5)
            adjustments.append(f"高波动: 建议降低杠杆至{suggested_leverage}x")

        return PositionSizeResult(
            position_size=position_size,
            position_pct=position_pct,
            leverage=suggested_leverage,
            risk_amount=actual_risk,
            method=method,
            adjustments=adjustments,
        )

    def assess_risk(
        self,
        account_balance: float,
        current_positions: list[dict[str, Any]],
        proposed_trade: dict[str, Any] | None = None,
        market_volatility: str = "normal",
    ) -> RiskAssessment:
        """
        评估当前风险状态

        Args:
            account_balance: 账户余额
            current_positions: 当前持仓列表
            proposed_trade: 拟议交易 (可选)
            market_volatility: 市场波动性状态

        Returns:
            RiskAssessment: 风险评估结果
        """
        factors = {}
        warnings = []
        recommendations = []

        # 1. 计算当前总敞口
        total_exposure = 0
        for pos in current_positions:
            pos_value = abs(float(pos.get("szi", 0)) * float(pos.get("entryPx", 0)))
            total_exposure += pos_value

        exposure_ratio = total_exposure / account_balance if account_balance > 0 else 0
        factors["exposure_ratio"] = min(100, exposure_ratio * 100)

        if exposure_ratio > self.params.max_total_exposure:
            warnings.append(
                f"总敞口 {exposure_ratio:.1%} 超过上限 {self.params.max_total_exposure:.1%}"
            )

        # 2. 计算单一资产集中度
        position_concentration = {}
        for pos in current_positions:
            symbol = pos.get("coin", "UNKNOWN")
            pos_value = abs(float(pos.get("szi", 0)) * float(pos.get("entryPx", 0)))
            concentration = pos_value / account_balance if account_balance > 0 else 0
            position_concentration[symbol] = concentration

            if concentration > self.params.max_single_position:
                warnings.append(f"{symbol} 集中度 {concentration:.1%} 超过上限")

        max_concentration = max(position_concentration.values()) if position_concentration else 0
        factors["concentration"] = min(100, max_concentration * 100)

        # 3. 未实现盈亏风险
        total_unrealized_pnl = 0
        for pos in current_positions:
            pnl = float(pos.get("unrealizedPnl", 0))
            total_unrealized_pnl += pnl

        pnl_ratio = total_unrealized_pnl / account_balance if account_balance > 0 else 0
        if pnl_ratio < -0.1:  # 浮亏超过10%
            factors["unrealized_loss"] = min(100, abs(pnl_ratio) * 200)
            warnings.append(f"总未实现亏损 {pnl_ratio:.1%}")
        else:
            factors["unrealized_loss"] = 0

        # 4. 波动性风险
        vol_risk = {"low": 10, "normal": 30, "high": 60, "extreme": 90}
        factors["volatility"] = vol_risk.get(market_volatility, 30)

        if market_volatility == "extreme":
            warnings.append("市场波动极大，建议减少交易")
        elif market_volatility == "high":
            warnings.append("市场波动较大，建议谨慎")

        # 5. 持仓数量风险
        num_positions = len(current_positions)
        if num_positions > 5:
            factors["position_count"] = min(100, num_positions * 10)
            warnings.append(f"持仓数量较多 ({num_positions})，管理难度增加")
        else:
            factors["position_count"] = num_positions * 5

        # 6. 如果有拟议交易，评估新增风险
        if proposed_trade:
            new_exposure = proposed_trade.get("size", 0) * proposed_trade.get("entry_price", 0)
            new_total = total_exposure + new_exposure
            new_exposure_ratio = new_total / account_balance if account_balance > 0 else 0

            if new_exposure_ratio > self.params.max_total_exposure:
                warnings.append(f"新交易将使总敞口达到 {new_exposure_ratio:.1%}，超过上限")
                factors["proposed_trade"] = 80
            else:
                factors["proposed_trade"] = new_exposure_ratio * 50

        # 计算综合风险分数 (0-100)
        weights = {
            "exposure_ratio": 0.25,
            "concentration": 0.20,
            "unrealized_loss": 0.20,
            "volatility": 0.20,
            "position_count": 0.10,
            "proposed_trade": 0.05,
        }

        risk_score = sum(factors.get(k, 0) * w for k, w in weights.items())

        # 确定风险级别
        if risk_score < 20:
            risk_level = RiskLevel.VERY_LOW
        elif risk_score < 35:
            risk_level = RiskLevel.LOW
        elif risk_score < 50:
            risk_level = RiskLevel.MEDIUM
        elif risk_score < 70:
            risk_level = RiskLevel.HIGH
        elif risk_score < 85:
            risk_level = RiskLevel.VERY_HIGH
        else:
            risk_level = RiskLevel.EXTREME

        # 生成建议
        if risk_level in [RiskLevel.HIGH, RiskLevel.VERY_HIGH, RiskLevel.EXTREME]:
            recommendations.append("建议减少新开仓")
            recommendations.append("考虑对冲或减仓")

        if exposure_ratio > 0.3:
            recommendations.append("考虑降低总敞口")

        if max_concentration > 0.15:
            recommendations.append("建议分散投资，降低单一资产集中度")

        # 是否可以交易
        can_trade = (
            risk_level.value <= RiskLevel.HIGH.value
            and exposure_ratio < self.params.max_total_exposure * 1.1
        )

        # 计算最大建议仓位
        remaining_capacity = max(0, self.params.max_total_exposure - exposure_ratio)
        max_suggested_position = account_balance * remaining_capacity * 0.5  # 保守估计

        return RiskAssessment(
            risk_level=risk_level,
            risk_score=risk_score,
            factors=factors,
            warnings=warnings,
            recommendations=recommendations,
            can_trade=can_trade,
            max_suggested_position=max_suggested_position,
        )

    def initialize_trailing_stop(
        self, symbol: str, entry_price: float, is_long: bool, initial_stop: float
    ) -> TrailingStopState:
        """
        初始化追踪止损

        Args:
            symbol: 交易对符号
            entry_price: 入场价格
            is_long: 是否做多
            initial_stop: 初始止损价

        Returns:
            TrailingStopState: 追踪止损状态
        """
        activation_distance = entry_price * self.params.trailing_stop_activation_pct

        if is_long:
            activation_price = entry_price + activation_distance
        else:
            activation_price = entry_price - activation_distance

        state = TrailingStopState(
            is_active=False,
            activation_price=activation_price,
            current_stop_price=initial_stop,
            highest_price=entry_price if is_long else float("inf"),
            lowest_price=entry_price if not is_long else 0,
            last_update_time=datetime.now().isoformat(),
        )

        self._trailing_stops[symbol] = state
        return state

    def update_trailing_stop(
        self, symbol: str, current_price: float, is_long: bool
    ) -> TrailingStopState | None:
        """
        更新追踪止损

        Args:
            symbol: 交易对符号
            current_price: 当前价格
            is_long: 是否做多

        Returns:
            更新后的追踪止损状态
        """
        if symbol not in self._trailing_stops:
            return None

        state = self._trailing_stops[symbol]

        if is_long:
            # 多头：跟踪最高价
            if current_price > state.highest_price:
                state.highest_price = current_price

            # 检查是否激活追踪止损
            if not state.is_active and current_price >= state.activation_price:
                state.is_active = True

            # 如果已激活，更新止损价
            if state.is_active:
                trailing_distance = state.highest_price * self.params.trailing_stop_distance_pct
                new_stop = state.highest_price - trailing_distance

                # 止损只能上移，不能下移
                if new_stop > state.current_stop_price:
                    state.current_stop_price = new_stop
        else:
            # 空头：跟踪最低价
            if current_price < state.lowest_price or state.lowest_price == 0:
                state.lowest_price = current_price

            if not state.is_active and current_price <= state.activation_price:
                state.is_active = True

            if state.is_active:
                trailing_distance = state.lowest_price * self.params.trailing_stop_distance_pct
                new_stop = state.lowest_price + trailing_distance

                # 止损只能下移，不能上移
                if new_stop < state.current_stop_price:
                    state.current_stop_price = new_stop

        state.last_update_time = datetime.now().isoformat()
        return state

    def should_close_position(
        self,
        symbol: str,
        current_price: float,
        is_long: bool,
        entry_price: float,
        holding_periods: int,
    ) -> tuple[bool, str]:
        """
        判断是否应该平仓

        Args:
            symbol: 交易对符号
            current_price: 当前价格
            is_long: 是否做多
            entry_price: 入场价格
            holding_periods: 持仓周期数

        Returns:
            (是否平仓, 原因)
        """
        # 检查追踪止损
        state = self._trailing_stops.get(symbol)
        if state and state.is_active:
            if (
                is_long
                and current_price <= state.current_stop_price
                or not is_long
                and current_price >= state.current_stop_price
            ):
                return True, f"触发追踪止损 (止损价: {state.current_stop_price:.2f})"

        # 检查最大持仓时间
        if holding_periods >= self.params.max_holding_periods:
            return (
                True,
                f"持仓超过最大周期数 ({holding_periods} >= {self.params.max_holding_periods})",
            )

        return False, ""

    def get_trailing_stop_state(self, symbol: str) -> TrailingStopState | None:
        """获取追踪止损状态"""
        return self._trailing_stops.get(symbol)

    def clear_trailing_stop(self, symbol: str):
        """清除追踪止损状态"""
        if symbol in self._trailing_stops:
            del self._trailing_stops[symbol]


def format_risk_assessment_for_prompt(assessment: RiskAssessment) -> str:
    """
    将风险评估格式化为可注入Prompt的文本

    Args:
        assessment: RiskAssessment 风险评估结果

    Returns:
        格式化的文本
    """
    lines = []
    lines.append("## ⚠️ 风险评估")
    lines.append(f"**风险级别**: {assessment.risk_level.name}")
    lines.append(f"**风险分数**: {assessment.risk_score:.1f}/100")
    lines.append(f"**是否可交易**: {'是' if assessment.can_trade else '否'}")
    lines.append(f"**最大建议仓位**: ${assessment.max_suggested_position:.2f}")
    lines.append("")

    if assessment.warnings:
        lines.append("### 警告")
        for w in assessment.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    if assessment.recommendations:
        lines.append("### 建议")
        for r in assessment.recommendations:
            lines.append(f"- 💡 {r}")
        lines.append("")

    lines.append("### 风险因素")
    for factor, value in assessment.factors.items():
        lines.append(f"- {factor}: {value:.1f}")

    return "\n".join(lines)
