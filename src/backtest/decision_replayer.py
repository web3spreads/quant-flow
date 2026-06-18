"""
决策回放器
从 JSONL 文件加载预录制的 LLM 决策，按时间戳顺序匹配，实现确定性回测。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class DecisionReplayer:
    """从 JSONL 文件加载预录制决策，按时间戳顺序查找"""

    def __init__(self, replay_path: str | Path, tolerance_seconds: int = 900):
        """
        初始化回放器

        Args:
            replay_path: JSONL 文件路径
            tolerance_seconds: 时间戳匹配容忍度（默认 900 秒 = 1 根 15m K 线）
        """
        self._tolerance = timedelta(seconds=tolerance_seconds)
        self._decisions: list[dict[str, Any]] = []
        self._indices: dict[str, int] = {}  # 每个 symbol 独立的顺序读取指针

        with open(replay_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                ts = record.get("timestamp", "")
                if isinstance(ts, str) and ts:
                    record["_ts"] = datetime.fromisoformat(ts)
                self._decisions.append(record)

        # 按时间戳排序，确保顺序指针查找的正确性
        self._decisions.sort(key=lambda x: x.get("_ts", datetime.min))

    @property
    def total_decisions(self) -> int:
        """预录制的决策总数"""
        return len(self._decisions)

    def get_decision(self, timestamp: datetime, symbol: str) -> tuple[str, dict[str, Any]] | None:
        """
        查找与给定时间戳最接近的决策记录

        利用时间单调递增特性，使用顺序指针实现 O(1) 摊还查找。

        Args:
            timestamp: 查询时间戳
            symbol: 交易对符号

        Returns:
            (decision_str, details_dict) 或 None（无匹配）
        """
        symbol_index = self._indices.get(symbol, 0)
        best_match = None
        best_diff = None
        best_idx = symbol_index

        for i in range(symbol_index, len(self._decisions)):
            record = self._decisions[i]
            if record.get("symbol") != symbol:
                continue

            record_ts = record.get("_ts")
            if record_ts is None:
                continue

            diff = abs(record_ts - timestamp)
            if diff <= self._tolerance:
                if best_diff is None or diff < best_diff:
                    best_match = record
                    best_diff = diff
                    best_idx = i + 1
            elif record_ts > timestamp + self._tolerance:
                # 超过容忍度，后续记录时间更大，停止搜索
                break

        if best_match:
            self._indices[symbol] = best_idx
            return best_match["decision"], best_match.get("details", {})
        return None

    def reset(self) -> None:
        """重置读取指针到开头（用于多次回放）"""
        self._indices.clear()
