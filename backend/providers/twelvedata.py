"""Twelve Data provider adapter.

Verified provider facts (Twelve Data docs / pricing, checked against the API
documentation as of implementation):

* REST endpoint: ``GET https://api.twelvedata.com/time_series`` with
  ``symbol``, ``interval``, ``apikey`` plus ``exchange`` and ``outputsize``.
* Supported intervals: ``1min 5min 15min 30min 45min 1h 2h 4h 1day 1week 1month``.
* Free "Basic" plan: **8 API credits/minute, 800/day** — but it only includes
  **3 market groups (US stocks, forex, crypto)**. India (NSE/BSE) coverage
  requires the paid Ultra plan or higher, per the official plan table.

Therefore this adapter is capability-gated OFF for Indian exchanges by default
(exactly the user constraint: never assume unsupported coverage, never return
wrong data). Operators with a paid plan that genuinely covers India can enable
it with ``TWELVEDATA_ENABLE_INDIA=1`` — the adapter then routes NSE/BSE
requests and classifies plan-coverage errors at runtime, falling back to the
next provider on the same request.

Bar timestamps: ``datetime`` fields are epoch seconds (absolute instants) and
are parsed as such; date-only fields for daily bars are stamped at the
Yahoo-compatible Indian end-of-day boundary (18:30Z, i.e. 00:00 IST next day)
so daily candles line up on the chart regardless of provider; weekly/monthly
date-only values are stamped midnight UTC of the reported date.

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

PROVIDER_KEY = "twelvedata"
SOURCE_DESCRIPTION = (
    "Twelve Data (provider-verified interval coverage; Indian exchanges are "
    "plan-restricted and provider-side delayed; not tick-by-tick)"
)
FRESHNESS_DESCRIPTION = (
    "provider timestamp supplied; twelve data free-tier plans do not cover "
    "Indian exchanges, so this provider is only active when enabled for a "
    "supported plan"
)

BASE_URL = "https://api.twelvedata.com/time_series"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_PER_MINUTE = 8  # free "Basic" plan credit budget

# Contract intervals this provider can honour (verified table above).
SUPPORTED_INTERVALS: dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "1h",
    "1h": "1h",
    "1d": "1day",
    "1wk": "1week",
    "1mo": "1month",
}

HTTP_CLIENT_ERROR_CODES = {400, 401, 403, 405}
HTTP_RATE_LIMIT_CODES = {402, 404, 429}

_minute_lock = threading.Lock()
_minute_window: deque[float] = deque()


def _requests_per_minute() -> int:
    raw = (os.environ.get("TWELVEDATA_MAX_REQUESTS_PER_MINUTE") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PER_MINUTE
    return value if value > 0 else DEFAULT_MAX_PER_MINUTE


def has_credentials() -> bool:
    """Credentials present (never logged, never returned)."""
    return bool((os.environ.get("TWELVEDATA_API_KEY") or "").strip())


def india_enabled() -> bool:
    """Capability gate: NSE/BSE needs a plan that truly covers India."""
    raw = (os.environ.get("TWELVEDATA_ENABLE_INDIA") or "").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def supports(request: DataRequest) -> bool:
    """Eligibility for one concrete request (capability table, not guessing)."""
    if request.interval not in SUPPORTED_INTERVALS:
        return False
    if request.exchange not in {"NSE", "BSE"}:
        return False
    if not india_enabled():
        return False  # free plan does not cover India; stays off unless paid
    return True


def _reserve_minute_credit() -> None:
    """Client-side sliding-window limiter (8 credits/min on the free plan)."""
    limit = _requests_per_minute()
    now = time_module.monotonic()
    with _minute_lock:
        while _minute_window and now - _minute_window[0] > 60.0:
            _minute_window.popleft()
        if len(_minute_window) >= limit:
            raise ProviderRateLimited(
                "Twelve Data per-minute credit limit reached; deferring to other providers"
            )
        _minute_window.append(now)


def _classify(payload: dict[str, Any], status_code: int) -> ProviderError:
    """Map a Twelve Data error-shaped payload onto the shared error taxonomy."""
    code = payload.get("code")
    message = str(payload.get("message", "")) or "no message supplied"
    if code in HTTP_CLIENT_ERROR_CODES or (isinstance(code, int) and code < 400):
        if "invalid" in message.lower() and "key" in message.lower():
            return ProviderAuthError("Twelve Data rejected the configured API key")
        if "not subscribed" in message.lower() or "plan" in message.lower():
            return ProviderNotSupported(
                "Twelve Data plan does not cover this market; provider skipped"
            )
        return ProviderUnavailable(
            f"Twelve Data rejected the request (code {code or status_code})"
        )
    if code in HTTP_RATE_LIMIT_CODES:
        return ProviderRateLimited(
            f"Twelve Data quota/limit reached (code {code or status_code})"
        )
    return ProviderUnavailable(
        f"Twelve Data returned an error payload (code {code!r}, HTTP {status_code})"
    )


def _parse_http_error(status_code: int, message: str) -> ProviderError:
    if status_code in HTTP_CLIENT_ERROR_CODES:
        return ProviderAuthError(
            f"Twelve Data rejected the configuration (HTTP {status_code})"
        )
    if status_code in HTTP_RATE_LIMIT_CODES:
        return ProviderRateLimited(f"Twelve Data limit reached (HTTP {status_code})")
    return ProviderUnavailable(
        f"Twelve Data returned HTTP {status_code}: {message[:120]}"
    )


def _stamp_daily_date_only(stamp: datetime, interval: str) -> datetime:
    """Align provider date stamps with the app's existing daily-bar convention."""
    daily_key = SUPPORTED_INTERVALS[interval] == "1day"
    if daily_key and stamp.time() == datetime.min.time() and stamp.utcoffset() == timedelta(0):
        midnight_utc = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if midnight_utc == stamp:
            # provider reported a bare session date: 18:30Z == end of IST day
            return stamp + timedelta(hours=18, minutes=30)
    return stamp


def fetch_history(request: DataRequest) -> ProviderResult:
    """Call the documented endpoint and normalise the response."""
    api_key = (os.environ.get("TWELVEDATA_API_KEY") or "").strip()
    if not api_key:
        raise ProviderAuthError("Twelve Data adapter enabled without a configured API key")

    params = {
        "symbol": request.symbol,
        "exchange": request.exchange,
        "interval": SUPPORTED_INTERVALS[request.interval],
        "outputsize": min(max(request.bars_estimate + 20, 30), 5000),
        "timezone": "UTC",
        "apikey": api_key,
    }

    _reserve_minute_credit()
    try:
        response = httpx.get(BASE_URL, params=params, timeout=DEFAULT_TIMEOUT_SECONDS)
    except Exception as exc:
        raise ProviderUnavailable(
            f"Twelve Data request failed: {type(exc).__name__}"
        ) from exc

    if response.status_code != 200:
        raise _parse_http_error(response.status_code, response.text[:120])

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderUnavailable("Twelve Data returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise ProviderUnavailable("Twelve Data returned an unexpected payload type")
    if payload.get("status") == "error" or "code" in payload:
        raise _classify(payload, response.status_code)

    values = payload.get("values")
    if not isinstance(values, list) or not values:
        raise ProviderUnavailable(
            f"No historical price data is available for {request.symbol} from Twelve Data."
        )

    candidates: list[dict[str, Any] | None] = [None] * len(values)
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            continue
        stamp = parse_timestamp(raw.get("datetime"))
        if stamp is not None:
            stamp = _stamp_daily_date_only(stamp, request.interval)
        candidates[index] = build_bar(
            stamp,
            raw.get("open"), raw.get("high"), raw.get("low"),
            raw.get("close"), raw.get("volume"),
        )

    bars = clean_bars(candidates)
    if not bars:
        raise ProviderUnavailable(
            "Twelve Data returned no valid candles; treating as unavailable"
        )

    return ProviderResult(
        provider=PROVIDER_KEY,
        provider_symbol=params["symbol"],
        bars=bars,
        fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        source=SOURCE_DESCRIPTION,
        freshness=FRESHNESS_DESCRIPTION,
        last_bar_timestamp=bars[-1]["timestamp"],
    )
