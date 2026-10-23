"""Tests for the central market-data manager.

Covers provider eligibility, round-robin/weighted/first-available routing,
sequential fallback, health cooldowns (rate limit / auth / repeated failures),
the explicit stale-fallback policy, single-flight through get_history, and the
market_data facade contract (keys, cache-hit honesty, stale labels, 502-class
error type).
"""

import threading
import time as time_module
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import market_cache
import market_manager
from market_manager import MarketManager, ManagedHistory, NoProviderSucceeded, estimate_bars
from market_data import MarketDataUnavailable, fetch_history
from providers.base import (
    ProviderAuthError,
    ProviderNotSupported,
    ProviderRateLimited,
    ProviderResult,
    ProviderUnavailable,
    build_bar,
)


@pytest.fixture(autouse=True)
def _isolated_pipeline():
    market_cache.clear()
    market_cache._inflight.clear()
    yield
    market_cache.clear()
    market_cache._inflight.clear()


class StubProvider:
    """Configurable provider module double implementing the manager protocol."""

    def __init__(self, key, *, creds=True, supports_fn=None, behavior=None):
        self.PROVIDER_KEY = key
        self._creds = creds
        self._supports_fn = supports_fn or (lambda request: True)
        self.calls = []
        self.behavior = behavior  # callable(request) -> result | Exception | None

    def has_credentials(self):
        return self._creds

    def supports(self, request):
        return self._supports_fn(request)

    def fetch_history(self, request):
        self.calls.append(request)
        outcome = self.behavior(request) if callable(self.behavior) else self.behavior
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return _result(self.PROVIDER_KEY, request)
        return outcome


def _result(provider, request, fetched_at="2026-03-09T10:00:00Z"):
    bars = []
    for index in range(3):
        bars.append(
            build_bar(
                datetime(2026, 1, 5 + index, 10, 0, tzinfo=timezone.utc),
                100 + index, 101 + index, 99 + index, 100.5 + index, 1000 + index,
            )
        )
    return ProviderResult(
        provider=provider,
        provider_symbol=f"{request.symbol}.{request.exchange}",
        bars=bars,
        fetched_at=fetched_at,
        source=f"{provider} source description",
        freshness="provider timestamp supplied",
        last_bar_timestamp=bars[-1]["timestamp"],
        chain=[provider],
    )


def _request(symbol="ALPHA", exchange="NSE", period="1mo", interval="1d"):
    return market_manager.build_request(symbol, exchange, period, interval)


class TestEligibility:
    def test_missing_credentials_leave_provider_out(self):
        stub = StubProvider("nokey", creds=False)
        manager = MarketManager([stub])
        with pytest.raises(NoProviderSucceeded, match="no API credential"):
            manager.get_history(_request())
        assert stub.calls == []

    def test_disabled_by_env_is_reported(self, monkeypatch):
        stub = StubProvider("gone")
        monkeypatch.setenv("CDF_ENABLE_GONE", "0")
        manager = MarketManager([stub])
        with pytest.raises(NoProviderSucceeded, match="disabled by configuration"):
            manager.get_history(_request())

    def test_capability_gating_skips_provider(self):
        stub = StubProvider(
            "limited", supports_fn=lambda request: request.interval == "1d"
        )
        manager = MarketManager([stub])
        with pytest.raises(NoProviderSucceeded, match="outside verified capabilities"):
            manager.get_history(_request(interval="5m"))

    def test_cooling_down_provider_is_skipped(self):
        stub = StubProvider("hot", behavior=ProviderRateLimited("quota over"))
        manager = MarketManager([stub])
        with pytest.raises(NoProviderSucceeded, match="Every eligible provider failed"):
            manager.get_history(_request(symbol="A"))
        with pytest.raises(NoProviderSucceeded, match="cooling down after recent failures"):
            manager.get_history(_request(symbol="B"))
        assert len(stub.calls) == 1  # second call never reached the cooling provider


class TestRouting:
    def test_first_available_prefers_registry_order(self, monkeypatch):
        first = StubProvider("aa")
        second = StubProvider("bb")
        third = StubProvider("cc")
        monkeypatch.setenv("CDF_MARKET_ROUTING", "first_available")
        manager = MarketManager([first, second, third])
        for index in range(4):
            outcome = manager.get_history(_request(symbol=f"S{index}"))
            assert outcome.result.provider == "aa"
        assert second.calls == [] and third.calls == []

    def test_round_robin_rotates_among_eligible(self, monkeypatch):
        first = StubProvider("aa")
        second = StubProvider("bb")
        monkeypatch.setenv("CDF_MARKET_ROUTING", "round_robin")
        manager = MarketManager([first, second])
        sequence = []
        for index in range(6):
            outcome = manager.get_history(_request(symbol=f"S{index}"))
            sequence.append(outcome.result.provider)
        assert sequence == ["aa", "bb", "aa", "bb", "aa", "bb"]
        assert len(first.calls) == 3 and len(second.calls) == 3

    def test_round_robin_locked_per_exchange_and_interval(self, monkeypatch):
        first = StubProvider("aa")
        second = StubProvider("bb")
        monkeypatch.setenv("CDF_MARKET_ROUTING", "round_robin")
        manager = MarketManager([first, second])
        # Same (exchange, interval) slot only rotates once per request
        first_outcome = manager.get_history(_request(symbol="X", interval="1d"))
        assert first_outcome.result.provider == "aa"
        # A different interval starts its own rotation slot
        other = manager.get_history(_request(symbol="X", interval="1wk"))
        assert other.result.provider == "aa"

    def test_weighted_distribution_is_deterministic(self, monkeypatch):
        monkeypatch.setenv("CDF_MARKET_ROUTING", "weighted")
        monkeypatch.setenv("CDF_PROVIDER_WEIGHT_AA", "60")
        monkeypatch.setenv("CDF_PROVIDER_WEIGHT_BB", "30")
        monkeypatch.setenv("CDF_PROVIDER_WEIGHT_CC", "10")
        first = StubProvider("aa")
        second = StubProvider("bb")
        third = StubProvider("cc")
        manager = MarketManager([first, second, third])
        chosen = []
        for index in range(10):
            outcome = manager.get_history(_request(symbol=f"S{index}"))
            chosen.append(outcome.result.provider)
        assert chosen.count("aa") == 6
        assert chosen.count("bb") == 3
        assert chosen.count("cc") == 1


class TestFallback:
    def test_failure_falls_through_to_next_provider(self):
        broken = StubProvider("aa", behavior=ProviderUnavailable("upstream down"))
        working = StubProvider("bb")
        manager = MarketManager([broken, working])
        outcome = manager.get_history(_request(symbol="X"))
        assert outcome.result.provider == "bb"
        assert outcome.chain == ["aa", "bb"]
        assert outcome.stale is False
        assert manager._health["bb"].total_successes == 1

    def test_not_supported_provider_still_falls_back(self):
        restricted = StubProvider("aa", behavior=ProviderNotSupported("plan lacks market"))
        working = StubProvider("bb")
        manager = MarketManager([restricted, working])
        outcome = manager.get_history(_request(symbol="X"))
        assert outcome.result.provider == "bb"

    def test_total_failure_reports_each_provider_error(self):
        broken_a = StubProvider("aa", behavior=ProviderUnavailable("dns broke"))
        broken_b = StubProvider("bb", behavior=ProviderRateLimited("25/day exhausted"))
        manager = MarketManager([broken_a, broken_b])
        with pytest.raises(NoProviderSucceeded) as excinfo:
            manager.get_history(_request(symbol="X"))
        message = str(excinfo.value)
        assert "aa: dns broke" in message
        assert "bb: 25/day exhausted" in message

    def test_unexpected_adapter_exception_is_contained(self):
        explodes = StubProvider("aa", behavior=RuntimeError("bug in adapter"))
        working = StubProvider("bb")
        manager = MarketManager([explodes, working])
        outcome = manager.get_history(_request(symbol="X"))
        assert outcome.result.provider == "bb"
        health = manager._health["aa"]
        assert health.total_failures == 1
        assert "unexpectedly" in (health.last_error or "")


class TestHealth:
    def test_rate_limit_cooldown_window(self):
        stub = StubProvider("aa", behavior=ProviderRateLimited("quota"))
        manager = MarketManager([stub])
        started = time_module.monotonic()
        with pytest.raises(NoProviderSucceeded):
            manager.get_history(_request(symbol="X"))
        health = manager._health["aa"]
        assert health.cooldown_reason == "quota/rate limit"
        assert health.cooldown_until >= started + market_manager.RATE_LIMIT_COOLDOWN_SECONDS - 1

    def test_auth_failure_disables_for_hours(self):
        stub = StubProvider("aa", behavior=ProviderAuthError("key rejected"))
        manager = MarketManager([stub])
        with pytest.raises(NoProviderSucceeded):
            manager.get_history(_request(symbol="X"))
        health = manager._health["aa"]
        assert health.cooldown_until >= time_module.monotonic() + 6 * 3600 - 1

    def test_third_consecutive_failure_triggers_backoff(self):
        behavior = ProviderUnavailable("still broken")
        stub = StubProvider("aa", behavior=behavior)
        manager = MarketManager([stub])
        for index in range(3):
            with pytest.raises(NoProviderSucceeded):
                manager.get_history(_request(symbol=f"S{index}"))
        health = manager._health["aa"]
        assert health.cooldown_reason == "repeated failures"
        assert health.cooldown_until >= time_module.monotonic() + market_manager.BASE_COOLDOWN_SECONDS - 1

    def test_success_resets_failure_streak(self):
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        outcome = manager.get_history(_request(symbol="S1"))
        health = manager._health["aa"]
        assert health.consecutive_failures == 0
        assert health.total_successes == 1
        assert health.avg_latency_ms is not None
        assert outcome.result.provider == "aa"

    def test_snapshot_reports_state(self):
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        manager.get_history(_request(symbol="S1"))
        snapshot = manager.snapshot()
        entry = snapshot["providers"][0]
        assert entry["key"] == "aa"
        assert entry["total_successes"] == 1
        assert entry["healthy"] is True
        assert snapshot["routing"] == "round_robin"


class TestSharedCacheBehaviour:
    def test_second_identical_request_served_from_cache(self):
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        request = _request(symbol="SAME")
        first = manager.get_history(request)
        second = manager.get_history(request)
        assert len(stub.calls) == 1             # second hit came from the cache
        assert second.result.fetched_at == first.result.fetched_at  # honest fetch time
        assert second.chain == ["aa"]

    def test_different_symbols_do_not_share_cache(self):
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        manager.get_history(_request(symbol="ONE"))
        manager.get_history(_request(symbol="TWO"))
        assert len(stub.calls) == 2

    def test_cache_disabled_refetches(self, monkeypatch):
        monkeypatch.setenv("CDF_MARKET_CACHE", "0")
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        manager.get_history(_request(symbol="SAME"))
        manager.get_history(_request(symbol="SAME"))
        assert len(stub.calls) == 2

    def test_concurrent_identical_requests_share_one_fetch(self):
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        original = stub.fetch_history

        def slow_fetch(request, _original=original):
            time_module.sleep(0.25)
            return _original(request)

        stub.fetch_history = slow_fetch  # type: ignore[method-assign]

        barrier = threading.Barrier(5)
        outcomes = []
        request = _request(symbol="RACE")
        request = market_manager.build_request("RACE", request.exchange, request.period, request.interval)

        def worker():
            barrier.wait()
            outcomes.append(manager.get_history(request))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert len(stub.calls) == 1
        unique_fetched = {outcome.result.fetched_at for outcome in outcomes}
        assert unique_fetched == {"2026-03-09T10:00:00Z"}
        assert all(isinstance(outcome, ManagedHistory) for outcome in outcomes)


class TestStalePolicy:
    def test_failed_refetch_serves_labelled_stale_entry(self, monkeypatch):
        # Freeze the cache clock; the manager's upstream attempts can fail fast.
        fake_clock = _FakeClock(time_module.monotonic())
        monkeypatch.setattr(market_cache, "time_module", SimpleNamespace(monotonic=fake_clock))
        monkeypatch.setattr(market_cache, "_ist_now", lambda: _monday_1100())

        stub = StubProvider("aa")
        manager = MarketManager([stub])
        request = _request(symbol="STALECASE")
        succeeded = manager.get_history(request)
        assert succeeded.stale is False

        # The provider now fails; the cache entry expires too.
        stub.behavior = ProviderUnavailable("upstream gone")
        fake_clock.advance(1000)  # > 900s in-session TTL, < 24h stale cap
        outcome = manager.get_history(request)
        assert outcome.stale is True
        assert outcome.result.provider == "aa"  # the previous provider's data
        assert market_cache.get(request_key(request)) is None

    def test_stale_policy_can_be_disabled(self, monkeypatch):
        fake_clock = _FakeClock(time_module.monotonic())
        monkeypatch.setattr(market_cache, "time_module", SimpleNamespace(monotonic=fake_clock))
        monkeypatch.setattr(market_cache, "_ist_now", lambda: _monday_1100())
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        request = _request(symbol="NOSTALE")
        assert manager.get_history(request).stale is False

        stub.behavior = ProviderUnavailable("upstream gone")
        fake_clock.advance(1000)
        monkeypatch.setenv("CDF_MARKET_STALE_FALLBACK", "0")
        with pytest.raises(NoProviderSucceeded):
            manager.get_history(request)

    def test_ancient_stale_entries_are_never_served(self, monkeypatch):
        fake_clock = _FakeClock(time_module.monotonic())
        monkeypatch.setattr(market_cache, "time_module", SimpleNamespace(monotonic=fake_clock))
        monkeypatch.setattr(market_cache, "_ist_now", lambda: _monday_1100())
        stub = StubProvider("aa")
        manager = MarketManager([stub])
        request = _request(symbol="ANCIENT")
        assert manager.get_history(request).stale is False

        stub.behavior = ProviderUnavailable("upstream gone")
        fake_clock.advance(30 * 3600)  # beyond the default 24h stale cap
        with pytest.raises(NoProviderSucceeded):
            manager.get_history(request)


class TestBarBudget:
    def test_estimates_match_interval_grain(self):
        month_5m = estimate_bars("1mo", "5m")                 # ~31*75 bars + buffer
        assert 1500 <= month_5m <= 2400
        assert estimate_bars("1y", "1d") in range(360, 380)   # ~365 daily bars
        # weekly bars are much fewer than daily bars for the same window
        assert estimate_bars("5y", "1wk") < estimate_bars("5y", "1d")
        assert estimate_bars("5y", "1wk") in range(230, 280)  # ~5y of weekly bars
        assert estimate_bars("max", "1d") <= 5000             # hard cap
        assert estimate_bars("ytd", "1d") >= 31               # worst-case floor


class TestMarketDataFacade:
    """End-to-end facade checks with a manager backed by stub providers."""

    def test_full_response_shape_and_chain(self, monkeypatch):
        stub = StubProvider("aa")
        _install_manager(monkeypatch, manager_for(stub))
        payload = fetch_history("Reliance", "NSE", "1mo", "1d")
        assert payload["symbol"] == "RELIANCE"
        assert payload["providerSymbol"] == "RELIANCE.NSE"
        assert payload["exchange"] == "NSE"
        assert payload["source"] == "aa source description"
        assert payload["interval"] == "1d"
        assert payload["fetchedAt"] == "2026-03-09T10:00:00Z"
        assert payload["lastDataTimestamp"] == payload["bars"][-1]["timestamp"]
        assert payload["providerChain"] == ["aa"]
        assert payload["bars"][0]["timestamp"] == "2026-01-05T10:00:00Z"
        assert "stale" not in payload

    def test_facade_failure_maps_to_market_data_unavailable(self, monkeypatch):
        broken = StubProvider("aa", behavior=ProviderUnavailable("no route to host"))
        _install_manager(monkeypatch, manager_for(broken))
        with pytest.raises(MarketDataUnavailable, match="no route to host"):
            fetch_history("RELIANCE", "NSE", "1mo", "1d")

    def test_facade_cache_hit_preserves_original_fetched_at(self, monkeypatch):
        stub = StubProvider("aa")
        _install_manager(monkeypatch, manager_for(stub))
        first = fetch_history("RELIANCE", "NSE", "1mo", "1d")
        second = fetch_history("RELIANCE", "NSE", "1mo", "1d")
        assert len(stub.calls) == 1
        assert second["fetchedAt"] == first["fetchedAt"]

    def test_facade_labels_stale_payloads(self, monkeypatch):
        fake_clock = _FakeClock(time_module.monotonic())
        monkeypatch.setattr(market_cache, "time_module", SimpleNamespace(monotonic=fake_clock))
        monkeypatch.setattr(market_cache, "_ist_now", lambda: _monday_1100())
        stub = StubProvider("aa")
        _install_manager(monkeypatch, manager_for(stub))
        assert fetch_history("RELIANCE", "NSE", "1mo", "1d") is not None

        stub.behavior = ProviderUnavailable("upstream gone")
        fake_clock.advance(1000)
        payload = fetch_history("RELIANCE", "NSE", "1mo", "1d")
        assert payload["stale"] is True
        assert payload["freshness"].startswith("stale:")
        assert payload["source"].endswith("STALE FALLBACK")

    def test_facade_keeps_provider_symbols_honest(self, monkeypatch):
        stub = StubProvider("bb")
        _install_manager(monkeypatch, manager_for(stub))
        payload = fetch_history("TATASTEEL", "BSE", "1mo", "1d")
        assert payload["providerSymbol"] == "TATASTEEL.BSE"
        assert payload["providerChain"] == ["bb"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def manager_for(*stubs):
    return MarketManager(list(stubs))


def _install_manager(monkeypatch, manager):
    """Replace the process manager singleton (restored after the test)."""
    monkeypatch.setattr(market_manager, "_manager", manager)


class _FakeClock:
    def __init__(self, start):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def _monday_1100():
    return datetime(2026, 3, 9, 11, 0, tzinfo=market_cache.IST)


def request_key(request):
    from market_cache import cache_key

    return cache_key(request.symbol, request.exchange, request.interval, request.period)
