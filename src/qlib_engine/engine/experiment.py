"""
实验管理

记录每次训练和预测的实验信息，支持回溯和对比。
轻量级实现，不依赖 MLflow，使用 JSON 文件持久化。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("QuantFlow.QLib")


class ExperimentRecord:
    """
    单次实验记录

    记录一次完整的训练-评估-预测过程中的所有关键信息。
    """

    def __init__(
        self,
        experiment_id: str,
        experiment_type: str = "train",
    ):
        """
        初始化实验记录

        Args:
            experiment_id: 实验唯一标识
            experiment_type: 实验类型（train/predict/retrain）
        """
        self.experiment_id = experiment_id
        self.experiment_type = experiment_type
        self.start_time = datetime.now().isoformat()
        self.end_time: str | None = None
        self.status = "running"

        # 实验参数
        self.params: dict = {}
        # 实验指标
        self.metrics: dict = {}
        # 实验标签
        self.tags: dict = {}
        # 产物路径（模型文件等）
        self.artifacts: list[str] = []

    def log_params(self, params: dict) -> None:
        """记录参数"""
        self.params.update(params)

    def log_metrics(self, metrics: dict) -> None:
        """记录指标"""
        self.metrics.update(metrics)

    def log_tag(self, key: str, value: str) -> None:
        """记录标签"""
        self.tags[key] = value

    def log_artifact(self, path: str) -> None:
        """记录产物路径"""
        self.artifacts.append(path)

    def finish(self, status: str = "completed") -> None:
        """完成实验"""
        self.end_time = datetime.now().isoformat()
        self.status = status

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "experiment_id": self.experiment_id,
            "experiment_type": self.experiment_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "params": self.params,
            "metrics": self.metrics,
            "tags": self.tags,
            "artifacts": self.artifacts,
        }


class ExperimentManager:
    """
    实验管理器

    提供类似 QLib Recorder/Experiment 的实验追踪功能：
    - 记录训练参数和评估指标
    - 对比不同实验的结果
    - 持久化实验记录
    - 查询历史实验
    """

    def __init__(self, experiment_dir: str = "experiments"):
        """
        初始化实验管理器

        Args:
            experiment_dir: 实验记录存储目录
        """
        self.experiment_dir = Path(experiment_dir)
        self.experiment_dir.mkdir(parents=True, exist_ok=True)

        self._records: dict[str, ExperimentRecord] = {}
        self._active_record: ExperimentRecord | None = None

        # 加载已有记录
        self._load_existing()

    def _load_existing(self) -> None:
        """加载已有的实验记录"""
        index_file = self.experiment_dir / "index.json"
        if not index_file.exists():
            return

        try:
            with open(index_file) as f:
                index = json.load(f)
            logger.info(f"加载 {len(index)} 条历史实验记录")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"加载实验索引失败: {e}")

    def start_experiment(
        self,
        experiment_type: str = "train",
        tags: dict | None = None,
    ) -> ExperimentRecord:
        """
        开始一次新实验

        Args:
            experiment_type: 实验类型
            tags: 实验标签

        Returns:
            实验记录对象
        """
        experiment_id = f"{experiment_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        record = ExperimentRecord(experiment_id, experiment_type)

        if tags:
            for k, v in tags.items():
                record.log_tag(k, v)

        self._records[experiment_id] = record
        self._active_record = record

        logger.info(f"实验开始: {experiment_id} (类型={experiment_type})")
        return record

    def end_experiment(
        self,
        status: str = "completed",
        record: ExperimentRecord | None = None,
    ) -> None:
        """
        结束实验并保存

        Args:
            status: 实验状态
            record: 要结束的实验记录（默认当前活跃实验）
        """
        target = record or self._active_record
        if target is None:
            logger.warning("没有活跃的实验记录")
            return

        target.finish(status)
        self._save_record(target)

        if target == self._active_record:
            self._active_record = None

        logger.info(
            f"实验结束: {target.experiment_id} "
            f"(状态={status}, 指标数={len(target.metrics)})"
        )

    def _save_record(self, record: ExperimentRecord) -> None:
        """
        保存单条实验记录

        Args:
            record: 实验记录
        """
        # 保存单条记录
        record_file = self.experiment_dir / f"{record.experiment_id}.json"
        with open(record_file, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)

        # 更新索引
        self._update_index()

    def _update_index(self) -> None:
        """更新实验索引文件"""
        index = []
        for eid, record in self._records.items():
            index.append({
                "experiment_id": eid,
                "experiment_type": record.experiment_type,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "status": record.status,
            })

        index_file = self.experiment_dir / "index.json"
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        """
        获取指定实验记录

        Args:
            experiment_id: 实验 ID

        Returns:
            实验记录或 None
        """
        if experiment_id in self._records:
            return self._records[experiment_id]

        # 尝试从文件加载
        record_file = self.experiment_dir / f"{experiment_id}.json"
        if record_file.exists():
            try:
                with open(record_file, encoding="utf-8") as f:
                    data = json.load(f)
                record = ExperimentRecord(data["experiment_id"], data["experiment_type"])
                record.start_time = data["start_time"]
                record.end_time = data.get("end_time")
                record.status = data.get("status", "unknown")
                record.params = data.get("params", {})
                record.metrics = data.get("metrics", {})
                record.tags = data.get("tags", {})
                record.artifacts = data.get("artifacts", [])
                self._records[experiment_id] = record
                return record
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"加载实验记录失败: {e}")
                return None

        return None

    def list_experiments(
        self,
        experiment_type: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        列出实验记录

        Args:
            experiment_type: 过滤实验类型
            status: 过滤状态
            limit: 最大返回数量

        Returns:
            实验摘要列表
        """
        results = []
        for record in self._records.values():
            if experiment_type and record.experiment_type != experiment_type:
                continue
            if status and record.status != status:
                continue
            results.append({
                "experiment_id": record.experiment_id,
                "type": record.experiment_type,
                "start_time": record.start_time,
                "status": record.status,
                "metrics_summary": {
                    k: round(v, 4) if isinstance(v, float) else v
                    for k, v in list(record.metrics.items())[:5]
                },
            })

        # 按开始时间倒序
        results.sort(key=lambda x: x["start_time"], reverse=True)
        return results[:limit]

    def compare_experiments(
        self,
        experiment_ids: list[str],
        metrics: list[str] | None = None,
    ) -> dict:
        """
        对比多个实验

        Args:
            experiment_ids: 要对比的实验 ID 列表
            metrics: 要对比的指标列表（None 表示全部）

        Returns:
            对比结果
        """
        comparison = {}
        for eid in experiment_ids:
            record = self.get_experiment(eid)
            if record is None:
                logger.warning(f"实验 {eid} 不存在")
                continue

            if metrics:
                comparison[eid] = {k: record.metrics.get(k) for k in metrics}
            else:
                comparison[eid] = record.metrics.copy()

        return comparison

    def get_best_experiment(
        self,
        metric: str = "ICIR",
        experiment_type: str = "train",
    ) -> ExperimentRecord | None:
        """
        获取指定指标最优的实验

        Args:
            metric: 评选指标
            experiment_type: 实验类型

        Returns:
            最优实验记录
        """
        best_record = None
        best_value = float("-inf")

        for record in self._records.values():
            if record.experiment_type != experiment_type:
                continue
            if record.status != "completed":
                continue

            value = record.metrics.get(metric, float("-inf"))
            if isinstance(value, (int, float)) and value > best_value:
                best_value = value
                best_record = record

        return best_record

    @property
    def active_experiment(self) -> ExperimentRecord | None:
        """当前活跃的实验"""
        return self._active_record
