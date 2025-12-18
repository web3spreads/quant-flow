"""
环境特征相似度计算
支持欧氏距离与余弦相似度，并允许为不同特征设置权重

注意：此模块已迁移到 src.agents.common.utils.similarity_scorer
此文件保留用于向后兼容，请使用新位置。
"""

# 从新位置导入（兼容层）
from src.agents.common.utils.similarity_scorer import (
    SimilarityScorer,
    DEFAULT_WEIGHTS,
)

__all__ = ["SimilarityScorer", "DEFAULT_WEIGHTS"]
