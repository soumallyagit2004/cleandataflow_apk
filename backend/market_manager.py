"""Central, provider-independent market-data manager.

Responsibilities
----------------
* Accept the canonical (symbol, exchange, period, interval) request.
* Route it to exactly ONE eligible provider per request — quota-aware,
  round-robin or weighted, deterministic and testable — never fetch the same
  data from every provider, and never treat different symbols, exchanges,
  intervals or history windows as interchangeable anywhere in the pipeline.
* Fall back to the next eligible provider when one fails (sequential recovery
  fallback; parallel calls are not needed for this flow).
* Track provider failures, rate limits, quotas (client-side budgets where the
  provider doesn't give a counter), latency and per-provider health so
  repeated calls avoid known-bad providers transiently.
* Own the explicit stale-fallback policy: when *no* provider could fulfil the
  request and stale-fallback is enabled, serve the last fresh cache entry
  clearly labelled as stale instead of silently pretending providers work.
"""

from __future__ import annotations

import os
import threading
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import market_cache
import providers as provider_registry
from market_cache import cache_key
from providers.base import (
    DataRequest,
    ProviderAuthError,
    ProviderNotSupported,
    ProviderRateLimited,
    ProviderResult,
    ProviderUnavailable,
)

# ---------------------------------------------------------------------------
# Honest error surfaced by the manager when every eligible provider failed
# ---------------------------------------------------------------------------


class NoProviderSucceeded(Exception):
    """No eligible provider could return usable data for this request."""


# ---------------------------------------------------------------------------
# Period/interval arithmetic shared by adapters (bar-count budgeting)
# ---------------------------------------------------------------------------

_PERIOD_DAYS = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 183,
    "1y": 365, "2y": 730, "5y": 1826, "10y": 3652, "ytd": 0, "max": 3652,
}

_INTERVAL_BARS_PER_DAY = {
    "1m": 375.0, "2m": 187.5, "5m": 75.0, "15m": 25.0, "30m": 13.0,
    "60m": 7.0, "90m": 5.0, "1h": 7.0,
    "1d": 1.0, "5d": 0.25, "1wk": 0.143, "1mo": 0.034, "3mo": 0.011,
}

_MAX_BAR_BUDGET = 5000


def estimate_bars(period: str, interval: str) -> int:
    """Rough bar-count budget used for provider ``outputsize`` planning."""
    if period == "ytd":
        today = datetime.now(timezone.utc)
        january_first = today.replace(month=1, day=1, hour=0, minute=0)
        days = max((today - january_first).days, 31)
    else:
        days = _PERIOD_DAYS.get(period, 183)
    return min(int(days * _INTERVAL_BARS_PER_DAY.get(interval, 1.0)) + 5, _MAX_BAR_BUDGET)


# ---------------------------------------------------------------------------
# Health tracking
# ---------------------------------------------------------------------------

BASE_COOLDOWN_SECONDS = 30.0
MAX_COOLDOWN_SECONDS = 300.0
RATE_LIMIT_COOLDOWN_SECONDS = 120.0
AUTH_COOLDOWN_SECONDS = 6 * 3600.0
FAILURE_THRESHOLD_FOR_COOLDOWN = 3


@dataclass
class ProviderHealth:
    """Mutable per-provider health for this process."""

    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    cooldown_until: float = 0.0
    cooldown_reason: str | None = None
    last_latency_ms: float | None = None
    avg_latency_ms: float | None = None
    last_error: str | None = None

    def record_failure(self, exc: Exception, now: float) -> None:
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_error = str(exc)

        if isinstance(exc, ProviderAuthError):
            self._cooldown(now, AUTH_COOLDOWN_SECONDS, "authentication rejected")
        elif isinstance(exc, ProviderRateLimited):
            self._cooldown(now, RATE_LIMIT_COOLDOWN_SECONDS, "quota/rate limit")
        elif self.consecutive_failures >= FAILURE_THRESHOLD_FOR_COOLDOWN:
            backoff = min(
                BASE_COOLDOWN_SECONDS * (2 ** (self.consecutive_failures - FAILURE_THRESHOLD_FOR_COOLDOWN)),
                MAX_COOLDOWN_SECONDS,
            )
            self._cooldown(now, backoff, "repeated failures")

    def record_success(self, elapsed_ms: float, now: float) -> None:
        self.total_successes += 1
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.cooldown_reason = None
        self.last_latency_ms = elapsed_ms
        if self.avg_latency_ms is None:
            self.avg_latency_ms = elapsed_ms
        else:
            self.avg_latency_ms = round(0.7 * self.avg_latency_ms + 0.3 * elapsed_ms, 2)

    def _cooldown(self, now: float, seconds: float, reason: str) -> None:
        until = now + seconds
        if until > self.cooldown_until:
            self.cooldown_until = now + seconds
            self.cooldown_reason = reason

    def healthy(self, now: float) -> bool:
        return now >= self.cooldown_until


# ---------------------------------------------------------------------------
# Provider entry + manager
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {"yahoo": 60, "twelvedata": 25, "alphavantage": 15}


class ProviderModule(Protocol):
    PROVIDER_KEY: str
    def has_credentials(self) -> bool: ...
    def supports(self, request: DataRequest) -> bool: ...
    def fetch_history(self, request: DataRequest) -> ProviderResult: ...


@dataclass
class _ProviderEntry:
    module: ProviderModule
    health: ProviderHealth = field(default_factory=ProviderHealth)

    @property
    def key(self) -> str:
        return self.module.PROVIDER_KEY


def _flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return default


def weight_for(provider_key: str) -> int:
    raw = (os.environ.get(f"CDF_PROVIDER_WEIGHT_{provider_key.upper()}") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WEIGHTS.get(provider_key, 0)
    return value if value >= 0 else DEFAULT_WEIGHTS.get(provider_key, 0)


def _provider_enabled(provider_key: str) -> bool:
    names = {
        "yahoo": "CDF_ENABLE_YAHOO",
        "twelvedata": "CDF_ENABLE_TWELVEDATA",
        "alphavantage": "CDF_ENABLE_ALPHAVANTAGE",
    }
    return _flag(names.get(provider_key, "CDF_ENABLE_" + provider_key.upper()), True)


def build_request(symbol: str, exchange: str, period: str, interval: str) -> DataRequest:
    """Canonical request; the facade has already validated symbol/period/interval."""
    return DataRequest(
        symbol=symbol,
        exchange=exchange,
        period=period,
        interval=interval,
        bars_estimate=estimate_bars(period, interval),
    )


@dataclass
class ManagedHistory:
    """Manager output consumed by the market_data facade."""

    result: ProviderResult
    stale: bool = False
    chain: list[str] = field(default_factory=list)


class MarketManager:
    """Routes requests to provider adapters with quota-aware distribution."""

    def __init__(self, provider_modules: list[ProviderModule] | None = None) -> None:
        self._entries_list: list[_ProviderEntry] = [
            _ProviderEntry(module=module)
            for module in (provider_modules if provider_modules is not None else list(provider_registry.registry))
        ]
        # Health objects live on the persistent entries so every path shares them.
        self._health: dict[str, ProviderHealth] = {
            entry.key: entry.health for entry in self._entries_list
        }
        self._rotation: dict[tuple[str, str], int] = {}
        self._rotation_lock = threading.Lock()

    # -- introspection ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Diagnostics: providers, health, routing mode (test/docs support)."""
        now = time_module.monotonic()
        return {
            "routing": self.routing_mode(),
            "providers": [
                {
                    "key": entry.key,
                    "enabled": _provider_enabled(entry.key),
                    "credentials": entry.module.has_credentials(),
                    "healthy": entry.health.healthy(now),
                    "consecutive_failures": entry.health.consecutive_failures,
                    "total_successes": entry.health.total_successes,
                    "total_failures": entry.health.total_failures,
                    "avg_latency_ms": entry.health.avg_latency_ms,
                    "cooldown_reason": entry.health.cooldown_reason,
                }
                for entry in self._entries_list
            ],
        }

    # -- routing ------------------------------------------------------------

    def routing_mode(self) -> str:
        raw = (os.environ.get("CDF_MARKET_ROUTING") or "").strip().lower()
        return raw if raw in {"round_robin", "weighted", "first_available"} else "round_robin"

    def _eligible(self, request: DataRequest) -> list[_ProviderEntry]:
        now = time_module.monotonic()
        eligible: list[_ProviderEntry] = []
        for entry in self._entries_list:
            module = entry.module
            key = entry.key
            if not _provider_enabled(key):
                continue
            try:
                configured = module.has_credentials()
            except Exception:  # noqa: BLE001 — a broken adapter must not crash routing
                configured = False
            if not configured:
                continue
            try:
                supported = module.supports(request)
            except Exception:  # noqa: BLE001
                supported = False
            if not supported:
                continue
            if not entry.health.healthy(now):
                continue
            eligible.append(entry)
        return eligible

    def _ordered(self, request: DataRequest) -> list[_ProviderEntry]:
        eligible = self._eligible(request)
        if not eligible:
            return []
        mode = self.routing_mode()

        if mode == "first_available":
            return eligible

        rotation_index = self._next_rotation_index(request)
        if mode == "round_robin":
            return eligible[rotation_index % len(eligible):] + eligible[: rotation_index % len(eligible)]

        # weighted: deterministic weighted round-robin walk over the pool
        weights = [max(weight_for(entry.key), 1) for entry in eligible]
        divisor = min(weights)
        repeats = [max(w // divisor, 1) for w in weights]
        pool: list[_ProviderEntry] = []
        for entry, repeat in zip(eligible, repeats):
            pool.extend([entry] * repeat)
        for _ in range(rotation_index % len(pool)):
            pool.append(pool.pop(0))
        # collapse adjacent duplicates so a provider is never tried twice in a row
        seen: set[str] = set()
        ordered: list[_ProviderEntry] = []
        for entry in pool:
            if entry.key in seen:
                continue
            seen.add(entry.key)
            ordered.append(entry)
        return ordered

    def _next_rotation_index(self, request: DataRequest) -> int:
        rot_key = (request.exchange, request.interval)
        with self._rotation_lock:
            current = self._rotation.get(rot_key, 0)
            self._rotation[rot_key] = current + 1
        return current

    # -- shared-cache plumbing ----------------------------------------------

    def caching_enabled(self) -> bool:
        return _flag("CDF_MARKET_CACHE", True)

    def stale_fallback_enabled(self) -> bool:
        return _flag("CDF_MARKET_STALE_FALLBACK", True)

    def stale_max_age_seconds(self) -> float:
        raw = (os.environ.get("CDF_MARKET_STALE_MAX_HOURS") or "").strip()
        try:
            hours = float(raw)
        except ValueError:
            return 24.0 * 3600.0
        return max(hours, 0.0) * 3600.0

    # -- main entry point (used by the market_data facade) ------------------

    def get_history(self, request: DataRequest) -> ManagedHistory:
        key = cache_key(request.symbol, request.exchange, request.interval, request.period)
        caching = self.caching_enabled()

        if caching:
            entry = market_cache.get(key)
            if entry is not None:
                outcome = ManagedHistory(result=entry.result, chain=[entry.result.provider])
                return outcome
            result, shared = market_cache.single_flight(
                key, lambda: self._route_and_store(key, request)
            )
        else:
            # Cache disabled: upstream calls are still deduplicated per key,
            # but there is nothing to fall back to and nothing is stored.
            result, shared = market_cache.single_flight(
                key, lambda: self._route_only(request)
            )

        if shared is False and result is not None:
            return ManagedHistory(result=result, chain=list(result.chain))
        if isinstance(shared, BaseException) and shared is not False:
            # The winning flight already attempted every provider; sharing the
            # identical outcome prevents repeated upstream calls for losers.
            outcome = self._stale_or_raise(key, request, str(shared), use_cache=caching)
            return outcome
        if shared is True:
            # Wait timed out: another request is still fetching — policy decides.
            outcome = self._stale_or_raise(
                key, request, "another request is still fetching", use_cache=caching
            )
            return outcome
        raise NoProviderSucceeded("market request inflight returned no outcome")

    def _stale_or_raise(
        self,
        key: tuple,
        request: DataRequest,
        last_error: str,
        use_cache: bool = True,
    ) -> ManagedHistory:
        if use_cache and self.stale_fallback_enabled():
            entry = market_cache.get_stale(key, self.stale_max_age_seconds())
            if entry is not None:
                return ManagedHistory(result=entry.result, stale=True, chain=[entry.result.provider])
        raise NoProviderSucceeded(
            "No enabled market-data provider could return usable history for "
            f"{request.symbol} ({request.exchange}, {request.period}/{request.interval}). "
            f"Upstream reason: {last_error}"
        )

    def _route_and_store(self, key: tuple, request: DataRequest) -> ProviderResult:
        result = self._route(request)
        if self.caching_enabled():
            market_cache.put(key, result, request.interval)
        return result

    def _route_only(self, request: DataRequest) -> ProviderResult:
        return self._route(request)

    def _route(self, request: DataRequest) -> ProviderResult:
        ordered = self._ordered(request)
        if not ordered:
            self._raise_no_eligible(request)

        chain: list[str] = []
        errors: list[str] = []
        for entry in ordered:
            chain.append(entry.key)
            started = time_module.perf_counter()
            try:
                result = entry.module.fetch_history(request)
            except (ProviderAuthError, ProviderRateLimited, ProviderNotSupported) as exc:
                errors.append(f"{entry.key}: {exc}")
                entry.health.record_failure(exc, time_module.monotonic())
                continue
            except ProviderUnavailable as exc:
                errors.append(f"{entry.key}: {exc}")
                entry.health.record_failure(exc, time_module.monotonic())
                continue
            except Exception as exc:  # noqa: BLE001 — an unexpected bug must not crash the route
                wrapped = ProviderUnavailable(
                    f"{entry.key} adapter failed unexpectedly: {type(exc).__name__}"
                )
                errors.append(f"{entry.key}: {wrapped}")
                entry.health.record_failure(wrapped, time_module.monotonic())
                continue

            elapsed_ms = (time_module.perf_counter() - started) * 1000.0
            entry.health.record_success(elapsed_ms, time_module.monotonic())
            result.chain = list(chain)
            return result

        message = "; ".join(errors) if errors else "no provider attempts recorded"
        raise NoProviderSucceeded(
            "Every eligible provider failed: " + message
        )

    def _raise_no_eligible(self, request: DataRequest) -> None:
        disabled = []
        for entry in self._entries_list:
            module = entry.module
            key = entry.key
            if not _provider_enabled(key):
                disabled.append(f"{key}: disabled by configuration")
            elif not module.has_credentials():
                disabled.append(f"{key}: no API credential configured")
            elif not module.supports(request):
                disabled.append(f"{key}: request outside verified capabilities")
            else:
                disabled.append(f"{key}: cooling down after recent failures")
        raise NoProviderSucceeded(
            "no eligible provider for "
            f"{request.symbol}/{request.exchange} at interval {request.interval}: "
            + "; ".join(disabled)
        )

    # -- test/ops helpers ----------------------------------------------------

    def reset_health(self) -> None:
        self._health = {entry.key: ProviderHealth() for entry in self._entries_list}
        for entry in self._entries_list:
            entry.health = self._health[entry.key]
        self._rotation = {}


# ---------------------------------------------------------------------------
# Process-wide manager singleton + helpers for the facade and tests
# ---------------------------------------------------------------------------

_manager: MarketManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> MarketManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = MarketManager()
        return _manager


def reset_manager() -> MarketManager:
    """Build a fresh manager (tests/ops); the singleton is replaced."""
    global _manager
    with _manager_lock:
        _manager = MarketManager()
        return _manager
