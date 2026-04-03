"""
决策录制器和回放器单元测试
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.backtest.decision_recorder import SCHEMA_VERSION, DecisionRecorder
from src.backtest.decision_replayer import DecisionReplayer


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def jsonl_path(tmp_dir):
    return tmp_dir / "decisions.jsonl"


# ──────────── DecisionRecorder 测试 ────────────


class TestDecisionRecorder:
    """决策录制器"""

    def test_writes_jsonl(self, jsonl_path):
        """录制 3 条决策后验证文件内容"""
        with DecisionRecorder(jsonl_path) as recorder:
            for i in range(3):
                recorder.record(
                    timestamp=datetime(2024, 1, 1, i, 0, 0),
                    symbol="BTC",
                    decision="BUY" if i % 2 == 0 else "DO_NOTHING",
                    details={"output": f"reason_{i}", "leverage": 5, "confidence": 0.8},
                )

        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 3

        for line in lines:
            record = json.loads(line)
            assert "timestamp" in record
            assert "symbol" in record
            assert "decision" in record
            assert "details" in record

    def test_schema_version(self, jsonl_path):
        """每行包含 schema_version"""
        with DecisionRecorder(jsonl_path) as recorder:
            recorder.record(
                timestamp="2024-01-01T00:00:00",
                symbol="BTC",
                decision="BUY",
                details={},
            )

        record = json.loads(jsonl_path.read_text().strip())
        assert record["schema_version"] == SCHEMA_VERSION

    def test_context_manager(self, jsonl_path):
        """with 语法正确关闭文件"""
        with DecisionRecorder(jsonl_path) as recorder:
            recorder.record(
                timestamp=datetime(2024, 1, 1),
                symbol="ETH",
                decision="SELL",
                details={"output": "test"},
            )
        # 文件应已关闭
        assert recorder._file.closed

    def test_serializes_core_fields_only(self, jsonl_path):
        """只保留核心字段，丢弃不可序列化的大对象"""
        with DecisionRecorder(jsonl_path) as recorder:
            recorder.record(
                timestamp=datetime(2024, 1, 1),
                symbol="BTC",
                decision="BUY",
                details={
                    "output": "reasoning",
                    "leverage": 5,
                    "confidence": 0.85,
                    "prompt": "very long prompt..." * 100,  # 不在 _DETAIL_KEYS 中
                    "unknown_field": [1, 2, 3],
                },
            )

        record = json.loads(jsonl_path.read_text().strip())
        details = record["details"]
        assert "output" in details
        assert "leverage" in details
        assert "prompt" not in details
        assert "unknown_field" not in details

    def test_count_property(self, jsonl_path):
        """count 属性正确递增"""
        with DecisionRecorder(jsonl_path) as recorder:
            assert recorder.count == 0
            recorder.record(
                timestamp=datetime(2024, 1, 1), symbol="BTC", decision="BUY", details={}
            )
            assert recorder.count == 1


# ──────────── DecisionReplayer 测试 ────────────


def _create_jsonl(path: Path, records: list[dict]) -> None:
    """辅助函数：创建 JSONL 测试文件"""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


class TestDecisionReplayer:
    """决策回放器"""

    def test_exact_match(self, jsonl_path):
        """精确时间戳匹配"""
        _create_jsonl(
            jsonl_path,
            [
                {
                    "schema_version": 1,
                    "timestamp": "2024-01-01T00:00:00",
                    "symbol": "BTC",
                    "decision": "BUY",
                    "details": {"leverage": 5},
                },
            ],
        )
        replayer = DecisionReplayer(jsonl_path)
        result = replayer.get_decision(datetime(2024, 1, 1, 0, 0, 0), "BTC")
        assert result is not None
        decision, details = result
        assert decision == "BUY"
        assert details["leverage"] == 5

    def test_tolerance_match(self, jsonl_path):
        """在容忍度范围内匹配（偏差 300 秒）"""
        _create_jsonl(
            jsonl_path,
            [
                {
                    "schema_version": 1,
                    "timestamp": "2024-01-01T00:00:00",
                    "symbol": "BTC",
                    "decision": "SELL",
                    "details": {},
                },
            ],
        )
        replayer = DecisionReplayer(jsonl_path, tolerance_seconds=900)
        # 查询时间偏差 5 分钟（300 秒），应匹配
        result = replayer.get_decision(datetime(2024, 1, 1, 0, 5, 0), "BTC")
        assert result is not None
        assert result[0] == "SELL"

    def test_no_match_beyond_tolerance(self, jsonl_path):
        """超出容忍度返回 None"""
        _create_jsonl(
            jsonl_path,
            [
                {
                    "schema_version": 1,
                    "timestamp": "2024-01-01T00:00:00",
                    "symbol": "BTC",
                    "decision": "BUY",
                    "details": {},
                },
            ],
        )
        replayer = DecisionReplayer(jsonl_path, tolerance_seconds=60)
        # 查询时间偏差 10 分钟（600 秒），超出 60 秒容忍度
        result = replayer.get_decision(datetime(2024, 1, 1, 0, 10, 0), "BTC")
        assert result is None

    def test_sequential_access(self, jsonl_path):
        """100 条记录顺序读取"""
        records = []
        for i in range(100):
            records.append(
                {
                    "schema_version": 1,
                    "timestamp": (datetime(2024, 1, 1) + timedelta(minutes=15 * i)).isoformat(),
                    "symbol": "BTC",
                    "decision": "BUY" if i % 3 == 0 else "DO_NOTHING",
                    "details": {"index": i},
                }
            )
        _create_jsonl(jsonl_path, records)

        replayer = DecisionReplayer(jsonl_path)
        assert replayer.total_decisions == 100

        matched = 0
        for i in range(100):
            ts = datetime(2024, 1, 1) + timedelta(minutes=15 * i)
            result = replayer.get_decision(ts, "BTC")
            if result is not None:
                matched += 1
        assert matched == 100

    def test_replay_determinism(self, jsonl_path):
        """同一 JSONL 两次加载，相同查询返回相同结果"""
        _create_jsonl(
            jsonl_path,
            [
                {
                    "schema_version": 1,
                    "timestamp": "2024-01-01T01:00:00",
                    "symbol": "BTC",
                    "decision": "SELL_SHORT",
                    "details": {"confidence": 0.9},
                },
            ],
        )

        r1 = DecisionReplayer(jsonl_path)
        r2 = DecisionReplayer(jsonl_path)

        ts = datetime(2024, 1, 1, 1, 0, 0)
        result1 = r1.get_decision(ts, "BTC")
        result2 = r2.get_decision(ts, "BTC")

        assert result1 == result2

    def test_empty_file(self, jsonl_path):
        """空文件不报错，所有查询返回 None"""
        jsonl_path.write_text("")
        replayer = DecisionReplayer(jsonl_path)
        assert replayer.total_decisions == 0
        result = replayer.get_decision(datetime(2024, 1, 1), "BTC")
        assert result is None

    def test_symbol_filtering(self, jsonl_path):
        """不同 symbol 的决策互不干扰"""
        _create_jsonl(
            jsonl_path,
            [
                {
                    "schema_version": 1,
                    "timestamp": "2024-01-01T00:00:00",
                    "symbol": "BTC",
                    "decision": "BUY",
                    "details": {},
                },
                {
                    "schema_version": 1,
                    "timestamp": "2024-01-01T00:00:00",
                    "symbol": "ETH",
                    "decision": "SELL",
                    "details": {},
                },
            ],
        )
        replayer = DecisionReplayer(jsonl_path)
        btc = replayer.get_decision(datetime(2024, 1, 1), "BTC")
        assert btc is not None
        assert btc[0] == "BUY"

        # per-symbol 独立指针，无需 reset 即可查询其他 symbol
        eth = replayer.get_decision(datetime(2024, 1, 1), "ETH")
        assert eth is not None
        assert eth[0] == "SELL"

    def test_multi_symbol_interleaved(self, jsonl_path):
        """多交易对交织排列时，各 symbol 的决策互不丢失"""
        _create_jsonl(
            jsonl_path,
            [
                {"schema_version": 1, "timestamp": "2024-01-01T00:00:00", "symbol": "BTC", "decision": "BUY", "details": {}},
                {"schema_version": 1, "timestamp": "2024-01-01T00:05:00", "symbol": "ETH", "decision": "SELL", "details": {}},
                {"schema_version": 1, "timestamp": "2024-01-01T00:10:00", "symbol": "BTC", "decision": "SELL_SHORT", "details": {}},
                {"schema_version": 1, "timestamp": "2024-01-01T00:15:00", "symbol": "ETH", "decision": "BUY", "details": {}},
            ],
        )
        replayer = DecisionReplayer(jsonl_path)

        # 先查 BTC 第 1 条
        r1 = replayer.get_decision(datetime(2024, 1, 1, 0, 0, 0), "BTC")
        assert r1 is not None and r1[0] == "BUY"

        # 查 ETH 第 1 条（BTC 指针推进不影响 ETH）
        r2 = replayer.get_decision(datetime(2024, 1, 1, 0, 5, 0), "ETH")
        assert r2 is not None and r2[0] == "SELL"

        # 继续查 BTC 第 2 条
        r3 = replayer.get_decision(datetime(2024, 1, 1, 0, 10, 0), "BTC")
        assert r3 is not None and r3[0] == "SELL_SHORT"

        # 继续查 ETH 第 2 条
        r4 = replayer.get_decision(datetime(2024, 1, 1, 0, 15, 0), "ETH")
        assert r4 is not None and r4[0] == "BUY"
