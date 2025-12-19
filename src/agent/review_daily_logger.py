"""
复盘经验每日日志记录器

按日期存储复盘经验,便于后续 LoRA 训练使用。
每条记录以 JSONL 格式存储,包含完整的输入输出对,
可直接转换为 Alpaca/ShareGPT 等训练格式。

注意:此模块已迁移到 src.agents.common.utils.review_daily_logger
此文件保留用于向后兼容,请使用新位置。
"""

# 从新位置导入(兼容层)
from src.agents.common.utils.review_daily_logger import ReviewDailyLogger

__all__ = ["ReviewDailyLogger"]
