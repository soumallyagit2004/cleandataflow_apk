"""Tests for the shared normalization/quality layer (providers.base).

These validate that corrupt responses are REJECTED (never repaired into
plausible-looking data) and that genuinely missing values stay None exactly
like the pre-existing Yahoo path, so analysis keeps working unchanged.
"""

from datetime import datetime, timezone

import pytest

from providers.base import (
    DataRequest,
    ProviderResult,
    build_bar,
    clean_bars,
    iso_z,
    parse_timestamp,
    to_finite_float,
)


def _request(symbol="RELIANCE", exchange="NSE", period="1mo", interval="1d"):
    return DataRequest(
        symbol=symbol, exchange=exchange, period=period,
        interval=interval, bars_estimate=40,
    )


class TestToFiniteFloat:
    def test_numbers_pass_through(self):
        assert to_finite_float(100) == 100.0
        assert to_finite_float(12.5) == 12.5
        assert to_finite_float(-3.0) == -3.0  # sign preserved; caller validates domain

    def test_numeric_strings_and_thousands_separators(self):
        assert to_finite_float("2305.75") == 2305.75
        assert to_finite_float(" 2305.75 ") == 2305.75
        assert to_finite_float("1,234.5") == 1234.5

    def test_missing_and_garbage_become_none(self):
        assert to_finite_float(None) is None
        assert to_finite_float("") is None
        assert to_finite_float("   ") is None
        assert to_finite_float("not-a-number") is None

    def test_booleans_and_non_finite_become_none(self):
        assert to_finite_float(True) is None
        assert to_finite_float(float("nan")) is None
        assert to_finite_float(float("inf")) is None
        assert to_finite_float(float("-inf")) is None


class TestParseTimestamp:
    def test_epoch_seconds(self):
        expected = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        epoch = int(expected.timestamp())
        assert parse_timestamp(epoch) == expected
        assert parse_timestamp(str(epoch)) == expected

    def test_iso_instants(self):
        assert parse_timestamp("2026-01-01T00:00:00Z") == datetime(
            2026, 1, 1, tzinfo=timezone.utc
        )
        assert parse_timestamp("2026-01-01T05:30:00+05:30") == datetime(
            2026, 1, 1, tzinfo=timezone.utc
        )

    def test_naive_and_bare_dates_assume_utc(self):
        assert parse_timestamp("2026-01-01") == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert parse_timestamp("2026-01-01 15:30:00") == datetime(
            2026, 1, 1, 15, 30, tzinfo=timezone.utc
        )

    def test_garbage_and_empty_are_none(self):
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None
        assert parse_timestamp("not a date") is None

    def test_datetime_objects_are_preserved(self):
        aware = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        assert parse_timestamp(aware) == aware


class TestIsoZ:
    def test_formats_with_z_suffix(self):
        assert iso_z(datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)) == (
            "2026-01-01T15:30:00Z"
        )


class TestBuildBar:
    def test_valid_bar_round_trip(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        bar = build_bar(stamp, 100.0, 101.5, 99.0, 100.25, 12345)
        assert bar == {
            "timestamp": "2026-01-05T10:00:00Z",
            "open": 100.0, "high": 101.5, "low": 99.0,
            "close": 100.25, "volume": 12345.0,
        }

    def test_missing_values_stay_none_never_fabricated(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        bar = build_bar(stamp, 100.0, None, 99.0, None, None)
        assert bar["open"] == 100.0
        assert bar["high"] is None
        assert bar["low"] == 99.0
        assert bar["close"] is None
        assert bar["volume"] is None

    def test_missing_timestamp_rejects_bar(self):
        assert build_bar(None, 100, 101, 99, 100, 10) is None

    def test_garbage_price_string_rejects_bar(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert build_bar(stamp, "that's not a price", 101, 99, 100, 10) is None

    def test_non_positive_prices_reject_bar(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert build_bar(stamp, 0, 101, 99, 100, 10) is None
        assert build_bar(stamp, 100, 101, 99, 0, 10) is None
        assert build_bar(stamp, 100, 101, -1, 100, 10) is None

    def test_inverted_candle_rejects_high_below_low(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert build_bar(stamp, 100, 90, 110, 95, 10) is None

    def test_close_above_high_rejects(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert build_bar(stamp, 100, 101, 99, 150, 10) is None

    def test_open_below_low_rejects(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert build_bar(stamp, 98, 101, 99, 100, 10) is None

    def test_negative_volume_rejects(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert build_bar(stamp, 100, 101, 99, 100, -5) is None

    def test_nan_string_price_rejects(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert build_bar(stamp, "nan", 101, 99, 100, 10) is None


class TestCleanBars:
    def test_rejects_none_candidates_and_sorts_ascending(self):
        early = build_bar(datetime(2026, 1, 1, tzinfo=timezone.utc), 1, 2, 1, 2, 5)
        late = build_bar(datetime(2026, 1, 3, tzinfo=timezone.utc), 2, 3, 2, 3, 5)
        bars = clean_bars([late, None, early, None])
        assert [bar["timestamp"] for bar in bars] == [
            "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z",
        ]

    def test_duplicate_timestamps_keep_first_occurrence(self):
        first = build_bar(datetime(2026, 1, 2, tzinfo=timezone.utc), 1, 2, 1, 2, 5)
        second = build_bar(datetime(2026, 1, 2, tzinfo=timezone.utc), 9, 9, 9, 9, 5)
        bars = clean_bars([second, first])
        assert len(bars) == 1
        assert bars[0]["close"] == 9.0  # first-seen wins, values are never merged

    def test_all_invalid_returns_empty(self):
        assert clean_bars([None, None]) == []


class TestProviderResult:
    def test_to_response_carries_contract_fields(self):
        stamp = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        bar = build_bar(stamp, 1, 2, 1, 2, 5)
        result = ProviderResult(
            provider="stub", provider_symbol="RELIANCE.NS", bars=[bar],
            fetched_at="2026-01-05T18:35:00Z", source="stub source",
            freshness="provider timestamp supplied",
            last_bar_timestamp=bar["timestamp"], chain=["stub"],
        )
        payload = result.to_response("RELIANCE")
        assert payload["symbol"] == "RELIANCE"
        assert payload["providerSymbol"] == "RELIANCE.NS"
        assert payload["source"] == "stub source"
        assert payload["fetchedAt"] == "2026-01-05T18:35:00Z"
        assert payload["lastDataTimestamp"] == bar["timestamp"]
        assert payload["freshness"] == "provider timestamp supplied"
        assert payload["bars"] == [bar]
        assert payload["providerChain"] == ["stub"]
        assert payload["exchange"] is None      # facade fills validated exchange
        assert payload["interval"] is None

    def test_to_response_derives_last_timestamp_from_bars(self):
        bar = build_bar(datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc), 1, 2, 1, 2, 5)
        result = ProviderResult(
            provider="stub", provider_symbol="X", bars=[bar],
            fetched_at="2026-01-05T18:35:00Z", source="s", freshness="f",
        )
        assert result.to_response("X")["lastDataTimestamp"] == bar["timestamp"]


class TestDataRequest:
    def test_request_is_immutable(self):
        request = _request()
        with pytest.raises(Exception):
            request.symbol = "OTHER"
