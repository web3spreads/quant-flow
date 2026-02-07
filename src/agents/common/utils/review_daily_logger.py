"""
复盘经验每日日志记录器

按日期存储复盘经验，便于后续 LoRA 训练使用。
每条记录以 JSONL 格式存储，包含完整的输入输出对，
可直接转换为 Alpaca/ShareGPT 等训练格式。
"""

import json
import os
import platform
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 跨平台文件锁支持：Unix 使用 fcntl，Windows 仅使用线程锁
_IS_WINDOWS = platform.system() == "Windows"
if not _IS_WINDOWS:
    import fcntl


class ReviewDailyLogger:
    """
    复盘经验每日日志记录器

    数据格式设计（便于 LoRA 训练）：
    - instruction: 系统指令（复盘任务描述）
    - input: 市场环境和决策历史
    - output: LLM 生成的复盘经验
    - metadata: 元数据（时间、交易对、统计信息等）

    文件结构：
    logs/review_daily/
    ├── 2025-12-15.jsonl
    ├── 2025-12-16.jsonl
    └── ...
    """

    # 支持的导出格式
    SUPPORTED_FORMATS = ("alpaca", "sharegpt", "raw")

    def __init__(
        self,
        base_dir: str = "logs/review_daily",
        date_format: str = "%Y-%m-%d",
        logger: Any | None = None,
    ):
        """
        初始化日志记录器

        Args:
            base_dir: 日志存储基础目录
            date_format: 日期格式，用于生成文件名
            logger: 可选的日志记录器，用于输出警告信息
        """
        self.base_dir = Path(base_dir)
        self.date_format = date_format
        self._lock = threading.Lock()
        self._logger = logger

        # 确保目录存在
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _log_warning(self, message: str) -> None:
        """输出警告信息"""
        if self._logger and hasattr(self._logger, "print_warning"):
            self._logger.print_warning(message)
        else:
            print(f"[ReviewDailyLogger] {message}")

    def _get_daily_file_path(self, date: datetime | None = None) -> Path:
        """获取指定日期的日志文件路径"""
        if date is None:
            date = datetime.now()
        filename = f"{date.strftime(self.date_format)}.jsonl"
        return self.base_dir / filename

    def log_review(
        self,
        symbol: str,
        prompt: str,
        raw_output: str,
        lessons: list[dict[str, Any]],
        summary: str,
        context_features: dict[str, Any],
        decision_digest: list[dict[str, Any]],
        stats: dict[str, Any],
        fills_summary: dict[str, Any] | None = None,
        existing_lessons: list[dict[str, Any]] | None = None,
        spot_checks: list[dict[str, Any]] | None = None,
    ) -> bool:
        """
        记录一次完整的复盘经验

        Args:
            symbol: 交易对
            prompt: 发送给 LLM 的完整 prompt（作为训练的 input）
            raw_output: LLM 的原始输出（作为训练的 output）
            lessons: 解析后的经验列表
            summary: 复盘摘要
            context_features: 当前市场环境特征
            decision_digest: 压缩后的决策历史
            stats: 统计信息
            fills_summary: 成交摘要（可选）
            existing_lessons: 历史经验（可选）
            spot_checks: 抽查点（可选）

        Returns:
            是否成功写入
        """
        timestamp = datetime.now()

        # 构建训练数据格式
        record = {
            # ===== 训练核心字段 =====
            # instruction: 任务描述（可用于构建 system prompt）
            "instruction": self._build_instruction(symbol),
            # input: 完整的 prompt（包含市场数据、历史决策等）
            "input": prompt,
            # output: LLM 的原始输出
            "output": raw_output,
            # ===== 结构化数据（便于筛选和分析）=====
            "parsed_output": {
                "lessons": lessons,
                "summary": summary,
                "spot_checks": spot_checks or [],
            },
            # ===== 决策历史（用于训练上下文理解）=====
            "decision_digest": decision_digest,
            # ===== 环境特征（便于按相似场景筛选训练数据）=====
            "context_features": context_features,
            # ===== 元数据 =====
            "metadata": {
                "timestamp": timestamp.isoformat(),
                "symbol": symbol,
                "stats": stats,
                "fills_summary": fills_summary or {},
                "decision_count": len(decision_digest),
                "lesson_count": len(lessons),
                "existing_lesson_count": len(existing_lessons) if existing_lessons else 0,
            },
        }

        return self._write_record(record, timestamp)

    def _build_instruction(self, symbol: str) -> str:
        """
        构建训练用的 instruction

        这个 instruction 描述了复盘任务，可用于：
        1. 构建训练数据的 system prompt
        2. 作为 Alpaca 格式的 instruction 字段
        """
        return (
            f"你是一个专业的加密货币量化交易复盘专家。"
            f"请分析 {symbol} 的近期交易决策记录，"
            f"提取可复用的交易经验规则，包括：\n"
            f"1. 识别成功和失败的交易模式\n"
            f"2. 总结有效的入场/出场时机判断\n"
            f"3. 评估风险控制的执行效果\n"
            f"4. 提炼可用于未来决策的具体规则\n"
            f"请以 JSON 格式输出，包含 summary、lessons 和 spot_checks 字段。"
        )

    def _write_record(self, record: dict[str, Any], timestamp: datetime) -> bool:
        """
        写入单条记录到日志文件

        使用线程锁 + 文件锁（Unix）确保并发安全
        Windows 系统仅使用线程锁
        """
        file_path = self._get_daily_file_path(timestamp)

        try:
            with self._lock:  # noqa: SIM117 - nested with needed for platform-specific file lock
                # 使用追加模式写入
                with open(file_path, "a", encoding="utf-8") as f:
                    # Unix 系统使用文件锁
                    if not _IS_WINDOWS:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        # 写入 JSON 行
                        json_line = json.dumps(record, ensure_ascii=False)
                        f.write(json_line + "\n")
                        # 确保数据写入磁盘（对训练数据尤为重要）
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        if not _IS_WINDOWS:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return True
        except Exception as e:
            # 记录失败不应影响主流程
            self._log_warning(f"写入日志文件失败 {file_path}: {e}")
            return False

    def read_daily_records(self, date: datetime | None = None) -> list[dict[str, Any]]:
        """
        读取指定日期的所有记录

        Args:
            date: 日期，默认为今天

        Returns:
            记录列表
        """
        file_path = self._get_daily_file_path(date)

        if not file_path.exists():
            return []

        records = []
        try:
            with open(file_path, encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            self._log_warning(f"日志文件 {file_path} 第 {line_num} 行格式错误: {e}")
                            continue
        except Exception as e:
            self._log_warning(f"读取日志文件失败 {file_path}: {e}")

        return records

    def read_date_range(
        self,
        start_date: datetime,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        读取日期范围内的所有记录

        Args:
            start_date: 开始日期
            end_date: 结束日期，默认为今天

        Returns:
            记录列表
        """
        if end_date is None:
            end_date = datetime.now()

        all_records = []
        current_date = start_date

        # 使用 .date() 比较，避免时间组件导致的问题
        while current_date.date() <= end_date.date():
            records = self.read_daily_records(current_date)
            all_records.extend(records)
            # 移动到下一天
            current_date += timedelta(days=1)

        return all_records

    def export_for_training(
        self,
        output_path: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        format_type: str = "alpaca",
        min_lesson_count: int = 1,
        symbols: list[str] | None = None,
    ) -> int:
        """
        导出训练数据

        Args:
            output_path: 输出文件路径
            start_date: 开始日期
            end_date: 结束日期
            format_type: 输出格式 ("alpaca", "sharegpt", "raw")
            min_lesson_count: 最少经验数量，用于过滤低质量复盘记录。
                默认为 1，即只导出至少有一条经验的记录。
                设置为 0 可包含所有记录（含无经验的"负面样本"，
                这些样本可能对训练模型识别无效场景有价值）。
            symbols: 筛选特定交易对

        Returns:
            导出的记录数量

        Raises:
            ValueError: 当 format_type 不是支持的格式时
        """
        # 验证 format_type
        if format_type not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"不支持的导出格式: {format_type}，支持的格式: {self.SUPPORTED_FORMATS}"
            )

        # 获取所有日志文件
        if start_date is None:
            # 读取所有文件
            records = []
            for file_path in sorted(self.base_dir.glob("*.jsonl")):
                try:
                    file_date = datetime.strptime(file_path.stem, self.date_format)
                except ValueError:
                    # 跳过文件名格式不匹配的文件
                    continue
                daily_records = self.read_daily_records(file_date)
                records.extend(daily_records)
        else:
            records = self.read_date_range(start_date, end_date)

        # 筛选
        filtered_records = []
        for record in records:
            # 检查经验数量
            lesson_count = record.get("metadata", {}).get("lesson_count", 0)
            if lesson_count < min_lesson_count:
                continue

            # 检查交易对
            if symbols:
                symbol = record.get("metadata", {}).get("symbol", "")
                if symbol not in symbols:
                    continue

            filtered_records.append(record)

        # 转换格式
        training_data = []
        for record in filtered_records:
            if format_type == "alpaca":
                training_data.append(
                    {
                        "instruction": record.get("instruction", ""),
                        "input": record.get("input", ""),
                        "output": record.get("output", ""),
                    }
                )
            elif format_type == "sharegpt":
                training_data.append(
                    {
                        "conversations": [
                            {
                                "from": "system",
                                "value": record.get("instruction", ""),
                            },
                            {
                                "from": "human",
                                "value": record.get("input", ""),
                            },
                            {
                                "from": "gpt",
                                "value": record.get("output", ""),
                            },
                        ],
                    }
                )
            else:  # raw
                training_data.append(record)

        # 写入输出文件
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(training_data, f, ensure_ascii=False, indent=2)

        return len(training_data)

    def get_statistics(self) -> dict[str, Any]:
        """
        获取日志统计信息

        Returns:
            统计信息字典
        """
        stats = {
            "total_files": 0,
            "total_records": 0,
            "total_lessons": 0,
            "symbols": {},
            "date_range": {"earliest": None, "latest": None},
        }

        for file_path in sorted(self.base_dir.glob("*.jsonl")):
            date_str = file_path.stem
            try:
                file_date = datetime.strptime(date_str, self.date_format)
            except ValueError:
                # 跳过文件名格式不匹配的文件
                continue

            stats["total_files"] += 1

            if stats["date_range"]["earliest"] is None:
                stats["date_range"]["earliest"] = date_str
            stats["date_range"]["latest"] = date_str

            records = self.read_daily_records(file_date)

            for record in records:
                stats["total_records"] += 1
                lesson_count = record.get("metadata", {}).get("lesson_count", 0)
                stats["total_lessons"] += lesson_count

                symbol = record.get("metadata", {}).get("symbol", "unknown")
                if symbol not in stats["symbols"]:
                    stats["symbols"][symbol] = {"records": 0, "lessons": 0}
                stats["symbols"][symbol]["records"] += 1
                stats["symbols"][symbol]["lessons"] += lesson_count

        return stats
