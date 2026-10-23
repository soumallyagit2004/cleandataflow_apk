"""Tests for the shared market cache: TTLs, expiry, stale access, LRU and
single-flight (concurrent identical requests share one upstream call).
"""

import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import market_cache
from providers.base import ProviderResult


MONDAY_1100 = datetime(2026, 3, 9, 11, 0, tzinfo=market_cache.IST)          # session open
MONDAY_2000 = datetime(2026, 3, 9, 20, 0, tzinfo=market_cache.IST)          # after close
SATURDAY_1100 = datetime(2026, 3, 14, 11, 0, tzinfo=market_cache.IST)       # weekend
MONDAY_0700 = datetime(2026, 3, 9, 7, 0, tzinfo=market_cache.IST)           # pre-open


class FakeClock:
    def __init__(self, start):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture(autouse=True)
def _empty_cache():
    market_cache.clear()
    market_cache._inflight.clear()
    yield
    market_cache.clear()
    market_cache._inflight.clear()


def _result(provider="yahoo", fetched="2026-01-05T18:35:00Z"):
    return ProviderResult(
        provider=provider, provider_symbol="RELIANCE.NS", bars=[
            {"timestamp": "2026-01-05T18:30:00Z", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1000.0},
        ],
        fetched_at=fetched, source="stub source", freshness="stub freshness",
        last_bar_timestamp="2026-01-05T18:30:00Z", chain=[provider],
    )


class TestMarketSessionWindows:
    def test_session_open(self):
        assert market_cache.is_session_open(MONDAY_1100) is True

    def test_pre_open_after_market_close_and_weekends_are_closed(self):
        assert market_cache.is_session_open(MONDAY_0700) is False
        assert market_cache.is_session_open(MONDAY_2000) is False
        assert market_cache.is_session_open(SATURDAY_1100) is False

    def test_in_session_ttls(self):
        assert market_cache.ttl_for_key("1m", MONDAY_1100) == 60.0
        assert market_cache.ttl_for_key("60m", MONDAY_1100) == 60.0
        assert market_cache.ttl_for_key("1d", MONDAY_1100) == 900.0
        assert market_cache.ttl_for_key("1wk", MONDAY_1100) == 900.0

    def test_after_close_daily_caches_until_next_session(self):
        # Monday 20:00 -> Tuesday 09:15 is 13h15m = 47700s
        assert market_cache.ttl_for_key("1d", MONDAY_2000) == pytest.approx(47700.0)

    def test_after_close_intraday_is_capped(self):
        # Never trust a stale intraday entry longer than the 12h cap
        assert market_cache.ttl_for_key("1m", MONDAY_2000) == pytest.approx(12 * 3600.0)

    def test_weekend_daily_survives_to_monday_session(self):
        # Saturday 11:00 -> Monday 09:15 = 46h15m = 166500s (below the 72h cap)
        assert market_cache.ttl_for_key("1d", SATURDAY_1100) == pytest.approx(166500.0)

    def test_weekend_intraday_capped(self):
        assert market_cache.ttl_for_key("5m", SATURDAY_1100) == pytest.approx(12 * 3600.0)


class TestCacheStore:
    def test_put_then_get_returns_same_entry(self):
        key = market_cache.cache_key("RELIANCE", "NSE", "1d", "1mo")
        assert market_cache.get(key) is None
        market_cache.put(key, _result(), "1d")
        entry = market_cache.get(key)
        assert entry is not None
        assert entry.result.provider == "yahoo"
        assert entry.result.fetched_at == "2026-01-05T18:35:00Z"

    def test_expired_entry_invisible_to_get_but_reachable_via_stale(self, monkeypatch):
        key = market_cache.cache_key("RELIANCE", "NSE", "1d", "1mo")
        clock = FakeClock(time.monotonic())
        monkeypatch.setattr(market_cache, "time_module", SimpleNamespace(monotonic=clock))
        monkeypatch.setattr(market_cache, "_ist_now", lambda: MONDAY_1100)
        market_cache.put(key, _result(), "1d")           # TTL 900s in-session
        assert market_cache.get(key) is not None
        clock.advance(901)
        assert market_cache.get(key) is None             # fresh window over
        stale = market_cache.get_stale(key, max_age_seconds=24 * 3600)
        assert stale is not None                          # fallback can still reach it

    def test_stale_rejected_beyond_max_age(self, monkeypatch):
        key = market_cache.cache_key("RELIANCE", "NSE", "1d", "1mo")
        clock = FakeClock(time.monotonic())
        monkeypatch.setattr(market_cache, "time_module", SimpleNamespace(monotonic=clock))
        monkeypatch.setattr(market_cache, "_ist_now", lambda: MONDAY_1100)
        market_cache.put(key, _result(), "1d")
        clock.advance(25 * 3600)
        assert market_cache.get(key) is None
        assert market_cache.get_stale(key, max_age_seconds=24 * 3600) is None

    def test_unknown_key_is_never_a_hit(self):
        assert market_cache.get(("NOPE", "NSE", "1d", "1mo")) is None
        assert market_cache.get_stale(("NOPE", "NSE", "1d", "1mo"), 3600) is None

    def test_keys_distinguish_every_dimension(self):
        base = market_cache.cache_key("RELIANCE", "NSE", "1d", "1mo")
        assert market_cache.cache_key("OTHER", "NSE", "1d", "1mo") != base
        assert market_cache.cache_key("RELIANCE", "BSE", "1d", "1mo") != base
        assert market_cache.cache_key("RELIANCE", "NSE", "5m", "1mo") != base
        assert market_cache.cache_key("RELIANCE", "NSE", "1d", "3mo") != base

    def test_lru_eviction_respects_max_entries(self, monkeypatch):
        monkeypatch.setenv("CDF_MARKET_CACHE_MAX_ENTRIES", "2")
        keys = [
            market_cache.cache_key(f"S{i}", "NSE", "1d", "1mo") for i in range(3)
        ]
        for index, key in enumerate(keys):
            market_cache.put(key, _result(fetched=f"2026-01-0{index + 1}T00:00:00Z"), "1d")
        assert market_cache.size() == 2
        assert market_cache.get(keys[0]) is None        # oldest evicted
        assert market_cache.get(keys[2]) is not None    # newest kept

    def test_clear_and_drop(self):
        key = market_cache.cache_key("RELIANCE", "NSE", "1d", "1mo")
        market_cache.put(key, _result(), "1d")
        market_cache.drop(key)
        assert market_cache.get(key) is None
        market_cache.put(key, _result(), "1d")
        market_cache.clear()
        assert market_cache.size() == 0


class TestSingleFlight:
    def test_concurrent_same_request_makes_one_upstream_call(self):
        key = ("RELIANCE", "NSE", "1d", "1mo")
        calls = []
        outcomes = []
        start = threading.Barrier(4)

        def loader():
            calls.append(1)
            time.sleep(0.3)  # widen the window so all racers pile onto the flight
            return "payload"

        def worker():
            start.wait()
            outcomes.append(market_cache.single_flight(key, loader))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert sum(calls) == 1
        assert outcomes == [("payload", False)] * 4

    def test_failure_is_shared_with_waiters(self):
        key = ("RELIANCE", "NSE", "1d", "1mo")
        calls = []
        failures = []
        failure = ValueError("upstream exploded")

        def loader():
            calls.append(1)
            time.sleep(0.2)
            raise failure

        def worker():
            failures.append(market_cache.single_flight(key, loader))

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert sum(calls) == 1
        assert failures == [(None, failure)] * 3
        # The flight is removed afterwards so the NEXT request may retry.
        assert key not in market_cache._inflight

    def test_waiter_timeout_returns_timeout_signal(self, monkeypatch):
        key = ("WAIT", "NSE", "1d", "1mo")
        monkeypatch.setattr(market_cache, "SINGLE_FLIGHT_WAIT_SECONDS", 0.05)
        release = threading.Event()

        def slow_loader():
            time.sleep(0.5)
            release.set()
            return "late"

        def owner():
            market_cache.single_flight(key, slow_loader)

        thread = threading.Thread(target=owner)
        thread.start()
        time.sleep(0.1)  # let the owner register the flight first
        outcome, shared = market_cache.single_flight(key, lambda: "self-load")
        assert outcome is None
        assert shared is True
        thread.join(5)
        assert key not in market_cache._inflight

    def test_sequential_requests_use_new_flights(self):
        key = ("SEQ", "NSE", "1d", "1mo")
        market_cache.single_flight(key, lambda: "one")
        value, shared = market_cache.single_flight(key, lambda: "two")
        assert (value, shared) == ("two", False)


class TestFlightBooking:
    def test_owner_registers_and_cleans_up(self):
        key = ("OWN", "NSE", "1d", "1mo")
        value, shared = market_cache.single_flight(key, lambda: "x")
        assert (value, shared) == ("x", False)
        assert key not in market_cache._inflight
