"""
市场信息存储模块
负责存储和读取外部市场信息，供交易决策 Agent 使用
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from enum import Enum


class TimePeriod(Enum):
    """时间周期枚举"""
    DAILY = "daily"          # 每日
    WEEKLY = "weekly"        # 每周
    BIWEEKLY = "biweekly"    # 两周
    MONTHLY = "monthly"      # 每月


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
        self._ensure_directories()

    def _ensure_directories(self):
        """确保所有必要的目录存在"""
        for period in TimePeriod:
            period_dir = self.base_dir / period.value
            period_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, period: TimePeriod, date: datetime) -> Path:
        """
        根据时间周期和日期获取文件路径

        Args:
            period: 时间周期
            date: 日期

        Returns:
            文件路径
        """
        if period == TimePeriod.DAILY:
            # 每日：YYYY-MM-DD.json
            filename = date.strftime("%Y-%m-%d.json")
        elif period == TimePeriod.WEEKLY:
            # 每周：YYYY-Www.json（ISO 周数）
            filename = date.strftime("%Y-W%W.json")
        elif period == TimePeriod.BIWEEKLY:
            # 两周：使用两周周期的起始日期
            # 计算两周周期的起始日期（以年初为基准）
            year_start = datetime(date.year, 1, 1)
            days_since_year_start = (date - year_start).days
            biweek_number = days_since_year_start // 14
            biweek_start = year_start + timedelta(days=biweek_number * 14)
            filename = biweek_start.strftime("%Y-%m-%d.json")
        elif period == TimePeriod.MONTHLY:
            # 每月：YYYY-MM.json
            filename = date.strftime("%Y-%m.json")
        else:
            raise ValueError(f"未知的时间周期: {period}")

        return self.base_dir / period.value / filename

    def save_report(
        self,
        period: TimePeriod,
        report: Dict[str, Any],
        date: Optional[datetime] = None
    ) -> str:
        """
        保存市场信息报告

        Args:
            period: 时间周期
            report: 报告内容
            date: 报告日期（默认为当前时间）

        Returns:
            保存的文件路径
        """
        if date is None:
            date = datetime.now()

        file_path = self._get_file_path(period, date)

        # 添加元数据
        report_with_meta = {
            "metadata": {
                "period": period.value,
                "generated_at": datetime.now().isoformat(),
                "report_date": date.isoformat(),
                "version": "1.0"
            },
            "data": report
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report_with_meta, f, ensure_ascii=False, indent=2)

        return str(file_path)

    def load_report(
        self,
        period: TimePeriod,
        date: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """
        加载市场信息报告

        Args:
            period: 时间周期
            date: 报告日期（默认为当前时间）

        Returns:
            报告内容，如果不存在则返回 None
        """
        if date is None:
            date = datetime.now()

        file_path = self._get_file_path(period, date)

        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return None

    def get_latest_reports(
        self,
        max_age_hours: int = 24
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        获取所有周期的最新报告

        Args:
            max_age_hours: 最大报告年龄（小时），超过则认为过期

        Returns:
            各周期的最新报告，格式为 {period: report}
        """
        now = datetime.now()
        reports = {}

        for period in TimePeriod:
            report = self.load_report(period, now)

            # 检查报告是否过期
            if report:
                generated_at = report.get("metadata", {}).get("generated_at")
                if generated_at:
                    try:
                        gen_time = datetime.fromisoformat(generated_at)
                        age_hours = (now - gen_time).total_seconds() / 3600
                        if age_hours > max_age_hours:
                            report = None  # 报告过期
                    except ValueError:
                        pass

            reports[period.value] = report

        return reports

    def get_combined_summary(
        self,
        symbols: Optional[List[str]] = None,
        max_length: int = 2000
    ) -> str:
        """
        获取合并的市场信息摘要，用于注入到交易决策中

        Args:
            symbols: 关注的币种列表
            max_length: 最大长度限制

        Returns:
            格式化的市场信息摘要文本
        """
        reports = self.get_latest_reports()
        summary_parts = []

        # 按优先级处理各周期报告
        priority_order = [
            TimePeriod.DAILY.value,
            TimePeriod.WEEKLY.value,
            TimePeriod.BIWEEKLY.value,
            TimePeriod.MONTHLY.value
        ]

        for period_name in priority_order:
            report = reports.get(period_name)
            if not report:
                continue

            data = report.get("data", {})
            period_summary = self._format_period_summary(period_name, data, symbols)

            if period_summary:
                summary_parts.append(period_summary)

        if not summary_parts:
            return ""

        # 合并所有摘要
        full_summary = "\n\n".join(summary_parts)

        # 如果超过最大长度，进行截断
        if len(full_summary) > max_length:
            full_summary = full_summary[:max_length - 3] + "..."

        return full_summary

    def _format_period_summary(
        self,
        period_name: str,
        data: Dict[str, Any],
        symbols: Optional[List[str]] = None
    ) -> str:
        """
        格式化单个周期的摘要

        Args:
            period_name: 周期名称
            data: 报告数据
            symbols: 关注的币种列表

        Returns:
            格式化的摘要文本
        """
        period_labels = {
            "daily": "📅 近24小时市场信息",
            "weekly": "📆 近一周市场信息",
            "biweekly": "📊 近两周市场信息",
            "monthly": "📈 近一月市场信息"
        }

        label = period_labels.get(period_name, f"📋 {period_name}")
        parts = [f"### {label}"]

        # 市场概况
        overview = data.get("market_overview", {})
        if overview:
            summary_text = overview.get("summary", "")
            trend = overview.get("trend", "")
            sentiment = overview.get("sentiment", "")

            if summary_text:
                parts.append(f"\n**市场概况**: {summary_text}")
            if trend:
                parts.append(f"**趋势**: {trend}")
            if sentiment:
                parts.append(f"**情绪**: {sentiment}")

        # 关键事件（过滤与目标币种相关的）
        key_events = data.get("key_events", [])
        if key_events:
            relevant_events = []
            for event in key_events[:5]:  # 最多5个事件
                event_coins = event.get("coins", [])
                # 如果指定了币种，只显示相关事件
                if symbols:
                    if any(coin in symbols for coin in event_coins) or not event_coins:
                        relevant_events.append(event)
                else:
                    relevant_events.append(event)

            if relevant_events:
                parts.append("\n**关键事件**:")
                for event in relevant_events[:3]:  # 最多显示3个
                    title = event.get("title", "")
                    impact = event.get("impact", "")
                    if title:
                        parts.append(f"- {title}（影响: {impact}）")

        # 市场情绪
        sentiment_data = data.get("market_sentiment", {})
        if sentiment_data:
            overall = sentiment_data.get("overall", "")
            fear_greed = sentiment_data.get("fear_greed_index", "")
            if overall:
                parts.append(f"\n**市场情绪**: {overall}")
            if fear_greed:
                parts.append(f"**恐惧贪婪指数**: {fear_greed}")

        # 风险提示
        risk_alerts = data.get("risk_alerts", [])
        if risk_alerts:
            high_risks = [r for r in risk_alerts if r.get("severity") == "高"]
            if high_risks:
                parts.append("\n**高风险警示**:")
                for risk in high_risks[:2]:  # 最多2个高风险
                    desc = risk.get("description", "")
                    if desc:
                        parts.append(f"- ⚠️ {desc}")

        # 交易参考
        trading_imp = data.get("trading_implications", {})
        if trading_imp:
            bullish = trading_imp.get("bullish_factors", [])
            bearish = trading_imp.get("bearish_factors", [])

            if bullish:
                parts.append(f"\n**利多因素**: {', '.join(bullish[:3])}")
            if bearish:
                parts.append(f"**利空因素**: {', '.join(bearish[:3])}")

        return "\n".join(parts) if len(parts) > 1 else ""

    def cleanup_old_reports(self, days_to_keep: int = 30):
        """
        清理过期的报告文件

        Args:
            days_to_keep: 保留的天数
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)

        for period in TimePeriod:
            period_dir = self.base_dir / period.value

            if not period_dir.exists():
                continue

            for file_path in period_dir.glob("*.json"):
                try:
                    # 获取文件修改时间
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if mtime < cutoff_date:
                        file_path.unlink()
                except Exception:
                    continue

    def get_report_status(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有报告的状态信息

        Returns:
            各周期报告的状态信息
        """
        status = {}

        for period in TimePeriod:
            period_dir = self.base_dir / period.value
            files = list(period_dir.glob("*.json")) if period_dir.exists() else []

            latest_file = max(files, key=lambda f: f.stat().st_mtime) if files else None

            period_status = {
                "total_files": len(files),
                "latest_file": str(latest_file) if latest_file else None,
                "latest_modified": None
            }

            if latest_file:
                mtime = datetime.fromtimestamp(latest_file.stat().st_mtime)
                period_status["latest_modified"] = mtime.isoformat()

            status[period.value] = period_status

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
