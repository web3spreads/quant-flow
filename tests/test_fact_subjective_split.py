"""
事实-主观分离反思测试（改进4）
"""

import json
import os
import tempfile

import pytest

from src.agent.review_agent import ReviewAgent
from src.agent.review_memory import ReviewMemoryStore


class TestFactSubjectiveSplit:
    """事实-主观分离测试"""

    @pytest.fixture
    def tmp_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    def test_infer_factual(self):
        """测试推断事实型经验"""
        assert ReviewAgent._infer_source_type("RSI > 70 时", "做多") == "factual"
        assert ReviewAgent._infer_source_type("MACD 金叉", "开多") == "factual"
        assert ReviewAgent._infer_source_type("EMA20 上穿 EMA50", "追随趋势") == "factual"
        assert ReviewAgent._infer_source_type("ATR 放大", "减仓") == "factual"
        assert ReviewAgent._infer_source_type("成交量突增", "确认突破") == "factual"

    def test_infer_subjective(self):
        """测试推断主观型经验"""
        assert ReviewAgent._infer_source_type("市场情绪极度贪婪", "减仓") == "subjective"
        assert ReviewAgent._infer_source_type("新闻利好", "观望") == "subjective"
        assert ReviewAgent._infer_source_type("恐惧指数极低", "逆向做多") == "subjective"
        assert ReviewAgent._infer_source_type("资金费率极端", "谨慎") == "subjective"

    def test_infer_mixed(self):
        """测试推断混合型经验"""
        assert ReviewAgent._infer_source_type(
            "RSI > 70 且市场情绪贪婪", "减仓"
        ) == "mixed"
        assert ReviewAgent._infer_source_type(
            "MACD 金叉但新闻利空", "观望"
        ) == "mixed"

    def test_infer_default_mixed(self):
        """测试无关键词时默认为 mixed"""
        assert ReviewAgent._infer_source_type("价格上涨", "做多") == "mixed"

    def test_vft_source_type_annotation(self, tmp_path):
        """测试 VFT 段落中的事实/主观标注"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "RSI > 70",
                        "action": "减仓",
                        "confidence": 0.8,
                        "support_count": 5,
                        "source_type": "factual",
                        "source_regime": "unknown",
                        "lesson_type": "positive",
                        "last_seen": "2026-03-12",
                        "context_features": {},
                    },
                    {
                        "rule": "市场情绪贪婪",
                        "action": "观望",
                        "confidence": 0.7,
                        "support_count": 3,
                        "source_type": "subjective",
                        "source_regime": "unknown",
                        "lesson_type": "positive",
                        "last_seen": "2026-03-11",
                        "context_features": {},
                    },
                ]
            }
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f)

        store = ReviewMemoryStore(path=tmp_path)
        section = store.get_verbal_finetuning_section("BTC")

        assert "[事实型]" in section
        assert "[主观型]" in section

    def test_vft_regime_weight_factual_boost(self, tmp_path):
        """测试震荡市中事实型经验权重提升"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "RSI 超买",
                        "action": "卖出",
                        "confidence": 0.6,
                        "support_count": 2,
                        "source_type": "factual",
                        "source_regime": "unknown",
                        "lesson_type": "positive",
                        "last_seen": "2026-03-12",
                        "context_features": {},
                    },
                    {
                        "rule": "市场恐惧",
                        "action": "买入",
                        "confidence": 0.65,
                        "support_count": 2,
                        "source_type": "subjective",
                        "source_regime": "unknown",
                        "lesson_type": "positive",
                        "last_seen": "2026-03-12",
                        "context_features": {},
                    },
                ]
            }
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f)

        store = ReviewMemoryStore(path=tmp_path)

        # 在 ranging 市场中，factual 应该排在前面（因为 ranging_factual_boost）
        section_ranging = store.get_verbal_finetuning_section(
            "BTC", limit=5, current_regime="ranging", ranging_factual_boost=2.0
        )
        # factual 规则应该在 subjective 之前
        factual_pos = section_ranging.find("RSI 超买")
        subjective_pos = section_ranging.find("市场恐惧")
        if factual_pos >= 0 and subjective_pos >= 0:
            assert factual_pos < subjective_pos

    def test_add_lessons_preserves_source_type(self, tmp_path):
        """测试添加经验时保留 source_type"""
        store = ReviewMemoryStore(path=tmp_path, max_lessons=30)
        lessons = [
            {
                "rule": "RSI 超买",
                "action": "卖出",
                "confidence": 0.7,
                "source_type": "factual",
            }
        ]
        added = store.add_lessons("BTC", lessons)

        assert len(added) == 1
        assert added[0]["source_type"] == "factual"
