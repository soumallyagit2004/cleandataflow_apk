"""Free-first historical market data provider using Yahoo Finance via yfinance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yfinance as yf


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
    """Raised when the upstream provider has no usable history."""


def fetch_history(symbol: str, exchange: str, period: str, interval: str) -> dict[str, Any]:
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
    qualified_symbol = clean_symbol if clean_symbol.endswith((".NS", ".BO")) else clean_symbol + EXCHANGE_SUFFIXES[exchange]

    try:
        frame = yf.Ticker(qualified_symbol).history(
            period=period, interval=interval, auto_adjust=False, timeout=12
        )
    except Exception as exc:
        raise MarketDataUnavailable(f"Yahoo Finance request failed: {exc}") from exc

    if frame is None or frame.empty:
        raise MarketDataUnavailable(f"No historical price data is available for {qualified_symbol}.")

    bars = []
    for stamp, row in frame.iterrows():
        def value(column: str):
            item = row.get(column)
            return None if item is None or item != item else float(item)

        timestamp = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        bars.append({
            "timestamp": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "open": value("Open"), "high": value("High"), "low": value("Low"),
            "close": value("Close"), "volume": value("Volume"),
        })

    fetched_at = datetime.now(timezone.utc)
    return {
        "symbol": clean_symbol,
        "providerSymbol": qualified_symbol,
        "exchange": exchange,
        "source": "Yahoo Finance via yfinance (free, delayed/interval-dependent; not tick-by-tick)",
        "interval": interval,
        "fetchedAt": fetched_at.isoformat().replace("+00:00", "Z"),
        "lastDataTimestamp": bars[-1]["timestamp"],
        "freshness": "provider timestamp supplied; delay varies by instrument and interval",
        "bars": bars,
    }
