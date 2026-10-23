"""Alpha Vantage provider adapter.

Verified provider facts (Alpha Vantage documentation/support, checked as of
implementation):

* REST endpoint: ``GET https://www.alphavantage.co/query`` with ``function``,
  ``symbol`` and ``apikey``.
* Free tier: **25 API requests per day, 5 per minute** (official support page).
* Official India examples cover **BSE** (``symbol=RELIANCE.BSE``). An ``.NSE``
  suffix route is implemented but capability-gated OFF by default because
  NSE coverage could not be verified and third-party reports describe NSE
  requests failing — the app therefore never sends unverified NSE traffic
  there unless the operator explicitly opts in with
  ``ALPHAVANTAGE_ENABLE_NSE=1`` for testing.
* Only daily/weekly/monthly time series are enabled for this provider.
  Intraday history for Indian symbols (``TIME_SERIES_INTRADAY`` trailing
  windows, premium rules per month) is NOT offered on this adapter' free tier
  and stays disabled; short-history honesty beats questionable coverage.

Error mapping (from the documented payload contract):
* ``{"Error Message": ...}``          -> symbol/request failure (bad fetch)
* ``{"Note": ...}``                   -> per-minute/25-per-day rate limit
* ``{"Information": ...}``            -> premium-only / limit notices

Bar timestamps: daily responses are bare session dates, stamped at the
Yahoo-compatible Indian end-of-day boundary (18:30Z = 00:00 IST next day);
weekly/monthly are stamped midnight UTC of the reported date.

No API key is ever included in raised error messages, logs or responses.
"""

from __future__ import annotations

import json
import os
import threading
import time as time_module
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from providers.base import (
    DataRequest,
    ProviderAuthError,
    ProviderNotSupported,
    ProviderRateLimited,
    ProviderResult,
    ProviderUnavailable,
    build_bar,
    clean_bars,
    parse_timestamp,
)

PROVIDER_KEY = "alphavantage"
SOURCE_DESCRIPTION = (
    "Alpha Vantage (free tier: BSE daily/weekly/monthly history; 25 "
    "requests/day; end-of-day, delayed; not tick-by-tick)"
)
FRESHNESS_DESCRIPTION = (
    "provider timestamp supplied; end-of-day history with provider-side delay"
)

BASE_URL = "https://www.alphavantage.co/query"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_PER_DAY = 25
DEFAULT_MAX_PER_MINUTE = 5

# Alpha Vantage resets its daily allowance at midnight US/Eastern time.
_AV_TZ = ZoneInfo("America/New_York")

HTTP_FUNCTIONS: dict[str, str] = {
    "1d": "TIME_SERIES_DAILY",
    "1wk": "TIME_SERIES_WEEKLY",
    "1mo": "TIME_SERIES_MONTHLY",
}
# Intraday functions exist but stay capability-gated off for this adapter.
INTRADAY_FUNCTIONS: dict[str, str] = {
    "1m": "TIME_SERIES_INTRADAY",
    "5m": "TIME_SERIES_INTRADAY",
    "15m": "TIME_SERIES_INTRADAY",
    "30m": "TIME_SERIES_INTRADAY",
    "60m": "TIME_SERIES_INTRADAY",
    "1h": "TIME_SERIES_INTRADAY",
}

_daily_lock = threading.Lock()
_minute_lock = threading.Lock()
_daily_count = 0
_daily_day_key: str | None = None
_minute_window: deque[float] = deque()


def _requests_per_day() -> int:
    raw = (os.environ.get("ALPHAVANTAGE_MAX_REQUESTS_PER_DAY") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PER_DAY
    return value if value > 0 else DEFAULT_MAX_PER_DAY


def _requests_per_minute() -> int:
    raw = (os.environ.get("ALPHAVANTAGE_MAX_REQUESTS_PER_MINUTE") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PER_MINUTE
    return value if value > 0 else DEFAULT_MAX_PER_MINUTE


def has_credentials() -> bool:
    """Credentials present (never logged, never returned)."""
    return bool((os.environ.get("ALPHAVANTAGE_API_KEY") or "").strip())


def nse_enabled() -> bool:
    """NSE route may only be enabled after an operator verifies live access."""
    raw = (os.environ.get("ALPHAVANTAGE_ENABLE_NSE") or "").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def intraday_enabled() -> bool:
    """Intraday history stays off unless an operator confirms their plan."""
    raw = (os.environ.get("ALPHAVANTAGE_ENABLE_INTRADAY") or "").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def supports(request: DataRequest) -> bool:
    """Eligibility for one concrete request (capability table, not guessing)."""
    if request.exchange == "BSE":
        interval_ok = request.interval in HTTP_FUNCTIONS or (
            request.interval in INTRADAY_FUNCTIONS and intraday_enabled()
        )
    elif request.exchange == "NSE":
        interval_ok = nse_enabled() and request.interval in HTTP_FUNCTIONS
    else:
        return False
    return interval_ok


def _reserve_quota() -> None:
    """Client-side budget: 25/day (midnight ET reset) and 5/min sliding window."""
    global _daily_count, _daily_day_key
    now = time_module.monotonic()
    day_key = datetime.now(_AV_TZ).date().isoformat()

    with _minute_lock:
        while _minute_window and now - _minute_window[0] > 60.0:
            _minute_window.popleft()
        if len(_minute_window) >= _requests_per_minute():
            raise ProviderRateLimited(
                "Alpha Vantage per-minute limit reached; deferring to other providers"
            )
        _minute_window.append(now)

    with _daily_lock:
        if _daily_day_key != day_key:
            _daily_count = 0
            _daily_day_key = day_key
        if _daily_count >= _requests_per_day():
            raise ProviderRateLimited(
                "Alpha Vantage daily quota exhausted; deferring to other providers until it resets"
            )


def _record_spend() -> None:
    """Charge one API credit for an actual upstream HTTP call."""
    global _daily_count
    with _daily_lock:
        _daily_count += 1


def _classify(payload: dict[str, Any]) -> ProviderError | None:
    """Map the documented error payloads onto the shared taxonomy."""
    message = str(payload.get("Error Message") or "")
    if message:
        lowered = message.lower()
        if ("api key" in lowered or "apikey" in lowered) and (
            "invalid" in lowered or "missing" in lowered
        ):
            return ProviderAuthError("Alpha Vantage rejected the configured API key")
        return ProviderUnavailable(
            f"Alpha Vantage rejected the request: {message[:120]}"
        )
    note = str(payload.get("Note") or "")
    if note:
        return ProviderRateLimited(
            "Alpha Vantage rate limit message received; deferring to other providers"
        )
    info = str(payload.get("Information") or "")
    if info:
        lowered = info.lower()
        if "premium" in lowered or "not available" in lowered:
            return ProviderNotSupported(
                "Alpha Vantage free tier does not provide this dataset; provider skipped"
            )
        return ProviderRateLimited(
            "Alpha Vantage limit notice received; deferring to other providers"
        )
    return None


def _stamp_date_only(stamp: datetime, interval: str) -> datetime:
    """Date-only daily bars use the app's existing ist end-of-day convention."""
    midnight = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    if midnight == stamp and stamp.utcoffset() == timedelta(0):
        if interval == "1d":
            return stamp + timedelta(hours=18, minutes=30)  # 24:00 IST of that day
        if interval in {"1wk", "1mo"}:
            return stamp  # midnight UTC of the reported period date
    return stamp


def fetch_history(request: DataRequest) -> ProviderResult:
    """Call the documented endpoint and normalise the response."""
    api_key = (os.environ.get("ALPHAVANTAGE_API_KEY") or "").strip()
    if not api_key:
        raise ProviderAuthError(
            "Alpha Vantage adapter enabled without a configured API key"
        )

    suffix = {"BSE": ".BSE", "NSE": ".NSE"}[request.exchange]
    qualified_symbol = f"{request.symbol}{suffix}"
    function = HTTP_FUNCTIONS[request.interval]
    params = {
        "function": function,
        "symbol": qualified_symbol,
        # compact = last 100 points; full = ~20 years. Only ask for the large
        # payload when the request genuinely needs more than 100 bars.
        "outputsize": "full" if request.bars_estimate > 100 else "compact",
        "datatype": "json",
        "apikey": api_key,
    }

    _reserve_quota()
    try:
        response = httpx.get(BASE_URL, params=params, timeout=DEFAULT_TIMEOUT_SECONDS)
    except Exception as exc:
        raise ProviderUnavailable(
            f"Alpha Vantage request failed: {type(exc).__name__}"
        ) from exc
    _record_spend()

    if response.status_code != 200:
        raise ProviderUnavailable(
            f"Alpha Vantage returned HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderUnavailable("Alpha Vantage returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise ProviderUnavailable("Alpha Vantage returned an unexpected payload type")
    failure = _classify(payload)
    if failure is not None:
        raise failure

    series_block = {
        "1d": "Time Series (Daily)",
        "1wk": "Weekly Time Series",
        "1mo": "Monthly Time Series",
    }[request.interval]
    series = payload.get(series_block)
    if not isinstance(series, dict) or not series:
        raise ProviderUnavailable(
            f"No historical price data is available for {qualified_symbol} "
            f"from Alpha Vantage."
        )

    candidates: list[dict[str, Any] | None] = [None] * len(series)
    for index, (raw_date, row) in enumerate(series.items()):
        if not isinstance(row, dict):
            continue
        stamp = parse_timestamp(raw_date)
        if stamp is not None:
            stamp = _stamp_date_only(stamp, request.interval)
        candidates[index] = build_bar(
            stamp,
            row.get("1. open"), row.get("2. high"), row.get("3. low"),
            row.get("4. close"), row.get("5. volume"),
        )

    bars = clean_bars(candidates)
    if not bars:
        raise ProviderUnavailable(
            "Alpha Vantage returned no valid candles; treating as unavailable"
        )

    return ProviderResult(
        provider=PROVIDER_KEY,
        provider_symbol=qualified_symbol,
        bars=bars,
        fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        source=SOURCE_DESCRIPTION,
        freshness=FRESHNESS_DESCRIPTION,
        last_bar_timestamp=bars[-1]["timestamp"],
    )
