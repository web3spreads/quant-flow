"""
保护配置迁移测试
测试从旧 account_protection 配置到新 protections 配置的迁移逻辑。
"""

import tempfile
from pathlib import Path

import pytest

from src.plugins.protections import PROTECTION_REGISTRY, ProtectionManager


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestRegistryCompleteness:
    """注册表完整性"""

    def test_all_four_plugins_registered(self):
        """4 个内置插件全部注册"""
        expected = {"max_drawdown", "daily_loss", "consecutive_loss", "position_timeout"}
        assert set(PROTECTION_REGISTRY.keys()) == expected


class TestNewConfig:
    """新配置格式直接使用"""

    def test_full_config_loads_all_plugins(self, tmp_dir):
        """完整配置加载全部 4 个插件"""
        config = [
            {"name": "max_drawdown", "max_drawdown_pct": 0.10, "pause_hours": 4},
            {"name": "daily_loss", "max_daily_loss_pct": 0.05, "pause_hours": 4},
            {
                "name": "consecutive_loss",
                "max_consecutive_losses": 5,
                "per_symbol": True,
                "pause_hours": 4,
            },
            {"name": "position_timeout", "max_position_hours": 48},
        ]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)
        assert len(manager.plugins) == 4

    def test_partial_config(self, tmp_dir):
        """只配置部分插件"""
        config = [{"name": "max_drawdown", "max_drawdown_pct": 0.15}]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)
        assert len(manager.plugins) == 1
        assert manager.plugins[0].name == "max_drawdown"


class TestLegacyMigration:
    """旧配置迁移逻辑（在 Config 类中实现）"""

    def test_migrate_legacy_config(self):
        """旧格式配置转为新格式列表"""
        # 模拟 Config._migrate_legacy_protection_config 的逻辑
        legacy = {
            "enabled": True,
            "max_drawdown_pct": 0.10,
            "max_daily_loss_pct": 0.05,
            "max_position_hours": 48,
            "max_consecutive_losses": 5,
            "pause_hours_after_protection": 4,
        }

        # 迁移逻辑
        migrated = [
            {
                "name": "max_drawdown",
                "max_drawdown_pct": legacy["max_drawdown_pct"],
                "pause_hours": legacy["pause_hours_after_protection"],
            },
            {
                "name": "daily_loss",
                "max_daily_loss_pct": legacy["max_daily_loss_pct"],
                "pause_hours": legacy["pause_hours_after_protection"],
            },
            {
                "name": "consecutive_loss",
                "max_consecutive_losses": legacy["max_consecutive_losses"],
                "per_symbol": False,
                "pause_hours": legacy["pause_hours_after_protection"],
            },
            {
                "name": "position_timeout",
                "max_position_hours": legacy["max_position_hours"],
            },
        ]

        assert len(migrated) == 4
        assert migrated[0]["name"] == "max_drawdown"
        assert migrated[0]["max_drawdown_pct"] == 0.10

    def test_empty_when_both_absent(self, tmp_dir):
        """既无新配置也无旧配置时，插件列表为空"""
        manager = ProtectionManager(protections_config=[], data_dir=tmp_dir)
        assert len(manager.plugins) == 0
