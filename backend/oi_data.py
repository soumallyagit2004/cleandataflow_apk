"""Open Interest (OI) data from the official NSE F&O end-of-day bhavcopy.

Why this source
---------------
NSE publishes a free, credential-free daily "UDiFF" bhavcopy for the Futures &
Options segment. It contains genuine per-contract Open Interest, traded volume
and settlement prices supplied by the exchange itself:

    https://nsearchives.nseindia.com/content/fo/
        BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip

Limitations (stated honestly, never hidden):
  * It is an **end-of-day** snapshot, not live or intraday data.
  * The archive publishes only after the session; weekends/holidays have no file.
  * The URL layout is an NSE archive convention and can change without notice.
  * This module covers **NSE** F&O only. BSE derivatives are not included.
  * Open interest is an exchange-reported statistic. It is not proof of
    manipulation and not a trading signal.

No authentication, cookies, scraping of UI pages, or fabricated values are used.
When data is unavailable the caller receives ``available: False`` plus a clear
reason instead of invented numbers.
"""

from __future__ import annotations

import csv
import io
import math
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

BHAVCOPY_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/fo/"
    "BhavCopy_NSE_FO_0_0_0_{stamp}_F_0000.csv.zip"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

SOURCE_DESCRIPTION = (
    "NSE India official F&O bhavcopy (UDiFF), end-of-day"
)
FRESHNESS_NOTE = (
    "End-of-day snapshot published by NSE after the session; "
    "not live, intraday or tick-by-tick."
)
GENERAL_NOTE = (
    "Open interest is an exchange-reported end-of-day statistic. It is not "
    "proof of market manipulation and not a trading signal."
)
UNIT_NOTE = (
    "Values are reported exactly as published by NSE in the bhavcopy "
    "OpnIntrst and TtlTradgVol columns. NSE does not state a per-row unit in "
    "this file, so use each contract's published lot_size and underlying "
    "price when interpreting. Aggregates sum listed contracts and are not "
    "comparable across different symbols."
)

DEFAULT_MAX_LOOKBACK_DAYS = 7
CACHE_TTL_SECONDS = 300


class OIDataUnavailable(Exception):
    """Raised internally when a bhavcopy cannot be downloaded or decoded."""


# Small cache of successful summaries for the default network path only.
# Key: (trade_date_iso, symbol). Tests that inject a downloader bypass it.
_summary_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    """Clear the in-memory summary cache (used by tests)."""
    _summary_cache.clear()


def bhavcopy_url(trade_date: date) -> str:
    """Return the official UDiFF bhavcopy archive URL for a trading date."""
    return BHAVCOPY_URL_TEMPLATE.format(stamp=trade_date.strftime("%Y%m%d"))


def _clean_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise ValueError("symbol must be a string")
    clean = symbol.strip().upper()
    if (
        not clean
        or len(clean) > 30
        or not all(c.isalnum() or c in "-^.&" for c in clean)
    ):
        raise ValueError("symbol contains unsupported characters")
    return clean


def _validate_exchange(exchange: str) -> str:
    if not isinstance(exchange, str):
        raise ValueError("exchange must be NSE or BSE")
    clean = exchange.strip().upper()
    if clean not in {"NSE", "BSE"}:
        raise ValueError("exchange must be NSE or BSE")
    return clean


def _parse_date(as_of: str | date | None) -> date | None:
    if as_of is None:
        return None
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    if not isinstance(as_of, str):
        raise ValueError("date must be an ISO date string (YYYY-MM-DD)")
    try:
        return datetime.strptime(as_of.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("date must be an ISO date string (YYYY-MM-DD)") from exc


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-", "NA", "N/A", "null", "None"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(round(number))


def parse_fo_bhavcopy(csv_text: str, symbol: str | None = None) -> list[dict[str, Any]]:
    """Parse a UDiFF F&O bhavcopy CSV into normalized contract dicts.

    Pure and offline: no network access. ``symbol`` filters rows by the
    exchange ticker (e.g. ``RELIANCE`` or ``NIFTY``). Contract fields are kept
    as supplied by NSE; missing/blank numbers become ``None``.
    """
    if not csv_text or not csv_text.strip():
        return []

    wanted = _clean_symbol(symbol) if symbol is not None else None

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return []

    contracts: list[dict[str, Any]] = []
    for raw in reader:
        ticker = (raw.get("TckrSymb") or "").strip().upper()
        if not ticker:
            continue
        if wanted is not None and ticker != wanted:
            continue

        contracts.append(
            {
                "symbol": ticker,
                "instrument_type": (raw.get("FinInstrmTp") or "").strip() or None,
                "instrument_name": (raw.get("FinInstrmNm") or "").strip() or None,
                "trade_date": (raw.get("TradDt") or "").strip() or None,
                "expiry": (raw.get("XpryDt") or "").strip() or None,
                "strike": _to_float(raw.get("StrkPric")),
                "option_type": (raw.get("OptnTp") or "").strip() or None,
                "open_interest": _to_int(raw.get("OpnIntrst")),
                "change_in_open_interest": _to_int(raw.get("ChngInOpnIntrst")),
                "volume": _to_int(raw.get("TtlTradgVol")),
                "close": _to_float(raw.get("ClsPric")),
                "last": _to_float(raw.get("LastPric")),
                "previous_close": _to_float(raw.get("PrvsClsgPric")),
                "underlying_price": _to_float(raw.get("UndrlygPric")),
                "lot_size": _to_int(raw.get("NewBrdLotQty")),
            }
        )
    return contracts


def _sum_optional(values: Iterable[int | None]) -> int:
    return sum(value for value in values if value is not None)


def summarize_open_interest(
    contracts: list[dict[str, Any]], symbol: str, exchange: str = "NSE"
) -> dict[str, Any]:
    """Aggregate per-contract rows into an honest OI summary.

    Returns ``available: False`` when there are no contracts. No number is
    invented: absent values stay ``None`` and aggregates only sum what NSE
    actually reported.
    """
    clean_symbol = _clean_symbol(symbol)

    if not contracts:
        return {
            "available": False,
            "symbol": clean_symbol,
            "exchange": exchange,
            "asOf": None,
            "source": SOURCE_DESCRIPTION,
            "freshness": FRESHNESS_NOTE,
            "open_interest": None,
            "contracts": [],
            "contract_count": 0,
            "reason": (
                f"No NSE F&O contracts were found for {clean_symbol} in the "
                f"bhavcopy. The symbol may have no derivatives or use a "
                f"different exchange ticker."
            ),
            "note": GENERAL_NOTE,
            "unit_note": UNIT_NOTE,
        }

    futures = [c for c in contracts if c.get("option_type") is None]
    calls = [c for c in contracts if c.get("option_type") == "CE"]
    puts = [c for c in contracts if c.get("option_type") == "PE"]

    total_oi = _sum_optional(c.get("open_interest") for c in contracts)
    change_total = _sum_optional(c.get("change_in_open_interest") for c in contracts)
    total_volume = _sum_optional(c.get("volume") for c in contracts)
    call_oi = _sum_optional(c.get("open_interest") for c in calls)
    put_oi = _sum_optional(c.get("open_interest") for c in puts)
    futures_oi = _sum_optional(c.get("open_interest") for c in futures)

    underlying_prices = [
        c.get("underlying_price")
        for c in contracts
        if c.get("underlying_price") is not None
    ]

    trade_dates = sorted(
        {c.get("trade_date") for c in contracts if c.get("trade_date")}
    )
    expiries = sorted({c.get("expiry") for c in contracts if c.get("expiry")})
    instrument_types = sorted(
        {c.get("instrument_type") for c in contracts if c.get("instrument_type")}
    )

    put_call_ratio = None
    if call_oi > 0:
        put_call_ratio = round(put_oi / call_oi, 4)

    ordered = sorted(
        contracts,
        key=lambda c: (
            c.get("expiry") or "",
            c.get("instrument_type") or "",
            c.get("option_type") or "",
            c.get("strike") if c.get("strike") is not None else 0.0,
        ),
    )

    return {
        "available": True,
        "symbol": clean_symbol,
        "exchange": exchange,
        "asOf": trade_dates[-1] if trade_dates else None,
        "source": SOURCE_DESCRIPTION,
        "freshness": FRESHNESS_NOTE,
        "open_interest": {
            "total": total_oi,
            "futures": futures_oi,
            "call_options": call_oi,
            "put_options": put_oi,
            "put_call_ratio": put_call_ratio,
            "change_in_open_interest_total": change_total,
            "total_volume": total_volume,
            "underlying_price": (
                round(underlying_prices[-1], 4) if underlying_prices else None
            ),
        },
        "expiries": expiries,
        "instrument_types": instrument_types,
        "contracts": ordered,
        "contract_count": len(ordered),
        "note": GENERAL_NOTE,
        "unit_note": UNIT_NOTE,
    }


def _download_bhavcopy_text(trade_date: date, timeout: int = 25) -> str:
    """Download and unzip one official bhavcopy CSV. Raises OIDataUnavailable."""
    url = bhavcopy_url(trade_date)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise OIDataUnavailable(f"NSE archive returned HTTP {status}")
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise OIDataUnavailable(
            f"NSE archive returned HTTP {exc.code} for {trade_date.isoformat()}"
        ) from exc
    except urllib.error.URLError as exc:
        raise OIDataUnavailable(
            f"Cannot reach the NSE archive: {exc.reason}"
        ) from exc
    except OIDataUnavailable:
        raise
    except Exception as exc:  # network/decoding failures stay honest
        raise OIDataUnavailable(
            f"Downloading the NSE bhavcopy failed: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise OIDataUnavailable("NSE bhavcopy archive contained no CSV file")
            with archive.open(names[0]) as handle:
                return handle.read().decode("utf-8-sig")
    except OIDataUnavailable:
        raise
    except Exception as exc:
        raise OIDataUnavailable(
            f"Decoding the NSE bhavcopy archive failed: {type(exc).__name__}: {exc}"
        ) from exc


def _candidate_dates(
    as_of: date | None, today: date, max_lookback_days: int
) -> list[date]:
    """Dates to try, newest first. Weekends are skipped, explicit dates are not."""
    if as_of is not None:
        return [as_of]

    candidates: list[date] = []
    for offset in range(max_lookback_days + 1):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:  # Saturday/Sunday
            continue
        candidates.append(candidate)
    return candidates


def _unavailable(
    symbol: str,
    exchange: str,
    reason: str,
    attempted_dates: list[str],
    last_error: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "symbol": symbol,
        "exchange": exchange,
        "asOf": as_of,
        "source": SOURCE_DESCRIPTION,
        "fetchedAt": _now_iso(),
        "freshness": FRESHNESS_NOTE,
        "open_interest": None,
        "contracts": [],
        "contract_count": 0,
        "attempted_dates": attempted_dates,
        "reason": reason,
        "note": GENERAL_NOTE,
        "unit_note": UNIT_NOTE,
    }
    if last_error:
        result["last_error"] = last_error
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_open_interest(
    symbol: str,
    exchange: str = "NSE",
    as_of: str | date | None = None,
    max_lookback_days: int = DEFAULT_MAX_LOOKBACK_DAYS,
    today: date | None = None,
    downloader: Callable[[date], str] | None = None,
) -> dict[str, Any]:
    """Fetch genuine NSE F&O Open Interest for one symbol.

    Parameters
    ----------
    symbol:
        Exchange ticker such as ``RELIANCE`` or ``NIFTY``.
    exchange:
        ``NSE`` (supported). ``BSE`` returns ``available: False`` because this
        free archive covers NSE F&O only.
    as_of:
        Optional explicit trade date (``YYYY-MM-DD``). When omitted the most
        recent published bhavcopy within ``max_lookback_days`` is used.
    today:
        Injectable "today" for deterministic tests (defaults to UTC today).
    downloader:
        Injectable CSV-text downloader for offline tests. When omitted the
        official NSE archive is downloaded and results are briefly cached.

    Returns
    -------
    dict
        Always a stable dict. ``available`` is ``False`` with a ``reason``
        when data cannot be obtained honestly.
    """
    clean_symbol = _clean_symbol(symbol)
    clean_exchange = _validate_exchange(exchange)
    requested_date = _parse_date(as_of)
    base_today = today or datetime.now(timezone.utc).date()

    if not isinstance(max_lookback_days, int) or isinstance(max_lookback_days, bool):
        raise ValueError("max_lookback_days must be an integer")
    if max_lookback_days < 0 or max_lookback_days > 30:
        raise ValueError("max_lookback_days must be between 0 and 30")

    if clean_exchange == "BSE":
        return _unavailable(
            clean_symbol,
            clean_exchange,
            (
                "This integration uses the official NSE F&O bhavcopy, which "
                "covers NSE derivatives only. BSE derivative Open Interest is "
                "not available from this free, credential-free source."
            ),
            attempted_dates=[],
            as_of=requested_date.isoformat() if requested_date else None,
        )

    use_cache = downloader is None
    effective_downloader = downloader or _download_bhavcopy_text
    candidates = _candidate_dates(requested_date, base_today, max_lookback_days)
    attempted: list[str] = []
    last_error: str | None = None

    for candidate in candidates:
        stamp = candidate.isoformat()
        attempted.append(stamp)

        if use_cache:
            cached = _summary_cache.get((stamp, clean_symbol))
            if cached is not None and cached[0] > time.time():
                return cached[1]

        try:
            csv_text = effective_downloader(candidate)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        contracts = parse_fo_bhavcopy(csv_text, symbol=clean_symbol)
        if not contracts:
            last_error = (
                f"Bhavcopy for {stamp} contained no contracts for {clean_symbol}"
            )
            continue

        summary = summarize_open_interest(contracts, clean_symbol, clean_exchange)
        summary["fetchedAt"] = _now_iso()
        summary["sourceUrl"] = bhavcopy_url(candidate)
        summary["attempted_dates"] = attempted

        if use_cache:
            _summary_cache[(stamp, clean_symbol)] = (
                time.time() + CACHE_TTL_SECONDS,
                summary,
            )
        return summary

    if requested_date is not None:
        reason = (
            f"No NSE F&O bhavcopy with contracts for {clean_symbol} was "
            f"available for {requested_date.isoformat()}. The date may be a "
            f"weekend/holiday, the file may not be published yet, or the "
            f"symbol may have no derivatives."
        )
    else:
        reason = (
            f"No NSE F&O bhavcopy with contracts for {clean_symbol} was found "
            f"in the last {max_lookback_days} calendar days. The symbol may "
            f"have no derivatives, or the NSE archive may be unreachable."
        )

    return _unavailable(
        clean_symbol,
        clean_exchange,
        reason,
        attempted_dates=attempted,
        last_error=last_error,
        as_of=requested_date.isoformat() if requested_date else None,
    )
