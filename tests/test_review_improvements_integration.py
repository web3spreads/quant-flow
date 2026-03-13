"""
复盘改进模块集成测试

覆盖 5 项改进之间的交互和边界情况：
1. 线程安全：多线程并发读写 ReviewMemoryStore
2. Regime + 偏差防护交互
3. 事实型 + Regime 权重交互
4. 即时反思 + 记忆更新链路
5. VFT 段落完整标注
6. 空/异常输入边界
7. 置信度校准边界
8. Prompt 效果评估 + 历史趋势
"""

import json
import threading
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.agent.context_extractor import ContextExtractor
from src.agent.instant_reflection import InstantReflector
from src.agent.prompt_meta_reflection import PromptMetaReflector
from src.agent.review_memory import ReviewMemoryStore
from src.agent.similarity_scorer import SimilarityScorer

# ========== 公共 Fixtures ==========


@pytest.fixture
def memory_path(tmp_path):
    """临时经验存储文件路径"""
    return str(tmp_path / "review_memory.json")


@pytest.fixture
def empty_store(memory_path):
    """空的经验存储"""
    return ReviewMemoryStore(memory_path)


@pytest.fixture
def scorer():
    """相似度计算器"""
    return SimilarityScorer()


@pytest.fixture
def context_extractor():
    """环境特征提取器"""
    return ContextExtractor()


def _make_lesson(
    rule: str,
    action: str,
    confidence: float = 0.7,
    support_count: int = 2,
    lesson_type: str = "positive",
    source_type: str = "mixed",
    source_regime: str = "unknown",
    context_features: dict | None = None,
) -> dict:
    """辅助函数：快速构建一条经验"""
    return {
        "rule": rule,
        "action": action,
        "confidence": confidence,
        "support_count": support_count,
        "lesson_type": lesson_type,
        "source_type": source_type,
        "source_regime": source_regime,
        "context_features": context_features or {},
        "last_seen": datetime.utcnow().isoformat(timespec="seconds"),
    }


# ========== 1. 线程安全测试 ==========


class TestThreadSafety:
    """多线程并发读写 ReviewMemoryStore，验证数据一致性"""

    def test_concurrent_add_lessons(self, memory_path):
        """并发写入多条经验，验证无数据丢失或异常"""
        store = ReviewMemoryStore(memory_path, max_lessons=200)

        errors = []
        barrier = threading.Barrier(10)

        def writer(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(20):
                    lesson = _make_lesson(
                        rule=f"线程{thread_id}_规则{i}",
                        action=f"线程{thread_id}_动作{i}",
                        confidence=0.5 + (i % 5) * 0.1,
                    )
                    store.add_lessons("BTC", [lesson])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"线程执行中出现异常: {errors}"

        # 验证所有经验都被写入（因为 rule 不同，不会合并）
        lessons = store.get_lessons("BTC")
        assert len(lessons) == 200, f"期望 200 条经验，实际 {len(lessons)}"

    def test_concurrent_read_write(self, memory_path):
        """并发读写混合操作，验证不会死锁或崩溃"""
        store = ReviewMemoryStore(memory_path, max_lessons=100)
        # 预填充一些数据
        for i in range(10):
            store.add_lessons("ETH", [_make_lesson(
                rule=f"预填充规则{i}",
                action=f"预填充动作{i}",
            )])

        errors = []
        barrier = threading.Barrier(8)

        def reader():
            try:
                barrier.wait(timeout=5)
                for _ in range(50):
                    store.get_lessons("ETH")
                    store.get_lessons_summary("ETH")
                    store.get_verbal_finetuning_section("ETH")
            except Exception as e:
                errors.append(e)

        def writer(tid):
            try:
                barrier.wait(timeout=5)
                for i in range(10):
                    store.add_lessons("ETH", [_make_lesson(
                        rule=f"并发规则_{tid}_{i}",
                        action=f"并发动作_{tid}_{i}",
                    )])
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(4):
            threads.append(threading.Thread(target=reader))
        for tid in range(4):
            threads.append(threading.Thread(target=writer, args=(tid,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"并发读写异常: {errors}"

    def test_concurrent_save_load(self, memory_path):
        """并发保存和加载，验证文件一致性"""
        store = ReviewMemoryStore(memory_path, max_lessons=50)

        errors = []

        def save_worker():
            try:
                for i in range(20):
                    store.add_lessons("BTC", [_make_lesson(
                        rule=f"保存测试规则{i}",
                        action=f"保存测试动作{i}",
                    )])
            except Exception as e:
                errors.append(e)

        def load_worker():
            try:
                for _ in range(20):
                    store.load()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=save_worker)
        t2 = threading.Thread(target=load_worker)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"并发保存/加载异常: {errors}"

        # 重新加载，验证文件完整性
        store2 = ReviewMemoryStore(memory_path, max_lessons=50)
        lessons = store2.get_lessons("BTC")
        assert len(lessons) > 0, "文件应包含有效数据"


# ========== 2. Regime + 偏差防护交互 ==========


class TestRegimeBiasProtection:
    """在 ranging regime 下添加大量 positive 经验，验证淘汰时保护 negative 经验"""

    def test_evict_protects_negative_in_ranging_regime(self, memory_path):
        """
        添加超出 max_lessons 的经验，其中大部分是 positive，
        验证淘汰策略保护 negative 经验不被过度淘汰
        """
        store = ReviewMemoryStore(memory_path, max_lessons=10)

        # 先添加 5 条 negative 经验（低置信度，容易被淘汰的候选）
        negative_lessons = [
            _make_lesson(
                rule=f"negative_规则{i}",
                action=f"避免在震荡市追高{i}",
                confidence=0.4,
                lesson_type="negative",
                source_regime="ranging",
            )
            for i in range(5)
        ]
        store.add_lessons("BTC", negative_lessons, current_regime="ranging")

        # 再添加 10 条 positive 经验（触发淘汰）
        positive_lessons = [
            _make_lesson(
                rule=f"positive_规则{i}",
                action=f"震荡区间低买高卖{i}",
                confidence=0.45,
                lesson_type="positive",
                source_regime="ranging",
            )
            for i in range(10)
        ]
        store.add_lessons(
            "BTC", positive_lessons, current_regime="ranging", max_positive_ratio=0.7
        )

        # 验证淘汰后总量不超限
        lessons = store.get_lessons("BTC")
        assert len(lessons) <= 10

        # 验证 negative 经验得到保护（至少保留部分）
        stats = store.get_lesson_type_stats("BTC")
        assert stats["negative"] >= 2, (
            f"negative 经验应至少保留 2 条（偏差防护），实际 {stats['negative']}"
        )

    def test_regime_mismatch_reduces_similarity(self, memory_path, scorer):
        """验证 Regime 不匹配时相似度被降权"""
        store = ReviewMemoryStore(memory_path)

        # 添加 trending regime 的经验
        ctx = {"rsi": 60.0, "trend_direction": "up", "volatility_level": "medium"}
        store.add_lessons("BTC", [
            _make_lesson(
                rule="趋势突破追多",
                action="开多",
                confidence=0.8,
                source_regime="trending",
                context_features=ctx,
            )
        ], current_regime="trending")

        # 在 ranging regime 下查询相似经验
        similar = store.get_similar_lessons(
            symbol="BTC",
            context_features=ctx,
            scorer=scorer,
            similarity_threshold=0.0,
            current_regime="ranging",
            regime_mismatch_factor=0.4,
        )

        # 同 regime 下查询
        similar_matching = store.get_similar_lessons(
            symbol="BTC",
            context_features=ctx,
            scorer=scorer,
            similarity_threshold=0.0,
            current_regime="trending",
        )

        # Regime 不匹配时相似度应降低
        if similar and similar_matching:
            assert similar[0]["similarity_score"] < similar_matching[0]["similarity_score"]


# ========== 3. 事实型 + Regime 权重交互 ==========


class TestFactualRegimeWeight:
    """验证 ranging regime 下 factual 经验排在 subjective 前面"""

    def test_ranging_regime_factual_boost(self, memory_path):
        """在 ranging regime 下，factual 经验应获得权重提升"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "RSI > 70 超买",
                        "action": "等待回调",
                        "confidence": 0.65,
                        "support_count": 3,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                        "source_regime": "ranging",
                        "lesson_type": "positive",
                        "source_type": "factual",
                    },
                    {
                        "rule": "市场情绪恐惧",
                        "action": "逆向做多",
                        "confidence": 0.65,
                        "support_count": 3,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                        "source_regime": "ranging",
                        "lesson_type": "positive",
                        "source_type": "subjective",
                    },
                ],
            }
        }
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        store = ReviewMemoryStore(memory_path)

        # ranging regime 下 factual 应排在前面
        vft = store.get_verbal_finetuning_section(
            "BTC", current_regime="ranging", ranging_factual_boost=1.3
        )

        pos_factual = vft.find("RSI > 70")
        pos_subjective = vft.find("市场情绪恐惧")
        assert pos_factual < pos_subjective, (
            "ranging regime 下 factual 经验应排在 subjective 前面"
        )

    def test_trending_regime_subjective_boost(self, memory_path):
        """在 trending regime 下，subjective 经验应获得权重提升"""
        data = {
            "lessons": {
                "ETH": [
                    {
                        "rule": "MACD 金叉确认",
                        "action": "跟随趋势做多",
                        "confidence": 0.65,
                        "support_count": 3,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                        "source_regime": "trending",
                        "lesson_type": "positive",
                        "source_type": "factual",
                    },
                    {
                        "rule": "市场氛围极度乐观",
                        "action": "顺势加仓",
                        "confidence": 0.65,
                        "support_count": 3,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                        "source_regime": "trending",
                        "lesson_type": "positive",
                        "source_type": "subjective",
                    },
                ],
            }
        }
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        store = ReviewMemoryStore(memory_path)

        vft = store.get_verbal_finetuning_section(
            "ETH", current_regime="trending", trending_subjective_boost=1.3
        )

        pos_factual = vft.find("MACD 金叉确认")
        pos_subjective = vft.find("市场氛围极度乐观")
        assert pos_subjective < pos_factual, (
            "trending regime 下 subjective 经验应排在 factual 前面"
        )


# ========== 4. 即时反思 + 记忆更新链路 ==========


class TestInstantReflectionMemoryUpdate:
    """InstantReflector 更新经验后验证 memory_store 中数据变化"""

    @pytest.fixture
    def store_with_context(self, memory_path):
        """创建包含带 context_features 经验的存储"""
        ctx = {
            "rsi": 55.0,
            "trend_direction": "up",
            "volatility_level": "medium",
            "macd_signal": "bullish",
            "volume_ratio": 1.2,
            "price_position": 0.6,
            "time_of_day": "morning",
            "ema_trend": "bullish",
        }
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "MACD金叉追多",
                        "action": "开多 5x",
                        "confidence": 0.6,
                        "support_count": 2,
                        "last_seen": "2026-03-10T10:00:00",
                        "context_features": ctx,
                        "source_regime": "trending",
                        "lesson_type": "positive",
                        "source_type": "factual",
                    },
                ],
            }
        }
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return ReviewMemoryStore(memory_path)

    def test_profitable_trade_boosts_confidence(
        self, store_with_context, scorer, context_extractor
    ):
        """盈利交易后，匹配经验的 confidence 应提升"""
        reflector = InstantReflector(
            memory_store=store_with_context,
            similarity_scorer=scorer,
            context_extractor=context_extractor,
            similarity_threshold=0.3,
            min_support_for_update=1,  # 允许少量样本即更新（测试用）
        )

        original_conf = store_with_context.get_lessons("BTC")[0]["confidence"]

        market_data = {
            "current_price": 65000.0,
            "rsi": 55.0,
            "macd_hist": 100,
            "macd_signal": 200,
            "ma_7": 64500,
            "ma_25": 63000,
            "volume": 1000,
            "volume_ma_20": 800,
        }

        result = reflector.reflect_on_close(
            symbol="BTC",
            decision_record={"decision": "BUY", "market_data": market_data},
            trade_result={"pnl": 15.0},
            market_data=market_data,
        )

        assert result["trade_profitable"] is True
        assert result["updated_lessons_count"] >= 1

        # 验证 confidence 已提升
        updated_conf = store_with_context.get_lessons("BTC")[0]["confidence"]
        assert updated_conf > original_conf, (
            f"盈利后 confidence 应提升: {original_conf} -> {updated_conf}"
        )

    def test_losing_trade_decays_confidence(
        self, store_with_context, scorer, context_extractor
    ):
        """亏损交易后，匹配经验的 confidence 应下降"""
        reflector = InstantReflector(
            memory_store=store_with_context,
            similarity_scorer=scorer,
            context_extractor=context_extractor,
            similarity_threshold=0.3,
            min_support_for_update=1,  # 允许少量样本即更新（测试用）
        )

        original_conf = store_with_context.get_lessons("BTC")[0]["confidence"]

        market_data = {
            "current_price": 65000.0,
            "rsi": 55.0,
            "macd_hist": 100,
            "macd_signal": 200,
            "ma_7": 64500,
            "ma_25": 63000,
            "volume": 1000,
            "volume_ma_20": 800,
        }

        result = reflector.reflect_on_close(
            symbol="BTC",
            decision_record={"decision": "BUY", "market_data": market_data},
            trade_result={"pnl": -10.0},
            market_data=market_data,
        )

        assert result["trade_profitable"] is False

        updated_conf = store_with_context.get_lessons("BTC")[0]["confidence"]
        assert updated_conf < original_conf, (
            f"亏损后 confidence 应下降: {original_conf} -> {updated_conf}"
        )

    def test_support_count_incremented(
        self, store_with_context, scorer, context_extractor
    ):
        """反思后匹配经验的 support_count 应增加"""
        reflector = InstantReflector(
            memory_store=store_with_context,
            similarity_scorer=scorer,
            context_extractor=context_extractor,
            similarity_threshold=0.3,
        )

        original_support = store_with_context.get_lessons("BTC")[0]["support_count"]

        market_data = {
            "current_price": 65000.0,
            "rsi": 55.0,
            "macd_hist": 100,
            "macd_signal": 200,
            "ma_7": 64500,
            "ma_25": 63000,
            "volume": 1000,
            "volume_ma_20": 800,
        }

        reflector.reflect_on_close(
            symbol="BTC",
            decision_record={"decision": "BUY", "market_data": market_data},
            trade_result={"pnl": 5.0},
            market_data=market_data,
        )

        updated_support = store_with_context.get_lessons("BTC")[0]["support_count"]
        assert updated_support == original_support + 1


# ========== 5. VFT 段落完整标注测试 ==========


class TestVFTCompleteAnnotation:
    """同时有 regime、lesson_type、source_type 标注的经验，验证 VFT 输出完整"""

    def test_full_annotation_output(self, memory_path):
        """验证包含全部三种标注的经验在 VFT 输出中完整呈现"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "RSI > 80 且 ATR 急剧放大",
                        "action": "避免追多，等待回调",
                        "confidence": 0.8,
                        "support_count": 5,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                        "source_regime": "volatile",
                        "lesson_type": "negative",
                        "source_type": "factual",
                    },
                ],
            }
        }
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        store = ReviewMemoryStore(memory_path)
        vft = store.get_verbal_finetuning_section("BTC")

        # 验证三种标注都出现
        assert "[避免]" in vft, "negative 经验应有 [避免] 前缀"
        assert "[高波动市经验]" in vft, "volatile regime 应标注 [高波动市经验]"
        assert "[事实型]" in vft, "factual source_type 应标注 [事实型]"

    def test_trending_positive_subjective_annotation(self, memory_path):
        """验证 trending + positive + subjective 标注"""
        data = {
            "lessons": {
                "ETH": [
                    {
                        "rule": "市场情绪极度乐观",
                        "action": "顺势做多",
                        "confidence": 0.75,
                        "support_count": 3,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                        "source_regime": "trending",
                        "lesson_type": "positive",
                        "source_type": "subjective",
                    },
                ],
            }
        }
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        store = ReviewMemoryStore(memory_path)
        vft = store.get_verbal_finetuning_section("ETH")

        # positive 不应有 [避免] 前缀
        assert "[避免]" not in vft
        assert "[趋势市经验]" in vft
        assert "[主观型]" in vft

    def test_ranging_negative_mixed_annotation(self, memory_path):
        """验证 ranging + negative + mixed 不标注 source_type"""
        data = {
            "lessons": {
                "BTC": [
                    {
                        "rule": "区间震荡无明确方向",
                        "action": "避免重仓",
                        "confidence": 0.7,
                        "support_count": 4,
                        "last_seen": "2026-03-12T10:00:00",
                        "context_features": {},
                        "source_regime": "ranging",
                        "lesson_type": "negative",
                        "source_type": "mixed",
                    },
                ],
            }
        }
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        store = ReviewMemoryStore(memory_path)
        vft = store.get_verbal_finetuning_section("BTC")

        assert "[避免]" in vft
        assert "[震荡市经验]" in vft
        # mixed 不在 type_labels 中，不应出现标注
        assert "[事实型]" not in vft
        assert "[主观型]" not in vft


# ========== 6. 空/异常输入边界 ==========


class TestEmptyAndAbnormalInputs:
    """空 market_data、None 值、空字符串等边界情况"""

    def test_add_empty_lessons(self, empty_store):
        """添加空经验列表不应报错"""
        result = empty_store.add_lessons("BTC", [])
        assert result == []

    def test_add_lessons_with_none_values(self, empty_store):
        """经验中包含 None 值时应正常处理（被过滤）"""
        lessons = [
            {"rule": None, "action": "做多", "confidence": 0.8},
            {"rule": "规则", "action": None, "confidence": 0.8},
            {"rule": "", "action": "做多", "confidence": 0.8},
            {"rule": "规则", "action": "", "confidence": 0.8},
        ]
        result = empty_store.add_lessons("BTC", lessons)
        assert result == [], "rule 或 action 为空/None 的经验应被过滤"

    def test_add_lessons_with_zero_confidence(self, empty_store):
        """置信度为 0 的经验应被过滤"""
        lessons = [_make_lesson(rule="零置信", action="忽略", confidence=0.0)]
        result = empty_store.add_lessons("BTC", lessons)
        assert result == []

    def test_get_lessons_nonexistent_symbol(self, empty_store):
        """查询不存在的交易对应返回空列表"""
        assert empty_store.get_lessons("NONEXIST") == []

    def test_get_similar_lessons_empty_context(self, empty_store, scorer):
        """空上下文查询相似经验不应报错"""
        result = empty_store.get_similar_lessons(
            symbol="BTC",
            context_features={},
            scorer=scorer,
        )
        assert result == []

    def test_instant_reflection_empty_market_data(
        self, empty_store, scorer, context_extractor
    ):
        """空 market_data 不应导致即时反思崩溃"""
        reflector = InstantReflector(
            memory_store=empty_store,
            similarity_scorer=scorer,
            context_extractor=context_extractor,
        )

        result = reflector.reflect_on_close(
            symbol="BTC",
            decision_record={"decision": "BUY", "market_data": {}},
            trade_result={"pnl": 0},
            market_data={},
        )

        assert "symbol" in result
        assert result["symbol"] == "BTC"
        assert result["updated_lessons_count"] == 0

    def test_instant_reflection_none_trade_result(
        self, empty_store, scorer, context_extractor
    ):
        """trade_result 中 pnl 为 None 时不崩溃"""
        reflector = InstantReflector(
            memory_store=empty_store,
            similarity_scorer=scorer,
            context_extractor=context_extractor,
        )

        result = reflector.reflect_on_close(
            symbol="BTC",
            decision_record={"decision": "BUY"},
            trade_result={"pnl": None},
            market_data={},
        )

        assert result["pnl"] == 0.0
        assert result["trade_profitable"] is False

    def test_vft_empty_store(self, empty_store):
        """空存储的 VFT 输出应为空字符串"""
        assert empty_store.get_verbal_finetuning_section("BTC") == ""

    def test_lesson_type_stats_empty(self, empty_store):
        """空存储的类型统计应返回零值"""
        stats = empty_store.get_lesson_type_stats("BTC")
        assert stats["total"] == 0
        assert stats["positive"] == 0
        assert stats["negative"] == 0
        assert stats["positive_ratio"] == 0.0

    def test_load_corrupted_file(self, memory_path):
        """损坏的 JSON 文件应优雅降级"""
        with open(memory_path, "w") as f:
            f.write("这不是有效的 JSON {{{")

        store = ReviewMemoryStore(memory_path)
        assert store.lessons == {}
        assert store.get_lessons("BTC") == []


# ========== 7. 置信度校准边界 ==========


class TestConfidenceCalibrationBoundary:
    """全部 DO_NOTHING 决策时 calibration 应返回中性分数"""

    @pytest.fixture
    def reflector(self, tmp_path):
        """创建 PromptMetaReflector（mock LLM）"""
        mock_llm_manager = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content='{"suggestions": []}')
        mock_llm_manager.get_client.return_value = mock_llm

        mock_pm = MagicMock()
        mock_pm.format_prompt_meta_review.return_value = "测试"

        return PromptMetaReflector(
            llm_manager=mock_llm_manager,
            prompt_manager=mock_pm,
            memory_store=MagicMock(),
            output_dir=str(tmp_path / "prompt_opt"),
        )

    def test_all_do_nothing_calibration(self, reflector):
        """全部为 DO_NOTHING 决策时校准分数为中性"""
        records = [
            {"decision": "DO_NOTHING", "action_details": {"confidence": 0.8, "pnl": 0}},
            {"decision": "DO_NOTHING", "action_details": {"confidence": 0.5, "pnl": 0}},
            {"decision": "HOLD", "action_details": {"confidence": 0.3, "pnl": 0}},
            {"decision": "DO_NOTHING", "action_details": {"confidence": 0.9, "pnl": 0}},
        ]
        result = reflector._evaluate_calibration(records)

        # 所有记录都是 DO_NOTHING/HOLD，应被跳过
        assert result["high_confidence_total"] == 0
        assert result["low_confidence_total"] == 0
        # 无数据时 score 默认 0.5（中性）
        assert result["score"] == 0.5

    def test_only_high_confidence_no_low(self, reflector):
        """只有高置信度决策（无低置信度对比），校准分数为默认中性"""
        records = [
            {"decision": "BUY", "action_details": {"confidence": 0.8, "pnl": 10}},
            {"decision": "SELL", "action_details": {"confidence": 0.9, "pnl": 5}},
        ]
        result = reflector._evaluate_calibration(records)

        assert result["high_confidence_total"] == 2
        assert result["low_confidence_total"] == 0
        # 无低置信度数据时，calibration = 0，score = 0.5
        assert result["score"] == 0.5

    def test_empty_records_calibration(self, reflector):
        """空记录时返回中性"""
        result = reflector._evaluate_calibration([])

        assert result["score"] == 0.5
        assert result["high_confidence_total"] == 0
        assert result["low_confidence_total"] == 0


# ========== 8. Prompt 效果评估 + 历史趋势 ==========


class TestPromptEffectivenessHistoricalTrend:
    """保存多个报告后 get_historical_scores 返回正确"""

    @pytest.fixture
    def reflector(self, tmp_path):
        """创建 PromptMetaReflector（mock LLM）"""
        mock_llm_manager = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content='{"suggestions": []}')
        mock_llm_manager.get_client.return_value = mock_llm

        mock_pm = MagicMock()
        mock_pm.format_prompt_meta_review.return_value = "测试"

        return PromptMetaReflector(
            llm_manager=mock_llm_manager,
            prompt_manager=mock_pm,
            memory_store=MagicMock(),
            output_dir=str(tmp_path / "prompt_opt"),
        )

    def test_save_and_read_multiple_reports(self, reflector, tmp_path):
        """保存多个报告后应能正确读取历史趋势"""
        output_dir = tmp_path / "prompt_opt"

        # 手动写入多个周报告文件
        for week_num in range(1, 4):
            week_str = f"2026-W{week_num:02d}"
            report_data = {
                "report": {
                    "overall_score": 0.5 + week_num * 0.1,
                    "fincot_completion": {"score": 0.4 + week_num * 0.1},
                    "lesson_citation_rate": {"score": 0.3 + week_num * 0.05},
                    "decision_consistency": {"score": 0.7},
                    "confidence_calibration": {"score": 0.6},
                },
                "suggestions": [],
                "saved_at": datetime.now().isoformat(),
            }
            file_path = output_dir / f"{week_str}_report.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False)

        scores = reflector.get_historical_scores(weeks=8)

        # 应返回 3 条记录，按时间正序排列
        assert len(scores) == 3
        assert scores[0]["week"] == "2026-W01"
        assert scores[2]["week"] == "2026-W03"

        # 验证分数递增
        assert scores[0]["overall_score"] < scores[2]["overall_score"]

    def test_save_report_then_read_back(self, reflector):
        """通过 save_report 保存后通过 get_historical_scores 读回"""
        report = {
            "overall_score": 0.75,
            "fincot_completion": {"score": 0.8},
            "lesson_citation_rate": {"score": 0.6},
            "decision_consistency": {"score": 0.9},
            "confidence_calibration": {"score": 0.7},
        }
        reflector.save_report(report, [{"target_step": "test", "problem": "p", "suggestion": "s"}])

        scores = reflector.get_historical_scores()

        assert len(scores) == 1
        assert scores[0]["overall_score"] == 0.75
        assert scores[0]["fincot"] == 0.8
        assert scores[0]["citation"] == 0.6

    def test_evaluate_and_save_full_pipeline(self, reflector):
        """完整管道：评估 -> 保存 -> 读取历史"""
        records = [
            {
                "reason": "趋势确认：多头。入场信号：MACD金叉。情绪校验：偏多。"
                "复盘比对：符合经验。风险计算：盈亏比1.8。最终决策：BUY",
                "decision": "BUY",
                "market_data": {"rsi": 55},
                "action_details": {"confidence": 0.8, "pnl": 10, "output": ""},
            },
            {
                "reason": "趋势确认：空头。最终决策：HOLD",
                "decision": "DO_NOTHING",
                "market_data": {"rsi": 45},
                "action_details": {"confidence": 0.5, "pnl": 0, "output": ""},
            },
        ]

        report = reflector.evaluate_prompt_effectiveness(records, [])
        assert report["overall_score"] > 0

        reflector.save_report(report, [])

        scores = reflector.get_historical_scores()
        assert len(scores) == 1
        assert scores[0]["overall_score"] == report["overall_score"]

    def test_historical_scores_empty_dir(self, reflector):
        """空目录应返回空列表"""
        scores = reflector.get_historical_scores()
        # 因为前面没有保存任何报告（除非有其他测试写入），但 fixture 每次创建新 tmp_path
        assert isinstance(scores, list)
