/*
 * Pure helpers for the Stock Analysis page.
 *
 * This file is deliberately dependency-free so the same functions used by the
 * browser can be unit-tested with Node's built-in test runner.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.ChartUtils = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Keep these lists in sync with backend/market_data.py.
  const PERIODS = [
    "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max",
  ];
  const INTERVALS = [
    "1m", "2m", "5m", "15m", "30m", "60m", "90m",
    "1h", "1d", "5d", "1wk", "1mo", "3mo",
  ];

  const PERIOD_LABELS = {
    "1d": "1 day",
    "5d": "5 days",
    "1mo": "1 month",
    "3mo": "3 months",
    "6mo": "6 months",
    "1y": "1 year",
    "2y": "2 years",
    "5y": "5 years",
    "10y": "10 years",
    ytd: "year to date",
    max: "maximum",
  };

  // Historical windows offered in the results area. They are filtered again by
  // the active interval so an option is only shown when the provider supports
  // the pair. The labels are deliberately short for a compact segmented control.
  const TIMEFRAME_OPTIONS = Object.freeze([
    { period: "1d", label: "1D", title: "1 day" },
    { period: "5d", label: "5D", title: "5 days" },
    { period: "1mo", label: "1M", title: "1 month" },
    { period: "3mo", label: "3M", title: "3 months" },
    { period: "6mo", label: "6M", title: "6 months" },
    { period: "1y", label: "1Y", title: "1 year" },
    { period: "5y", label: "5Y", title: "5 years" },
  ]);

  const INTERVAL_LABELS = {
    "1m": "1 minute",
    "2m": "2 minutes",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "60m": "60 minutes",
    "90m": "90 minutes",
    "1h": "1 hour",
    "1d": "1 day",
    "5d": "5 days",
    "1wk": "1 week",
    "1mo": "1 month",
    "3mo": "3 months",
  };

  const INTRADAY_INTERVALS = new Set([
    "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h",
  ]);

  // Yahoo Finance intraday history limits, mirrored from the backend.
  // 1m -> 7 days; 2m/5m/15m/30m/90m -> 60 days; 60m/1h -> 730 days.
  const INTERVAL_PERIODS = {
    "1m": ["1d", "5d"],
    "2m": ["1d", "5d", "1mo"],
    "5m": ["1d", "5d", "1mo"],
    "15m": ["1d", "5d", "1mo"],
    "30m": ["1d", "5d", "1mo"],
    "90m": ["1d", "5d", "1mo"],
    "60m": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"],
    "1h": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"],
  };

  // Used only to choose the closest shorter period when reconciling a combo.
  const PERIOD_RANK = {
    "1d": 1,
    "5d": 2,
    "1mo": 3,
    "3mo": 4,
    "6mo": 5,
    "1y": 6,
    ytd: 6.5,
    "2y": 7,
    "5y": 8,
    "10y": 9,
    max: 10,
  };

  // Latest-bar card configuration. Kept separate from marker configuration so
  // adding a historical marker source never duplicates an analysis card.
  const DETECTOR_CONFIG = Object.freeze([
    {
      key: "lof_anomaly",
      name: "Local Outlier Factor (LOF)",
      short: "LOF",
      position: "aboveBar",
      shape: "arrowDown",
      color: "#7b1fa2",
      scoreField: "lof_score",
      scoreLabel: "LOF score",
      thresholdField: "threshold",
    },
    {
      key: "autoencoder_anomaly",
      name: "Autoencoder",
      short: "AE",
      position: "belowBar",
      shape: "circle",
      color: "#e65100",
      scoreField: "reconstruction_error",
      scoreLabel: "Reconstruction error",
      thresholdField: "threshold",
    },
    {
      key: "transformer_anomaly",
      name: "Transformer",
      short: "TR",
      position: "inBar",
      shape: "arrowUp",
      color: "#00695c",
      scoreField: "anomaly_score",
      scoreLabel: "Prediction error",
      thresholdField: "threshold",
    },
  ]);

  // All anomaly markers deliberately share one restrained red circle style.
  // The legend and the marker-detail tooltip distinguish the detectors.
  const MARKER_DETECTOR_CONFIG = Object.freeze([
    {
      key: "lof_anomaly",
      name: "Local Outlier Factor (LOF)",
      short: "LOF",
      latestScoreField: "lof_score",
      latestScoreLabel: "LOF score",
      latestThresholdField: "threshold",
      latestSignalField: null,
      latestSignalValues: [],
      latestAnomalyField: "is_anomaly",
      hasScore: true,
      latestOnly: false,
    },
    {
      key: "volume_spike",
      name: "Volume spike",
      short: "VOL",
      latestScoreField: "volume_ratio",
      latestScoreLabel: "Volume ratio",
      latestThresholdField: null,
      latestThresholdValue: 2.0,
      latestSignalField: "signal",
      latestSignalValues: ["spike"],
      latestAnomalyField: null,
      hasScore: true,
      latestOnly: false,
    },
    {
      key: "volatility_anomaly",
      name: "Volatility anomaly",
      short: "ATR",
      latestScoreField: "volatility_ratio",
      latestScoreLabel: "Volatility ratio",
      latestThresholdField: null,
      latestThresholdValue: 2.0,
      latestSignalField: "signal",
      latestSignalValues: ["anomalous"],
      latestAnomalyField: null,
      hasScore: true,
      latestOnly: false,
    },
    {
      key: "volume_price_divergence",
      name: "Volume–price divergence",
      short: "DIV",
      latestScoreField: null,
      latestScoreLabel: null,
      latestThresholdField: null,
      latestSignalField: "signal",
      latestSignalValues: ["bullish", "bearish"],
      latestAnomalyField: null,
      hasScore: false,
      latestOnly: false,
    },
    {
      key: "autoencoder_anomaly",
      name: "Autoencoder",
      short: "AE",
      latestScoreField: "reconstruction_error",
      latestScoreLabel: "Reconstruction error",
      latestThresholdField: "threshold",
      latestSignalField: null,
      latestSignalValues: [],
      latestAnomalyField: "is_anomaly",
      hasScore: true,
      latestOnly: true,
    },
    {
      key: "transformer_anomaly",
      name: "Transformer",
      short: "TR",
      latestScoreField: "anomaly_score",
      latestScoreLabel: "Prediction error",
      latestThresholdField: "threshold",
      latestSignalField: null,
      latestSignalValues: [],
      latestAnomalyField: "is_anomaly",
      hasScore: true,
      latestOnly: true,
    },
  ]);

  // NSE and BSE both use India Standard Time; daily Yahoo bars are timestamped
  // at 18:30Z (00:00 IST), so formatting in IST preserves the trading date.
  const EXCHANGE_TIME_ZONE = "Asia/Kolkata";

  // ---------- Period / interval compatibility ----------
  function isIntraday(interval) {
    return INTRADAY_INTERVALS.has(interval);
  }

  function periodsForInterval(interval) {
    const supported = INTERVAL_PERIODS[interval];
    return supported ? supported.slice() : PERIODS.slice();
  }

  function isCompatible(period, interval) {
    return periodsForInterval(interval).includes(period);
  }

  function timeframesForInterval(interval) {
    return TIMEFRAME_OPTIONS.filter((option) =>
      isCompatible(option.period, interval)
    ).map((option) => ({ ...option }));
  }

  function timeframeLabel(period) {
    const option = TIMEFRAME_OPTIONS.find((item) => item.period === period);
    return option ? option.label : periodLabel(period);
  }

  function periodLabel(period) {
    return PERIOD_LABELS[period] || period || "unknown period";
  }

  function intervalLabel(interval) {
    return INTERVAL_LABELS[interval] || interval || "unknown interval";
  }

  function closestSupportedPeriod(period, interval) {
    const allowed = periodsForInterval(interval);
    if (allowed.includes(period)) return period;

    const targetRank = PERIOD_RANK[period] == null ? 0 : PERIOD_RANK[period];
    const ordered = allowed
      .slice()
      .sort((a, b) => (PERIOD_RANK[a] || 0) - (PERIOD_RANK[b] || 0));
    const shorter = ordered.filter(
      (candidate) => (PERIOD_RANK[candidate] || 0) <= targetRank
    );
    return shorter.length ? shorter[shorter.length - 1] : ordered[0];
  }

  /**
   * Keep a period/interval pair provider-compatible.
   *
   * changedField:
   *   "interval" -> the user just changed the interval, so shorten the period.
   *   "period" / "initial" -> the user chose the period, so fall back to 1 day.
   */
  function reconcileSelection(period, interval, changedField) {
    const safePeriod = PERIODS.includes(period) ? period : "3mo";
    const safeInterval = INTERVALS.includes(interval) ? interval : "1d";

    if (isCompatible(safePeriod, safeInterval)) {
      return {
        period: safePeriod,
        interval: safeInterval,
        changed: false,
        field: null,
        message: "",
      };
    }

    if (changedField === "interval") {
      const nextPeriod = closestSupportedPeriod(safePeriod, safeInterval);
      return {
        period: nextPeriod,
        interval: safeInterval,
        changed: true,
        field: "period",
        message:
          `${intervalLabel(safeInterval)} bars are not available for ` +
          `${periodLabel(safePeriod)}. Period changed to ${periodLabel(nextPeriod)}.`,
      };
    }

    return {
      period: safePeriod,
      interval: "1d",
      changed: true,
      field: "interval",
      message:
        `${intervalLabel(safeInterval)} bars are not available for ` +
        `${periodLabel(safePeriod)}. Interval changed to 1 day.`,
    };
  }

  // ---------- Timestamps and number normalisation ----------
  function parseTimestamp(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return null;
      return Math.floor(value > 1e12 ? value / 1000 : value);
    }
    if (typeof value === "string") {
      const milliseconds = Date.parse(value);
      if (!Number.isFinite(milliseconds)) return null;
      return Math.floor(milliseconds / 1000);
    }
    return null;
  }

  function finiteNumber(value) {
    if (
      value === null ||
      value === undefined ||
      value === "" ||
      typeof value === "boolean"
    ) {
      return null;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function barHasCompleteOHLC(bar) {
    if (!bar || typeof bar !== "object") return false;
    const open = finiteNumber(bar.open);
    const high = finiteNumber(bar.high);
    const low = finiteNumber(bar.low);
    const close = finiteNumber(bar.close);
    if ([open, high, low, close].some((value) => value === null)) return false;
    if (!(open > 0 && high > 0 && low > 0 && close > 0)) return false;
    return high >= low;
  }

  function barHasCompleteOHLCV(bar) {
    if (!barHasCompleteOHLC(bar)) return false;
    const volume = finiteNumber(bar.volume);
    return volume !== null && volume >= 0;
  }

  /**
   * Convert backend bars into chart-ready bars using only returned values.
   * Bars with unusable OHLC are skipped (never filled or interpolated).
   * Volume is optional; a missing volume becomes null, not zero.
   */
  function mapBars(rawBars) {
    const source = Array.isArray(rawBars) ? rawBars : [];
    const skipped = [];
    const candidates = [];

    source.forEach((bar, index) => {
      const timestamp = bar && typeof bar === "object" ? bar.timestamp : null;
      const time = parseTimestamp(timestamp);
      const open = finiteNumber(bar && bar.open);
      const high = finiteNumber(bar && bar.high);
      const low = finiteNumber(bar && bar.low);
      const close = finiteNumber(bar && bar.close);

      if (
        time === null ||
        open === null ||
        high === null ||
        low === null ||
        close === null
      ) {
        skipped.push({
          index,
          timestamp,
          reason: "missing or invalid OHLC",
        });
        return;
      }
      if (high < low) {
        skipped.push({ index, timestamp, reason: "high is below low" });
        return;
      }

      const volume = finiteNumber(bar.volume);
      candidates.push({
        time,
        timestamp,
        open,
        high,
        low,
        close,
        volume: volume !== null && volume >= 0 ? volume : null,
        rawIndex: index,
      });
    });

    candidates.sort((a, b) => a.time - b.time);

    const bars = [];
    candidates.forEach((bar) => {
      const previous = bars.length ? bars[bars.length - 1] : null;
      if (previous && previous.time === bar.time) {
        skipped.push({
          index: bar.rawIndex,
          timestamp: bar.timestamp,
          reason: "duplicate timestamp (kept the last returned value)",
        });
        bars[bars.length - 1] = bar;
        return;
      }
      bars.push(bar);
    });

    const lastRaw = source.length ? source[source.length - 1] : null;
    return {
      bars,
      skipped,
      total: source.length,
      firstTime: bars.length ? bars[0].time : null,
      lastTime: bars.length ? bars[bars.length - 1].time : null,
      lastRaw,
      lastRawHasOHLC: barHasCompleteOHLC(lastRaw),
      lastRawComplete: barHasCompleteOHLCV(lastRaw),
    };
  }

  function barAtTimestamp(bars, timestamp) {
    const target = parseTimestamp(timestamp);
    if (target === null || !Array.isArray(bars)) return null;
    for (let index = bars.length - 1; index >= 0; index -= 1) {
      if (bars[index].time === target) return bars[index];
    }
    return null;
  }

  // ---------- Formatting ----------
  function formatPrice(value, digits) {
    const number = finiteNumber(value);
    if (number === null) return "—";
    const fractionDigits = digits == null ? 2 : digits;
    return number.toLocaleString(undefined, {
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    });
  }

  function formatVolume(value, compact) {
    const number = finiteNumber(value);
    if (number === null) return "—";
    if (compact) {
      return number.toLocaleString(undefined, {
        notation: "compact",
        maximumFractionDigits: 2,
      });
    }
    return number.toLocaleString(undefined, {
      maximumFractionDigits: 0,
    });
  }

  function formatTime(iso, interval) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    if (isIntraday(interval)) {
      return date.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: EXCHANGE_TIME_ZONE,
      });
    }
    return date.toLocaleDateString(undefined, {
      dateStyle: "medium",
      timeZone: EXCHANGE_TIME_ZONE,
    });
  }

  function formatDateTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    return date.toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "medium",
      timeZone: EXCHANGE_TIME_ZONE,
    });
  }

  /**
   * Lightweight Charts tick-mark formatter.
   * tickMarkType: 0 year, 1 month, 2 day of month, 3 time, 4 time with seconds.
   */
  function formatTickMark(time, tickMarkType, interval) {
    const date = new Date(time * 1000);
    if (Number.isNaN(date.getTime())) return "";
    const zone = { timeZone: EXCHANGE_TIME_ZONE };

    if (isIntraday(interval)) {
      if (tickMarkType === 0) {
        return date.toLocaleDateString(undefined, {
          ...zone,
          year: "numeric",
        });
      }
      if (tickMarkType === 1) {
        return date.toLocaleDateString(undefined, {
          ...zone,
          month: "short",
        });
      }
      if (tickMarkType === 2) {
        return date.toLocaleDateString(undefined, {
          ...zone,
          month: "short",
          day: "numeric",
        });
      }
      return date.toLocaleTimeString(undefined, {
        ...zone,
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }

    if (tickMarkType === 0) {
      return date.toLocaleDateString(undefined, {
        ...zone,
        year: "numeric",
      });
    }
    if (tickMarkType === 1) {
      // Month ticks stay as "Aug" so they cannot be mistaken for a day
      // number. Year ticks (type 0) provide the year for multi-year ranges.
      return date.toLocaleDateString(undefined, {
        ...zone,
        month: "short",
      });
    }
    return date.toLocaleDateString(undefined, {
      ...zone,
      day: "numeric",
    });
  }

  // ---------- Anomaly marker placement ----------
  function _uniqueStrings(values) {
    return Array.from(
      new Set(values.filter((value) => typeof value === "string" && value.trim()))
    );
  }

  function _lastBarWithVolume(bars) {
    if (!Array.isArray(bars)) return null;
    for (let index = bars.length - 1; index >= 0; index -= 1) {
      const bar = bars[index];
      const volume = finiteNumber(bar && bar.volume);
      if (bar && volume !== null && volume > 0) return bar;
    }
    return null;
  }

  function _inferLatestTimestamp(config, bars) {
    if (!Array.isArray(bars) || !bars.length) return null;
    if (config.key === "volume_spike" || config.key === "volume_price_divergence") {
      const withVolume = _lastBarWithVolume(bars);
      return withVolume ? withVolume.timestamp : null;
    }
    return bars[bars.length - 1].timestamp;
  }

  function _latestMarkerCandidate(config, block, bars) {
    if (!block || typeof block !== "object") {
      return { status: "unavailable", reason: "No analysis block was returned." };
    }

    if (block.available !== true) {
      return {
        status: "unavailable",
        reason:
          block.explanation || "The backend marked this detector unavailable.",
      };
    }

    let flagged = false;
    if (config.latestAnomalyField) {
      const value = block[config.latestAnomalyField];
      if (typeof value !== "boolean") {
        return {
          status: "unavailable",
          reason:
            "The backend marked the detector available but did not return a " +
            "boolean anomaly flag.",
        };
      }
      flagged = value === true;
    } else if (config.latestSignalField) {
      flagged = config.latestSignalValues.includes(block[config.latestSignalField]);
    } else {
      return {
        status: "unavailable",
        reason: "This detector has no defined latest-bar anomaly condition.",
      };
    }

    if (!flagged) {
      return {
        status: "clear",
        reason: block.explanation || "",
        score: config.latestScoreField
          ? finiteNumber(block[config.latestScoreField])
          : null,
        threshold: config.latestThresholdField
          ? finiteNumber(block[config.latestThresholdField])
          : finiteNumber(config.latestThresholdValue),
        timestamp: block.evaluated_timestamp || _inferLatestTimestamp(config, bars),
      };
    }

    return {
      status: "flagged",
      reason: block.explanation || "",
      score: config.latestScoreField
        ? finiteNumber(block[config.latestScoreField])
        : null,
      threshold: config.latestThresholdField
        ? finiteNumber(block[config.latestThresholdField])
        : finiteNumber(config.latestThresholdValue),
      timestamp: block.evaluated_timestamp || _inferLatestTimestamp(config, bars),
      signal: config.latestSignalField ? block[config.latestSignalField] : null,
      values: {},
      source: "latest",
    };
  }

  function _historicalEnvelope(historical, key) {
    const detectors = historical && historical.detectors;
    if (!detectors || typeof detectors !== "object") return null;
    const block = detectors[key];
    return block && typeof block === "object" ? block : null;
  }

  /**
   * Build red circular markers from timestamped historical detector points and
   * the real latest-bar flags. Every marker is anchored to a returned complete
   * bar; markers that cannot be mapped are reported, never guessed. Multiple
   * detector points on one candle share one clustered marker so the chart stays
   * legible, while the cluster retains every detector detail for its tooltip.
   */
  function buildAnomalyMarkers(analysis, bars, historical, options) {
    const issues = [];
    const limitations = [];
    const entries = [];
    const placedPoints = [];
    const maximumLabels = Math.max(
      0,
      Number.isFinite(options && options.maximumLabels)
        ? options.maximumLabels
        : 40
    );

    if (historical && Array.isArray(historical.limitations)) {
      limitations.push(...historical.limitations);
    }

    if (!analysis || typeof analysis !== "object") {
      return {
        markers: [],
        entries: [],
        issues: ["No analysis object was returned by the backend."],
        limitations: _uniqueStrings(limitations),
        flaggedCount: 0,
        markedCandles: 0,
        pointCount: 0,
        latestOnlyKeys: [],
        labelsSuppressed: false,
      };
    }

    MARKER_DETECTOR_CONFIG.forEach((config) => {
      const block = analysis[config.key];
      const envelope = _historicalEnvelope(historical, config.key);
      const envelopeScope = envelope && envelope.scope ? envelope.scope : null;
      const latestOnly =
        config.latestOnly ||
        envelopeScope === "latest_only" ||
        (!envelope && config.latestOnly);
      const historicalAvailable = Boolean(
        envelope &&
          envelope.scope !== "latest_only" &&
          envelope.historical_available !== false &&
          envelope.available === true
      );

      if (envelope && Array.isArray(envelope.limitations)) {
        limitations.push(...envelope.limitations);
      }
      if (envelope && envelope.scope === "latest_only" && envelope.reason) {
        limitations.push(envelope.reason);
      }

      const base = {
        key: config.key,
        name: config.name,
        short: config.short,
        color: "#ef4444",
        position: "aboveBar",
        scoreLabel: config.latestScoreLabel,
        scope: historicalAvailable ? "historical" : "latest_only",
        historicalAvailable,
        hasHistoricalEnvelope: Boolean(envelope),
        latestOnly,
      };

      const detectorPoints = [];
      const seenTimes = new Set();
      let historicalCount = 0;
      let unplacedCount = 0;
      let lastPlacedBar = null;

      if (
        envelope &&
        envelope.scope !== "latest_only" &&
        envelope.available === true &&
        Array.isArray(envelope.points)
      ) {
        envelope.points.forEach((point) => {
          if (!point || point.is_anomaly !== true) return;
          const parsed = parseTimestamp(point.timestamp);
          if (parsed === null) return;
          if (seenTimes.has(parsed)) return;
          seenTimes.add(parsed);

          const bar = barAtTimestamp(bars, point.timestamp);
          if (!bar || finiteNumber(bar.close) === null) {
            unplacedCount += 1;
            const reason =
              `${config.name} returned a historical point whose timestamp ` +
              `(${point.timestamp || "missing"}) does not match a complete ` +
              `returned bar, so no marker was drawn for that point.`;
            issues.push(reason);
            return;
          }

          historicalCount += 1;
          lastPlacedBar = bar;
          detectorPoints.push({
            key: config.key,
            detectorName: config.name,
            short: config.short,
            timestamp: bar.timestamp,
            time: bar.time,
            price: bar.close,
            source: "historical",
            signal: point.signal || null,
            score: finiteNumber(point.score),
            threshold: finiteNumber(point.threshold),
            scoreLabel: point.score_label || config.latestScoreLabel,
            values:
              point.values && typeof point.values === "object"
                ? { ...point.values }
                : {},
            explanation: point.explanation || "",
          });
        });
      }

      const latest = _latestMarkerCandidate(config, block, bars);
      let latestPlaced = false;
      let latestBar = null;

      if (latest.status === "flagged") {
        latestBar = barAtTimestamp(bars, latest.timestamp);
        if (!latestBar || finiteNumber(latestBar.close) === null) {
          const reason =
            `${config.name} flagged its latest evaluated bar, but its ` +
            `timestamp (${latest.timestamp || "missing"}) does not match a ` +
            `complete returned bar, so no marker was drawn for that point.`;
          issues.push(reason);
          unplacedCount += 1;
        } else if (!seenTimes.has(latestBar.time)) {
          latestPlaced = true;
          seenTimes.add(latestBar.time);
          lastPlacedBar = latestBar;
          detectorPoints.push({
            key: config.key,
            detectorName: config.name,
            short: config.short,
            timestamp: latestBar.timestamp,
            time: latestBar.time,
            price: latestBar.close,
            source: "latest",
            signal: latest.signal || null,
            score: latest.score,
            threshold: latest.threshold,
            scoreLabel: config.latestScoreLabel,
            values: latest.values || {},
            explanation: latest.reason || "",
          });
        }
      }

      detectorPoints.forEach((point) => placedPoints.push(point));

      const flaggedCount = detectorPoints.length;
      let status = "unavailable";
      if (
        flaggedCount > 0 ||
        latestPlaced ||
        (latest.status === "flagged" && latestBar)
      ) {
        status = "flagged";
      } else if (latest.status === "clear" || (envelope && envelope.available === true)) {
        status = "clear";
      } else if (latest.status === "unavailable" && !envelope) {
        status = "unavailable";
      } else if (latest.status === "unavailable" && envelope) {
        status = envelope.available === false ? "unavailable" : "clear";
      }
      if (status !== "flagged" && unplacedCount > 0 && latest.status === "flagged") {
        status = "unplaced";
      }

      let reason =
        latest.status === "unavailable" || latest.status === "clear"
          ? latest.reason || ""
          : (envelope && envelope.reason) || latest.reason || "";
      if (
        envelope &&
        envelope.available !== true &&
        !latest.reason &&
        envelope.reason
      ) {
        reason = envelope.reason;
      }

      entries.push({
        ...base,
        status,
        reason,
        score: latest.score === undefined ? null : latest.score,
        threshold: latest.threshold === undefined ? null : latest.threshold,
        evaluatedTimestamp:
          (latestBar && latestBar.timestamp) ||
          (lastPlacedBar && lastPlacedBar.timestamp) ||
          (latest.timestamp || null),
        barTimestamp: lastPlacedBar ? lastPlacedBar.timestamp : null,
        price: lastPlacedBar ? finiteNumber(lastPlacedBar.close) : null,
        historicalPoints: historicalCount,
        latestFlag: latest.status === "flagged" && Boolean(latestBar),
        detectedBars: finiteNumber(envelope && envelope.detected_bars),
        unplacedPoints: unplacedCount,
        method: (envelope && envelope.method) || block?.method || "",
      });
    });

    const byTime = new Map();
    placedPoints.forEach((point) => {
      if (!byTime.has(point.time)) {
        byTime.set(point.time, {
          time: point.time,
          barTimestamp: point.timestamp,
          price: point.price,
          points: [],
        });
      }
      byTime.get(point.time).points.push(point);
    });

    const clusters = Array.from(byTime.values()).sort((a, b) => a.time - b.time);
    const labelsSuppressed = clusters.length > maximumLabels;
    const markers = clusters.map((cluster) => {
      const text =
        labelsSuppressed || cluster.points.length === 0
          ? ""
          : cluster.points.length > 1
            ? String(cluster.points.length)
            : cluster.points[0].short;
      return {
        time: cluster.time,
        position: "aboveBar",
        color: "#ef4444",
        shape: "circle",
        text,
        size: labelsSuppressed
          ? 0.55
          : cluster.points.length > 1
            ? 1.15
            : 1,
      };
    });

    return {
      markers,
      clusters,
      entries,
      issues,
      limitations: _uniqueStrings(limitations),
      flaggedCount: markers.length,
      markedCandles: clusters.length,
      pointCount: placedPoints.length,
      latestOnlyKeys: entries
        .filter((entry) => entry.latestOnly)
        .map((entry) => entry.key),
      labelsSuppressed,
    };
  }

  function markerClusterAtTime(markerResult, time) {
    if (!markerResult || !Array.isArray(markerResult.clusters)) return null;
    const target = parseTimestamp(time);
    if (target === null) return null;
    return (
      markerResult.clusters.find((cluster) => cluster.time === target) || null
    );
  }

  /**
   * Price lines for values the backend actually returned. SMA/EMA are latest
   * values only (no historical series was returned), so they are labelled
   * "latest"; Fibonacci levels are derived from the returned range.
   */
  function buildIndicatorLevels(analysis) {
    const levels = [];
    if (!analysis || typeof analysis !== "object") return levels;

    const sma = analysis.sma;
    if (sma && sma.available === true) {
      const price = finiteNumber(sma.value);
      if (price !== null) {
        levels.push({
          id: "sma",
          title: `SMA ${sma.period == null ? "" : sma.period} (latest)`.trim(),
          price,
          color: "#2962ff",
          lineStyle: 2,
          axisLabelVisible: true,
          source: "sma",
        });
      }
    }

    const ema = analysis.ema_deviation;
    if (ema && ema.available === true) {
      const price = finiteNumber(ema.ema);
      if (price !== null) {
        levels.push({
          id: "ema",
          title: `EMA ${ema.period == null ? "" : ema.period} (latest)`.trim(),
          price,
          color: "#00897b",
          lineStyle: 2,
          axisLabelVisible: true,
          source: "ema",
        });
      }
    }

    const fibonacci = analysis.fibonacci;
    if (fibonacci && fibonacci.levels && typeof fibonacci.levels === "object") {
      Object.keys(fibonacci.levels).forEach((ratio) => {
        const price = finiteNumber(fibonacci.levels[ratio]);
        if (price === null) return;
        const percent = Number(ratio) * 100;
        const label = Number.isFinite(percent)
          ? `${percent.toFixed(1)}%`
          : String(ratio);
        levels.push({
          id: `fib-${ratio}`,
          title: `Fib ${label}`,
          price,
          color: "#9e9e9e",
          lineStyle: 1,
          // The levels stay visible as dotted lines, but their right-axis
          // labels are suppressed because five levels plus SMA/EMA overlap on
          // compressed ranges. The Fibonacci card below lists every level.
          axisLabelVisible: false,
          source: "fibonacci",
        });
      });
    }

    return levels;
  }

  // ---------- Request ordering ----------
  class RequestGuard {
    constructor() {
      this.sequence = 0;
      this.controller = null;
    }

    begin() {
      this.invalidate();
      const id = this.sequence;
      const controller = new AbortController();
      this.controller = controller;
      return {
        id,
        signal: controller.signal,
        isCurrent: () => this.isCurrent(id),
      };
    }

    isCurrent(id) {
      return id === this.sequence;
    }

    invalidate() {
      this.cancel();
      this.sequence += 1;
    }

    cancel() {
      if (this.controller) {
        this.controller.abort();
        this.controller = null;
      }
    }
  }

  // ---------- Request URL and deep links ----------
  function buildHistoryUrl(base, selection) {
    const params = new URLSearchParams({
      symbol: selection.symbol,
      exchange: selection.exchange,
      period: selection.period,
      interval: selection.interval,
      analyze: selection.analyze === false ? "false" : "true",
    });
    return `${String(base).replace(/\/+$/, "")}/api/market/history?${params.toString()}`;
  }

  function validateSymbol(value) {
    const symbol = String(value == null ? "" : value).trim().toUpperCase();
    if (!/^[A-Z0-9.^-]{1,30}$/.test(symbol)) {
      return {
        valid: false,
        symbol,
        message:
          "Symbols may only contain letters, digits, dots, carets and " +
          "hyphens (matching the backend rule).",
      };
    }
    return { valid: true, symbol, message: "" };
  }

  /**
   * Read and reconcile a deep link such as
   * ?symbol=RELIANCE&exchange=NSE&period=6mo&interval=1d
   */
  function selectionFromParams(params, defaults) {
    const fallback = Object.assign(
      { exchange: "NSE", period: "3mo", interval: "1d" },
      defaults || {}
    );
    const notes = [];

    const symbol = String(params.get("symbol") || "").trim().toUpperCase();
    let exchange = String(params.get("exchange") || fallback.exchange)
      .trim()
      .toUpperCase();
    let period = String(params.get("period") || fallback.period).trim();
    let interval = String(params.get("interval") || fallback.interval).trim();

    if (exchange !== "NSE" && exchange !== "BSE") {
      notes.push(`Unknown exchange "${exchange}" was ignored.`);
      exchange = fallback.exchange;
    }
    if (!PERIODS.includes(period)) {
      notes.push(`Unknown period "${period}" was ignored.`);
      period = fallback.period;
    }
    if (!INTERVALS.includes(interval)) {
      notes.push(`Unknown interval "${interval}" was ignored.`);
      interval = fallback.interval;
    }

    const reconciled = reconcileSelection(period, interval, "initial");
    if (reconciled.changed) notes.push(reconciled.message);

    return {
      symbol,
      exchange,
      period: reconciled.period,
      interval: reconciled.interval,
      notes,
    };
  }

  return {
    PERIODS,
    INTERVALS,
    PERIOD_LABELS,
    INTERVAL_LABELS,
    TIMEFRAME_OPTIONS,
    DETECTOR_CONFIG,
    MARKER_DETECTOR_CONFIG,
    isIntraday,
    periodsForInterval,
    timeframesForInterval,
    timeframeLabel,
    isCompatible,
    periodLabel,
    intervalLabel,
    closestSupportedPeriod,
    reconcileSelection,
    parseTimestamp,
    finiteNumber,
    barHasCompleteOHLC,
    barHasCompleteOHLCV,
    mapBars,
    barAtTimestamp,
    formatPrice,
    formatVolume,
    formatTime,
    formatDateTime,
    formatTickMark,
    buildAnomalyMarkers,
    markerClusterAtTime,
    buildIndicatorLevels,
    RequestGuard,
    buildHistoryUrl,
    validateSymbol,
    selectionFromParams,
  };
});
