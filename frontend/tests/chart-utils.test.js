"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const CU = require("../chart-utils.js");

function makeBars() {
  return [
    {
      timestamp: "2026-01-03T00:00:00Z",
      open: 12,
      high: 13,
      low: 11,
      close: 12.5,
      volume: 1000,
    },
    {
      timestamp: "2026-01-01T00:00:00Z",
      open: 10,
      high: 11,
      low: 9,
      close: 10.5,
      volume: 900,
    },
    {
      timestamp: "2026-01-02T00:00:00Z",
      open: 11,
      high: 12,
      low: 10,
      close: 11.5,
      volume: null,
    },
    {
      timestamp: "2026-01-02T00:00:00Z",
      open: 11,
      high: 12,
      low: 10,
      close: 11.6,
      volume: 950,
    },
    {
      timestamp: "2026-01-04T00:00:00Z",
      open: 13,
      high: 14,
      low: 12,
      close: null,
      volume: 1000,
    },
    {
      timestamp: "not-a-date",
      open: 1,
      high: 1,
      low: 1,
      close: 1,
      volume: 1,
    },
    {
      timestamp: "2026-01-05T00:00:00Z",
      open: 14,
      high: 13,
      low: 15,
      close: 14,
      volume: 1,
    },
  ];
}

test("period/interval compatibility mirrors provider limits", () => {
  assert.equal(CU.isCompatible("1d", "1m"), true);
  assert.equal(CU.isCompatible("5d", "1m"), true);
  assert.equal(CU.isCompatible("3mo", "1m"), false);
  assert.equal(CU.isCompatible("1mo", "5m"), true);
  assert.equal(CU.isCompatible("3mo", "5m"), false);
  assert.equal(CU.isCompatible("2y", "1h"), true);
  assert.equal(CU.isCompatible("5y", "1h"), false);
  assert.equal(CU.isCompatible("max", "1d"), true);

  assert.ok(CU.periodsForInterval("1h").includes("2y"));
  assert.ok(!CU.periodsForInterval("1h").includes("5y"));
  assert.deepEqual(CU.periodsForInterval("1d"), CU.PERIODS);
});

test("timeframe options are honest supported subsets and keep short labels", () => {
  assert.deepEqual(
    CU.timeframesForInterval("1d").map((option) => option.period),
    ["1d", "5d", "1mo", "3mo", "6mo", "1y", "5y"]
  );
  assert.deepEqual(
    CU.timeframesForInterval("1m").map((option) => option.period),
    ["1d", "5d"]
  );
  assert.deepEqual(
    CU.timeframesForInterval("1h").map((option) => option.period),
    ["1d", "5d", "1mo", "3mo", "6mo", "1y"]
  );
  assert.equal(CU.timeframeLabel("1mo"), "1M");
  assert.equal(CU.timeframeLabel("5y"), "5Y");
  assert.equal(CU.timeframeLabel("2y"), "2 years");
});

test("daily axis labels cannot read like an out-of-order day number", () => {
  const monthTick = Date.UTC(2026, 7, 1, 18, 30) / 1000;
  const dayTick = Date.UTC(2026, 7, 13, 18, 30) / 1000;
  const yearTick = Date.UTC(2026, 0, 1, 18, 30) / 1000;

  assert.match(CU.formatTickMark(monthTick, 1, "1d"), /^[A-Za-z]{3,}$/);
  assert.match(CU.formatTickMark(dayTick, 2, "1d"), /^\d{1,2}$/);
  assert.match(CU.formatTickMark(yearTick, 0, "1d"), /^\d{4}$/);
});

test("reconcileSelection shortens the period when the interval changes", () => {
  const first = CU.reconcileSelection("3mo", "1m", "interval");
  assert.equal(first.changed, true);
  assert.equal(first.interval, "1m");
  assert.equal(first.period, "5d");
  assert.match(first.message, /5 days/i);

  const second = CU.reconcileSelection("5y", "1h", "interval");
  assert.equal(second.changed, true);
  assert.equal(second.interval, "1h");
  assert.equal(second.period, "2y");

  const third = CU.reconcileSelection("max", "5m", "interval");
  assert.equal(third.period, "1mo");
});

test("reconcileSelection keeps the period and falls back to daily bars", () => {
  const result = CU.reconcileSelection("3mo", "1m", "period");
  assert.equal(result.changed, true);
  assert.equal(result.period, "3mo");
  assert.equal(result.interval, "1d");
  assert.match(result.message, /1 day/i);

  const unchanged = CU.reconcileSelection("6mo", "1d", "initial");
  assert.equal(unchanged.changed, false);
  assert.equal(unchanged.period, "6mo");
  assert.equal(unchanged.interval, "1d");
});

test("selectionFromParams prefers the window parameter", () => {
  const linked = CU.selectionFromParams(
    new URLSearchParams("?symbol=tcs&exchange=bse&window=max")
  );
  assert.equal(linked.window, "max");
  assert.equal(linked.period, "max");
  assert.equal(linked.interval, "1d");
  assert.deepEqual(linked.notes, []);

  // An unknown window falls back to the legacy period, not to a guess.
  const unknownWindow = CU.selectionFromParams(
    new URLSearchParams("?symbol=infy&window=99x&period=6mo")
  );
  assert.equal(unknownWindow.window, "6mo");
  assert.equal(unknownWindow.period, "6mo");
  assert.equal(unknownWindow.interval, "1d");
  assert.ok(unknownWindow.notes.some((note) => /Unknown window/.test(note)));

  // A window parameter overrides a stale legacy period on the same link.
  const windowWins = CU.selectionFromParams(
    new URLSearchParams("?symbol=infy&window=1mo&period=5y")
  );
  assert.equal(windowWins.window, "1mo");
  assert.equal(windowWins.period, "1mo");
  assert.equal(windowWins.interval, "1d");
  assert.deepEqual(windowWins.notes, []);
});

test("legacy period/interval links map onto windows with honest notes", () => {
  const twoYears = CU.selectionFromParams(
    new URLSearchParams("?symbol=tcs&period=2y")
  );
  assert.equal(twoYears.window, "max");
  assert.equal(twoYears.period, "max");
  assert.equal(twoYears.interval, "1d");
  assert.ok(twoYears.notes.some((note) => /2 years/.test(note)));

  const weekly = CU.selectionFromParams(
    new URLSearchParams("?symbol=tcs&period=1y&interval=1wk")
  );
  assert.equal(weekly.window, "1y");
  assert.equal(weekly.period, "1y");
  assert.equal(weekly.interval, "1d", "windows always use daily bars");
  assert.ok(
    weekly.notes.some((note) => /daily bars/.test(note)),
    "the ignored interval is disclosed"
  );

  const intraday = CU.selectionFromParams(
    new URLSearchParams("?symbol=infy&period=1mo&interval=15m")
  );
  assert.equal(intraday.window, "1mo");
  assert.equal(intraday.interval, "1d");
  assert.ok(intraday.notes.some((note) => /daily bars/.test(note)));
});

test("validateSymbol normalizes case and rejects backend-invalid characters", () => {
  assert.deepEqual(CU.validateSymbol(" reliance "), {
    valid: true,
    symbol: "RELIANCE",
    message: "",
  });
  assert.equal(CU.validateSymbol("RELIANCE.NS").valid, true);
  assert.equal(CU.validateSymbol("M&M").valid, false);
  assert.equal(CU.validateSymbol("").valid, false);
  assert.equal(CU.validateSymbol("A".repeat(31)).valid, false);
});

test("selectionFromParams reads deep links and reconciles invalid pairs", () => {
  const basic = CU.selectionFromParams(
    new URLSearchParams("?symbol=reliance&exchange=nse")
  );
  assert.equal(basic.symbol, "RELIANCE");
  assert.equal(basic.exchange, "NSE");
  assert.equal(basic.window, "3mo");
  assert.equal(basic.period, "3mo");
  assert.equal(basic.interval, "1d");
  assert.deepEqual(basic.notes, []);

  const full = CU.selectionFromParams(
    new URLSearchParams("?symbol=tcs&exchange=bse&window=6mo")
  );
  assert.equal(full.symbol, "TCS");
  assert.equal(full.exchange, "BSE");
  assert.equal(full.window, "6mo");
  assert.equal(full.period, "6mo");
  assert.equal(full.interval, "1d");
  assert.deepEqual(full.notes, []);

  const incompatible = CU.selectionFromParams(
    new URLSearchParams("?symbol=INFY&period=3mo&interval=1m")
  );
  assert.equal(incompatible.period, "3mo");
  assert.equal(incompatible.interval, "1d");
  assert.ok(incompatible.notes.length >= 1);

  const unknown = CU.selectionFromParams(
    new URLSearchParams("?symbol=INFY&exchange=NYSE&period=forever&interval=2h")
  );
  assert.equal(unknown.exchange, "NSE");
  assert.equal(unknown.window, "3mo");
  assert.equal(unknown.period, "3mo");
  assert.equal(unknown.interval, "1d");
  assert.ok(unknown.notes.length >= 3);
});

test("HISTORICAL_WINDOWS map to provider-supported daily pairs", () => {
  assert.deepEqual(
    CU.HISTORICAL_WINDOWS.map((option) => option.window),
    ["1mo", "3mo", "6mo", "1y", "max"]
  );
  for (const option of CU.HISTORICAL_WINDOWS) {
    assert.equal(option.interval, "1d");
    assert.ok(CU.isCompatible(option.period, option.interval));
  }

  assert.deepEqual(CU.windowForId("6mo"), {
    window: "6mo",
    period: "6mo",
    interval: "1d",
    label: "6 months",
  });
  assert.equal(CU.windowForId("max").period, "max");
  assert.equal(CU.windowForId(" 1y ").window, "1y", "surrounding spaces are trimmed");
  assert.equal(CU.windowForId("nope"), null);
  assert.equal(CU.windowForId(null), null);
  assert.equal(CU.windowForId(""), null);
});

test("windowLabel keeps human labels for windows and legacy periods", () => {
  assert.equal(CU.windowLabel("1mo"), "1 month");
  assert.equal(CU.windowLabel("max"), "Maximum available");
  assert.equal(CU.windowLabel("5y"), "5 years", "legacy periods keep their label");
  assert.equal(CU.windowLabel("12x"), "12x", "unknown ids fall back to their raw label");
});

test("windowFromPeriod maps legacy provider periods to windows", () => {
  for (const id of ["1mo", "3mo", "6mo", "1y", "max"]) {
    const same = CU.windowFromPeriod(id);
    assert.equal(same.window, id);
    assert.equal(same.changed, false);
    assert.equal(same.message, "");
  }

  const twoYears = CU.windowFromPeriod("2y");
  assert.equal(twoYears.window, "max");
  assert.equal(twoYears.changed, true);
  assert.match(twoYears.message, /2 years/);
  assert.match(twoYears.message, /Maximum available/);

  assert.equal(CU.windowFromPeriod("5y").window, "max");
  assert.equal(CU.windowFromPeriod("10y").window, "max");
  assert.equal(CU.windowFromPeriod("ytd").window, "1y");
  assert.equal(CU.windowFromPeriod("1d").window, "1mo");
  assert.equal(CU.windowFromPeriod("5d").window, "1mo");
  assert.match(CU.windowFromPeriod("5d").message, /5 days/);
  assert.match(CU.windowFromPeriod("5d").message, /1 month/);

  const unknown = CU.windowFromPeriod("forever");
  assert.equal(unknown.window, "3mo");
  assert.equal(unknown.changed, true);
  assert.match(unknown.message, /forever/);
});

test("buildHistoryUrl sends the selected parameters", () => {
  const url = CU.buildHistoryUrl("http://localhost:8000/", {
    symbol: "RELIANCE",
    exchange: "NSE",
    period: "6mo",
    interval: "1d",
  });
  const parsed = new URL(url);
  assert.equal(parsed.origin, "http://localhost:8000");
  assert.equal(parsed.pathname, "/api/market/history");
  assert.equal(parsed.searchParams.get("symbol"), "RELIANCE");
  assert.equal(parsed.searchParams.get("exchange"), "NSE");
  assert.equal(parsed.searchParams.get("period"), "6mo");
  assert.equal(parsed.searchParams.get("interval"), "1d");
  assert.equal(parsed.searchParams.get("analyze"), "true");

  const withoutAnalysis = CU.buildHistoryUrl("http://localhost:8000", {
    symbol: "TCS",
    exchange: "BSE",
    period: "1mo",
    interval: "5m",
    analyze: false,
  });
  assert.equal(new URL(withoutAnalysis).searchParams.get("analyze"), "false");
});

test("buildHistoryUrl supports same-origin relative base for production", () => {
  const url = CU.buildHistoryUrl("", {
    symbol: "RELIANCE",
    exchange: "NSE",
    period: "6mo",
    interval: "1d",
  });
  assert.equal(url.startsWith("/api/market/history?"), true);
  const parsed = new URL(url, "https://cleandataflow.onrender.com");
  assert.equal(parsed.origin, "https://cleandataflow.onrender.com");
  assert.equal(parsed.pathname, "/api/market/history");
  assert.equal(parsed.searchParams.get("symbol"), "RELIANCE");
  assert.equal(parsed.searchParams.get("exchange"), "NSE");
  assert.equal(parsed.searchParams.get("period"), "6mo");
  assert.equal(parsed.searchParams.get("interval"), "1d");
  assert.equal(parsed.searchParams.get("analyze"), "true");
});

test("config.js resolves same-origin relative URLs in production and preserves local dev", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const vm = require("node:vm");

  const configPath = path.resolve(__dirname, "..", "config.js");
  const configCode = fs.readFileSync(configPath, "utf-8");

  // 1. Production on Render (e.g. cleandataflow.onrender.com)
  const renderSandbox = {
    window: {
      location: {
        protocol: "https:",
        hostname: "cleandataflow.onrender.com",
        port: "",
      },
    },
  };
  vm.runInNewContext(configCode, renderSandbox);
  assert.equal(renderSandbox.window.CDF_API_BASE, "");

  // 2. Unified local server (port 8000)
  const localUnifiedSandbox = {
    window: {
      location: {
        protocol: "http:",
        hostname: "localhost",
        port: "8000",
      },
    },
  };
  vm.runInNewContext(configCode, localUnifiedSandbox);
  assert.equal(localUnifiedSandbox.window.CDF_API_BASE, "");

  // 3. Standalone local frontend dev server (e.g. VS Code Live Server on 5500)
  const localSeparateSandbox = {
    window: {
      location: {
        protocol: "http:",
        hostname: "localhost",
        port: "5500",
      },
    },
  };
  vm.runInNewContext(configCode, localSeparateSandbox);
  assert.equal(localSeparateSandbox.window.CDF_API_BASE, "http://localhost:8000");

  // 4. File protocol
  const fileSandbox = {
    window: {
      location: {
        protocol: "file:",
        hostname: "",
        port: "",
      },
    },
  };
  vm.runInNewContext(configCode, fileSandbox);
  assert.equal(fileSandbox.window.CDF_API_BASE, "http://localhost:8000");
});

test("timestamp parsing accepts ISO strings, seconds and milliseconds", () => {
  const iso = "2026-10-18T18:30:00Z";
  assert.equal(CU.parseTimestamp(iso), Math.floor(Date.parse(iso) / 1000));
  assert.equal(CU.parseTimestamp("2026-10-18"), Math.floor(Date.parse("2026-10-18") / 1000));
  assert.equal(CU.parseTimestamp(1700000000), 1700000000);
  assert.equal(CU.parseTimestamp(1700000000000), 1700000000);
  assert.equal(CU.parseTimestamp("not-a-date"), null);
  assert.equal(CU.parseTimestamp(null), null);
});

test("finiteNumber and OHLC/OHLCV completeness are strict", () => {
  assert.equal(CU.finiteNumber("12.5"), 12.5);
  assert.equal(CU.finiteNumber(""), null);
  assert.equal(CU.finiteNumber("abc"), null);
  assert.equal(CU.finiteNumber(NaN), null);
  assert.equal(CU.finiteNumber(Infinity), null);

  const complete = {
    open: 10,
    high: 12,
    low: 9,
    close: 11,
    volume: 100,
  };
  assert.equal(CU.barHasCompleteOHLC(complete), true);
  assert.equal(CU.barHasCompleteOHLCV(complete), true);
  assert.equal(CU.barHasCompleteOHLC({ ...complete, high: 8 }), false);
  assert.equal(CU.barHasCompleteOHLC({ ...complete, close: null }), false);
  assert.equal(CU.barHasCompleteOHLCV({ ...complete, volume: null }), false);
  assert.equal(CU.barHasCompleteOHLCV({ ...complete, volume: -1 }), false);
  assert.equal(
    CU.barHasCompleteOHLCV({ ...complete, volume: 0 }),
    true
  );
});

test("mapBars sorts, skips invalid bars, deduplicates and preserves real volume", () => {
  const mapped = CU.mapBars(makeBars());

  assert.equal(mapped.total, 7);
  assert.equal(mapped.bars.length, 3);
  assert.equal(mapped.skipped.length, 4);
  assert.deepEqual(
    mapped.bars.map((bar) => bar.timestamp),
    [
      "2026-01-01T00:00:00Z",
      "2026-01-02T00:00:00Z",
      "2026-01-03T00:00:00Z",
    ]
  );

  const second = mapped.bars[1];
  assert.equal(second.close, 11.6, "last duplicate timestamp wins");
  assert.equal(second.volume, 950);

  const reasons = mapped.skipped.map((item) => item.reason).join(" | ");
  assert.match(reasons, /duplicate timestamp/);
  assert.match(reasons, /high is below low/);
  assert.match(reasons, /missing or invalid OHLC/);

  assert.equal(mapped.lastRaw.timestamp, "2026-01-05T00:00:00Z");
  assert.equal(mapped.lastRawHasOHLC, false);
  assert.equal(mapped.lastRawComplete, false);
});

test("mapBars keeps missing volume as null rather than zero", () => {
  const mapped = CU.mapBars([
    {
      timestamp: "2026-01-01T00:00:00Z",
      open: 10,
      high: 11,
      low: 9,
      close: 10.5,
      volume: null,
    },
    {
      timestamp: "2026-01-02T00:00:00Z",
      open: 10.5,
      high: 12,
      low: 10,
      close: 11.5,
      volume: -5,
    },
  ]);

  assert.equal(mapped.bars.length, 2);
  assert.equal(mapped.bars[0].volume, null);
  assert.equal(mapped.bars[1].volume, null);
});

test("mapBars returns an honest empty result for non-arrays", () => {
  const mapped = CU.mapBars(null);
  assert.equal(mapped.total, 0);
  assert.equal(mapped.bars.length, 0);
  assert.equal(mapped.lastRaw, null);
});

test("barAtTimestamp only finds returned bars", () => {
  const mapped = CU.mapBars(makeBars());
  assert.equal(
    CU.barAtTimestamp(mapped.bars, "2026-01-02T00:00:00Z").close,
    11.6
  );
  assert.equal(CU.barAtTimestamp(mapped.bars, "2026-01-09T00:00:00Z"), null);
  assert.equal(CU.barAtTimestamp(mapped.bars, "bad"), null);
});

function mappedBarsForMarkers() {
  return CU.mapBars([
    {
      timestamp: "2026-01-01T00:00:00Z",
      open: 10,
      high: 11,
      low: 9,
      close: 10.5,
      volume: 900,
    },
    {
      timestamp: "2026-01-02T00:00:00Z",
      open: 10.5,
      high: 12,
      low: 10,
      close: 11.5,
      volume: 950,
    },
    {
      timestamp: "2026-01-03T00:00:00Z",
      open: 11.5,
      high: 13,
      low: 11,
      close: 12.5,
      volume: 1000,
    },
  ]).bars;
}

test("buildAnomalyMarkers places legacy latest flags as red circles", () => {
  const bars = mappedBarsForMarkers();
  const latest = bars[bars.length - 1];
  const analysis = {
    lof_anomaly: {
      available: true,
      is_anomaly: true,
      evaluated_timestamp: latest.timestamp,
      lof_score: 2.5,
      threshold: 1.5,
      explanation: "LOF explanation",
    },
    autoencoder_anomaly: {
      available: true,
      is_anomaly: false,
      evaluated_timestamp: latest.timestamp,
      reconstruction_error: 0.2,
      threshold: 1.0,
      explanation: "AE explanation",
    },
    transformer_anomaly: {
      available: false,
      is_anomaly: null,
      evaluated_timestamp: latest.timestamp,
      anomaly_score: null,
      threshold: null,
      explanation: "Not enough history",
    },
  };

  const result = CU.buildAnomalyMarkers(analysis, bars);
  assert.equal(result.flaggedCount, 1);
  assert.equal(result.markers.length, 1);
  assert.deepEqual(result.markers[0], {
    time: latest.time,
    position: "aboveBar",
    color: "#ef4444",
    shape: "circle",
    text: "LOF",
    size: 1,
  });

  const lof = result.entries.find((entry) => entry.key === "lof_anomaly");
  const ae = result.entries.find((entry) => entry.key === "autoencoder_anomaly");
  const transformer = result.entries.find(
    (entry) => entry.key === "transformer_anomaly"
  );

  assert.equal(lof.status, "flagged");
  assert.equal(lof.price, 12.5);
  assert.equal(lof.evaluatedTimestamp, latest.timestamp);
  assert.equal(ae.status, "clear");
  assert.equal(transformer.status, "unavailable");
  assert.deepEqual(result.issues, []);
});

test("simultaneous latest flags cluster into one marker per candle", () => {
  const bars = mappedBarsForMarkers();
  const latest = bars[bars.length - 1];
  const block = (overrides) => ({
    available: true,
    is_anomaly: true,
    evaluated_timestamp: latest.timestamp,
    threshold: 1,
    explanation: "flagged",
    ...overrides,
  });

  const analysis = {
    lof_anomaly: block({ lof_score: 2 }),
    autoencoder_anomaly: block({ reconstruction_error: 3 }),
    transformer_anomaly: block({ anomaly_score: 4 }),
  };

  const result = CU.buildAnomalyMarkers(analysis, bars);
  assert.equal(result.flaggedCount, 1);
  assert.equal(result.markedCandles, 1);
  assert.equal(result.pointCount, 3);
  assert.equal(result.markers[0].text, "3");
  assert.equal(result.markers[0].shape, "circle");
  assert.equal(result.markers[0].color, "#ef4444");
  assert.equal(result.clusters[0].points.length, 3);
  const flagged = result.entries.filter((entry) => entry.status === "flagged");
  assert.deepEqual(
    flagged.map((entry) => entry.key).sort(),
    ["autoencoder_anomaly", "lof_anomaly", "transformer_anomaly"]
  );
  assert.ok(flagged.every((entry) => entry.price === 12.5));
});

test("historical points use returned timestamps and one cluster per candle", () => {
  const bars = mappedBarsForMarkers();
  const historical = {
    limitations: ["Historical points use only earlier bars."],
    detectors: {
      lof_anomaly: {
        available: true,
        historical_available: true,
        scope: "historical",
        method: "walk-forward LOF",
        points: [
          {
            timestamp: "2026-01-01T00:00:00Z",
            is_anomaly: true,
            score: 2.2,
            threshold: 1.4,
            score_label: "LOF score",
            explanation: "LOF historical point",
          },
        ],
      },
      volume_spike: {
        available: true,
        historical_available: true,
        scope: "historical",
        points: [
          {
            timestamp: "2026-01-02T00:00:00Z",
            is_anomaly: true,
            score: 2.8,
            threshold: 2,
            score_label: "Volume ratio",
            explanation: "Volume spike point",
          },
        ],
      },
      volume_price_divergence: {
        available: true,
        historical_available: true,
        scope: "historical",
        detected_bars: 4,
        points: [
          {
            timestamp: "2026-01-02T00:00:00Z",
            is_anomaly: true,
            score: null,
            threshold: null,
            signal: "bearish",
            explanation: "Divergence run start",
          },
        ],
      },
      autoencoder_anomaly: {
        available: false,
        historical_available: false,
        scope: "latest_only",
        points: [
          {
            timestamp: "2026-01-02T00:00:00Z",
            is_anomaly: true,
            score: 99,
          },
        ],
        reason: "Autoencoder remains latest-only.",
      },
    },
  };
  const analysis = {
    lof_anomaly: {
      available: true,
      is_anomaly: false,
      evaluated_timestamp: "2026-01-03T00:00:00Z",
    },
    autoencoder_anomaly: {
      available: true,
      is_anomaly: false,
      evaluated_timestamp: "2026-01-03T00:00:00Z",
      reconstruction_error: 0.1,
      threshold: 1,
    },
  };

  const result = CU.buildAnomalyMarkers(analysis, bars, historical);
  assert.equal(result.markedCandles, 2);
  assert.equal(result.pointCount, 3);
  assert.equal(result.markers[0].time, bars[0].time);
  assert.equal(result.markers[1].time, bars[1].time);
  assert.equal(result.markers[1].text, "2");
  assert.ok(result.markers.every((marker) => marker.shape === "circle"));
  assert.ok(result.markers.every((marker) => marker.color === "#ef4444"));

  const ae = result.entries.find((entry) => entry.key === "autoencoder_anomaly");
  assert.equal(ae.historicalPoints, 0);
  assert.equal(ae.latestOnly, true);
  assert.equal(ae.scope, "latest_only");
  assert.ok(result.latestOnlyKeys.includes("autoencoder_anomaly"));
  assert.ok(
    result.limitations.includes("Historical points use only earlier bars.")
  );

  const cluster = CU.markerClusterAtTime(result, bars[1].timestamp);
  assert.equal(cluster.points.length, 2);
  assert.deepEqual(
    cluster.points.map((point) => point.key).sort(),
    ["volume_price_divergence", "volume_spike"]
  );
  const divergence = cluster.points.find(
    (point) => point.key === "volume_price_divergence"
  );
  assert.equal(divergence.score, null);
  assert.equal(divergence.threshold, null);
  assert.equal(divergence.signal, "bearish");
});

test("duplicate historical timestamps are deduplicated per detector", () => {
  const bars = mappedBarsForMarkers();
  const historical = {
    detectors: {
      lof_anomaly: {
        available: true,
        historical_available: true,
        scope: "historical",
        points: [
          { timestamp: bars[0].timestamp, is_anomaly: true, score: 2 },
          { timestamp: bars[0].timestamp, is_anomaly: true, score: 3 },
        ],
      },
    },
  };
  const result = CU.buildAnomalyMarkers({}, bars, historical);
  assert.equal(result.markedCandles, 1);
  assert.equal(result.pointCount, 1);
  assert.equal(result.clusters[0].points[0].score, 2);
});

test("buildAnomalyMarkers never invents a position for an unmatched timestamp", () => {
  const bars = mappedBarsForMarkers();
  const historical = {
    detectors: {
      lof_anomaly: {
        available: true,
        historical_available: true,
        scope: "historical",
        points: [
          {
            timestamp: "2020-01-01T00:00:00Z",
            is_anomaly: true,
            score: 2.5,
            threshold: 1.5,
          },
        ],
      },
    },
  };
  const analysis = {
    lof_anomaly: {
      available: true,
      is_anomaly: false,
      evaluated_timestamp: bars[bars.length - 1].timestamp,
    },
  };

  const result = CU.buildAnomalyMarkers(analysis, bars, historical);
  assert.equal(result.flaggedCount, 0);
  assert.equal(result.markers.length, 0);
  assert.match(result.issues[0], /does not match a complete returned bar/);
  const lof = result.entries.find((entry) => entry.key === "lof_anomaly");
  assert.equal(lof.unplacedPoints, 1);
});

test("buildAnomalyMarkers rejects an available detector with no boolean flag", () => {
  const bars = mappedBarsForMarkers();
  const analysis = {
    lof_anomaly: {
      available: true,
      is_anomaly: null,
      evaluated_timestamp: bars[bars.length - 1].timestamp,
      lof_score: 2.5,
      threshold: 1.5,
    },
  };

  const result = CU.buildAnomalyMarkers(analysis, bars);
  assert.equal(result.markers.length, 0);
  const lof = result.entries.find((entry) => entry.key === "lof_anomaly");
  assert.equal(lof.status, "unavailable");
});

test("latest volume-spike markers infer the last real volume bar and threshold", () => {
  const bars = mappedBarsForMarkers();
  bars[bars.length - 1].volume = null;
  const analysis = {
    volume_spike: {
      available: true,
      signal: "spike",
      volume_ratio: 3.2,
      explanation: "latest volume is elevated",
    },
  };

  const result = CU.buildAnomalyMarkers(analysis, bars);
  assert.equal(result.markers.length, 1);
  assert.equal(result.markers[0].time, bars[1].time);
  assert.equal(result.markers[0].text, "VOL");
  const entry = result.entries.find((item) => item.key === "volume_spike");
  assert.equal(entry.threshold, 2);
  assert.equal(entry.score, 3.2);
});

test("dense historical markers suppress labels but keep every circle", () => {
  const bars = mappedBarsForMarkers();
  const historical = {
    detectors: {
      volume_spike: {
        available: true,
        historical_available: true,
        scope: "historical",
        points: bars.map((bar) => ({
          timestamp: bar.timestamp,
          is_anomaly: true,
          score: 3,
          threshold: 2,
        })),
      },
    },
  };
  const result = CU.buildAnomalyMarkers({}, bars, historical, {
    maximumLabels: 1,
  });
  assert.equal(result.markers.length, 3);
  assert.equal(result.labelsSuppressed, true);
  assert.ok(result.markers.every((marker) => marker.text === ""));
  assert.ok(result.markers.every((marker) => marker.shape === "circle"));
  assert.ok(result.markers.every((marker) => marker.size === 0.55));
});

test("buildAnomalyMarkers returns an honest message when analysis is missing", () => {
  const result = CU.buildAnomalyMarkers(null, []);
  assert.equal(result.markers.length, 0);
  assert.equal(result.entries.length, 0);
  assert.equal(result.issues.length, 1);
  assert.match(result.issues[0], /No analysis object/);
});

test("buildIndicatorLevels uses only returned SMA, EMA and Fibonacci values", () => {
  const analysis = {
    sma: { available: true, value: 100, period: 20 },
    ema_deviation: { available: true, ema: 101, period: 9 },
    fibonacci: {
      levels: {
        "0.236": 90,
        "0.618": 95,
      },
    },
  };

  const levels = CU.buildIndicatorLevels(analysis);
  assert.equal(levels.length, 4);
  const titles = levels.map((level) => level.title);
  assert.ok(titles.includes("SMA 20 (latest)"));
  assert.ok(titles.includes("EMA 9 (latest)"));
  assert.ok(titles.includes("Fib 23.6%"));
  assert.ok(titles.includes("Fib 61.8%"));
  assert.equal(
    levels.filter((level) => level.source === "fibonacci").every(
      (level) => level.axisLabelVisible === false
    ),
    true
  );
  assert.equal(
    levels.filter((level) => level.source !== "fibonacci").every(
      (level) => level.axisLabelVisible === true
    ),
    true
  );

  const unavailable = CU.buildIndicatorLevels({
    sma: { available: false, value: null, period: 20 },
    ema_deviation: { available: false, ema: null, period: 9 },
    fibonacci: { levels: null },
  });
  assert.deepEqual(unavailable, []);
});

test("RequestGuard marks older requests stale and aborts them", () => {
  const guard = new CU.RequestGuard();
  const first = guard.begin();
  assert.equal(first.isCurrent(), true);
  assert.equal(first.signal.aborted, false);

  const second = guard.begin();
  assert.equal(first.isCurrent(), false);
  assert.equal(first.signal.aborted, true);
  assert.equal(second.isCurrent(), true);
  assert.equal(guard.isCurrent(first.id), false);
  assert.equal(guard.isCurrent(second.id), true);

  guard.cancel();
  assert.equal(second.signal.aborted, true);
});

test("RequestGuard invalidate marks the active request stale and aborts it", () => {
  const guard = new CU.RequestGuard();
  const first = guard.begin();
  assert.equal(first.isCurrent(), true);
  guard.invalidate();
  assert.equal(first.isCurrent(), false);
  assert.equal(first.signal.aborted, true);
  assert.equal(guard.isCurrent(first.id), false);
});

test("formatters use real values and IST exchange time", () => {
  assert.notEqual(CU.formatPrice(1234.5), "—");
  assert.notEqual(CU.formatVolume(1500000), "—");
  assert.equal(CU.formatPrice(null), "—");
  assert.equal(CU.formatVolume(undefined), "—");

  const daily = CU.formatTime("2026-10-18T18:30:00Z", "1d");
  assert.match(daily, /19/);
  assert.match(daily, /2026/);

  const intraday = CU.formatTime("2026-10-19T04:15:00Z", "15m");
  assert.match(intraday, /19/);

  const tick = CU.formatTickMark(
    Math.floor(Date.parse("2026-10-18T18:30:00Z") / 1000),
    2,
    "1d"
  );
  assert.match(tick, /19/);

  const timeTick = CU.formatTickMark(
    Math.floor(Date.parse("2026-10-19T04:15:00Z") / 1000),
    3,
    "15m"
  );
  assert.match(timeTick, /^\d{2}:\d{2}$/);

  assert.notEqual(CU.formatDateTime("2026-10-19T04:15:00Z"), "—");
});
