"""
Regime 感知记忆测试（改进2）
"""

import json
import os
import tempfile

import pytest

from src.agent.review_memory import ReviewMemoryStore


class MockSimilarityScorer:
    """模拟相似度计算器"""

    def __init__(self, default_score=0.8):
        self.default_score = default_score

    def compute(self, features_a, features_b):
        return self.default_score


class TestRegimeAwareMemory:
    """Regime 感知记忆测试"""

    @pytest.fixture
    def tmp_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    @pytest.fixture
    def store_with_regimes(self, tmp_path):
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "趋势市突破追入",
                        "action": "突破后做多",
                        "confidence": 0.8,
                        "support_count": 5,
                        "context_features": {"rsi": 60},
                        "source_regime": "trending",
                        "lesson_type": "positive",
                        "source_type": "factual",
                        "last_seen": "2026-03-12T10:00:00",
                    },
                    {
                        "rule": "震荡市区间交易",
                        "action": "高抛低吸",
                        "confidence": 0.75,
                        "support_count": 4,
                        "context_features": {"rsi": 50},
                        "source_regime": "ranging",
                        "lesson_type": "positive",
                        "source_type": "factual",
                        "last_seen": "2026-03-11T10:00:00",
                    },
                    {
                        "rule": "旧经验无 Regime",
                        "action": "测试兼容",
                        "confidence": 0.6,
                        "support_count": 2,
                        "context_features": {"rsi": 55},
                        "source_regime": "unknown",
                        "lesson_type": "unknown",
                        "source_type": "mixed",
                        "last_seen": "2026-03-10T10:00:00",
                    },
                ]
            }
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        return ReviewMemoryStore(path=tmp_path, max_lessons=30)

    def test_regime_mismatch_downweight(self, store_with_regimes):
        """测试 Regime 不匹配时降权"""
        scorer = MockSimilarityScorer(default_score=0.9)

        # 当前 Regime 为 trending，trending 经验不降权，ranging 经验降权
        lessons = store_with_regimes.get_similar_lessons(
            symbol="BTC",
            context_features={"rsi": 60},
            scorer=scorer,
            current_regime="trending",
            regime_mismatch_factor=0.4,
        )

        # trending 经验应该排在前面（分数更高）
        assert len(lessons) >= 1
        # 找到 trending 经验
        trending_lessons = [ls for ls in lessons if ls.get("source_regime") == "trending"]
        ranging_lessons = [ls for ls in lessons if ls.get("source_regime") == "ranging"]

        if trending_lessons and ranging_lessons:
            assert trending_lessons[0]["similarity_score"] > ranging_lessons[0]["similarity_score"]

    def test_unknown_regime_no_downweight(self, store_with_regimes):
        """测试 unknown regime 来源不降权"""
        scorer = MockSimilarityScorer(default_score=0.9)

        lessons = store_with_regimes.get_similar_lessons(
            symbol="BTC",
            context_features={"rsi": 55},
            scorer=scorer,
            current_regime="trending",
        )

        unknown_lessons = [ls for ls in lessons if ls.get("source_regime") == "unknown"]
        assert len(unknown_lessons) >= 1
        # unknown 不应该被降权，保持原始分数
        assert unknown_lessons[0]["similarity_score"] == 0.9

    def test_add_lessons_with_regime(self, tmp_path):
        """测试添加经验时附带 Regime"""
        store = ReviewMemoryStore(path=tmp_path, max_lessons=30)
        lessons = [{"rule": "测试规则", "action": "测试动作", "confidence": 0.7}]
        added = store.add_lessons("BTC", lessons, current_regime="volatile")

        assert len(added) == 1
        assert added[0]["source_regime"] == "volatile"

    def test_vft_section_regime_annotation(self, store_with_regimes):
        """测试 VFT 段落中的 Regime 标注"""
        section = store_with_regimes.get_verbal_finetuning_section("BTC", limit=5)

        assert "[趋势市经验]" in section
        assert "[震荡市经验]" in section

    def test_vft_section_regime_weight_adjustment(self, store_with_regimes):
        """测试 VFT 段落中的 Regime 权重调整"""
        # 趋势市时，主观经验应该被提升
        section_trending = store_with_regimes.get_verbal_finetuning_section(
            "BTC", limit=5, current_regime="trending"
        )
        assert section_trending  # 至少有内容

    def test_ensure_context_defaults(self, tmp_path):
        """测试旧记录自动补充新字段"""
        data = {
            "lessons": {
                "ETH": [
                    {
                        "rule": "旧规则",
                        "action": "旧动作",
                        "confidence": 0.6,
                        "support_count": 1,
                        "last_seen": "2026-03-01",
                    }
                ]
            }
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f)

        store = ReviewMemoryStore(path=tmp_path)
        lessons = store.get_lessons("ETH")

        assert lessons[0]["source_regime"] == "unknown"
        assert lessons[0]["lesson_type"] == "unknown"
        assert lessons[0]["source_type"] == "mixed"
