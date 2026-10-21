// ---------- Stock Analysis page ----------
// Fetches real bars from GET /api/market/history?analyze=true and renders them
// with TradingView Lightweight Charts. No prices, candles or indicator values
// are generated on the client.

const API_ROOT =
  typeof API_BASE !== "undefined" ? API_BASE : "http://localhost:8000";
const CU = typeof window !== "undefined" ? window.ChartUtils : null;
const REQUEST_TIMEOUT_MS = 120000;
let timedOut = false;

// ---------- Initialise ----------
document.addEventListener('DOMContentLoaded', () => {
  // Initially show the search stage, hide the dashboard stage
  const searchStage = document.getElementById('searchStage');
  const dashboardStage = document.getElementById('dashboardStage');

  if (searchStage) searchStage.classList.remove('hidden');
  if (dashboardStage) dashboardStage.classList.add('hidden');

  if (!CU) {
    setStatus(
      "error",
      "Chart helpers failed to load. Check that chart-utils.js is present."
    );
    if (searchStage) searchStage.classList.add('hidden');
    return;
  }

  // If there are deep-link params, pre-fill the symbol but still require
  // the user to click "Stock Analysis" to begin.
  restoreFromUrl();

  wireEvents();
});

// ---------- Fetch and render ----------

// ---------- Symbol autocomplete suggestions ----------

let symbolSearchTimeout = null;

function showSymbolSuggestions(symbol, indices) {
  const panel = document.getElementById('symbolSuggestionsPanel');
  const input = document.getElementById('symbolInput');

  if (!panel || !input) return;

  if (!symbol || symbol.length < 1) {
    panel.style.display = 'none';
    panel.innerHTML = '';
    return;
  }

  const matches = indices.filter(idx =>
    idx.symbol.toUpperCase().includes(symbol.toUpperCase()) ||
    idx.name.toUpperCase().includes(symbol.toUpperCase())
  );

  if (matches.length === 0) {
    panel.style.display = 'none';
    panel.innerHTML = '<p class="text-muted small">No matching indices found.</p>';
    return;
  }

  panel.style.display = 'block';
  panel.innerHTML = matches.slice(0, 8).map(idx => `
    <div style="padding:6px 10px; border:1px solid var(--border-mute); border-radius:var(--radius-sm); background:var(--bg-input); cursor:pointer; transition:all var(--transition-fast);">
      <strong style="color:var(--accent-cyan);">${escapeHtml(idx.symbol)}</strong>
      <span style="color:var(--text-secondary); font-size:12px;">${escapeHtml(idx.name)}</span>
    </div>
  `).join('');

  // Add click handlers
  panel.querySelectorAll('div').forEach((div) => {
    div.addEventListener('click', () => {
      const symbol = div.querySelector('strong').textContent;
      input.value = symbol;
      panel.style.display = 'none';
      panel.innerHTML = '';
      if (els.form) els.form.requestSubmit();
    });
  });
}

function hideSymbolSuggestions() {
  const panel = document.getElementById('symbolSuggestionsPanel');
  if (panel) {
    panel.style.display = 'none';
    panel.innerHTML = '';
  }
}

// Debounced search for symbol suggestions
function loadSymbolSuggestions(symbol) {
  if (symbolSearchTimeout) {
    clearTimeout(symbolSearchTimeout);
  }
  symbolSearchTimeout = setTimeout(() => {
    fetch(`${API_BASE}/api/indices`)
      .then(res => {
        if (!res.ok) throw new Error('Backend returned ' + res.status);
        return res.json();
      })
      .then(data => {
        showSymbolSuggestions(symbol, data.indices || []);
      })
      .catch(err => {
        console.error(err);
        showSymbolSuggestions(symbol, []);
      });
  }, 300);
}

// ---------- Event wiring ----------

function wireEvents() {
  if (!els.form) return;

  // Auto-complete symbol suggestions on keystroke
  if (els.symbol) {
    els.symbol.addEventListener('input', (e) => {
      const symbol = e.target.value.trim();
      loadSymbolSuggestions(symbol);
    });

    // Hide suggestions when clicking outside
    document.addEventListener('click', (e) => {
      const symbolInput = els.symbol;
      const suggestionsPanel = document.getElementById('symbolSuggestionsPanel');
      if (suggestionsPanel && !symbolInput.contains(e.target) && !suggestionsPanel.contains(e.target)) {
        hideSymbolSuggestions();
      }
    });
  }

  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    hideSelectionNote();

    const selection = selectionFromControls();
    const check = CU.validateSymbol(selection.symbol);
    if (!check.valid) {
      cancelActiveRequest();
      clearResults();
      setStatus("error", check.message || "Enter a stock symbol first.");
      if (els.symbol) els.symbol.focus();
      return;
    }
    selection.symbol = check.symbol;
    if (els.symbol) els.symbol.value = check.symbol;

    // Hide suggestions on submit
    hideSymbolSuggestions();

    // Reconcile period/interval
    const reconciled = CU.reconcileSelection(
      selection.period,
      selection.interval,
      "period"
    );
    if (reconciled.changed) {
      selection.period = reconciled.period;
      selection.interval = reconciled.interval;
      if (els.period) els.period.value = reconciled.period;
      showSelectionNote(reconciled.message);
    }

    // Transition from search stage to dashboard stage
    const searchStage = document.getElementById('searchStage');
    const dashboardStage = document.getElementById('dashboardStage');
    if (searchStage) searchStage.classList.add('hidden');
    if (dashboardStage) dashboardStage.classList.remove('hidden');

    runAnalysis(selection);
  });

  if (els.period) {
    els.period.addEventListener("change", () => {
      const reconciled = CU.reconcileSelection(
        els.period.value,
        els.interval.value,
        "period"
      );
      if (reconciled.changed) {
        els.interval.value = reconciled.interval;
        showSelectionNote(reconciled.message);
      } else {
        hideSelectionNote();
      }
    });
  }

  if (els.interval) {
    els.interval.addEventListener("change", () => {
      const reconciled = CU.reconcileSelection(
        els.period.value,
        els.interval.value,
        "interval"
      );
      if (reconciled.changed) {
        els.period.value = reconciled.period;
        showSelectionNote(reconciled.message);
      } else {
        hideSelectionNote();
      }
    });
  }

  if (els.exchange) {
    els.exchange.addEventListener("change", () => {
      hideSelectionNote();
    });
  }

  if (els.modeCandles) {
    els.modeCandles.addEventListener("click", () => setMode("candles"));
  }
  if (els.modeLine) {
    els.modeLine.addEventListener("click", () => setMode("line"));
  }
  if (els.volumeToggle) {
    els.volumeToggle.addEventListener("change", () => {
      setVolumeVisible(els.volumeToggle.checked);
    });
  }
  if (els.levelsToggle) {
    els.levelsToggle.addEventListener("change", () => {
      state.showLevels = els.levelsToggle.checked;
      if (state.lastData) {
        applyLevels(state.lastData.analysis);
        renderChartNote(
          state.mapped,
          state.lastData,
          state.volumeSeries ? !els.volumeToggle.disabled : false
        );
      }
    });
  }
  if (els.resetZoom) {
    els.resetZoom.addEventListener("click", resetZoom);
  }
}

// ---------- Symbol autocomplete suggestions (inline) ----------

function showInlineSuggestions(symbol, indices) {
  const panel = document.getElementById('symbolSuggestionsPanel');
  const input = document.getElementById('symbolInput');

  if (!panel || !input) return;

  if (!symbol || symbol.length < 1) {
    panel.style.display = 'none';
    panel.innerHTML = '';
    return;
  }

  const matches = indices.filter(idx =>
    idx.symbol.toUpperCase().includes(symbol.toUpperCase()) ||
    idx.name.toUpperCase().includes(symbol.toUpperCase())
  );

  if (matches.length === 0) {
    panel.style.display = 'none';
    panel.innerHTML = '<p class="text-muted small">No matching indices found.</p>';
    return;
  }

  panel.style.display = 'block';
  panel.innerHTML = matches.slice(0, 8).map(idx => `
    <div style="padding:6px 10px; border:1px solid var(--border-mute); border-radius:var(--radius-sm); background:var(--bg-input); cursor:pointer; transition:all var(--transition-fast);">
      <strong style="color:var(--accent-cyan);">${escapeHtml(idx.symbol)}</strong>
      <span style="color:var(--text-secondary); font-size:12px;">${escapeHtml(idx.name)}</span>
    </div>
  `).join('');

  panel.querySelectorAll('div').forEach((div) => {
    div.addEventListener('click', () => {
      const symbol = div.querySelector('strong').textContent;
      input.value = symbol;
      panel.style.display = 'none';
      panel.innerHTML = '';
      if (els.form) els.form.requestSubmit();
    });
  });
}

// ---------- Status and panels ----------

function setStatus(kind, message) {
  if (!els.status) return;
  els.status.className = `analysis-status ${kind}`;
  els.status.textContent = message;
  els.status.classList.remove("hidden");
}

// ---------- Selection from controls ----------

function selectionFromControls() {
  return {
    symbol: els.symbol ? els.symbol.value.trim().toUpperCase() : "",
    exchange: els.exchange ? els.exchange.value : "NSE",
    period: els.period ? els.period.value : "3mo",
    interval: els.interval ? els.interval.value : "1d",
  };
}

// ---------- URL and deep links ----------

function restoreFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const symbol = params.get("symbol");
  const exchange = params.get("exchange");

  if (symbol) {
    els.symbol.value = symbol.toUpperCase();
    if (exchange) {
      els.exchange.value = exchange.toUpperCase();
    }
  }
}

// ---------- Status and panels ----------

function showSelectionNote(message) {
  if (!els.selectionNote || !message) return;
  els.selectionNote.textContent = message;
  els.selectionNote.classList.remove("hidden");
}

function hideSelectionNote() {
  if (!els.selectionNote) return;
  els.selectionNote.textContent = "";
  els.selectionNote.classList.add("hidden");
}

function hideResults() {
  ["metaPanel", "chartPanel", "indicatorPanel", "anomalyPanel"].forEach((id) => {
    const panel = document.getElementById(id);
    if (panel) panel.classList.add("hidden");
  });
}

function showResults() {
  ["metaPanel", "chartPanel", "indicatorPanel", "anomalyPanel"].forEach((id) => {
    const panel = document.getElementById(id);
    if (panel) panel.classList.remove("hidden");
  });
}

// ---------- Chart lifecycle ----------


function chartingAvailable() {
  return Boolean(
    typeof window !== "undefined" &&
      window.LightweightCharts &&
      typeof window.LightweightCharts.createChart === "function"
  );
}

function destroyChart() {
  if (state.resizeObserver) {
    try {
      state.resizeObserver.disconnect();
    } catch (error) {
      // Ignore a browser that removed the observer already.
    }
    state.resizeObserver = null;
  }

  state.priceLines = [];
  state.markers = [];
  state.markerResult = null;
  state.mapped = null;
  state.lastData = null;

  if (state.chart) {
    try {
      state.chart.remove();
    } catch (error) {
      // The chart may already have been removed by the library.
    }
    state.chart = null;
  }
  state.candleSeries = null;
  state.lineSeries = null;
  state.volumeSeries = null;

  if (els.priceChart) els.priceChart.innerHTML = "";
  if (els.chartLegend) {
    els.chartLegend.innerHTML =
      '<span class="legend-hint">Hover the chart for OHLCV details.</span>';
  }
  if (els.chartNote) els.chartNote.textContent = "";
  if (els.anomalyLegend) {
    els.anomalyLegend.innerHTML = "";
    els.anomalyLegend.classList.add("hidden");
  }
  if (els.volumeToggle) els.volumeToggle.disabled = false;
}

function clearResults() {
  destroyChart();
  if (els.metaList) els.metaList.innerHTML = "";
  if (els.metaBadge) {
    els.metaBadge.textContent = "";
    els.metaBadge.className = "meta-badge";
  }
  if (els.indicatorGrid) els.indicatorGrid.innerHTML = "";
  if (els.anomalyGrid) els.anomalyGrid.innerHTML = "";
  hideResults();
}

// Invalid input must also invalidate any request already in flight, otherwise
// its late response could repaint results for the discarded symbol.
function cancelActiveRequest() {
  if (state.guard) state.guard.invalidate();
  if (els.analyzeBtn) els.analyzeBtn.disabled = false;
}// ---------- Request ordering ----------
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


function formatCrosshairTime(time) {
  if (typeof time === "number") {
    try {
      return CU.formatTime(new Date(time * 1000).toISOString(), state.interval);
    } catch (error) {
      return String(time);
    }
  }
  if (CU) {
    const seconds = CU.parseTimestamp(time);
    if (seconds !== null) {
      return CU.formatTime(new Date(seconds * 1000).toISOString(), state.interval);
    }
  }
  return String(time);
}

function ensureChart() {
  if (state.chart) return state.chart;
  if (!chartingAvailable() || !els.priceChart) return null;

  const width = Math.max(320, els.priceChart.clientWidth || 800);
  const height = Math.max(280, els.priceChart.clientHeight || 460);
  const library = window.LightweightCharts;

  const chart = library.createChart(els.priceChart, {
    width,
    height,
    layout: {
      background: { type: library.ColorType.Solid, color: "#ffffff" },
      textColor: "#333333",
      fontFamily: "Arial, sans-serif",
    },
    grid: {
      vertLines: { color: "#f2f2f2" },
      horzLines: { color: "#f2f2f2" },
    },
    crosshair: {
      mode: library.CrosshairMode.Normal,
      vertLine: { color: "#90a4ae", width: 1, style: 3, labelBackgroundColor: "#37474f" },
      horzLine: { color: "#90a4ae", width: 1, style: 3, labelBackgroundColor: "#37474f" },
    },
    rightPriceScale: {
      borderColor: "#dddddd",
      scaleMargins: { top: 0.08, bottom: 0.28 },
    },
    timeScale: {
      borderColor: "#dddddd",
      timeVisible: CU ? CU.isIntraday(state.interval) : false,
      secondsVisible: false,
      rightOffset: 4,
      barSpacing: 8,
      minBarSpacing: 0.5,
      tickMarkFormatter: (time, tickMarkType) =>
        CU ? CU.formatTickMark(time, tickMarkType, state.interval) : String(time),
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
    localization: {
      priceFormatter: (price) => (CU ? CU.formatPrice(price, 2) : String(price)),
      timeFormatter: formatCrosshairTime,
    },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#089981",
    downColor: "#f23645",
    borderUpColor: "#089981",
    borderDownColor: "#f23645",
    wickUpColor: "#089981",
    wickDownColor: "#f23645",
    priceLineVisible: true,
    lastValueVisible: true,
  });

  const lineSeries = chart.addLineSeries({
    color: "#2962ff",
    lineWidth: 2,
    visible: false,
    priceLineVisible: true,
    lastValueVisible: true,
    crosshairMarkerVisible: true,
  });

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
    visible: true,
    priceLineVisible: false,
    lastValueVisible: false,
  });
  volumeSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.8, bottom: 0 },
  });

  chart.subscribeCrosshairMove(handleCrosshairMove);

  if (typeof ResizeObserver !== "undefined") {
    state.resizeObserver = new ResizeObserver(() => {
      if (!state.chart || !els.priceChart) return;
      const nextWidth = els.priceChart.clientWidth;
      const nextHeight = els.priceChart.clientHeight;
      if (nextWidth > 0) state.chart.applyOptions({ width: nextWidth });
      if (nextHeight > 0) state.chart.applyOptions({ height: nextHeight });
    });
    state.resizeObserver.observe(els.priceChart);
  }

  state.chart = chart;
  state.candleSeries = candleSeries;
  state.lineSeries = lineSeries;
  state.volumeSeries = volumeSeries;
  return chart;
}

function handleCrosshairMove(param) {
  if (!state.chart || !state.mapped) return;
  const latest = state.mapped.bars.length
    ? state.mapped.bars[state.mapped.bars.length - 1]
    : null;
  if (!param || !param.time || !param.point) {
    renderChartLegend(latest);
    return;
  }
  const bar = CU ? CU.barAtTimestamp(state.mapped.bars, param.time) : null;
  renderChartLegend(bar || latest);
}

function renderChartLegend(bar) {
  if (!els.chartLegend) return;
  if (!bar) {
    els.chartLegend.innerHTML =
      '<span class="legend-hint">Hover the chart for OHLCV details.</span>';
    return;
  }

  const isLatest =
    state.mapped &&
    state.mapped.bars.length > 0 &&
    state.mapped.bars[state.mapped.bars.length - 1].time === bar.time;
  const direction = bar.close >= bar.open ? "up" : "down";
  const volume = bar.volume === null ? "—" : fmtVolume(bar.volume);

  els.chartLegend.innerHTML = `
    <span class="legend-tag">${isLatest ? "Latest complete" : "Selected"}</span>
    <span class="legend-date">${fmtTime(bar.timestamp)}</span>
    <span class="legend-item"><span class="legend-label">O</span>${fmtValue(bar.open)}</span>
    <span class="legend-item"><span class="legend-label">H</span>${fmtValue(bar.high)}</span>
    <span class="legend-item"><span class="legend-label">L</span>${fmtValue(bar.low)}</span>
    <span class="legend-item ${direction}"><span class="legend-label">C</span>${fmtValue(bar.close)}</span>
    <span class="legend-item"><span class="legend-label">Vol</span>${escape(volume)}</span>
  `;
}

function removePriceLines() {
  state.priceLines.forEach(({ series, line }) => {
    try {
      series.removePriceLine(line);
    } catch (error) {
      // The series may already be gone during a teardown.
    }
  });
  state.priceLines = [];
}

function applyMarkers(markerResult) {
  if (!markerResult) return;
  state.markerResult = markerResult;
  state.markers = markerResult.markers;

  if (state.candleSeries) state.candleSeries.setMarkers([]);
  if (state.lineSeries) state.lineSeries.setMarkers([]);

  const active = state.mode === "line" ? state.lineSeries : state.candleSeries;
  if (active) active.setMarkers(markerResult.markers);
}

function applyLevels(analysis) {
  removePriceLines();
  if (!state.chart || !state.showLevels || !CU) return;

  const levels = CU.buildIndicatorLevels(analysis);
  const active = state.mode === "line" ? state.lineSeries : state.candleSeries;
  if (!active) return;

  levels.forEach((level) => {
    const line = active.createPriceLine({
      price: level.price,
      color: level.color,
      lineWidth: 1,
      lineStyle: level.lineStyle,
      axisLabelVisible: true,
      title: level.title,
    });
    state.priceLines.push({ series: active, line, level });
  });
}

function setMode(mode) {
  state.mode = mode === "line" ? "line" : "candles";
  const candles = state.mode === "candles";

  if (state.candleSeries) {
    state.candleSeries.applyOptions({ visible: candles });
  }
  if (state.lineSeries) {
    state.lineSeries.applyOptions({ visible: !candles });
  }

  if (els.modeCandles) {
    els.modeCandles.classList.toggle("active", candles);
    els.modeCandles.setAttribute("aria-pressed", String(candles));
  }
  if (els.modeLine) {
    els.modeLine.classList.toggle("active", !candles);
    els.modeLine.setAttribute("aria-pressed", String(!candles));
  }

  if (state.markerResult) applyMarkers(state.markerResult);
  if (state.lastData) applyLevels(state.lastData.analysis);
}

function setVolumeVisible(visible) {
  state.showVolume =
    Boolean(visible) && Boolean(els.volumeToggle) && !els.volumeToggle.disabled;

  if (state.volumeSeries) {
    state.volumeSeries.applyOptions({ visible: state.showVolume });
  }
  if (state.chart) {
    state.chart.priceScale("right").applyOptions({
      scaleMargins: {
        top: 0.08,
        bottom: state.showVolume ? 0.28 : 0.08,
      },
    });
  }
}

function resetZoom() {
  if (state.chart) state.chart.timeScale().fitContent();
}

// ---------- Chart note and anomaly legend ----------

function renderChartNote(mapped, data, hasVolume) {
  if (!els.chartNote || !CU) return;
  const notes = [];
  const omitted = mapped.total - mapped.bars.length;

  if (mapped.total === 0) {
    notes.push("The backend returned no bars.");
  }
  if (omitted > 0) {
    notes.push(
      `${omitted} returned bar(s) were not drawn because their OHLC values ` +
        `were missing, invalid or duplicated.`
    );
  }
  if (mapped.lastRaw && !mapped.lastRawHasOHLC) {
    notes.push(
      "The latest returned bar is incomplete (missing or invalid OHLC) and is " +
        "not drawn. Anomaly detectors evaluate the latest complete bar."
    );
  } else if (mapped.lastRaw && !mapped.lastRawComplete) {
    notes.push(
      "The latest returned bar has no usable volume, so it is drawn as a " +
        "candle but anomaly detectors evaluate the latest complete OHLCV bar."
    );
  }

  const analysis = data.analysis || {};
  const excluded = ["lof_anomaly", "autoencoder_anomaly", "transformer_anomaly"]
    .map((key) => {
      const block = analysis[key];
      const value = block ? CU.finiteNumber(block.excluded_trailing_bars) : null;
      return value === null ? 0 : value;
    })
    .reduce((max, value) => Math.max(max, value), 0);

  if (excluded > 0) {
    notes.push(
      `The anomaly detectors excluded ${excluded} trailing incomplete bar(s) ` +
        `and scored the latest complete bar.`
    );
  }
  if (!hasVolume) {
    notes.push(
      "No usable volume data was returned, so the volume histogram is hidden."
    );
  }

  const levels = CU ? CU.buildIndicatorLevels(analysis) : [];
  if (state.showLevels && levels.length) {
    notes.push(
      "Dashed lines are the latest SMA/EMA values; dotted lines are " +
        "Fibonacci levels derived from the returned range. No historical " +
        "indicator series was returned."
    );
  }
  if (!notes.length) {
    notes.push(
      "All returned bars had usable OHLC values. Crosshair details use the " +
        "same returned bars."
    );
  }

  els.chartNote.textContent = notes.join(" ");
}

function anomalyStatusLabel(status) {
  if (status === "flagged") return "Flagged";
  if (status === "clear") return "No anomaly";
  if (status === "unplaced") return "Flagged, not placed";
  return "Unavailable";
}

function anomalyDetail(entry, interval) {
  if (entry.status === "flagged") {
    const parts = [];
    if (entry.score !== null) {
      parts.push(`${entry.scoreLabel} ${fmtValue(entry.score, 4)}`);
    }
    if (entry.threshold !== null) {
      parts.push(`threshold ${fmtValue(entry.threshold, 4)}`);
    }
    if (entry.price !== null) {
      parts.push(`close ${fmtValue(entry.price)}`);
    }
    if (entry.barTimestamp) {
      parts.push(fmtTime(entry.barTimestamp, interval));
    }
    return parts.join(" · ");
  }
  if (entry.status === "clear") {
    return (
      `${entry.scoreLabel} ${fmtValue(entry.score, 4)} vs threshold ` +
      `${fmtValue(entry.threshold, 4)} · no flag`
    );
  }
  if (entry.status === "unavailable") {
    return "Unavailable";
  }
  return "Flagged but not placed on a returned bar";
}

function renderAnomalyLegend(result, interval) {
  if (!els.anomalyLegend) return;
  if (!result || !Array.isArray(result.entries) || !result.entries.length) {
    els.anomalyLegend.innerHTML = "";
    els.anomalyLegend.classList.add("hidden");
    return;
  }

  const entries = result.entries;
  const flaggedCount = entries.filter((entry) => entry.status === "flagged").length;
  const unavailableCount = entries.filter(
    (entry) => entry.status === "unavailable"
  ).length;

  const head = `
    <div class="anomaly-legend-head">
      <strong>Latest complete bar checks</strong>
      <span>${flaggedCount} of ${entries.length} flagged${
        unavailableCount ? ` · ${unavailableCount} unavailable` : ""
      }</span>
    </div>
  `;

  const items = entries
    .map((entry) => {
      const detail = anomalyDetail(entry, interval);
      const reason = entry.reason || "No explanation was returned.";
      return `
        <li class="anomaly-legend-item ${entry.status}" title="${escapeAttr(reason)}">
          <span class="anomaly-swatch" style="background:${entry.color}">${escape(entry.short)}</span>
          <span class="anomaly-legend-name">${escape(entry.name)}</span>
          <span class="anomaly-legend-status">${escape(anomalyStatusLabel(entry.status))}</span>
          <span class="anomaly-legend-detail">${escape(detail)}</span>
        </li>
      `;
    })
    .join("");

  const warnings = (result.issues || [])
    .map((issue) => `<p class="anomaly-warning">${escape(issue)}</p>`)
    .join("");

  els.anomalyLegend.innerHTML = `
    ${head}
    <ul class="anomaly-legend-list">${items}</ul>
    ${warnings}
    <p class="anomaly-legend-note">
      Markers are placed on the evaluated bar; the legend shows that bar's close
      price. Historical anomaly timelines are not shown because the backend
      returns only latest-bar scores. A statistical anomaly is not proof of
      manipulation.
    </p>
  `;
  els.anomalyLegend.classList.remove("hidden");
}

// ---------- Chart rendering ----------

function renderChart(data, mapped) {
  destroyChart();
  state.interval = data.interval || state.interval;
  state.mapped = mapped;
  state.lastData = data;

  const markerResult = CU
    ? CU.buildAnomalyMarkers(data.analysis, mapped.bars)
    : null;
  state.markerResult = markerResult;

  if (!chartingAvailable()) {
    if (els.priceChart) {
      els.priceChart.innerHTML =
        '<div class="chart-empty">The local Lightweight Charts bundle could ' +
        "not be loaded, so no price chart was drawn. Check that " +
        "<code>vendor/lightweight-charts.standalone.production.js</code> is present.</div>";
    }
    renderChartNote(mapped, data, false);
    renderAnomalyLegend(markerResult, state.interval);
    return;
  }

  if (!mapped.bars.length) {
    if (els.priceChart) {
      els.priceChart.innerHTML =
        '<div class="chart-empty">No complete OHLC bars were returned, so no ' +
        "candlestick or volume chart was drawn. Try a longer period or a " +
        "different symbol.</div>";
    }
    renderChartNote(mapped, data, false);
    renderAnomalyLegend(markerResult, state.interval);
    return;
  }

  const chart = ensureChart();
  if (!chart) {
    if (els.priceChart) {
      els.priceChart.innerHTML =
        '<div class="chart-empty">The chart container could not be initialised.</div>';
    }
    renderChartNote(mapped, data, false);
    renderAnomalyLegend(markerResult, state.interval);
    return;
  }

  chart.applyOptions({
    timeScale: {
      timeVisible: CU.isIntraday(state.interval),
      secondsVisible: false,
    },
  });

  state.candleSeries.setData(
    mapped.bars.map((bar) => ({
      time: bar.time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }))
  );
  state.lineSeries.setData(
    mapped.bars.map((bar) => ({ time: bar.time, value: bar.close }))
  );

  const volumeData = mapped.bars
    .filter((bar) => bar.volume !== null)
    .map((bar) => ({
      time: bar.time,
      value: bar.volume,
      color:
        bar.close >= bar.open
          ? "rgba(8, 153, 129, 0.45)"
          : "rgba(242, 54, 69, 0.45)",
    }));
  state.volumeSeries.setData(volumeData);

  const hasVolume = volumeData.length > 0;
  if (els.volumeToggle) els.volumeToggle.disabled = !hasVolume;
  state.showVolume =
    hasVolume && Boolean(els.volumeToggle && els.volumeToggle.checked);
  state.volumeSeries.applyOptions({ visible: state.showVolume });
  chart.priceScale("right").applyOptions({
    scaleMargins: {
      top: 0.08,
      bottom: state.showVolume ? 0.28 : 0.08,
    },
  });

  applyMarkers(markerResult);
  applyLevels(data.analysis);
  chart.timeScale().fitContent();
  renderChartLegend(mapped.bars[mapped.bars.length - 1]);
  renderChartNote(mapped, data, hasVolume);
  renderAnomalyLegend(markerResult, state.interval);
}

// ---------- Data source panel ----------

function renderMeta(data, mapped, selection) {
  if (!els.metaList) return;
  const latest = mapped.bars.length
    ? mapped.bars[mapped.bars.length - 1]
    : null;
  const omitted = mapped.total - mapped.bars.length;
  const analysis = data.analysis || {};
  const excluded = ["lof_anomaly", "autoencoder_anomaly", "transformer_anomaly"]
    .map((key) => {
      const block = analysis[key];
      const value = block ? CU.finiteNumber(block.excluded_trailing_bars) : null;
      return value === null ? 0 : value;
    })
    .reduce((max, value) => Math.max(max, value), 0);

  const rows = [
    ["Symbol", `${data.symbol || "—"}${data.providerSymbol ? ` (${data.providerSymbol})` : ""}`],
    ["Exchange", data.exchange || selection.exchange],
    ["Requested period", CU.periodLabel(selection.period)],
    ["Requested interval", CU.intervalLabel(selection.interval)],
    ["Bars returned", String(mapped.total)],
    ["Complete bars drawn", String(mapped.bars.length)],
    ["Bars not drawn", String(omitted)],
    ["Latest complete close", latest ? fmtValue(latest.close) : "—"],
    ["Latest complete bar", latest ? fmtTime(latest.timestamp) : "—"],
    ["Last raw bar timestamp", data.lastDataTimestamp ? fmtTime(data.lastDataTimestamp) : "—"],
    ["Anomaly trailing bars excluded", String(excluded)],
    ["Source", data.source || "—"],
    ["Fetched at", fmtDateTime(data.fetchedAt)],
  ];

  els.metaList.innerHTML = rows
    .map(
      ([label, value]) =>
        `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`
    )
    .join("");

  if (els.metaBadge) {
    if (mapped.total === 0) {
      els.metaBadge.textContent = "No bars";
      els.metaBadge.className = "meta-badge warn";
    } else if (omitted > 0) {
      els.metaBadge.textContent = `${omitted} bar(s) not drawn`;
      els.metaBadge.className = "meta-badge warn";
    } else {
      els.metaBadge.textContent = "All returned bars drawn";
      els.metaBadge.className = "meta-badge ok";
    }
  }
}

// ---------- Analysis cards ----------

function badge(textValue, className) {
  return `<span class="badge ${className || ""}">${escape(textValue)}</span>`;
}

function card(title, status, bodyHtml, badgeText, badgeClass) {
  const classes = ["analysis-card"];
  if (status === "unavailable") classes.push("is-unavailable");
  if (status === "flagged") classes.push("is-flagged");

  const fallbackBadge =
    status === "unavailable" ? "Unavailable" : "Available";
  const fallbackClass =
    status === "unavailable"
      ? "unavailable"
      : status === "flagged"
        ? "flagged"
        : "ok";

  return `<article class="${classes.join(" ")}">
    <h3><span>${escape(title)}</span>${badge(
      badgeText || fallbackBadge,
      badgeClass || fallbackClass
    )}</h3>
    ${bodyHtml}
  </article>`;
}

function row(label, valueHtml) {
  return `<div class="row"><span>${escape(label)}</span><span>${valueHtml}</span></div>`;
}

function featureList(featuresUsed) {
  if (!Array.isArray(featuresUsed) || !featuresUsed.length) return "";
  return `<p class="feature-list"><strong>Features:</strong> ${featuresUsed
    .map((feature) => escape(feature))
    .join(", ")}</p>`;
}

function featureTable(features, caption) {
  if (!features || typeof features !== "object") return "";
  const entries = Object.entries(features).filter(
    ([, value]) => CU.finiteNumber(value) !== null
  );
  if (!entries.length) return "";

  const rows = entries
    .map(
      ([name, value]) =>
        `<tr><td>${escape(name)}</td><td>${fmtValue(value, 4)}</td></tr>`
    )
    .join("");

  return `<table class="feature-table">
    <caption>${escape(caption || "Latest feature values")}</caption>
    <thead><tr><th>Feature</th><th>Value</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function smaCard(sma, rawBarCount, completeBarCount) {
  if (!sma || sma.available !== true || CU.finiteNumber(sma.value) === null) {
    const period = sma && sma.period ? sma.period : 20;
    return card(
      "SMA",
      "unavailable",
      `<p class="explain">The backend marked SMA unavailable. ${completeBarCount} complete bar(s) were drawn from ${rawBarCount} returned bar(s); this SMA needs ${period} usable closes. Try a longer period or a different symbol.</p>
       ${methodText(sma && sma.method)}`,
      "Unavailable",
      "unavailable"
    );
  }

  return card(
    "SMA",
    "ok",
    `<p class="metric">${fmtValue(sma.value)}</p>
     ${row("Period", escape(sma.period))}
     <p class="metric-note">Latest SMA value. The chart shows it as a dashed price line, not a historical series.</p>
     ${methodText(sma.method)}`,
    "Available",
    "ok"
  );
}

function rsiCard(rsi, rawBarCount, completeBarCount) {
  if (!rsi || rsi.available !== true || CU.finiteNumber(rsi.value) === null) {
    const period = rsi && rsi.period ? rsi.period : 14;
    return card(
      "RSI",
      "unavailable",
      `<p class="explain">The backend marked RSI unavailable. ${completeBarCount} complete bar(s) were drawn from ${rawBarCount} returned bar(s); RSI needs more than ${period} usable closes. Try a longer period.</p>
       ${methodText(rsi && rsi.method)}`,
      "Unavailable",
      "unavailable"
    );
  }

  const value = Number(rsi.value);
  let zone = "Between 30 and 70 — neutral zone";
  if (value >= 70) zone = "Above 70 — often read as overbought";
  else if (value <= 30) zone = "Below 30 — often read as oversold";

  return card(
    "RSI",
    "ok",
    `<p class="metric">${fmtValue(value)}</p>
     <p class="metric-note">${escape(zone)}</p>
     ${row("Period", escape(rsi.period))}
     ${methodText(rsi.method)}`,
    "Available",
    "ok"
  );
}

function fibonacciCard(fibonacci) {
  const levels =
    fibonacci && fibonacci.levels && typeof fibonacci.levels === "object"
      ? Object.keys(fibonacci.levels)
      : [];

  if (!levels.length) {
    return card(
      "Fibonacci retracement",
      "unavailable",
      `<p class="explain">The backend returned no Fibonacci levels, usually because the returned high/low range is empty or flat. No levels were invented.</p>
       ${methodText(fibonacci && fibonacci.method)}`,
      "Unavailable",
      "unavailable"
    );
  }

  const levelRows = levels
    .map((ratio) => {
      const percent = Number(ratio) * 100;
      const label = Number.isFinite(percent)
        ? `${percent.toFixed(1)}%`
        : String(ratio);
      return `<tr><td>${escape(label)}</td><td>${fmtValue(fibonacci.levels[ratio])}</td></tr>`;
    })
    .join("");

  return card(
    "Fibonacci retracement",
    "ok",
    `${row("Range low", fmtValue(fibonacci.rangeLow))}
     ${row("Range high", fmtValue(fibonacci.rangeHigh))}
     <table class="fib-table">
       <thead><tr><th>Retracement</th><th>Price</th></tr></thead>
       <tbody>${levelRows}</tbody>
     </table>
     <p class="metric-note">Levels come from the returned high/low range and are shown as dotted price lines.</p>
     ${methodText(fibonacci.method)}`,
    "Available",
    "ok"
  );
}

function emaDeviationCard(ema) {
  if (
    !ema ||
    ema.available !== true ||
    CU.finiteNumber(ema.deviation_percent) === null
  ) {
    return card(
      "EMA deviation",
      "unavailable",
      `<p class="explain">The backend marked EMA deviation unavailable, typically because there are not enough usable closes for the EMA period.</p>
       ${methodText(ema && ema.method)}`,
      "Unavailable",
      "unavailable"
    );
  }

  const deviation = Number(ema.deviation_percent);
  const cssClass = deviation > 0 ? "positive" : deviation < 0 ? "negative" : "";
  const direction = deviation > 0 ? "above" : deviation < 0 ? "below" : "at";

  return card(
    "EMA deviation",
    "ok",
    `<p class="metric ${cssClass}">${fmtSignedValue(deviation)}%</p>
     <p class="metric-note">Latest close is ${fmtValue(Math.abs(deviation))}% ${direction} the EMA. The chart shows the latest EMA as a dashed price line.</p>
     ${row("Latest close", fmtValue(ema.latest_close))}
     ${row("EMA", fmtValue(ema.ema))}
     ${row("Period", escape(ema.period))}
     ${methodText(ema.method)}`,
    "Available",
    "ok"
  );
}

function divergenceCard(divergence) {
  if (!divergence || divergence.available !== true) {
    return card(
      "Volume–price divergence",
      "unavailable",
      `<p class="explain">${
        divergence && divergence.explanation
          ? escape(divergence.explanation)
          : "The backend could not compute a price/volume divergence for this lookback."
      }</p>`,
      "Unavailable",
      "unavailable"
    );
  }

  return card(
    "Volume–price divergence",
    "ok",
    `<p class="metric signal">${escape(divergence.signal)}</p>
     ${row("Price change", `${fmtSignedValue(divergence.price_change_percent)}%`)}
     ${row("Volume change", `${fmtSignedValue(divergence.volume_change_percent)}%`)}
     ${explainText(divergence.explanation)}`,
    "Available",
    "ok"
  );
}

function volumeSpikeCard(spike) {
  if (!spike || spike.available !== true) {
    return card(
      "Volume spike",
      "unavailable",
      `<p class="explain">${
        spike && spike.explanation
          ? escape(spike.explanation)
          : "The backend could not compare the latest volume with a baseline."
      }</p>`,
      "Unavailable",
      "unavailable"
    );
  }

  return card(
    "Volume spike",
    "ok",
    `<p class="metric">${fmtValue(spike.volume_ratio)}×</p>
     ${row("Latest volume", fmtVolume(spike.latest_volume))}
     ${row("Baseline average", fmtVolume(spike.baseline_average))}
     ${row("Above baseline", `${fmtSignedValue(spike.percent_above_baseline)}%`)}
     ${row("Signal", escape(spike.signal))}
     ${explainText(spike.explanation)}`,
    "Available",
    "ok"
  );
}

function volatilityAnomalyCard(anomaly) {
  if (!anomaly || anomaly.available !== true) {
    return card(
      "Volatility anomaly",
      "unavailable",
      `<p class="explain">${
        anomaly && anomaly.explanation
          ? escape(anomaly.explanation)
          : "The backend could not compare recent volatility with its baseline."
      }</p>`,
      "Unavailable",
      "unavailable"
    );
  }

  return card(
    "Volatility anomaly",
    "ok",
    `<p class="metric">${fmtValue(anomaly.volatility_ratio)}×</p>
     ${row("Recent ATR", fmtValue(anomaly.recent_atr))}
     ${row("Baseline ATR", fmtValue(anomaly.baseline_atr))}
     ${row("Change vs baseline", `${fmtSignedValue(anomaly.percent_change)}%`)}
     ${row("Signal", escape(anomaly.signal))}
     ${explainText(anomaly.explanation)}
     <p class="method">Unusually large price ranges are volatility, not proof of manipulation.</p>`,
    "Available",
    "ok"
  );
}

function anomalyCard(config, block) {
  if (!block || typeof block !== "object") {
    return card(
      config.name,
      "unavailable",
      `<p class="explain">No ${escape(config.name)} block was returned by the backend.</p>`,
      "Unavailable",
      "unavailable"
    );
  }

  const available = block.available === true;
  const flagged = available && block.is_anomaly === true;

  if (available && typeof block.is_anomaly !== "boolean") {
    return card(
      config.name,
      "unavailable",
      `<p class="explain">The backend marked this detector available but did not return a boolean is_anomaly value, so no signal was inferred.</p>
       ${methodText(block.method)}`,
      "Unavailable",
      "unavailable"
    );
  }

  if (!available) {
    const reason =
      block.explanation ||
      "The backend marked this detector unavailable for the returned data.";
    const details = [
      row(
        "Evaluated bar",
        block.evaluated_timestamp ? fmtTime(block.evaluated_timestamp) : "—"
      ),
      row(
        "History observations",
        block.history_observations === null ||
          block.history_observations === undefined
          ? "—"
          : String(block.history_observations)
      ),
      row(
        "Excluded trailing bars",
        block.excluded_trailing_bars === null ||
          block.excluded_trailing_bars === undefined
          ? "—"
          : String(block.excluded_trailing_bars)
      ),
    ].join("");

    return card(
      config.name,
      "unavailable",
      `<p class="explain">${escape(reason)}</p>
       ${details}
       ${methodText(block.method)}`,
      "Unavailable",
      "unavailable"
    );
  }

  const score = CU.finiteNumber(block[config.scoreField]);
  const threshold = CU.finiteNumber(block[config.thresholdField]);
  const details = [];

  if (block.decision_value !== null && block.decision_value !== undefined) {
    details.push(row("Decision value", fmtValue(block.decision_value, 4)));
  }
  if (block.n_neighbors !== null && block.n_neighbors !== undefined) {
    details.push(row("Neighbours", String(block.n_neighbors)));
  }
  if (block.contamination !== null && block.contamination !== undefined) {
    details.push(row("Contamination", fmtValue(block.contamination, 4)));
  }
  if (block.hidden_layers) {
    details.push(
      row(
        "Hidden layers",
        Array.isArray(block.hidden_layers)
          ? escape(block.hidden_layers.join(" → "))
          : escape(block.hidden_layers)
      )
    );
  }

  details.push(
    row(
      "History observations",
      block.history_observations === null ||
        block.history_observations === undefined
        ? "—"
        : String(block.history_observations)
    )
  );
  details.push(
    row(
      "Excluded trailing bars",
      block.excluded_trailing_bars === null ||
        block.excluded_trailing_bars === undefined
        ? "—"
        : String(block.excluded_trailing_bars)
    )
  );
  details.push(
    row(
      "Evaluated bar",
      block.evaluated_timestamp ? fmtTime(block.evaluated_timestamp) : "—"
    )
  );

  const body = `
    <p class="metric ${flagged ? "flagged" : "clear"}>
      ${fmtValue(score, 4)}
      <span class="metric-unit">${escape(config.scoreLabel)}</span>
    </p>
    <p class="metric-note">
      ${
        flagged
          ? "Above the anomaly threshold. This bar is a statistical outlier."
          : "At or below the anomaly threshold. No anomaly was flagged for this bar."
      }
      Threshold: ${fmtValue(threshold, 4)}.
    </p>
    <div class="anomaly-meta">${details.join("")}</div>
    ${featureList(block.features_used)}
    ${featureTable(block.latest_features, "Latest feature values")}
    ${featureTable(block.latest_prediction, "Predicted feature values")}
    ${explainText(block.explanation)}
    ${methodText(block.method)}
    <p class="method">Scores only the latest complete bar. A statistical anomaly is not proof of manipulation.</p>
  `;

  return card(
    config.name,
    flagged ? "flagged" : "ok",
    body,
    flagged ? "Anomaly flagged" : "No anomaly",
    flagged ? "flagged" : "clear"
  );
}

function noticeCard(title, message) {
  return `<article class="analysis-card is-unavailable">
    <h3><span>${escape(title)}</span>${badge("Notice", "unavailable")}</h3>
    <p class="explain">${escape(message)}</p>
  </article>`;
}

function renderAnalysis(analysis, rawBarCount, completeBarCount) {
  const blocks = analysis && typeof analysis === "object" ? analysis : null;
  const notices = [];

  if (!blocks) {
    notices.push(
      noticeCard(
        "No analysis object",
        "The backend response did not include analysis results. No indicator values were invented."
      )
    );
  }
  if (completeBarCount === 0) {
    notices.push(
      noticeCard(
        "No complete bars",
        `${rawBarCount} bar(s) were returned, but none had complete OHLC values. Indicators and anomaly detectors cannot be computed honestly.`
      )
    );
  }

  const indicatorCards = [
    smaCard(blocks && blocks.sma, rawBarCount, completeBarCount),
    rsiCard(blocks && blocks.rsi, rawBarCount, completeBarCount),
    fibonacciCard(blocks && blocks.fibonacci),
    emaDeviationCard(blocks && blocks.ema_deviation),
    divergenceCard(blocks && blocks.volume_price_divergence),
    volumeSpikeCard(blocks && blocks.volume_spike),
    volatilityAnomalyCard(blocks && blocks.volatility_anomaly),
  ];
  if (els.indicatorGrid) {
    els.indicatorGrid.innerHTML = notices.join("") + indicatorCards.join("");
  }

  const anomalyCards = CU
    ? CU.DETECTOR_CONFIG.map((config) =>
        anomalyCard(config, blocks && blocks[config.key])
      )
    : [];
  if (els.anomalyGrid) {
    els.anomalyGrid.innerHTML = notices.join("") + anomalyCards.join("");
  }
}

// ---------- Result rendering ----------

function renderResult(data, selection, mapped) {
  state.interval = data.interval || selection.interval;
  renderMeta(data, mapped, selection);
  renderAnalysis(data.analysis, data.bars.length, mapped.bars.length);
  showResults();
  renderChart(data, mapped);
}

// ---------- URL and selection ----------

function syncUrl(selection) {
  try {
    const params = new URLSearchParams({
      symbol: selection.symbol,
      exchange: selection.exchange,
      period: selection.period,
      interval: selection.interval,
    });
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}?${params.toString()}`
    );
  } catch (error) {
    // file:// pages and some embedded browsers disallow URL updates.
  }
}

function applySelection(selection) {
  if (!selection) return;
  if (els.symbol && selection.symbol !== undefined) {
    els.symbol.value = selection.symbol;
  }
  if (els.exchange && selection.exchange) {
    els.exchange.value = selection.exchange;
  }
  if (els.period && selection.period) {
    els.period.value = selection.period;
  }
  if (els.interval && selection.interval) {
    els.interval.value = selection.interval;
  }
}

function selectionFromControls() {
  return {
    symbol: els.symbol.value.trim().toUpperCase(),
    exchange: els.exchange.value,
    period: els.period.value,
    interval: els.interval.value,
  };
}

// ---------- Deep links ----------

function restoreFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const symbol = params.get("symbol");
  const exchange = params.get("exchange");

  if (symbol) {
    els.symbol.value = symbol.toUpperCase();
    if (exchange) {
      els.exchange.value = exchange.toUpperCase();
    }
  }
}