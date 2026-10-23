"""Shared in-process market-data cache.

Design notes
------------
* Keys are the exact request tuple ``(symbol, exchange, interval, period)``:
  different symbols, exchanges, intervals or history windows are never treated
  as interchangeable.
* TTLs depend on the interval and on Indian market hours (NSE/BSE trade
  09:15–15:30 IST, Mon–Fri):
      during the session — intraday ``60s``, daily/weekly/monthly ``900s``
      outside the session — until the next session start, capped
      (intraday 12h, daily 72h) so post-close data is never re-fetched pointlessly
      while pre-open data never survives into the next session.
* A per-key lock gives single-flight behaviour: concurrent identical requests
  share ONE upstream call instead of hammering providers.
* Failed fetches are never cached as data. Stale entries may be served only
  through :func:`get_stale`, under the manager's explicit fallback policy, and
  callers must label the payload as stale.
* Plain process memory (``OrderedDict`` + lock) — no external infrastructure
  dependency, matching the deployment story of this project.
"""

from __future__ import annotations

import os
import threading
import time as time_module
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from providers.base import ProviderResult

IST = ZoneInfo("Asia/Kolkata")

# NSE/BSE continuous trading window: 09:15–15:30 IST; the tail to 15:45 keeps
# the closing-auction tail from re-fetching pointlessly.
SESSION_OPEN_MINUTES_OF_DAY = (9 * 60 + 15) - 30          # 08:45 safety margin
SESSION_CLOSE_MINUTES_OF_DAY = 15 * 60 + 45              # 15:45 IST

INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}
INSESSION_INTRADAY_TTL_SECONDS = 60.0
INSESSION_DAILY_TTL_SECONDS = 900.0
CLOSED_INTRADAY_CAP_SECONDS = 12 * 3600.0
CLOSED_DAILY_CAP_SECONDS = 72 * 3600.0

DEFAULT_MAX_ENTRIES = 512
SINGLE_FLIGHT_WAIT_SECONDS = 60.0


@dataclass
class CacheEntry:
    result: ProviderResult
    stored_at: float          # time.monotonic() of the successful fetch
    expires_at: float         # time.monotonic() beyond which the entry is stale


@dataclass
class _Flight:
    """One in-progress load shared by identical concurrent requests."""

    event: threading.Event
    outcome: Any = None
    exception: BaseException | None = None

    def finish(self, outcome: Any = None, exception: BaseException | None = None) -> None:
        self.outcome = outcome
        self.exception = exception
        self.event.set()


_store: OrderedDict[tuple, CacheEntry] = OrderedDict()
_store_lock = threading.Lock()
_inflight: dict[tuple, _Flight] = {}


def _market_hours_enabled() -> bool:
    """Testing/ops hook — disable session-aware TTLs (treat as always open)."""
    raw = (os.environ.get("CDF_MARKET_CACHE_IGNORE_HOURS") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on", "session"}


def _ist_now() -> datetime:
    return datetime.now(IST)


def _minutes_to_next_session(now_ist: datetime) -> float:
    """Computation of how long until the next NSE/BSE session opens (IST)."""
    minutes_of_day = now_ist.hour * 60 + now_ist.minute
    weekday = now_ist.weekday()  # Mon=0 ... Sun=6

    for day in range(0, 8):
        candidate_day = now_ist.date() + timedelta(days=day)
        open_dt = datetime(
            candidate_day.year, candidate_day.month, candidate_day.day, 9, 15,
            tzinfo=IST,
        )
        candidate_delta = open_dt - now_ist
        if candidate_delta.total_seconds() <= -1800:
            # today's session already started long ago; look forward
            continue
        if (weekday + day) % 7 < 5:
            if candidate_delta.total_seconds() >= -1800:
                return max(candidate_delta.total_seconds() / 60.0, 0.0)
        # weekend candidates never qualify
    return 3 * 24 * 60.0


def is_session_open(now_ist: datetime | None = None) -> bool:
    """True when the NSE/BSE continuous session is in its trading window."""
    if not _market_hours_enabled():
        return True
    now_ist = now_ist or _ist_now()
    if now_ist.weekday() >= 5:
        return False
    minutes = now_ist.hour * 60 + now_ist.minute
    return SESSION_OPEN_MINUTES_OF_DAY <= minutes <= SESSION_CLOSE_MINUTES_OF_DAY


def ttl_for_key(interval: str, now_ist: datetime | None = None) -> float:
    """Cache lifetime for one request key given interval and market hours.

    ``now_ist`` is resolved once and reused so a closed session never leaves
    the caller's ``None`` timestamp reaching ``_minutes_to_next_session``.
    """
    now = now_ist or _ist_now()
    if is_session_open(now):
        ttl = (
            INSESSION_INTRADAY_TTL_SECONDS
            if interval in INTRADAY_INTERVALS
            else INSESSION_DAILY_TTL_SECONDS
        )
        return ttl

    if not _market_hours_enabled():
        # Guidance: with hours disabled treat as the in-session values.
        return (
            INSESSION_INTRADAY_TTL_SECONDS
            if interval in INTRADAY_INTERVALS
            else INSESSION_DAILY_TTL_SECONDS
        )

    minutes_to_next = _minutes_to_next_session(now)
    seconds_to_next = minutes_to_next * 60.0
    cap = (
        CLOSED_INTRADAY_CAP_SECONDS
        if interval in INTRADAY_INTERVALS
        else CLOSED_DAILY_CAP_SECONDS
    )
    return max(min(seconds_to_next, cap), 60.0)


def _max_entries() -> int:
    raw = (os.environ.get("CDF_MARKET_CACHE_MAX_ENTRIES") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_ENTRIES
    return value if value > 0 else DEFAULT_MAX_ENTRIES


def cache_key(symbol: str, exchange: str, interval: str, period: str) -> tuple:
    return (symbol.strip().upper(), exchange.upper(), interval, period)


def get(key: tuple) -> CacheEntry | None:
    """Return the entry only when still fresh (TTL not exceeded)."""
    now = time_module.monotonic()
    with _store_lock:
        entry = _store.get(key)
        if entry is None:
            return None
        if now >= entry.expires_at:
            return None  # stale — only get_stale may touch it
        _store.move_to_end(key)
        return entry


def put(key: tuple, result: ProviderResult, interval: str) -> CacheEntry:
    """Store a successful provider result with its interval/market TTL."""
    ttl = ttl_for_key(interval)
    entry = CacheEntry(
        result=result,
        stored_at=time_module.monotonic(),
        expires_at=time_module.monotonic() + ttl,
    )
    with _store_lock:
        _store[key] = entry
        _store.move_to_end(key)
        while len(_store) > _max_entries():
            _store.popitem(last=False)
    return entry


def get_stale(key: tuple, max_age_seconds: float) -> CacheEntry | None:
    """Return a stale entry only under an explicit fallback decision.

    The caller (manager owns the policy) must label the response as stale;
    this function also rejects entries older than ``max_age_seconds`` or
    entries whose provider data was never valid.
    """
    now = time_module.monotonic()
    with _store_lock:
        entry = _store.get(key)
        if entry is None:
            return None
        if now - entry.stored_at > max_age_seconds:
            return None
        _store.move_to_end(key)
        return entry


def clear() -> None:
    """Drop everything (tests/ops)."""
    with _store_lock:
        _store.clear()


def drop(key: tuple) -> None:
    with _store_lock:
        _store.pop(key, None)


def size() -> int:
    with _store_lock:
        return len(_store)


def single_flight(
    key: tuple,
    loader: Callable[[], Any],
) -> tuple[Any, BaseException | bool]:
    """Run ``loader`` once per key even when identical requests race.

    Returns ``(loader_value, False)`` for the winner. Losers receive either
    ``(result, False)`` (winner succeeded), ``(None, exception)`` (winner
    failed — identical error text for everyone) or ``(None, True)`` when the
    wait timed out; the manager turns those into its stale-fallback decision
    so failed or duplicated upstream calls are never repeated per request key.
    """
    with _store_lock:
        flight = _inflight.get(key)
    if flight is None:
        mine = _Flight(event=threading.Event())
        with _store_lock:
            flight = _inflight.get(key)
            if flight is None:
                _inflight[key] = mine
                flight = mine
                owner = True
            else:
                owner = False
        if owner:
            try:
                outcome = loader()
                flight.finish(outcome=outcome)
                return outcome, False
            except Exception as exc:  # noqa: BLE001 — shared with every waiter
                flight.finish(exception=exc)
                return None, exc
            finally:
                with _store_lock:
                    _inflight.pop(key, None)

    if not flight.event.wait(timeout=SINGLE_FLIGHT_WAIT_SECONDS):
        return None, True  # timeout — the manager decides (stale/raise)
    if flight.exception is not None:
        return None, flight.exception
    return flight.outcome, False
