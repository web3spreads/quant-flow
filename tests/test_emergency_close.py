"""紧急平仓兜底机制测试（_emergency_close_with_retry）。

覆盖止损单失败/下单异常后避免裸仓的核心资金保护路径：
- 首次部分平仓成功
- 部分平仓多次失败后，市价全平兜底成功
- 全部失败时返回 False 并发出 critical 告警
"""

from unittest.mock import MagicMock, patch

from src.trading.client import HyperliquidClient


def _make_client() -> HyperliquidClient:
    """绕过 __init__（无需网络/密钥）构造一个仅用于测试方法逻辑的实例。"""
    return HyperliquidClient.__new__(HyperliquidClient)


def test_emergency_close_first_attempt_success():
    """首次按 size 部分平仓即成功：只调用一次，不触发全平兜底。"""
    client = _make_client()
    client.close_position = MagicMock(return_value={"r": "ok"})
    client.check_order_success = MagicMock(return_value=(True, None))

    with patch("src.trading.client.time.sleep"):
        ok, result = client._emergency_close_with_retry("BTC", 0.1, reason="测试")

    assert ok is True
    client.close_position.assert_called_once_with("BTC", 0.1)


def test_emergency_close_falls_back_to_full_close():
    """按 size 平仓全部失败（如 get_positions 查不到），市价全平兜底成功。"""
    client = _make_client()
    # 前 3 次（size 部分平仓）失败，第 4 次（全平 size=None）成功
    client.close_position = MagicMock(side_effect=[{"e": 1}, {"e": 2}, {"e": 3}, {"ok": 1}])
    client.check_order_success = MagicMock(
        side_effect=[(False, "无持仓"), (False, "无持仓"), (False, "无持仓"), (True, None)]
    )

    with patch("src.trading.client.time.sleep"):
        ok, _ = client._emergency_close_with_retry("BTC", 0.1, reason="止损单失败")

    assert ok is True
    # 3 次部分平仓 + 1 次全平
    assert client.close_position.call_count == 4
    assert client.close_position.call_args_list[-1].args == ("BTC", None)


def test_emergency_close_all_fail_sends_critical_alert():
    """重试 + 全平兜底全部失败：返回 False 且发出 critical 风控告警。"""
    client = _make_client()
    client.close_position = MagicMock(return_value={"status": "error"})
    client.check_order_success = MagicMock(return_value=(False, "交易所拒绝"))

    cloud = MagicMock()
    with (
        patch("src.trading.client.time.sleep"),
        patch("src.trading.client.get_cloud_logger", return_value=cloud),
    ):
        ok, _ = client._emergency_close_with_retry("BTC", 0.1, reason="下单异常")

    assert ok is False
    cloud.send_risk_event.assert_called_once()
    kwargs = cloud.send_risk_event.call_args.kwargs
    assert kwargs["risk_type"] == "emergency_close_failed_critical"
    assert kwargs["level"] == "error"
