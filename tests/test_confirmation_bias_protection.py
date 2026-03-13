"""
确认偏差防护测试（改进3）
"""

import json
import os
import tempfile

import pytest

from src.agent.review_memory import ReviewMemoryStore


class TestConfirmationBiasProtection:
    """确认偏差防护测试"""

    @pytest.fixture
    def tmp_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    def test_lesson_type_stats(self, tmp_path):
        """测试经验类型统计"""
        data = {
            "lessons": {
                "BTC": [
                    {"rule": "r1", "action": "做多", "lesson_type": "positive", "confidence": 0.7, "last_seen": "2026-03-12"},
                    {"rule": "r2", "action": "做多", "lesson_type": "positive", "confidence": 0.8, "last_seen": "2026-03-12"},
                    {"rule": "r3", "action": "避免追高", "lesson_type": "negative", "confidence": 0.75, "last_seen": "2026-03-12"},
                ]
            }
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f)

        store = ReviewMemoryStore(path=tmp_path)
        stats = store.get_lesson_type_stats("BTC")

        assert stats["total"] == 3
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert abs(stats["positive_ratio"] - 2 / 3) < 0.01
        assert abs(stats["negative_ratio"] - 1 / 3) < 0.01

    def test_lesson_type_stats_empty(self, tmp_path):
        """测试空经验的类型统计"""
        with open(tmp_path, "w") as f:
            json.dump({"lessons": {}}, f)

        store = ReviewMemoryStore(path=tmp_path)
        stats = store.get_lesson_type_stats("BTC")

        assert stats["total"] == 0
        assert stats["positive_ratio"] == 0.0

    def test_bias_protection_eviction(self, tmp_path):
        """测试偏差防护淘汰策略"""
        # 创建超过上限的经验，大部分是 positive
        lessons_data = []
        for i in range(10):
            lessons_data.append({
                "rule": f"positive 规则 {i}",
                "action": f"做多 {i}",
                "confidence": 0.5 + i * 0.01,
                "support_count": 1,
                "last_seen": f"2026-03-{10 + i:02d}",
                "lesson_type": "positive",
                "source_regime": "unknown",
                "source_type": "mixed",
                "context_features": {},
            })

        # 添加 2 条 negative
        for i in range(2):
            lessons_data.append({
                "rule": f"negative 规则 {i}",
                "action": f"避免 {i}",
                "confidence": 0.5,
                "support_count": 1,
                "last_seen": f"2026-03-{20 + i:02d}",
                "lesson_type": "negative",
                "source_regime": "unknown",
                "source_type": "mixed",
                "context_features": {},
            })

        data = {"lessons": {"BTC": lessons_data}}
        with open(tmp_path, "w") as f:
            json.dump(data, f)

        store = ReviewMemoryStore(path=tmp_path, max_lessons=8)

        # 触发淘汰
        new_lessons = [{"rule": "新规则", "action": "新动作", "confidence": 0.7}]
        store.add_lessons("BTC", new_lessons, max_positive_ratio=0.7)

        remaining = store.get_lessons("BTC")
        assert len(remaining) <= 8

        # 确保 negative 经验被保护
        negative_remaining = [ls for ls in remaining if ls.get("lesson_type") == "negative"]
        assert len(negative_remaining) >= 1  # negative 应该被保护

    def test_negative_confidence_boost_in_review(self):
        """测试 negative 经验在 _enrich_lessons 中的置信度加成"""
        from src.agent.review_agent import ReviewAgent

        # 测试 _infer_lesson_type
        assert ReviewAgent._infer_lesson_type("避免追高做多") == "negative"
        assert ReviewAgent._infer_lesson_type("不要在高位加仓") == "negative"
        assert ReviewAgent._infer_lesson_type("谨慎操作") == "negative"
        assert ReviewAgent._infer_lesson_type("做多突破位") == "positive"
        assert ReviewAgent._infer_lesson_type("追随趋势") == "positive"

    def test_vft_negative_prefix(self, tmp_path):
        """测试 VFT 段落中 negative 经验的 [避免] 前缀"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "RSI > 80 时追多",
                        "action": "避免追高做多",
                        "confidence": 0.8,
                        "support_count": 5,
                        "lesson_type": "negative",
                        "source_regime": "unknown",
                        "source_type": "factual",
                        "last_seen": "2026-03-12",
                        "context_features": {},
                    },
                ]
            }
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f)

        store = ReviewMemoryStore(path=tmp_path)
        section = store.get_verbal_finetuning_section("BTC")

        assert "[避免]" in section
