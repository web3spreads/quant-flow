"""签名探测工具测试

``accepts_parameter`` 用于向后兼容地扩展回调/插件签名。它替代的是
``try/except TypeError`` 降级重调——后者在被调方内部自己抛 TypeError 时会误判成
「签名不兼容」而重调一次，对上报盈亏这类带副作用的调用等于事件被计两次。
"""

import unittest

from src.utils.introspect import accepts_parameter


class TestAcceptsParameter(unittest.TestCase):
    def test_named_parameter_is_detected(self):
        self.assertTrue(accepts_parameter(lambda s, p, forced=False: None, "forced"))

    def test_missing_parameter_is_detected(self):
        self.assertFalse(accepts_parameter(lambda s, p: None, "forced"))

    def test_var_keyword_counts_as_accepting(self):
        self.assertTrue(accepts_parameter(lambda s, **kw: None, "forced"))

    def test_var_positional_counts_as_accepting(self):
        self.assertTrue(accepts_parameter(lambda *a: None, "forced"))

    def test_keyword_only_parameter_is_detected(self):
        self.assertTrue(accepts_parameter(lambda s, *, forced=False: None, "forced"))

    def test_bound_method_excludes_self(self):
        class _Plugin:
            def on_trade_close(self, symbol, pnl, forced=False):
                pass

        class _Old:
            def on_trade_close(self, symbol, pnl):
                pass

        self.assertTrue(accepts_parameter(_Plugin().on_trade_close, "forced"))
        self.assertFalse(accepts_parameter(_Old().on_trade_close, "forced"))

    def test_unintrospectable_defaults_to_accepting(self):
        """取不到签名时按新签名调用：现役实现都已升级，误判成旧签名会静默丢参数"""
        self.assertTrue(accepts_parameter(print, "forced"))
        self.assertTrue(accepts_parameter(None, "forced"))


if __name__ == "__main__":
    unittest.main()
