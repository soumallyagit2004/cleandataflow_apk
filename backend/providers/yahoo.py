"""Yahoo Finance provider (the original, working Clean Data Flow source).

This adapter wraps the exact yfinance usage that ``market_data.fetch_history``
has always had — same symbol qualification, same period/interval semantics,
same timestamp convention and the same honest ``source``/``freshness``
strings — so existing behaviour is preserved bit for bit while the request can
now also be routed/filled by other providers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from providers.base import (
    DataRequest,
    ProviderResult,
    ProviderUnavailable,
    build_bar,
    clean_bars,
    iso_z,
)

PROVIDER_KEY = "yahoo"
SOURCE_DESCRIPTION = (
    "Yahoo Finance via yfinance (free, delayed/interval-dependent; not tick-by-tick)"
)
FRESHNESS_DESCRIPTION = (
    "provider timestamp supplied; delay varies by instrument and interval"
)


def has_credentials() -> bool:
    """Yahoo needs no API key: it is always configured.

    Shared by the manager's registry checks so the availability story stays
    simple: without a key Yahoo is the only enabled provider.
    """
    return True


def supports(request: DataRequest) -> bool:
    """Yahoo supports every exchange/period/interval the backend validates."""
    return True


def fetch_history(request: DataRequest) -> ProviderResult:
    """Fetch OHLCV via yfinance and normalise to the shared bar contract."""
    import yfinance as yf  # imported lazily so tests can monkeypatch cheaply

    from market_data import EXCHANGE_SUFFIXES

    qualified_symbol = request.symbol
    if not qualified_symbol.endswith((".NS", ".BO")):
        qualified_symbol = request.symbol + EXCHANGE_SUFFIXES[request.exchange]

    try:
        frame = yf.Ticker(qualified_symbol).history(
            period=request.period, interval=request.interval,
            auto_adjust=False, timeout=12,
        )
    except Exception as exc:
        raise ProviderUnavailable(f"Yahoo Finance request failed: {exc}") from exc

    if frame is None or frame.empty:
        raise ProviderUnavailable(
            f"No historical price data is available for {qualified_symbol}."
        )

    bars: list[dict[str, Any] | None] = []
    for stamp, row in frame.iterrows():
        def value(column: str):
            item = row.get(column)
            return None if item is None or item != item else float(item)

        timestamp = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        bars.append({
            "timestamp": iso_z(timestamp),
            "open": value("Open"), "high": value("High"), "low": value("Low"),
            "close": value("Close"), "volume": value("Volume"),
        })

    # Yahoo bars are produced by the same per-row mapping that shipped with the
    # original path; the shared validator only guards against corrupt candles
    # and can never invent values for a provider that already returned them.
    from datetime import datetime as _dt

    normalised = clean_bars(
        [
            build_bar(
                _dt.fromisoformat(bar["timestamp"].replace("Z", "+00:00")),
                bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"],
            )
            for bar in bars
        ]
    )

    if not normalised:
        # Every yfinance bar failed validation: refuse rather than truncate.
        raise ProviderUnavailable(
            f"Yahoo Finance returned no valid candles for {qualified_symbol}."
        )

    return ProviderResult(
        provider=PROVIDER_KEY,
        provider_symbol=qualified_symbol,
        bars=normalised,
        fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        source=SOURCE_DESCRIPTION,
        freshness=FRESHNESS_DESCRIPTION,
        last_bar_timestamp=normalised[-1]["timestamp"],
    )
