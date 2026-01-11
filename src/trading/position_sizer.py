"""
动态仓位管理器
基于凯利公式、信号强度和风险评估动态计算最优仓位大小

核心功能：
1. 凯利公式计算最优仓位
2. 基于信号强度的仓位调整
3. 基于波动率的仓位调整
4. 风险预算控制
5. 连续亏损后的仓位收缩
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
import math


class PositionSizeMethod(str, Enum):
    """仓位计算方法"""
    FIXED = "fixed"  # 固定金额
    KELLY = "kelly"  # 凯利公式
    VOLATILITY_ADJUSTED = "volatility_adjusted"  # 波动率调整
    RISK_PARITY = "risk_parity"  # 风险平价
    SIGNAL_BASED = "signal_based"  # 基于信号强度


@dataclass
class PositionSizeResult:
    """仓位计算结果"""
    method: PositionSizeMethod
    base_size: float  # 基础仓位（美元）
    adjusted_size: float  # 调整后仓位（美元）
    size_ratio: float  # 相对于最大仓位的比例 (0-1)

    # 各项调整因子
    kelly_factor: float = 1.0
    signal_factor: float = 1.0
    volatility_factor: float = 1.0
    drawdown_factor: float = 1.0

    # 计算细节
    details: Dict[str, Any] = field(default_factory=dict)

    def get_summary(self) -> str:
        """获取摘要"""
        factors = []
        if self.kelly_factor != 1.0:
            factors.append(f"Kelly={self.kelly_factor:.2f}")
        if self.signal_factor != 1.0:
            factors.append(f"Signal={self.signal_factor:.2f}")
        if self.volatility_factor != 1.0:
            factors.append(f"Vol={self.volatility_factor:.2f}")
        if self.drawdown_factor != 1.0:
            factors.append(f"DD={self.drawdown_factor:.2f}")

        factor_str = ", ".join(factors) if factors else "无调整"
        return f"${self.adjusted_size:.2f} ({self.size_ratio:.0%}) [{factor_str}]"


@dataclass
class TradeHistory:
    """交易历史记录"""
    is_win: bool
    pnl_pct: float  # 盈亏百分比
    signal_score: float  # 当时的信号评分


class PositionSizer:
    """
    动态仓位管理器

    综合考虑多种因素计算最优仓位大小
    """

    def __init__(
        self,
        max_position_size: float = 1000.0,  # 单笔最大仓位（美元）
        max_account_risk: float = 0.02,  # 单笔最大账户风险 (2%)
        max_total_exposure: float = 0.5,  # 最大总敞口 (50%)
        kelly_fraction: float = 0.25,  # 凯利分数（使用1/4凯利）
        min_position_ratio: float = 0.1,  # 最小仓位比例
        max_position_ratio: float = 1.0,  # 最大仓位比例
        volatility_lookback: int = 20,  # 波动率回看周期
        drawdown_sensitivity: float = 2.0,  # 回撤敏感度
        win_rate_lookback: int = 20  # 胜率计算回看周期
    ):
        self.max_position_size = max_position_size
        self.max_account_risk = max_account_risk
        self.max_total_exposure = max_total_exposure
        self.kelly_fraction = kelly_fraction
        self.min_position_ratio = min_position_ratio
        self.max_position_ratio = max_position_ratio
        self.volatility_lookback = volatility_lookback
        self.drawdown_sensitivity = drawdown_sensitivity
        self.win_rate_lookback = win_rate_lookback

        # 交易历史
        self._trade_history: List[TradeHistory] = []

        # 当前回撤
        self._current_drawdown: float = 0.0
        self._peak_balance: float = 0.0

    def calculate_position_size(
        self,
        account_balance: float,
        signal_score: float,
        stop_loss_pct: float,
        current_volatility: Optional[float] = None,
        average_volatility: Optional[float] = None,
        current_exposure: float = 0.0,
        method: PositionSizeMethod = PositionSizeMethod.SIGNAL_BASED
    ) -> PositionSizeResult:
        """
        计算仓位大小

        Args:
            account_balance: 当前账户余额
            signal_score: 信号评分 (0-1)
            stop_loss_pct: 止损百分比
            current_volatility: 当前波动率（ATR/价格）
            average_volatility: 平均波动率
            current_exposure: 当前总敞口
            method: 计算方法

        Returns:
            PositionSizeResult
        """
        # 基础仓位：基于风险预算
        base_size = self._calculate_risk_based_size(
            account_balance, stop_loss_pct
        )

        # 不超过最大仓位限制
        base_size = min(base_size, self.max_position_size)

        # 计算各项调整因子
        kelly_factor = 1.0
        signal_factor = 1.0
        volatility_factor = 1.0
        drawdown_factor = 1.0

        details = {}

        # 1. 凯利因子
        if method in [PositionSizeMethod.KELLY, PositionSizeMethod.SIGNAL_BASED]:
            kelly_factor = self._calculate_kelly_factor(signal_score, stop_loss_pct)
            details['kelly'] = {
                'win_rate': self._get_win_rate(),
                'avg_win': self._get_average_win(),
                'avg_loss': self._get_average_loss(),
                'kelly_pct': kelly_factor
            }

        # 2. 信号因子
        if method in [PositionSizeMethod.SIGNAL_BASED]:
            signal_factor = self._calculate_signal_factor(signal_score)
            details['signal'] = {
                'score': signal_score,
                'factor': signal_factor
            }

        # 3. 波动率因子
        if method in [PositionSizeMethod.VOLATILITY_ADJUSTED, PositionSizeMethod.SIGNAL_BASED]:
            if current_volatility is not None and average_volatility is not None:
                volatility_factor = self._calculate_volatility_factor(
                    current_volatility, average_volatility
                )
                details['volatility'] = {
                    'current': current_volatility,
                    'average': average_volatility,
                    'factor': volatility_factor
                }

        # 4. 回撤因子
        drawdown_factor = self._calculate_drawdown_factor()
        if drawdown_factor != 1.0:
            details['drawdown'] = {
                'current_dd': self._current_drawdown,
                'factor': drawdown_factor
            }

        # 5. 敞口限制因子
        exposure_factor = self._calculate_exposure_factor(
            account_balance, current_exposure, base_size
        )
        details['exposure'] = {
            'current': current_exposure,
            'max': self.max_total_exposure,
            'factor': exposure_factor
        }

        # 综合计算调整后仓位
        combined_factor = (
            kelly_factor *
            signal_factor *
            volatility_factor *
            drawdown_factor *
            exposure_factor
        )

        # 限制在允许范围内
        combined_factor = max(
            self.min_position_ratio,
            min(self.max_position_ratio, combined_factor)
        )

        # 如果敞口因子为0，强制设为0
        if exposure_factor == 0:
            combined_factor = 0

        adjusted_size = base_size * combined_factor

        # 确保不超过账户余额的合理比例
        if stop_loss_pct > 0:
            max_per_trade = account_balance * self.max_account_risk / stop_loss_pct
            adjusted_size = min(adjusted_size, max_per_trade)

        # 确保不为负数
        adjusted_size = max(0, adjusted_size)

        size_ratio = adjusted_size / self.max_position_size if self.max_position_size > 0 else 0

        return PositionSizeResult(
            method=method,
            base_size=base_size,
            adjusted_size=adjusted_size,
            size_ratio=size_ratio,
            kelly_factor=kelly_factor,
            signal_factor=signal_factor,
            volatility_factor=volatility_factor,
            drawdown_factor=drawdown_factor,
            details=details
        )

    def _calculate_risk_based_size(
        self,
        account_balance: float,
        stop_loss_pct: float
    ) -> float:
        """
        基于风险预算计算仓位

        公式: 仓位 = 账户 * 最大风险 / 止损比例
        """
        if stop_loss_pct <= 0:
            return 0.0

        return account_balance * self.max_account_risk / stop_loss_pct

    def _calculate_kelly_factor(
        self,
        signal_score: float,
        stop_loss_pct: float
    ) -> float:
        """
        计算凯利因子

        凯利公式: f = (p * b - q) / b
        其中:
        - p = 胜率
        - q = 1 - p = 败率
        - b = 盈亏比

        使用分数凯利(1/4)以降低风险
        """
        win_rate = self._get_win_rate()
        avg_win = self._get_average_win()
        avg_loss = self._get_average_loss()

        # 如果没有足够的历史数据，使用信号评分作为代理
        if len(self._trade_history) < 5:
            # 使用信号评分估计胜率
            estimated_win_rate = 0.4 + signal_score * 0.2  # 40%-60%
            estimated_rr = 2.0  # 假设2:1盈亏比

            kelly = (estimated_win_rate * estimated_rr - (1 - estimated_win_rate)) / estimated_rr
        else:
            if avg_loss == 0:
                kelly = 0.5  # 默认值
            else:
                b = avg_win / avg_loss  # 盈亏比
                kelly = (win_rate * b - (1 - win_rate)) / b

        # 应用凯利分数（保守起见使用1/4凯利）
        kelly = kelly * self.kelly_fraction

        # 限制在合理范围
        kelly = max(0.1, min(1.0, kelly))

        return kelly

    def _calculate_signal_factor(self, signal_score: float) -> float:
        """
        基于信号强度计算仓位因子

        信号越强，仓位越大
        """
        if signal_score >= 0.8:
            return 1.0  # 满仓
        elif signal_score >= 0.7:
            return 0.8
        elif signal_score >= 0.6:
            return 0.6
        elif signal_score >= 0.5:
            return 0.4
        elif signal_score >= 0.4:
            return 0.3
        else:
            return 0.2  # 最小仓位

    def _calculate_volatility_factor(
        self,
        current_volatility: float,
        average_volatility: float
    ) -> float:
        """
        基于波动率计算仓位因子

        高波动时减少仓位，低波动时可以增加仓位
        """
        if average_volatility <= 0:
            return 1.0

        vol_ratio = current_volatility / average_volatility

        if vol_ratio <= 0.5:
            # 波动率很低，可以稍微增加仓位
            return 1.2
        elif vol_ratio <= 0.8:
            return 1.1
        elif vol_ratio <= 1.2:
            return 1.0
        elif vol_ratio <= 1.5:
            return 0.8
        elif vol_ratio <= 2.0:
            return 0.6
        else:
            # 波动率过高，大幅减仓
            return 0.4

    def _calculate_drawdown_factor(self) -> float:
        """
        基于回撤计算仓位因子

        回撤越大，仓位越小
        """
        if self._current_drawdown <= 0.02:
            return 1.0
        elif self._current_drawdown <= 0.05:
            return 0.8
        elif self._current_drawdown <= 0.10:
            return 0.6
        elif self._current_drawdown <= 0.15:
            return 0.4
        else:
            # 严重回撤，最小仓位
            return 0.2

    def _calculate_exposure_factor(
        self,
        account_balance: float,
        current_exposure: float,
        proposed_size: float
    ) -> float:
        """
        基于当前敞口计算仓位因子

        确保总敞口不超过限制
        """
        if account_balance <= 0:
            return 0.0

        current_exposure_pct = current_exposure / account_balance
        remaining_capacity = self.max_total_exposure - current_exposure_pct

        if remaining_capacity <= 0:
            return 0.0

        # 如果剩余容量不足以容纳完整仓位，按比例缩减
        proposed_pct = proposed_size / account_balance
        if proposed_pct > remaining_capacity:
            return remaining_capacity / proposed_pct

        return 1.0

    def _get_win_rate(self) -> float:
        """获取历史胜率"""
        if not self._trade_history:
            return 0.5  # 默认50%

        recent = self._trade_history[-self.win_rate_lookback:]
        wins = sum(1 for t in recent if t.is_win)
        return wins / len(recent) if recent else 0.5

    def _get_average_win(self) -> float:
        """获取平均盈利"""
        wins = [t.pnl_pct for t in self._trade_history if t.is_win]
        if not wins:
            return 0.05  # 默认5%
        return sum(wins) / len(wins)

    def _get_average_loss(self) -> float:
        """获取平均亏损"""
        losses = [abs(t.pnl_pct) for t in self._trade_history if not t.is_win]
        if not losses:
            return 0.02  # 默认2%
        return sum(losses) / len(losses)

    def record_trade(self, is_win: bool, pnl_pct: float, signal_score: float = 0.5):
        """
        记录交易结果

        Args:
            is_win: 是否盈利
            pnl_pct: 盈亏百分比（正数=盈利，负数=亏损）
            signal_score: 开仓时的信号评分
        """
        self._trade_history.append(TradeHistory(
            is_win=is_win,
            pnl_pct=pnl_pct,
            signal_score=signal_score
        ))

        # 限制历史记录长度
        max_history = self.win_rate_lookback * 2
        if len(self._trade_history) > max_history:
            self._trade_history = self._trade_history[-max_history:]

    def update_balance(self, current_balance: float):
        """
        更新账户余额，计算回撤

        Args:
            current_balance: 当前余额
        """
        if current_balance > self._peak_balance:
            self._peak_balance = current_balance
            self._current_drawdown = 0.0
        elif self._peak_balance > 0:
            self._current_drawdown = (self._peak_balance - current_balance) / self._peak_balance

    def get_consecutive_losses(self) -> int:
        """获取连续亏损次数"""
        count = 0
        for trade in reversed(self._trade_history):
            if trade.is_win:
                break
            count += 1
        return count

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'total_trades': len(self._trade_history),
            'win_rate': self._get_win_rate(),
            'avg_win': self._get_average_win(),
            'avg_loss': self._get_average_loss(),
            'profit_factor': self._get_average_win() / self._get_average_loss() if self._get_average_loss() > 0 else 0,
            'current_drawdown': self._current_drawdown,
            'peak_balance': self._peak_balance,
            'consecutive_losses': self.get_consecutive_losses()
        }


def calculate_optimal_leverage(
    signal_score: float,
    stop_loss_pct: float,
    max_leverage: int = 10,
    max_risk_per_trade: float = 0.02
) -> int:
    """
    计算最优杠杆倍数

    基于信号强度和止损比例计算合理的杠杆

    Args:
        signal_score: 信号评分 (0-1)
        stop_loss_pct: 止损百分比
        max_leverage: 最大杠杆限制
        max_risk_per_trade: 单笔最大风险

    Returns:
        建议杠杆倍数
    """
    if stop_loss_pct <= 0:
        return 1

    # 基于风险计算安全杠杆
    # 杠杆 = 最大风险 / 止损比例
    safe_leverage = max_risk_per_trade / stop_loss_pct

    # 基于信号强度调整
    if signal_score >= 0.8:
        signal_multiplier = 1.0
    elif signal_score >= 0.6:
        signal_multiplier = 0.8
    elif signal_score >= 0.4:
        signal_multiplier = 0.6
    else:
        signal_multiplier = 0.4

    optimal_leverage = safe_leverage * signal_multiplier

    # 限制在允许范围内
    optimal_leverage = max(1, min(max_leverage, int(optimal_leverage)))

    return optimal_leverage


def create_position_sizer_from_config(config: Dict[str, Any]) -> PositionSizer:
    """
    从配置创建仓位管理器

    Args:
        config: 配置字典

    Returns:
        PositionSizer 实例
    """
    trading_config = config.get('trading', {})
    enhanced_config = config.get('enhanced_analysis', {})
    risk_config = enhanced_config.get('risk', {})

    return PositionSizer(
        max_position_size=trading_config.get('max_trade_amount', 1000.0),
        max_account_risk=risk_config.get('max_risk_per_trade', 0.02),
        max_total_exposure=risk_config.get('max_total_exposure', 0.5),
        kelly_fraction=risk_config.get('kelly_fraction', 0.25),
        min_position_ratio=risk_config.get('min_position_ratio', 0.1),
        max_position_ratio=risk_config.get('max_position_ratio', 1.0)
    )
