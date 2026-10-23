"""Provider-independent request/response contract and strict OHLCV normalisation.

Every provider adapter converts its raw upstream payload into ``ProviderResult``
whose bars all satisfy :func:`build_bars` validation. Nothing here fabricates
values: missing fields stay ``None`` (exactly like the pre-existing Yahoo path)
and invalid candles are rejected, never repaired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base class for provider failures. All are retryable via fallback."""


class ProviderUnavailable(ProviderError):
    """Network, timeout, HTTP, decode or empty-response failure."""


class ProviderAuthError(ProviderError):
    """The credential is missing/invalid. Disable the provider until restart."""


class ProviderRateLimited(ProviderError):
    """The provider's quota or rate limit is exhausted (this call)."""


class ProviderNotSupported(ProviderError):
    """The request is outside the provider's verified capabilities.

    This includes plan-restricted markets (e.g. Twelve Data free tier does
    not cover India) and symbols the provider does not list.
    """


class ProviderBadData(ProviderError):
    """The response could not be validated as real OHLCV candles."""


# ---------------------------------------------------------------------------
# Request / result models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataRequest:
    """One canonical market-data request, already shape-validated upstream."""

    symbol: str          # clean bare symbol, e.g. RELIANCE
    exchange: str        # NSE or BSE (upper)
    period: str          # e.g. 3mo, 1y, 5y
    interval: str        # e.g. 1d
    bars_estimate: int   # approximate number of daily-equivalent bars wanted


@dataclass
class ProviderResult:
    """A successful, normalised provider response."""

    provider: str                     # stable provider key, e.g. "yahoo"
    provider_symbol: str              # exactly what was sent upstream
    bars: list[dict[str, Any]]
    fetched_at: str                   # UTC ISO instant of the successful call
    source: str                       # human description shown in the API
    freshness: str                    # human description of delay expectations
    last_bar_timestamp: str | None = None
    chain: list[str] = field(default_factory=list)  # providers attempted, in order

    def to_response(self, symbol_out: str) -> dict[str, Any]:
        """Shape the result as the fetch_history contract dict.

        ``source`` keeps the exact legacy Yahoo wording when Yahoo succeeds so
        existing consumers see an unchanged value; other providers use the same
        key with an equally honest description.
        """
        payload: dict[str, Any] = {
            "symbol": symbol_out,
            "providerSymbol": self.provider_symbol,
            "exchange": None,     # filled by the caller (exchange is validated there)
            "source": self.source,
            "interval": None,     # filled by the caller
            "fetchedAt": self.fetched_at,
            "lastDataTimestamp": self.last_bar_timestamp or (
                self.bars[-1]["timestamp"] if self.bars else None
            ),
            "freshness": self.freshness,
            "bars": self.bars,
            "providerChain": list(self.chain),
        }
        return payload


# ---------------------------------------------------------------------------
# Normalisation helpers (shared by every adapter)
# ---------------------------------------------------------------------------


def to_finite_float(value: Any) -> float | None:
    """Convert numbers/strings to finite floats; anything else is ``None``.

    Handles provider quirks (comma thousands separators) without inventing
    values: unparsable input yields ``None``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if number == number and abs(number) != float("inf") else None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def parse_timestamp(raw: Any) -> datetime | None:
    """Parse provider timestamps into aware UTC datetimes.

    Accepted: epoch seconds (int/numeric string), epoch milliseconds,
    ISO-8601 instants (``Z`` or offset), and bare dates (``YYYY-MM-DD``;
    interpreted as midnight UTC — adapters shift daily bars to the
    Yahoo-compatible IST close boundary before calling this).
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, dict):
        return None
    value = str(raw).strip()
    if not value:
        return None
    value = value.replace("Z", "+00:00")

    # numeric epochs first (str.isnumeric-ish, incl. negative-free decimals)
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_z(stamp: datetime) -> str:
    """Format an aware datetime as the contract's ``...Z`` string."""
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_bar(
    stamp: datetime | None,
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    volume: Any,
) -> dict[str, Any] | None:
    """Validated bar in the exact response format, or ``None`` when corrupt.

    Validation rules (same spirit as the Yahoo path):
    * the timestamp must parse;
    * present prices must be finite and internally consistent
      (``high >= max(open, close)``, ``low <= min(open, close)``,
      ``high >= low``, all strictly positive);
    * present volume must be finite and non-negative;
    * genuinely missing (``None``) fields are preserved as ``None`` because the
      existing analysis pipeline handles trailing partial candles honestly.
    """
    if stamp is None:
        return None

    def check_price(raw: Any) -> float | None | str:
        number = to_finite_float(raw)
        if number is None and raw is not None:
            return "bad"
        if number is None:
            return None
        if number <= 0:
            return "bad"
        return number

    parsed = {
        "open": check_price(open_),
        "high": check_price(high),
        "low": check_price(low),
        "close": check_price(close),
    }
    if "bad" in parsed.values():
        return None

    o, h, l, c = (parsed["open"], parsed["high"], parsed["low"], parsed["close"])
    # price consistency: high must bound every present price from above,
    # low must bound open/close from below (h >= l is a special case of this).
    if h is not None:
        for other in (o, l, c):
            if other is not None and other > h:
                return None
    if l is not None:
        for other in (o, c):
            if other is not None and other < l:
                return None

    volume_number = to_finite_float(volume)
    if volume_number is not None and volume_number < 0:
        return None

    return {
        "timestamp": iso_z(stamp),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume_number,
    }


def clean_bars(candidates: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    """Drop corrupt candles, deduplicate timestamps and sort ascending.

    Different providers are never merged: ``candidates`` is always one
    provider's payload for one request.
    """
    bars: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        stamp = candidate["timestamp"]
        if stamp in seen:
            continue
        seen.add(stamp)
        bars.append(candidate)
    bars.sort(key=lambda bar: bar["timestamp"])
    return bars
