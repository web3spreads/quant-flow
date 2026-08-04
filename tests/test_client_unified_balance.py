"""统一账户（unified account）余额兼容测试。

线上实测（测试网 2026-07-04）：新式统一账户 spot/perp 保证金合一，USDC 抵押在
spot 视图（balances[].total，挂单占用在 hold），usdClassTransfer 被禁用。
perp marginSummary 有两种误导形态：
1. 无持仓：accountValue 恒为 0 → $11 账户被判 $0，资金不足拒绝布单；
2. 有持仓：accountValue 仅等于被占用的抵押（实测 $11 账户开仓后只剩 $2.28
   == spot hold）→ 净值被低估约 80%，污染停机线/自适应仓位/净值快照。
判定依据官方 userAbstraction 接口（"unifiedAccount"/"portfolioMargin"/"default"），
结果缓存；判定失败退回启发式（perp 视图无资产才尝试 spot）。
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
        client = _make_client(
            {
                "user_state": _perp_state(100.0, 20.0),
                "query_user_abstraction_state": "default",
            }
        )
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 100.0)
        self.assertEqual(balance["available"], 80.0)
        self.assertNotIn("spot_user_state", client._calls)

    def test_unified_account_falls_back_to_spot_usdc(self):
        # 统一账户无持仓：perp 全零 → 读 spot USDC（total=抵押，hold=占用）
        client = _make_client(
            {
                "user_state": _perp_state(0.0),
                "query_user_abstraction_state": "unifiedAccount",
                "spot_user_state": {
                    "balances": [
                        {"coin": "USDC", "total": "11.0", "hold": "2.0"},
                        {"coin": "TZERO", "total": "0.0", "hold": "0.0"},
                    ]
                },
            }
        )
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 11.0)
        self.assertEqual(balance["totalMarginUsed"], 2.0)
        self.assertEqual(balance["available"], 9.0)

    def test_unified_account_with_position_uses_spot_total(self):
        # 关键回归（实测形态 2）：统一账户有持仓时 perp accountValue 只等于
        # 被占用的抵押（$2.28 > 0，旧启发式不会回退）→ 必须仍以 spot 为准
        client = _make_client(
            {
                "user_state": _perp_state(2.282516, 1.16565),
                "query_user_abstraction_state": "unifiedAccount",
                "spot_user_state": {
                    "balances": [{"coin": "USDC", "total": "11.060748", "hold": "2.282516"}]
                },
            }
        )
        balance = client.get_balance()
        self.assertAlmostEqual(balance["accountValue"], 11.060748)
        self.assertAlmostEqual(balance["totalMarginUsed"], 2.282516)
        self.assertAlmostEqual(balance["available"], 11.060748 - 2.282516)

    def test_abstraction_mode_cached(self):
        # 账户模式查询结果缓存：连续两次 get_balance 只查一次 userAbstraction
        client = _make_client(
            {
                "user_state": _perp_state(100.0),
                "query_user_abstraction_state": "default",
            }
        )
        client.get_balance()
        client.get_balance()
        self.assertEqual(client._calls.count("query_user_abstraction_state"), 1)

    def test_abstraction_query_failure_falls_back_to_heuristic(self):
        # 判定失败 + perp 有值：维持经典行为，不查 spot（启发式不触发）
        client = _make_client(
            {
                "user_state": _perp_state(100.0, 20.0),
                "query_user_abstraction_state": RuntimeError("模拟判定接口失败"),
            }
        )
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 100.0)
        self.assertNotIn("spot_user_state", client._calls)

    def test_abstraction_query_failure_zero_balance_still_falls_back(self):
        # 判定失败 + perp 全零：启发式兜底，仍回退 spot（形态 1 不回退会误判 $0）
        client = _make_client(
            {
                "user_state": _perp_state(0.0),
                "query_user_abstraction_state": RuntimeError("模拟判定接口失败"),
                "spot_user_state": {"balances": [{"coin": "USDC", "total": "11.0", "hold": "0.0"}]},
            }
        )
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 11.0)

    def test_unified_fallback_no_usdc(self):
        # spot 里没有 USDC：维持零余额，不异常
        client = _make_client(
            {
                "user_state": _perp_state(0.0),
                "query_user_abstraction_state": "unifiedAccount",
                "spot_user_state": {"balances": [{"coin": "TZERO", "total": "5", "hold": "0"}]},
            }
        )
        balance = client.get_balance()
        self.assertEqual(balance["accountValue"], 0.0)

    def test_spot_query_failure_keeps_perp_view(self):
        # spot 查询失败：不因兼容路径引入新故障，维持 perp 视图结果
        client = _make_client(
            {
                "user_state": _perp_state(0.0),
                "query_user_abstraction_state": "unifiedAccount",
                "spot_user_state": RuntimeError("模拟 spot 查询失败"),
            }
        )
        balance = client.get_balance()
        self.assertIsNotNone(balance)
        self.assertEqual(balance["accountValue"], 0.0)


if __name__ == "__main__":
    unittest.main()
