"""网格数学引擎测试：区间计算、自适应仓位与资金不足拒绝。"""

from src.utils.grid_math import GridLevel, GridLevelState, calculate_grid_config, extract_order_id


class TestCalculateGridConfig:
    def test_neutral_range_symmetric(self):
        cfg = calculate_grid_config(
            current_price=100.0, available_balance=1000.0, mode="NEUTRAL", width_pct=0.10
        )
        assert cfg["action"] == "UPDATE_GRID"
        assert cfg["lower_price"] == 95.0
        assert cfg["upper_price"] == 105.0
        assert cfg["mode"] == "NEUTRAL"

    def test_long_range_below_price(self):
        cfg = calculate_grid_config(
            current_price=100.0, available_balance=1000.0, mode="LONG", width_pct=0.10
        )
        assert cfg["lower_price"] == 90.0
        assert cfg["upper_price"] == 101.0

    def test_adaptive_reduces_grid_num_for_small_account(self):
        # 总额度 = 50 × 5 × 0.4 = 100，单格最小 $11 → 只够 9 格，6 格请求原样保留；
        # 但若请求 20 格则单格 $5 不足，应降格数而不是抬单格金额
        cfg = calculate_grid_config(
            current_price=100.0,
            available_balance=50.0,
            grid_num=20,
            leverage=5,
            adaptive_sizing=True,
        )
        assert cfg["action"] == "UPDATE_GRID"
        assert cfg["grid_num"] < 20
        assert cfg["amount_per_grid"] >= 11.0

    def test_adaptive_insufficient_capital_rejected(self):
        # $7.71 小账户：总额度 7.71 × 5 × 0.4 ≈ $15.4，连 3 格最小网格都撑不起
        cfg = calculate_grid_config(
            current_price=100.0,
            available_balance=7.71,
            grid_num=6,
            leverage=5,
            adaptive_sizing=True,
            min_grid_num=3,
        )
        assert cfg["action"] == "INSUFFICIENT_CAPITAL"
        assert cfg["required_balance"] > 7.71
        assert "资金不足" in cfg["reason"]

    def test_legacy_sizing_clamps(self):
        # 历史行为：单格金额被钳制在 [15.5, 25.5]
        big = calculate_grid_config(current_price=100.0, available_balance=10000.0, grid_num=6)
        assert big["amount_per_grid"] == 25.5
        small = calculate_grid_config(current_price=100.0, available_balance=10.0, grid_num=6)
        assert small["amount_per_grid"] == 15.5

    def test_tp_sl_ratio_bounds(self):
        cfg = calculate_grid_config(current_price=100.0, available_balance=1000.0, width_pct=0.10)
        assert cfg["tp_ratio"] > 0
        assert 0.005 <= cfg["sl_ratio"] <= 0.02

    def test_invalid_params_defended(self):
        cfg = calculate_grid_config(
            current_price=100.0, available_balance=1000.0, grid_num=0, leverage=0
        )
        assert cfg["grid_num"] >= 1  # 非法输入被钳制而非崩溃


class TestExtractOrderId:
    def test_resting(self):
        res = {"response": {"data": {"statuses": [{"resting": {"oid": 123}}]}}}
        assert extract_order_id(res) == 123

    def test_filled(self):
        res = {"response": {"data": {"statuses": [{"filled": {"oid": 456}}]}}}
        assert extract_order_id(res) == 456

    def test_malformed(self):
        assert extract_order_id({}) is None
        assert extract_order_id({"response": None}) is None


class TestGridLevel:
    def test_round_trip_serialization(self):
        from decimal import Decimal

        level = GridLevel(id="L0", price=Decimal("100.5"), amount=Decimal("20"), side="LONG")
        level.state = GridLevelState.OPEN_FILLED
        level.open_fill_price = Decimal("100.4")
        restored = GridLevel.from_dict(level.to_dict())
        assert restored.id == "L0"
        assert restored.price == Decimal("100.5")
        assert restored.state == GridLevelState.OPEN_FILLED
        assert restored.open_fill_price == Decimal("100.4")

    def test_reset_keeps_stats(self):
        from decimal import Decimal

        level = GridLevel(id="L1", price=Decimal("100"), amount=Decimal("20"), side="LONG")
        level.round_trip_count = 3
        level.cumulative_pnl = Decimal("1.5")
        level.open_order_id = 42
        level.reset()
        assert level.state == GridLevelState.IDLE
        assert level.open_order_id is None
        assert level.round_trip_count == 3  # 统计保留
        assert level.cumulative_pnl == Decimal("1.5")
