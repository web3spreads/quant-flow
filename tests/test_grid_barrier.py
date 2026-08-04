"""Triple Barrier 网格级风控测试。"""

from decimal import Decimal

from src.trading.grid_barrier import GridBarrierMonitor, TripleBarrierConfig


def make_monitor(**overrides) -> GridBarrierMonitor:
    config = TripleBarrierConfig(
        stop_loss_pct=Decimal("0.05"),
        take_profit_pct=Decimal("0.10"),
        time_limit_seconds=3600,
        trailing_stop_activation_pct=Decimal("0.03"),
        trailing_stop_delta_pct=Decimal("0.01"),
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return GridBarrierMonitor(config, start_time=1000.0)


def check(monitor, pnl_pct: str, now: float = 1100.0, price: str = "100"):
    return monitor.check(
        current_price=Decimal(price), net_pnl_pct=Decimal(pnl_pct), current_time=now
    )


class TestBarriers:
    def test_safe_state(self):
        assert check(make_monitor(), "0.01") is None

    def test_stop_loss(self):
        assert "STOP_LOSS" in check(make_monitor(), "-0.06")

    def test_take_profit(self):
        assert "TAKE_PROFIT" in check(make_monitor(), "0.12")

    def test_time_limit(self):
        assert "TIME_LIMIT" in check(make_monitor(), "0.0", now=1000.0 + 3601)

    def test_price_limit(self):
        monitor = make_monitor(price_lower_limit=Decimal("90"))
        assert "PRICE_LIMIT" in check(monitor, "0.0", price="89")

    def test_trailing_stop_hysteresis(self):
        monitor = make_monitor()
        assert check(monitor, "0.04") is None  # 激活追踪（高水位 4%）
        assert check(monitor, "0.05") is None  # 高水位上移至 5%
        assert "TRAILING_STOP" in check(monitor, "0.035")  # 回撤 1.5% ≥ 1%

    def test_trailing_not_activated_below_threshold(self):
        monitor = make_monitor()
        assert check(monitor, "0.02") is None  # 未达激活阈值
        assert check(monitor, "0.005") is None  # 无高水位则无追踪止损

    def test_reset(self):
        monitor = make_monitor()
        check(monitor, "0.04")
        monitor.reset(start_time=5000.0)
        assert monitor._trailing_stop_high_water is None
        assert check(monitor, "0.0", now=5100.0) is None


class TestConfigFromDict:
    def test_defaults_when_empty(self):
        config = TripleBarrierConfig.from_config({})
        assert config.stop_loss_pct == Decimal("0.05")

    def test_overrides(self):
        config = TripleBarrierConfig.from_config(
            {"stop_loss_pct": 0.08, "time_limit_seconds": 7200, "take_profit_pct": None}
        )
        assert config.stop_loss_pct == Decimal("0.08")
        assert config.time_limit_seconds == 7200
        assert config.take_profit_pct is None
