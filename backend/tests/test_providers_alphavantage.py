"""Tests for the Alpha Vantage adapter with mocked HTTP responses.

No real API key or network access is used — every response is scripted.
Headers/errors follow the documented payload contract (Error Message / Note /
Information), and the client-side 25/day + 5/minute budget is exercised.
"""

from datetime import datetime, timedelta, timezone

import pytest

from providers import alphavantage
from providers.base import (
    DataRequest,
    ProviderAuthError,
    ProviderNotSupported,
    ProviderRateLimited,
    ProviderUnavailable,
)

API_KEY_FOR_TESTS = "alphavantage-test-key-never-real"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, invalid_json=False, text=""):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json
        self.text = text

    def json(self):
        if self._invalid_json:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def _adapter_state(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", API_KEY_FOR_TESTS)
    monkeypatch.delenv("ALPHAVANTAGE_ENABLE_NSE", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_ENABLE_INTRADAY", raising=False)
    alphavantage._minute_window.clear()
    alphavantage._daily_count = 0
    alphavantage._daily_day_key = None
    yield
    alphavantage._minute_window.clear()
    alphavantage._daily_count = 0
    alphavantage._daily_day_key = None


def _request(symbol="RELIANCE", exchange="BSE", period="1mo", interval="1d", estimate=40):
    return DataRequest(
        symbol=symbol, exchange=exchange, period=period,
        interval=interval, bars_estimate=estimate,
    )


def _daily_payload():
    return {
        "Meta Data": {
            "1. Information": "Daily Prices (open, high, low, close) and Volumes",
            "2. Symbol": "RELIANCE.BSE",
            "4. Time Series": "Daily",
        },
        "Time Series (Daily)": {
            "2026-01-07": {"1. open": "2300", "2. high": "2310.5",
                           "3. low": "2280", "4. close": "2305.75", "5. volume": "1204000"},
            "2026-01-06": {"1. open": "2290", "2. high": "2305",
                           "3. low": "2288", "4. close": "2299", "5. volume": "998000"},
            "2026-01-05": {"1. open": "2280", "2. high": "2295",
                           "3. low": "2270", "4. close": "2290.5", "5. volume": "890000"},
        },
    }


class TestSuccessfulFetch:
    def test_daily_bse_bars_sorted_with_ist_end_of_day_stamps(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        result = alphavantage.fetch_history(_request())
        assert captured["url"] == "https://www.alphavantage.co/query"
        assert captured["params"]["function"] == "TIME_SERIES_DAILY"
        assert captured["params"]["symbol"] == "RELIANCE.BSE"
        assert result.provider == "alphavantage"
        assert result.provider_symbol == "RELIANCE.BSE"
        assert [bar["timestamp"] for bar in result.bars] == [
            "2026-01-05T18:30:00Z", "2026-01-06T18:30:00Z", "2026-01-07T18:30:00Z",
        ]
        assert result.bars[-1]["close"] == 2305.75
        assert result.last_bar_timestamp == "2026-01-07T18:30:00Z"

    def test_weekly_function_and_block(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["params"] = params
            return FakeResponse(payload={
                "Meta Data": {"2. Symbol": "RELIANCE.BSE"},
                "Weekly Time Series": {
                    "2026-01-05": {"1. open": "2280", "2. high": "2310",
                                   "3. low": "2270", "4. close": "2305", "5. volume": "5000000"},
                },
            })

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        result = alphavantage.fetch_history(_request(interval="1wk"))
        assert captured["params"]["function"] == "TIME_SERIES_WEEKLY"
        assert [bar["timestamp"] for bar in result.bars] == ["2026-01-05T00:00:00Z"]

    def test_compact_for_small_requests_full_for_long_history(self, monkeypatch):
        captured = []

        def fake_get(url, params=None, timeout=None):
            captured.append(dict(params))
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        alphavantage.fetch_history(_request(period="1d", estimate=6))
        alphavantage.fetch_history(_request(period="5y", estimate=1300))
        assert captured[0]["outputsize"] == "compact"
        assert captured[1]["outputsize"] == "full"

    def test_missing_fields_rejected_without_fabrication(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={"Time Series (Daily)": {
                "2026-01-07": {"1. open": "", "2. high": "10", "3. low": "9",
                               "4. close": "9.5", "5. volume": "10"},
                "2026-01-06": {"1. open": "10", "2. high": "11", "3. low": "9.5",
                               "4. close": "10.5", "5. volume": "12"},
            }})

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        result = alphavantage.fetch_history(_request())
        assert len(result.bars) == 1
        assert result.bars[0]["timestamp"] == "2026-01-06T18:30:00Z"


class TestErrorHandling:
    def test_invalid_api_key_maps_to_auth_error(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "Error Message": "Invalid API call. Please retry or visit the documentation. "
                                 "API key is missing or invalid.",
            })

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderAuthError) as excinfo:
            alphavantage.fetch_history(_request())
        assert API_KEY_FOR_TESTS not in str(excinfo.value)

    def test_bad_symbol_maps_to_unavailable(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "Error Message": "Invalid symbol. Please check the symbol and try again.",
            })

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="Invalid symbol"):
            alphavantage.fetch_history(_request())

    def test_note_payload_maps_to_rate_limited(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "Note": "Thank you for using Alpha Vantage! Our standard API rate "
                        "limit is 25 requests per day.",
            })

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderRateLimited):
            alphavantage.fetch_history(_request())

    def test_premium_information_maps_to_not_supported(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "Information": "This API endpoint is not available on the free plan. "
                                "Please upgrade to premium.",
            })

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderNotSupported):
            alphavantage.fetch_history(_request())

    def test_other_information_maps_to_rate_limited(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={"Information": "Thank you for using Alpha Vantage! "
                                                       "Our standard API call frequency is 5 "
                                                       "requests per minute."})

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderRateLimited):
            alphavantage.fetch_history(_request())

    def test_http_error_status_is_unavailable(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(status_code=503, text="unavailable")

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="HTTP 503"):
            alphavantage.fetch_history(_request())

    def test_invalid_json_is_unavailable(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload=None, invalid_json=True)

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="invalid JSON"):
            alphavantage.fetch_history(_request())

    def test_missing_series_block_is_unavailable(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={"Meta Data": {}})

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="No historical price data"):
            alphavantage.fetch_history(_request())

    def test_network_failure_is_unavailable(self, monkeypatch):
        class FakeTimeout(Exception):
            pass

        def fake_get(url, params=None, timeout=None):
            raise FakeTimeout("read timeout")

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="FakeTimeout"):
            alphavantage.fetch_history(_request())


class TestQuotaAccounting:
    def test_daily_budget_defers_excess_requests(self, monkeypatch):
        monkeypatch.setenv("ALPHAVANTAGE_MAX_REQUESTS_PER_DAY", "2")
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(1)
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        alphavantage.fetch_history(_request(symbol="A"))
        alphavantage.fetch_history(_request(symbol="B"))
        with pytest.raises(ProviderRateLimited, match="daily quota"):
            alphavantage.fetch_history(_request(symbol="C"))
        assert sum(calls) == 2  # third blocked client-side, never sent upstream

    def test_minute_budget_defers_excess_requests(self, monkeypatch):
        monkeypatch.setenv("ALPHAVANTAGE_MAX_REQUESTS_PER_MINUTE", "1")
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(1)
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        alphavantage.fetch_history(_request(symbol="A"))
        with pytest.raises(ProviderRateLimited, match="per-minute"):
            alphavantage.fetch_history(_request(symbol="B"))
        assert sum(calls) == 1

    def test_successful_call_spends_exactly_one_credit(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        alphavantage.fetch_history(_request())
        assert alphavantage._daily_count == 1

    def test_day_key_change_resets_budget(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(alphavantage.httpx, "get", fake_get)
        alphavantage.fetch_history(_request(symbol="A"))
        # simulate the previous day still holding a maxed counter
        alphavantage._daily_count = alphavantage._requests_per_day()
        alphavantage._daily_day_key = "2000-01-01"
        alphavantage.fetch_history(_request(symbol="B"))  # must not be rate limited
        # the stale counter reset to 0 on the new day, then one fresh spend
        assert alphavantage._daily_count == 1


class TestCapabilityGate:
    def test_bse_daily_weekly_monthly_enabled_by_default(self):
        assert alphavantage.supports(_request(interval="1d")) is True
        assert alphavantage.supports(_request(interval="1wk")) is True
        assert alphavantage.supports(_request(interval="1mo")) is True

    def test_nse_disabled_by_default_pending_verification(self, monkeypatch):
        assert alphavantage.supports(_request(exchange="NSE")) is False
        monkeypatch.setenv("ALPHAVANTAGE_ENABLE_NSE", "1")
        assert alphavantage.supports(_request(exchange="NSE")) is True
        # NSE keeps only daily+ even when opted in — intraday stays off
        assert alphavantage.supports(_request(exchange="NSE", interval="15m")) is False

    def test_intraday_off_until_explicit_opt_in(self, monkeypatch):
        assert alphavantage.supports(_request(interval="15m")) is False
        monkeypatch.setenv("ALPHAVANTAGE_ENABLE_INTRADAY", "1")
        assert alphavantage.supports(_request(interval="15m")) is True
        assert alphavantage.supports(_request(exchange="NSE", interval="15m")) is False

    def test_other_exchanges_never_routed(self):
        assert alphavantage.supports(_request(exchange="NYSE")) is False

    def test_missing_credentials_disable_gracefully(self, monkeypatch):
        monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
        assert alphavantage.has_credentials() is False

    def test_fetch_without_key_raises_auth_error(self):
        import os

        saved = os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        try:
            with pytest.raises(ProviderAuthError, match="without a configured API key"):
                alphavantage.fetch_history(_request())
        finally:
            if saved is not None:
                os.environ["ALPHAVANTAGE_API_KEY"] = saved
