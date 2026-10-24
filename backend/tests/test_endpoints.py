"""Integration tests for FastAPI endpoints using TestClient.

Tests do not depend on internet access or live market data.
"""

import math
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


class TestHealth:
    def test_health_endpoint(self):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Clean Data Flow backend is running"


class TestMarketHistorySuccess:
    @patch("main.fetch_history")
    def test_successful_history_returns_bars_and_analysis(self, mock_fetch):
        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": "2026-01-01T00:00:00Z",
            "freshness": "test",
            "bars": [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000,
                }
            ],
        }
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=1d&interval=1d"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "RELIANCE"
        assert data["exchange"] == "NSE"
        assert len(data["bars"]) == 1
        assert "analysis" in data
        analysis = data["analysis"]
        # SMA/RSI/Fibonacci from analyze_bars
        assert "sma" in analysis
        assert "rsi" in analysis
        assert "fibonacci" in analysis
        # New: EMA deviation
        assert "ema_deviation" in analysis
        ema = analysis["ema_deviation"]
        assert "ema" in ema
        assert "latest_close" in ema
        assert "deviation_percent" in ema
        assert "period" in ema
        assert "available" in ema
        # New: Volume-price divergence
        assert "volume_price_divergence" in analysis
        vpd = analysis["volume_price_divergence"]
        assert "price_change_percent" in vpd
        assert "volume_change_percent" in vpd
        assert "signal" in vpd
        assert "available" in vpd
        assert "explanation" in vpd
        # New: LOF anomaly (only one bar, so it must be honestly unavailable)
        assert "lof_anomaly" in analysis
        lof = analysis["lof_anomaly"]
        assert lof["available"] is False
        assert lof["is_anomaly"] is None
        assert lof["lof_score"] is None
        assert "features_used" in lof
        assert lof["explanation"]

        # Bollinger Bands (only one bar, so it must be honestly unavailable)
        assert "bollinger_bands" in analysis
        bb = analysis["bollinger_bands"]
        assert bb["available"] is False
        assert bb["is_anomaly"] is None
        assert bb["upper"] is None
        assert bb["middle"] is None
        assert bb["lower"] is None
        assert bb["explanation"]

        # New: Transformer anomaly (only one bar, so it must be honestly unavailable)
        assert "transformer_anomaly" in analysis
        tr = analysis["transformer_anomaly"]
        assert tr["available"] is False
        assert tr["is_anomaly"] is None
        assert tr["anomaly_score"] is None
        assert tr["threshold"] is None
        assert "features_used" in tr
        assert tr["explanation"]

    @patch("main.fetch_history")
    def test_analyze_false_omits_analysis(self, mock_fetch):
        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": "2026-01-01T00:00:00Z",
            "freshness": "test",
            "bars": [],
        }
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=1d&interval=1d&analyze=false"
        )
        assert response.status_code == 200
        data = response.json()
        assert "analysis" not in data

    @patch("main.fetch_history")
    def test_bse_symbol(self, mock_fetch):
        mock_fetch.return_value = {
            "symbol": "TATASTEEL",
            "providerSymbol": "TATASTEEL.BO",
            "exchange": "BSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": "2026-01-01T00:00:00Z",
            "freshness": "test",
            "bars": [],
        }
        response = client.get(
            "/api/market/history?symbol=TATASTEEL&exchange=BSE&period=1d&interval=1d"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["exchange"] == "BSE"
        assert data["providerSymbol"] == "TATASTEEL.BO"

    @patch("main.fetch_history")
    def test_analysis_contains_all_components(self, mock_fetch):
        """Verify all analysis components are present when analyze=true."""
        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": "2026-01-01T00:00:00Z",
            "freshness": "test",
            "bars": [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000,
                }
            ],
        }
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=1d&interval=1d"
        )
        assert response.status_code == 200
        data = response.json()
        analysis = data["analysis"]
        # All ten analysis components
        assert set(analysis.keys()) == {
            "sma",
            "rsi",
            "fibonacci",
            "ema_deviation",
            "volume_price_divergence",
            "volume_spike",
            "volatility_anomaly",
            "lof_anomaly",
            "bollinger_bands",
            "transformer_anomaly",
        }

    @patch("main.fetch_history")
    def test_bollinger_bands_analysis_reuses_fetched_bars(self, mock_fetch):
        """Bollinger Bands must use the single fetched bar block, never refetch data."""
        bars = []
        for index in range(40):
            close = 100.0 + 5.0 * math.sin(index / 3.0)
            bars.append(
                {
                    "timestamp": f"2026-01-01T00:00:{index:02d}Z",
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000 + (index % 7) * 120,
                }
            )

        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": bars[-1]["timestamp"],
            "freshness": "test",
            "bars": bars,
        }

        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=3mo&interval=1d"
        )

        assert response.status_code == 200
        assert mock_fetch.call_count == 1
        bb = response.json()["analysis"]["bollinger_bands"]
        assert bb["available"] is True
        assert isinstance(bb["is_anomaly"], bool)
        assert bb["upper"] > bb["middle"] > bb["lower"]
        assert bb["period"] == 20
        assert bb["multiplier"] == 2.0
        assert isinstance(bb["percent_b"], float)
        assert bb["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert "Bollinger Bands" in bb["explanation"]

    @patch("main.fetch_history")
    def test_transformer_analysis_reuses_fetched_bars(self, mock_fetch):
        """Transformer must use the single fetched bar block, never refetch data."""
        bars = []
        for index in range(40):
            close = 100.0 + 5.0 * math.sin(index / 3.0)
            bars.append(
                {
                    "timestamp": f"2026-01-01T00:00:{index:02d}Z",
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000 + (index % 7) * 120,
                }
            )

        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": bars[-1]["timestamp"],
            "freshness": "test",
            "bars": bars,
        }

        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=3mo&interval=1d"
        )

        assert response.status_code == 200
        assert mock_fetch.call_count == 1
        tr = response.json()["analysis"]["transformer_anomaly"]
        assert tr["available"] is True
        assert isinstance(tr["is_anomaly"], bool)
        assert isinstance(tr["anomaly_score"], (int, float))
        assert isinstance(tr["threshold"], (int, float))
        assert tr["features_used"] == [
            "price_return_pct",
            "volume_change_pct",
            "true_range_pct",
        ]
        assert tr["history_observations"] == 38
        assert tr["history_limit"] == 180
        assert tr["excluded_older_bars"] == 0
        assert tr["training_epochs"] == 500
        assert tr["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert "no future data" in tr["explanation"]

    @patch("main.fetch_history")
    def test_lof_analysis_reuses_fetched_bars(self, mock_fetch):
        """LOF must use the single fetched bar block, never refetch data."""
        bars = []
        for index in range(40):
            close = 100.0 + 5.0 * math.sin(index / 3.0)
            bars.append(
                {
                    "timestamp": f"2026-01-01T00:00:{index:02d}Z",
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000 + (index % 7) * 120,
                }
            )

        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": bars[-1]["timestamp"],
            "freshness": "test",
            "bars": bars,
        }

        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=3mo&interval=1d"
        )

        assert response.status_code == 200
        assert mock_fetch.call_count == 1
        lof = response.json()["analysis"]["lof_anomaly"]
        assert lof["available"] is True
        assert isinstance(lof["is_anomaly"], bool)
        assert lof["features_used"] == [
            "price_return_pct",
            "volume_change_pct",
            "true_range_pct",
        ]
        assert lof["history_observations"] == 38
        assert lof["n_neighbors"] == 20
        assert lof["contamination"] == 0.05
        assert lof["evaluated_timestamp"] == bars[-1]["timestamp"]


class TestMarketHistoryInvalidRequests:
    def test_invalid_symbol_returns_400(self):
        response = client.get(
            "/api/market/history?symbol=RELIANCE!!&exchange=NSE&period=1d&interval=1d"
        )
        assert response.status_code == 400
        assert "unsupported characters" in response.json()["detail"]

    def test_invalid_exchange_returns_400(self):
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=LSE&period=1d&interval=1d"
        )
        assert response.status_code == 400
        assert "exchange must be NSE or BSE" in response.json()["detail"]

    def test_invalid_period_returns_400(self):
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=invalid&interval=1d"
        )
        assert response.status_code == 400
        assert "period must be one of" in response.json()["detail"]

    def test_invalid_interval_returns_400(self):
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=1d&interval=invalid"
        )
        assert response.status_code == 400
        assert "interval must be one of" in response.json()["detail"]

    def test_empty_symbol_returns_400(self):
        response = client.get(
            "/api/market/history?symbol=&exchange=NSE&period=1d&interval=1d"
        )
        assert response.status_code == 400


class TestMarketHistoryProviderFailures:
    @patch("main.fetch_history")
    def test_yfinance_exception_returns_502(self, mock_fetch):
        from market_data import MarketDataUnavailable

        mock_fetch.side_effect = MarketDataUnavailable("Yahoo Finance request failed")
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=1d&interval=1d"
        )
        assert response.status_code == 502
        assert "Yahoo Finance request failed" in response.json()["detail"]

    @patch("main.fetch_history")
    def test_provider_failure_never_returns_dummy_data(self, mock_fetch):
        from market_data import MarketDataUnavailable

        mock_fetch.side_effect = MarketDataUnavailable("Provider unavailable")
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=1d&interval=1d"
        )
        assert response.status_code == 502
        # Response must contain an error detail, not bars
        data = response.json()
        assert "detail" in data
        assert "bars" not in data


class TestMarketOpenInterestEndpoint:
    @patch("main.fetch_open_interest")
    def test_oi_success_returns_reported_contracts(self, mock_oi):
        mock_oi.return_value = {
            "available": True,
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "asOf": "2026-10-21",
            "source": "NSE India official F&O bhavcopy (UDiFF), end-of-day",
            "fetchedAt": "2026-10-22T10:00:00Z",
            "freshness": "End-of-day snapshot",
            "open_interest": {
                "total": 800000,
                "futures": 500000,
                "call_options": 120000,
                "put_options": 180000,
                "put_call_ratio": 1.5,
                "change_in_open_interest_total": 6000,
                "total_volume": 22000,
                "underlying_price": 2500.0,
            },
            "contracts": [{"symbol": "RELIANCE", "open_interest": 500000}],
            "contract_count": 1,
            "note": "not proof of market manipulation",
        }

        response = client.get("/api/market/oi?symbol=RELIANCE&exchange=NSE")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert data["symbol"] == "RELIANCE"
        assert data["open_interest"]["total"] == 800000
        assert data["contract_count"] == 1
        mock_oi.assert_called_once_with(
            "RELIANCE", exchange="NSE", as_of=None, max_lookback_days=7
        )

    @patch("main.fetch_open_interest")
    def test_oi_passes_explicit_date_and_lookback(self, mock_oi):
        mock_oi.return_value = {"available": False, "symbol": "RELIANCE"}

        response = client.get(
            "/api/market/oi?symbol=RELIANCE&exchange=NSE&date=2026-10-21&max_lookback_days=3"
        )

        assert response.status_code == 200
        mock_oi.assert_called_once_with(
            "RELIANCE", exchange="NSE", as_of="2026-10-21", max_lookback_days=3
        )

    @patch("main.fetch_open_interest")
    def test_oi_unavailable_is_honest_not_500(self, mock_oi):
        mock_oi.return_value = {
            "available": False,
            "symbol": "TCS",
            "exchange": "NSE",
            "reason": "No NSE F&O bhavcopy with contracts for TCS was found.",
            "open_interest": None,
            "contracts": [],
        }

        response = client.get("/api/market/oi?symbol=TCS&exchange=NSE")

        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["open_interest"] is None
        assert data["contracts"] == []
        assert "No NSE F&O bhavcopy" in data["reason"]

    @patch("main.fetch_open_interest")
    def test_oi_invalid_request_returns_400(self, mock_oi):
        mock_oi.side_effect = ValueError("exchange must be NSE or BSE")

        response = client.get("/api/market/oi?symbol=RELIANCE&exchange=LSE")

        assert response.status_code == 400
        assert "exchange must be NSE or BSE" in response.json()["detail"]


class TestHistoryDoesNotFetchOpenInterest:
    @patch("main.fetch_open_interest")
    @patch("main.fetch_history")
    def test_history_never_calls_oi_provider(self, mock_history, mock_oi):
        mock_history.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": "2026-01-01T00:00:00Z",
            "freshness": "test",
            "bars": [],
        }

        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=1d&interval=1d&analyze=false"
        )

        assert response.status_code == 200
        mock_history.assert_called_once_with("RELIANCE", "NSE", "1d", "1d")
        mock_oi.assert_not_called()


class TestIncompatiblePeriodInterval:
    def test_incompatible_pair_returns_400_before_provider(self):
        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=3mo&interval=1m"
        )
        assert response.status_code == 400
        assert "not supported with interval '1m'" in response.json()["detail"]

    def test_incompatible_pair_does_not_call_fetch_history(self):
        with patch("main.fetch_history") as mock_fetch:
            response = client.get(
                "/api/market/history?symbol=RELIANCE&exchange=NSE&period=5y&interval=1h"
            )
            assert response.status_code == 400
            mock_fetch.assert_not_called()


class TestTrailingIncompleteBarIntegration:
    @patch("main.fetch_history")
    def test_anomalies_score_latest_complete_bar(self, mock_fetch):
        bars = []
        for index in range(41):
            close = 100.0 + 4.0 * math.sin(index / 3.0)
            bars.append(
                {
                    "timestamp": f"2026-01-{index + 1:02d}T00:00:00Z",
                    "open": close - 0.4,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000 + (index % 7) * 120,
                }
            )
        # Yahoo-style partial latest bar: present but with no usable close.
        bars[-1]["close"] = None

        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-01-01T00:00:00Z",
            "lastDataTimestamp": bars[-2]["timestamp"],
            "freshness": "test",
            "bars": bars,
        }

        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=3mo&interval=1d"
        )

        assert response.status_code == 200
        assert mock_fetch.call_count == 1
        analysis = response.json()["analysis"]
        for key in ("lof_anomaly", "transformer_anomaly"):
            block = analysis[key]
            assert block["available"] is True, key
            assert block["evaluated_timestamp"] == bars[-2]["timestamp"], key
            assert block["excluded_trailing_bars"] == 1, key
            assert block["history_observations"] == 38, key

class TestHistoricalAnomalyEndpoint:
    @patch("main.fetch_history")
    def test_history_adds_timestamped_historical_points_without_changing_analysis_keys(
        self, mock_fetch
    ):
        timestamps = pd.date_range("2026-01-01", periods=32, freq="D", tz="UTC")
        bars = []
        for index, stamp in enumerate(timestamps):
            close = 100.0 + index * 0.5
            bars.append(
                {
                    "timestamp": stamp.isoformat().replace("+00:00", "Z"),
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 5000 if index == 28 else 1000,
                }
            )
        mock_fetch.return_value = {
            "symbol": "RELIANCE",
            "providerSymbol": "RELIANCE.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-02-01T00:00:00Z",
            "lastDataTimestamp": bars[-1]["timestamp"],
            "freshness": "test",
            "bars": bars,
        }

        response = client.get(
            "/api/market/history?symbol=RELIANCE&exchange=NSE&period=3mo&interval=1d"
        )
        assert response.status_code == 200
        data = response.json()
        assert set(data["analysis"].keys()) == {
            "sma",
            "rsi",
            "fibonacci",
            "ema_deviation",
            "volume_price_divergence",
            "volume_spike",
            "volatility_anomaly",
            "lof_anomaly",
            "bollinger_bands",
            "transformer_anomaly",
        }

        historical = data["historical_anomalies"]
        assert historical["bar_count"] == len(bars)
        assert set(historical["detectors"].keys()) == {
            "volume_spike",
            "volatility_anomaly",
            "volume_price_divergence",
            "lof_anomaly",
            "bollinger_bands",
            "transformer_anomaly",
        }
        assert historical["detectors"]["volume_spike"]["scope"] == "historical"
        assert historical["detectors"]["bollinger_bands"]["scope"] == "historical"
        assert historical["detectors"]["transformer_anomaly"]["scope"] == "latest_only"
        assert historical["latest_only_detectors"] == [
            "transformer_anomaly",
        ]

        returned = {bar["timestamp"] for bar in bars}
        for block in historical["detectors"].values():
            for point in block["points"]:
                assert point["timestamp"] in returned
                assert point["is_anomaly"] is True


class TestStaticFileServing:
    def test_root_serves_index_html(self):
        response = client.get("/")
        assert response.status_code == 200
        assert "Clean Data Flow" in response.text
        assert "html" in response.headers.get("content-type", "").lower()

    def test_stocks_page_serves_html(self):
        response = client.get("/stocks.html")
        assert response.status_code == 200
        assert "Stocks" in response.text

    def test_stock_analysis_page_serves_html(self):
        response = client.get("/stock-analysis.html")
        assert response.status_code == 200
        assert "Stock Analysis" in response.text

    def test_config_js_serves_javascript(self):
        response = client.get("/config.js")
        assert response.status_code == 200
        assert "CDF_API_BASE" in response.text

    def test_health_api_route_takes_precedence_over_static(self):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


    @patch("main.fetch_history")
    def test_two_phase_loading_include_transformer_flag(self, mock_fetch):
        """Phase 1 skips Transformer with status=pending; Phase 2 computes it."""
        bars = []
        for index in range(40):
            close = 100.0 + index * 0.5
            bars.append({
                "timestamp": f"2026-01-01T00:00:{index:02d}Z",
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000,
            })
        mock_fetch.return_value = {
            "symbol": "TCS",
            "providerSymbol": "TCS.NS",
            "exchange": "NSE",
            "source": "Yahoo Finance via yfinance",
            "interval": "1d",
            "fetchedAt": "2026-02-01T00:00:00Z",
            "lastDataTimestamp": bars[-1]["timestamp"],
            "freshness": "test",
            "bars": bars,
        }

        # Phase 1: include_transformer=false
        res1 = client.get("/api/market/history?symbol=TCS&exchange=NSE&period=3mo&interval=1d&include_transformer=false")
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["analysis"]["bollinger_bands"]["available"] is True
        tr1 = data1["analysis"]["transformer_anomaly"]
        assert tr1["available"] is False
        assert tr1["status"] == "pending"

        # Phase 2: include_transformer=true
        res2 = client.get("/api/market/history?symbol=TCS&exchange=NSE&period=3mo&interval=1d&include_transformer=true")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["analysis"]["bollinger_bands"]["available"] is True
        tr2 = data2["analysis"]["transformer_anomaly"]
        assert tr2.get("status") != "pending"
