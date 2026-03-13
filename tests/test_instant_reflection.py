"""
即时反思模块测试（改进1a）
"""

import json
import os
import tempfile

import pytest

from src.agent.instant_reflection import InstantReflector
from src.agent.review_memory import ReviewMemoryStore


class MockSimilarityScorer:
    """模拟相似度计算器"""

    def __init__(self, default_score=0.8):
        self.default_score = default_score

    def compute(self, features_a, features_b):
        return self.default_score


class MockContextExtractor:
    """模拟上下文特征提取器"""

    def extract(self, market_data, **kwargs):
        return {
            "rsi": market_data.get("rsi", 50),
            "trend_direction": "up",
            "volatility_level": "medium",
        }


class TestInstantReflector:
    """即时反思器测试"""

    @pytest.fixture
    def tmp_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    @pytest.fixture
    def store_with_lessons(self, tmp_path):
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "RSI > 70 时观望",
                        "action": "避免追高做多",
                        "confidence": 0.7,
                        "support_count": 3,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {"rsi": 72, "trend_direction": "up"},
                        "source_regime": "unknown",
                        "lesson_type": "negative",
                        "source_type": "factual",
                    },
                ]
            }
        }
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        return ReviewMemoryStore(path=tmp_path, max_lessons=30)

    @pytest.fixture
    def reflector(self, store_with_lessons):
        return InstantReflector(
            memory_store=store_with_lessons,
            similarity_scorer=MockSimilarityScorer(default_score=0.8),
            context_extractor=MockContextExtractor(),
        )

    def test_reflect_on_close_profitable(self, reflector):
        """测试盈利交易的即时反思"""
        decision_record = {
            "decision": "SELL",
            "timestamp": "2026-03-12T12:00:00",
            "market_data": {"rsi": 65, "current_price": 100000},
            "action_details": {"take_profit_ratio": 0.05, "amount": 100},
        }
        trade_result = {"pnl": 5.0, "status": "SUCCESS"}
        market_data = {"rsi": 65, "current_price": 100000}

        result = reflector.reflect_on_close("BTC", decision_record, trade_result, market_data)

        assert result["trade_profitable"] is True
        assert result["pnl"] == 5.0
        assert result["updated_lessons_count"] > 0
        assert result["symbol"] == "BTC"

    def test_reflect_on_close_losing(self, reflector):
        """测试亏损交易的即时反思"""
        decision_record = {
            "decision": "BUY_TO_COVER",
            "timestamp": "2026-03-12T12:00:00",
            "market_data": {"rsi": 35},
            "action_details": {},
        }
        trade_result = {"pnl": -3.0}
        market_data = {"rsi": 35}

        result = reflector.reflect_on_close("BTC", decision_record, trade_result, market_data)

        assert result["trade_profitable"] is False
        assert result["pnl"] == -3.0

    def test_confidence_boost_on_profit(self, reflector, store_with_lessons):
        """测试盈利交易后经验置信度提升"""
        original_confidence = store_with_lessons.get_lessons("BTC")[0]["confidence"]

        decision_record = {
            "decision": "SELL",
            "market_data": {"rsi": 72},
            "action_details": {},
        }
        trade_result = {"pnl": 10.0}
        market_data = {"rsi": 72}

        reflector.reflect_on_close("BTC", decision_record, trade_result, market_data)

        updated_confidence = store_with_lessons.get_lessons("BTC")[0]["confidence"]
        assert updated_confidence > original_confidence

    def test_confidence_decay_on_loss(self, reflector, store_with_lessons):
        """测试亏损交易后经验置信度降低"""
        original_confidence = store_with_lessons.get_lessons("BTC")[0]["confidence"]

        decision_record = {
            "decision": "SELL",
            "market_data": {"rsi": 72},
            "action_details": {},
        }
        trade_result = {"pnl": -5.0}
        market_data = {"rsi": 72}

        reflector.reflect_on_close("BTC", decision_record, trade_result, market_data)

        updated_confidence = store_with_lessons.get_lessons("BTC")[0]["confidence"]
        assert updated_confidence < original_confidence

    def test_no_update_low_similarity(self):
        """测试低相似度时不更新经验"""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            data = {
                "lessons": {
                    "BTC": [
                        {
                            "rule": "test",
                            "action": "test",
                            "confidence": 0.7,
                            "support_count": 1,
                            "context_features": {"rsi": 20},
                            "last_seen": "2026-03-12",
                        }
                    ]
                }
            }
            with open(path, "w") as f:
                json.dump(data, f)

            store = ReviewMemoryStore(path=path, max_lessons=30)
            reflector = InstantReflector(
                memory_store=store,
                similarity_scorer=MockSimilarityScorer(default_score=0.3),  # 低相似度
                context_extractor=MockContextExtractor(),
            )

            result = reflector.reflect_on_close(
                "BTC",
                {"decision": "SELL", "market_data": {}, "action_details": {}},
                {"pnl": 5.0},
                {"rsi": 80},
            )
            assert result["updated_lessons_count"] == 0
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_deviation_calculation(self, reflector):
        """测试偏差计算"""
        decision_record = {
            "decision": "SELL",
            "timestamp": "2026-03-12T10:00:00",
            "action_details": {
                "take_profit_ratio": 0.05,
                "amount": 100,
            },
        }
        trade_result = {
            "pnl": 3.0,
            "timestamp": "2026-03-12T12:00:00",
        }

        result = reflector.reflect_on_close(
            "BTC", decision_record, trade_result, {"rsi": 50}
        )

        deviation = result["deviation"]
        assert deviation["actual_pnl"] == 3.0
        assert deviation["expected_pnl"] == 5.0  # 100 * 0.05
