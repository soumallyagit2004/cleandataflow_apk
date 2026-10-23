from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import os
import re
import urllib.request
import urllib.error
import json
import time
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Dict, Any, Tuple
from market_data import (
    fetch_history,
    validate_period_interval,
    MarketDataUnavailable,
)
from news_data import fetch_market_news, NewsUnavailable
from oi_data import fetch_open_interest
from analysis import (
    analyze_bars,
    calculate_ema_deviation,
    calculate_divergence,
    calculate_volume_spike,
    calculate_volatility_anomaly,
    calculate_lof_anomaly,
    calculate_autoencoder_anomaly,
    calculate_transformer_anomaly,
    calculate_historical_anomalies,
)

app = FastAPI()


def _parse_allowed_origins(raw: str | None) -> list[str]:
    """Parse the ALLOWED_ORIGINS environment variable into a CORS origin list.

    Unset/empty keeps the previous development behaviour (any origin allowed).
    Production deployments should set it explicitly, for example:

        ALLOWED_ORIGINS=https://clean-data-flow.vercel.app,https://www.example.com

    Entries are comma-separated and a trailing slash is ignored.
    """
    if not raw or not raw.strip():
        return ["*"]
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    # This app exposes public read-only GET data; credentials are never needed.
    allow_origins=_parse_allowed_origins(os.environ.get("ALLOWED_ORIGINS")),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

NSE_BASE = "https://www.nseindia.com/api"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.nseindia.com/",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# Simple in-memory cache: {url: (data, expiry_time)}
_cache: Dict[str, Tuple[Any, float]] = {}
CACHE_TTL_SECONDS = 60  # cache responses for 1 minute to avoid aggressive polling


def _fetch_nse(path: str) -> Any:
    """Fetch JSON from NSE India with error handling and brief caching."""
    url = f"{NSE_BASE}{path}"

    # Return cached copy if still fresh
    now = time.time()
    if url in _cache:
        cached_data, expiry = _cache[url]
        if now < expiry:
            return cached_data

    req = urllib.request.Request(url, headers=NSE_HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"NSE India returned status {resp.status}")
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise HTTPException(status_code=502, detail="NSE India access forbidden (403). Try again later.")
        if e.code == 429:
            raise HTTPException(status_code=502, detail="NSE India rate limit exceeded. Try again later.")
        raise HTTPException(status_code=502, detail=f"NSE India error: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach NSE India: {e.reason}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="NSE India returned invalid JSON.")
    except HTTPException:
        raise
    except Exception as e:
        # Avoid forwarding raw exception internals (URLs, paths) to callers.
        raise HTTPException(status_code=502, detail=f"Unexpected error from NSE India: {type(e).__name__}")

    # Store in cache
    _cache[url] = (data, now + CACHE_TTL_SECONDS)
    return data


@app.get("/api/health")
def health():
    return {
        "status": "success",
        "message": "Clean Data Flow backend is running"
    }


@app.get("/api/market/history")
def get_market_history(
    symbol: str,
    exchange: str = "NSE",
    period: str = "3mo",
    interval: str = "1d",
    analyze: bool = True,
):
    """Return provider-sourced historical OHLCV and optional explainable indicators."""
    try:
        # Reject provider-incompatible pairs before attempting a network call.
        validate_period_interval(period, interval)
        result = fetch_history(symbol, exchange, period, interval)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if analyze:
        bars = result["bars"]

        # Existing SMA/RSI/Fibonacci
        analysis = analyze_bars(bars)

        # New: EMA deviation
        ema = calculate_ema_deviation(bars)

        # New: Volume-price divergence
        divergence = calculate_divergence(bars)

        # New: Volume Spike
        vs = calculate_volume_spike(bars)

        # New: Volatility Anomaly
        vsa = calculate_volatility_anomaly(bars)

        # New: Local Outlier Factor anomaly (scored on the latest bar only)
        lof = calculate_lof_anomaly(bars)

        # New: Autoencoder anomaly (scored on the latest bar only)
        ae = calculate_autoencoder_anomaly(bars)

        # New: Transformer anomaly (scored on the latest bar only)
        tr = calculate_transformer_anomaly(bars)

        # Combine into a single analysis dict
        result["analysis"] = {
            "sma": analysis["sma"],
            "rsi": analysis["rsi"],
            "fibonacci": analysis["fibonacci"],
            "ema_deviation": {
                "ema": ema["ema"],
                "latest_close": ema["latest_close"],
                "deviation_percent": ema["deviation_percent"],
                "period": ema["period"],
                "available": ema["available"],
                "method": ema["method"],
            },
            "volume_price_divergence": {
                "price_change_percent": divergence["price_change_percent"],
                "volume_change_percent": divergence["volume_change_percent"],
                "signal": divergence["signal"],
                "available": divergence["available"],
                "evaluated_timestamp": divergence.get("evaluated_timestamp"),
                "explanation": divergence["explanation"],
            },
            "volume_spike": {
                "latest_volume": vs["latest_volume"],
                "baseline_average": vs["baseline_average"],
                "volume_ratio": vs["volume_ratio"],
                "percent_above_baseline": vs["percent_above_baseline"],
                "signal": vs["signal"],
                "available": vs["available"],
                "evaluated_timestamp": vs.get("evaluated_timestamp"),
                "explanation": vs["explanation"],
            },
            "volatility_anomaly": {
                "recent_atr": vsa["recent_atr"],
                "baseline_atr": vsa["baseline_atr"],
                "volatility_ratio": vsa["volatility_ratio"],
                "percent_change": vsa["percent_change"],
                "signal": vsa["signal"],
                "available": vsa["available"],
                "evaluated_timestamp": vsa.get("evaluated_timestamp"),
                "explanation": vsa["explanation"],
            },
            "lof_anomaly": {
                "available": lof["available"],
                "is_anomaly": lof["is_anomaly"],
                "lof_score": lof["lof_score"],
                "threshold": lof["threshold"],
                "decision_value": lof["decision_value"],
                "n_neighbors": lof["n_neighbors"],
                "contamination": lof["contamination"],
                "features_used": lof["features_used"],
                "latest_features": lof["latest_features"],
                "evaluated_timestamp": lof["evaluated_timestamp"],
                "history_observations": lof["history_observations"],
                "excluded_trailing_bars": lof.get("excluded_trailing_bars", 0),
                "method": lof["method"],
                "explanation": lof["explanation"],
            },
            "autoencoder_anomaly": {
                "available": ae["available"],
                "is_anomaly": ae["is_anomaly"],
                "reconstruction_error": ae["reconstruction_error"],
                "threshold": ae["threshold"],
                "hidden_layers": ae["hidden_layers"],
                "features_used": ae["features_used"],
                "latest_features": ae["latest_features"],
                "evaluated_timestamp": ae["evaluated_timestamp"],
                "history_observations": ae["history_observations"],
                "excluded_trailing_bars": ae.get("excluded_trailing_bars", 0),
                "method": ae["method"],
                "explanation": ae["explanation"],
            },
            "transformer_anomaly": {
                "available": tr["available"],
                "is_anomaly": tr["is_anomaly"],
                "anomaly_score": tr["anomaly_score"],
                "threshold": tr["threshold"],
                "features_used": tr["features_used"],
                "latest_features": tr["latest_features"],
                "latest_prediction": tr["latest_prediction"],
                "evaluated_timestamp": tr["evaluated_timestamp"],
                "history_observations": tr["history_observations"],
                "history_limit": tr.get("history_limit"),
                "excluded_older_bars": tr.get("excluded_older_bars", 0),
                "excluded_trailing_bars": tr.get("excluded_trailing_bars", 0),
                "training_epochs": tr.get("training_epochs"),
                "method": tr["method"],
                "explanation": tr["explanation"],
            },
        }

        # Timestamped historical points live outside the established analysis
        # key set so existing indicator/detector consumers remain unchanged.
        result["historical_anomalies"] = calculate_historical_anomalies(bars)
    return result


@app.get("/api/market/oi")
def get_market_open_interest(
    symbol: str,
    exchange: str = "NSE",
    date: str = None,
    max_lookback_days: int = 7,
):
    """Return genuine NSE F&O Open Interest from the official end-of-day bhavcopy.

    This is a separate route on purpose: it never fetches price history and the
    existing ``/api/market/history`` response is unchanged. Data is an
    exchange-published end-of-day snapshot, not live/intraday.
    """
    try:
        return fetch_open_interest(
            symbol,
            exchange=exchange,
            as_of=date,
            max_lookback_days=max_lookback_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/indices")
def get_indices():
    """Return the list of NSE indices with their current levels."""
    data = _fetch_nse("/allIndices")
    indices = []
    for item in data.get("data", []):
        indices.append({
            "symbol": item.get("indexSymbol", item.get("index", "")),
            "name": item.get("index", ""),
            "last": item.get("last"),
            "change": item.get("variation", item.get("change")),
            "percentChange": item.get("percentChange"),
            "open": item.get("open"),
            "high": item.get("high"),
            "low": item.get("low"),
            "previousClose": item.get("previousClose"),
        })
    return {"indices": indices, "timestamp": data.get("timestamp")}


@app.get("/api/indices/{index_name}/constituents")
def get_index_constituents(index_name: str):
    """Return an NSE index summary and its constituent stocks with real prices."""
    encoded = quote(index_name, safe="")
    data = _fetch_nse(f"/equity-stock-indices?index={encoded}")

    items = data.get("data", [])
    if not items:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for index '{index_name}'. It may not be an NSE index (e.g. Sensex is a BSE index)."
        )

    index_summary = None
    constituents = []

    for item in items:
        if item.get("priority") == 1:
            index_summary = {
                "symbol": item.get("symbol", ""),
                "name": item.get("identifier", ""),
                "last": item.get("lastPrice"),
                "change": item.get("change"),
                "percentChange": item.get("pChange"),
                "open": item.get("open"),
                "high": item.get("dayHigh"),
                "low": item.get("dayLow"),
                "previousClose": item.get("previousClose"),
                "yearHigh": item.get("yearHigh"),
                "yearLow": item.get("yearLow"),
            }
        else:
            constituents.append({
                "symbol": item.get("symbol", ""),
                "name": item.get("identifier", ""),
                "last": item.get("lastPrice"),
                "change": item.get("change"),
                "percentChange": item.get("pChange"),
                "open": item.get("open"),
                "high": item.get("dayHigh"),
                "low": item.get("dayLow"),
                "previousClose": item.get("previousClose"),
            })

    if index_summary is None and items:
        index_summary = {
            "symbol": items[0].get("symbol", ""),
            "name": items[0].get("identifier", ""),
            "last": items[0].get("lastPrice"),
        }

    return {
        "index": index_summary,
        "constituents": constituents,
        "constituentCount": len(constituents),
        "marketStatus": data.get("marketStatus", {}),
        "timestamp": data.get("timestamp"),
        "source": "NSE India (latest session data, not guaranteed live or tick-by-tick)"
    }


# ==========================================================================
# Market news
# ==========================================================================


@app.get("/api/news")
def get_market_news(limit: int = 6):
    """Return recent real market headlines from a public RSS publisher feed.

    Headlines, links, publishers and publication times are passed through from
    the feed. ``fetchedAt`` is when this backend retrieved the feed. If the feed
    is unavailable the route fails instead of returning invented news.
    """

    try:
        return fetch_market_news(limit=limit)
    except NewsUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Market news is temporarily unavailable: {exc} "
                "No headlines were invented."
            ),
        )


# ==========================================================================
# Feedback
# ==========================================================================

FEEDBACK_FILE = Path(
    os.environ.get(
        "FEEDBACK_FILE",
        Path(__file__).resolve().parent / "data" / "feedback.jsonl",
    )
)
_feedback_lock = threading.Lock()
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class FeedbackPayload(BaseModel):
    name: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=120)
    rating: int = Field(ge=1, le=5)
    message: str = Field(min_length=10, max_length=2000)
    page: str = Field(default="home", max_length=40)


@app.post("/api/feedback")
def submit_feedback(payload: FeedbackPayload):
    """Store validated feedback in a JSONL file on this backend instance.

    This is a genuine submission endpoint, not an email service. On free hosts
    with ephemeral filesystems the file may not survive a redeploy; the response
    says so instead of implying permanent storage or an email delivery.
    """

    name = payload.name.strip()
    email = payload.email.strip()
    message = payload.message.strip()
    page = payload.page.strip() or "home"
    if not message:
        raise HTTPException(status_code=422, detail="Feedback message cannot be empty.")
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=422,
            detail="If an email is provided, it must look like name@example.com.",
        )

    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entry = {
        "id": uuid.uuid4().hex,
        "receivedAt": received_at,
        "name": name,
        "email": email,
        "rating": payload.rating,
        "message": message,
        "page": page,
    }

    try:
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _feedback_lock:
            with FEEDBACK_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Feedback could not be stored on this backend instance. "
                "Nothing was submitted; please try again later."
            ),
        ) from exc

    return {
        "status": "success",
        "message": "Feedback stored on the backend instance.",
        "id": entry["id"],
        "storedAt": received_at,
        "persistence": (
            "Saved as a local JSONL file by this FastAPI backend. Hosted "
            "filesystems may be ephemeral and reset on redeploy; this is not an "
            "email subscription or a ticketing system."
        ),
    }
