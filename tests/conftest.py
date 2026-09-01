"""测试共享夹具：路径设置与轻量测试桩。"""

import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

# 保证从任意工作目录运行 pytest 时都能导入 src 包。
# 必须无条件插到 sys.path 最前：pytest 会把 rootdir 追加在 sys.path 末尾，
# 旧写法的「不存在才插入」守卫因此恒为假而跳过，import src 转而命中
# .venv/lib/*/site-packages/src（本项目为非 editable 安装，uv run 不会
# 每次改动都重新同步）——测试跑的是陈旧副本，改动验证全部失真且无声。
REPO_ROOT = Path(__file__).resolve().parents[1]
if sys.path[:1] != [str(REPO_ROOT)]:
    sys.path.insert(0, str(REPO_ROOT))

from src.llm import LLMError  # noqa: E402
from src.utils.logger import TradingLogger  # noqa: E402

PROMPTS_DIR = REPO_ROOT / "prompts"

# 模块级共享的测试日志器：写入临时目录，避免测试运行污染仓库 logs/
QUIET_LOGGER = TradingLogger(log_dir=tempfile.mkdtemp(prefix="quantflow-test-logs-"))


class FakeLLM:
    """LLM 测试桩：按序返回预置回复，或抛出预置异常。"""

    def __init__(self, replies: list[str] | None = None, error: Exception | None = None):
        self.replies = list(replies or [])
        self.error = error
        self.calls: list[tuple] = []

    def chat(self, system: str, user: str, temperature: float | None = None) -> str:
        self.calls.append((system, user, temperature))
        if self.error is not None:
            raise self.error
        if not self.replies:
            raise LLMError("FakeLLM 无预置回复")
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class FakeOrderManager:
    """订单管理器测试桩：记录调用，返回可配置结果。"""

    def __init__(self, available: float = 1000.0, positions: list | None = None):
        self.available = available
        self.balance_ok = True
        self.positions = positions or []
        self.calls: list[tuple] = []
        self.execute_result = {"success": True, "quantity": 0.5, "fill_price": 100.0}
        self.close_result: dict | None = {"status": "ok", "fill_price": 110.0}

    def get_available_balance_info(self):
        if not self.balance_ok:
            return {"status": "error", "message": "网络错误"}
        return {
            "status": "ok",
            "available": self.available,
            "total": self.available,
            "equity": self.available,
            "unrealized_pnl": 0.0,
            "occupied": 0.0,
        }

    def get_current_positions(self):
        return list(self.positions)

    def check_sufficient_balance(self, amount: float) -> bool:
        return amount <= self.available

    def execute_long(self, symbol, usdt_amount, leverage=None, with_tpsl=True):
        self.calls.append(("execute_long", symbol, usdt_amount, leverage, with_tpsl))
        return self.execute_result

    def execute_short(self, symbol, usdt_amount, leverage=None, with_tpsl=True):
        self.calls.append(("execute_short", symbol, usdt_amount, leverage, with_tpsl))
        return self.execute_result

    def close_position(self, symbol, size=None):
        self.calls.append(("close_position", symbol, size))
        return self.close_result


def make_ohlcv(rows: int = 100, start_price: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    """构造线性上行的合成 OHLCV 数据（足够计算全部指标）。"""
    closes = [start_price + i * step for i in range(rows)]
    df = pd.DataFrame(
        {
            "open": [c - 0.2 for c in closes],
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000.0 + i for i in range(rows)],
        },
        index=pd.date_range("2026-01-01", periods=rows, freq="1h"),
    )
    return df


@pytest.fixture
def test_logger(tmp_path) -> TradingLogger:
    """写入临时目录的日志器（避免污染仓库 logs/）。"""
    return TradingLogger(log_dir=str(tmp_path / "logs"))
