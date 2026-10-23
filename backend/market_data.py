"""Provider-routed historical market data for Clean Data Flow.

Historically this module fetched everything directly from Yahoo Finance via
yfinance. It now keeps the exact same public surface — ``fetch_history``,
``validate_period_interval``, ``MarketDataUnavailable``, the supported
period/interval tables — while delegating the fetch to the central manager
(``market_manager``), which can distribute requests across multiple verified
providers with shared caching and honest fallback:

* Yahoo Finance via yfinance — always available, unchanged behaviour.
* Twelve Data — adapter implemented; free tier does not cover Indian
  exchanges, so it stays disabled for NSE/BSE until configured for a
  plan that genuinely supports them (``TWELVEDATA_ENABLE_INDIA=1``).
* Alpha Vantage — adapter implemented; free tier verified for BSE
  daily/weekly/monthly (25 requests/day). NSE stays disabled pending
  verification (``ALPHAVANTAGE_ENABLE_NSE=1`` opts in for testing).

The response shape for callers does not change: same keys, same bar format,
same exception types (``ValueError`` for user errors, ``MarketDataUnavailable``
for upstream failures). Cache hits honestly keep the original ``fetchedAt``;
stale entries are always labelled as stale.
"""

from __future__ import annotations

from typing import Any

# Kept as a module attribute for backward compatibility: existing tests patch
# "market_data.yf.Ticker", and the provider adapter resolves the shared
# yfinance module through it, so the patch surface is unchanged.
import yfinance as yf  # noqa: F401

EXCHANGE_SUFFIXES = {"NSE": ".NS", "BSE": ".BO"}
ALLOWED_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
ALLOWED_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}

# Yahoo Finance intraday history limits (documented provider behaviour):
#   1m                         -> at most 7 days
#   2m, 5m, 15m, 30m, 90m      -> at most 60 days
#   60m / 1h                   -> at most 730 days
# Daily and longer intervals have no comparable restriction here.
INTERVAL_PERIODS = {
    "1m": {"1d", "5d"},
    "2m": {"1d", "5d", "1mo"},
    "5m": {"1d", "5d", "1mo"},
    "15m": {"1d", "5d", "1mo"},
    "30m": {"1d", "5d", "1mo"},
    "90m": {"1d", "5d", "1mo"},
    "60m": {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"},
    "1h": {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"},
}


def supported_periods_for_interval(interval: str) -> set[str]:
    """Return the periods this backend supports for an interval."""
    return INTERVAL_PERIODS.get(interval, set(ALLOWED_PERIODS))


def validate_period_interval(period: str, interval: str) -> None:
    """Raise ValueError for an unknown or provider-incompatible pair."""
    if period not in ALLOWED_PERIODS:
        raise ValueError(
            f"period must be one of: {', '.join(sorted(ALLOWED_PERIODS))}"
        )
    if interval not in ALLOWED_INTERVALS:
        raise ValueError(
            f"interval must be one of: {', '.join(sorted(ALLOWED_INTERVALS))}"
        )

    supported = supported_periods_for_interval(interval)
    if period not in supported:
        allowed = ", ".join(
            item for item in sorted(ALLOWED_PERIODS) if item in supported
        )
        raise ValueError(
            f"period '{period}' is not supported with interval '{interval}'. "
            f"Supported periods for {interval}: {allowed}"
        )


class MarketDataUnavailable(Exception):
    """Raised when the upstream providers have no usable history."""


def fetch_history(symbol: str, exchange: str, period: str, interval: str) -> dict[str, Any]:
    """Fetch provider-sourced OHLCV for one validated request.

    The heavy lifting (routing, shared cache, single-flight, fallback) lives in
    ``market_manager``; this facade keeps the historical contract: identical
    parameter validation, identical response keys, identical exception types.
    """
    exchange = exchange.upper()
    if exchange not in EXCHANGE_SUFFIXES:
        raise ValueError("exchange must be NSE or BSE")
    if period not in ALLOWED_PERIODS:
        raise ValueError(f"period must be one of: {', '.join(sorted(ALLOWED_PERIODS))}")
    if interval not in ALLOWED_INTERVALS:
        raise ValueError(f"interval must be one of: {', '.join(sorted(ALLOWED_INTERVALS))}")
    validate_period_interval(period, interval)

    clean_symbol = symbol.strip().upper()
    if not clean_symbol or len(clean_symbol) > 30 or not all(c.isalnum() or c in "-^." for c in clean_symbol):
        raise ValueError("symbol contains unsupported characters")

    # Imported lazily so the manager (and provider registry) can be monkey-
    # patched by tests without import-order coupling.
    from market_manager import NoProviderSucceeded, build_request, get_manager

    request = build_request(clean_symbol, exchange, period, interval)
    try:
        managed = get_manager().get_history(request)
    except NoProviderSucceeded as exc:
        # Preserve the historical upstream-failure contract for main.py:
        # MarketDataUnavailable -> 502 with an honest reason, never fabricated
        # data and never a 500 from an internal error type.
        raise MarketDataUnavailable(str(exc)) from exc
    response = managed.result.to_response(clean_symbol)
    response["exchange"] = exchange
    response["interval"] = interval

    if managed.stale:
        response["stale"] = True
        response["freshness"] = (
            "stale: all configured providers failed this request, so the last "
            "successful cache entry is served; it was fetched "
            f"{response['fetchedAt']} and must not be treated as live"
        )
        response["source"] = f"{response['source']} — STALE FALLBACK"
    return response
