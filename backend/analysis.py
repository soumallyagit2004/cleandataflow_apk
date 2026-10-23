"""Explainable technical indicators calculated only from returned price bars."""

import copy
import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Any

import numpy as np
from sklearn.neighbors import LocalOutlierFactor
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler


FIBONACCI_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)

# EMA configuration: default period, and supported periods
EMA_DEFAULT_PERIOD = 20
SUPPORTED_EMA_PERIODS = {5, 10, 15, 20, 25, 30}

# Divergence configuration: default lookback period and supported values
DIVERGENCE_DEFAULT_PERIOD = 20
SUPPORTED_DIVERGENCE_PERIODS = {5, 10, 15, 20, 25, 30}

# Volume Spike configuration: default lookback period and supported values
VOLUME_SPIKE_DEFAULT_PERIOD = 20
SUPPORTED_VOLUME_SPIKE_PERIODS = {5, 10, 15, 20, 25, 30}

# Volatility Anomaly configuration: default lookback period and supported values
VOLATILITY_ANOMALY_DEFAULT_PERIOD = 20
SUPPORTED_VOLATILITY_ANOMALY_PERIODS = {5, 10, 15, 20, 25, 30}

# Local Outlier Factor configuration
LOF_FEATURES = ("price_return_pct", "volume_change_pct", "true_range_pct")
LOF_DEFAULT_NEIGHBORS = 20
LOF_DEFAULT_CONTAMINATION = 0.05
LOF_MIN_NEIGHBORS = 2
LOF_MAX_NEIGHBORS = 200
LOF_MIN_HISTORY = 20  # historical observations required before scoring the latest bar
LOF_METHOD = (
    "Features: price return %, volume change %, True Range %. StandardScaler "
    "fitted on historical observations only, then "
    "sklearn.neighbors.LocalOutlierFactor(novelty=True) fitted on the same "
    "history; the latest observation is scored as a new point. "
    "lof_score = -score_samples (higher = more anomalous); "
    "is_anomaly = lof_score > threshold."
)


def analyze_bars(bars: list[dict[str, Any]], sma_period: int = 20, rsi_period: int = 14) -> dict[str, Any]:
    closes = [bar["close"] for bar in bars if bar.get("close") is not None]
    highs = [bar["high"] for bar in bars if bar.get("high") is not None]
    lows = [bar["low"] for bar in bars if bar.get("low") is not None]

    sma = None
    if len(closes) >= sma_period:
        sma = sum(closes[-sma_period:]) / sma_period

    rsi = None
    if len(closes) > rsi_period:
        changes = [closes[i] - closes[i - 1] for i in range(len(closes) - rsi_period, len(closes))]
        gains = sum(max(change, 0) for change in changes) / rsi_period
        losses = sum(max(-change, 0) for change in changes) / rsi_period
        if losses == 0:
            rsi = 100.0 if gains > 0 else 50.0
        else:
            rsi = 100 - (100 / (1 + gains / losses))

    fib = None
    if highs and lows:
        swing_high, swing_low = max(highs), min(lows)
        span = swing_high - swing_low
        if span > 0:
            fib = {
                "rangeLow": swing_low,
                "rangeHigh": swing_high,
                "levels": {str(ratio): swing_high - span * ratio for ratio in FIBONACCI_RATIOS},
                "method": "retracement levels measured down from the highest high to the lowest low in returned bars",
            }

    return {
        "sma": {"period": sma_period, "value": sma, "available": sma is not None,
                "method": f"arithmetic mean of the latest {sma_period} closes"},
        "rsi": {"period": rsi_period, "value": rsi, "available": rsi is not None,
                "method": f"simple average gains/losses over the latest {rsi_period} close-to-close changes"},
        "fibonacci": fib,
    }


def _ema(values: list[float], period: int) -> list[float | None]:
    """Calculate exponential moving average over *values*.

    Returns a list the same length as *values*; earlier entries are ``None``
    until enough data exists for the smoothing multiplier.
    """
    if period <= 0 or len(values) < period:
        return [None] * len(values)

    multiplier = 2.0 / (period + 1)

    # Start with SMA of the first *period* values as the initial EMA
    ema_start = sum(values[:period]) / period
    results: list[float | None] = [None] * len(values)
    results[period - 1] = ema_start

    for i in range(period, len(values)):
        ema = (values[i] - ema_start) * multiplier + ema_start
        results[i] = ema
        ema_start = ema  # recursive carry-forward

    return results


def calculate_ema_deviation(
    bars: list[dict[str, Any]],
    period: int = EMA_DEFAULT_PERIOD,
) -> dict[str, Any]:
    """Return EMA value, latest close, deviation percentage, and availability.

    Deviation = ((latest_close - latest_ema) / latest_ema) * 100

    If there is insufficient history or missing prices, availability is ``False``
    and the numeric fields are ``None``.
    """
    if period not in SUPPORTED_EMA_PERIODS:
        return {
            "ema": None,
            "latest_close": None,
            "deviation_percent": None,
            "period": period,
            "available": False,
            "method": f"EMA-{period} is not a supported period; supported: {sorted(SUPPORTED_EMA_PERIODS)}",
        }

    closes = [bar.get("close") for bar in bars if bar.get("close") is not None]

    if len(closes) < period:
        return {
            "ema": None,
            "latest_close": closes[-1] if closes else None,
            "deviation_percent": None,
            "period": period,
            "available": False,
            "method": f"insufficient history: {len(closes)} closes, need {period}",
        }

    ema_values = _ema(closes, period)
    latest_ema = ema_values[-1]
    latest_close = closes[-1]

    if latest_ema is None or latest_close is None:
        return {
            "ema": latest_ema,
            "latest_close": latest_close,
            "deviation_percent": None,
            "period": period,
            "available": False,
            "method": "EMA calculation resulted in None; check input data",
        }

    deviation = ((latest_close - latest_ema) / latest_ema) * 100

    return {
        "ema": latest_ema,
        "latest_close": latest_close,
        "deviation_percent": deviation,
        "period": period,
        "available": True,
        "method": f"EMA-{period} deviation from latest close; multiplier={2.0/(period+1):.4f}",
    }


def _pct_change(values: list[float]) -> float | None:
    """Return ((latest - oldest) / oldest) * 100, or None if unavailable."""
    if len(values) < 2 or values[0] == 0:
        return None
    return ((values[-1] - values[0]) / abs(values[0])) * 100


def calculate_divergence(
    bars: list[dict[str, Any]],
    period: int = DIVERGENCE_DEFAULT_PERIOD,
) -> dict[str, Any]:
    """Return price/volume divergence analysis using real OHLCV bars.

    Divergence compares the direction of price change versus volume change
    over a configurable lookback period.

    Signal values:
      "bullish"   — price falling while volume rising (potential support)
      "bearish"   — price rising while volume weakening (potential resistance)
      "aligned"   — price and volume moving in the same direction
      "neutral"   — no meaningful change in either metric

    Missing or zero volumes are handled honestly; divergence is marked
    ``unavailable`` when the lookback cannot be computed.
    """
    if period not in SUPPORTED_DIVERGENCE_PERIODS:
        return {
            "price_change_percent": None,
            "volume_change_percent": None,
            "signal": None,
            "available": False,
            "period": period,
            "explanation": f"Divergence period {period} not supported; choose {sorted(SUPPORTED_DIVERGENCE_PERIODS)}",
        }

    # Extract closing prices and volumes in chronological order
    closes = [bar.get("close") for bar in bars if bar.get("close") is not None]
    volumes = [bar.get("volume") for bar in bars if bar.get("volume") is not None and bar.get("volume") != 0]

    # Need at least period+1 data points to compute change over the lookback period
    if len(closes) < period + 1 or len(volumes) < period + 1:
        return {
            "price_change_percent": None,
            "volume_change_percent": None,
            "signal": None,
            "available": False,
            "explanation": f"insufficient data: {len(closes)} closes, {len(volumes)} valid volumes; need at least {period + 1}",
        }

    price_pct = _pct_change(closes[-period - 1 : -1] if len(closes) > period else closes)
    if price_pct is None:
        # fallback: use all available closes (but still insufficient for lookback)
        price_pct = _pct_change(closes)

    volume_pct = _pct_change(volumes[-period - 1 : -1] if len(volumes) > period else volumes)
    if volume_pct is None:
        volume_pct = _pct_change(volumes)

    if price_pct is None or volume_pct is None:
        return {
            "price_change_percent": price_pct,
            "volume_change_percent": volume_pct,
            "signal": None,
            "available": False,
            "explanation": "could not compute percentage change (likely zero or missing volume)",
        }

    # Determine signal
    price_up = price_pct > 0
    price_down = price_pct < 0
    volume_up = volume_pct > 0
    volume_down = volume_pct < 0

    if price_up and volume_down:
        signal = "bearish"
        explanation = (
            f"price rose {price_pct:.2f}% while volume fell {abs(volume_pct):.2f}% — "
            f"bearish divergence, potential resistance"
        )
    elif price_down and volume_up:
        signal = "bullish"
        explanation = (
            f"price fell {abs(price_pct):.2f}% while volume rose {volume_pct:.2f}% — "
            f"bullish divergence, potential support"
        )
    elif (price_up and volume_up) or (price_down and volume_down):
        signal = "aligned"
        explanation = (
            f"price {abs(price_pct):.2f}% and volume {abs(volume_pct):.2f}% moved in the same direction — "
            f"aligned movement, no divergence"
        )
    else:
        # Both zero or both very small
        signal = "neutral"
        explanation = (
            f"price changed {price_pct:.2f}% and volume changed {volume_pct:.2f}% — "
            f"no meaningful divergence"
        )

    return {
        "price_change_percent": round(price_pct, 2),
        "volume_change_percent": round(volume_pct, 2),
        "signal": signal,
        "available": True,
        "evaluated_timestamp": _evaluated_bar_timestamp(
            bars, positive_volume=True
        ),
        "explanation": explanation,
    }


def _avg(values: list[float]) -> float | None:
    """Return the arithmetic mean of *values*, or None if no valid values."""
    if not values:
        return None
    return sum(values) / len(values)


def _true_range(bars: list[dict[str, Any]]) -> list[float | None]:
    """Calculate True Range for each bar.

    True Range = max(high - low, |high - prev_close|, |low - prev_close|)
    The first bar's True Range is simply high - low since there is no previous close.
    """
    if not bars:
        return []

    tr_values: list[float | None] = []
    prev_close = None

    for i, bar in enumerate(bars):
        high = bar.get("high")
        low = bar.get("low")
        close = bar.get("close")

        if high is None or low is None or close is None:
            tr_values.append(None)
            continue

        if prev_close is None:
            # First bar: TR = high - low
            tr = high - low if high is not None and low is not None else None
        else:
            # TR = max(high - low, |high - prev_close|, |low - prev_close|)
            tr1 = high - low if high is not None and low is not None else None
            tr2 = abs((high or 0) - prev_close) if high is not None else None
            tr3 = abs((low or 0) - prev_close) if low is not None else None

            # Filter out None values for max
            tr_vals = [v for v in [tr1, tr2, tr3] if v is not None]
            tr = max(tr_vals) if tr_vals else None

        tr_values.append(tr)
        prev_close = close

    return tr_values


def calculate_volume_spike(
    bars: list[dict[str, Any]],
    period: int = VOLUME_SPIKE_DEFAULT_PERIOD,
) -> dict[str, Any]:
    """Return volume-spike analysis using real OHLCV bars.

    Compares the latest bar's volume against the average volume of a configurable
    number of preceding bars, excluding the latest bar from the baseline.

    Returns latest volume, baseline average volume, volume ratio,
    percentage above baseline, availability status, and a clear explanation.

    Missing or zero volumes are excluded from the baseline; if the baseline is
    zero or unavailable, the result is marked ``unavailable``.
    """
    if period not in SUPPORTED_VOLUME_SPIKE_PERIODS:
        return {
            "latest_volume": None,
            "baseline_average": None,
            "volume_ratio": None,
            "percent_above_baseline": None,
            "signal": None,
            "available": False,
            "explanation": f"Volume Spike period {period} not supported; supported: {sorted(SUPPORTED_VOLUME_SPIKE_PERIODS)}",
        }

    # Extract volumes in chronological order
    all_volumes = [bar.get("volume") for bar in bars if bar.get("volume") is not None]

    if len(all_volumes) < period + 1:
        # Need at least period+1 bars: period for baseline + 1 for latest
        return {
            "latest_volume": None,
            "baseline_average": None,
            "volume_ratio": None,
            "percent_above_baseline": None,
            "signal": None,
            "available": False,
            "explanation": f"insufficient data: {len(all_volumes)} volumes; need at least {period + 1}",
        }

    latest_volume = all_volumes[-1]
    baseline_volumes = all_volumes[:-1]  # all bars except the latest

    # Filter out zero and missing volumes from the baseline
    positive_volumes = [v for v in baseline_volumes if v is not None and v > 0]

    if not positive_volumes:
        # No valid baseline volumes — cannot compute a ratio
        return {
            "latest_volume": latest_volume,
            "baseline_average": None,
            "volume_ratio": None,
            "percent_above_baseline": None,
            "signal": None,
            "available": False,
            "explanation": "baseline volume is zero or missing; cannot compute volume ratio",
        }

    baseline_avg = _avg(positive_volumes)
    volume_ratio = latest_volume / baseline_avg if baseline_avg else None
    percent_above = ((latest_volume - baseline_avg) / baseline_avg) * 100 if baseline_avg else None

    # Determine a simple label
    if volume_ratio is not None and volume_ratio > 2.0:
        label = "spike"
    elif volume_ratio is not None and volume_ratio >= 1.5:
        label = "high"
    elif volume_ratio is not None and volume_ratio >= 1.0:
        label = "elevated"
    else:
        label = "normal"

    return {
        "latest_volume": latest_volume,
        "baseline_average": round(baseline_avg, 2),
        "volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
        "percent_above_baseline": round(percent_above, 2) if percent_above is not None else None,
        "signal": label,
        "available": True,
        "evaluated_timestamp": _evaluated_bar_timestamp(bars),
        "explanation": f"latest volume {latest_volume:,} vs baseline avg {baseline_avg:,.0f}; ratio {volume_ratio:.2f}x ({label}) — {percent_above:.1f}% {'above' if percent_above and percent_above > 0 else 'below' if percent_above and percent_above < 0 else 'equal to'} baseline",
    }


def calculate_volatility_anomaly(
    bars: list[dict[str, Any]],
    period: int = VOLATILITY_ANOMALY_DEFAULT_PERIOD,
) -> dict[str, Any]:
    """Return volatility-anomaly analysis using real OHLCV bars.

    Calculates True Range (TR) for each bar, then compares the average TR of
    the recent *period* bars against a historical baseline average TR from
    preceding bars (the recent window is excluded from the baseline).

    Returns recent ATR, baseline ATR, volatility ratio, percentage change,
    availability status, a signal label, and a clear explanation.

    Missing OHLCV values are handled honestly; zero baseline ATR marks the
    result unavailable. No claim is made that high volatility proves manipulation.
    """
    if period not in SUPPORTED_VOLATILITY_ANOMALY_PERIODS:
        return {
            "recent_atr": None,
            "baseline_atr": None,
            "volatility_ratio": None,
            "percent_change": None,
            "signal": None,
            "available": False,
            "explanation": f"Volatility Anomaly period {period} not supported; supported: {sorted(SUPPORTED_VOLATILITY_ANOMALY_PERIODS)}",
        }

    # Calculate True Range for all bars
    tr_values = _true_range(bars)

    # Filter out None TR values
    valid_tr = [v for v in tr_values if v is not None]

    if len(valid_tr) < period + 1:
        # Need at least period+1 valid TR values: period for recent avg + 1 for baseline
        return {
            "recent_atr": None,
            "baseline_atr": None,
            "volatility_ratio": None,
            "percent_change": None,
            "signal": None,
            "available": False,
            "explanation": f"insufficient TR data: {len(valid_tr)} valid TR values; need at least {period + 1}",
        }

    # Recent ATR: average of the last *period* True Range values
    recent_tr = valid_tr[-period:]  # most recent period TR values
    recent_atr = sum(recent_tr) / period

    # Baseline TR: all preceding valid TR values (exclude the recent window)
    baseline_tr = valid_tr[:-period]  # all TR values before the recent window

    if not baseline_tr:
        # No baseline TR available
        return {
            "recent_atr": round(recent_atr, 2),
            "baseline_atr": None,
            "volatility_ratio": None,
            "percent_change": None,
            "signal": None,
            "available": False,
            "explanation": "no baseline TR available (insufficient historical data)",
        }

    baseline_atr = sum(baseline_tr) / len(baseline_tr)

    # Compute ratio and percent change
    if baseline_atr == 0:
        # Zero baseline – cannot compute a meaningful ratio
        return {
            "recent_atr": round(recent_atr, 2),
            "baseline_atr": None,
            "volatility_ratio": None,
            "percent_change": None,
            "signal": None,
            "available": False,
            "explanation": "baseline ATR is zero; cannot compute volatility ratio",
        }
    else:
        volatility_ratio = recent_atr / baseline_atr
        percent_change = ((recent_atr - baseline_atr) / baseline_atr) * 100
        if volatility_ratio > 2.0:
            label = "anomalous"
        elif volatility_ratio >= 1.5:
            label = "elevated"
        elif volatility_ratio >= 1.0:
            label = "slightly elevated"
        else:
            label = "normal"
        explanation = (
            f"recent ATR {recent_atr:,.2f} vs baseline ATR {baseline_atr:,.2f}; "
            f"ratio {volatility_ratio:.2f}x ({label}) — "
            f"{percent_change:.1f}% {'above' if percent_change and percent_change > 0 else 'below' if percent_change and percent_change < 0 else 'at'} baseline"
        )

    return {
        "recent_atr": round(recent_atr, 2),
        "baseline_atr": round(baseline_atr, 2),
        "volatility_ratio": round(volatility_ratio, 4) if volatility_ratio is not None else None,
        "percent_change": round(percent_change, 2) if percent_change is not None else None,
        "signal": label,
        "available": True,
        "evaluated_timestamp": _evaluated_bar_timestamp(bars),
        "explanation": explanation,
    }


def _lof_unavailable(
    explanation: str,
    n_neighbors: int | None = None,
    contamination: float | None = None,
    history_observations: int = 0,
    evaluated_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a complete, stable LOF result shape for unavailable cases."""
    return {
        "available": False,
        "is_anomaly": None,
        "lof_score": None,
        "threshold": None,
        "decision_value": None,
        "n_neighbors": n_neighbors,
        "contamination": contamination,
        "features_used": list(LOF_FEATURES),
        "latest_features": None,
        "evaluated_timestamp": evaluated_timestamp,
        "history_observations": history_observations,
        "method": LOF_METHOD,
        "explanation": explanation,
    }


def _lof_feature_rows(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn OHLCV bars into one causal feature row per usable bar.

    Each row for bar *i* only uses bar *i* and bar *i - 1*:

      price_return_pct = (close[i] - close[i-1]) / close[i-1] * 100
      volume_change_pct = (volume[i] - volume[i-1]) / volume[i-1] * 100
      true_range_pct = TrueRange[i] / close[i-1] * 100

    True Range is max(high - low, |high - previous close|,
    |low - previous close|). Rows with missing, non-finite or unusable
    values (non-positive prices or previous volume, negative volume,
    high below low) are skipped, so no value is invented and no future bar
    is used.
    """
    rows: list[dict[str, Any]] = []

    for index in range(1, len(bars)):
        previous = bars[index - 1]
        current = bars[index]

        raw_values = [
            previous.get("close"),
            current.get("close"),
            current.get("high"),
            current.get("low"),
            previous.get("volume"),
            current.get("volume"),
        ]
        if any(value is None for value in raw_values):
            continue

        try:
            numbers = [float(value) for value in raw_values]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in numbers):
            continue

        previous_close, close, high, low, previous_volume, volume = numbers
        if (
            previous_close <= 0
            or close <= 0
            or high <= 0
            or low <= 0
            or previous_volume <= 0
            or volume < 0
            or high < low
        ):
            continue

        price_return_pct = ((close - previous_close) / previous_close) * 100
        volume_change_pct = ((volume - previous_volume) / previous_volume) * 100
        true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_range_pct = (true_range / previous_close) * 100

        rows.append(
            {
                "bar_index": index,
                "timestamp": current.get("timestamp"),
                "features": (price_return_pct, volume_change_pct, true_range_pct),
            }
        )

    return rows


def _bar_has_complete_ohlcv(bar: dict[str, Any]) -> bool:
    """True when a bar has finite, usable close/high/low/volume values.

    Used only to distinguish a genuinely incomplete trailing bar (for example
    Yahoo's partial current session with a null close) from an internal gap
    that would make the latest bar impossible to score honestly.
    """
    raw = [bar.get("close"), bar.get("high"), bar.get("low"), bar.get("volume")]
    if any(value is None for value in raw):
        return False
    try:
        close, high, low, volume = (float(value) for value in raw)
    except (TypeError, ValueError):
        return False
    return bool(
        all(math.isfinite(value) for value in (close, high, low, volume))
        and close > 0
        and high > 0
        and low > 0
        and high >= low
        and volume >= 0
    )


def _evaluated_bar_timestamp(
    bars: list[dict[str, Any]], *, positive_volume: bool = False
) -> str | None:
    """Return the timestamp of the bar the latest-only filters actually use.

    Deterministic volume/volatility/divergence filters do not currently return
    an evaluated timestamp. The frontend needs one to place a latest-only marker
    on the exact returned candle, so this helper identifies the last complete
    OHLCV bar (with a positive volume where the detector requires one).
    """
    for bar in reversed(bars):
        if not _bar_has_complete_ohlcv(bar):
            continue
        if positive_volume and float(bar.get("volume") or 0) <= 0:
            continue
        timestamp = bar.get("timestamp")
        return timestamp if isinstance(timestamp, str) else None
    return None


def _trailing_incomplete_bars(
    bars: list[dict[str, Any]], latest_row: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """Return trailing bars after the latest scorable row, or ``None`` on a gap.

    The latest scorable feature row already requires complete OHLCV data for
    that bar and the one before it. Any bar after it is normally a partial
    trailing bar such as Yahoo's in-progress session with a missing close.
    If one of those trailing bars is itself complete, the real cause is an
    invalid earlier bar, so the caller must fail honestly instead of scoring
    an older observation as if it were the latest.
    """
    trailing = bars[latest_row["bar_index"] + 1:]
    if any(_bar_has_complete_ohlcv(bar) for bar in trailing):
        return None
    return trailing


def calculate_lof_anomaly(
    bars: list[dict[str, Any]],
    n_neighbors: int = LOF_DEFAULT_NEIGHBORS,
    contamination: float = LOF_DEFAULT_CONTAMINATION,
) -> dict[str, Any]:
    """Score the latest bar with scikit-learn's Local Outlier Factor (LOF).

    Method
    ------
    1. Build causal features from consecutive real OHLCV bars: price return
       %, volume change % and True Range as a percentage of the previous
       close. A feature row for bar *i* never reads bar *i + 1*.
    2. Fit ``StandardScaler`` on the historical feature rows only, then fit
       ``LocalOutlierFactor(novelty=True)`` on that scaled history.
    3. Score only the *latest* bar as a new observation against that fitted
       history. The latest bar is excluded from fitting, and no bar after it
       is ever used.

    The returned ``lof_score`` is the Local Outlier Factor of the latest bar
    (higher means its neighbourhood is less dense than its neighbours').
    ``threshold`` is the decision boundary derived from ``contamination``;
    ``is_anomaly`` is true when ``lof_score > threshold`` (equivalently when
    sklearn's decision value is negative). Typical LOF values are close to
    1.0. An anomaly here is a statistical density outlier only: it is not
    proof of market manipulation and not a trading signal.

    Missing values, insufficient history, constant features, invalid
    parameters and internal numerical failures return ``available: False``
    with an explanation rather than a fabricated flag.
    """
    # ---------- parameter validation ----------
    valid_neighbors = (
        n_neighbors
        if isinstance(n_neighbors, int) and not isinstance(n_neighbors, bool)
        else None
    )
    if valid_neighbors is None or not (LOF_MIN_NEIGHBORS <= valid_neighbors <= LOF_MAX_NEIGHBORS):
        return _lof_unavailable(
            f"n_neighbors must be an integer between {LOF_MIN_NEIGHBORS} and {LOF_MAX_NEIGHBORS}."
        )

    if (
        isinstance(contamination, bool)
        or not isinstance(contamination, (int, float))
        or not math.isfinite(float(contamination))
        or not (0 < float(contamination) <= 0.5)
    ):
        return _lof_unavailable(
            "contamination must be a finite number greater than 0 and at most 0.5.",
            n_neighbors=valid_neighbors,
        )
    contamination_value = float(contamination)

    # ---------- feature extraction ----------
    rows = _lof_feature_rows(bars)

    if not rows:
        return _lof_unavailable(
            "No usable consecutive OHLCV observations: each LOF row needs a bar "
            "plus the previous bar with close, high, low and positive volume.",
            n_neighbors=valid_neighbors,
            contamination=contamination_value,
        )

    if rows[-1]["bar_index"] != len(bars) - 1:
        trailing_bars = _trailing_incomplete_bars(bars, rows[-1])
        if trailing_bars is None:
            return _lof_unavailable(
                "A returned bar after the latest scorable observation has complete "
                "OHLCV data but cannot form a feature row because an earlier bar is "
                "invalid, so the latest observation cannot be scored honestly.",
                n_neighbors=valid_neighbors,
                contamination=contamination_value,
                history_observations=max(len(rows) - 1, 0),
                evaluated_timestamp=rows[-1]["timestamp"],
            )
        excluded_trailing_bars = len(trailing_bars)
    else:
        excluded_trailing_bars = 0

    history_rows = rows[:-1]
    latest_row = rows[-1]
    history_count = len(history_rows)

    if history_count < LOF_MIN_HISTORY:
        return _lof_unavailable(
            f"Insufficient history: {history_count} usable historical observations, "
            f"but at least {LOF_MIN_HISTORY} are required before the latest bar can be scored.",
            n_neighbors=valid_neighbors,
            contamination=contamination_value,
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    if history_count < valid_neighbors + 1:
        return _lof_unavailable(
            f"Insufficient history for n_neighbors={valid_neighbors}: {history_count} "
            f"historical observations, but at least {valid_neighbors + 1} are required.",
            n_neighbors=valid_neighbors,
            contamination=contamination_value,
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    # ---------- scaling and LOF ----------
    try:
        history_matrix = np.asarray([row["features"] for row in history_rows], dtype=float)
        latest_vector = np.asarray(latest_row["features"], dtype=float).reshape(1, -1)

        if not np.all(np.isfinite(history_matrix)) or not np.all(np.isfinite(latest_vector)):
            return _lof_unavailable(
                "Feature matrix contains non-finite values; LOF cannot be computed.",
                n_neighbors=valid_neighbors,
                contamination=contamination_value,
                history_observations=history_count,
                evaluated_timestamp=latest_row["timestamp"],
            )

        combined = np.vstack([history_matrix, latest_vector])
        if np.all(np.ptp(combined, axis=0) == 0):
            return _lof_unavailable(
                "All LOF features are constant across the historical observations "
                "and the latest bar, so the detector cannot distinguish normal from "
                "anomalous behaviour.",
                n_neighbors=valid_neighbors,
                contamination=contamination_value,
                history_observations=history_count,
                evaluated_timestamp=latest_row["timestamp"],
            )

        scaler = StandardScaler().fit(history_matrix)
        scaled_history = scaler.transform(history_matrix)
        scaled_latest = scaler.transform(latest_vector)

        model = LocalOutlierFactor(
            n_neighbors=valid_neighbors,
            novelty=True,
            contamination=contamination_value,
        )
        model.fit(scaled_history)

        # scikit-learn's score_samples is the negative LOF; negate it so a
        # larger value means "more anomalous" for API consumers.
        lof_score = float(-model.score_samples(scaled_latest)[0])
        threshold = float(-model.offset_)
        decision_value = float(model.decision_function(scaled_latest)[0])
    except Exception as exc:  # keep analysis failures out of the API error path
        return _lof_unavailable(
            f"LOF computation failed: {type(exc).__name__}: {exc}",
            n_neighbors=valid_neighbors,
            contamination=contamination_value,
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    is_anomaly = bool(decision_value < 0)
    timestamp = latest_row["timestamp"]
    latest_features = {
        name: round(float(value), 4)
        for name, value in zip(LOF_FEATURES, latest_row["features"])
    }

    prefix = (
        f"The model was fitted on the {history_count} observations before the latest "
        f"bar and scored only the latest bar as a new point, so no future data was used."
    )
    if excluded_trailing_bars:
        prefix += (
            f" {excluded_trailing_bars} trailing returned bar(s) with missing or "
            f"invalid OHLCV were excluded, so the latest complete bar was scored."
        )

    if is_anomaly:
        explanation = (
            f"LOF flagged the latest bar ({timestamp}) as a density outlier: score "
            f"{lof_score:.2f} is above the anomaly threshold {threshold:.2f} "
            f"(contamination={contamination_value:g}, n_neighbors={valid_neighbors}). "
            f"{prefix} This is a statistical outlier, not proof of market manipulation "
            f"or a guaranteed trading signal."
        )
    else:
        explanation = (
            f"The latest bar ({timestamp}) is not flagged: LOF score {lof_score:.2f} "
            f"is at or below the anomaly threshold {threshold:.2f} "
            f"(contamination={contamination_value:g}, n_neighbors={valid_neighbors}). "
            f"{prefix}"
        )

    return {
        "available": True,
        "is_anomaly": is_anomaly,
        "lof_score": round(lof_score, 4),
        "threshold": round(threshold, 4),
        "decision_value": round(decision_value, 4),
        "n_neighbors": valid_neighbors,
        "contamination": contamination_value,
        "features_used": list(LOF_FEATURES),
        "latest_features": latest_features,
        "evaluated_timestamp": timestamp,
        "history_observations": history_count,
        "excluded_trailing_bars": excluded_trailing_bars,
        "method": LOF_METHOD,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Autoencoder-based anomaly detection (Phase 3A)
# ---------------------------------------------------------------------------

AE_DEFAULT_HIDDEN_LAYERS = (8, 4, 8)
AE_MAX_ITER = 3000
AE_RANDOM_STATE = 42
AE_ALPHA = 1e-4
AE_MIN_HISTORY = 15          # historical observations required before scoring latest
AE_ANOMALY_K = 2.0           # threshold = mean + k * std of historical errors

AE_METHOD = (
    "sklearn.neural_network.MLPRegressor used as a small deterministic "
    "autoencoder (input 3 -> hidden 8-4-8 -> output 3) trained to reconstruct "
    "its own input. StandardScaler is fitted on historical observations only. "
    "The latest bar is scored separately as reconstruction error "
    "(mean squared error between input and reconstruction); higher error means "
    "the pattern is less like the training data. Threshold = mean(historical "
    "reconstruction errors) + 2 * std(historical reconstruction errors), "
    "computed only on training data. No future data or the latest bar influences "
    "training, scaling, or the threshold."
)


def _ae_unavailable(
    explanation: str,
    hidden_layers: tuple[int, ...] | None = None,
    history_observations: int = 0,
    evaluated_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a complete, stable autoencoder result shape for unavailable cases."""
    return {
        "available": False,
        "is_anomaly": None,
        "reconstruction_error": None,
        "threshold": None,
        "hidden_layers": None if hidden_layers is None else list(hidden_layers),
        "features_used": list(LOF_FEATURES),
        "latest_features": None,
        "evaluated_timestamp": evaluated_timestamp,
        "history_observations": history_observations,
        "method": AE_METHOD,
        "explanation": explanation,
    }


def calculate_autoencoder_anomaly(
    bars: list[dict[str, Any]],
    hidden_layers: tuple[int, ...] = AE_DEFAULT_HIDDEN_LAYERS,
) -> dict[str, Any]:
    """Score the latest bar with a small deterministic neural-network autoencoder.

    Method
    ------
    1. Build causal features from consecutive real OHLCV bars: price return
       %, volume change % and True Range as a percentage of the previous
       close. A feature row for bar *i* never reads bar *i + 1*.
    2. Fit ``StandardScaler`` on the historical feature rows only, then train
       an ``MLPRegressor`` autoencoder to reconstruct its own scaled input
       (input 3 -> hidden 8-4-8 -> output 3). Training uses only historical
       observations; the latest bar is excluded.
    3. Compute the reconstruction error (mean squared error between input and
       reconstruction) for each historical observation, then derive the
       anomaly threshold as ``mean(errors) + 2 * std(errors)`` from those
       historical errors only.
    4. Score only the *latest* bar as a new observation: reconstruction error
       of the latest bar vs the historical threshold.

    The returned ``reconstruction_error`` is the latest bar's mean squared
    reconstruction error (higher = less like the training data).
    ``is_anomaly`` is true when ``reconstruction_error > threshold``.

    Missing values, insufficient history, constant features, invalid
    parameters and training failures return ``available: False`` with an
    explanation rather than a fabricated flag. An anomaly is a statistical
    reconstruction outlier only: it is not proof of market manipulation and
    not a trading signal.
    """
    # ---------- parameter validation ----------
    valid_layers = None
    if (
        isinstance(hidden_layers, (tuple, list))
        and len(hidden_layers) >= 1
        and all(
            isinstance(size, int) and not isinstance(size, bool) and size > 0
            for size in hidden_layers
        )
    ):
        valid_layers = tuple(int(size) for size in hidden_layers)

    if valid_layers is None:
        return _ae_unavailable(
            "hidden_layers must be a non-empty sequence of positive integers.",
        )

    # ---------- feature extraction ----------
    rows = _lof_feature_rows(bars)

    if not rows:
        return _ae_unavailable(
            "No usable consecutive OHLCV observations: each autoencoder row "
            "needs a bar plus the previous bar with close, high, low and "
            "positive volume.",
            hidden_layers=valid_layers,
        )

    if rows[-1]["bar_index"] != len(bars) - 1:
        trailing_bars = _trailing_incomplete_bars(bars, rows[-1])
        if trailing_bars is None:
            return _ae_unavailable(
                "A returned bar after the latest scorable observation has complete "
                "OHLCV data but cannot form a feature row because an earlier bar is "
                "invalid, so the latest observation cannot be scored honestly.",
                hidden_layers=valid_layers,
                history_observations=max(len(rows) - 1, 0),
                evaluated_timestamp=rows[-1]["timestamp"],
            )
        excluded_trailing_bars = len(trailing_bars)
    else:
        excluded_trailing_bars = 0

    history_rows = rows[:-1]
    latest_row = rows[-1]
    history_count = len(history_rows)

    if history_count < AE_MIN_HISTORY:
        return _ae_unavailable(
            f"Insufficient history: {history_count} usable historical "
            f"observations, but at least {AE_MIN_HISTORY} are required before "
            f"the latest bar can be scored.",
            hidden_layers=valid_layers,
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    # ---------- scaling ----------
    try:
        history_matrix = np.asarray(
            [row["features"] for row in history_rows], dtype=float
        )
        latest_vector = np.asarray(latest_row["features"], dtype=float).reshape(1, -1)

        if not np.all(np.isfinite(history_matrix)) or not np.all(
            np.isfinite(latest_vector)
        ):
            return _ae_unavailable(
                "Feature matrix contains non-finite values; autoencoder cannot "
                "be trained.",
                hidden_layers=valid_layers,
                history_observations=history_count,
                evaluated_timestamp=latest_row["timestamp"],
            )

        if np.all(np.ptp(history_matrix, axis=0) == 0):
            return _ae_unavailable(
                "All historical LOF features are constant, so the autoencoder "
                "has no variance to learn from and cannot distinguish normal "
                "from anomalous behaviour.",
                hidden_layers=valid_layers,
                history_observations=history_count,
                evaluated_timestamp=latest_row["timestamp"],
            )

        scaler = StandardScaler().fit(history_matrix)
        scaled_history = scaler.transform(history_matrix)
        scaled_latest = scaler.transform(latest_vector)

        # ---------- train autoencoder on history only ----------
        model = MLPRegressor(
            hidden_layer_sizes=valid_layers,
            activation="relu",
            solver="adam",
            alpha=AE_ALPHA,
            max_iter=AE_MAX_ITER,
            random_state=AE_RANDOM_STATE,
            shuffle=False,
            early_stopping=False,
        )
        model.fit(scaled_history, scaled_history)

        # Historical reconstruction errors (training data only).
        history_reconstruction = model.predict(scaled_history)
        history_errors = np.mean((scaled_history - history_reconstruction) ** 2, axis=1)

        mean_error = float(np.mean(history_errors))
        std_error = float(np.std(history_errors))
        threshold = mean_error + AE_ANOMALY_K * std_error
        if not math.isfinite(threshold) or threshold <= 0:
            max_error = float(np.max(history_errors))
            threshold = max_error if max_error > 0 else 1.0

        # ---------- score the latest bar separately ----------
        latest_reconstruction = model.predict(scaled_latest)
        reconstruction_error = float(
            np.mean((scaled_latest - latest_reconstruction) ** 2)
        )
    except Exception as exc:  # keep analysis failures out of the API error path
        return _ae_unavailable(
            f"Autoencoder computation failed: {type(exc).__name__}: {exc}",
            hidden_layers=valid_layers,
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    is_anomaly = bool(reconstruction_error > threshold)
    timestamp = latest_row["timestamp"]
    latest_features = {
        name: round(float(value), 4)
        for name, value in zip(LOF_FEATURES, latest_row["features"])
    }

    prefix = (
        f"The autoencoder was trained only on the {history_count} observations "
        f"before the latest bar; the latest bar is scored separately as a "
        f"reconstruction error, so no future data was used."
    )
    if excluded_trailing_bars:
        prefix += (
            f" {excluded_trailing_bars} trailing returned bar(s) with missing or "
            f"invalid OHLCV were excluded, so the latest complete bar was scored."
        )

    if is_anomaly:
        explanation = (
            f"Autoencoder flagged the latest bar ({timestamp}): reconstruction "
            f"error {reconstruction_error:.4f} is above the anomaly threshold "
            f"{threshold:.4f} (mean + {AE_ANOMALY_K} * std of historical "
            f"reconstruction errors). {prefix} This is a statistical outlier, "
            f"not proof of market manipulation or a guaranteed trading signal."
        )
    else:
        explanation = (
            f"The latest bar ({timestamp}) is not flagged: reconstruction "
            f"error {reconstruction_error:.4f} is at or below the anomaly "
            f"threshold {threshold:.4f} (mean + {AE_ANOMALY_K} * std of "
            f"historical reconstruction errors). {prefix}"
        )

    return {
        "available": True,
        "is_anomaly": is_anomaly,
        "reconstruction_error": round(reconstruction_error, 4),
        "threshold": round(threshold, 4),
        "hidden_layers": list(valid_layers),
        "features_used": list(LOF_FEATURES),
        "latest_features": latest_features,
        "evaluated_timestamp": timestamp,
        "history_observations": history_count,
        "excluded_trailing_bars": excluded_trailing_bars,
        "method": AE_METHOD,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Transformer-based anomaly detection (Phase 3B)
# ---------------------------------------------------------------------------

TR_DEFAULT_SEQ_LEN = 8
TR_D_MODEL = 16
TR_N_HEADS = 2
TR_D_FF = 32
TR_EPOCHS = 500
# Long timeframe requests (1Y/5Y) otherwise spend minutes training the pure
# NumPy Transformer. The latest-only check remains causal but uses a bounded
# recent window and a reduced epoch count, both reported in the response.
TR_MAX_HISTORY_BARS = 180
TR_EXTENDED_EPOCHS = 120
TR_FULL_TRAINING_HISTORY = 80
TR_LR = 0.001
TR_RANDOM_STATE = 42
TR_MIN_HISTORY = 20          # historical observations before scoring latest
TR_MIN_SEQUENCES = 5         # minimum training sequences required
TR_ANOMALY_K = 2.0           # threshold = mean + k * std of historical errors

TR_METHOD = (
    "A small self-attention Transformer encoder implemented in NumPy "
    "(no torch / TensorFlow dependency). Input: a window of historical "
    "feature vectors -> linear projection + positional encoding -> "
    "multi-head self-attention (+ residual) -> feed-forward (+ residual) "
    "-> mean pool -> linear output that predicts the NEXT feature vector. "
    "Trained with MSE loss and SGD on historical sequences only. "
    "Anomaly score = mean squared prediction error of the latest feature "
    "vector; higher error means the pattern is less like the training data. "
    "Threshold = mean(historical prediction errors) + 2 * std(...), "
    "computed only on training data. The latest bar is excluded from "
    "training, scaling, and threshold calculation."
)


def _tr_unavailable(
    explanation: str,
    history_observations: int = 0,
    evaluated_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a complete, stable Transformer result shape for unavailable cases."""
    return {
        "available": False,
        "is_anomaly": None,
        "anomaly_score": None,
        "threshold": None,
        "features_used": list(LOF_FEATURES),
        "latest_features": None,
        "latest_prediction": None,
        "evaluated_timestamp": evaluated_timestamp,
        "history_observations": history_observations,
        "method": TR_METHOD,
        "explanation": explanation,
    }


def _tr_relu(x):
    return np.maximum(0.0, x)


def _tr_relu_backward(grad: np.ndarray, z: np.ndarray) -> np.ndarray:
    return grad * (z > 0.0)


def _tr_clip(grads: dict[str, np.ndarray], max_norm: float = 1.0) -> dict[str, np.ndarray]:
    total = 0.0
    for value in grads.values():
        total += float(np.sum(value ** 2))
    total = math.sqrt(total)
    if total > max_norm:
        scale = max_norm / (total + 1e-12)
        for key in grads:
            grads[key] = grads[key] * scale
    return grads


class _TinyTransformer:
    """Minimal Transformer encoder for next-step feature prediction.

    Architecture (all NumPy, deterministic given a seed):
      (seq_len, n_features) -> linear + positional encoding
      -> multi-head self-attention (+ residual)
      -> feed-forward (+ residual)
      -> mean pool -> linear -> (n_features,) next-step prediction

    Trained with MSE loss + SGD. No external deep-learning dependency is used
    so the backend stays lightweight and runnable offline.
    """

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        d_ff: int,
        seq_len: int,
        n_features: int,
        seed: int = TR_RANDOM_STATE,
    ):
        if d_model % n_heads != 0:
            raise ValueError("d_model must be divisible by n_heads")
        rng = np.random.RandomState(seed)
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_model // n_heads
        self.d_ff = d_ff
        self.seq_len = seq_len
        self.n_features = n_features

        def xavier(fan_in: int, fan_out: int, mult: float = 1.0) -> np.ndarray:
            return (
                rng.normal(0, math.sqrt(2.0 / (fan_in + fan_out)), (fan_in, fan_out))
                * mult
            )

        self.params: dict[str, np.ndarray] = {
            "W_in": xavier(n_features, d_model),
            "W_pos": xavier(seq_len, d_model) * 0.1,
            "W_q": xavier(d_model, d_model),
            "W_k": xavier(d_model, d_model),
            "W_v": xavier(d_model, d_model),
            "W_o": xavier(d_model, d_model) * 0.5,
            "W1": xavier(d_model, d_ff),
            "b1": np.zeros(d_ff),
            "W2": xavier(d_ff, d_model) * 0.5,
            "b2": np.zeros(d_model),
            "W_out": xavier(d_model, n_features) * 0.5,
            "b_out": np.zeros(n_features),
        }

    def forward(self, X: np.ndarray):
        """X: (seq_len, n_features). Returns (prediction, cache)."""
        seq_len = X.shape[0]
        pos = self.params["W_pos"][:seq_len]

        z = X @ self.params["W_in"] + pos  # (seq_len, d_model)

        # ---- Multi-head self-attention ----
        q = z @ self.params["W_q"]
        k = z @ self.params["W_k"]
        v = z @ self.params["W_v"]
        qh = q.reshape(seq_len, self.n_heads, self.d_head).transpose(1, 0, 2)
        kh = k.reshape(seq_len, self.n_heads, self.d_head).transpose(1, 0, 2)
        vh = v.reshape(seq_len, self.n_heads, self.d_head).transpose(1, 0, 2)
        scores = np.matmul(qh, kh.transpose(0, 2, 1)) / math.sqrt(self.d_head)
        scores = scores - np.max(scores, axis=-1, keepdims=True)
        exp_s = np.exp(scores)
        attn = exp_s / np.sum(exp_s, axis=-1, keepdims=True)
        attn_out = np.matmul(attn, vh)  # (n_heads, seq_len, d_head)
        attn_out = attn_out.transpose(1, 0, 2).reshape(seq_len, self.d_model)
        attn_out = attn_out @ self.params["W_o"]

        z2 = z + attn_out  # residual

        # ---- Feed forward ----
        h = _tr_relu(z2 @ self.params["W1"] + self.params["b1"])  # (seq_len, d_ff)
        f = h @ self.params["W2"] + self.params["b2"]  # (seq_len, d_model)
        z3 = z2 + f  # residual

        # ---- Mean pool + output ----
        pooled = np.mean(z3, axis=0)  # (d_model,)
        pred = pooled @ self.params["W_out"] + self.params["b_out"]  # (n_features,)

        cache = (
            X, z, q, k, v, qh, kh, vh, scores, attn, attn_out,
            z2, h, f, z3, pooled,
        )
        return pred, cache

    def backward(self, d_pred: np.ndarray, cache, lr: float) -> None:
        (
            X, z, q, k, v, qh, kh, vh, scores, attn, attn_out_raw,
            z2, h, f, z3, pooled,
        ) = cache
        seq_len = X.shape[0]
        grads: dict[str, np.ndarray] = {}

        # ---- Output layer ----
        grads["W_out"] = np.outer(pooled, d_pred)
        grads["b_out"] = d_pred
        d_pooled = d_pred @ self.params["W_out"].T
        d_z3 = np.full((seq_len, self.d_model), d_pooled / seq_len)

        # ---- Residual z3 = z2 + f ----
        d_z2 = d_z3.copy()
        d_f = d_z3.copy()
        grads["b2"] = np.sum(d_f, axis=0)
        grads["W2"] = h.T @ d_f
        d_h = d_f @ self.params["W2"].T
        d_h = _tr_relu_backward(d_h, z2 @ self.params["W1"] + self.params["b1"])
        grads["b1"] = np.sum(d_h, axis=0)
        grads["W1"] = z2.T @ d_h
        d_z2 = d_z2 + (d_h @ self.params["W1"].T)

        # ---- Residual z2 = z + attn_out ----
        d_z = d_z2.copy()
        d_attn_out = d_z2.copy()

        grads["W_o"] = attn_out_raw.T @ d_attn_out
        d_attn_raw = d_attn_out @ self.params["W_o"].T
        d_attn_raw_h = d_attn_raw.reshape(seq_len, self.n_heads, self.d_head).transpose(1, 0, 2)

        # ---- Attention backward ----
        d_v = np.matmul(attn.transpose(0, 2, 1), d_attn_raw_h)
        d_attn = np.matmul(d_attn_raw_h, vh.transpose(0, 2, 1))
        d_scores = attn * (d_attn - np.sum(d_attn * attn, axis=-1, keepdims=True))
        d_scores = d_scores / math.sqrt(self.d_head)
        d_q = np.matmul(d_scores, kh)
        d_k = np.matmul(d_scores.transpose(0, 2, 1), qh)

        d_q = d_q.transpose(1, 0, 2).reshape(seq_len, self.d_model)
        d_k = d_k.transpose(1, 0, 2).reshape(seq_len, self.d_model)
        d_v = d_v.transpose(1, 0, 2).reshape(seq_len, self.d_model)

        grads["W_q"] = z.T @ d_q
        grads["W_k"] = z.T @ d_k
        grads["W_v"] = z.T @ d_v
        d_z = d_z + (
            d_q @ self.params["W_q"].T
            + d_k @ self.params["W_k"].T
            + d_v @ self.params["W_v"].T
        )

        # ---- Input projection + positional ----
        grads["W_in"] = X.T @ d_z
        grads["W_pos"] = d_z

        grads = _tr_clip(grads, max_norm=1.0)

        for key in grads:
            self.params[key] -= lr * grads[key]

    def predict(self, X: np.ndarray) -> np.ndarray:
        pred, _ = self.forward(X)
        return pred


def _tr_build_sequences(history_feats: list[list[float]], seq_len: int):
    """X: (n_samples, seq_len, n_features), y: (n_samples, n_features)."""
    xs, ys = [], []
    for t in range(len(history_feats) - seq_len):
        xs.append(history_feats[t:t + seq_len])
        ys.append(history_feats[t + seq_len])
    if not xs:
        return None, None
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


# ---------------------------------------------------------------------------
# Transformer result cache
# ---------------------------------------------------------------------------
# The TinyTransformer is fully deterministic (fixed seed, no shuffling), so the
# same returned bars and parameters always produce the same result. Retraining
# costs seconds of CPU on every identical call (page reload, another user on
# the same symbol/timeframe, a period re-selection that ends on the same
# window), so results are cached behind a digest of EXACTLY the inputs that
# influence the output:
#
#   * every returned bar's values (the model history cap is applied inside the
#     calculation, so the full list is digested rather than re-deriving which
#     slice mattered),
#   * the raw seq_len / epochs / lr arguments,
#   * every module constant that can change a result byte (history cap, epoch
#     budget, model shape, seed, thresholds, feature names) plus an explicit
#     implementation version string.
#
# Correctness therefore never depends on the TTL: new bars produce a new key
# and the Transformer retrains. Entries older than the TTL are simply dropped
# to bound memory. Unavailable results are never cached: they may reflect
# transient conditions and must be honestly recomputed. Bounded at 64 entries
# (oldest evicted). Duplicate concurrent calls may each train once; the lock
# is never held during training.
TR_CACHE_VERSION = "tr-result-cache-1"
TR_CACHE_MAX_ENTRIES = 64
_TR_CACHE_DEFAULT_TTL_SECONDS = 900.0  # matches the market-overview cache horizon

_TR_CACHE: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()
_TR_CACHE_LOCK = threading.Lock()


def _tr_cache_enabled() -> bool:
    """Read the CDF_TR_CACHE toggle (default enabled; falsy values disable)."""
    raw = (os.environ.get("CDF_TR_CACHE") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _tr_cache_ttl_seconds() -> float:
    """Read CDF_TR_CACHE_TTL; invalid or negative values keep the default."""
    raw = (os.environ.get("CDF_TR_CACHE_TTL") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return _TR_CACHE_DEFAULT_TTL_SECONDS
    return value if value >= 0 else _TR_CACHE_DEFAULT_TTL_SECONDS


def _tr_cache_key(bars: Any, seq_len: Any, epochs: Any, lr: Any) -> dict[str, Any] | None:
    """Build the cache key for one call, or None when it cannot be trusted."""
    try:
        fingerprint = json.dumps(
            {
                "version": TR_CACHE_VERSION,
                "algorithm": {
                    "max_history_bars": TR_MAX_HISTORY_BARS,
                    "extended_epochs": TR_EXTENDED_EPOCHS,
                    "full_training_history": TR_FULL_TRAINING_HISTORY,
                    "d_model": TR_D_MODEL,
                    "n_heads": TR_N_HEADS,
                    "d_ff": TR_D_FF,
                    "random_state": TR_RANDOM_STATE,
                    "min_history": TR_MIN_HISTORY,
                    "min_sequences": TR_MIN_SEQUENCES,
                    "anomaly_k": TR_ANOMALY_K,
                    "features": list(LOF_FEATURES),
                    "method": TR_METHOD,
                },
                "seq_len": seq_len,
                "epochs": epochs,
                "lr": lr,
                "bars": bars,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return {"digest": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()}
    except Exception:
        # A non-serialisable arguments shape is exotic; bypass the cache
        # entirely instead of ever aliasing distinct inputs to one entry.
        return None


def _tr_cache_get(key: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a deep copy of a live cached result, or None on miss/expiry."""
    if not _tr_cache_enabled() or not key:
        return None
    now = time.monotonic()
    with _TR_CACHE_LOCK:
        entry = _TR_CACHE.get(key["digest"])
        if entry is None:
            return None
        expires_at, result = entry
        if now >= expires_at:
            _TR_CACHE.pop(key["digest"], None)
            return None
        _TR_CACHE.move_to_end(key["digest"])
    return copy.deepcopy(result)


def _tr_cache_put(key: dict[str, Any] | None, result: dict[str, Any]) -> None:
    """Cache one available (True) result; never unavailable results."""
    if not _tr_cache_enabled() or not key:
        return
    if result.get("available") is not True:
        return
    expires_at = time.monotonic() + _tr_cache_ttl_seconds()
    with _TR_CACHE_LOCK:
        _TR_CACHE[key["digest"]] = (expires_at, copy.deepcopy(result))
        _TR_CACHE.move_to_end(key["digest"])
        while len(_TR_CACHE) > TR_CACHE_MAX_ENTRIES:
            _TR_CACHE.popitem(last=False)


def _tr_cache_size() -> int:
    """Number of currently held entries (tests/ops diagnostics only)."""
    with _TR_CACHE_LOCK:
        return len(_TR_CACHE)


def _tr_cache_clear() -> None:
    """Drop every cached Transformer result (tests/ops diagnostics only)."""
    with _TR_CACHE_LOCK:
        _TR_CACHE.clear()


def calculate_transformer_anomaly(
    bars: list[dict[str, Any]],
    seq_len: int = TR_DEFAULT_SEQ_LEN,
    epochs: int = TR_EPOCHS,
    lr: float = TR_LR,
) -> dict[str, Any]:
    """Score the latest bar with a small deterministic NumPy Transformer.

    Identical calls (same bars and parameters) reuse the cached result of the
    first computation: see the "Transformer result cache" section above for
    the exact key, bounding, ttl and disable controls. The returned payload is
    identical whether it came from training or from the cache; an
    ``available: False`` result is always freshly computed.

    The full method description is on
    ``_calculate_transformer_anomaly_uncached``.
    """
    key = _tr_cache_key(bars, seq_len, epochs, lr)
    cached = _tr_cache_get(key)
    if cached is not None:
        return cached
    result = _calculate_transformer_anomaly_uncached(bars, seq_len, epochs, lr)
    _tr_cache_put(key, result)
    return result


def _calculate_transformer_anomaly_uncached(
    bars: list[dict[str, Any]],
    seq_len: int = TR_DEFAULT_SEQ_LEN,
    epochs: int = TR_EPOCHS,
    lr: float = TR_LR,
) -> dict[str, Any]:
    """Score the latest bar with a small deterministic NumPy Transformer.

    Method
    ------
    1. Build causal features from consecutive real OHLCV bars: price return
       %, volume change % and True Range as a percentage of the previous
       close. A feature row for bar *i* never reads bar *i + 1*.
    2. Split features into historical rows (all but the latest) and the
       latest row. Build next-step prediction sequences from history only:
       features[t:t+seq_len] -> features[t+seq_len].
    3. Train the Transformer encoder on those historical sequences with MSE
       loss and SGD. The latest bar is excluded from training.
    4. Derive the anomaly threshold as mean(historical prediction errors)
       + 2 * std(historical prediction errors), computed only on training
       data.
    5. Score only the latest bar: prediction error of the latest feature
       vector vs the historical threshold.

    The returned ``anomaly_score`` is the latest bar's mean squared
    prediction error (higher = less like the training data).
    ``is_anomaly`` is true when ``anomaly_score > threshold``.

    Missing values, insufficient history, constant features, invalid
    parameters and training failures return ``available: False`` with an
    explanation rather than a fabricated flag. An anomaly is a statistical
    reconstruction outlier only: it is not proof of market manipulation and
    not a trading signal.
    """
    # ---------- parameter validation ----------
    valid_seq_len = (
        seq_len
        if isinstance(seq_len, int) and not isinstance(seq_len, bool) and seq_len >= 2
        else None
    )
    if valid_seq_len is None:
        return _tr_unavailable(
            "seq_len must be an integer >= 2.",
        )

    valid_epochs = (
        epochs
        if isinstance(epochs, int) and not isinstance(epochs, bool) and epochs >= 1
        else None
    )
    if valid_epochs is None:
        return _tr_unavailable(
            "epochs must be an integer >= 1.",
            history_observations=0,
        )

    valid_lr = (
        lr
        if isinstance(lr, (int, float)) and not isinstance(lr, bool)
        and math.isfinite(float(lr)) and float(lr) > 0
        else None
    )
    if valid_lr is None:
        return _tr_unavailable(
            "lr must be a finite positive number.",
            history_observations=0,
        )
    valid_lr = float(valid_lr)

    # ---------- bounded history for extended timeframes ----------
    model_bars = bars
    excluded_older_bars = 0
    if isinstance(bars, list) and len(bars) > TR_MAX_HISTORY_BARS:
        model_bars = bars[-TR_MAX_HISTORY_BARS:]
        excluded_older_bars = len(bars) - TR_MAX_HISTORY_BARS

    # ---------- feature extraction ----------
    rows = _lof_feature_rows(model_bars)

    if not rows:
        return _tr_unavailable(
            "No usable consecutive OHLCV observations: each Transformer row "
            "needs a bar plus the previous bar with close, high, low and "
            "positive volume.",
        )

    if rows[-1]["bar_index"] != len(model_bars) - 1:
        trailing_bars = _trailing_incomplete_bars(model_bars, rows[-1])
        if trailing_bars is None:
            return _tr_unavailable(
                "A returned bar after the latest scorable observation has complete "
                "OHLCV data but cannot form a feature row because an earlier bar is "
                "invalid, so the latest observation cannot be scored honestly.",
                history_observations=max(len(rows) - 1, 0),
                evaluated_timestamp=rows[-1]["timestamp"],
            )
        excluded_trailing_bars = len(trailing_bars)
    else:
        excluded_trailing_bars = 0

    history_rows = rows[:-1]
    latest_row = rows[-1]
    history_count = len(history_rows)

    if history_count < TR_MIN_HISTORY:
        return _tr_unavailable(
            f"Insufficient history: {history_count} usable historical "
            f"observations, but at least {TR_MIN_HISTORY} are required before "
            f"the latest bar can be scored.",
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    # Need enough training sequences: history_count - seq_len >= TR_MIN_SEQUENCES
    if history_count - valid_seq_len < TR_MIN_SEQUENCES:
        return _tr_unavailable(
            f"Insufficient training sequences: {history_count - valid_seq_len} "
            f"sequences built from {history_count} historical observations "
            f"with seq_len={valid_seq_len}, but at least {TR_MIN_SEQUENCES} "
            f"are required.",
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    # Standard windows keep the existing epoch count. Extended histories use a
    # bounded training budget so a 1Y/5Y timeframe remains responsive.
    effective_epochs = valid_epochs
    if (
        history_count > TR_FULL_TRAINING_HISTORY
        and valid_epochs > TR_EXTENDED_EPOCHS
    ):
        effective_epochs = TR_EXTENDED_EPOCHS

    # ---------- build training data ----------
    try:
        history_feats = [row["features"] for row in history_rows]
        latest_features = list(latest_row["features"])

        x_train, y_train = _tr_build_sequences(history_feats, valid_seq_len)
        if x_train is None:
            return _tr_unavailable(
                "Could not build training sequences from historical features.",
                history_observations=history_count,
                evaluated_timestamp=latest_row["timestamp"],
            )

        # ---------- train Transformer on history only ----------
        model = _TinyTransformer(
            d_model=TR_D_MODEL,
            n_heads=TR_N_HEADS,
            d_ff=TR_D_FF,
            seq_len=valid_seq_len,
            n_features=len(LOF_FEATURES),
            seed=TR_RANDOM_STATE,
        )

        n_samples = len(x_train)
        indices = np.arange(n_samples)
        for _epoch in range(effective_epochs):
            total_loss = 0.0
            for idx in indices:
                pred, cache = model.forward(x_train[idx])
                err = pred - y_train[idx]
                total_loss += float(np.mean(err ** 2))
                model.backward(err, cache, valid_lr)

        # ---------- historical prediction errors (training data only) ----------
        hist_errors = []
        for idx in range(n_samples):
            pred = model.predict(x_train[idx])
            err = float(np.mean((pred - y_train[idx]) ** 2))
            hist_errors.append(err)
        hist_errors = np.asarray(hist_errors, dtype=float)

        if not np.all(np.isfinite(hist_errors)):
            return _tr_unavailable(
                "Historical prediction errors contain non-finite values; "
                "threshold cannot be computed reliably.",
                history_observations=history_count,
                evaluated_timestamp=latest_row["timestamp"],
            )

        mean_error = float(np.mean(hist_errors))
        std_error = float(np.std(hist_errors))
        threshold = mean_error + TR_ANOMALY_K * std_error
        if not math.isfinite(threshold) or threshold <= 0:
            max_error = float(np.max(hist_errors))
            threshold = max_error if max_error > 0 else 1.0

        # ---------- score the latest bar separately ----------
        x_latest = np.asarray(history_feats[-valid_seq_len:], dtype=float)
        pred_latest = model.predict(x_latest)
        latest_array = np.asarray(latest_features, dtype=float)
        anomaly_score = float(np.mean((pred_latest - latest_array) ** 2))

    except Exception as exc:  # keep analysis failures out of the API error path
        return _tr_unavailable(
            f"Transformer computation failed: {type(exc).__name__}: {exc}",
            history_observations=history_count,
            evaluated_timestamp=latest_row["timestamp"],
        )

    is_anomaly = bool(anomaly_score > threshold)
    timestamp = latest_row["timestamp"]
    latest_features_dict = {
        name: round(float(value), 4)
        for name, value in zip(LOF_FEATURES, latest_row["features"])
    }
    latest_prediction_dict = {
        name: round(float(value), 4)
        for name, value in zip(LOF_FEATURES, pred_latest)
    }

    prefix = (
        f"The Transformer was trained only on the {history_count} observations "
        f"before the latest bar; the latest bar is scored separately as a "
        f"prediction error, so no future data was used."
    )
    if excluded_older_bars:
        prefix += (
            f" Only the most recent {TR_MAX_HISTORY_BARS} returned bars were "
            f"used for training; {excluded_older_bars} older bars were excluded "
            f"to keep extended timeframes responsive."
        )
    if effective_epochs != valid_epochs:
        prefix += (
            f" Training used {effective_epochs} epochs instead of "
            f"{valid_epochs} for the same reason."
        )
    if excluded_trailing_bars:
        prefix += (
            f" {excluded_trailing_bars} trailing returned bar(s) with missing or "
            f"invalid OHLCV were excluded, so the latest complete bar was scored."
        )

    if is_anomaly:
        explanation = (
            f"Transformer flagged the latest bar ({timestamp}): prediction "
            f"error {anomaly_score:.4f} is above the anomaly threshold "
            f"{threshold:.4f} (mean + {TR_ANOMALY_K} * std of historical "
            f"prediction errors). {prefix} This is a statistical outlier, "
            f"not proof of market manipulation or a guaranteed trading signal."
        )
    else:
        explanation = (
            f"The latest bar ({timestamp}) is not flagged: prediction "
            f"error {anomaly_score:.4f} is at or below the anomaly threshold "
            f"{threshold:.4f} (mean + {TR_ANOMALY_K} * std of "
            f"historical prediction errors). {prefix}"
        )

    return {
        "available": True,
        "is_anomaly": is_anomaly,
        "anomaly_score": round(anomaly_score, 4),
        "threshold": round(threshold, 4),
        "features_used": list(LOF_FEATURES),
        "latest_features": latest_features_dict,
        "latest_prediction": latest_prediction_dict,
        "evaluated_timestamp": timestamp,
        "history_observations": history_count,
        "history_limit": TR_MAX_HISTORY_BARS,
        "excluded_older_bars": excluded_older_bars,
        "excluded_trailing_bars": excluded_trailing_bars,
        "training_epochs": effective_epochs,
        "method": TR_METHOD,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Timestamped historical anomaly evaluation
# ---------------------------------------------------------------------------

# The deterministic detectors are cheap enough to evaluate causally on every
# returned candle. LOF requires a model fit per scored candle, so its walk-
# forward scan is deliberately bounded. These limits are returned to clients;
# they are not silently applied.
HISTORICAL_LOF_MAX_TARGETS = 80
HISTORICAL_LOF_HISTORY_WINDOW = 240

HISTORICAL_SCOPE_NOTE = (
    "Historical points are evaluated causally: each point uses only the bar it "
    "describes and bars before it. No future bar, fabricated timestamp or "
    "interpolated price is used."
)

HISTORICAL_LIMITATIONS = [
    HISTORICAL_SCOPE_NOTE,
    "Historical markers are limited to the bars returned for the selected "
    "timeframe and interval; nothing outside that response is inferred.",
    "Autoencoder and Transformer historical scores are not produced because "
    "their existing training routines would need to be repeated for every "
    "candle. They remain latest-complete-bar checks and never contribute "
    "historical markers.",
    "A statistical anomaly or divergence is an unusual observation, not proof "
    "of market manipulation, a prediction, or investment advice.",
]


def _historical_timestamp(bar: dict[str, Any]) -> str | None:
    """Return a usable returned timestamp, or None when it is missing."""
    timestamp = bar.get("timestamp") if isinstance(bar, dict) else None
    if isinstance(timestamp, str) and timestamp.strip():
        return timestamp
    return None


def _historical_point(
    bar: dict[str, Any],
    *,
    score: float | None,
    threshold: float | None,
    score_label: str | None,
    values: dict[str, Any],
    explanation: str,
    signal: str | None = None,
) -> dict[str, Any]:
    """Build one timestamped historical point tied to a real returned bar."""
    return {
        "timestamp": bar.get("timestamp"),
        "is_anomaly": True,
        "score": score,
        "threshold": threshold,
        "score_label": score_label,
        "signal": signal,
        "values": values,
        "explanation": explanation,
    }


def _historical_detector(
    *,
    available: bool,
    points: list[dict[str, Any]],
    evaluated_bars: int,
    method: str,
    limitations: list[str] | None = None,
    reason: str | None = None,
    scope: str = "historical",
    detected_bars: int | None = None,
) -> dict[str, Any]:
    """Return the stable detector envelope used by the API and frontend."""
    payload: dict[str, Any] = {
        "available": bool(available),
        "historical_available": scope == "historical" and bool(available),
        "scope": scope,
        "points": points,
        "marked_bars": len(points),
        "evaluated_bars": int(evaluated_bars),
        "method": method,
        "limitations": list(limitations or []),
    }
    if detected_bars is not None:
        payload["detected_bars"] = int(detected_bars)
    if reason:
        payload["reason"] = reason
    return payload


def _historical_volume_spikes(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate volume spikes on every usable historical bar, causally.

    The existing latest-bar detector compares the target volume with the mean
    of all preceding positive volumes and calls a ratio above 2.0 a spike.
    Re-running that same calculation on each prefix preserves its threshold
    and avoids look-ahead.

    Implementation note: the baseline for the target bar is exactly the
    growing set of preceding positive volumes, so incremental running totals
    (left-fold sum, same order as ``sum()``) reproduce the per-prefix
    calculation bit-for-bit without re-listing the whole prefix per bar.
    """
    points: list[dict[str, Any]] = []
    evaluated = 0
    period = VOLUME_SPIKE_DEFAULT_PERIOD

    # Running totals over bars 0..i-1 while bar i (the target) is evaluated.
    # The physics of the original detector: every bar with a non-None volume
    # joins the volume count; strictly positive ones join the baseline.
    count_volumes = 0
    baseline_sum = 0  # left-fold sum of positive volumes, same order as sum()
    baseline_count = 0

    for bar in bars:
        if _bar_has_complete_ohlcv(bar) and _historical_timestamp(bar):
            # The prefix the original detector sees ends at this bar, so the
            # volume count includes the target bar while the baseline
            # (strictly positive volumes before it) does not.
            available = count_volumes + 1 >= period + 1 and baseline_count > 0
            if available:
                latest_volume = bar.get("volume")
                baseline_avg = baseline_sum / baseline_count
                volume_ratio = latest_volume / baseline_avg
                evaluated += 1
                if volume_ratio > 2.0:
                    rounded_ratio = round(volume_ratio, 4)
                    points.append(
                        _historical_point(
                            bar,
                            score=rounded_ratio,
                            threshold=2.0,
                            score_label="Volume ratio",
                            values={
                                "latest_volume": latest_volume,
                                "baseline_average": round(baseline_avg, 2),
                                "percent_above_baseline": round(
                                    ((latest_volume - baseline_avg) / baseline_avg) * 100, 2
                                ),
                            },
                            signal="spike",
                            explanation=(
                                f"Volume on {bar.get('timestamp')} was {rounded_ratio}x the average "
                                "of all preceding positive volumes in the returned series; "
                                "the existing spike threshold is > 2.0x. This is unusual "
                                "volume, not proof of manipulation."
                            ),
                        )
                    )

        # Advance the running state for every bar (gated or not), exactly like
        # the prefix the next bar would see.
        volume = bar.get("volume")
        if volume is not None:
            count_volumes += 1
            if volume > 0:
                baseline_sum = baseline_sum + volume
                baseline_count += 1

    if evaluated == 0:
        return _historical_detector(
            available=False,
            points=[],
            evaluated_bars=0,
            method=(
                "Re-evaluates the existing volume-spike calculation on each "
                "returned prefix; spike when the target volume is > 2.0x the "
                "mean of preceding positive volumes."
            ),
            reason=(
                "At least 21 complete OHLCV bars are needed before a historical "
                "volume baseline can be evaluated."
            ),
        )

    return _historical_detector(
        available=True,
        points=points,
        evaluated_bars=evaluated,
        method=(
            "Walk-forward re-evaluation of the existing volume-spike formula. "
            "For each complete historical bar, only that bar and earlier bars "
            "enter the baseline; the existing spike threshold is ratio > 2.0x."
        ),
    )


def _historical_volatility_anomalies(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the existing ATR anomaly on each historical prefix.

    Implementation note: True Range for bar *i* depends only on bars *i* and
    *i - 1*, and the per-prefix baseline is exactly the set of valid TR values
    that have slid out of the fixed-size recent window. Incremental state
    (same left-fold summation order as the per-prefix ``sum()`` calls)
    reproduces the per-prefix calculation bit-for-bit without re-scanning
    every prefix.
    """
    points: list[dict[str, Any]] = []
    evaluated = 0
    period = VOLATILITY_ANOMALY_DEFAULT_PERIOD

    prev_close: float | None = None
    recent: deque[float] = deque()  # the last `period` valid TR values, oldest first
    baseline_sum = 0.0  # left-fold sum over valid TR values that left the window
    baseline_count = 0
    valid_count = 0  # all valid TR values seen so far

    for bar in bars:
        high = bar.get("high")
        low = bar.get("low")
        close = bar.get("close")

        # Exactly _true_range's per-bar logic: missing values yield no TR and
        # do not update prev_close.
        if high is None or low is None or close is None:
            tr: float | None = None
        elif prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

        if tr is not None:
            valid_count += 1
            if len(recent) == period:
                # The oldest window value permanently joins the baseline, in
                # the same chronological order the prefix baseline would use.
                left = recent.popleft()
                baseline_sum = baseline_sum + left
                baseline_count += 1
            recent.append(tr)

        if _bar_has_complete_ohlcv(bar) and _historical_timestamp(bar):
            # The prefix the original detector sees ends at this bar, so the
            # state above (which already includes this bar's TR) is the exact
            # per-prefix state.
            if valid_count >= period + 1 and baseline_count > 0:
                recent_atr = sum(recent) / period
                baseline_atr = baseline_sum / baseline_count
                if baseline_atr != 0:
                    volatility_ratio = recent_atr / baseline_atr
                    evaluated += 1
                    if volatility_ratio > 2.0:
                        rounded_ratio = round(volatility_ratio, 4)
                        percent_change = ((recent_atr - baseline_atr) / baseline_atr) * 100
                        points.append(
                            _historical_point(
                                bar,
                                score=rounded_ratio,
                                threshold=2.0,
                                score_label="Volatility ratio",
                                values={
                                    "recent_atr": round(recent_atr, 2),
                                    "baseline_atr": round(baseline_atr, 2),
                                    "percent_change": round(percent_change, 2),
                                },
                                signal="anomalous",
                                explanation=(
                                    f"Recent ATR on {bar.get('timestamp')} was {rounded_ratio}x the "
                                    "preceding baseline ATR; the existing anomalous threshold "
                                    "is > 2.0x. This describes unusually large price ranges, "
                                    "not manipulation or a future move."
                                ),
                            )
                        )

        if close is not None:
            prev_close = close

    if evaluated == 0:
        return _historical_detector(
            available=False,
            points=[],
            evaluated_bars=0,
            method=(
                "Re-evaluates the existing 20-bar ATR comparison on each "
                "returned prefix; anomalous when ratio > 2.0x."
            ),
            reason=(
                "At least 21 usable True Range observations are needed before "
                "a historical volatility baseline can be evaluated."
            ),
        )

    return _historical_detector(
        available=True,
        points=points,
        evaluated_bars=evaluated,
        method=(
            "Walk-forward re-evaluation of the existing volatility anomaly. "
            "For each complete historical bar, the recent 20-bar ATR is "
            "compared with the ATR of preceding returned bars only; the "
            "existing > 2.0x threshold marks an anomalous observation."
        ),
    )


def _historical_divergences(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the existing directional divergence signal on each prefix.

    Divergence is intentionally signal-based. The existing algorithm has no
    numeric anomaly threshold, so none is invented here. Only the meaningful
    ``bullish`` and ``bearish`` signals become historical points.

    Implementation note: the per-prefix close and valid-volume lists are grown
    incrementally and only the fixed ``period``-long lookback window is
    re-read for each bar, so the per-prefix calculation is reproduced exactly
    without re-listing the whole prefix per bar.
    """
    points: list[dict[str, Any]] = []
    evaluated = 0
    detected = 0
    previous_signal: str | None = None
    period = DIVERGENCE_DEFAULT_PERIOD

    # Incremental per-prefix lists (the prefix that ends at the current bar).
    closes: list[float] = []
    volumes: list[float] = []

    def _pct_change_window(values: list[float], window_first: float, window_last: float) -> float | None:
        """Replicate _pct_change on the lookback slice without re-slicing."""
        if len(values) < 2 or window_first == 0:
            return None
        return ((window_last - window_first) / abs(window_first)) * 100

    for bar in bars:
        # Advance the per-prefix lists first: the prefix seen by the original
        # detector ends at (and includes) the current bar.
        close = bar.get("close")
        if close is not None:
            closes.append(close)
        volume = bar.get("volume")
        if volume is not None and volume != 0:
            volumes.append(volume)

        if not (_bar_has_complete_ohlcv(bar) and _historical_timestamp(bar)):
            continue

        if len(closes) < period + 1 or len(volumes) < period + 1:
            continue

        # price change over closes[-(period+1):-1]: first vs last of the slice
        price_pct = _pct_change_window(
            closes, closes[len(closes) - 1 - period], closes[len(closes) - 2]
        )
        if price_pct is None:
            # fallback: use all available closes (but still insufficient for lookback)
            price_pct = (
                None
                if closes[0] == 0
                else ((closes[-1] - closes[0]) / abs(closes[0])) * 100
            )

        volume_pct = _pct_change_window(
            volumes, volumes[len(volumes) - 1 - period], volumes[len(volumes) - 2]
        )
        if volume_pct is None:
            volume_pct = (
                None
                if volumes[0] == 0
                else ((volumes[-1] - volumes[0]) / abs(volumes[0])) * 100
            )

        if price_pct is None or volume_pct is None:
            continue

        price_up = price_pct > 0
        price_down = price_pct < 0
        volume_up = volume_pct > 0
        volume_down = volume_pct < 0

        if price_up and volume_down:
            signal = "bearish"
            explanation = (
                f"price rose {price_pct:.2f}% while volume fell {abs(volume_pct):.2f}% — "
                f"bearish divergence, potential resistance"
            )
        elif price_down and volume_up:
            signal = "bullish"
            explanation = (
                f"price fell {abs(price_pct):.2f}% while volume rose {volume_pct:.2f}% — "
                f"bullish divergence, potential support"
            )
        elif (price_up and volume_up) or (price_down and volume_down):
            signal = "aligned"
            explanation = (
                f"price {abs(price_pct):.2f}% and volume {abs(volume_pct):.2f}% moved in the same direction — "
                f"aligned movement, no divergence"
            )
        else:
            # Both zero or both very small
            signal = "neutral"
            explanation = (
                f"price changed {price_pct:.2f}% and volume changed {volume_pct:.2f}% — "
                f"no meaningful divergence"
            )

        evaluated += 1
        if signal not in ("bullish", "bearish"):
            previous_signal = signal
            continue

        detected += 1
        # The directional comparison can stay bearish for many sessions in a
        # row. Mark the first bar of each sustained run so the chart shows a
        # readable event without hiding the detector's ongoing context.
        if signal == previous_signal:
            continue
        previous_signal = signal

        points.append(
            _historical_point(
                bar,
                score=None,
                threshold=None,
                score_label=None,
                values={
                    "price_change_percent": round(price_pct, 2),
                    "volume_change_percent": round(volume_pct, 2),
                },
                signal=signal,
                explanation=(
                    f"{explanation} This is the first bar "
                    "of a sustained directional volume-price run. It is a "
                    "signal, not a numeric anomaly score; no threshold is "
                    "claimed."
                ).strip(),
            )
        )

    if evaluated == 0:
        return _historical_detector(
            available=False,
            points=[],
            evaluated_bars=0,
            method=(
                "Re-evaluates the existing 20-bar price/volume direction "
                "comparison on each returned prefix. Signal-based; no numeric "
                "anomaly threshold exists."
            ),
            reason=(
                "Insufficient usable closes or positive volumes to evaluate a "
                "20-bar historical divergence."
            ),
        )

    return _historical_detector(
        available=True,
        points=points,
        evaluated_bars=evaluated,
        detected_bars=detected,
        method=(
            "Walk-forward re-evaluation of the existing divergence formula. "
            "For each complete historical bar, price and volume changes are "
            "compared over the prior 20-bar lookback. One point marks the "
            "first bar of each sustained bullish/bearish run; the signal is "
            "context, not a scored anomaly threshold."
        ),
        limitations=[
            "Divergence is a directional signal with no numeric anomaly "
            "threshold. Its points have no score or threshold, and no score "
            "was invented.",
            "To keep sustained runs readable, only the first bar of each "
            "bullish/bearish run is marked; the detector's signal can persist "
            "on later returned bars.",
        ],
    )


def _historical_lof(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Score recent historical bars using only prior observations.

    A separate StandardScaler and LOF novelty model is fitted for each target
    bar. To keep the endpoint responsive, each model sees at most the trailing
    ``HISTORICAL_LOF_HISTORY_WINDOW`` feature rows and the scan covers at most
    the most recent ``HISTORICAL_LOF_MAX_TARGETS`` scorable targets. The
    detector's established neighbours, contamination and threshold logic are
    unchanged.
    """
    rows = _lof_feature_rows(bars)
    min_history = max(LOF_MIN_HISTORY, LOF_DEFAULT_NEIGHBORS + 1)

    candidates: list[int] = []
    for target_index in range(len(rows)):
        history_start = max(0, target_index - HISTORICAL_LOF_HISTORY_WINDOW)
        if target_index - history_start >= min_history:
            candidates.append(target_index)

    if not candidates:
        return _historical_detector(
            available=False,
            points=[],
            evaluated_bars=0,
            method=LOF_METHOD,
            reason=(
                f"Insufficient history: at least {min_history} usable feature "
                "observations are required before a bar can be scored."
            ),
            limitations=[
                "LOF historical evaluation is walk-forward and bounded; it "
                "does not backfill scores that cannot be computed causally."
            ],
        )

    selected = candidates[-HISTORICAL_LOF_MAX_TARGETS:]
    points: list[dict[str, Any]] = []
    evaluated = 0

    for target_index in selected:
        target_row = rows[target_index]
        target_bar = bars[target_row["bar_index"]]
        target_timestamp = _historical_timestamp(target_bar)
        if target_timestamp is None:
            continue
        history_start = max(0, target_index - HISTORICAL_LOF_HISTORY_WINDOW)
        history_rows = rows[history_start:target_index]
        if len(history_rows) < min_history:
            continue

        try:
            history_matrix = np.asarray(
                [row["features"] for row in history_rows], dtype=float
            )
            target_vector = np.asarray(
                target_row["features"], dtype=float
            ).reshape(1, -1)
            if not np.all(np.isfinite(history_matrix)) or not np.all(
                np.isfinite(target_vector)
            ):
                continue

            combined = np.vstack([history_matrix, target_vector])
            if np.all(np.ptp(combined, axis=0) == 0):
                continue

            scaler = StandardScaler().fit(history_matrix)
            model = LocalOutlierFactor(
                n_neighbors=LOF_DEFAULT_NEIGHBORS,
                novelty=True,
                contamination=LOF_DEFAULT_CONTAMINATION,
            )
            model.fit(scaler.transform(history_matrix))
            score = float(
                -model.score_samples(scaler.transform(target_vector))[0]
            )
            threshold = float(-model.offset_)
            decision_value = float(
                model.decision_function(scaler.transform(target_vector))[0]
            )
        except Exception:
            # A single numerical failure must not invent a point or break the
            # analysis response. The detector's envelope still reports scope.
            continue

        evaluated += 1
        if decision_value >= 0:
            continue

        points.append(
            _historical_point(
                target_bar,
                score=round(score, 4),
                threshold=round(threshold, 4),
                score_label="LOF score",
                values={
                    name: round(float(value), 4)
                    for name, value in zip(
                        LOF_FEATURES, target_row["features"]
                    )
                },
                signal="density_outlier",
                explanation=(
                    f"LOF flagged {target_timestamp} as a density outlier: "
                    f"score {score:.2f} is above the threshold {threshold:.2f} "
                    f"(n_neighbors={LOF_DEFAULT_NEIGHBORS}, "
                    f"contamination={LOF_DEFAULT_CONTAMINATION:g}). The model "
                    f"was fitted on {len(history_rows)} earlier feature rows "
                    "only, so no future data was used. This is a statistical "
                    "outlier, not proof of manipulation."
                ),
            )
        )

    if evaluated == 0:
        return _historical_detector(
            available=False,
            points=[],
            evaluated_bars=0,
            method=LOF_METHOD,
            reason=(
                "LOF historical evaluation could not fit a model with varying "
                "features on the available prior observations."
            ),
        )

    return _historical_detector(
        available=True,
        points=points,
        evaluated_bars=evaluated,
        method=(
            f"Walk-forward LOF using the existing {LOF_DEFAULT_NEIGHBORS}-"
            f"neighbour and {LOF_DEFAULT_CONTAMINATION:g} contamination "
            "settings. Each target is scored by a StandardScaler and novelty "
            f"LOF fitted on up to {HISTORICAL_LOF_HISTORY_WINDOW} preceding "
            "feature rows only. The most recent "
            f"{HISTORICAL_LOF_MAX_TARGETS} scorable bars are evaluated."
        ),
        limitations=[
            "LOF historical markers are limited to a bounded recent "
            "walk-forward scan to keep the endpoint responsive.",
            "The latest complete bar's separate card may use a longer history "
            "than the historical walk-forward points.",
        ],
    )


def _latest_only_detector(
    *, name: str, method: str, limitation: str
) -> dict[str, Any]:
    """Describe a detector that only supports the latest complete bar."""
    return _historical_detector(
        available=False,
        points=[],
        evaluated_bars=0,
        method=method,
        reason=(
            f"{name} remains a latest-complete-bar check. No historical series "
            "is returned or inferred."
        ),
        scope="latest_only",
        limitations=[limitation],
    )


def calculate_historical_anomalies(
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return timestamped anomaly points that can be computed causally.

    Supported historical sources are volume spike, volatility anomaly,
    volume-price divergence and a bounded walk-forward LOF. Autoencoder and
    Transformer remain latest-only because their existing training loops would
    be prohibitively expensive to repeat for every historical candle. Every
    returned point is tied to a real bar timestamp and every score/threshold is
    taken from the corresponding existing detector.
    """
    source = bars if isinstance(bars, list) else []
    detectors = {
        "volume_spike": _historical_volume_spikes(source),
        "volatility_anomaly": _historical_volatility_anomalies(source),
        "volume_price_divergence": _historical_divergences(source),
        "lof_anomaly": _historical_lof(source),
        "autoencoder_anomaly": _latest_only_detector(
            name="Autoencoder",
            method=AE_METHOD,
            limitation=(
                "Autoencoder historical markers are omitted rather than "
                "retraining the neural network for every candle. Its latest "
                "card is still computed by the existing endpoint."
            ),
        ),
        "transformer_anomaly": _latest_only_detector(
            name="Transformer",
            method=TR_METHOD,
            limitation=(
                "Transformer historical markers are omitted rather than "
                "retraining the sequence model for every candle. Its latest "
                "card is still computed by the existing endpoint, using a "
                "bounded recent history and reduced epoch budget on extended "
                "timeframes."
            ),
        ),
    }

    return {
        "available": any(
            block.get("available") is True for block in detectors.values()
        ),
        "scope": HISTORICAL_SCOPE_NOTE,
        "bar_count": len(source),
        "detectors": detectors,
        "latest_only_detectors": [
            "autoencoder_anomaly",
            "transformer_anomaly",
        ],
        "limitations": list(HISTORICAL_LIMITATIONS),
    }