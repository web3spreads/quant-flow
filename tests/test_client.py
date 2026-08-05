"""HyperliquidClient 单元测试（离线）：价格/数量精度、部分平仓钳制、紧急平仓兜底。

构造方式：绕过 __init__（构造函数会请求网络拉 meta），用 __new__ + 手工装配
最小属性集，逐方法打桩。
"""

from typing import Any

from src.trading.client import HyperliquidClient

FILLED_ORDER = {
    "status": "ok",
    "response": {
        "type": "order",
        "data": {"statuses": [{"filled": {"avgPx": "100.0", "totalSz": "0.5", "oid": 1}}]},
    },
}
REJECTED_ORDER = {
    "status": "ok",
    "response": {"type": "order", "data": {"statuses": [{"error": "Order rejected"}]}},
}


def make_client(sz_decimals: int = 3) -> HyperliquidClient:
    client = HyperliquidClient.__new__(HyperliquidClient)
    client._asset_info_cache = {}
    client.get_asset_info = lambda symbol: {"szDecimals": sz_decimals}  # type: ignore[method-assign]
    return client


class TestFormatPrice:
    def test_high_price_five_sig_figs(self):
        client = make_client(sz_decimals=4)
        assert client.format_price("BTC", 94283.7) == 94284.0

    def test_eth_price_keeps_decimal(self):
        client = make_client(sz_decimals=4)
        assert client.format_price("ETH", 1872.34) == 1872.3

    def test_low_price_symbol_not_flattened_to_tick(self):
        # 历史缺陷：硬编码 0.1 tick 会把 0.12345 拍成 0.1
        client = make_client(sz_decimals=0)
        assert client.format_price("DOGE", 0.123456) == 0.12346

    def test_max_decimals_limited_by_sz_decimals(self):
        # 永续 max_decimals = 6 - szDecimals：szDecimals=2 → 最多 4 位小数
        client = make_client(sz_decimals=2)
        assert client.format_price("XYZ", 0.123456) == 0.1235


class TestRoundSize:
    def test_round_down_not_bankers(self):
        # 内建 round(0.0035, 3) 银行家舍入会进位到 0.004（放大敞口），必须向下
        client = make_client(sz_decimals=3)
        assert client.round_size("ETH", 0.0035) == 0.003
        assert client.round_size("ETH", 0.9999) == 0.999

    def test_exact_value_unchanged(self):
        client = make_client(sz_decimals=3)
        assert client.round_size("ETH", 0.5) == 0.5


class TestClosePositionPartial:
    def _client_with_position(self, szi: str) -> tuple[HyperliquidClient, list]:
        client = make_client(sz_decimals=3)
        client.get_positions = lambda: [{"coin": "ETH", "szi": szi}]  # type: ignore[method-assign]
        calls: list[tuple[Any, ...]] = []

        def fake_request(func_name, *args, is_exchange=False, **kwargs):
            calls.append((func_name, args))
            return FILLED_ORDER

        client._request_with_fallback = fake_request  # type: ignore[method-assign]
        return client, calls

    def test_partial_close_clamped_to_position(self):
        # 平仓量必须钳制到实际持仓：簿记漂移时超量平仓会反向开出新仓
        client, calls = self._client_with_position(szi="0.3")
        result = client.close_position("ETH", size=0.5)
        assert result["status"] == "ok"
        func_name, args = calls[0]
        assert func_name == "market_close"  # reduce-only 语义，绝不 market_open 反向
        assert args[1] == 0.3  # 钳制到持仓量

    def test_positions_query_failure_returns_error(self):
        client = make_client()
        client.get_positions = lambda: None  # type: ignore[method-assign]
        result = client.close_position("ETH", size=0.5)
        assert result["status"] == "error"
        assert "查询失败" in result["message"]

    def test_no_position_returns_error(self):
        client, _ = self._client_with_position(szi="0.3")
        client.get_positions = lambda: []  # type: ignore[method-assign]
        result = client.close_position("ETH", size=0.5)
        assert result["status"] == "error"


class TestEmergencyCloseWithRetry:
    def test_falls_back_to_full_close(self, monkeypatch):
        # 按量平仓连续失败后必须退化为市价全平兜底
        client = make_client()
        monkeypatch.setattr("time.sleep", lambda s: None)
        attempts: list[Any] = []

        def fake_close(symbol, size=None):
            attempts.append(size)
            if size is None:
                return FILLED_ORDER  # 全平兜底成功
            return REJECTED_ORDER

        client.close_position = fake_close  # type: ignore[method-assign]
        ok, result = client.emergency_close_with_retry("ETH", 0.5, reason="测试", max_retries=2)
        assert ok is True
        assert attempts == [0.5, 0.5, None]

    def test_all_failures_reported(self, monkeypatch):
        client = make_client()
        monkeypatch.setattr("time.sleep", lambda s: None)
        client.close_position = lambda symbol, size=None: REJECTED_ORDER  # type: ignore[method-assign]
        ok, result = client.emergency_close_with_retry("ETH", None, reason="测试", max_retries=2)
        assert ok is False

    def test_error_dict_never_treated_as_success(self, monkeypatch):
        # close_position 吞异常返回的 {"status":"error"} 是真值字典，
        # 校验层必须识别为失败（历史 `if result:` 恒真的根因）
        client = make_client()
        monkeypatch.setattr("time.sleep", lambda s: None)
        client.close_position = lambda symbol, size=None: {"status": "error", "message": "网络异常"}  # type: ignore[method-assign]
        ok, _ = client.emergency_close_with_retry("ETH", None, reason="测试", max_retries=1)
        assert ok is False
