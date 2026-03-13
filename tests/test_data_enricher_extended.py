"""
数据增强模块扩展测试
测试 CEX 资金费率领先信号、链上 MVRV/SOPR、恐惧贪婪指数等新增功能
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.data.data_enricher import MarketDataEnricher


class MockMarketFetcher:
    """模拟市场数据获取器"""

    def __init__(self, funding_rate=0.0001):
        self._funding_rate = funding_rate

    def get_funding_rate(self, symbol: str):
        return self._funding_rate


class TestCexFundingRate:
    """CEX 资金费率领先信号测试"""

    @pytest.fixture
    def enricher(self):
        return MarketDataEnricher(MockMarketFetcher())

    def test_cex_leading_bullish_signal(self, enricher):
        """测试 CEX 费率显著高于 DEX 时的多头信号"""
        # 模拟 Binance 返回高费率
        binance_data = [{"fundingRate": "0.001"}]
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(binance_data).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = enricher._get_cex_funding_rate("BTC", 0.0001)

        assert result["cex_funding_rate"] == 0.001
        assert result["cex_dex_funding_diff"] == pytest.approx(0.0009, abs=1e-6)
        assert result["cex_funding_signal_type"] == "cex_leading_bullish"
        assert "CEX" in result["cex_funding_signal"]

    def test_cex_leading_bearish_signal(self, enricher):
        """测试 CEX 费率显著低于 DEX 时的空头信号"""
        binance_data = [{"fundingRate": "-0.0005"}]
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(binance_data).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = enricher._get_cex_funding_rate("BTC", 0.0002)

        assert result["cex_funding_rate"] == -0.0005
        assert result["cex_dex_funding_diff"] < -0.0005
        assert result["cex_funding_signal_type"] == "cex_leading_bearish"

    def test_cex_neutral_signal(self, enricher):
        """测试 CEX 和 DEX 费率接近时的中性信号"""
        binance_data = [{"fundingRate": "0.0002"}]
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(binance_data).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = enricher._get_cex_funding_rate("BTC", 0.0001)

        assert result["cex_funding_signal_type"] == "neutral"

    def test_cex_api_failure_graceful_degradation(self, enricher):
        """测试 Binance API 失败时的优雅降级"""
        with patch("urllib.request.urlopen", side_effect=Exception("HTTP 451")):
            result = enricher._get_cex_funding_rate("BTC", 0.0001)

        assert result["cex_funding_rate"] == 0
        assert result["cex_funding_signal"] == "数据不可用"
        assert result["cex_funding_signal_type"] == "unknown"

    def test_cex_empty_response(self, enricher):
        """测试 Binance 返回空数据"""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps([]).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = enricher._get_cex_funding_rate("BTC", 0.0001)

        assert result["cex_funding_signal"] == "数据不可用"

    def test_cex_uses_correct_symbol_format(self, enricher):
        """测试 CEX 请求使用正确的交易对格式"""
        binance_data = [{"fundingRate": "0.0001"}]
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(binance_data).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            enricher._get_cex_funding_rate("ETH", 0.0001)

        # 验证 URL 中使用 ETHUSDT 格式
        call_args = mock_open.call_args
        request_obj = call_args[0][0]
        assert "ETHUSDT" in request_obj.full_url


class TestOnchainMvrvSopr:
    """链上 MVRV/SOPR 数据测试"""

    @pytest.fixture
    def enricher(self):
        return MarketDataEnricher(MockMarketFetcher())

    def test_non_btc_returns_default(self, enricher):
        """测试非 BTC 币种返回默认值"""
        result = enricher._get_onchain_mvrv_sopr("ETH")

        assert result["mvrv_signal"] == "仅BTC支持链上指标"
        assert result["sopr_signal"] == "仅BTC支持链上指标"
        assert result["onchain_summary"] == "仅BTC支持链上指标"

    def test_non_btc_returns_default_for_sol(self, enricher):
        """测试 SOL 也返回默认值"""
        result = enricher._get_onchain_mvrv_sopr("SOL")

        assert result["onchain_summary"] == "仅BTC支持链上指标"

    def test_btc_blockchain_info_success(self, enricher):
        """测试 blockchain.info 数据获取成功"""
        blockchain_stats = {
            "market_price_usd": 70000,
            "n_btc_mined": 1970000000000000,  # 单位是 satoshi
            "trade_volume_usd": 500000000,
            "hash_rate": 600000000,
            "miners_revenue_btc": 500,
        }

        hashrate_data = {
            "hashrates": [
                {"avgHashrate": 580000000},
                {"avgHashrate": 600000000},
            ]
        }

        def mock_urlopen(req, **kwargs):
            resp = MagicMock()
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "blockchain.info" in url:
                resp.read.return_value = json.dumps(blockchain_stats).encode()
            elif "mempool.space" in url:
                resp.read.return_value = json.dumps(hashrate_data).encode()
            else:
                resp.read.return_value = b"{}"
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = enricher._get_onchain_mvrv_sopr("BTC")

        assert "BTC" in result["mvrv_signal"] or "市价" in result["mvrv_signal"]
        assert result["onchain_summary"] != "链上数据不可用"

    def test_btc_api_failure_graceful(self, enricher):
        """测试所有 API 都失败时的优雅降级"""
        with patch("urllib.request.urlopen", side_effect=Exception("网络超时")):
            result = enricher._get_onchain_mvrv_sopr("BTC")

        # 应不抛异常，返回降级结果
        assert "onchain_summary" in result
        assert isinstance(result["onchain_summary"], str)

    def test_hashrate_increasing(self, enricher):
        """测试算力上升信号"""
        blockchain_stats = {
            "market_price_usd": 70000,
            "n_btc_mined": 1970000000000000,
            "trade_volume_usd": 500000000,
            "hash_rate": 600000000,
            "miners_revenue_btc": 500,
        }

        hashrate_data = {
            "hashrates": [
                {"avgHashrate": 500000000},
                {"avgHashrate": 600000000},  # 上升 20%
            ]
        }

        def mock_urlopen(req, **kwargs):
            resp = MagicMock()
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "blockchain.info" in url:
                resp.read.return_value = json.dumps(blockchain_stats).encode()
            else:
                resp.read.return_value = json.dumps(hashrate_data).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = enricher._get_onchain_mvrv_sopr("BTC")

        assert "上升" in result["sopr_signal"]
        assert "看多" in result["sopr_signal"]

    def test_empty_onchain_helper(self, enricher):
        """测试 _empty_onchain 辅助方法"""
        result = enricher._empty_onchain("自定义原因")

        assert result["mvrv_signal"] == "自定义原因"
        assert result["sopr_signal"] == "自定义原因"
        assert result["onchain_summary"] == "自定义原因"

    def test_empty_cex_funding_helper(self):
        """测试 _empty_cex_funding 辅助方法"""
        result = MarketDataEnricher._empty_cex_funding()

        assert result["cex_funding_rate"] == 0
        assert result["cex_funding_signal_type"] == "unknown"


class TestFearGreedIndex:
    """恐惧贪婪指数测试"""

    @pytest.fixture
    def enricher(self):
        return MarketDataEnricher(MockMarketFetcher())

    def test_extreme_fear(self, enricher):
        """测试极度恐惧信号"""
        api_response = {"data": [{"value": "15", "value_classification": "Extreme Fear"}]}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_response).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = enricher._get_fear_greed_index()

        assert result["fear_greed_value"] == 15
        assert "极度恐惧" in result["fear_greed_sentiment"]
        assert result["fear_greed_signal_bias"] == "bullish_contrarian"

    def test_extreme_greed(self, enricher):
        """测试极度贪婪信号"""
        api_response = {"data": [{"value": "85", "value_classification": "Extreme Greed"}]}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_response).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = enricher._get_fear_greed_index()

        assert result["fear_greed_value"] == 85
        assert "极度贪婪" in result["fear_greed_sentiment"]
        assert result["fear_greed_signal_bias"] == "bearish_contrarian"

    def test_neutral_sentiment(self, enricher):
        """测试中性情绪"""
        api_response = {"data": [{"value": "50", "value_classification": "Neutral"}]}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(api_response).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = enricher._get_fear_greed_index()

        assert result["fear_greed_signal_bias"] == "mild_bearish"

    def test_api_failure(self, enricher):
        """测试 API 失败"""
        with patch("urllib.request.urlopen", side_effect=Exception("超时")):
            result = enricher._get_fear_greed_index()

        assert result["fear_greed_value"] == -1
        assert result["fear_greed_signal_bias"] == "unknown"


class TestFundingRateSignal:
    """资金费率极值信号测试"""

    def test_extreme_positive_rate(self):
        """测试极端正费率 → 逆向看空"""
        enricher = MarketDataEnricher(MockMarketFetcher(funding_rate=0.002))
        result = enricher._get_oi_and_funding("BTC")

        assert result["funding_rate_signal_strength"] == "bearish_contrarian"
        assert "逆向看空" in result["funding_rate_signal"]

    def test_extreme_negative_rate(self):
        """测试极端负费率 → 逆向看多"""
        enricher = MarketDataEnricher(MockMarketFetcher(funding_rate=-0.002))
        result = enricher._get_oi_and_funding("BTC")

        assert result["funding_rate_signal_strength"] == "bullish_contrarian"
        assert "逆向看多" in result["funding_rate_signal"]

    def test_mild_positive_rate(self):
        """测试温和正费率"""
        enricher = MarketDataEnricher(MockMarketFetcher(funding_rate=0.0008))
        result = enricher._get_oi_and_funding("BTC")

        assert result["funding_rate_signal_strength"] == "mild_bullish"

    def test_neutral_rate(self):
        """测试中性费率"""
        enricher = MarketDataEnricher(MockMarketFetcher(funding_rate=0.0001))
        result = enricher._get_oi_and_funding("BTC")

        assert result["funding_rate_signal_strength"] == "neutral"


class TestEnrichMarketDataIntegration:
    """enrich_market_data 集成测试（新增字段）"""

    @pytest.fixture
    def enricher(self):
        return MarketDataEnricher(MockMarketFetcher())

    def test_enriched_data_contains_new_fields(self, enricher):
        """测试增强数据包含所有新增字段"""
        market_data = {"current_price": 50000.0, "rsi": 55.0}

        with (
            patch.object(
                enricher,
                "_get_cex_funding_rate",
                return_value={
                    "cex_funding_rate": 0,
                    "cex_dex_funding_diff": 0,
                    "cex_funding_signal": "测试",
                    "cex_funding_signal_type": "neutral",
                },
            ),
            patch.object(
                enricher,
                "_get_onchain_mvrv_sopr",
                return_value={
                    "mvrv_ratio": 0,
                    "mvrv_signal": "测试",
                    "sopr_value": 0,
                    "sopr_signal": "测试",
                    "onchain_summary": "测试",
                },
            ),
            patch.object(
                enricher,
                "_get_fear_greed_index",
                return_value={
                    "fear_greed_value": 50,
                    "fear_greed_label": "Neutral",
                    "fear_greed_sentiment": "中性",
                    "fear_greed_signal_bias": "neutral",
                },
            ),
        ):
            result = enricher.enrich_market_data("BTC", market_data)

        # 验证新增字段都存在
        assert "cex_funding_signal" in result
        assert "onchain_summary" in result
        assert "fear_greed_sentiment" in result
        assert "funding_rate_signal" in result
