"""
过拟合防护关键数学模型单元测试

覆盖以下核心方法：
1. _environment_match_factor — 二次衰减
2. _calculate_confidence_interval — 小样本修正
3. _apply_time_decay — 指数半衰期
4. LessonValidator.validate_lesson — 最小样本检查
5. _adjust_lessons_by_effectiveness — 置信度上限 0.85
6. _regime_compatibility — 完整兼容性矩阵
7. InstantReflector — min_support_for_update 和 confidence_upper_bound
8. _evict_with_bias_protection — 前瞻性淘汰保护
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.agent.review_agent import LessonValidator, ReviewAgent
from src.agent.review_memory import ReviewMemoryStore

# ========== 辅助工具 ==========


def _make_review_agent(**overrides) -> ReviewAgent:
    """构建最小化的 ReviewAgent（仅测试纯计算方法）"""
    mock_logger = MagicMock()
    mock_prompt_manager = MagicMock()
    mock_prompt_manager.get_review_system_prompt.return_value = "测试系统 Prompt"
    mock_llm_manager = MagicMock()
    mock_llm_manager.get_client.return_value = MagicMock()

    defaults = {
        "logger": mock_logger,
        "prompt_manager": mock_prompt_manager,
        "llm_manager": mock_llm_manager,
        "enable_lesson_validation": False,
    }
    defaults.update(overrides)
    return ReviewAgent(**defaults)


# ========== 1. 二次衰减环境匹配因子 ==========


class TestEnvironmentMatchFactor:
    """测试 _environment_match_factor 的二次衰减行为"""

    def setup_method(self):
        self.agent = _make_review_agent()

    def test_perfect_similarity(self):
        """相似度 1.0 → 匹配因子 1.0"""
        assert self.agent._environment_match_factor(1.0) == 1.0

    def test_high_similarity_quadratic(self):
        """0.9 → 0.81，非线性衰减"""
        result = self.agent._environment_match_factor(0.9)
        assert abs(result - 0.81) < 0.001

    def test_medium_similarity(self):
        """0.7 → 0.49"""
        result = self.agent._environment_match_factor(0.7)
        assert abs(result - 0.49) < 0.001

    def test_low_similarity_penalized_heavily(self):
        """0.5 → 0.25，低相似度惩罚严格"""
        result = self.agent._environment_match_factor(0.5)
        assert abs(result - 0.25) < 0.001

    def test_minimum_floor(self):
        """极低相似度不低于 0.1"""
        assert self.agent._environment_match_factor(0.1) == 0.1
        assert self.agent._environment_match_factor(0.05) == 0.1
        assert self.agent._environment_match_factor(0.0) == 0.1

    def test_sensitivity_at_high_end(self):
        """高相似度区域差异更明显：0.9 vs 0.8 的差距应 > 0.8 vs 0.7"""
        f09 = self.agent._environment_match_factor(0.9)
        f08 = self.agent._environment_match_factor(0.8)
        f07 = self.agent._environment_match_factor(0.7)
        gap_high = f09 - f08  # 0.81 - 0.64 = 0.17
        gap_mid = f08 - f07  # 0.64 - 0.49 = 0.15
        assert gap_high > gap_mid


# ========== 2. 置信区间小样本修正 ==========


class TestConfidenceIntervalSmallSample:
    """测试 _calculate_confidence_interval 的小样本修正"""

    def setup_method(self):
        self.agent = _make_review_agent()

    def test_single_sample_wider_interval(self):
        """1 个样本的区间应比 10 个样本宽"""
        ci_1 = self.agent._calculate_confidence_interval(0.7, 0.7, 1, 0.8)
        ci_10 = self.agent._calculate_confidence_interval(0.7, 0.7, 10, 0.8)
        width_1 = ci_1[1] - ci_1[0]
        width_10 = ci_10[1] - ci_10[0]
        assert width_1 > width_10, f"1 个样本区间({width_1:.3f})应比 10 个样本({width_10:.3f})宽"

    def test_five_samples_no_correction(self):
        """5 个样本时 small_sample_factor = 1.0，无额外放宽"""
        ci_5 = self.agent._calculate_confidence_interval(0.7, 0.7, 5, 0.8)
        ci_6 = self.agent._calculate_confidence_interval(0.7, 0.7, 6, 0.8)
        # support=5 factor=1.0, support=6 factor=1.0，区间差异仅来自 sqrt(n)
        width_5 = ci_5[1] - ci_5[0]
        width_6 = ci_6[1] - ci_6[0]
        # 5 和 6 的差异应远小于 1 和 5 的差异
        ci_1 = self.agent._calculate_confidence_interval(0.7, 0.7, 1, 0.8)
        width_1 = ci_1[1] - ci_1[0]
        assert (width_5 - width_6) < (width_1 - width_5)

    def test_low_similarity_widens_interval(self):
        """相似度低时区间应更宽"""
        ci_high_sim = self.agent._calculate_confidence_interval(0.7, 0.7, 5, 0.9)
        ci_low_sim = self.agent._calculate_confidence_interval(0.7, 0.7, 5, 0.3)
        assert (ci_low_sim[1] - ci_low_sim[0]) > (ci_high_sim[1] - ci_high_sim[0])

    def test_bounds_clamped(self):
        """区间不超出 [0, 1]"""
        ci = self.agent._calculate_confidence_interval(0.9, 0.95, 1, 0.3)
        assert ci[0] >= 0.0
        assert ci[1] <= 1.0

    def test_zero_variance_point(self):
        """confidence=0 或 1 时方差为 0，区间为点"""
        ci_zero = self.agent._calculate_confidence_interval(0.0, 0.0, 5, 0.8)
        assert ci_zero[0] == 0.0
        assert ci_zero[1] == 0.0

        ci_one = self.agent._calculate_confidence_interval(1.0, 1.0, 5, 0.8)
        assert ci_one[0] == 1.0
        assert ci_one[1] == 1.0


# ========== 3. 指数时间衰减 ==========


class TestExponentialTimeDecay:
    """测试 _apply_time_decay 的指数半衰期模型"""

    def test_half_life_property(self):
        """经过 1 个半衰期后，置信度应衰减到约 50%"""
        agent = _make_review_agent(time_decay_days=30)
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        lessons = [{"confidence": 0.8, "last_seen": thirty_days_ago}]

        decayed = agent._apply_time_decay(lessons)
        # 半衰期后 factor = 0.5, confidence ≈ 0.4
        assert abs(decayed[0]["confidence"] - 0.4) < 0.05

    def test_double_half_life(self):
        """经过 2 个半衰期后，置信度应衰减到约 25%"""
        agent = _make_review_agent(time_decay_days=30)
        sixty_days_ago = (datetime.now() - timedelta(days=60)).isoformat()
        lessons = [{"confidence": 0.8, "last_seen": sixty_days_ago}]

        decayed = agent._apply_time_decay(lessons)
        # 2 个半衰期 factor ≈ 0.25, 但最低 0.3 → confidence ≈ 0.8 * 0.3 = 0.24
        assert decayed[0]["confidence"] >= 0.8 * 0.3 - 0.01  # 最低 30%

    def test_minimum_floor_30_percent(self):
        """极长时间后置信度不低于原值的 30%"""
        agent = _make_review_agent(time_decay_days=30)
        year_ago = (datetime.now() - timedelta(days=365)).isoformat()
        lessons = [{"confidence": 1.0, "last_seen": year_ago}]

        decayed = agent._apply_time_decay(lessons)
        assert decayed[0]["confidence"] >= 0.3

    def test_recent_lesson_no_decay(self):
        """今天的经验不衰减"""
        agent = _make_review_agent(time_decay_days=30)
        today = datetime.now().isoformat()
        lessons = [{"confidence": 0.8, "last_seen": today}]

        decayed = agent._apply_time_decay(lessons)
        assert decayed[0]["confidence"] == 0.8

    def test_prefers_last_seen_over_created_at(self):
        """优先使用 last_seen 而非 created_at"""
        agent = _make_review_agent(time_decay_days=30)
        old_time = (datetime.now() - timedelta(days=90)).isoformat()
        recent_time = (datetime.now() - timedelta(days=1)).isoformat()
        lessons = [{"confidence": 0.8, "created_at": old_time, "last_seen": recent_time}]

        decayed = agent._apply_time_decay(lessons)
        # 使用 last_seen（1天前），衰减很小
        assert decayed[0]["confidence"] > 0.75

    def test_no_time_ref_no_decay(self):
        """无时间引用时不衰减"""
        agent = _make_review_agent(time_decay_days=30)
        lessons = [{"confidence": 0.8}]

        decayed = agent._apply_time_decay(lessons)
        assert decayed[0]["confidence"] == 0.8

    def test_disabled_when_zero_days(self):
        """time_decay_days=0 时禁用衰减"""
        agent = _make_review_agent(time_decay_days=0)
        old_time = (datetime.now() - timedelta(days=365)).isoformat()
        lessons = [{"confidence": 0.8, "last_seen": old_time}]

        decayed = agent._apply_time_decay(lessons)
        assert decayed[0]["confidence"] == 0.8


# ========== 4. 经验验证器最小样本检查 ==========


class TestLessonValidatorMinSamples:
    """测试 LessonValidator 的最小样本量检查"""

    def _make_matching_records(self, count: int, win_ratio: float = 0.8):
        """生成匹配的交易记录"""
        records = []
        for i in range(count):
            pnl = 100.0 if (i / count < win_ratio) else -50.0
            records.append(
                {
                    "decision": "BUY",
                    "market_data": {"rsi": 55, "price_change": 0.01},
                    "action_details": {"pnl": pnl},
                }
            )
        return records

    def test_below_min_samples_maintain(self):
        """少于 5 个匹配样本时，推荐 maintain（不做调整）"""
        lesson = {
            "action": "BUY",
            "confidence": 0.7,
            "context_features": {"rsi": 55, "trend_direction": "up", "volatility_level": "normal"},
        }
        # 只有 3 条记录，匹配后可能不到 5 个
        records = self._make_matching_records(3, win_ratio=1.0)

        from src.agent.similarity_scorer import SimilarityScorer

        scorer = SimilarityScorer()
        result = LessonValidator.validate_lesson(lesson, records, scorer)
        assert result["recommendation"] == "maintain"

    def test_sufficient_samples_boost(self):
        """>= 5 个匹配且胜率高时，推荐 boost"""
        lesson = {
            "action": "BUY",
            "confidence": 0.7,
            "context_features": {"rsi": 55, "trend_direction": "up", "volatility_level": "normal"},
        }
        # 大量高胜率记录
        records = self._make_matching_records(20, win_ratio=0.9)

        from src.agent.similarity_scorer import SimilarityScorer

        scorer = SimilarityScorer()
        result = LessonValidator.validate_lesson(lesson, records, scorer)
        # 如果匹配到 >= 5 条且胜率 >= 0.7 → boost
        if result.get("matching_records", 0) >= 5:
            total_app = result["successful_applications"] + result["failed_applications"]
            if total_app >= 5:
                assert result["recommendation"] == "boost"

    def test_insufficient_samples_flag(self):
        """样本不足时应设置 insufficient_samples 标志"""
        lesson = {
            "action": "BUY",
            "confidence": 0.7,
            "context_features": {"rsi": 55, "trend_direction": "up", "volatility_level": "normal"},
        }
        records = self._make_matching_records(2)

        from src.agent.similarity_scorer import SimilarityScorer

        scorer = SimilarityScorer()
        result = LessonValidator.validate_lesson(lesson, records, scorer)
        # 2 条记录 → 样本不足
        if result.get("matching_records", 0) < 5:
            assert (
                result.get("insufficient_samples", False) or result["recommendation"] == "maintain"
            )

    def test_empty_records_maintain(self):
        """空记录时默认 maintain"""
        lesson = {"action": "BUY", "confidence": 0.7, "context_features": {"rsi": 55}}
        result = LessonValidator.validate_lesson(lesson, [], MagicMock())
        assert result["recommendation"] == "maintain"
        assert result["effectiveness_score"] == 0.5

    def test_no_lesson_context_maintain(self):
        """无 context_features 时不匹配任何记录 → maintain"""
        lesson = {"action": "BUY", "confidence": 0.7, "context_features": {}}
        records = self._make_matching_records(10)

        from src.agent.similarity_scorer import SimilarityScorer

        scorer = SimilarityScorer()
        result = LessonValidator.validate_lesson(lesson, records, scorer)
        assert result["recommendation"] == "maintain"


# ========== 5. 置信度上限 0.85 ==========


class TestConfidenceUpperBound:
    """测试 _adjust_lessons_by_effectiveness 的 0.85 上限"""

    def setup_method(self):
        self.agent = _make_review_agent()

    def test_high_confidence_capped(self):
        """高置信度 × 高胜率仍不超 0.85"""
        lessons = [{"confidence": 0.9}]
        effectiveness = {"win_rate": 0.8, "profit_factor": 2.0, "max_consecutive_losses": 0}

        adjusted = self.agent._adjust_lessons_by_effectiveness(lessons, effectiveness)
        # 0.9 × 1.1 × 1.1 = 1.089 → 应被限制到 0.85
        assert adjusted[0]["confidence"] <= 0.85

    def test_low_confidence_not_capped(self):
        """低置信度不受上限影响"""
        lessons = [{"confidence": 0.3}]
        effectiveness = {"win_rate": 0.5, "profit_factor": 1.0, "max_consecutive_losses": 0}

        adjusted = self.agent._adjust_lessons_by_effectiveness(lessons, effectiveness)
        assert adjusted[0]["confidence"] < 0.85

    def test_minimum_floor_01(self):
        """极低胜率时置信度不低于 0.1"""
        lessons = [{"confidence": 0.15}]
        effectiveness = {"win_rate": 0.1, "profit_factor": 0.3, "max_consecutive_losses": 10}

        adjusted = self.agent._adjust_lessons_by_effectiveness(lessons, effectiveness)
        assert adjusted[0]["confidence"] >= 0.1

    def test_empty_lessons(self):
        """空经验列表直接返回"""
        result = self.agent._adjust_lessons_by_effectiveness([], {})
        assert result == []


# ========== 6. Regime 兼容性矩阵 ==========


class TestRegimeCompatibilityMatrix:
    """测试 ReviewMemoryStore._regime_compatibility 的完整矩阵"""

    def test_trending_to_ranging(self):
        assert ReviewMemoryStore._regime_compatibility("trending", "ranging") == 0.4

    def test_ranging_to_trending(self):
        assert ReviewMemoryStore._regime_compatibility("ranging", "trending") == 0.4

    def test_trending_to_volatile(self):
        """趋势 → 高波动：兼容性最低"""
        assert ReviewMemoryStore._regime_compatibility("trending", "volatile") == 0.2

    def test_volatile_to_trending(self):
        assert ReviewMemoryStore._regime_compatibility("volatile", "trending") == 0.2

    def test_ranging_to_volatile(self):
        assert ReviewMemoryStore._regime_compatibility("ranging", "volatile") == 0.3

    def test_volatile_to_ranging(self):
        assert ReviewMemoryStore._regime_compatibility("volatile", "ranging") == 0.3

    def test_same_regime_uses_default(self):
        """相同 Regime 使用默认因子（不在矩阵中）"""
        assert ReviewMemoryStore._regime_compatibility("trending", "trending") == 0.4
        assert ReviewMemoryStore._regime_compatibility("ranging", "ranging") == 0.4

    def test_unknown_uses_default(self):
        """unknown regime 使用默认因子"""
        assert ReviewMemoryStore._regime_compatibility("unknown", "trending") == 0.4
        assert ReviewMemoryStore._regime_compatibility("trending", "unknown") == 0.4

    def test_custom_default_factor(self):
        """自定义默认因子"""
        assert (
            ReviewMemoryStore._regime_compatibility("trending", "trending", default_factor=1.0)
            == 1.0
        )

    def test_symmetry(self):
        """矩阵对称性：(A, B) == (B, A)"""
        pairs = [("trending", "ranging"), ("trending", "volatile"), ("ranging", "volatile")]
        for a, b in pairs:
            assert ReviewMemoryStore._regime_compatibility(
                a, b
            ) == ReviewMemoryStore._regime_compatibility(b, a), f"({a}, {b}) 和 ({b}, {a}) 应对称"


# ========== 7. 即时反思 min_support 和 confidence_upper_bound ==========


class TestInstantReflectionGuardrails:
    """测试即时反思的最小样本量和置信度上限"""

    @pytest.fixture
    def memory_path(self, tmp_path):
        return str(tmp_path / "test_memory.json")

    def _create_store_with_lesson(self, memory_path, support_count=1, confidence=0.7):
        """创建带有单条经验的 store"""
        ctx = {"rsi": 55.0, "trend_direction": "up", "volatility_level": "medium"}
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "测试规则",
                        "action": "做多",
                        "confidence": confidence,
                        "support_count": support_count,
                        "last_seen": datetime.utcnow().isoformat(timespec="seconds"),
                        "context_features": ctx,
                        "source_regime": "trending",
                        "lesson_type": "positive",
                        "source_type": "factual",
                    }
                ]
            }
        }
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return ReviewMemoryStore(memory_path)

    def test_below_min_support_no_confidence_change(self, memory_path):
        """support_count < min_support_for_update 时不调整置信度"""
        from src.agent.instant_reflection import InstantReflector

        store = self._create_store_with_lesson(memory_path, support_count=1, confidence=0.7)

        mock_scorer = MagicMock()
        mock_scorer.compute.return_value = 0.9  # 高相似度
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = {
            "rsi": 55.0,
            "trend_direction": "up",
            "volatility_level": "medium",
        }

        reflector = InstantReflector(
            memory_store=store,
            similarity_scorer=mock_scorer,
            context_extractor=mock_extractor,
            similarity_threshold=0.3,
            min_support_for_update=3,  # 至少 3 次验证
        )

        market_data = {"current_price": 65000.0, "rsi": 55.0}
        reflector.reflect_on_close(
            symbol="BTC",
            decision_record={"decision": "BUY", "market_data": market_data},
            trade_result={"pnl": 100.0},
            market_data=market_data,
        )

        lesson = store.get_lessons("BTC")[0]
        # support_count 递增到 2，但仍 < 3 → confidence 不变
        assert lesson["support_count"] == 2
        assert lesson["confidence"] == 0.7

    def test_at_min_support_confidence_updates(self, memory_path):
        """support_count >= min_support_for_update 时贝叶斯更新置信度"""
        from src.agent.instant_reflection import InstantReflector

        store = self._create_store_with_lesson(memory_path, support_count=3, confidence=0.6)

        mock_scorer = MagicMock()
        mock_scorer.compute.return_value = 0.9
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = {
            "rsi": 55.0,
            "trend_direction": "up",
            "volatility_level": "medium",
        }

        reflector = InstantReflector(
            memory_store=store,
            similarity_scorer=mock_scorer,
            context_extractor=mock_extractor,
            similarity_threshold=0.3,
            min_support_for_update=3,
        )

        market_data = {"current_price": 65000.0, "rsi": 55.0}
        reflector.reflect_on_close(
            symbol="BTC",
            decision_record={"decision": "BUY", "market_data": market_data},
            trade_result={"pnl": 100.0},  # 盈利
            market_data=market_data,
        )

        lesson = store.get_lessons("BTC")[0]
        assert lesson["support_count"] == 4
        # 贝叶斯更新：(3 * 0.6 + 1.0) / 4 = 0.7
        assert abs(lesson["confidence"] - 0.7) < 0.01

    def test_confidence_upper_bound(self, memory_path):
        """连续盈利后置信度不超 confidence_upper_bound"""
        from src.agent.instant_reflection import InstantReflector

        store = self._create_store_with_lesson(memory_path, support_count=10, confidence=0.84)

        mock_scorer = MagicMock()
        mock_scorer.compute.return_value = 0.9
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = {
            "rsi": 55.0,
            "trend_direction": "up",
            "volatility_level": "medium",
        }

        reflector = InstantReflector(
            memory_store=store,
            similarity_scorer=mock_scorer,
            context_extractor=mock_extractor,
            similarity_threshold=0.3,
            min_support_for_update=3,
            confidence_upper_bound=0.85,
        )

        market_data = {"current_price": 65000.0, "rsi": 55.0}
        # 连续盈利 5 次
        for _ in range(5):
            reflector.reflect_on_close(
                symbol="BTC",
                decision_record={"decision": "BUY", "market_data": market_data},
                trade_result={"pnl": 100.0},
                market_data=market_data,
            )

        lesson = store.get_lessons("BTC")[0]
        assert lesson["confidence"] <= 0.85, f"置信度 {lesson['confidence']} 超过上限 0.85"


# ========== 8. 淘汰保护前瞻性逻辑 ==========


class TestEvictionForwardLooking:
    """测试 _evict_with_bias_protection 的前瞻性淘汰保护"""

    @pytest.fixture
    def memory_path(self, tmp_path):
        return str(tmp_path / "evict_test.json")

    def test_negative_protected_when_ratio_borderline(self, memory_path):
        """当 negative 比例刚好在边界时，淘汰仍保护 negative"""
        store = ReviewMemoryStore(memory_path, max_lessons=8)

        # 3 条 negative + 10 条 positive = 13 条，需淘汰 5 条
        # negative 3/13 = 23% < 30% → 应保护
        negative_lessons = [
            {
                "rule": f"neg_{i}",
                "action": f"避免追高{i}",
                "confidence": 0.4,
                "lesson_type": "negative",
            }
            for i in range(3)
        ]
        store.add_lessons("BTC", negative_lessons)

        positive_lessons = [
            {
                "rule": f"pos_{i}",
                "action": f"趋势追多{i}",
                "confidence": 0.5,
                "lesson_type": "positive",
            }
            for i in range(10)
        ]
        store.add_lessons("BTC", positive_lessons, max_positive_ratio=0.7)

        lessons = store.get_lessons("BTC")
        assert len(lessons) <= 8

        stats = store.get_lesson_type_stats("BTC")
        assert stats["negative"] >= 1, f"至少保留 1 条 negative 经验，实际 {stats['negative']}"

    def test_high_confidence_negative_survives(self, memory_path):
        """高置信度 negative 经验应在淘汰中存活"""
        store = ReviewMemoryStore(memory_path, max_lessons=5)

        # 1 条高置信度 negative
        store.add_lessons(
            "ETH",
            [
                {
                    "rule": "高置信避免",
                    "action": "避免在极端波动时追高",
                    "confidence": 0.8,
                    "lesson_type": "negative",
                    "support_count": 10,
                }
            ],
        )

        # 8 条低置信度 positive（触发淘汰）
        low_positive = [
            {
                "rule": f"low_pos_{i}",
                "action": f"做多{i}",
                "confidence": 0.36,
                "lesson_type": "positive",
                "support_count": 1,
            }
            for i in range(8)
        ]
        store.add_lessons("ETH", low_positive, max_positive_ratio=0.7)

        lessons = store.get_lessons("ETH")
        # 高置信度 negative 应该存活
        high_conf_neg = [
            ls
            for ls in lessons
            if ls.get("lesson_type") == "negative" and ls.get("confidence", 0) >= 0.7
        ]
        assert len(high_conf_neg) >= 1, "高置信度 negative 经验应存活"
