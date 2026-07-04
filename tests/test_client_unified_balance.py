"""统一账户（unified account）余额兼容测试。

线上实测（测试网 2026-07-04）：新式统一账户 spot/perp 保证金合一，经典 perp
marginSummary 恒为 0，USDC 抵押在 spot 视图（balances[].total，挂单占用在 hold），
usdClassTransfer 被禁用。get_balance 只读 perp 视图会把有钱的账户误判为零余额，
自适应仓位随即以 INSUFFICIENT_CAPITAL 拒绝布单——$11 账户被判 $0 即此缺陷。
"""

import unittest

from src.trading.client import HyperliquidClient


def _make_client(responses):
    """绕过网络初始化构造 client，注入固定的 API 响应。

    responses: {func_name: 返回值或 Exception 实例}
    """
    client = HyperliquidClient.__new__(HyperliquidClient)
    client.address = "0xTEST"
    calls = []

    def fake_request(func_name, *args, **kwargs):
        calls.append(func_name)
        result = responses[func_name]
        if isinstance(result, Exception):
            raise result
        return result

    client._request_with_fallback = fake_request
    client._calls = calls
    return client


def _perp_state(account_value, margin_used=0.0):
    return {
        "marginSummary": {
            "accountValue": str(account_value),
            "totalMarginUsed": str(margin_used),
            "totalRawUsd": str(account_value),
        },
        "withdrawable": str(account_value - margin_used),
    }


class TestUnifiedBalanceFallback(unittest.TestCase):
    def test_classic_account_no_spot_query(self):
        # 经典账户 perp 有值：不触发 spot 回退（不多打一次 API）
        client = _make_client({"user_state": _perp_state(100.0, 20.0)})
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 100.0)
        self.assertEqual(balance["available"], 80.0)
        self.assertNotIn("spot_user_state", client._calls)

    def test_unified_account_falls_back_to_spot_usdc(self):
        # 统一账户：perp 全零 → 回退 spot USDC（total=抵押，hold=挂单占用）
        client = _make_client({
            "user_state": _perp_state(0.0),
            "spot_user_state": {
                "balances": [
                    {"coin": "USDC", "total": "11.0", "hold": "2.0"},
                    {"coin": "TZERO", "total": "0.0", "hold": "0.0"},
                ]
            },
        })
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 11.0)
        self.assertEqual(balance["totalMarginUsed"], 2.0)
        self.assertEqual(balance["available"], 9.0)

    def test_unified_fallback_no_usdc(self):
        # spot 里没有 USDC：维持零余额，不异常
        client = _make_client({
            "user_state": _perp_state(0.0),
            "spot_user_state": {"balances": [{"coin": "TZERO", "total": "5", "hold": "0"}]},
        })
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 0.0)

    def test_spot_query_failure_keeps_perp_view(self):
        # spot 查询失败：不因兼容路径引入新故障，维持 perp 视图结果
        client = _make_client({
            "user_state": _perp_state(0.0),
            "spot_user_state": RuntimeError("模拟 spot 查询失败"),
        })
        balance = client.get_balance()
        self.assertIsNotNone(balance)
        self.assertEqual(balance["accountValue"], 0.0)


if __name__ == "__main__":
    unittest.main()
