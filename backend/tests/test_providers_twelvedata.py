"""Tests for the Twelve Data adapter with mocked HTTP responses.

No real API key or network access is used — every response is scripted.
"""

from datetime import datetime, timezone

import pytest

from providers import twelvedata
from providers.base import (
    DataRequest,
    ProviderAuthError,
    ProviderNotSupported,
    ProviderRateLimited,
    ProviderUnavailable,
)

API_KEY_FOR_TESTS = "twelvedata-test-key-never-real"


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
    monkeypatch.setenv("TWELVEDATA_API_KEY", API_KEY_FOR_TESTS)
    monkeypatch.delenv("TWELVEDATA_ENABLE_INDIA", raising=False)
    twelvedata._minute_window.clear()
    yield
    twelvedata._minute_window.clear()


def _request(symbol="RELIANCE", exchange="NSE", period="1mo", interval="1d", estimate=40):
    return DataRequest(
        symbol=symbol, exchange=exchange, period=period,
        interval=interval, bars_estimate=estimate,
    )


def _daily_payload():
    return {
        "meta": {"symbol": "RELIANCE", "exchange": "NSE", "interval": "1day"},
        "values": [  # newest first, exactly like the live API
            {"datetime": "2026-01-07", "open": "2300", "high": "2310.5",
             "low": "2280", "close": "2305.75", "volume": "1,204,000"},
            {"datetime": "2026-01-06", "open": "2290", "high": "2305",
             "low": "2288", "close": "2299", "volume": "998,000"},
            {"datetime": "2026-01-05", "open": "2280", "high": "2295",
             "low": "2270", "close": "2290.5", "volume": "890,000"},
        ],
        "status": "ok",
    }


class TestSuccessfulFetch:
    def test_daily_bars_sorted_with_ist_end_of_day_stamps(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        result = twelvedata.fetch_history(_request())
        assert captured["url"] == "https://api.twelvedata.com/time_series"
        assert captured["params"]["symbol"] == "RELIANCE"
        assert captured["params"]["exchange"] == "NSE"
        assert captured["params"]["interval"] == "1day"
        assert captured["params"]["timezone"] == "UTC"
        assert result.provider == "twelvedata"
        assert result.provider_symbol == "RELIANCE"
        # date-only values use the app's 18:30Z (24:00 IST) daily convention
        assert [bar["timestamp"] for bar in result.bars] == [
            "2026-01-05T18:30:00Z", "2026-01-06T18:30:00Z", "2026-01-07T18:30:00Z",
        ]
        assert result.bars[-1]["close"] == 2305.75
        assert result.bars[-1]["volume"] == 1204000.0
        assert result.last_bar_timestamp == "2026-01-07T18:30:00Z"

    def test_intraday_epoch_timestamps_kept_absolute(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["params"] = params
            return FakeResponse(payload={
                "values": [
                    {"datetime": "1767229200", "open": "100", "high": "101",
                     "low": "99", "close": "100.5", "volume": "500"},
                    {"datetime": "1767232200", "open": "100.5", "high": "102",
                     "low": "100", "close": "101", "volume": "600"},
                ],
                "status": "ok",
            })

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        result = twelvedata.fetch_history(_request(interval="5m", estimate=80))
        assert captured["params"]["interval"] == "5min"
        assert [bar["timestamp"] for bar in result.bars] == [
            datetime.fromtimestamp(1767229200, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            datetime.fromtimestamp(1767232200, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        ]

    def test_outputsize_is_budgeted_and_bounded(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["params"] = params
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        twelvedata.fetch_history(_request(estimate=300))
        assert captured["params"]["outputsize"] == 320  # estimate + margin
        twelvedata.fetch_history(_request(estimate=9000))
        assert captured["params"]["outputsize"] == 5000  # provider maximum

    def test_corrupt_candles_dropped_valid_ones_kept(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "values": [
                    {"datetime": "2026-01-06", "open": "100", "high": "102",
                     "low": "99", "close": "101", "volume": "500"},
                    {"datetime": "2026-01-07", "open": "not-a-price", "high": "103",
                     "low": "99", "close": "102", "volume": "500"},
                ],
                "status": "ok",
            })

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        result = twelvedata.fetch_history(_request())
        assert len(result.bars) == 1
        assert result.bars[0]["timestamp"] == "2026-01-06T18:30:00Z"


class TestErrorHandling:
    def test_invalid_api_key_maps_to_auth_error(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "code": 401, "status": "error",
                "message": "**Invalid API key. The API key you provided is not valid.",
            })

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderAuthError) as excinfo:
            twelvedata.fetch_history(_request())
        assert API_KEY_FOR_TESTS not in str(excinfo.value)

    def test_rate_limit_payload_maps_to_rate_limited(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "code": 429, "status": "error",
                "message": "**Request too big. **You have exceeded your quota.",
            })

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderRateLimited):
            twelvedata.fetch_history(_request())

    def test_plan_coverage_error_maps_to_not_supported(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={
                "code": 405, "status": "error",
                "message": "The provided API key is not subscribed to this market plan.",
            })

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderNotSupported):
            twelvedata.fetch_history(_request())

    def test_empty_values_is_unavailable_not_silent(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload={"values": [], "status": "ok"})

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="No historical price data"):
            twelvedata.fetch_history(_request())

    def test_invalid_json_is_unavailable(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload=None, invalid_json=True)

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="invalid JSON"):
            twelvedata.fetch_history(_request())

    def test_unexpected_payload_shape_is_unavailable(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(payload=["not", "an", "object"])

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable):
            twelvedata.fetch_history(_request())

    def test_http_error_status_maps_by_code(self, monkeypatch):
        def fake_get(url, params=None, timeout=None):
            return FakeResponse(status_code=402, text="Payment required")

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderRateLimited, match="HTTP 402"):
            twelvedata.fetch_history(_request())

        def fake_get_500(url, params=None, timeout=None):  # noqa: F811
            return FakeResponse(status_code=500, text="server error")

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get_500)
        with pytest.raises(ProviderUnavailable, match="HTTP 500"):
            twelvedata.fetch_history(_request())

    def test_network_failure_is_unavailable(self, monkeypatch):
        class FakeTimeout(Exception):
            pass

        def fake_get(url, params=None, timeout=None):
            raise FakeTimeout("connection timed out")

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        with pytest.raises(ProviderUnavailable, match="FakeTimeout"):
            twelvedata.fetch_history(_request())


class TestClientSideLimiter:
    def test_second_call_within_window_is_deferred(self, monkeypatch):
        monkeypatch.setenv("TWELVEDATA_MAX_REQUESTS_PER_MINUTE", "1")
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(1)
            return FakeResponse(payload=_daily_payload())

        monkeypatch.setattr(twelvedata.httpx, "get", fake_get)
        twelvedata.fetch_history(_request(symbol="A"))
        with pytest.raises(ProviderRateLimited, match="per-minute"):
            twelvedata.fetch_history(_request(symbol="B"))
        assert sum(calls) == 1  # the limiter blocked the second upstream call


class TestCapabilityGate:
    def test_india_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("TWELVEDATA_ENABLE_INDIA", raising=False)
        assert twelvedata.has_credentials() is True
        assert twelvedata.supports(_request(exchange="NSE")) is False
        assert twelvedata.supports(_request(exchange="BSE")) is False

    def test_india_opt_in_enables_verified_request_shapes(self, monkeypatch):
        monkeypatch.setenv("TWELVEDATA_ENABLE_INDIA", "1")
        assert twelvedata.supports(_request(exchange="NSE", interval="1d")) is True
        assert twelvedata.supports(_request(exchange="NSE", interval="60m")) is True
        assert twelvedata.supports(_request(exchange="BSE", interval="1wk")) is True

    def test_unsupported_intervals_stay_off_even_when_india_enabled(self, monkeypatch):
        monkeypatch.setenv("TWELVEDATA_ENABLE_INDIA", "1")
        for interval in ("2m", "90m", "5d", "3mo"):
            assert twelvedata.supports(_request(interval=interval)) is False

    def test_non_indian_exchanges_are_never_routed(self):
        assert twelvedata.supports(_request(exchange="NYSE")) is False

    def test_missing_credentials_disable_gracefully(self, monkeypatch):
        monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
        assert twelvedata.has_credentials() is False

    def test_fetch_without_key_raises_auth_error(self, monkeypatch):
        monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
        monkeypatch.setenv("TWELVEDATA_ENABLE_INDIA", "1")
        with pytest.raises(ProviderAuthError, match="without a configured API key"):
            twelvedata.fetch_history(_request())
