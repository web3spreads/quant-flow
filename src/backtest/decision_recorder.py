"""
决策录制器
将回测过程中的 LLM 决策逐条序列化为 JSONL 文件，用于后续确定性回放。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# 录制时保留的决策 details 核心字段
_DETAIL_KEYS = (
    "output",
    "leverage",
    "sl_pct",
    "tp_pct",
    "confidence",
    "entry_price",
    "size",
    "amount",
    "action",
    "fill_price",
    "price",
)


class DecisionRecorder:
    """将回测决策逐条追加写入 JSONL 文件"""

    def __init__(self, output_path: str | Path):
        self._path = Path(output_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "w", encoding="utf-8")  # noqa: SIM115
        self._count = 0

    def record(
        self,
        timestamp: datetime | str,
        symbol: str,
        decision: str,
        details: dict[str, Any],
    ) -> None:
        """
        记录一条决策

        Args:
            timestamp: 决策时间戳
            symbol: 交易对符号
            decision: 决策类型（BUY/SELL/SELL_SHORT/BUY_TO_COVER/DO_NOTHING）
            details: Agent 返回的详情字典
        """
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()
        record = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": timestamp,
            "symbol": symbol,
            "decision": decision,
            "details": self._serialize_details(details),
        }
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._file.flush()
        self._count += 1

    @property
    def count(self) -> int:
        """已录制的决策数量"""
        return self._count

    def close(self) -> None:
        """关闭文件"""
        if self._file and not self._file.closed:
            self._file.close()

    @staticmethod
    def _serialize_details(details: dict[str, Any]) -> dict[str, Any]:
        """提取决策核心字段，丢弃不可序列化的大对象"""
        return {k: details[k] for k in _DETAIL_KEYS if k in details}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
