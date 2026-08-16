"""配置模块测试：默认值、YAML 覆盖、环境变量与保护链默认。"""

import pytest

from src.config import DEFAULT_PROTECTIONS, Config


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.delenv("HYPERLIQUID_ACCOUNT_ADDRESS", raising=False)
    monkeypatch.delenv("HYPERLIQUID_TESTNET", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_missing_config_file_uses_defaults(tmp_path):
    cfg = Config.load(config_path=str(tmp_path / "nonexistent.yaml"))
    assert cfg.trading.symbols == ("BTC",)
    assert cfg.trading.perp_enabled is True
    assert cfg.trading.grid_enabled is False
    assert cfg.trading.max_leverage == 5
    assert cfg.grid.force_neutral is True
    assert cfg.exchange.testnet is True  # 默认测试网（安全取向）


def test_missing_private_key_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "")
    with pytest.raises(ValueError, match="HYPERLIQUID_PRIVATE_KEY"):
        Config.load(config_path=str(tmp_path / "nonexistent.yaml"))


def test_yaml_overrides(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
llm:
  base_url: https://example.com/v1/
  model: my-model
  temperature: 0.5
trading:
  symbols: [eth, btc]
  grid_enabled: true
  max_trade_amount: 250
  max_leverage: 3
grid:
  interval_minutes: 10
  width_min_pct: 0.03
  barrier:
    stop_loss_pct: 0.08
""",
        encoding="utf-8",
    )
    cfg = Config.load(config_path=str(config_file))
    assert cfg.llm.base_url == "https://example.com/v1"  # 末尾斜杠被清理
    assert cfg.llm.model == "my-model"
    assert cfg.trading.symbols == ("ETH", "BTC")  # 符号统一大写
    assert cfg.trading.grid_enabled is True
    assert cfg.trading.max_trade_amount == 250.0
    assert cfg.grid.interval_minutes == 10
    assert cfg.grid.width_min_pct == 0.03
    assert cfg.grid.barrier == {"stop_loss_pct": 0.08}


def test_empty_symbols_raises(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("trading:\n  symbols: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="symbols"):
        Config.load(config_path=str(config_file))


def test_protections_default_when_absent(tmp_path):
    cfg = Config.load(config_path=str(tmp_path / "nonexistent.yaml"))
    assert cfg.protections == DEFAULT_PROTECTIONS
    # 默认列表是拷贝，修改配置不会污染模块级常量
    cfg.protections[0]["max_drawdown_pct"] = 0.99
    assert DEFAULT_PROTECTIONS[0]["max_drawdown_pct"] == 0.10


def test_protections_explicit_empty_respected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("protections: []\n", encoding="utf-8")
    cfg = Config.load(config_path=str(config_file))
    assert cfg.protections == []


def test_env_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_TESTNET", "false")
    monkeypatch.setenv("HYPERLIQUID_ACCOUNT_ADDRESS", "0xABC")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    cfg = Config.load(config_path=str(tmp_path / "nonexistent.yaml"))
    assert cfg.exchange.testnet is False
    assert cfg.exchange.account_address == "0xABC"
    assert cfg.llm.api_key == "sk-test"


def test_openai_key_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config.load(config_path=str(tmp_path / "nonexistent.yaml"))
    assert cfg.llm.api_key == "sk-openai"


def test_symbols_scalar_string_coerced(tmp_path):
    # symbols 误写标量字符串时按单交易对纠偏，不得拆成 ("B","T","C")
    config_file = tmp_path / "config.yaml"
    config_file.write_text("trading:\n  symbols: eth\n", encoding="utf-8")
    cfg = Config.load(config_path=str(config_file))
    assert cfg.trading.symbols == ("ETH",)


def test_trend_filter_timeframes_scalar_coerced(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("grid:\n  trend_filter_timeframes: 15m\n", encoding="utf-8")
    cfg = Config.load(config_path=str(config_file))
    assert cfg.grid.trend_filter_timeframes == ("15m",)


def test_legacy_flat_grid_keys_warned(tmp_path, caplog):
    # 旧扁平键 trading.grid_* 被忽略时必须告警（静默失效=安全阀悄悄消失）
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "trading:\n  grid_max_position_notional_usd: 250\n  grid_halt_below_usd: 100\n",
        encoding="utf-8",
    )
    import logging

    with caplog.at_level(logging.WARNING, logger="quantflow"):
        Config.load(config_path=str(config_file))
    text = caplog.text
    assert "grid_max_position_notional_usd" in text
    assert "grid:" in text  # 迁移指引


def test_legacy_top_level_sections_warned(tmp_path, caplog):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("run_mode: grid\nnotification:\n  enabled: true\n", encoding="utf-8")
    import logging

    with caplog.at_level(logging.WARNING, logger="quantflow"):
        Config.load(config_path=str(config_file))
    assert "run_mode" in caplog.text
    assert "notification" in caplog.text


def test_perp_llm_alert_cycles_loaded(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("trading:\n  llm_failure_alert_cycles: 3\n", encoding="utf-8")
    cfg = Config.load(config_path=str(config_file))
    assert cfg.trading.llm_failure_alert_cycles == 3
