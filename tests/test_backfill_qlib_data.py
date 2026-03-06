"""
QLib 历史数据回填脚本单元测试

覆盖范围：
- 常量和配置验证
- fetch_candles_batch: 分批拉取、重试、API 调用间隔
- merge_and_save: 新建文件、增量合并、去重、dry-run
- parse_args: 命令行参数解析
- main: 端到端流程（mock API）
"""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backfill_qlib_data import (
    FREQ_MINUTES,
    MAX_CANDLES_PER_REQUEST,
    fetch_candles_batch,
    merge_and_save,
    parse_args,
)

# ============================================================
# 公共 Fixtures
# ============================================================


@pytest.fixture
def tmp_data_dir():
    """临时数据目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_candles():
    """模拟 Hyperliquid API 返回的 K 线数据"""
    base_ts = int(datetime(2026, 1, 1).timestamp() * 1000)
    return [
        {
            "t": base_ts + i * 3600_000,
            "o": str(70000 + i * 10),
            "h": str(70100 + i * 10),
            "l": str(69900 + i * 10),
            "c": str(70050 + i * 10),
            "v": str(100 + i),
        }
        for i in range(10)
    ]


@pytest.fixture
def sample_df():
    """用于 merge_and_save 的标准 DataFrame"""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=10, freq="1h"),
            "open": [70000 + i * 10 for i in range(10)],
            "high": [70100 + i * 10 for i in range(10)],
            "low": [69900 + i * 10 for i in range(10)],
            "close": [70050 + i * 10 for i in range(10)],
            "volume": [100 + i for i in range(10)],
        }
    )


# ============================================================
# 常量验证
# ============================================================


class TestConstants:
    """常量和配置验证"""

    def test_freq_minutes_mapping(self):
        """验证频率到分钟数的映射"""
        assert FREQ_MINUTES["1m"] == 1
        assert FREQ_MINUTES["5m"] == 5
        assert FREQ_MINUTES["15m"] == 15
        assert FREQ_MINUTES["1h"] == 60
        assert FREQ_MINUTES["4h"] == 240
        assert FREQ_MINUTES["1d"] == 1440

    def test_max_candles_per_request(self):
        """验证单次请求上限"""
        assert MAX_CANDLES_PER_REQUEST == 500


# ============================================================
# fetch_candles_batch 测试
# ============================================================


class TestFetchCandlesBatch:
    """分批获取 K 线数据测试"""

    def test_single_batch(self, sample_candles):
        """时间范围小于单批容量时只发一次请求"""
        mock_info = MagicMock()
        mock_info.candles_snapshot.return_value = sample_candles

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        end_ms = int(datetime(2026, 1, 1, 10).timestamp() * 1000)  # 10 小时

        df = fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, 0.0)

        assert mock_info.candles_snapshot.call_count == 1
        assert len(df) == 10
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
        assert "datetime64" in str(df["timestamp"].dtype)

    def test_multiple_batches(self):
        """时间范围超过单批容量时分多次请求"""
        mock_info = MagicMock()
        # 每批返回 5 条
        batch1 = [
            {
                "t": int(datetime(2026, 1, 1, i).timestamp() * 1000),
                "o": "70000",
                "h": "70100",
                "l": "69900",
                "c": "70050",
                "v": "100",
            }
            for i in range(5)
        ]
        batch2 = [
            {
                "t": int(datetime(2026, 1, 22, i).timestamp() * 1000),
                "o": "71000",
                "h": "71100",
                "l": "70900",
                "c": "71050",
                "v": "200",
            }
            for i in range(5)
        ]
        mock_info.candles_snapshot.side_effect = [batch1, batch2]

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        # 500 * 60min = 500h ≈ 20.8 天，取 21 天 + 余量确保需要 2 批
        end_ms = int(datetime(2026, 1, 22).timestamp() * 1000)

        df = fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, 0.0)

        assert mock_info.candles_snapshot.call_count == 2
        assert len(df) == 10

    def test_api_retry_on_failure(self, sample_candles):
        """首次请求失败后重试成功"""
        mock_info = MagicMock()
        mock_info.candles_snapshot.side_effect = [
            Exception("连接超时"),
            sample_candles,
        ]

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        end_ms = int(datetime(2026, 1, 1, 10).timestamp() * 1000)

        df = fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, 0.0)

        assert mock_info.candles_snapshot.call_count == 2
        assert len(df) == 10

    def test_api_both_attempts_fail(self):
        """两次请求都失败时返回空 DataFrame"""
        mock_info = MagicMock()
        mock_info.candles_snapshot.side_effect = Exception("API 不可用")

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        end_ms = int(datetime(2026, 1, 1, 5).timestamp() * 1000)

        df = fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, 0.0)

        assert df.empty
        # 首次 + 重试 = 2 次
        assert mock_info.candles_snapshot.call_count == 2

    def test_empty_response(self):
        """API 返回空列表"""
        mock_info = MagicMock()
        mock_info.candles_snapshot.return_value = []

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        end_ms = int(datetime(2026, 1, 1, 5).timestamp() * 1000)

        df = fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, 0.0)

        assert df.empty

    def test_deduplication(self):
        """返回数据中有重复 timestamp 时自动去重"""
        base_ts = int(datetime(2026, 1, 1).timestamp() * 1000)
        candles_with_dup = [
            {"t": base_ts, "o": "70000", "h": "70100", "l": "69900", "c": "70050", "v": "100"},
            {
                "t": base_ts,
                "o": "70001",
                "h": "70101",
                "l": "69901",
                "c": "70051",
                "v": "101",
            },  # 重复
            {
                "t": base_ts + 3600_000,
                "o": "70010",
                "h": "70110",
                "l": "69910",
                "c": "70060",
                "v": "110",
            },
        ]
        mock_info = MagicMock()
        mock_info.candles_snapshot.return_value = candles_with_dup

        start_ms = base_ts
        end_ms = base_ts + 2 * 3600_000

        df = fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, 0.0)

        assert len(df) == 2
        # 保留最后一条（keep="last"）
        assert df.iloc[0]["open"] == 70001.0

    def test_data_type_conversion(self, sample_candles):
        """验证数值列被正确转换为 float"""
        mock_info = MagicMock()
        mock_info.candles_snapshot.return_value = sample_candles

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        end_ms = int(datetime(2026, 1, 1, 10).timestamp() * 1000)

        df = fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, 0.0)

        for col in ["open", "high", "low", "close", "volume"]:
            assert df[col].dtype in ("float64", "int64"), f"{col} 类型应为数值"

    def test_request_interval_between_batches(self):
        """验证批次间存在 API 调用间隔"""
        mock_info = MagicMock()
        batch = [
            {
                "t": int(datetime(2026, 1, 1, i).timestamp() * 1000),
                "o": "70000",
                "h": "70100",
                "l": "69900",
                "c": "70050",
                "v": "100",
            }
            for i in range(3)
        ]
        mock_info.candles_snapshot.return_value = batch

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        # 强制分 2 批：500h ≈ 20.8 天
        end_ms = int(datetime(2026, 1, 22).timestamp() * 1000)

        interval = 0.3  # 使用较短间隔加速测试

        with patch("backfill_qlib_data.time.sleep") as mock_sleep:
            fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, interval)

            # 至少调用了一次 sleep(interval) 作为批次间间隔
            sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
            assert interval in sleep_calls, f"批次间应有 {interval}s 间隔，实际调用: {sleep_calls}"

    def test_retry_interval_is_tripled(self):
        """验证重试时等待时间为 3 倍 request_interval"""
        mock_info = MagicMock()
        mock_info.candles_snapshot.side_effect = [
            Exception("超时"),
            [],  # 重试成功但无数据
        ]

        start_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
        end_ms = int(datetime(2026, 1, 1, 5).timestamp() * 1000)
        interval = 0.5

        with patch("backfill_qlib_data.time.sleep") as mock_sleep:
            fetch_candles_batch(mock_info, "BTC", "1h", start_ms, end_ms, interval)

            sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
            assert interval * 3 in sleep_calls, f"重试间隔应为 {interval * 3}s，实际: {sleep_calls}"


# ============================================================
# merge_and_save 测试
# ============================================================


class TestMergeAndSave:
    """数据合并保存测试"""

    def test_create_new_file(self, sample_df, tmp_data_dir):
        """本地无数据时创建新文件"""
        total = merge_and_save(sample_df, "BTC", "1h", tmp_data_dir)

        assert total == 10
        assert (tmp_data_dir / "BTC_1h.parquet").exists()

        saved = pd.read_parquet(tmp_data_dir / "BTC_1h.parquet")
        assert len(saved) == 10

    def test_incremental_merge(self, sample_df, tmp_data_dir):
        """增量合并新旧数据"""
        # 先写入初始数据
        merge_and_save(sample_df, "BTC", "1h", tmp_data_dir)

        # 新数据，部分重叠
        new_df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01 05:00", periods=10, freq="1h"),
                "open": [71000 + i for i in range(10)],
                "high": [71100 + i for i in range(10)],
                "low": [70900 + i for i in range(10)],
                "close": [71050 + i for i in range(10)],
                "volume": [200 + i for i in range(10)],
            }
        )
        total = merge_and_save(new_df, "BTC", "1h", tmp_data_dir)

        # 00:00~09:00 (10条) + 05:00~14:00 (10条) 重叠 5 条 -> 15 条
        assert total == 15

    def test_deduplication_keeps_last(self, tmp_data_dir):
        """去重时保留最新记录（keep='last'）"""
        old_df = pd.DataFrame(
            {
                "timestamp": [datetime(2026, 1, 1, 0)],
                "open": [70000.0],
                "high": [70100.0],
                "low": [69900.0],
                "close": [70050.0],
                "volume": [100.0],
            }
        )
        merge_and_save(old_df, "BTC", "1h", tmp_data_dir)

        # 同一时间戳，不同价格
        new_df = pd.DataFrame(
            {
                "timestamp": [datetime(2026, 1, 1, 0)],
                "open": [99999.0],
                "high": [99999.0],
                "low": [99999.0],
                "close": [99999.0],
                "volume": [999.0],
            }
        )
        merge_and_save(new_df, "BTC", "1h", tmp_data_dir)

        saved = pd.read_parquet(tmp_data_dir / "BTC_1h.parquet")
        assert len(saved) == 1
        assert saved.iloc[0]["close"] == 99999.0

    def test_dry_run_no_write(self, sample_df, tmp_data_dir):
        """dry-run 模式不写入文件"""
        merge_and_save(sample_df, "BTC", "1h", tmp_data_dir, dry_run=True)

        assert not (tmp_data_dir / "BTC_1h.parquet").exists()

    def test_dry_run_no_modify(self, sample_df, tmp_data_dir):
        """dry-run 模式不修改已有文件"""
        merge_and_save(sample_df, "BTC", "1h", tmp_data_dir)

        import os

        mtime_before = os.path.getmtime(tmp_data_dir / "BTC_1h.parquet")

        new_df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-02-01", periods=5, freq="1h"),
                "open": range(5),
                "high": range(5),
                "low": range(5),
                "close": range(5),
                "volume": range(5),
            }
        )
        merge_and_save(new_df, "BTC", "1h", tmp_data_dir, dry_run=True)

        mtime_after = os.path.getmtime(tmp_data_dir / "BTC_1h.parquet")
        assert mtime_before == mtime_after

    def test_creates_directory(self, sample_df, tmp_data_dir):
        """数据目录不存在时自动创建"""
        nested_dir = tmp_data_dir / "deep" / "nested" / "dir"
        merge_and_save(sample_df, "ETH", "4h", nested_dir)

        assert (nested_dir / "ETH_4h.parquet").exists()

    def test_sorted_output(self, tmp_data_dir):
        """合并后数据按 timestamp 排序"""
        # 故意乱序
        df = pd.DataFrame(
            {
                "timestamp": [
                    datetime(2026, 1, 1, 5),
                    datetime(2026, 1, 1, 1),
                    datetime(2026, 1, 1, 3),
                ],
                "open": [1, 2, 3],
                "high": [1, 2, 3],
                "low": [1, 2, 3],
                "close": [1, 2, 3],
                "volume": [1, 2, 3],
            }
        )
        merge_and_save(df, "SOL", "1h", tmp_data_dir)

        saved = pd.read_parquet(tmp_data_dir / "SOL_1h.parquet")
        timestamps = saved["timestamp"].tolist()
        assert timestamps == sorted(timestamps)

    def test_corrupted_local_file(self, sample_df, tmp_data_dir):
        """本地文件损坏时仍能正常写入新数据"""
        bad_file = tmp_data_dir / "BTC_1h.parquet"
        bad_file.write_text("这不是 parquet 文件")

        total = merge_and_save(sample_df, "BTC", "1h", tmp_data_dir)

        assert total == 10


# ============================================================
# parse_args 测试
# ============================================================


class TestParseArgs:
    """命令行参数解析测试"""

    def test_defaults(self):
        """默认参数值"""
        with patch("sys.argv", ["backfill_qlib_data.py"]):
            args = parse_args()
            assert args.symbols == ["BTC", "ETH", "SOL"]
            assert args.freq == "1h"
            assert args.days == 90
            assert args.start_date is None
            assert args.end_date is None
            assert args.data_dir == "data/qlib"
            assert args.request_interval == 2.0
            assert args.testnet is False
            assert args.dry_run is False

    def test_custom_symbols(self):
        """自定义交易对"""
        with patch("sys.argv", ["backfill_qlib_data.py", "--symbols", "BTC", "ETH"]):
            args = parse_args()
            assert args.symbols == ["BTC", "ETH"]

    def test_date_range(self):
        """指定日期范围"""
        with patch(
            "sys.argv",
            ["backfill_qlib_data.py", "--start-date", "2025-06-01", "--end-date", "2025-12-31"],
        ):
            args = parse_args()
            assert args.start_date == "2025-06-01"
            assert args.end_date == "2025-12-31"

    def test_dry_run_flag(self):
        """dry-run 标志"""
        with patch("sys.argv", ["backfill_qlib_data.py", "--dry-run"]):
            args = parse_args()
            assert args.dry_run is True

    def test_testnet_flag(self):
        """测试网标志"""
        with patch("sys.argv", ["backfill_qlib_data.py", "--testnet"]):
            args = parse_args()
            assert args.testnet is True


# ============================================================
# main 端到端测试
# ============================================================


class TestMainFlow:
    """主函数端到端测试（mock API）"""

    def test_end_to_end_single_symbol(self, tmp_data_dir):
        """单交易对端到端流程"""
        base_ts = int(datetime(2026, 3, 5).timestamp() * 1000)
        mock_candles = [
            {
                "t": base_ts + i * 3600_000,
                "o": str(70000 + i),
                "h": str(70100 + i),
                "l": str(69900 + i),
                "c": str(70050 + i),
                "v": str(100 + i),
            }
            for i in range(24)
        ]

        with (
            patch(
                "sys.argv",
                [
                    "backfill_qlib_data.py",
                    "--symbols",
                    "BTC",
                    "--days",
                    "1",
                    "--data-dir",
                    str(tmp_data_dir),
                    "--request-interval",
                    "0",
                ],
            ),
            patch("backfill_qlib_data.Info") as MockInfo,
            patch("backfill_qlib_data.time.sleep"),
        ):
            mock_instance = MockInfo.return_value
            mock_instance.candles_snapshot.return_value = mock_candles

            from backfill_qlib_data import main

            main()

            assert (tmp_data_dir / "BTC_1h.parquet").exists()
            saved = pd.read_parquet(tmp_data_dir / "BTC_1h.parquet")
            assert len(saved) == 24

    def test_end_to_end_multiple_symbols(self, tmp_data_dir):
        """多交易对流程"""
        base_ts = int(datetime(2026, 3, 5).timestamp() * 1000)

        def make_candles(price_base):
            return [
                {
                    "t": base_ts + i * 3600_000,
                    "o": str(price_base + i),
                    "h": str(price_base + 100 + i),
                    "l": str(price_base - 100 + i),
                    "c": str(price_base + 50 + i),
                    "v": str(100 + i),
                }
                for i in range(12)
            ]

        with (
            patch(
                "sys.argv",
                [
                    "backfill_qlib_data.py",
                    "--symbols",
                    "BTC",
                    "ETH",
                    "--days",
                    "1",
                    "--data-dir",
                    str(tmp_data_dir),
                    "--request-interval",
                    "0",
                ],
            ),
            patch("backfill_qlib_data.Info") as MockInfo,
            patch("backfill_qlib_data.time.sleep"),
        ):
            mock_instance = MockInfo.return_value
            mock_instance.candles_snapshot.side_effect = [
                make_candles(70000),
                make_candles(3000),
            ]

            from backfill_qlib_data import main

            main()

            assert (tmp_data_dir / "BTC_1h.parquet").exists()
            assert (tmp_data_dir / "ETH_1h.parquet").exists()

    def test_start_date_before_end_date_validation(self):
        """开始日期晚于结束日期时退出"""
        with (
            patch(
                "sys.argv",
                [
                    "backfill_qlib_data.py",
                    "--start-date",
                    "2026-06-01",
                    "--end-date",
                    "2026-01-01",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from backfill_qlib_data import main

            main()

        assert exc_info.value.code == 1

    def test_all_symbols_fail_exits_with_error(self, tmp_data_dir):
        """所有交易对数据获取失败时以非零退出"""
        with (
            patch(
                "sys.argv",
                [
                    "backfill_qlib_data.py",
                    "--symbols",
                    "INVALID",
                    "--days",
                    "1",
                    "--data-dir",
                    str(tmp_data_dir),
                    "--request-interval",
                    "0",
                ],
            ),
            patch("backfill_qlib_data.Info") as MockInfo,
            patch("backfill_qlib_data.time.sleep"),
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_instance = MockInfo.return_value
            mock_instance.candles_snapshot.return_value = []

            from backfill_qlib_data import main

            main()

        assert exc_info.value.code == 1
