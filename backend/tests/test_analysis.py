"""Unit tests for analysis.py - RSI, SMA, Fibonacci edge cases, EMA deviation, divergence, volume spike, volatility anomaly, and LOF."""

import math
import random
from datetime import datetime, timedelta, timezone

from analysis import (
    analyze_bars,
    calculate_divergence,
    calculate_ema_deviation,
    calculate_lof_anomaly,
    calculate_autoencoder_anomaly,
    calculate_transformer_anomaly,
    calculate_volume_spike,
    calculate_volatility_anomaly,
)
from typing import Any


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
            volumes=[1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000, 1500, 1600, 1700, 1700, 1800, 1900, 1900, 2000, 3000, 4000]
        )
        result = calculate_volume_spike(bars, period=5)
        assert result["available"] is True
        assert result["latest_volume"] == 4000
        assert result["baseline_average"] > 1000  # average of first 19 bars
        assert result["volume_ratio"] > 1.0
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


class TestAutoEncoderAnomaly:
    """Autoencoder reconstruction-error scoring of the latest bar."""

    @staticmethod
    def _make_bars(count: int = 41, seed: int = 11) -> list[dict[str, Any]]:
        """Deterministic pseudo-random OHLCV series (no network needed)."""
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

    def test_normal_dataset_not_flagged(self):
        """A typical latest bar in a well-populated history is not an outlier."""
        bars = self._make_bars()
        result = calculate_autoencoder_anomaly(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is False
        assert result["reconstruction_error"] <= result["threshold"]
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
        latest["close"] = latest["close"] * 1.25
        latest["high"] = latest["close"] * 1.02
        latest["low"] = latest["close"] * 0.98
        latest["volume"] = latest["volume"] * 30

        result = calculate_autoencoder_anomaly(bars)

        assert result["available"] is True
        assert result["is_anomaly"] is True
        assert result["reconstruction_error"] > result["threshold"]
        assert "not proof of market manipulation" in result["explanation"]

    def test_insufficient_history(self):
        """Too few bars returns an honest unavailable result."""
        result = calculate_autoencoder_anomaly(self._make_bars(10))

        assert result["available"] is False
        assert result["is_anomaly"] is None
        assert result["reconstruction_error"] is None
        assert result["threshold"] is None
        assert "Insufficient history" in result["explanation"]
        assert result["history_observations"] < 15

    def test_missing_and_invalid_values(self):
        """Missing lows and a missing latest volume are handled honestly."""
        bars = self._make_bars()
        for bar in bars:
            bar["low"] = None
        result = calculate_autoencoder_anomaly(bars)
        assert result["available"] is False
        assert "No usable consecutive OHLCV observations" in result["explanation"]

        bars = self._make_bars()
        bars[-1]["volume"] = None
        result = calculate_autoencoder_anomaly(bars)
        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert result["evaluated_timestamp"] == bars[-2]["timestamp"]
        assert "trailing returned bar" in result["explanation"]

        bars = self._make_bars()
        bars[-1]["close"] = float("nan")
        result = calculate_autoencoder_anomaly(bars)
        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert isinstance(result["is_anomaly"], bool)

    def test_constant_features(self):
        """A flat series has no usable variance for the autoencoder."""
        bars = []
        start = datetime(2026, 2, 1, tzinfo=timezone.utc)
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

        result = calculate_autoencoder_anomaly(bars)

        assert result["available"] is False
        assert result["is_anomaly"] is None
        assert "constant" in result["explanation"]

    def test_invalid_hidden_layers(self):
        """Invalid hidden layer sizes are rejected."""
        bars = self._make_bars()

        for bad_layers in ((), [], [0], [-1], "8,4,8", None, True, [1.5]):
            result = calculate_autoencoder_anomaly(bars, hidden_layers=bad_layers)
            assert result["available"] is False, bad_layers
            assert result["explanation"]

    def test_deterministic_output(self):
        """The same input produces the same output every time."""
        bars = self._make_bars()
        first = calculate_autoencoder_anomaly(bars)
        second = calculate_autoencoder_anomaly(bars)

        assert first["available"] is True
        assert first["reconstruction_error"] == second["reconstruction_error"]
        assert first["threshold"] == second["threshold"]
        assert first["is_anomaly"] == second["is_anomaly"]
        assert first["latest_features"] == second["latest_features"]
        assert first["explanation"] == second["explanation"]

    def test_latest_bar_is_excluded_from_training(self):
        """The latest bar is excluded from fitting; history count proves it."""
        bars = self._make_bars(41)
        result = calculate_autoencoder_anomaly(bars)

        assert result["available"] is True
        assert result["history_observations"] == 39  # 41 bars -> 40 rows, 39 historical
        assert result["evaluated_timestamp"] == bars[-1]["timestamp"]
        assert "trained only on" in result["explanation"]
        assert "scored separately" in result["explanation"]

    def test_trailing_incomplete_bar_is_excluded_not_blocking(self):
        """Yahoo's partial latest bar must not disable reconstruction scoring."""
        complete = calculate_autoencoder_anomaly(self._make_bars(41))
        assert complete["available"] is True
        assert complete["excluded_trailing_bars"] == 0

        bars = self._make_bars(41)
        bars[-1]["close"] = None
        result = calculate_autoencoder_anomaly(bars)

        assert result["available"] is True
        assert result["excluded_trailing_bars"] == 1
        assert result["evaluated_timestamp"] == bars[-2]["timestamp"]
        assert result["history_observations"] == len(bars) - 3
        assert "trailing returned bar" in result["explanation"]


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