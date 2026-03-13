"""
复盘经验 Verbal Fine-tuning 测试
测试 ReviewMemoryStore 的 get_verbal_finetuning_section 方法
"""

import json
import os
import tempfile

import pytest

from src.agent.review_memory import ReviewMemoryStore


class TestVerbalFinetuning:
    """Verbal Fine-tuning 注入段落测试"""

    @pytest.fixture
    def tmp_path(self):
        """创建临时文件路径"""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    @pytest.fixture
    def store_with_lessons(self, tmp_path):
        """创建包含经验的存储"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "RSI > 70 且资金费率极端正",
                        "action": "观望不做多",
                        "confidence": 0.85,
                        "support_count": 5,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                    },
                    {
                        "rule": "MACD金叉且4H趋势一致",
                        "action": "开多 5x 杠杆",
                        "confidence": 0.72,
                        "support_count": 3,
                        "last_seen": "2026-03-11T08:00:00",
                        "context_features": {},
                    },
                    {
                        "rule": "价格跌破EMA20且成交量放大",
                        "action": "减仓或观望",
                        "confidence": 0.45,
                        "support_count": 2,
                        "last_seen": "2026-03-10T12:00:00",
                        "context_features": {},
                    },
                    {
                        "rule": "恐惧贪婪 < 20 且 RSI < 30",
                        "action": "考虑逢低建仓",
                        "confidence": 0.38,
                        "support_count": 1,
                        "last_seen": "2026-03-09T15:00:00",
                        "context_features": {},
                    },
                ],
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return ReviewMemoryStore(tmp_path)

    @pytest.fixture
    def empty_store(self, tmp_path):
        """创建空存储"""
        return ReviewMemoryStore(tmp_path)

    # === 基础功能测试 ===

    def test_empty_store_returns_empty_string(self, empty_store):
        """测试空存储返回空字符串"""
        result = empty_store.get_verbal_finetuning_section("BTC")
        assert result == ""

    def test_nonexistent_symbol_returns_empty(self, store_with_lessons):
        """测试不存在的币种返回空字符串"""
        result = store_with_lessons.get_verbal_finetuning_section("DOGE")
        assert result == ""

    def test_returns_nonempty_for_existing_symbol(self, store_with_lessons):
        """测试已有币种返回非空结果"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")
        assert len(result) > 0

    # === 格式和内容测试 ===

    def test_contains_symbol_name(self, store_with_lessons):
        """测试结果包含币种名称"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")
        assert "BTC" in result

    def test_contains_section_header(self, store_with_lessons):
        """测试结果包含段落标题"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")
        assert "复盘经验" in result
        assert "优先参考" in result

    def test_contains_high_confidence_section(self, store_with_lessons):
        """测试结果包含高置信规则区域"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")
        assert "高置信规则" in result

    def test_contains_low_confidence_section(self, store_with_lessons):
        """测试结果包含待验证规则区域"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")
        assert "待验证规则" in result

    def test_high_confidence_rules_contain_verification_count(self, store_with_lessons):
        """测试高置信规则包含验证次数"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")
        assert "验证" in result
        assert "次" in result

    def test_rules_content_present(self, store_with_lessons):
        """测试规则内容被包含"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")
        assert "RSI > 70" in result
        assert "MACD金叉" in result

    # === 排序和优先级测试 ===

    def test_high_confidence_rules_sorted_first(self, store_with_lessons):
        """测试高置信规则排在前面"""
        result = store_with_lessons.get_verbal_finetuning_section("BTC")

        # 高置信规则应该出现在待验证规则之前
        high_conf_pos = result.find("高置信规则")
        low_conf_pos = result.find("待验证规则")
        assert high_conf_pos < low_conf_pos

    def test_scoring_considers_support_count(self, tmp_path):
        """测试综合评分考虑证据数量"""
        # 创建两条规则：一条高置信低证据，一条中置信高证据
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "规则A_高置信低证据",
                        "action": "动作A",
                        "confidence": 0.9,
                        "support_count": 1,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                    },
                    {
                        "rule": "规则B_中置信高证据",
                        "action": "动作B",
                        "confidence": 0.65,
                        "support_count": 10,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                    },
                ],
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        store = ReviewMemoryStore(tmp_path)

        result = store.get_verbal_finetuning_section("BTC")

        # 规则B (0.65 * log(11) ≈ 1.56) 应排在规则A (0.9 * log(2) ≈ 0.62) 之前
        pos_a = result.find("规则A")
        pos_b = result.find("规则B")
        assert pos_b < pos_a, "高证据数的规则应排在前面"

    # === 限制测试 ===

    def test_limit_parameter(self, tmp_path):
        """测试 limit 参数限制返回数量"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": f"规则{i}",
                        "action": f"动作{i}",
                        "confidence": 0.7,
                        "support_count": 1,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                    }
                    for i in range(10)
                ],
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        store = ReviewMemoryStore(tmp_path)

        result = store.get_verbal_finetuning_section("BTC", limit=3)

        # 应只包含 3 条规则
        rule_count = result.count("当 规则")
        assert rule_count == 3

    def test_default_limit_is_five(self, tmp_path):
        """测试默认限制为 5 条"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": f"规则{i}",
                        "action": f"动作{i}",
                        "confidence": 0.7,
                        "support_count": 1,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                    }
                    for i in range(10)
                ],
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        store = ReviewMemoryStore(tmp_path)

        result = store.get_verbal_finetuning_section("BTC")

        rule_count = result.count("当 规则")
        assert rule_count == 5

    # === 边界情况测试 ===

    def test_all_low_confidence(self, tmp_path):
        """测试全部为低置信规则"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "低置信规则",
                        "action": "某动作",
                        "confidence": 0.4,
                        "support_count": 1,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                    },
                ],
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        store = ReviewMemoryStore(tmp_path)

        result = store.get_verbal_finetuning_section("BTC")

        assert "待验证规则" in result
        # 不应有高置信区域
        assert "高置信规则" not in result

    def test_all_high_confidence(self, tmp_path):
        """测试全部为高置信规则"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "高置信规则",
                        "action": "某动作",
                        "confidence": 0.8,
                        "support_count": 3,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                    },
                ],
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        store = ReviewMemoryStore(tmp_path)

        result = store.get_verbal_finetuning_section("BTC")

        assert "高置信规则" in result
        # 不应有待验证区域
        assert "待验证规则" not in result

    def test_missing_fields_handled(self, tmp_path):
        """测试缺少字段时不报错"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "简单规则",
                        "action": "简单动作",
                        # 缺少 confidence, support_count 等
                    },
                ],
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        store = ReviewMemoryStore(tmp_path)

        # 不应抛异常
        result = store.get_verbal_finetuning_section("BTC")
        assert isinstance(result, str)
