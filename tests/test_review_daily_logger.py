"""
ReviewDailyLogger 单元测试

测试每日日志记录器的核心功能：
- 日志写入和读取
- 日期文件管理
- 训练数据导出
"""

import copy

# 动态导入模块，避免 agent/__init__.py 的依赖问题
import importlib.util
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "review_daily_logger", Path(__file__).parent.parent / "src" / "agent" / "review_daily_logger.py"
)
review_daily_logger_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_daily_logger_module)
ReviewDailyLogger = review_daily_logger_module.ReviewDailyLogger


class TestReviewDailyLogger:
    """ReviewDailyLogger 测试类"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # 测试后清理
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def logger(self, temp_dir):
        """创建日志记录器实例"""
        return ReviewDailyLogger(base_dir=temp_dir)

    @pytest.fixture
    def sample_review_data(self):
        """样本复盘数据"""
        return {
            "symbol": "BTC",
            "prompt": "请分析以下交易决策记录...",
            "raw_output": '{"summary": "测试摘要", "lessons": [{"rule": "测试规则", "action": "测试动作", "confidence": 0.8}], "spot_checks": []}',
            "lessons": [
                {
                    "rule": "当 RSI 超过 70 时考虑卖出",
                    "action": "设置止盈位",
                    "confidence": 0.85,
                    "conditions": ["RSI > 70", "价格突破布林带上轨"],
                    "evidence": ["历史数据显示此策略成功率高"],
                }
            ],
            "summary": "本次复盘总结了 5 次交易决策，提取了 1 条经验规则。",
            "context_features": {
                "rsi": 65.5,
                "macd_signal": "bullish",
                "trend_direction": "up",
                "volatility_level": "medium",
                "volume_ratio": 1.2,
                "price_position": 0.65,
                "time_of_day": "afternoon",
                "ema_trend": "bullish",
            },
            "decision_digest": [
                {
                    "timestamp": "2025-12-15T10:00:00",
                    "decision": "BUY",
                    "price": 100000.0,
                    "result": "success",
                    "reason": "技术指标看涨",
                }
            ],
            "stats": {
                "total_decisions": 5,
                "buy_count": 2,
                "sell_count": 1,
                "idle_count": 2,
                "min_price": 99000.0,
                "max_price": 101000.0,
                "average_price": 100000.0,
            },
            "fills_summary": {"total_fills": 3, "total_pnl": 150.0},
            "existing_lessons": [],
            "spot_checks": [],
        }

    def test_init_creates_directory(self, temp_dir):
        """测试初始化时创建目录"""
        log_dir = os.path.join(temp_dir, "nested", "log", "dir")
        ReviewDailyLogger(base_dir=log_dir)
        assert os.path.exists(log_dir)

    def test_log_review_creates_file(self, logger, temp_dir, sample_review_data):
        """测试写入日志创建文件"""
        result = logger.log_review(**sample_review_data)
        assert result is True

        # 检查文件是否创建
        today = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(temp_dir, f"{today}.jsonl")
        assert os.path.exists(file_path)

    def test_log_review_content_format(self, logger, temp_dir, sample_review_data):
        """测试日志内容格式正确"""
        logger.log_review(**sample_review_data)

        # 读取并验证内容
        records = logger.read_daily_records()
        assert len(records) == 1

        record = records[0]
        # 检查训练核心字段
        assert "instruction" in record
        assert "input" in record
        assert "output" in record
        assert record["input"] == sample_review_data["prompt"]
        assert record["output"] == sample_review_data["raw_output"]

        # 检查结构化数据
        assert "parsed_output" in record
        assert record["parsed_output"]["lessons"] == sample_review_data["lessons"]
        assert record["parsed_output"]["summary"] == sample_review_data["summary"]

        # 检查元数据
        assert "metadata" in record
        assert record["metadata"]["symbol"] == "BTC"
        assert "timestamp" in record["metadata"]

        # 检查环境特征
        assert "context_features" in record
        assert record["context_features"]["rsi"] == 65.5

        # 检查决策历史（用于训练上下文理解）
        assert "decision_digest" in record
        assert len(record["decision_digest"]) == 1
        assert record["decision_digest"][0]["decision"] == "BUY"

    def test_multiple_records_same_day(self, logger, sample_review_data):
        """测试同一天多条记录"""
        # 写入多条记录
        for i in range(3):
            data = copy.deepcopy(sample_review_data)
            data["symbol"] = f"SYMBOL_{i}"
            logger.log_review(**data)

        # 验证所有记录都被保存
        records = logger.read_daily_records()
        assert len(records) == 3

        symbols = [r["metadata"]["symbol"] for r in records]
        assert "SYMBOL_0" in symbols
        assert "SYMBOL_1" in symbols
        assert "SYMBOL_2" in symbols

    def test_read_nonexistent_date(self, logger):
        """测试读取不存在的日期"""
        future_date = datetime.now() + timedelta(days=100)
        records = logger.read_daily_records(future_date)
        assert records == []

    def test_read_date_range(self, temp_dir, sample_review_data):
        """测试读取日期范围内的记录"""
        logger = ReviewDailyLogger(base_dir=temp_dir)

        # 写入今天的数据
        logger.log_review(**sample_review_data)

        # 测试读取今天的范围
        today = datetime.now()
        records = logger.read_date_range(today, today)
        assert len(records) == 1
        assert records[0]["metadata"]["symbol"] == "BTC"

        # 测试空范围（未来日期）
        future = today + timedelta(days=10)
        future_end = future + timedelta(days=5)
        empty_records = logger.read_date_range(future, future_end)
        assert empty_records == []

        # 测试 end_date 默认为今天
        records_default = logger.read_date_range(today)
        assert len(records_default) == 1

    def test_export_alpaca_format(self, logger, temp_dir, sample_review_data):
        """测试导出 Alpaca 格式"""
        # 写入一些数据
        logger.log_review(**sample_review_data)

        # 导出
        output_path = os.path.join(temp_dir, "training_alpaca.json")
        count = logger.export_for_training(
            output_path=output_path,
            format_type="alpaca",
        )

        assert count == 1
        assert os.path.exists(output_path)

        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1
        assert "instruction" in data[0]
        assert "input" in data[0]
        assert "output" in data[0]

    def test_export_sharegpt_format(self, logger, temp_dir, sample_review_data):
        """测试导出 ShareGPT 格式"""
        logger.log_review(**sample_review_data)

        output_path = os.path.join(temp_dir, "training_sharegpt.json")
        count = logger.export_for_training(
            output_path=output_path,
            format_type="sharegpt",
        )

        assert count == 1

        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1
        assert "conversations" in data[0]
        conversations = data[0]["conversations"]
        assert len(conversations) == 3
        assert conversations[0]["from"] == "system"
        assert conversations[1]["from"] == "human"
        assert conversations[2]["from"] == "gpt"

    def test_export_raw_format(self, logger, temp_dir, sample_review_data):
        """测试导出 raw 格式（完整记录）"""
        logger.log_review(**sample_review_data)

        output_path = os.path.join(temp_dir, "training_raw.json")
        count = logger.export_for_training(
            output_path=output_path,
            format_type="raw",
        )

        assert count == 1

        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1
        # raw 格式应该包含所有字段
        assert "instruction" in data[0]
        assert "input" in data[0]
        assert "output" in data[0]
        assert "parsed_output" in data[0]
        assert "context_features" in data[0]
        assert "metadata" in data[0]
        assert "decision_digest" in data[0]

    def test_export_with_filter(self, logger, temp_dir, sample_review_data):
        """测试带过滤条件的导出"""
        # 写入 BTC 数据（有经验）
        logger.log_review(**sample_review_data)

        # 写入 ETH 数据（无经验）
        eth_data = copy.deepcopy(sample_review_data)
        eth_data["symbol"] = "ETH"
        eth_data["lessons"] = []  # 无经验
        logger.log_review(**eth_data)

        # 按 symbol 过滤
        output_path = os.path.join(temp_dir, "btc_only.json")
        count = logger.export_for_training(
            output_path=output_path,
            format_type="alpaca",
            symbols=["BTC"],
        )
        assert count == 1

        # 按最小经验数过滤
        output_path2 = os.path.join(temp_dir, "with_lessons.json")
        count2 = logger.export_for_training(
            output_path=output_path2,
            format_type="alpaca",
            min_lesson_count=1,
        )
        assert count2 == 1  # 只有 BTC 有经验

    def test_export_invalid_format_raises_error(self, logger, temp_dir, sample_review_data):
        """测试无效的导出格式抛出异常"""
        logger.log_review(**sample_review_data)

        output_path = os.path.join(temp_dir, "invalid.json")
        with pytest.raises(ValueError, match="不支持的导出格式"):
            logger.export_for_training(
                output_path=output_path,
                format_type="invalid_format",
            )

    def test_get_statistics(self, logger, sample_review_data):
        """测试统计信息"""
        # 写入一些数据
        logger.log_review(**sample_review_data)

        eth_data = copy.deepcopy(sample_review_data)
        eth_data["symbol"] = "ETH"
        eth_data["lessons"] = [{"rule": "ETH 规则", "action": "ETH 动作"}]
        logger.log_review(**eth_data)

        stats = logger.get_statistics()

        assert stats["total_files"] == 1
        assert stats["total_records"] == 2
        assert stats["total_lessons"] == 2  # BTC 1条 + ETH 1条
        assert "BTC" in stats["symbols"]
        assert "ETH" in stats["symbols"]
        assert stats["symbols"]["BTC"]["records"] == 1
        assert stats["symbols"]["ETH"]["records"] == 1

    def test_instruction_content(self, logger, sample_review_data):
        """测试 instruction 内容包含交易对信息"""
        logger.log_review(**sample_review_data)
        records = logger.read_daily_records()

        instruction = records[0]["instruction"]
        assert "BTC" in instruction
        assert "复盘" in instruction or "分析" in instruction

    def test_concurrent_writes(self, logger, sample_review_data):
        """测试并发写入"""
        import threading

        results = []

        def write_record(symbol):
            data = copy.deepcopy(sample_review_data)
            data["symbol"] = symbol
            result = logger.log_review(**data)
            results.append(result)

        # 启动多个线程并发写入
        threads = []
        for i in range(5):
            t = threading.Thread(target=write_record, args=(f"SYM_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 验证所有写入成功
        assert all(results)

        # 验证所有记录都被保存
        records = logger.read_daily_records()
        assert len(records) == 5


class TestReviewDailyLoggerEdgeCases:
    """边界情况测试"""

    @pytest.fixture
    def temp_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    def test_empty_lessons(self, temp_dir):
        """测试空经验列表"""
        logger = ReviewDailyLogger(base_dir=temp_dir)
        result = logger.log_review(
            symbol="BTC",
            prompt="测试",
            raw_output="{}",
            lessons=[],
            summary="",
            context_features={},
            decision_digest=[],
            stats={},
        )
        assert result is True

        records = logger.read_daily_records()
        assert len(records) == 1
        assert records[0]["parsed_output"]["lessons"] == []

    def test_unicode_content(self, temp_dir):
        """测试中文和特殊字符"""
        logger = ReviewDailyLogger(base_dir=temp_dir)
        result = logger.log_review(
            symbol="BTC",
            prompt="请分析以下数据：📈📉🔔",
            raw_output='{"summary": "中文摘要：测试emoji👍"}',
            lessons=[{"rule": "当出现🚀信号时买入", "action": "立即买入"}],
            summary="中文摘要",
            context_features={"trend": "看涨🔥"},
            decision_digest=[],
            stats={},
        )
        assert result is True

        records = logger.read_daily_records()
        assert "📈" in records[0]["input"]
        assert "🚀" in records[0]["parsed_output"]["lessons"][0]["rule"]

    def test_large_data(self, temp_dir):
        """测试大数据量"""
        logger = ReviewDailyLogger(base_dir=temp_dir)

        # 生成大量决策历史
        large_digest = [
            {
                "timestamp": f"2025-12-15T{i:02d}:00:00",
                "decision": "BUY" if i % 2 == 0 else "SELL",
                "price": 100000.0 + i * 100,
                "result": "success",
                "reason": "x" * 500,  # 长文本
            }
            for i in range(100)
        ]

        result = logger.log_review(
            symbol="BTC",
            prompt="x" * 10000,  # 非常长的 prompt
            raw_output="y" * 10000,
            lessons=[{"rule": f"规则_{i}", "action": f"动作_{i}"} for i in range(50)],
            summary="z" * 5000,
            context_features={f"feature_{i}": i for i in range(20)},
            decision_digest=large_digest,
            stats={"total": 100},
        )
        assert result is True

        records = logger.read_daily_records()
        assert len(records) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
