"""Unit tests for analysis.py - RSI, SMA, Fibonacci edge cases, EMA deviation, divergence, volume spike, volatility anomaly, and LOF."""

import json
import math
import random
import time as time_module
from datetime import datetime, timedelta, timezone

import pytest

import analysis as _analysis
from analysis import (
    analyze_bars,
    calculate_divergence,
    calculate_ema_deviation,
    calculate_lof_anomaly,
    calculate_bollinger_bands,
    calculate_transformer_anomaly,
    calculate_volume_spike,
    calculate_volatility_anomaly,
    calculate_historical_anomalies,
    TR_EXTENDED_EPOCHS,
    TR_MAX_HISTORY_BARS,
)
from typing import Any


@pytest.fixture(autouse=True)
def _isolated_transformer_cache():
    """Start every test in this module with an empty Transformer cache so
    cached and freshly trained calls are independent."""
    _analysis._tr_cache_clear()
    yield
    _analysis._tr_cache_clear()


def make_bar(close, high=None, low=None, open_price=None, volume=1000):
    """Helper to build a single bar dict."""
    return {
        "close": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "open": open_price if open_price is not None else close,
        "volume": volume,
    }


class TestEmptyBars:
    def test_empty_bars_returns_none_indicators(self):
        result = analyze_bars([])
        assert result["sma"]["available"] is False
        assert result["sma"]["value"] is None
        assert result["rsi"]["available"] is False
        assert result["rsi"]["value"] is None
        assert result["fibonacci"] is None


class TestInsufficientHistory:
    def test_insufficient_bars_for_sma(self):
        bars = [make_bar(100 + i) for i in range(5)]
        result = analyze_bars(bars)
        assert result["sma"]["available"] is False
        assert result["sma"]["value"] is None

    def test_insufficient_bars_for_rsi(self):
        bars = [make_bar(100 + i) for i in range(10)]
        result = analyze_bars(bars)
        assert result["rsi"]["available"] is False
        assert result["rsi"]["value"] is None

    def test_exactly_20_bars_gives_sma(self):
        bars = [make_bar(100 + i) for i in range(20)]
        result = analyze_bars(bars)
        assert result["sma"]["available"] is True
        expected_sma = sum(100 + i for i in range(20)) / 20
        assert abs(result["sma"]["value"] - expected_sma) < 1e-9

    def test_exactly_15_bars_gives_rsi(self):
        bars = [make_bar(100 + i) for i in range(15)]
        result = analyze_bars(bars)
        assert result["rsi"]["available"] is True


class TestMissingValues:
    def test_none_close_values_excluded(self):
        bars = [
            make_bar(None),
            make_bar(100),
            make_bar(None),
            make_bar(102),
        ]
        result = analyze_bars(bars)
        assert result["sma"]["available"] is False
        assert result["rsi"]["available"] is False

    def test_none_high_low_excluded_from_fib(self):
        bars = [
            {"close": 100, "high": None, "low": None, "open": 100, "volume": 1000},
            {"close": 101, "high": None, "low": None, "open": 101, "volume": 1000},
        ]
        result = analyze_bars(bars)
        assert result["fibonacci"] is None


class TestConstantPrices:
    def test_all_gains_rsi_100(self):
        bars = [make_bar(100 + i) for i in range(20)]
        result = analyze_bars(bars)
        assert result["rsi"]["available"] is True
        assert result["rsi"]["value"] == 100.0

    def test_all_losses_rsi_0(self):
        bars = [make_bar(100 - i) for i in range(20)]
        result = analyze_bars(bars)
        assert result["rsi"]["available"] is True
        assert result["rsi"]["value"] == 0.0

    def test_flat_prices_rsi_50(self):
        bars = [make_bar(100) for _ in range(20)]
        result = analyze_bars(bars)
        assert result["rsi"]["available"] is True
        assert result["rsi"]["value"] == 50.0


class TestZeroFibonacciRange:
    def test_zero_span_fibonacci_none(self):
        bars = [make_bar(100) for _ in range(20)]
        result = analyze_bars(bars)
        assert result["fibonacci"] is None

    def test_single_bar_fibonacci_none(self):
        bars = [make_bar(100)]
        result = analyze_bars(bars)
        assert result["fibonacci"] is None


class TestNormalCase:
    def test_normal_analysis_returns_all_values(self):
        bars = [make_bar(100 + i * 0.5) for i in range(30)]
        result = analyze_bars(bars)
        assert result["sma"]["available"] is True
        assert result["rsi"]["available"] is True
        assert result["fibonacci"] is not None
        assert result["fibonacci"]["rangeHigh"] > result["fibonacci"]["rangeLow"]
        assert "0.236" in result["fibonacci"]["levels"]
        assert "0.618" in result["fibonacci"]["levels"]

    def test_sma_uses_latest_n_closes(self):
        bars = [make_bar(float(i)) for i in range(25)]
        result = analyze_bars(bars, sma_period=5)
        expected = (20 + 21 + 22 + 23 + 24) / 5
        assert abs(result["sma"]["value"] - expected) < 1e-9

    def test_rsi_period_param(self):
        bars = [make_bar(100 + i) for i in range(20)]
        result = analyze_bars(bars, rsi_period=9)
        assert result["rsi"]["period"] == 9
        assert result["rsi"]["available"] is True

    def test_fibonacci_levels_are_retracements_from_high(self):
        bars = [make_bar(100 + i) for i in range(20)]
        result = analyze_bars(bars)
        fib = result["fibonacci"]
        assert fib["rangeHigh"] == 119.0
        assert fib["rangeLow"] == 100.0
        assert abs(fib["levels"]["0.5"] - 109.5) < 1e-9


class TestEMADeviation:
    """EMA deviation calculation using real closing prices from bars."""

    def _make_bars(self, closes: list[float]) -> list[dict[str, Any]]:
        """Helper: build bars from a close-price list; other fields default sensibly."""
        return [
            {
                "close": c,
                "high": c + 2,
                "low": c - 2,
                "open": c + 1,
                "volume": 1000,
            }
            for c in closes
        ]

    def test_positive_deviation(self):
        bars = self._make_bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0,
                                110.0, 111.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0, 118.0, 119.0,
                                120.0, 121.0, 122.0, 123.0, 124.0, 125.0, 126.0, 127.0, 128.0, 129.0,
                                130.0])
        result = calculate_ema_deviation(bars, period=20)
        assert result["available"] is True
        assert result["ema"] is not None
        assert result["latest_close"] is not None
        assert result["deviation_percent"] > 0
        assert result["period"] == 20

    def test_negative_deviation(self):
        bars = self._make_bars([130.0, 129.0, 128.0, 127.0, 126.0, 125.0, 124.0, 123.0, 122.0, 121.0,
                                120.0, 119.0, 118.0, 117.0, 116.0, 115.0, 114.0, 113.0, 112.0, 111.0,
                                110.0, 109.0, 108.0, 107.0, 106.0, 105.0, 104.0, 103.0, 102.0, 101.0,
                                100.0])
        result = calculate_ema_deviation(bars, period=20)
        assert result["available"] is True
        assert result["deviation_percent"] < 0

    def test_zero_deviation(self):
        bars = self._make_bars([100.0] * 30)
        result = calculate_ema_deviation(bars, period=20)
        assert result["available"] is True
        assert abs(result["deviation_percent"]) < 1e-6 or result["deviation_percent"] == 0.0

    def test_insufficient_history(self):
        bars = self._make_bars([100.0] * 10)
        result = calculate_ema_deviation(bars, period=20)
        assert result["available"] is False
        assert result["ema"] is None
        assert result["deviation_percent"] is None

    def test_invalid_period(self):
        bars = self._make_bars([100.0] * 30)
        result = calculate_ema_deviation(bars, period=7)
        assert result["available"] is False
        assert result["period"] == 7
        assert result["method"] is not None

    def test_empty_bars(self):
        result = calculate_ema_deviation([], period=20)
        assert result["available"] is False
        assert result["ema"] is None
        assert result["deviation_percent"] is None

    def test_all_missing_closes(self):
        bars = [{"close": None, "high": 101, "low": 99, "open": 101, "volume": 1000} for _ in range(30)]
        result = calculate_ema_deviation(bars, period=20)
        assert result["available"] is False
        assert result["ema"] is None
        assert result["deviation_percent"] is None


class TestDivergence:
    """Divergence analysis using real OHLCV bars."""

    def _make_bars(self, closes, volumes=None):
        if volumes is None:
            volumes = [1000] * len(closes)
        return [
            {
                "close": c,
                "high": c + 5,
                "low": c - 5,
                "open": c + 2,
                "volume": v,
            }
            for c, v in zip(closes, volumes)
        ]

    def test_bearish_divergence(self):
        """Rising price with weakening volume."""
        bars = self._make_bars([100, 101, 102, 103, 104, 105, 106, 106, 107, 108, 109, 110, 111, 111, 112, 113, 114, 115, 115, 116, 117],
                               volumes=[2000, 1900, 1800, 1700, 1600, 1500, 1400, 1300, 1200, 1100, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100, 50])
        result = calculate_divergence(bars, period=5)
        assert result["available"] is True
        assert result["signal"] == "bearish"

    def test_bullish_divergence(self):
        """Falling price with rising volume."""
        bars = self._make_bars([120, 119, 118, 117, 116, 115, 114, 113, 112, 111, 110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 100],
                               volumes=[50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000])
        result = calculate_divergence(bars, period=5)
        assert result["available"] is True
        assert result["signal"] == "bullish"

    def test_aligned_movement(self):
        """Price and volume both rising."""
        bars = self._make_bars([100, 101, 102, 103, 104, 104, 105, 106, 107, 108, 109, 110, 111, 112, 112, 113, 114, 115, 116, 117, 118],
                               volumes=[500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1700, 1800, 1800, 1900, 2000, 2000, 2100, 2200])
        result = calculate_divergence(bars, period=5)
        assert result["available"] is True
        assert result["signal"] == "aligned"

    def test_insufficient_history(self):
        """Not enough bars for the lookback period."""
        bars = self._make_bars([100, 101, 102])
        result = calculate_divergence(bars, period=5)
        assert result["available"] is False
        assert result["signal"] is None

    def test_zero_volume_handled_honestly(self):
        """Bars with zero volume should not crash; divergence marked unavailable."""
        bars = self._make_bars([100, 101, 102, 103, 105, 106, 106, 107, 108, 109, 110, 111, 111, 112, 113, 114, 115, 116, 117, 118],
                               volumes=[0] * 20)
        result = calculate_divergence(bars, period=5)
        assert result["available"] is False
        assert result["signal"] is None

    def test_all_missing_values(self):
        """Bars with None closes should be handled."""
        bars = [{"close": None, "high": 105, "low": 95, "open": 103, "volume": 1000} for _ in range(30)]
        result = calculate_divergence(bars, period=5)
        assert result["available"] is False
        assert result["signal"] is None

    def test_invalid_period(self):
        """Unsupported divergence period."""
        bars = self._make_bars([100.0] * 30)
        result = calculate_divergence(bars, period=7)
        assert result["available"] is False
        assert result["period"] == 7
        assert result["explanation"] is not None

    def test_neutral_movement(self):
        """Price and volume both falling."""
        bars = self._make_bars([120, 119, 118, 117, 116, 115, 114, 113, 112, 111, 110, 109, 108, 107, 107, 106, 105, 104, 103, 102, 101],
                               volumes=[2000, 1900, 1800, 1700, 1600, 1500, 1400, 1300, 1200, 1100, 1000, 900, 900, 800, 700, 600, 500, 400, 300, 200])
        result = calculate_divergence(bars, period=5)
        assert result["available"] is True
        assert result["signal"] == "aligned"


class TestDivergenceWindowRegression:
    """Regression tests for the aligned change window.

    The change must be measured from the first to the last bar of the trailing
    eligible window, so the latest eligible bar is evaluated, price and volume
    share the same two endpoint bars, and zero-volume bars never shift one
    horizon relative to the other.
    """

    def _make_bars(self, closes, volumes, timestamps=True):
        bars = [
            {
                "close": c,
                "high": c + 5,
                "low": c - 5,
                "open": c + 2,
                "volume": v,
            }
            for c, v in zip(closes, volumes)
        ]
        if timestamps:
            for i, bar in enumerate(bars):
                bar["timestamp"] = f"2026-01-{i + 1:02d}T09:15:00+00:00"
        return bars

    def test_latest_bar_is_evaluated(self):
        """Flat price and flat volume until a move exactly at the latest bar.

        The old window (`values[-period-1:-1]`) ended one bar early and would
        report a neutral 0%/0% result here; the corrected window must report
        the latest bar's move: price down, volume up -> bullish.
        """
        closes = [110.0] * 20 + [100.0]
        volumes = [1000.0] * 20 + [2000.0]
        bars = self._make_bars(closes, volumes)
        result = calculate_divergence(bars, period=5)
        assert result["available"] is True
        expected_price = round((100.0 - 110.0) / 110.0 * 100, 2)
        assert result["price_change_percent"] == pytest.approx(expected_price, abs=1e-9)
        assert result["volume_change_percent"] == pytest.approx(100.0)
        assert result["signal"] == "bullish"
        # The evaluated bar is the latest returned bar, not the previous one.
        assert result["evaluated_timestamp"] == "2026-01-21T09:15:00+00:00"

    def test_price_and_volume_share_the_same_window_bars(self):
        """Zero-volume bars are skipped for both series together.

        With zero volume on every other bar, the eligible endpoints for price
        and volume are the same two bars. A misaligned implementation would
        report a volume change measured over a different horizon.
        """
        closes = [100.0, 100.0, 102.0, 100.0, 104.0, 100.0, 106.0, 100.0, 108.0, 100.0, 110.0]
        volumes = [1000.0, 0.0, 1000.0, 0.0, 1000.0, 0.0, 1000.0, 0.0, 1000.0, 0.0, 1000.0]
        bars = self._make_bars(closes, volumes)
        result = calculate_divergence(bars, period=5)
        assert result["available"] is True
        # Same eligible endpoints for both series: closes 100 -> 110 (+10%),
        # volumes 1000 -> 1000 (0%) across the same eligible bars.
        assert result["price_change_percent"] == pytest.approx(10.0)
        assert result["volume_change_percent"] == pytest.approx(0.0)
        assert result["evaluated_timestamp"] == "2026-01-11T09:15:00+00:00"

    def test_trailing_zero_volume_bar_is_not_scored_as_the_latest(self):
        """A zero-volume latest bar is ineligible; both series move back with it.

        The latest eligible bar (22nd, volume 0) must be excluded for price AND
        volume alike: the endpoints become bars 15..21, so volume contributed
        by the last bar's rise is measured to bar 21, never to a bar-index mix.
        """
        closes = [100.0] * 20 + [105.0, 110.0]
        volumes = [1000.0] * 20 + [1500.0, 0.0]
        bars = self._make_bars(closes, volumes)
        result = calculate_divergence(bars, period=5)
        assert result["available"] is True
        assert result["price_change_percent"] == pytest.approx(5.0)
        assert result["volume_change_percent"] == pytest.approx(50.0)
        assert result["signal"] == "aligned"
        assert result["evaluated_timestamp"] == "2026-01-21T09:15:00+00:00"

    def test_insufficient_eligible_observations_with_mixed_data(self):
        """Eligibility counts usable close+positive-volume pairs, not just bars."""
        closes = [100.0] * 12
        volumes = [1000.0] * 8 + [0.0] * 4  # last four bars have zero volume
        bars = self._make_bars(closes, volumes)
        result = calculate_divergence(bars, period=10)  # needs 11 eligible points
        assert result["available"] is False
        assert result["signal"] is None
        assert "8 usable price+volume observations" in result["explanation"]


class TestVolumeSpike:
    """Volume Spike analysis using real OHLCV bars."""

    def _make_bars(self, closes, volumes=None):
        if volumes is None:
            volumes = [1000] * len(closes)
        return [
            {
                "close": c,
                "high": c + 5,
                "low": c - 5,
                "open": c + 2,
                "volume": v,
            }
            for c, v in zip(closes, volumes)
        ]

    def test_volume_spike(self):
        """Latest volume is much higher than the baseline average."""
        bars = self._make_bars(
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119],
            volumes=[1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000, 1500, 1600, 1700, 1700, 1800, 1900, 1900, 2000, 3000, 6000]
        )
        result = calculate_volume_spike(bars, period=5)
        assert result["available"] is True
        assert result["latest_volume"] == 6000
        # Baseline is exactly the 5 bars before the latest: (1800+1900+1900+2000+3000)/5
        assert result["baseline_average"] == pytest.approx(2120.0)
        assert result["volume_ratio"] == pytest.approx(round(6000 / 2120, 4), abs=1e-9)
        assert result["percent_above_baseline"] > 0
        assert result["signal"] == "spike"

    def test_normal_volume(self):
        """Latest volume is close to the baseline average."""
        bars = self._make_bars(
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119],
            volumes=[1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1700, 1800, 1800, 1900, 1900, 2000, 2000, 2100, 2100, 2200, 2200, 2300, 2400]
        )
        result = calculate_volume_spike(bars, period=5)
        assert result["available"] is True
        assert result["signal"] in ("normal", "elevated")

    def test_zero_baseline_volume(self):
        """All baseline volumes are zero; result marked unavailable."""
        bars = self._make_bars(
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119],
            volumes=[0] * 20
        )
        result = calculate_volume_spike(bars, period=5)
        assert result["available"] is False
        assert result["baseline_average"] is None

    def test_insufficient_history(self):
        """Not enough bars for the lookback period."""
        bars = self._make_bars([100, 101, 102])
        result = calculate_volume_spike(bars, period=5)
        assert result["available"] is False

    def test_missing_volumes(self):
        """Bars with None volume should be handled."""
        bars = [{"close": 100, "high": 105, "low": 95, "open": 103, "volume": None} for _ in range(30)]
        result = calculate_volume_spike(bars, period=5)
        assert result["available"] is False

    def test_invalid_period(self):
        """Unsupported volume spike period."""
        bars = self._make_bars([100.0] * 30)
        result = calculate_volume_spike(bars, period=7)
        assert result["available"] is False
        assert result["explanation"] is not None


class TestVolumeSpikeBaselineRegression:
    """Regression tests pinning the `period`-bar baseline window.

    The baseline must be the average of exactly the *period* bars before the
    latest bar, never the latest bar itself and never the whole returned
    history (the old bug made the baseline grow with the fetched window).
    """

    def _make_bars(self, volumes):
        return [
            {
                "close": 100.0 + i,
                "high": 105.0 + i,
                "low": 95.0 + i,
                "open": 102.0 + i,
                "volume": v,
            }
            for i, v in enumerate(volumes)
        ]

    def test_different_periods_use_different_baseline_windows(self):
        """period=5 and period=10 must average different trailing volumes."""
        volumes = [float(100 * (i + 1)) for i in range(30)]  # 100, 200, ..., 3000
        bars = self._make_bars(volumes)

        result_p5 = calculate_volume_spike(bars, period=5)
        result_p10 = calculate_volume_spike(bars, period=10)

        # period=5 baseline: volumes[-6:-1] = [2500, 2600, 2700, 2800, 2900]
        assert result_p5["baseline_average"] == pytest.approx(2700.0)
        # period=10 baseline: volumes[-11:-1] = [2000, 2100, ..., 2900]
        assert result_p10["baseline_average"] == pytest.approx(2450.0)
        assert result_p5["baseline_average"] != result_p10["baseline_average"]
        # Same latest bar in both cases.
        assert result_p5["latest_volume"] == result_p10["latest_volume"] == 3000.0

    def test_baseline_grows_nothing_beyond_period_bars(self):
        """Adding older history must not change the baseline for the same period."""
        # Eleven shared trailing bars: ten of them (all but the latest) are the
        # period=10 baseline in both bars lists, so older history is excluded.
        trailing = [float(10 * (i + 1)) for i in range(11)]  # 10, 20, ..., 110
        bars_short = self._make_bars([500.0] * 6 + trailing)
        bars_long = self._make_bars([9000.0] * 30 + trailing)

        result_short = calculate_volume_spike(bars_short, period=10)
        result_long = calculate_volume_spike(bars_long, period=10)

        # Baseline = mean(10, 20, ..., 100); older bars never contribute.
        assert result_short["baseline_average"] == pytest.approx(55.0)
        assert result_long["baseline_average"] == pytest.approx(55.0)

    def test_latest_bar_excluded_from_baseline(self):
        """A huge latest volume never enters its own baseline."""
        volumes = [100.0] * 25 + [99999.0]
        bars = self._make_bars(volumes)
        result = calculate_volume_spike(bars, period=10)
        assert result["available"] is True
        assert result["latest_volume"] == 99999.0
        assert result["baseline_average"] == pytest.approx(100.0)

    def test_zero_volumes_inside_the_window_are_skipped(self):
        """Zero volumes inside the baseline window are filtered, not counted as 0."""
        volumes = [100.0] * 10 + [0.0] * 5 + [100.0] * 10  # 25 bars
        bars = self._make_bars(volumes)
        result = calculate_volume_spike(bars, period=10)
        assert result["available"] is True
        # Baseline window = 10 bars before latest = one zero + nine 100s.
        assert result["baseline_average"] == pytest.approx(100.0)


class TestVolatilityAnomaly:
    """Volatility anomaly analysis using real OHLCV bars."""

    def _make_bars(self, closes, volumes=None):
        if volumes is None:
            volumes = [1000] * len(closes)
        return [
            {
                "close": c,
                "high": c + 5,
                "low": c - 5,
                "open": c + 2,
                "volume": v,
            }
            for c, v in zip(closes, volumes)
        ]

    def test_elevated_volatility(self):
        """Recent ATR is elevated above the baseline (ratio >= 1.5 but < 2.0)."""
        # 30 bars: recent 5 bars have high-low=19, baseline has high-low=10 → ratio=1.9
        closes = [100 + i * 0.5 for i in range(30)]
        bars = self._make_bars(closes, volumes=[1000] * 30)
        # Recent 5 bars: high-low = 19 (elevated but < 2x baseline)
        for i in range(25, 30):  # last 5 bars
            bars[i]["high"] = bars[i]["close"] + 14  # high-low = 19
            bars[i]["low"] = bars[i]["close"] - 5
        # Earlier bars have normal high-low = 10
        for i in range(25):
            bars[i]["high"] = bars[i]["close"] + 5
            bars[i]["low"] = bars[i]["close"] - 5
        result = calculate_volatility_anomaly(bars, period=5)
        assert result["available"] is True
        assert result["volatility_ratio"] >= 1.5
        assert result["volatility_ratio"] < 2.0
        assert result["percent_change"] > 0
        assert result["signal"] == "elevated"

    def test_normal_volatility(self):
        """Recent ATR is close to the baseline (ratio < 1.0)."""
        # Recent 5 bars have high-low=9, baseline has high-low=10 → ratio=0.9
        closes = [100 + i * 0.5 for i in range(30)]
        bars = self._make_bars(closes, volumes=[1000] * 30)
        # Recent 5 bars: high-low = 9 (slightly below baseline)
        for i in range(25, 30):  # last 5 bars
            bars[i]["high"] = bars[i]["close"] + 4  # high-low = 9
            bars[i]["low"] = bars[i]["close"] - 5
        # Earlier bars have normal high-low = 10
        for i in range(25):
            bars[i]["high"] = bars[i]["close"] + 5
            bars[i]["low"] = bars[i]["close"] - 5
        result = calculate_volatility_anomaly(bars, period=5)
        assert result["available"] is True
        assert result["volatility_ratio"] < 1.0
        assert result["signal"] == "normal"

    def test_insufficient_history(self):
        """Not enough bars for the lookback period."""
        bars = self._make_bars([100, 101, 102])
        result = calculate_volatility_anomaly(bars, period=5)
        assert result["available"] is False

    def test_missing_values(self):
        """Bars with missing OHLCV values should be handled."""
        bars = [
            {"close": 100, "high": 105, "low": None, "open": 103, "volume": 1000}
            for _ in range(30)
        ]
        result = calculate_volatility_anomaly(bars, period=5)
        # With many missing values, likely insufficient TR data
        assert result["available"] is False

    def test_zero_baseline(self):
        """Zero baseline ATR; result marked unavailable."""
        # All bars have high=low, so TR=0, baseline will be 0
        bars = self._make_bars(
            [100] * 30,
            volumes=[1000] * 30,
        )
        for bar in bars:
            bar["high"] = bar["close"]
            bar["low"] = bar["close"]
        result = calculate_volatility_anomaly(bars, period=5)
        assert result["available"] is False
        assert result["baseline_atr"] is None

    def test_invalid_period(self):
        """Unsupported volatility anomaly period."""
        bars = self._make_bars([100.0] * 30)
        result = calculate_volatility_anomaly(bars, period=7)
        assert result["available"] is False
        assert result["explanation"] is not None


class TestLOFAnomaly:
    """Local Outlier Factor scoring of the latest bar against historical bars."""

    @staticmethod
    def _make_bars(count: int = 81, seed: int = 7) -> list[dict[str, Any]]:
        """Deterministic pseudo-random OHLCV series (no network needed)."""
        rng = random.Random(seed)
        bars = []
        price = 100.0
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)

        for index in range(count):
            change = rng.uniform(-0.012, 0.012)
            price = price * (1 + change)
            high = price * (1 + abs(change) * 0.4 + 0.003)
            low = price * (1 - abs(change) * 0.4 - 0.003)
            volume = int(1000 * math.exp(rng.uniform(-0.25, 0.25)))
            stamp = (start + timedelta(days=index)).isoformat().replace("+00:00", "Z")
            bars.append(
                {
                    "timestamp": stamp,
                    "open": price * (1 - change / 2),
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": volume,
                }
            )

        return bars

    def test_normal_dataset_not_flagged(self):
        """A typical latest bar in a well-populated history is not an outlier."""
        bars = self._make_bars()
        result = calculate_lof_anomaly(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is False
        assert result["lof_score"] <= result["threshold"]
        assert result["decision_value"] >= 0
        assert result["history_observations"] == len(bars) - 2
        assert result["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert result["features_used"] == [
            "price_return_pct",
            "volume_change_pct",
            "true_range_pct",
        ]
        assert set(result["latest_features"].keys()) == set(result["features_used"])
        assert "no future data" in result["explanation"]

    def test_injected_anomaly_flagged(self):
        """A large price jump, volume surge and wide range is detected."""
        bars = self._make_bars()
        latest = bars[-1]
        latest["close"] = latest["close"] * 1.15
        latest["high"] = latest["close"] * 1.01
        latest["low"] = latest["close"] * 0.99
        latest["volume"] = latest["volume"] * 20

        result = calculate_lof_anomaly(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is True
        assert result["lof_score"] > result["threshold"]
        assert result["decision_value"] < 0
        assert "not proof of market manipulation" in result["explanation"]

    def test_insufficient_history(self):
        """Too few bars returns an honest unavailable result."""
        result = calculate_lof_anomaly(self._make_bars(10))

        assert result["available"] is False
        assert result["is_anomaly"] is None
        assert result["lof_score"] is None
        assert "Insufficient history" in result["explanation"]
        assert result["history_observations"] < 20

    def test_n_neighbors_larger_than_history(self):
        """A valid but too-large neighbour count is reported honestly."""
        result = calculate_lof_anomaly(self._make_bars(30), n_neighbors=50)

        assert result["available"] is False
        assert "n_neighbors=50" in result["explanation"]

    def test_missing_and_invalid_values(self):
        """Missing lows and a missing latest volume are handled honestly."""
        bars = self._make_bars()
        for bar in bars:
            bar["low"] = None
        result = calculate_lof_anomaly(bars)
        assert result["available"] is False
        assert "No usable consecutive OHLCV observations" in result["explanation"]

        bars = self._make_bars()
        bars[-1]["volume"] = None
        result = calculate_lof_anomaly(bars)
        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert result["evaluated_timestamp"] == bars[-2]["timestamp"]
        assert "trailing returned bar" in result["explanation"]

        bars = self._make_bars()
        bars[-1]["close"] = float("nan")
        result = calculate_lof_anomaly(bars)
        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert isinstance(result["is_anomaly"], bool)

    def test_constant_features(self):
        """A flat series has no usable variance for LOF."""
        bars = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(30):
            stamp = (start + timedelta(days=index)).isoformat().replace("+00:00", "Z")
            bars.append(
                {
                    "timestamp": stamp,
                    "open": 100.0,
                    "high": 100.5,
                    "low": 99.5,
                    "close": 100.0,
                    "volume": 1000,
                }
            )

        result = calculate_lof_anomaly(bars)

        assert result["available"] is False
        assert result["is_anomaly"] is None
        assert "constant" in result["explanation"]

    def test_invalid_parameters(self):
        """Invalid neighbour counts and contamination values are rejected."""
        bars = self._make_bars()

        for bad_neighbors in (0, 1, -5, "20", None, True, 201):
            result = calculate_lof_anomaly(bars, n_neighbors=bad_neighbors)
            assert result["available"] is False, bad_neighbors
            assert result["explanation"]

        for bad_contamination in (0, -0.1, 0.51, 1.5, "0.05", None, True):
            result = calculate_lof_anomaly(bars, contamination=bad_contamination)
            assert result["available"] is False, bad_contamination
            assert result["explanation"]

    def test_latest_bar_is_scored_against_history_only(self):
        """The latest bar is excluded from fitting; history count proves it."""
        bars = self._make_bars(41)
        result = calculate_lof_anomaly(bars)

        assert result["available"] is True
        assert result["history_observations"] == 39  # 41 bars -> 40 rows, 39 historical
        assert result["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert "scored only the latest bar" in result["explanation"]

    def test_trailing_incomplete_bar_is_excluded_not_blocking(self):
        """Yahoo's partial latest bar must not disable scoring of a real bar."""
        complete = calculate_lof_anomaly(self._make_bars(41))
        assert complete["available"] is True
        assert complete["excluded_trailing_bars"] == 0

        bars = self._make_bars(41)
        bars[-1]["close"] = None
        result = calculate_lof_anomaly(bars)

        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert result["evaluated_timestamp"] == bars[-2]["timestamp"]
        assert result["history_observations"] == len(bars) - 3
        assert "trailing returned bar" in result["explanation"]
        assert "not proof of market manipulation" in result["explanation"]


class TestBollingerBands:
    """Bollinger Bands indicator and squeeze/breakout detector tests."""

    @staticmethod
    def _make_bars(count: int = 41, seed: int = 11) -> list[dict[str, Any]]:
        rng = random.Random(seed)
        bars = []
        price = 100.0
        start = datetime(2026, 2, 1, tzinfo=timezone.utc)

        for index in range(count):
            change = rng.uniform(-0.012, 0.012)
            price = price * (1 + change)
            high = price * (1 + abs(change) * 0.4 + 0.003)
            low = price * (1 - abs(change) * 0.4 - 0.003)
            volume = int(1000 * math.exp(rng.uniform(-0.25, 0.25)))
            stamp = (start + timedelta(days=index)).isoformat().replace("+00:00", "Z")
            bars.append(
                {
                    "timestamp": stamp,
                    "open": price * (1 - change / 2),
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": volume,
                }
            )

        return bars

    def test_normal_dataset_computes_bands_and_percent_b(self):
        """A normal series returns upper, middle, lower bands and valid %B."""
        bars = self._make_bars(30)
        result = calculate_bollinger_bands(bars)

        assert result["available"] is True
        assert result["period"] == 20
        assert result["multiplier"] == 2.0
        assert result["upper"] > result["middle"] > result["lower"]
        assert result["bandwidth"] > 0
        assert isinstance(result["percent_b"], float)
        assert result["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert result["history_observations"] == 30
        assert "Bollinger Bands" in result["explanation"]

    def test_breakout_upper(self):
        """A sharp upward jump flags a breakout above the upper band."""
        bars = self._make_bars(30)
        bars[-1]["close"] = 150.0
        bars[-1]["high"] = 155.0
        result = calculate_bollinger_bands(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is True
        assert result["signal"] in ("above_upper", "breakout_upper")
        assert result["percent_b"] > 1.0

    def test_breakout_lower(self):
        """A sharp downward dump flags a breakout below the lower band."""
        bars = self._make_bars(30)
        bars[-1]["close"] = 50.0
        bars[-1]["low"] = 45.0
        result = calculate_bollinger_bands(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is True
        assert result["signal"] in ("below_lower", "breakout_lower")
        assert result["percent_b"] < 0.0

    def test_squeeze_detected(self):
        """When bandwidth is in its lowest percentile, a squeeze is flagged."""
        bars = self._make_bars(30)
        last_stamp = datetime.fromisoformat(bars[-1]["timestamp"].replace("Z", "+00:00"))
        tight_price = bars[-1]["close"]
        for i in range(1, 15):
            stamp = (last_stamp + timedelta(days=i)).isoformat().replace("+00:00", "Z")
            bars.append({
                "timestamp": stamp,
                "open": tight_price,
                "high": tight_price + 0.01,
                "low": tight_price - 0.01,
                "close": tight_price,
                "volume": 1000,
            })

        result = calculate_bollinger_bands(bars)
        assert result["available"] is True
        assert result["squeeze"] is True
        assert "squeeze" in result["signal"].lower()
        assert result["squeeze_lookback"] == min(len(bars) - 19, 120)

    def test_insufficient_history(self):
        """Fewer than 20 bars returns honest unavailable."""
        result = calculate_bollinger_bands(self._make_bars(15))
        assert result["available"] is False
        assert result["upper"] is None
        assert result["middle"] is None
        assert result["lower"] is None
        assert result["percent_b"] is None
        assert "At least 20 bars" in result["explanation"]

    def test_trailing_incomplete_bar_excluded(self):
        """Yahoo's partial latest bar is excluded, scoring the latest complete bar."""
        bars = self._make_bars(25)
        bars[-1]["close"] = None
        result = calculate_bollinger_bands(bars)
        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert result["evaluated_timestamp"] == bars[-2]["timestamp"]


class TestTransformerAnomaly:
    """Transformer next-step prediction scoring of the latest bar."""

    @staticmethod
    def _make_bars(count: int = 60, seed: int = 13) -> list[dict[str, Any]]:
        """Deterministic pseudo-random OHLCV series (no network needed)."""
        rng = random.Random(seed)
        bars = []
        price = 100.0
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)

        for index in range(count):
            change = rng.uniform(-0.012, 0.012)
            price = price * (1 + change)
            high = price * (1 + abs(change) * 0.4 + 0.003)
            low = price * (1 - abs(change) * 0.4 - 0.003)
            volume = int(1000 * math.exp(rng.uniform(-0.25, 0.25)))
            stamp = (start + timedelta(days=index)).isoformat().replace("+00:00", "Z")
            bars.append(
                {
                    "timestamp": stamp,
                    "open": price * (1 - change / 2),
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": volume,
                }
            )

        return bars

    def test_normal_dataset_not_flagged(self):
        """A typical latest bar in a well-populated history is not an outlier."""
        bars = self._make_bars()
        result = calculate_transformer_anomaly(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is False
        assert result["anomaly_score"] <= result["threshold"]
        assert result["history_observations"] == len(bars) - 2
        assert result["history_limit"] == TR_MAX_HISTORY_BARS
        assert result["excluded_older_bars"] == 0
        assert result["training_epochs"] == 500
        assert result["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert result["features_used"] == [
            "price_return_pct",
            "volume_change_pct",
            "true_range_pct",
        ]
        assert set(result["latest_features"].keys()) == set(result["features_used"])
        assert result["latest_prediction"] is not None
        assert "no future data" in result["explanation"]

    def test_injected_anomaly_flagged(self):
        """A large price jump, volume surge and wide range is detected."""
        bars = self._make_bars()
        latest = bars[-1]
        latest["close"] = latest["close"] * 1.25
        latest["high"] = latest["close"] * 1.02
        latest["low"] = latest["close"] * 0.98
        latest["volume"] = latest["volume"] * 30

        result = calculate_transformer_anomaly(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is True
        assert result["anomaly_score"] > result["threshold"]
        assert "not proof of market manipulation" in result["explanation"]

    def test_insufficient_history(self):
        """Too few bars returns an honest unavailable result."""
        result = calculate_transformer_anomaly(self._make_bars(10))

        assert result["available"] is False
        assert result["is_anomaly"] is None
        assert result["anomaly_score"] is None
        assert result["threshold"] is None
        assert "Insufficient history" in result["explanation"]
        assert result["history_observations"] < 20

    def test_missing_and_invalid_values(self):
        """Missing lows and a missing latest volume are handled honestly."""
        bars = self._make_bars()
        for bar in bars:
            bar["low"] = None
        result = calculate_transformer_anomaly(bars)
        assert result["available"] is False
        assert "No usable consecutive OHLCV observations" in result["explanation"]

        bars = self._make_bars()
        bars[-1]["volume"] = None
        result = calculate_transformer_anomaly(bars)
        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert result["evaluated_timestamp"] == bars[-2]["timestamp"]
        assert "trailing returned bar" in result["explanation"]

        bars = self._make_bars()
        bars[-1]["close"] = float("nan")
        result = calculate_transformer_anomaly(bars)
        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert isinstance(result["is_anomaly"], bool)

    def test_invalid_parameters(self):
        """Invalid seq_len, epochs, and lr are rejected."""
        bars = self._make_bars()

        for bad_seq_len in (0, 1, -3, "8", None, True):
            result = calculate_transformer_anomaly(bars, seq_len=bad_seq_len)
            assert result["available"] is False, bad_seq_len
            assert result["explanation"]

        for bad_epochs in (0, -5, "100", None, True):
            result = calculate_transformer_anomaly(bars, epochs=bad_epochs)
            assert result["available"] is False, bad_epochs
            assert result["explanation"]

        for bad_lr in (0, -0.1, "0.001", None, True, float("nan")):
            result = calculate_transformer_anomaly(bars, lr=bad_lr)
            assert result["available"] is False, bad_lr
            assert result["explanation"]

    def test_deterministic_output(self):
        """The same input produces the same output every time."""
        bars = self._make_bars()
        first = calculate_transformer_anomaly(bars)
        second = calculate_transformer_anomaly(bars)

        assert first["available"] is True
        assert first["anomaly_score"] == second["anomaly_score"]
        assert first["threshold"] == second["threshold"]
        assert first["is_anomaly"] == second["is_anomaly"]
        assert first["latest_features"] == second["latest_features"]
        assert first["latest_prediction"] == second["latest_prediction"]
        assert first["explanation"] == second["explanation"]

    def test_latest_bar_is_excluded_from_training(self):
        """The latest bar is excluded from fitting; history count proves it."""
        bars = self._make_bars(41)
        result = calculate_transformer_anomaly(bars)

        assert result["available"] is True
        assert result["history_observations"] == 39  # 41 bars -> 40 rows, 39 historical
        assert result["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert "trained only on" in result["explanation"]
        assert "scored separately" in result["explanation"]

    def test_long_history_is_bounded_and_reported(self):
        """Extended windows train on only the recent bars, honestly reported."""
        bars = self._make_bars(TR_MAX_HISTORY_BARS + 20)
        result = calculate_transformer_anomaly(bars, epochs=2)

        assert result["available"] is True
        assert result["history_limit"] == TR_MAX_HISTORY_BARS
        assert result["excluded_older_bars"] == 20
        assert result["history_observations"] == TR_MAX_HISTORY_BARS - 2
        assert result["training_epochs"] == 2
        assert result["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert "20 older bars were excluded" in result["explanation"]

    def test_extended_history_reduces_epochs(self):
        """A longer history uses the bounded epoch budget and says so."""
        bars = self._make_bars(100)
        result = calculate_transformer_anomaly(bars)

        assert result["available"] is True
        assert result["training_epochs"] == TR_EXTENDED_EPOCHS
        assert result["excluded_older_bars"] == 0
        assert f"Training used {TR_EXTENDED_EPOCHS} epochs" in result["explanation"]

    def test_trailing_incomplete_bar_is_excluded_not_blocking(self):
        """Yahoo's partial latest bar must not disable prediction scoring."""
        complete = calculate_transformer_anomaly(self._make_bars(41))
        assert complete["available"] is True
        assert complete["excluded_trailing_bars"] == 0

        bars = self._make_bars(41)
        bars[-1]["close"] = None
        result = calculate_transformer_anomaly(bars)

        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert result["evaluated_timestamp"] == bars[-2]["timestamp"]
        assert result["history_observations"] == len(bars) - 3
        assert "trailing returned bar" in result["explanation"]
class TestHistoricalAnomalies:
    """Timestamped, causal anomaly points and honest detector limits."""

    @staticmethod
    def _bars(count: int = 45) -> list[dict[str, Any]]:
        origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars: list[dict[str, Any]] = []
        for index in range(count):
            if index <= 20:
                close = 120.0 - index
                volume = 1000 + index * 50
            else:
                close = 100.0
                volume = 5000 if index == 30 else 1000
            span = 0.3 if index < 25 else 4.0
            bars.append(
                {
                    "timestamp": (origin + timedelta(days=index))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "open": close,
                    "high": close + span / 2,
                    "low": close - span / 2,
                    "close": close,
                    "volume": volume,
                }
            )
        return bars

    def test_every_point_uses_a_returned_timestamp_and_detector_fields(self):
        bars = self._bars()
        result = calculate_historical_anomalies(bars)
        timestamps = {bar["timestamp"] for bar in bars}

        assert result["available"] is True
        assert result["bar_count"] == len(bars)
        assert result["scope"]
        assert result["limitations"]

        for key in (
            "volume_spike",
            "volatility_anomaly",
            "volume_price_divergence",
            "lof_anomaly",
        ):
            block = result["detectors"][key]
            assert block["available"] is True, key
            assert block["scope"] == "historical", key
            assert block["historical_available"] is True, key
            assert block["method"]
            for point in block["points"]:
                assert point["is_anomaly"] is True
                assert point["timestamp"] in timestamps
                assert point["explanation"]

        spikes = result["detectors"]["volume_spike"]["points"]
        assert any(
            point["timestamp"] == bars[30]["timestamp"] for point in spikes
        )
        for point in spikes:
            assert point["score"] > point["threshold"] == 2.0
            assert point["score_label"] == "Volume ratio"

        divergences = result["detectors"]["volume_price_divergence"]["points"]
        assert divergences
        assert all(point["signal"] in ("bullish", "bearish") for point in divergences)
        assert all(
            point["score"] is None and point["threshold"] is None
            for point in divergences
        )
        assert result["detectors"]["volume_price_divergence"]["detected_bars"] >= len(
            divergences
        )

        for point in result["detectors"]["lof_anomaly"]["points"]:
            assert point["score"] is not None
            assert point["threshold"] is not None
            assert point["score_label"] == "LOF score"

        block = result["detectors"]["transformer_anomaly"]
        assert block["available"] is False
        assert block["historical_available"] is False
        assert block["scope"] == "latest_only"
        assert block["points"] == []
        assert block["limitations"]
        assert "latest-complete-bar" in block["reason"]
        assert result["latest_only_detectors"] == ["transformer_anomaly"]

    def test_historical_results_are_causal_when_future_bars_are_removed(self):
        bars = self._bars()
        full = calculate_historical_anomalies(bars)
        prefix = bars[:31]
        prefix_result = calculate_historical_anomalies(prefix)
        last_prefix_timestamp = prefix[-1]["timestamp"]

        for key in (
            "volume_spike",
            "volatility_anomaly",
            "volume_price_divergence",
            "lof_anomaly",
        ):
            full_early = [
                point
                for point in full["detectors"][key]["points"]
                if point["timestamp"] <= last_prefix_timestamp
            ]
            assert full_early == prefix_result["detectors"][key]["points"], key

    def test_insufficient_history_is_unavailable_not_invented(self):
        result = calculate_historical_anomalies(self._bars(6))

        assert result["available"] is False
        assert result["bar_count"] == 6
        for key in (
            "volume_spike",
            "volatility_anomaly",
            "volume_price_divergence",
            "lof_anomaly",
        ):
            block = result["detectors"][key]
            assert block["available"] is False
            assert block["points"] == []
            assert block["reason"]
        assert result["detectors"]["transformer_anomaly"]["scope"] == "latest_only"
        assert result["detectors"]["transformer_anomaly"]["points"] == []

    def test_missing_timestamps_and_values_do_not_create_points(self):
        bars = self._bars()
        bars[30]["timestamp"] = None
        bars[31]["volume"] = None
        result = calculate_historical_anomalies(bars)

        for key, block in result["detectors"].items():
            if key == "transformer_anomaly":
                continue
            for point in block["points"]:
                assert point["timestamp"]
                assert point["timestamp"] != "None"
        assert not any(
            point["timestamp"] is None
            for block in result["detectors"].values()
            for point in block["points"]
        )

    def test_non_list_input_does_not_crash(self):
        result = calculate_historical_anomalies(None)
        assert result["available"] is False
        assert result["bar_count"] == 0
        assert all(
            block["points"] == [] for block in result["detectors"].values()
        )


class TestHistoricalWalkForwardEquivalence:
    """The incremental walk-forward detectors must match the per-prefix
    reference implementation exactly.

    The historical detectors were refactored from re-running the public
    per-prefix calculation on ``bars[:index + 1]`` for every gated bar to
    incremental running totals. These tests pin that equivalence: for every
    input the points, explanations, scores, rounding, evaluated_bars and
    detected_bars must be identical to the original loop.
    """

    @staticmethod
    def _gated(bar: dict[str, Any]) -> bool:
        from analysis import _bar_has_complete_ohlcv, _historical_timestamp

        return _bar_has_complete_ohlcv(bar) and _historical_timestamp(bar) is not None

    def _reference_volume_spikes(self, bars):
        from analysis import _historical_point

        points, evaluated = [], 0
        for index, bar in enumerate(bars):
            if not self._gated(bar):
                continue
            result = calculate_volume_spike(bars[: index + 1])
            if result.get("available") is not True:
                continue
            evaluated += 1
            if result.get("signal") != "spike":
                continue
            ratio = result.get("volume_ratio")
            points.append(
                _historical_point(
                    bar,
                    score=ratio,
                    threshold=2.0,
                    score_label="Volume ratio",
                    values={
                        "latest_volume": result.get("latest_volume"),
                        "baseline_average": result.get("baseline_average"),
                        "percent_above_baseline": result.get("percent_above_baseline"),
                    },
                    signal="spike",
                    explanation=(
                        f"Volume on {bar.get('timestamp')} was {ratio}x the average "
                        "of the 20 bars before it in the returned series; "
                        "the existing spike threshold is > 2.0x. This is unusual "
                        "volume, not proof of manipulation."
                    ),
                )
            )
        return points, evaluated

    def _reference_volatility(self, bars):
        from analysis import _historical_point

        points, evaluated = [], 0
        for index, bar in enumerate(bars):
            if not self._gated(bar):
                continue
            result = calculate_volatility_anomaly(bars[: index + 1])
            if result.get("available") is not True:
                continue
            evaluated += 1
            if result.get("signal") != "anomalous":
                continue
            ratio = result.get("volatility_ratio")
            points.append(
                _historical_point(
                    bar,
                    score=ratio,
                    threshold=2.0,
                    score_label="Volatility ratio",
                    values={
                        "recent_atr": result.get("recent_atr"),
                        "baseline_atr": result.get("baseline_atr"),
                        "percent_change": result.get("percent_change"),
                    },
                    signal="anomalous",
                    explanation=(
                        f"Recent ATR on {bar.get('timestamp')} was {ratio}x the "
                        "preceding baseline ATR; the existing anomalous threshold "
                        "is > 2.0x. This describes unusually large price ranges, "
                        "not manipulation or a future move."
                    ),
                )
            )
        return points, evaluated

    def _reference_divergences(self, bars):
        from analysis import _historical_point

        points, evaluated, detected = [], 0, 0
        previous_signal = None
        for index, bar in enumerate(bars):
            if not self._gated(bar):
                continue
            result = calculate_divergence(bars[: index + 1])
            if result.get("available") is not True:
                continue
            evaluated += 1
            signal = result.get("signal")
            if signal not in ("bullish", "bearish"):
                previous_signal = signal
                continue
            detected += 1
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
                        "price_change_percent": result.get("price_change_percent"),
                        "volume_change_percent": result.get("volume_change_percent"),
                    },
                    signal=signal,
                    explanation=(
                        f"{result.get('explanation', '')} This is the first bar "
                        "of a sustained directional volume-price run. It is a "
                        "signal, not a numeric anomaly score; no threshold is "
                        "claimed."
                    ).strip(),
                )
            )
        return points, evaluated, detected

    @staticmethod
    def _bars_random(count: int, seed: int) -> list[dict[str, Any]]:
        rng = random.Random(seed)
        bars: list[dict[str, Any]] = []
        price = 100.0
        for i in range(count):
            close = round(price + rng.uniform(-2.5, 2.5), 4)
            high = round(close + rng.uniform(0.0, 1.4), 4)
            low = round(close - rng.uniform(0.0, 1.4), 4)
            volume = rng.choice([0, None, rng.randint(1, 900000), rng.randint(1, 900000)])
            bar: dict[str, Any] = {
                "timestamp": f"2024-04-{(i % 28) + 1:02d}T09:15:00Z",
                "open": round(price, 4),
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
            roll = rng.random()
            if roll < 0.05:
                bar["high"] = None
            elif roll < 0.08:
                bar["timestamp"] = ""
            bars.append(bar)
            price = close
        return bars

    @staticmethod
    def _bars_edge() -> list[dict[str, Any]]:
        """Deterministic zeros, negatives, missing values, zero close."""
        bars: list[dict[str, Any]] = []
        for i in range(50):
            bars.append(
                {
                    "timestamp": f"2024-02-{(i % 28) + 1:02d}T09:15:00Z",
                    "open": 100.0 + i,
                    "high": 102.0 + i,
                    "low": 99.0 + i,
                    "close": 101.0 + i,
                    "volume": 1000 + i * 10,
                }
            )
        bars[3]["volume"] = 0            # zero volume: excluded from all baselines
        bars[5]["volume"] = None         # missing volume: excluded from count
        bars[7]["volume"] = -50          # negative volume: latest-only, no baseline
        bars[9]["close"] = None          # incomplete OHLC, volume still contributes later
        bars[11]["timestamp"] = None     # unusable timestamp
        bars[12]["high"] = None          # incomplete OHLC
        bars[15]["volume"] = 500000      # spike attempt
        bars[16]["close"] = 0.0          # zero close: _pct_change fallback path
        bars[18]["volume"] = 0
        bars[19]["volume"] = None
        bars[20]["volume"] = 900000
        return bars

    def test_volume_spikes_match_per_prefix_reference(self):
        from analysis import _historical_volume_spikes

        for bars, label in [
            (self._bars_edge(), "edge"),
            (self._bars_random(40, 1), "r40"),
            (self._bars_random(120, 3), "r120"),
            (self._bars_random(250, 4), "r250"),
        ]:
            ref_points, ref_evaluated = self._reference_volume_spikes(bars)
            result = _historical_volume_spikes(bars)
            assert (
                json.dumps(ref_points, sort_keys=True, default=str)
                == json.dumps(result["points"], sort_keys=True, default=str)
            ), f"volume spike points differ for {label}"
            assert result["evaluated_bars"] == ref_evaluated, label

    def test_volatility_matches_per_prefix_reference(self):
        from analysis import _historical_volatility_anomalies

        for bars, label in [
            (self._bars_edge(), "edge"),
            (self._bars_random(40, 1), "r40"),
            (self._bars_random(120, 3), "r120"),
            (self._bars_random(250, 4), "r250"),
        ]:
            ref_points, ref_evaluated = self._reference_volatility(bars)
            result = _historical_volatility_anomalies(bars)
            assert (
                json.dumps(ref_points, sort_keys=True, default=str)
                == json.dumps(result["points"], sort_keys=True, default=str)
            ), f"volatility points differ for {label}"
            assert result["evaluated_bars"] == ref_evaluated, label

    def test_divergences_match_per_prefix_reference(self):
        from analysis import _historical_divergences

        for bars, label in [
            (self._bars_edge(), "edge"),
            (self._bars_random(40, 1), "r40"),
            (self._bars_random(120, 3), "r120"),
            (self._bars_random(250, 4), "r250"),
        ]:
            ref_points, ref_evaluated, ref_detected = self._reference_divergences(bars)
            result = _historical_divergences(bars)
            assert (
                json.dumps(ref_points, sort_keys=True, default=str)
                == json.dumps(result["points"], sort_keys=True, default=str)
            ), f"divergence points differ for {label}"
            assert result["evaluated_bars"] == ref_evaluated, label
            if ref_evaluated == 0:
                # The original envelope omits detected_bars when nothing was evaluated.
                assert "detected_bars" not in result, label
            else:
                assert result["detected_bars"] == ref_detected, label


class TestTransformerCache:
    """The Transformer result cache must be transparent, bounded and
    disable-able, and must never cache unavailable results.

    Determinism makes reuse semantically identical to retraining: the digested
    key covers every returned bar, the raw seq_len/epochs/lr arguments and all
    algorithm constants plus an implementation version string.
    """

    @staticmethod
    def _bars(count: int = 40, seed: int = 11) -> list[dict[str, Any]]:
        rng = random.Random(seed)
        bars: list[dict[str, Any]] = []
        price = 120.0
        for index in range(count):
            price += rng.uniform(-1.5, 1.8)
            bars.append(
                {
                    "timestamp": (
                        datetime(2024, 1, 1, tzinfo=timezone.utc)
                        + timedelta(days=index)
                    )
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "open": round(price, 2),
                    "high": round(price + 0.8, 2),
                    "low": round(price - 0.8, 2),
                    "close": round(price, 2),
                    "volume": round(max(1.0, 5000 + index * 10 + rng.uniform(-100, 100)), 2),
                }
            )
        return bars

    @pytest.fixture
    def training_counter(self, monkeypatch):
        """Count actual TinyTransformer constructions (each = one training)."""
        counter = {"trainings": 0}
        real_cls = _analysis._TinyTransformer

        class CountingTransformer(real_cls):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                counter["trainings"] += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(_analysis, "_TinyTransformer", CountingTransformer)
        return counter

    def test_cache_hit_returns_identical_result_without_retraining(
        self, training_counter
    ):
        bars = self._bars()

        first = calculate_transformer_anomaly(bars, epochs=3)
        assert first.get("available") is True
        assert training_counter["trainings"] == 1
        assert _analysis._tr_cache_size() == 1

        second = calculate_transformer_anomaly(bars, epochs=3)
        assert training_counter["trainings"] == 1, "cache hit retrained"
        assert json.dumps(first, sort_keys=True) == json.dumps(
            second, sort_keys=True
        ), "cached result differs from the trained one"

    def test_different_parameters_create_new_keys(self, training_counter):
        bars = self._bars()

        calculate_transformer_anomaly(bars, epochs=3)
        calculate_transformer_anomaly(bars, epochs=3)
        assert training_counter["trainings"] == 1

        calculate_transformer_anomaly(bars, epochs=4)
        assert training_counter["trainings"] == 2

        calculate_transformer_anomaly(bars, seq_len=6, epochs=3)
        assert training_counter["trainings"] == 3

    def test_changed_bars_invalidate_the_key(self, training_counter):
        bars = self._bars()
        calculate_transformer_anomaly(bars, epochs=3)
        assert training_counter["trainings"] == 1

        mutated = [dict(bar) for bar in bars]
        mutated[10]["close"] = float(mutated[10]["close"]) + 3.5
        calculate_transformer_anomaly(mutated, epochs=3)
        assert training_counter["trainings"] == 2, "changed bars reused old result"

        # Adding a bar changes the key too (a genuine new market day).
        extended = [dict(bar) for bar in bars]
        tail = self._bars(count=41, seed=5)[-1]
        extended.append(dict(tail))
        calculate_transformer_anomaly(extended, epochs=3)
        assert training_counter["trainings"] == 3

    def test_cached_result_is_isolated_from_mutations(self, training_counter):
        bars = self._bars()
        first = calculate_transformer_anomaly(bars, epochs=3)
        assert first.get("available") is True

        original_score = first.get("anomaly_score")
        first["anomaly_score"] = 999999.0
        if first.get("latest_features"):
            first["latest_features"]["price_return_pct"] = "mutated"

        second = calculate_transformer_anomaly(bars, epochs=3)
        assert second["anomaly_score"] == original_score
        assert second["latest_features"]["price_return_pct"] != "mutated"
        assert training_counter["trainings"] == 1

    def test_unavailable_results_are_never_cached(self, training_counter):
        # 15 bars leave fewer usable historical observations than TR_MIN_HISTORY.
        short_history = self._bars(count=15)
        first = calculate_transformer_anomaly(short_history, epochs=3)
        second = calculate_transformer_anomaly(short_history, epochs=3)
        assert first.get("available") is False
        assert second.get("available") is False
        assert _analysis._tr_cache_size() == 0, "unavailable result was cached"
        assert training_counter["trainings"] == 0, "unavailable path trained"

        # A valid call afterwards still computes and stores normally.
        valid = calculate_transformer_anomaly(self._bars(), epochs=3)
        assert valid.get("available") is True
        assert _analysis._tr_cache_size() == 1

    def test_ttl_expiry_forces_retraining(self, monkeypatch, training_counter):
        monkeypatch.setenv("CDF_TR_CACHE_TTL", "0.01")
        bars = self._bars()

        calculate_transformer_anomaly(bars, epochs=3)
        assert training_counter["trainings"] == 1

        time_module.sleep(0.06)  # comfortably past the 10 ms ttl
        calculate_transformer_anomaly(bars, epochs=3)
        assert training_counter["trainings"] == 2, "expired entry was reused"
        assert _analysis._tr_cache_size() == 1, "expired entry was not replaced"

    def test_environment_disable_stops_caching(self, monkeypatch, training_counter):
        monkeypatch.setenv("CDF_TR_CACHE", "0")
        bars = self._bars()

        calculate_transformer_anomaly(bars, epochs=3)
        calculate_transformer_anomaly(bars, epochs=3)
        assert training_counter["trainings"] == 2, "disabled cache still reused"
        assert _analysis._tr_cache_size() == 0

    def test_cache_is_bounded_and_evicts_oldest(self, monkeypatch):
        monkeypatch.delenv("CDF_TR_CACHE", raising=False)
        monkeypatch.delenv("CDF_TR_CACHE_TTL", raising=False)
        fake_result = {"available": True, "note": "entry"}

        extra = 2
        for index in range(_analysis.TR_CACHE_MAX_ENTRIES + extra):
            _analysis._tr_cache_put({"digest": f"key-{index}"}, fake_result)

        assert _analysis._tr_cache_size() == _analysis.TR_CACHE_MAX_ENTRIES
        # oldest entries ("key-0", "key-1") are evicted first
        assert _analysis._tr_cache_get({"digest": "key-0"}) is None
        assert _analysis._tr_cache_get({"digest": "key-1"}) is None
        newest = _analysis.TR_CACHE_MAX_ENTRIES + extra - 1
        assert _analysis._tr_cache_get({"digest": f"key-{newest}"}) is not None

    def test_unserialisable_arguments_bypass_the_cache(self, training_counter):
        circular: list[Any] = []
        circular.append(circular)
        assert _analysis._tr_cache_key(circular, 8, 3, 0.001) is None

        result = calculate_transformer_anomaly(circular, epochs=3)
        assert result.get("available") is False  # cannot form features anyway
        assert _analysis._tr_cache_size() == 0

    def test_cold_and_warm_calls_log_equally_shaped_payloads(self, training_counter):
        bars = self._bars(count=45)
        cold = calculate_transformer_anomaly(bars, epochs=3)
        warm = calculate_transformer_anomaly(bars, epochs=3)

        assert set(cold.keys()) == set(warm.keys())
        assert cold["history_limit"] == TR_MAX_HISTORY_BARS
        assert training_counter["trainings"] == 1
