"""
回测场景模块

提供多种市场条件下的回测验证，确保策略在不同环境下的鲁棒性。
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class MarketCondition(StrEnum):
    """市场条件类型"""

    # 趋势类型
    BULL_MARKET = "bull_market"  # 牛市上涨
    BEAR_MARKET = "bear_market"  # 熊市下跌
    SIDEWAYS = "sideways"  # 横盘震荡

    # 波动类型
    HIGH_VOLATILITY = "high_volatility"  # 高波动
    LOW_VOLATILITY = "low_volatility"  # 低波动

    # 特殊事件
    BLACK_SWAN = "black_swan"  # 黑天鹅事件
    FLASH_CRASH = "flash_crash"  # 闪崩
    RECOVERY = "recovery"  # 恢复期


@dataclass
class BacktestScenario:
    """回测场景定义"""

    name: str
    description: str
    condition: MarketCondition
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    symbol: str
    expected_behavior: str  # 预期策略行为描述
    risk_level: str  # low, medium, high

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "condition": self.condition.value,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "symbol": self.symbol,
            "expected_behavior": self.expected_behavior,
            "risk_level": self.risk_level,
        }


# 预定义的回测场景（基于真实历史事件）
REQUIRED_SCENARIOS: list[BacktestScenario] = [
    BacktestScenario(
        name="2024_btc_halving_rally",
        description="BTC 减半后的牛市上涨期",
        condition=MarketCondition.BULL_MARKET,
        start_date="2024-04-01",
        end_date="2024-06-30",
        symbol="BTC",
        expected_behavior="策略应能持续盈利，胜率 >= 50%",
        risk_level="medium",
    ),
    BacktestScenario(
        name="2022_luna_crash",
        description="Luna/UST 崩盘引发的市场暴跌",
        condition=MarketCondition.BLACK_SWAN,
        start_date="2022-05-01",
        end_date="2022-05-31",
        symbol="BTC",
        expected_behavior="策略应触发止损保护，最大回撤 <= 30%",
        risk_level="high",
    ),
    BacktestScenario(
        name="2023_q3_consolidation",
        description="2023 Q3 市场长期横盘整理",
        condition=MarketCondition.SIDEWAYS,
        start_date="2023-07-01",
        end_date="2023-09-30",
        symbol="BTC",
        expected_behavior="策略应减少交易频率，避免假突破",
        risk_level="medium",
    ),
    BacktestScenario(
        name="2024_high_volatility",
        description="高波动率测试周期",
        condition=MarketCondition.HIGH_VOLATILITY,
        start_date="2024-01-01",
        end_date="2024-01-31",
        symbol="BTC",
        expected_behavior="策略应正确调整止损位",
        risk_level="high",
    ),
    BacktestScenario(
        name="2023_recovery",
        description="2023 年熊市恢复期",
        condition=MarketCondition.RECOVERY,
        start_date="2023-01-01",
        end_date="2023-03-31",
        symbol="BTC",
        expected_behavior="策略应识别趋势反转信号",
        risk_level="medium",
    ),
]


@dataclass
class ScenarioResult:
    """单场景回测结果"""

    scenario: BacktestScenario
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    passed: bool  # 是否通过预期检验
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.to_dict(),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "passed": self.passed,
            "failure_reason": self.failure_reason,
        }


@dataclass
class RobustnessReport:
    """策略鲁棒性验证报告"""

    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    overall_passed: bool
    results: list[ScenarioResult]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "failed_scenarios": self.failed_scenarios,
            "overall_passed": self.overall_passed,
            "pass_rate": self.passed_scenarios / self.total_scenarios
            if self.total_scenarios > 0
            else 0,
            "results": [r.to_dict() for r in self.results],
            "timestamp": self.timestamp,
        }

    def save(self, output_path: str):
        """保存报告到文件"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


class StrategyValidator:
    """
    策略鲁棒性验证器

    遍历多种历史场景，确保策略在各种市场环境下：
    1. 不会大幅亏损
    2. 回撤在可控范围
    3. 胜率保持稳定
    """

    # 验证阈值
    MAX_ACCEPTABLE_DRAWDOWN = 0.30  # 最大可接受回撤 30%
    MIN_WIN_RATE = 0.40  # 最低胜率 40%
    MIN_SHARPE_RATIO = 0.5  # 最低夏普比率

    def __init__(
        self,
        scenarios: list[BacktestScenario] | None = None,
        max_drawdown_threshold: float = 0.30,
        min_win_rate: float = 0.40,
        min_sharpe: float = 0.5,
    ):
        """
        初始化验证器

        Args:
            scenarios: 要验证的场景列表，默认使用 REQUIRED_SCENARIOS
            max_drawdown_threshold: 最大回撤阈值
            min_win_rate: 最低胜率要求
            min_sharpe: 最低夏普比率要求
        """
        self.scenarios = scenarios or REQUIRED_SCENARIOS
        self.max_drawdown_threshold = max_drawdown_threshold
        self.min_win_rate = min_win_rate
        self.min_sharpe = min_sharpe

    def validate_scenario_result(
        self, scenario: BacktestScenario, backtest_result: dict[str, Any]
    ) -> ScenarioResult:
        """
        验证单个场景的回测结果

        Args:
            scenario: 回测场景
            backtest_result: 回测引擎返回的结果

        Returns:
            ScenarioResult 验证结果
        """
        # 提取关键指标
        total_trades = backtest_result.get("total_trades", 0)
        winning_trades = backtest_result.get("winning_trades", 0)
        losing_trades = backtest_result.get("losing_trades", 0)
        total_pnl = backtest_result.get("total_pnl", 0.0)
        max_drawdown = backtest_result.get("max_drawdown", 0.0)
        sharpe_ratio = backtest_result.get("sharpe_ratio", 0.0)

        # 计算胜率
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

        # 验证是否通过
        passed = True
        failure_reasons = []

        # 1. 回撤检查
        if max_drawdown > self.max_drawdown_threshold:
            passed = False
            failure_reasons.append(
                f"回撤过大: {max_drawdown:.1%} > {self.max_drawdown_threshold:.1%}"
            )

        # 2. 胜率检查（仅对足够多的交易有意义）
        if total_trades >= 10 and win_rate < self.min_win_rate:
            passed = False
            failure_reasons.append(f"胜率过低: {win_rate:.1%} < {self.min_win_rate:.1%}")

        # 3. 夏普比率检查
        if sharpe_ratio < self.min_sharpe and total_trades >= 10:
            passed = False
            failure_reasons.append(f"夏普比率过低: {sharpe_ratio:.2f} < {self.min_sharpe:.2f}")

        # 4. 黑天鹅场景特殊检查
        if (
            scenario.condition == MarketCondition.BLACK_SWAN
            and total_pnl < -0.20 * backtest_result.get("initial_balance", 10000)
        ):
            passed = False
            failure_reasons.append(f"黑天鹅场景亏损过大: {total_pnl:.2f}")

        return ScenarioResult(
            scenario=scenario,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            passed=passed,
            failure_reason="; ".join(failure_reasons) if failure_reasons else None,
        )

    def generate_robustness_report(self, results: list[ScenarioResult]) -> RobustnessReport:
        """
        生成鲁棒性验证报告

        Args:
            results: 所有场景的验证结果

        Returns:
            RobustnessReport 综合报告
        """
        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count

        # 至少 80% 场景通过才算整体通过
        overall_passed = (passed_count / len(results)) >= 0.8 if results else False

        return RobustnessReport(
            total_scenarios=len(results),
            passed_scenarios=passed_count,
            failed_scenarios=failed_count,
            overall_passed=overall_passed,
            results=results,
            timestamp=datetime.now().isoformat(),
        )

    def get_scenarios_by_condition(self, condition: MarketCondition) -> list[BacktestScenario]:
        """根据市场条件筛选场景"""
        return [s for s in self.scenarios if s.condition == condition]

    def get_high_risk_scenarios(self) -> list[BacktestScenario]:
        """获取高风险场景"""
        return [s for s in self.scenarios if s.risk_level == "high"]


def get_all_scenario_commands(
    output_dir: str = "backtest_results/robustness",
    initial_balance: float = 10000.0,
    interval: int = 15,
) -> list[str]:
    """
    生成所有场景的回测命令

    Args:
        output_dir: 输出目录
        initial_balance: 初始余额
        interval: 决策间隔

    Returns:
        回测命令列表
    """
    commands = []

    for scenario in REQUIRED_SCENARIOS:
        cmd = (
            f"python backtest.py "
            f"--symbol {scenario.symbol} "
            f"--start-date {scenario.start_date} "
            f"--end-date {scenario.end_date} "
            f"--initial-balance {initial_balance} "
            f"--interval {interval} "
            f"--output-dir {output_dir}/{scenario.name}"
        )
        commands.append(cmd)

    return commands


def print_scenario_summary():
    """打印所有场景的摘要"""
    print("\n📊 策略鲁棒性验证场景\n")
    print(f"{'场景名称':<30} {'市场条件':<20} {'时间范围':<25} {'风险等级':<10}")
    print("-" * 90)

    for scenario in REQUIRED_SCENARIOS:
        print(
            f"{scenario.name:<30} "
            f"{scenario.condition.value:<20} "
            f"{scenario.start_date} ~ {scenario.end_date:<10} "
            f"{scenario.risk_level:<10}"
        )

    print(f"\n总计 {len(REQUIRED_SCENARIOS)} 个验证场景")
