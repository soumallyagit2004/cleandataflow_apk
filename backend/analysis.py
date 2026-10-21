"""Explainable technical indicators calculated only from returned price bars."""

import math
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
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "percent_above_baseline": round(percent_above, 2) if percent_above is not None else None,
        "signal": label,
        "available": True,
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
        "volatility_ratio": round(volatility_ratio, 2) if volatility_ratio is not None else None,
        "percent_change": round(percent_change, 2) if percent_change is not None else None,
        "signal": label,
        "available": True,
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


def calculate_transformer_anomaly(
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

    # ---------- feature extraction ----------
    rows = _lof_feature_rows(bars)

    if not rows:
        return _tr_unavailable(
            "No usable consecutive OHLCV observations: each Transformer row "
            "needs a bar plus the previous bar with close, high, low and "
            "positive volume.",
        )

    if rows[-1]["bar_index"] != len(bars) - 1:
        trailing_bars = _trailing_incomplete_bars(bars, rows[-1])
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
        for _epoch in range(valid_epochs):
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
        "excluded_trailing_bars": excluded_trailing_bars,
        "method": TR_METHOD,
        "explanation": explanation,
    }