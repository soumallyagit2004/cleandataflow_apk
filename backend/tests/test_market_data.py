"""Unit tests for market_data.py - validation and provider error handling.

All tests mock yfinance so they never depend on internet access or live market data.
"""

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from market_data import (
    ALLOWED_INTERVALS,
    ALLOWED_PERIODS,
    MarketDataUnavailable,
    fetch_history,
    supported_periods_for_interval,
    validate_period_interval,
)


class TestSymbolValidation:
    """Symbol must be non-empty, <= 30 chars, and contain only safe characters."""

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError, match="symbol contains unsupported characters"):
            fetch_history("", "NSE", "1d", "1d")

    def test_symbol_with_invalid_chars_raises(self):
        with pytest.raises(ValueError, match="symbol contains unsupported characters"):
            fetch_history("RELIANCE!!", "NSE", "1d", "1d")

    def test_symbol_too_long_raises(self):
        with pytest.raises(ValueError, match="symbol contains unsupported characters"):
            fetch_history("A" * 31, "NSE", "1d", "1d")

    def test_symbol_with_dot_and_dash_ok(self):
        # These characters pass validation; yfinance is mocked so no real call happens.
        with patch("market_data.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = pd.DataFrame(
                {"Open": [100], "High": [101], "Low": [99], "Close": [100], "Volume": [1000]},
                index=pd.date_range("2026-01-01", periods=1, tz="UTC"),
            )
            result = fetch_history("RELIANCE", "NSE", "1d", "1d")
            assert result is not None


class TestExchangeValidation:
    """Only NSE and BSE are supported exchanges."""

    def test_invalid_exchange_raises(self):
        with pytest.raises(ValueError, match="exchange must be NSE or BSE"):
            fetch_history("RELIANCE", "LSE", "1d", "1d")

    def test_exchange_is_case_insensitive(self):
        with patch("market_data.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = pd.DataFrame(
                {"Open": [100], "High": [101], "Low": [99], "Close": [100], "Volume": [1000]},
                index=pd.date_range("2026-01-01", periods=1, tz="UTC"),
            )
            result = fetch_history("RELIANCE", "nse", "1d", "1d")
            assert result["exchange"] == "NSE"


class TestPeriodValidation:
    def test_invalid_period_raises(self):
        with pytest.raises(ValueError, match="period must be one of"):
            fetch_history("RELIANCE", "NSE", "invalid", "1d")


class TestIntervalValidation:
    def test_invalid_interval_raises(self):
        with pytest.raises(ValueError, match="interval must be one of"):
            fetch_history("RELIANCE", "NSE", "1d", "invalid")


class TestPeriodIntervalCompatibility:
    """Provider-incompatible period/interval pairs must fail before yfinance."""

    def test_every_allowed_interval_has_a_supported_period(self):
        for interval in ALLOWED_INTERVALS:
            supported = supported_periods_for_interval(interval)
            assert supported, interval
            assert supported <= ALLOWED_PERIODS
            for period in supported:
                validate_period_interval(period, interval)

    def test_one_minute_allows_only_short_periods(self):
        validate_period_interval("1d", "1m")
        validate_period_interval("5d", "1m")
        with pytest.raises(ValueError, match="not supported with interval '1m'"):
            validate_period_interval("3mo", "1m")

    def test_five_minute_allows_one_month_but_not_three_months(self):
        validate_period_interval("1mo", "5m")
        with pytest.raises(ValueError, match="not supported with interval '5m'"):
            validate_period_interval("3mo", "5m")

    def test_hourly_allows_two_years_but_not_five_years(self):
        validate_period_interval("2y", "1h")
        validate_period_interval("3mo", "60m")
        with pytest.raises(ValueError, match="not supported with interval '1h'"):
            validate_period_interval("5y", "1h")

    def test_daily_intervals_allow_all_periods(self):
        for interval in ("1d", "5d", "1wk", "1mo", "3mo"):
            for period in ALLOWED_PERIODS:
                validate_period_interval(period, interval)

    def test_fetch_history_rejects_incompatible_pair_before_provider(self):
        with patch("market_data.yf.Ticker") as mock_ticker:
            with pytest.raises(ValueError, match="not supported with interval '5m'"):
                fetch_history("RELIANCE", "NSE", "3mo", "5m")
            mock_ticker.assert_not_called()


class TestQualifiedSymbolConstruction:
    """Exchange suffix should be appended when missing, preserved when present."""

    @patch("market_data.yf.Ticker")
    def test_nse_suffix_appended(self, mock_ticker):
        mock_ticker.return_value.history.return_value = pd.DataFrame(
            {"Open": [100], "High": [101], "Low": [99], "Close": [100], "Volume": [1000]},
            index=pd.date_range("2026-01-01", periods=1, tz="UTC"),
        )
        result = fetch_history("RELIANCE", "NSE", "1d", "1d")
        assert result["providerSymbol"] == "RELIANCE.NS"
        mock_ticker.assert_called_once_with("RELIANCE.NS")

    @patch("market_data.yf.Ticker")
    def test_bse_suffix_appended(self, mock_ticker):
        mock_ticker.return_value.history.return_value = pd.DataFrame(
            {"Open": [100], "High": [101], "Low": [99], "Close": [100], "Volume": [1000]},
            index=pd.date_range("2026-01-01", periods=1, tz="UTC"),
        )
        result = fetch_history("TATASTEEL", "BSE", "1d", "1d")
        assert result["providerSymbol"] == "TATASTEEL.BO"
        mock_ticker.assert_called_once_with("TATASTEEL.BO")

    @patch("market_data.yf.Ticker")
    def test_existing_suffix_preserved(self, mock_ticker):
        mock_ticker.return_value.history.return_value = pd.DataFrame(
            {"Open": [100], "High": [101], "Low": [99], "Close": [100], "Volume": [1000]},
            index=pd.date_range("2026-01-01", periods=1, tz="UTC"),
        )
        result = fetch_history("RELIANCE.NS", "NSE", "1d", "1d")
        assert result["providerSymbol"] == "RELIANCE.NS"
        mock_ticker.assert_called_once_with("RELIANCE.NS")


class TestProviderErrors:
    """Yahoo Finance failures must be surfaced honestly, never masked with dummy data."""

    @patch("market_data.yf.Ticker")
    def test_yfinance_exception_raises_market_data_unavailable(self, mock_ticker):
        mock_ticker.return_value.history.side_effect = Exception("Network timeout")
        with pytest.raises(MarketDataUnavailable):
            fetch_history("RELIANCE", "NSE", "1d", "1d")

    @patch("market_data.yf.Ticker")
    def test_empty_dataframe_raises_market_data_unavailable(self, mock_ticker):
        mock_ticker.return_value.history.return_value = pd.DataFrame()
        with pytest.raises(MarketDataUnavailable, match="No historical price data"):
            fetch_history("RELIANCE", "NSE", "1d", "1d")

    @patch("market_data.yf.Ticker")
    def test_none_dataframe_raises_market_data_unavailable(self, mock_ticker):
        mock_ticker.return_value.history.return_value = None
        with pytest.raises(MarketDataUnavailable, match="No historical price data"):
            fetch_history("RELIANCE", "NSE", "1d", "1d")


class TestMissingOHLCVValues:
    """Missing OHLCV values must be returned as null, not fabricated."""

    @patch("market_data.yf.Ticker")
    def test_nan_values_become_none(self, mock_ticker):
        import numpy as np

        df = pd.DataFrame(
            {
                "Open": [100.0, np.nan],
                "High": [101.0, 102.0],
                "Low": [99.0, 101.0],
                "Close": [100.0, np.nan],
                "Volume": [1000, 2000],
            },
            index=pd.date_range("2026-01-01", periods=2, tz="UTC"),
        )
        mock_ticker.return_value.history.return_value = df
        result = fetch_history("RELIANCE", "NSE", "1d", "1d")
        assert len(result["bars"]) == 2
        assert result["bars"][0]["open"] == 100.0
        assert result["bars"][1]["open"] is None
        assert result["bars"][1]["close"] is None
