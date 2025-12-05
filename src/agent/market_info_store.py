"""
市场信息存储模块
负责存储和读取外部市场信息，供交易决策 Agent 使用
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum


class RiskSeverity(Enum):
    """风险等级枚举 (1-5)"""
    VERY_LOW = 1    # 极低
    LOW = 2         # 低
    MEDIUM = 3      # 中
    HIGH = 4        # 高
    CRITICAL = 5    # 极高/严重


class MarketInfoStore:
    """
    市场信息存储类
    负责管理外部市场信息的存储和读取
    """

    def __init__(self, base_dir: str = "data/market_info"):
        """
        初始化市场信息存储

        Args:
            base_dir: 存储的基础目录
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, start_time: datetime, end_time: datetime) -> Path:
        """
        根据时间范围获取文件路径

        Args:
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            文件路径，格式: YYYY-MM-DD_HH-MM.json
        """
        # 格式: 2025-12-05_19-22.json
        date_str = end_time.strftime("%Y-%m-%d")
        start_hour = start_time.strftime("%H-%M")
        end_hour = end_time.strftime("%H-%M")
        filename = f"{date_str}_{start_hour}_to_{end_hour}.json"
        return self.base_dir / filename

    def save_report(
        self,
        report: Dict[str, Any],
        start_time: datetime,
        end_time: datetime
    ) -> str:
        """
        保存市场信息报告

        Args:
            report: 报告内容
            start_time: 报告开始时间
            end_time: 报告结束时间

        Returns:
            保存的文件路径
        """
        file_path = self._get_file_path(start_time, end_time)

        # 添加元数据
        report_with_meta = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "interval_hours": (end_time - start_time).total_seconds() / 3600,
                "version": "2.0"
            },
            "data": report
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report_with_meta, f, ensure_ascii=False, indent=2)

        return str(file_path)

    def load_latest_report(self) -> Optional[Dict[str, Any]]:
        """
        加载最新的市场信息报告

        Returns:
            报告内容，如果不存在则返回 None
        """
        try:
            # 获取所有 JSON 文件
            json_files = list(self.base_dir.glob("*.json"))
            if not json_files:
                return None

            # 按修改时间排序，获取最新的
            latest_file = max(json_files, key=lambda f: f.stat().st_mtime)

            with open(latest_file, "r", encoding="utf-8") as f:
                return json.load(f)

        except Exception as e:
            print(f"❌ 加载最新报告失败: {e}")
            return None

    def get_combined_summary(
        self,
        symbols: Optional[List[str]] = None,
        max_length: int = 2000
    ) -> str:
        """
        获取最新报告的市场信息摘要

        Args:
            symbols: 关注的币种列表
            max_length: 最大长度限制

        Returns:
            格式化的市场信息摘要文本
        """
        report = self.load_latest_report()
        if not report:
            return ""

        data = report.get("data", {})
        metadata = report.get("metadata", {})

        # 格式化摘要
        summary_parts = []

        # 时间范围
        start_time = metadata.get("start_time", "")
        end_time = metadata.get("end_time", "")
        if start_time and end_time:
            summary_parts.append(f"### 📰 外部市场信息")
            summary_parts.append(f"**时间范围**: {start_time} 至 {end_time}")

        # 市场概况
        overview = data.get("market_overview", {})
        if overview:
            summary_text = overview.get("summary", "")
            trend = overview.get("trend", "")
            sentiment = overview.get("sentiment", "")

            if summary_text:
                summary_parts.append(f"\n**市场概况**: {summary_text}")
            if trend:
                summary_parts.append(f"**趋势**: {trend}")
            if sentiment:
                summary_parts.append(f"**情绪**: {sentiment}")

        # 关键事件（过滤与目标币种相关的）
        key_events = data.get("key_events", [])
        if key_events:
            relevant_events = []
            for event in key_events[:5]:
                event_coins = event.get("coins", [])
                if symbols:
                    if any(coin in symbols for coin in event_coins) or not event_coins:
                        relevant_events.append(event)
                else:
                    relevant_events.append(event)

            if relevant_events:
                summary_parts.append("\n**关键事件**:")
                for event in relevant_events[:3]:
                    title = event.get("title", "")
                    impact = event.get("impact", "")
                    if title:
                        summary_parts.append(f"- {title}（影响: {impact}）")

        # 风险提示
        risk_alerts = data.get("risk_alerts", [])
        if risk_alerts:
            # 筛选高风险和极高风险 (severity >= 4)
            high_risks = [r for r in risk_alerts if r.get("severity", 0) >= RiskSeverity.HIGH.value]
            if high_risks:
                summary_parts.append("\n**高风险警示**:")
                for risk in high_risks[:2]:
                    desc = risk.get("description", "")
                    if desc:
                        summary_parts.append(f"- ⚠️ {desc}")

        # 交易参考
        trading_imp = data.get("trading_implications", {})
        if trading_imp:
            bullish = trading_imp.get("bullish_factors", [])
            bearish = trading_imp.get("bearish_factors", [])

            if bullish:
                summary_parts.append(f"\n**利多因素**: {', '.join(bullish[:3])}")
            if bearish:
                summary_parts.append(f"**利空因素**: {', '.join(bearish[:3])}")

        full_summary = "\n".join(summary_parts)

        # 如果超过最大长度，进行截断
        if len(full_summary) > max_length:
            full_summary = full_summary[:max_length - 3] + "..."

        return full_summary

    def cleanup_old_reports(self, days_to_keep: int = 7):
        """
        清理过期的报告文件

        Args:
            days_to_keep: 保留的天数
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)

        for file_path in self.base_dir.glob("*.json"):
            try:
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if mtime < cutoff_date:
                    file_path.unlink()
            except Exception:
                continue

    def get_report_status(self) -> Dict[str, Any]:
        """
        获取报告状态信息

        Returns:
            报告状态信息
        """
        files = list(self.base_dir.glob("*.json"))
        latest_file = max(files, key=lambda f: f.stat().st_mtime) if files else None

        status = {
            "total_files": len(files),
            "latest_file": str(latest_file) if latest_file else None,
            "latest_modified": None
        }

        if latest_file:
            mtime = datetime.fromtimestamp(latest_file.stat().st_mtime)
            status["latest_modified"] = mtime.isoformat()

        return status


def get_market_info_store(base_dir: str = "data/market_info") -> MarketInfoStore:
    """
    获取市场信息存储实例

    Args:
        base_dir: 存储的基础目录

    Returns:
        MarketInfoStore 实例
    """
    return MarketInfoStore(base_dir)
