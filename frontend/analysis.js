/*
 * Stock Analyser page.
 *
 * Fetches real bars from GET /api/market/history and renders them with the
 * vendored TradingView Lightweight Charts build. No price, candle, indicator
 * or anomaly value is generated on the client.
 *
 * The search stage exposes the symbol input (with autocomplete), exchange,
 * the primary "Analyse Stock" action and a secondary Reset. One Historical
 * Window select inside the chart header controls both the chart data and the
 * analysis data from the same fetch; a RequestGuard prevents stale responses
 * from overwriting newer selections. Results are revealed only after a
 * successful backend response; the pre-analysis panel contains no prices or
 * scores.
 */

const API_ROOT =
  typeof API_BASE !== "undefined" ? API_BASE : "";
const CU = typeof window !== "undefined" ? window.ChartUtils : null;
const REQUEST_TIMEOUT_MS = 120000;
let timedOut = false;

/* ==========================================================================
   DOM
   ========================================================================== */

const els = {
  form: document.getElementById("analysisForm"),
  symbol: document.getElementById("symbolInput"),
  suggestions: document.getElementById("symbolSuggestionsPanel"),
  exchange: document.getElementById("exchangeSelect"),
  windowSelect: document.getElementById("windowSelect"),
  windowStatus: document.getElementById("windowStatus"),
  analyzeBtn: document.getElementById("analyzeBtn"),
  analyzeLabel: document.getElementById("analyzeBtnLabel"),
  resetBtn: document.getElementById("resetBtn"),
  status: document.getElementById("analysisStatus"),
  selectionNote: document.getElementById("selectionNote"),
  searchStage: document.getElementById("searchStage"),
  preAnalysis: document.getElementById("preAnalysis"),
  dashboardStage: document.getElementById("dashboardStage"),
  resultSummary: document.getElementById("resultSummary"),
  resultTitle: document.getElementById("resultTitle"),
  resultSub: document.getElementById("resultSub"),
  resultLast: document.getElementById("resultLast"),
  resultLastDate: document.getElementById("resultLastDate"),
  resultChange: document.getElementById("resultChange"),
  resultWindow: document.getElementById("resultWindow"),
  resultSource: document.getElementById("resultSource"),
  resultFetched: document.getElementById("resultFetched"),
  resultFreshness: document.getElementById("resultFreshness"),
  metaPanel: document.getElementById("metaPanel"),
  metaList: document.getElementById("metaList"),
  metaBadge: document.getElementById("metaBadge"),
  chartPanel: document.getElementById("chartPanel"),
  chartLegend: document.getElementById("chartLegend"),
  chartCanvasWrap: document.getElementById("chartCanvasWrap"),
  priceChart: document.getElementById("priceChart"),
  markerTooltip: document.getElementById("anomalyMarkerTooltip"),
  chartNote: document.getElementById("chartNote"),
  anomalyLegend: document.getElementById("anomalyLegend"),
  indicatorPanel: document.getElementById("indicatorPanel"),
  indicatorGrid: document.getElementById("indicatorGrid"),
  anomalyPanel: document.getElementById("anomalyPanel"),
  anomalyGrid: document.getElementById("anomalyGrid"),
  modeCandles: document.getElementById("modeCandles"),
  modeLine: document.getElementById("modeLine"),
  volumeToggle: document.getElementById("volumeToggle"),
  levelsToggle: document.getElementById("levelsToggle"),
  resetZoom: document.getElementById("resetZoom"),
};

const state = {
  guard: CU ? new CU.RequestGuard() : null,
  chart: null,
  candleSeries: null,
  lineSeries: null,
  volumeSeries: null,
  resizeObserver: null,
  priceLines: [],
  markers: [],
  markerResult: null,
  mapped: null,
  lastData: null,
  selection: null,
  loadedSelection: null,
  interval: "1d",
  mode: "candles",
  showLevels: true,
  showVolume: true,
  loading: false,
  activeSuggestion: -1,
  hoveredAnomalyTime: null,
  pinnedAnomalyTime: null,
  markerPointer: null,
  refreshKeepsResults: false,
  activeRequestKey: null,
};

/* ==========================================================================
   Formatting and escaping helpers
   ========================================================================== */

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}

function escapeAttr(value) {
  return escapeHtml(value)
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtValue(value, digits) {
  if (!CU) return "—";
  return CU.formatPrice(value, digits == null ? 2 : digits);
}

function fmtVolume(value) {
  if (!CU) return "—";
  return CU.formatVolume(value, true);
}

function fmtTime(iso, interval) {
  if (!CU) return "—";
  return CU.formatTime(iso, interval || state.interval);
}

function fmtDateTime(iso) {
  if (!CU) return "—";
  return CU.formatDateTime(iso);
}

function fmtSignedValue(value, digits = 2) {
  const number = CU ? CU.finiteNumber(value) : null;
  if (number === null) return "—";
  const sign = number > 0 ? "+" : "";
  return `${sign}${fmtValue(number, digits)}`;
}

function methodText(method) {
  if (!method || typeof method !== "string") return "";
  return `<p class="method"><strong>Method:</strong> ${escapeHtml(method)}</p>`;
}

function explainText(explanation) {
  if (!explanation || typeof explanation !== "string") return "";
  return `<p class="explain">${escapeHtml(explanation)}</p>`;
}

/* ==========================================================================
   Status, notes and busy state
   ========================================================================== */

function setStatus(kind, message, retryAction) {
  if (!els.status) return;

  els.status.innerHTML = "";

  const text = document.createElement("span");
  text.className = "status-text";
  text.textContent = message;
  els.status.appendChild(text);

  if (typeof retryAction === "function") {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "retry-btn";
    retry.textContent = "Retry";
    retry.addEventListener("click", retryAction);
    els.status.appendChild(retry);
  }

  els.status.className = `analysis-status ${kind}`;
  els.status.classList.remove("hidden");
}

function hideStatus() {
  if (!els.status) return;
  els.status.className = "analysis-status hidden";
  els.status.innerHTML = "";
}

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

function setBusy(isBusy) {
  state.loading = Boolean(isBusy);
  if (els.analyzeBtn) {
    els.analyzeBtn.disabled = Boolean(isBusy);
    els.analyzeBtn.setAttribute("aria-busy", String(Boolean(isBusy)));
  }
  if (els.analyzeLabel) {
    els.analyzeLabel.textContent = isBusy ? "Analysing…" : "Analyse Stock";
  }
  if (els.form) els.form.setAttribute("aria-busy", String(Boolean(isBusy)));
  if (els.dashboardStage) {
    els.dashboardStage.setAttribute("aria-busy", String(Boolean(isBusy)));
  }
  if (els.windowSelect) {
    els.windowSelect.disabled = Boolean(isBusy);
  }
}

function setSymbolInvalid(invalid) {
  if (!els.symbol) return;
  if (invalid) {
    els.symbol.setAttribute("aria-invalid", "true");
  } else {
    els.symbol.removeAttribute("aria-invalid");
  }
}

function setWindowStatus(kind, message) {
  if (!els.windowStatus) return;
  els.windowStatus.className = "window-status";
  if (!message) {
    els.windowStatus.textContent = "";
    els.windowStatus.classList.add("hidden");
    return;
  }
  els.windowStatus.textContent = message;
  els.windowStatus.classList.add(kind);
  els.windowStatus.classList.remove("hidden");
}

function setChartRefreshing(refreshing) {
  if (els.chartCanvasWrap) {
    els.chartCanvasWrap.classList.toggle(
      "is-loading",
      Boolean(refreshing)
    );
  }
}

/* ==========================================================================
   Selection and URL
   ========================================================================== */

function selectionFromControls() {
  const windowEntry =
    CU.windowForId(els.windowSelect ? els.windowSelect.value : "") ||
    CU.windowForId("3mo");
  return {
    symbol: els.symbol ? els.symbol.value.trim().toUpperCase() : "",
    exchange: els.exchange ? els.exchange.value : "NSE",
    window: windowEntry.window,
    period: windowEntry.period,
    interval: windowEntry.interval,
  };
}

function applySelection(selection) {
  if (!selection) return;
  if (els.symbol && selection.symbol !== undefined) {
    els.symbol.value = selection.symbol;
  }
  if (els.exchange && selection.exchange) els.exchange.value = selection.exchange;
  if (els.windowSelect) {
    const windowId =
      selection.window || (CU.windowFromPeriod(selection.period) || {}).window || "3mo";
    els.windowSelect.value = windowId;
  }
}

function syncUrl(selection) {
  try {
    const params = new URLSearchParams({
      symbol: selection.symbol,
      exchange: selection.exchange,
      window:
        selection.window || (CU.windowFromPeriod(selection.period) || {}).window || "3mo",
    });
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}?${params.toString()}`
    );
  } catch (error) {
    /* file:// and embedded browsers may reject history updates. */
  }
}

function restoreFromUrl() {
  if (!CU) return null;
  const params = new URLSearchParams(window.location.search);
  const selection = CU.selectionFromParams(params, {
    exchange: "NSE",
    window: "3mo",
  });

  applySelection(selection);

  if (selection.symbol) {
    if (selection.notes && selection.notes.length) {
      showSelectionNote(selection.notes.join(" "));
    }
    setStatus(
      "info",
      `Loaded ${selection.symbol} from the link. Select Analyse Stock to run it.`
    );
  }
  return selection;
}

function resetSearch() {
  cancelActiveRequest();
  clearResults();
  hideStatus();
  hideSelectionNote();
  closeSuggestions();

  if (els.symbol) {
    els.symbol.value = "";
    setSymbolInvalid(false);
  }
  if (els.exchange) els.exchange.value = "NSE";
  if (els.windowSelect) els.windowSelect.value = "3mo";

  try {
    window.history.replaceState(null, "", window.location.pathname);
  } catch (error) {
    /* file:// and embedded browsers may reject history updates. */
  }

  if (els.symbol) els.symbol.focus();
}

function autorunSelection(restored) {
  if (!restored || !restored.symbol) return;
  const params = new URLSearchParams(window.location.search);
  if (params.get("autorun") !== "1") return;

  const check = CU.validateSymbol(restored.symbol);
  if (!check.valid) {
    setSymbolInvalid(true);
    setStatus("error", check.message || "The linked symbol is not valid.");
    return;
  }

  // restoreFromUrl already reconciled the link into a valid daily window.
  const selection = {
    symbol: check.symbol,
    exchange: restored.exchange,
    window: restored.window || "3mo",
    period: restored.period,
    interval: restored.interval,
  };
  if (restored.notes && restored.notes.length) {
    showSelectionNote(restored.notes.join(" "));
    applySelection(selection);
  }
  syncUrl(selection);
  runAnalysis(selection);
}

/* ==========================================================================
   Autocomplete — real NIFTY 50 constituents, manual entry always allowed
   ========================================================================== */

const stockUniverse = {
  items: null,
  promise: null,
};

let suggestionTimer = null;

function ensureStockUniverse() {
  if (stockUniverse.items) return Promise.resolve(stockUniverse.items);
  if (stockUniverse.promise) return stockUniverse.promise;

  const url = `${API_ROOT}/api/indices/${encodeURIComponent(
    "NIFTY 50"
  )}/constituents`;

  stockUniverse.promise = fetch(url, { headers: { Accept: "application/json" } })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((payload) => {
      const list = Array.isArray(payload && payload.constituents)
        ? payload.constituents
        : [];
      const items = list
        .map((item) => ({
          symbol: String(item.symbol || "").trim().toUpperCase(),
          name: String(item.name || "").trim(),
        }))
        .filter((item) => /^[A-Z0-9.^-]{1,30}$/.test(item.symbol));
      stockUniverse.items = items;
      return items;
    })
    .catch((error) => {
      console.warn("Autocomplete universe unavailable:", error);
      stockUniverse.items = [];
      return [];
    })
    .finally(() => {
      stockUniverse.promise = null;
    });

  return stockUniverse.promise;
}

function closeSuggestions() {
  state.activeSuggestion = -1;
  if (suggestionTimer !== null) {
    window.clearTimeout(suggestionTimer);
    suggestionTimer = null;
  }
  if (!els.suggestions) return;
  els.suggestions.innerHTML = "";
  els.suggestions.style.display = "none";
  if (els.symbol) els.symbol.setAttribute("aria-expanded", "false");
}

function suggestionMessage(message) {
  if (!els.suggestions) return;
  els.suggestions.innerHTML = `<p class="suggestion-empty">${escapeHtml(
    message
  )}</p>`;
  els.suggestions.style.display = "block";
  if (els.symbol) els.symbol.setAttribute("aria-expanded", "true");
}

function renderSuggestionMatches(value) {
  if (!els.suggestions) return;

  const query = value.trim().toUpperCase();
  if (!query) {
    closeSuggestions();
    return;
  }

  const items = stockUniverse.items || [];
  if (!items.length) {
    suggestionMessage(
      "Autocomplete is unavailable right now. Type the exact NSE symbol (for example RELIANCE) and select Analyse Stock."
    );
    return;
  }

  const starts = [];
  const contains = [];
  items.forEach((item) => {
    const symbol = String(item.symbol || "").toUpperCase();
    const displayName =
      typeof displayConstituentName === "function"
        ? displayConstituentName(item)
        : item.name;
    const name = `${item.name || ""} ${displayName || ""}`.toUpperCase();
    if (symbol === query || symbol.startsWith(query)) starts.push(item);
    else if (name.includes(query) || symbol.includes(query)) contains.push(item);
  });

  const matches = [...starts, ...contains].slice(0, 8);
  if (!matches.length) {
    suggestionMessage(
      "No NIFTY 50 match. You can still type an exact NSE/BSE symbol and run the analysis."
    );
    return;
  }

  state.activeSuggestion = -1;
  els.suggestions.innerHTML = matches
    .map((item, index) => {
      const displayName =
        typeof displayConstituentName === "function"
          ? displayConstituentName(item)
          : item.name;
      const nameHtml =
        displayName && displayName !== "NSE" && displayName !== "NSE equity"
          ? `<span class=\"suggestion-name\">${escapeHtml(displayName)}</span>`
          : "";
      return `
        <button type=\"button\" class=\"suggestion-item\" role=\"option\"
          data-symbol=\"${escapeAttr(item.symbol)}\" data-index=\"${index}\"
          aria-selected=\"false\">
          <span class=\"suggestion-symbol\">${escapeHtml(item.symbol)}</span>
          ${nameHtml}
        </button>
      `;
    })
    .join("");

  els.suggestions.style.display = "block";
  if (els.symbol) els.symbol.setAttribute("aria-expanded", "true");

  els.suggestions.querySelectorAll(".suggestion-item").forEach((button) => {
    button.addEventListener("click", () => {
      selectSuggestion(button.getAttribute("data-symbol"));
    });
  });
}

function selectSuggestion(symbol) {
  if (!symbol || !els.symbol) return;
  els.symbol.value = symbol;
  closeSuggestions();
  hideSelectionNote();
  if (els.analyzeBtn) els.analyzeBtn.focus();
}

function updateSuggestions(value) {
  if (stockUniverse.items) {
    renderSuggestionMatches(value);
    return;
  }

  suggestionMessage("Loading real NIFTY 50 stock names…");
  ensureStockUniverse().then(() => {
    if (!els.symbol || els.symbol.value.trim() !== value) return;
    renderSuggestionMatches(value);
  });
}

function handleSymbolInput() {
  const value = els.symbol ? els.symbol.value : "";
  window.clearTimeout(suggestionTimer);

  if (!value.trim()) {
    closeSuggestions();
    return;
  }

  suggestionTimer = window.setTimeout(() => updateSuggestions(value), 220);
}

function moveSuggestion(direction) {
  if (!els.suggestions) return;
  const items = Array.from(
    els.suggestions.querySelectorAll(".suggestion-item")
  );
  if (!items.length) return;

  state.activeSuggestion += direction;
  if (state.activeSuggestion < 0) state.activeSuggestion = items.length - 1;
  if (state.activeSuggestion >= items.length) state.activeSuggestion = 0;

  items.forEach((item, index) => {
    item.setAttribute(
      "aria-selected",
      String(index === state.activeSuggestion)
    );
    if (index === state.activeSuggestion) {
      item.scrollIntoView({ block: "nearest" });
    }
  });
}

/* ==========================================================================
   Chart lifecycle
   ========================================================================== */

function chartingAvailable() {
  return Boolean(
    window.LightweightCharts &&
      typeof window.LightweightCharts.createChart === "function"
  );
}

function chartPalette() {
  const light = document.documentElement.dataset.theme === "light";
  return light
    ? {
        background: "#ffffff",
        text: "#334155",
        grid: "rgba(15, 23, 42, 0.08)",
        border: "rgba(15, 23, 42, 0.18)",
        crosshair: "#475569",
        crosshairLabel: "#334155",
        volumeUp: "rgba(21, 128, 61, 0.40)",
        volumeDown: "rgba(185, 28, 28, 0.38)",
      }
    : {
        background: "#0b1422",
        text: "#cbd5e1",
        grid: "rgba(148, 163, 184, 0.12)",
        border: "rgba(148, 163, 184, 0.28)",
        crosshair: "#94a3b8",
        crosshairLabel: "#1e293b",
        volumeUp: "rgba(34, 197, 94, 0.42)",
        volumeDown: "rgba(248, 113, 113, 0.40)",
      };
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
      return CU.formatTime(
        new Date(seconds * 1000).toISOString(),
        state.interval
      );
    }
  }
  return String(time);
}

function ensureChart() {
  if (state.chart) return state.chart;
  if (!chartingAvailable() || !els.priceChart) return null;

  const width = Math.max(320, els.priceChart.clientWidth || 800);
  const height = Math.max(280, els.priceChart.clientHeight || 420);
  const library = window.LightweightCharts;
  const palette = chartPalette();

  const chart = library.createChart(els.priceChart, {
    width,
    height,
    layout: {
      background: { type: library.ColorType.Solid, color: palette.background },
      textColor: palette.text,
      fontFamily: "Inter, Segoe UI, sans-serif",
      fontSize: 12,
    },
    grid: {
      vertLines: { color: palette.grid },
      horzLines: { color: palette.grid },
    },
    crosshair: {
      mode: library.CrosshairMode.Normal,
      vertLine: {
        color: palette.crosshair,
        width: 1,
        style: 3,
        labelBackgroundColor: palette.crosshairLabel,
      },
      horzLine: {
        color: palette.crosshair,
        width: 1,
        style: 3,
        labelBackgroundColor: palette.crosshairLabel,
      },
    },
    rightPriceScale: {
      borderColor: palette.border,
      scaleMargins: { top: 0.13, bottom: 0.30 },
    },
    timeScale: {
      borderColor: palette.border,
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
      priceFormatter: (price) =>
        CU ? CU.formatPrice(price, 2) : String(price),
      timeFormatter: formatCrosshairTime,
    },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#22c55e",
    downColor: "#f87171",
    borderUpColor: "#22c55e",
    borderDownColor: "#f87171",
    wickUpColor: "#22c55e",
    wickDownColor: "#f87171",
    priceLineVisible: true,
    lastValueVisible: true,
  });

  const lineSeries = chart.addLineSeries({
    color: "#38bdf8",
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
  chart.subscribeClick(handleChartClick);

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

function destroyChart() {
  if (state.resizeObserver) {
    try {
      state.resizeObserver.disconnect();
    } catch (error) {
      /* already disconnected */
    }
    state.resizeObserver = null;
  }

  state.priceLines = [];
  state.markers = [];
  state.markerResult = null;
  state.mapped = null;
  state.lastData = null;
  state.hoveredAnomalyTime = null;
  state.pinnedAnomalyTime = null;
  state.markerPointer = null;
  hideMarkerTooltip();

  if (state.chart) {
    try {
      state.chart.remove();
    } catch (error) {
      /* library already removed it */
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

function updateChartTheme() {
  if (!state.chart || !window.LightweightCharts) return;
  const library = window.LightweightCharts;
  const palette = chartPalette();

  state.chart.applyOptions({
    layout: {
      background: { type: library.ColorType.Solid, color: palette.background },
      textColor: palette.text,
    },
    grid: {
      vertLines: { color: palette.grid },
      horzLines: { color: palette.grid },
    },
    crosshair: {
      vertLine: {
        color: palette.crosshair,
        labelBackgroundColor: palette.crosshairLabel,
      },
      horzLine: {
        color: palette.crosshair,
        labelBackgroundColor: palette.crosshairLabel,
      },
    },
    rightPriceScale: { borderColor: palette.border },
    timeScale: { borderColor: palette.border },
  });
}

const MARKER_VALUE_LABELS = {
  latest_volume: "Volume",
  baseline_average: "Baseline average",
  percent_above_baseline: "Above baseline",
  recent_atr: "Recent ATR",
  baseline_atr: "Baseline ATR",
  percent_change: "Change vs baseline",
  price_change_percent: "Price change",
  volume_change_percent: "Volume change",
  price_return_pct: "Price return",
  volume_change_pct: "Volume change",
  true_range_pct: "True Range",
};

function markerValueHtml(key, value) {
  const number = CU ? CU.finiteNumber(value) : null;
  if (number === null) return "";
  let formatted;
  if (key === "latest_volume" || key === "baseline_average") {
    formatted = fmtVolume(number);
  } else if (key.includes("percent")) {
    formatted = `${fmtSignedValue(number)}%`;
  } else {
    formatted = fmtValue(number, 4);
  }
  const label = MARKER_VALUE_LABELS[key] || key.replace(/_/g, " ");
  return `<span class="marker-value"><span>${escapeHtml(
    label
  )}</span><strong>${escapeHtml(formatted)}</strong></span>`;
}

function markerTooltipHtml(cluster) {
  if (!cluster || !Array.isArray(cluster.points) || !cluster.points.length) {
    return "";
  }

  const points = cluster.points
    .map((point) => {
      const metrics = [];
      if (point.score !== null) {
        metrics.push(
          `<span><span>${escapeHtml(
            point.scoreLabel || "Score"
          )}</span><strong>${escapeHtml(fmtValue(point.score, 4))}</strong></span>`
        );
      }
      if (point.threshold !== null) {
        metrics.push(
          `<span><span>Threshold</span><strong>${escapeHtml(
            fmtValue(point.threshold, 4)
          )}</strong></span>`
        );
      }
      if (point.signal) {
        metrics.push(
          `<span><span>Signal</span><strong>${escapeHtml(
            point.signal
          )}</strong></span>`
        );
      }
      const values = Object.entries(point.values || {})
        .map(([key, value]) => markerValueHtml(key, value))
        .join("");
      const sourceLabel =
        point.source === "latest" ? "Latest returned bar" : "Historical";
      return `<section class="marker-detail">
        <header>
          <span class="marker-dot" aria-hidden="true"></span>
          <strong>${escapeHtml(point.detectorName)}</strong>
          <span class="marker-scope-pill ${point.source}">${sourceLabel}</span>
        </header>
        ${
          metrics.length
            ? `<div class="marker-metrics">${metrics.join("")}</div>`
            : ""
        }
        ${values ? `<div class="marker-values">${values}</div>` : ""}
        ${
          point.explanation
            ? `<p class="marker-explanation">${escapeHtml(
                point.explanation
              )}</p>`
            : ""
        }
      </section>`;
    })
    .join("");

  return `
    <div class="marker-tooltip-head">
      <span class="marker-kicker">Anomaly marker</span>
      <strong>${escapeHtml(fmtTime(cluster.barTimestamp))}</strong>
      <span>Close ${escapeHtml(fmtValue(cluster.price))}</span>
    </div>
    <div class="marker-tooltip-list">${points}</div>
    <p class="marker-tooltip-note">
      ${cluster.points.length > 1
        ? `${cluster.points.length} detectors flagged this candle. `
        : ""}Click the candle to pin these details; click again to release.
    </p>
  `;
}

function positionMarkerTooltip(cluster, point) {
  if (!els.markerTooltip || !els.chartCanvasWrap) return;
  const width = els.chartCanvasWrap.clientWidth;
  const height = els.chartCanvasWrap.clientHeight;
  const chartHeight =
    els.priceChart && els.priceChart.clientHeight
      ? els.priceChart.clientHeight
      : height;
  const tooltipWidth = Math.min(380, Math.max(240, width - 24));
  els.markerTooltip.style.width = `${tooltipWidth}px`;
  els.markerTooltip.style.maxHeight = `${Math.max(
    160,
    Math.min(height, chartHeight) - 24
  )}px`;

  let left = (point && Number.isFinite(point.x) ? point.x : width - 40) + 16;
  if (left + tooltipWidth > width - 8) {
    left = (point ? point.x : width) - tooltipWidth - 16;
  }
  left = Math.max(8, Math.min(left, Math.max(8, width - tooltipWidth - 8)));

  let top = (point && Number.isFinite(point.y) ? point.y : 40) - 16;
  const tooltipHeight = els.markerTooltip.offsetHeight || 0;
  top = Math.max(8, Math.min(top, Math.max(8, height - tooltipHeight - 8)));
  els.markerTooltip.style.left = `${left}px`;
  els.markerTooltip.style.top = `${top}px`;
}

function showMarkerTooltip(cluster, point) {
  if (!els.markerTooltip || !cluster) return;
  els.markerTooltip.innerHTML = markerTooltipHtml(cluster);
  els.markerTooltip.classList.remove("hidden");
  els.markerTooltip.classList.toggle(
    "pinned",
    state.pinnedAnomalyTime === cluster.time
  );
  positionMarkerTooltip(cluster, point);
}

function hideMarkerTooltip() {
  if (!els.markerTooltip) return;
  els.markerTooltip.classList.add("hidden");
  els.markerTooltip.innerHTML = "";
  els.markerTooltip.classList.remove("pinned");
}

function handleCrosshairMove(param) {
  if (!state.chart || !state.mapped) return;
  const latest = state.mapped.bars.length
    ? state.mapped.bars[state.mapped.bars.length - 1]
    : null;

  if (!param || !param.time || !param.point) {
    renderChartLegend(latest);
    if (state.pinnedAnomalyTime !== null) {
      const pinned = CU
        ? CU.markerClusterAtTime(state.markerResult, state.pinnedAnomalyTime)
        : null;
      if (pinned) showMarkerTooltip(pinned, state.markerPointer);
    } else {
      hideMarkerTooltip();
    }
    return;
  }

  const bar = CU ? CU.barAtTimestamp(state.mapped.bars, param.time) : null;
  renderChartLegend(bar || latest);

  const cluster = CU
    ? CU.markerClusterAtTime(state.markerResult, param.time)
    : null;
  if (cluster) {
    state.hoveredAnomalyTime = cluster.time;
    showMarkerTooltip(cluster, param.point);
  } else if (state.pinnedAnomalyTime !== null) {
    const pinned = CU
      ? CU.markerClusterAtTime(state.markerResult, state.pinnedAnomalyTime)
      : null;
    if (pinned) showMarkerTooltip(pinned, state.markerPointer);
    else hideMarkerTooltip();
  } else {
    state.hoveredAnomalyTime = null;
    hideMarkerTooltip();
  }
}

function handleChartClick(param) {
  if (!CU || !param || param.time === undefined || param.time === null) return;
  const cluster = CU.markerClusterAtTime(state.markerResult, param.time);
  if (!cluster) {
    state.pinnedAnomalyTime = null;
    state.markerPointer = null;
    hideMarkerTooltip();
    return;
  }

  if (state.pinnedAnomalyTime === cluster.time) {
    state.pinnedAnomalyTime = null;
    state.markerPointer = null;
    hideMarkerTooltip();
    return;
  }

  state.pinnedAnomalyTime = cluster.time;
  state.markerPointer = param.point || null;
  showMarkerTooltip(cluster, param.point);
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
    <span class="legend-tag">${isLatest ? "Latest returned" : "Selected"}</span>
    <span class="legend-date">${escapeHtml(fmtTime(bar.timestamp))}</span>
    <span class="legend-item"><span class="legend-label">O</span>${fmtValue(bar.open)}</span>
    <span class="legend-item"><span class="legend-label">H</span>${fmtValue(bar.high)}</span>
    <span class="legend-item"><span class="legend-label">L</span>${fmtValue(bar.low)}</span>
    <span class="legend-item ${direction}"><span class="legend-label">C</span>${fmtValue(bar.close)}</span>
    <span class="legend-item"><span class="legend-label">Vol</span>${escapeHtml(volume)}</span>
  `;
}

function removePriceLines() {
  state.priceLines.forEach(({ series, line }) => {
    try {
      series.removePriceLine(line);
    } catch (error) {
      /* series may already be gone */
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
      axisLabelVisible: level.axisLabelVisible !== false,
      title: level.title,
    });
    state.priceLines.push({ series: active, line, level });
  });
}

function setMode(mode) {
  state.mode = mode === "line" ? "line" : "candles";
  const candles = state.mode === "candles";

  if (state.candleSeries) state.candleSeries.applyOptions({ visible: candles });
  if (state.lineSeries) state.lineSeries.applyOptions({ visible: !candles });

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
        top: 0.13,
        bottom: state.showVolume ? 0.30 : 0.10,
      },
    });
  }
}

function resetZoom() {
  if (state.chart) state.chart.timeScale().fitContent();
}

/* ==========================================================================
   Chart note and anomaly legend
   ========================================================================== */

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
        "not drawn. Anomaly detectors evaluate the latest bar with usable OHLCV."
    );
  } else if (mapped.lastRaw && !mapped.lastRawComplete) {
    notes.push(
      "The latest returned bar has no usable volume, so it is drawn as a " +
        "candle but anomaly detectors evaluate the latest bar with usable OHLCV."
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
        `and scored the latest usable bar.`
    );
  }
  if (!hasVolume) {
    notes.push(
      "No usable volume data was returned, so the volume histogram is hidden."
    );
  }

  const levels = CU.buildIndicatorLevels(analysis);
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

function anomalyStatusLabel(entry) {
  if (entry.status === "flagged") {
    return entry.scope === "latest_only" ? "Latest flag" : "Marked";
  }
  if (entry.status === "clear") return "No flag";
  if (entry.status === "unplaced") return "Not placeable";
  return "Unavailable";
}

function anomalyScopeLabel(entry) {
  if (entry.historicalAvailable) return "Historical + latest";
  if (entry.latestOnly || !entry.hasHistoricalEnvelope) return "Latest bar only";
  return "Historical unavailable";
}

function anomalyDetail(entry, interval) {
  const parts = [];

  if (entry.scope === "latest_only") {
    parts.push("latest usable bar only");
  } else if (entry.historicalAvailable) {
    const count = Number(entry.historicalPoints) || 0;
    const detected =
      entry.detectedBars === null || entry.detectedBars === undefined
        ? null
        : Number(entry.detectedBars);
    if (detected !== null && detected !== count) {
      parts.push(`${count} historical run(s) of ${detected} signal bar(s)`);
    } else {
      parts.push(`${count} historical marker(s)`);
    }
    if (entry.latestFlag) parts.push("latest bar also flagged");
  }

  if (entry.score !== null) {
    const prefix = entry.historicalAvailable ? "latest " : "";
    parts.push(
      `${prefix}${entry.scoreLabel || "score"} ${fmtValue(entry.score, 4)}`
    );
  }
  if (entry.threshold !== null) {
    const prefix = entry.historicalAvailable ? "latest " : "";
    parts.push(`${prefix}threshold ${fmtValue(entry.threshold, 4)}`);
  }
  if (entry.barTimestamp) {
    parts.push(`last marked ${fmtTime(entry.barTimestamp, interval)}`);
  }
  if (!parts.length) {
    if (entry.status === "unavailable") return entry.reason || "Unavailable";
    if (entry.status === "clear") return "No anomaly on the latest bar.";
    if (entry.status === "unplaced") {
      return "A flag was returned, but its timestamp did not match a drawn bar.";
    }
  }
  return parts.join(" · ");
}

function renderAnomalyLegend(result, interval) {
  if (!els.anomalyLegend) return;
  if (!result || !Array.isArray(result.entries) || !result.entries.length) {
    els.anomalyLegend.innerHTML = "";
    els.anomalyLegend.classList.add("hidden");
    return;
  }

  const entries = result.entries;
  const flaggedCount = Number(result.markedCandles) || 0;
  const pointCount = Number(result.pointCount) || 0;
  const latestOnlyNames = entries
    .filter((entry) => entry.scope === "latest_only" || entry.latestOnly)
    .map((entry) => entry.name);

  const head = `
    <div class="anomaly-legend-head">
      <div>
        <span class="eyebrow">Anomaly markers</span>
        <strong>${flaggedCount} marked candle${flaggedCount === 1 ? "" : "s"}</strong>
      </div>
      <span>${pointCount} placed detector point${
        pointCount === 1 ? "" : "s"
      } · one red circle per candle</span>
    </div>
  `;

  const items = entries
    .map((entry) => {
      const detail = anomalyDetail(entry, interval);
      const reason = entry.reason || "No explanation was returned.";
      return `
        <li class="anomaly-legend-item ${entry.status}" title="${escapeAttr(
          reason
        )}">
          <span class="anomaly-swatch marker-swatch" aria-hidden="true">${escapeHtml(
            entry.short
          )}</span>
          <span class="anomaly-legend-name">${escapeHtml(entry.name)}</span>
          <span class="anomaly-legend-scope">${escapeHtml(
            anomalyScopeLabel(entry)
          )}</span>
          <span class="anomaly-legend-status">${escapeHtml(
            anomalyStatusLabel(entry)
          )}</span>
          <span class="anomaly-legend-detail">${escapeHtml(detail)}</span>
        </li>
      `;
    })
    .join("");

  const warnings = (result.issues || [])
    .map((issue) => `<p class="anomaly-warning">${escapeHtml(issue)}</p>`)
    .join("");

  const limitations = (result.limitations || [])
    .slice(0, 7)
    .map(
      (item) => `<li>${escapeHtml(item)}</li>`
    )
    .join("");

  const latestOnlyNote = latestOnlyNames.length
    ? `<p class="anomaly-legend-note"><strong>Latest-only:</strong> ${escapeHtml(
        latestOnlyNames.join(", ")
      )} have no historical series, so no historical marker is drawn for them. A latest flag still appears at the latest evaluated candle when the detector returns one.</p>`
    : "";

  els.anomalyLegend.innerHTML = `
    ${head}
    <div class="anomaly-overlay-legend" aria-label="Chart symbol legend">
      <span><i class="overlay-swatch anomaly"></i>Red circle: anomaly marker</span>
      <span><i class="overlay-swatch sma"></i>Dashed blue: latest SMA</span>
      <span><i class="overlay-swatch ema"></i>Dashed teal: latest EMA</span>
      <span><i class="overlay-swatch fib"></i>Dotted grey: Fibonacci</span>
    </div>
    <ul class="anomaly-legend-list">${items}</ul>
    ${result.labelsSuppressed ? `<p class="anomaly-legend-note">Many candles are marked, so marker text labels are hidden to keep the chart readable. Every red circle still opens its full detector details.</p>` : ""}
    ${latestOnlyNote}
    ${
      limitations
        ? `<details class="anomaly-limitations"><summary>Historical method & limitations</summary><ul>${limitations}</ul></details>`
        : ""
    }
    ${warnings}
    <p class="anomaly-legend-note">
      Hover or click a marked candle to see detector names, timestamp, returned
      values, available scores/thresholds and explanations. The legend lists the
      most recent placed point per detector. A statistical anomaly is not proof
      of manipulation, a prediction, or investment advice.
    </p>
  `;
  els.anomalyLegend.classList.remove("hidden");
}

/* ==========================================================================
   Chart rendering
   ========================================================================== */

function renderChart(data, mapped) {
  destroyChart();
  state.interval = data.interval || state.interval;
  state.mapped = mapped;
  state.lastData = data;

  const markerResult = CU
    ? CU.buildAnomalyMarkers(
        data.analysis,
        mapped.bars,
        data.historical_anomalies,
        { maximumLabels: 10 }
      )
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
        '<div class="chart-empty">No usable OHLCV bars were returned, so no ' +
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

  const palette = chartPalette();
  const volumeData = mapped.bars
    .filter((bar) => bar.volume !== null)
    .map((bar) => ({
      time: bar.time,
      value: bar.volume,
      color: bar.close >= bar.open ? palette.volumeUp : palette.volumeDown,
    }));
  state.volumeSeries.setData(volumeData);

  const hasVolume = volumeData.length > 0;
  if (els.volumeToggle) els.volumeToggle.disabled = !hasVolume;
  state.showVolume =
    hasVolume && Boolean(els.volumeToggle && els.volumeToggle.checked);
  state.volumeSeries.applyOptions({ visible: state.showVolume });
  chart.priceScale("right").applyOptions({
    scaleMargins: {
      top: 0.13,
      bottom: state.showVolume ? 0.30 : 0.10,
    },
  });

  applyMarkers(markerResult);
  applyLevels(data.analysis);
  chart.timeScale().fitContent();
  renderChartLegend(mapped.bars[mapped.bars.length - 1]);
  renderChartNote(mapped, data, hasVolume);
  renderAnomalyLegend(markerResult, state.interval);
}

/* ==========================================================================
   Data source panel
   ========================================================================== */

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
    [
      "Symbol",
      `${data.symbol || "—"}${
        data.providerSymbol ? ` (${data.providerSymbol})` : ""
      }`,
    ],
    ["Exchange", data.exchange || selection.exchange],
    ["Requested period", CU.periodLabel(selection.period)],
    ["Requested interval", CU.intervalLabel(selection.interval)],
    ["Bars returned", String(mapped.total)],
    ["Bars with usable OHLCV", String(mapped.bars.length)],
    ["Bars skipped", String(omitted)],
    ["Latest returned close", latest ? fmtValue(latest.close) : "—"],
    ["Latest returned bar", latest ? fmtTime(latest.timestamp) : "—"],
    [
      "Last raw bar timestamp",
      data.lastDataTimestamp ? fmtTime(data.lastDataTimestamp) : "—",
    ],
    ["Anomaly trailing bars excluded", String(excluded)],
    ["Source", data.source || "Yahoo Finance via the backend"],
    [
      "Provider chain",
      Array.isArray(data.providerChain) && data.providerChain.length
        ? data.providerChain.join(" → ")
        : "Not reported by the backend",
    ],
    ["Freshness note", data.freshness || "Not reported by the backend"],
    ["Fetched at", fmtDateTime(data.fetchedAt)],
  ];

  els.metaList.innerHTML = rows
    .map(
      ([label, value]) =>
        `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
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

/* ==========================================================================
   Analysis card components
   ========================================================================== */

function badge(textValue, className) {
  return `<span class="badge ${className || ""}">${escapeHtml(
    textValue
  )}</span>`;
}

function card(title, status, bodyHtml, badgeText, badgeClass) {
  const classes = ["analysis-card"];
  if (status === "unavailable") classes.push("is-unavailable");
  if (status === "flagged") classes.push("is-flagged");

  const fallbackBadge = status === "unavailable" ? "Unavailable" : "Available";
  const fallbackClass =
    status === "unavailable"
      ? "unavailable"
      : status === "flagged"
        ? "flagged"
        : "ok";

  return `<article class="${classes.join(" ")}">
    <h3><span>${escapeHtml(title)}</span>${badge(
      badgeText || fallbackBadge,
      badgeClass || fallbackClass
    )}</h3>
    ${bodyHtml}
  </article>`;
}

function row(label, valueHtml) {
  return `<div class="row"><span>${escapeHtml(
    label
  )}</span><span>${valueHtml}</span></div>`;
}

function featureList(featuresUsed) {
  if (!Array.isArray(featuresUsed) || !featuresUsed.length) return "";
  return `<p class="feature-list"><strong>Features:</strong> ${featuresUsed
    .map((feature) => escapeHtml(feature))
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
        `<tr><td>${escapeHtml(name)}</td><td>${fmtValue(value, 4)}</td></tr>`
    )
    .join("");

  return `<table class="feature-table">
    <caption>${escapeHtml(caption || "Latest feature values")}</caption>
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
      `<p class="explain">The backend marked SMA unavailable. ${completeBarCount} bar(s) with usable OHLCV were drawn from ${rawBarCount} returned bar(s); this SMA needs ${period} usable closes. Try a longer period or a different symbol.</p>
       ${methodText(sma && sma.method)}`,
      "Unavailable",
      "unavailable"
    );
  }

  return card(
    "SMA",
    "ok",
    `<p class="metric">${fmtValue(sma.value)}</p>
     ${row("Period", escapeHtml(String(sma.period)))}
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
      `<p class="explain">The backend marked RSI unavailable. ${completeBarCount} bar(s) with usable OHLCV were drawn from ${rawBarCount} returned bar(s); RSI needs more than ${period} usable closes. Try a longer period.</p>
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
     <p class="metric-note">${escapeHtml(zone)}</p>
     ${row("Period", escapeHtml(String(rsi.period)))}
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
      return `<tr><td>${escapeHtml(label)}</td><td>${fmtValue(
        fibonacci.levels[ratio]
      )}</td></tr>`;
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
  const cssClass =
    deviation > 0 ? "positive" : deviation < 0 ? "negative" : "";
  const direction = deviation > 0 ? "above" : deviation < 0 ? "below" : "at";

  return card(
    "EMA deviation",
    "ok",
    `<p class="metric ${cssClass}">${fmtSignedValue(deviation)}%</p>
     <p class="metric-note">Latest close is ${fmtValue(
       Math.abs(deviation)
     )}% ${direction} the EMA. The chart shows the latest EMA as a dashed price line.</p>
     ${row("Latest close", fmtValue(ema.latest_close))}
     ${row("EMA", fmtValue(ema.ema))}
     ${row("Period", escapeHtml(String(ema.period)))}
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
          ? escapeHtml(divergence.explanation)
          : "The backend could not compute a price/volume divergence for this lookback."
      }</p>`,
      "Unavailable",
      "unavailable"
    );
  }

  return card(
    "Volume–price divergence",
    "ok",
    `<p class="metric signal">${escapeHtml(divergence.signal)}</p>
     ${row(
       "Price change",
       `${fmtSignedValue(divergence.price_change_percent)}%`
     )}
     ${row(
       "Volume change",
       `${fmtSignedValue(divergence.volume_change_percent)}%`
     )}
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
          ? escapeHtml(spike.explanation)
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
     ${row(
       "Above baseline",
       `${fmtSignedValue(spike.percent_above_baseline)}%`
     )}
     ${row("Signal", escapeHtml(spike.signal))}
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
          ? escapeHtml(anomaly.explanation)
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
     ${row("Signal", escapeHtml(anomaly.signal))}
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
      `<p class="explain">No ${escapeHtml(
        config.name
      )} block was returned by the backend.</p>`,
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
      `<p class="explain">${escapeHtml(reason)}</p>
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
          ? escapeHtml(block.hidden_layers.join(" → "))
          : escapeHtml(block.hidden_layers)
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
    <p class="metric ${flagged ? "flagged" : "clear"}">
      ${fmtValue(score, 4)}
      <span class="metric-unit">${escapeHtml(config.scoreLabel)}</span>
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
    <p class="method">Scores only the latest bar with usable OHLCV. A statistical anomaly is not proof of manipulation.</p>
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
    <h3><span>${escapeHtml(title)}</span>${badge("Notice", "unavailable")}</h3>
    <p class="explain">${escapeHtml(message)}</p>
  </article>`;
}

function analysisGroup(title, note, content) {
  if (!content) return "";
  return `<details class="analysis-group" open>
    <summary class="analysis-group-summary" title="Expand or collapse this group">
      <div class="analysis-group-head">
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(note)}</p>
      </div>
      <span class="group-chevron" aria-hidden="true"></span>
    </summary>
    <div class="analysis-grid">${content}</div>
  </details>`;
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
        "No usable OHLCV bars",
        `${rawBarCount} bar(s) were returned, but none had usable OHLCV values. Indicators and anomaly detectors cannot be computed honestly.`
      )
    );
  }

  const trendCards = [
    smaCard(blocks && blocks.sma, rawBarCount, completeBarCount),
    rsiCard(blocks && blocks.rsi, rawBarCount, completeBarCount),
    fibonacciCard(blocks && blocks.fibonacci),
    emaDeviationCard(blocks && blocks.ema_deviation),
  ];
  const volumeCards = [
    divergenceCard(blocks && blocks.volume_price_divergence),
    volumeSpikeCard(blocks && blocks.volume_spike),
    volatilityAnomalyCard(blocks && blocks.volatility_anomaly),
  ];

  if (els.indicatorGrid) {
    els.indicatorGrid.innerHTML =
      notices.join("") +
      analysisGroup(
        "Trend & technical indicators",
        "Computed from the returned closes and range. A latest-only level is a price line, not a historical series.",
        trendCards.join("")
      ) +
      analysisGroup(
        "Volume & price behaviour",
        "Compares recent participation with the returned baseline. Unusual volume is not automatically manipulation.",
        volumeCards.join("")
      );
  }

  const anomalyCards = CU
    ? CU.DETECTOR_CONFIG.map((config) =>
        anomalyCard(config, blocks && blocks[config.key])
      )
    : [];

  if (els.anomalyGrid) {
    els.anomalyGrid.innerHTML = analysisGroup(
      "Model outputs",
      "LOF, autoencoder and transformer outputs are reported as returned. Latest-only models are never drawn backwards through history.",
      anomalyCards.join("")
    );
  }
}

/* ==========================================================================
   Result rendering
   ========================================================================== */

function clearResults() {
  destroyChart();
  state.loadedSelection = null;
  state.selection = null;
  if (els.metaList) els.metaList.innerHTML = "";
  if (els.metaBadge) {
    els.metaBadge.textContent = "";
    els.metaBadge.className = "meta-badge";
  }
  if (els.indicatorGrid) els.indicatorGrid.innerHTML = "";
  if (els.anomalyGrid) els.anomalyGrid.innerHTML = "";
  if (els.resultTitle) els.resultTitle.textContent = "—";
  if (els.resultSub) {
    els.resultSub.textContent = "Symbol, exchange and provider will appear here.";
  }
  if (els.resultLast) els.resultLast.textContent = "—";
  if (els.resultLastDate) els.resultLastDate.textContent = "—";
  if (els.resultChange) {
    els.resultChange.textContent = "—";
    els.resultChange.className = "";
  }
  if (els.resultWindow) els.resultWindow.textContent = "—";
  if (els.resultSource) {
    els.resultSource.textContent = "—";
    els.resultSource.removeAttribute("title");
  }
  if (els.resultFetched) els.resultFetched.textContent = "Fetched time unavailable";
  if (els.resultFreshness) {
    els.resultFreshness.textContent = "Historical data · not live";
    els.resultFreshness.className = "freshness-pill";
    els.resultFreshness.removeAttribute("title");
  }
  setWindowStatus("", "");
  setChartRefreshing(false);
  if (els.dashboardStage) els.dashboardStage.classList.add("hidden");
  if (els.preAnalysis) els.preAnalysis.classList.remove("hidden");
}

function renderResultSummary(data, selection, mapped) {
  const latest = mapped.bars.length
    ? mapped.bars[mapped.bars.length - 1]
    : null;
  const previous =
    mapped.bars.length > 1 ? mapped.bars[mapped.bars.length - 2] : null;

  const change =
    latest && previous ? latest.close - previous.close : null;
  const changePercent =
    latest && previous && previous.close !== 0
      ? ((latest.close - previous.close) / previous.close) * 100
      : null;

  if (els.resultTitle) {
    els.resultTitle.textContent = `${selection.symbol} · ${selection.exchange}`;
  }
  if (els.resultSub) {
    els.resultSub.textContent = `${data.symbol || selection.symbol}${
      data.providerSymbol ? ` (${data.providerSymbol})` : ""
    } · ${CU.windowLabel(selection.window)} of ${CU.intervalLabel(
      data.interval || selection.interval
    )} bars`;
  }
  if (els.resultLast) {
    els.resultLast.textContent = latest ? fmtValue(latest.close) : "—";
  }
  if (els.resultLastDate) {
    els.resultLastDate.textContent = latest
      ? `Latest returned bar: ${fmtTime(latest.timestamp)}`
      : "No usable bars";
  }
  if (els.resultChange) {
    if (change === null) {
      els.resultChange.textContent = "Not enough usable bars";
      els.resultChange.className = "";
    } else {
      els.resultChange.textContent = `${fmtSignedValue(change)} (${fmtSignedValue(
        changePercent
      )}%)`;
      els.resultChange.className =
        change > 0 ? "change-positive" : change < 0 ? "change-negative" : "";
    }
  }
  if (els.resultWindow) {
    els.resultWindow.textContent = `${CU.windowLabel(
      selection.window
    )} · ${CU.intervalLabel(data.interval || selection.interval)}`;
  }
  if (els.resultSource) {
    els.resultSource.textContent = data.source || "Provider not specified";
    els.resultSource.title = data.source || "Provider not specified";
  }
  if (els.resultFetched) {
    els.resultFetched.textContent = `Fetched ${fmtDateTime(
      data.fetchedAt
    )}${
      data.lastDataTimestamp
        ? ` · last raw bar ${fmtTime(data.lastDataTimestamp)}`
        : ""
    }`;
  }
  if (els.resultFreshness) {
    const interval = data.interval || selection.interval;
    const intraday = CU.isIntraday(interval);
    els.resultFreshness.textContent = intraday
      ? "Intraday history · not a live tick feed"
      : "Daily bars · delayed, not live";
    els.resultFreshness.className = "freshness-pill";
    els.resultFreshness.title =
      (intraday
        ? ""
        : "Daily bars can include the current, still-forming session while the market is open. ") +
      (data.freshness ||
        "The backend did not supply a separate freshness note for this response.");
  }
}

function renderResult(data, selection, mapped) {
  state.selection = selection;
  state.loadedSelection = { ...selection };
  state.interval = data.interval || selection.interval;

  renderResultSummary(data, selection, mapped);
  renderMeta(data, mapped, selection);
  renderAnalysis(
    data.analysis,
    Array.isArray(data.bars) ? data.bars.length : 0,
    mapped.bars.length
  );

  if (els.preAnalysis) els.preAnalysis.classList.add("hidden");
  if (els.dashboardStage) els.dashboardStage.classList.remove("hidden");

  if (typeof recordRecentAnalysis === "function" && mapped.bars.length) {
    const latest = mapped.bars[mapped.bars.length - 1];
    const previous =
      mapped.bars.length > 1 ? mapped.bars[mapped.bars.length - 2] : null;
    recordRecentAnalysis({
      symbol: selection.symbol,
      exchange: selection.exchange,
      period: selection.period,
      interval: data.interval || selection.interval,
      source: data.source || "",
      fetchedAt: data.fetchedAt || null,
      barTimestamp: latest.timestamp,
      lastClose: latest.close,
      changePercent:
        previous && previous.close !== 0
          ? ((latest.close - previous.close) / previous.close) * 100
          : null,
    });
  }

  setWindowStatus(
    "success",
    `${CU.windowLabel(selection.window)} daily window loaded for ${
      selection.symbol
    } · ${mapped.bars.length} usable bar(s) drawn from ${mapped.total} returned.`
  );
  renderChart(data, mapped);
}

/* ==========================================================================
   Analysis request
   ========================================================================== */

function cancelActiveRequest() {
  if (state.guard) state.guard.invalidate();
  setBusy(false);
}

function selectionKey(selection) {
  return [
    selection && selection.symbol,
    selection && selection.exchange,
    selection && selection.period,
    selection && selection.interval,
  ].join("|");
}

function retryFor(selection) {
  return () =>
    runAnalysis(selection, {
      preserveResults: Boolean(state.loadedSelection),
      force: true,
    });
}

async function runAnalysis(selection, options) {
  if (!CU) {
    setStatus(
      "error",
      "Chart helpers failed to load. Check that chart-utils.js is present, then reload the page."
    );
    return;
  }

  const settings = options || {};
  const key = selectionKey(selection);
  if (state.loading && state.activeRequestKey === key && !settings.force) {
    return;
  }

  const preserveResults = Boolean(
    settings.preserveResults && state.loadedSelection
  );
  const previousSelection = state.loadedSelection
    ? { ...state.loadedSelection }
    : null;

  let timedOut = false;
  state.activeRequestKey = key;
  hideSelectionNote();

  if (preserveResults) {
    setChartRefreshing(true);
    setWindowStatus(
      "loading",
      `Loading the ${CU.windowLabel(selection.window)} window for ${
        selection.symbol
      }… The current chart stays visible until the new response arrives.`
    );
  } else {
    clearResults();
    if (els.preAnalysis) els.preAnalysis.classList.add("hidden");
  }

  setBusy(true);
  setStatus(
    "loading",
    `Fetching the ${CU.windowLabel(selection.window)} window (daily bars) for ${selection.symbol}…`
  );

  const ticket = state.guard.begin();
  let timeoutId = null;

  try {
    timeoutId = window.setTimeout(() => {
      timedOut = true;
      state.guard.cancel();
    }, REQUEST_TIMEOUT_MS);

    const url = CU.buildHistoryUrl(API_ROOT, selection);
    const response = await fetch(url, {
      signal: ticket.signal,
      headers: { Accept: "application/json" },
    });

    if (!ticket.isCurrent()) return;

    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }

    if (!response.ok) {
      const detail =
        payload && typeof payload.detail === "string"
          ? payload.detail
          : `The backend returned HTTP ${response.status}.`;
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }

    if (!payload || !Array.isArray(payload.bars)) {
      throw new Error(
        "The backend response did not contain a bars array."
      );
    }

    const mapped = CU.mapBars(payload.bars);
    if (!ticket.isCurrent()) return;

    applySelection(selection);
    syncUrl(selection);
    renderResult(payload, selection, mapped);

    if (mapped.bars.length) {
      setStatus(
        "success",
        `Analysis complete: ${mapped.bars.length} usable bar(s) drawn from ${mapped.total} returned for the ${CU.windowLabel(
          selection.window
        )} window.`
      );
    } else {
      setWindowStatus(
        "warning",
        `The ${CU.windowLabel(selection.window)} window returned no usable OHLCV bars; nothing was drawn or invented.`
      );
      setStatus(
        "info",
        `The backend returned ${mapped.total} bar(s), but none had usable OHLCV values. Nothing was drawn or invented.`
      );
    }
  } catch (error) {
    if (!ticket.isCurrent()) return;

    let message;
    if (timedOut) {
      message = `The request exceeded ${Math.round(
        REQUEST_TIMEOUT_MS / 1000
      )} seconds and was stopped. The backend or market-data provider may be slow; try again.`;
    } else if (error && error.name === "AbortError") {
      message =
        "The request was cancelled before a response arrived. No stale response was applied.";
    } else if (error instanceof TypeError) {
      const isLocalHost =
        typeof window !== "undefined" &&
        window.location &&
        (window.location.hostname === "localhost" ||
          window.location.hostname === "127.0.0.1" ||
          window.location.protocol === "file:");
      const localBackend = /(localhost|127\.0\.0\.1)([:\/]|$)/.test(API_ROOT);
      message =
        isLocalHost && (localBackend || !API_ROOT)
          ? `Cannot reach the local backend at ${API_ROOT || "http://localhost:8000"}. Start it with: cd backend, then python -m uvicorn main:app --host 127.0.0.1 --port 8000`
          : "Cannot reach the analysis backend service. The server may be waking up or temporarily unavailable. Please try again shortly.";
    } else if (error && error.status === 502) {
      message = `The backend is running, but the upstream market-data provider failed: ${error.message} No prices were invented.`;
    } else {
      message =
        error && error.message
          ? error.message
          : "The analysis request failed. No data was invented.";
    }

    if (preserveResults && previousSelection) {
      setWindowStatus(
        "error",
        `Could not load the ${CU.windowLabel(selection.window)} window. The previous ${CU.windowLabel(
          previousSelection.window
        )} chart is still shown. ${message}`
      );
    } else {
      if (els.preAnalysis) els.preAnalysis.classList.remove("hidden");
      setWindowStatus(
        "error",
        `Could not load the ${CU.windowLabel(selection.window)} window. ${message}`
      );
    }
    setStatus("error", message, retryFor(selection));
    console.error("Analysis request failed:", error);
  } finally {
    if (timeoutId !== null) window.clearTimeout(timeoutId);
    if (ticket.isCurrent()) {
      state.activeRequestKey = null;
      setChartRefreshing(false);
      setBusy(false);
    }
  }
}

/* ==========================================================================
   Events and initialisation
   ========================================================================== */

function wireEvents() {
  if (!els.form) return;

  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    hideSelectionNote();
    setSymbolInvalid(false);

    if (state.loading) return;

    const selection = selectionFromControls();
    const check = CU.validateSymbol(selection.symbol);

    if (!check.valid) {
      cancelActiveRequest();
      clearResults();
      setSymbolInvalid(true);
      setStatus("error", check.message || "Enter a stock symbol first.");
      if (els.symbol) els.symbol.focus();
      return;
    }

    selection.symbol = check.symbol;
    if (els.symbol) els.symbol.value = check.symbol;
    setSymbolInvalid(false);

    closeSuggestions();

    // The window select already maps to a provider-supported daily pair, so no
    // period/interval reconciliation is needed here.
    if (els.exchange) selection.exchange = els.exchange.value;

    syncUrl(selection);
    runAnalysis(selection);
  });

  if (els.symbol) {
    els.symbol.addEventListener("input", () => {
      setSymbolInvalid(false);
      handleSymbolInput();
    });
    els.symbol.addEventListener("focus", () => {
      const value = els.symbol.value.trim();
      if (value) updateSuggestions(value);
    });

    els.symbol.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveSuggestion(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        moveSuggestion(-1);
      } else if (event.key === "Escape") {
        closeSuggestions();
      } else if (event.key === "Enter" && state.activeSuggestion >= 0) {
        const items = els.suggestions
          ? els.suggestions.querySelectorAll(".suggestion-item")
          : [];
        const item = items[state.activeSuggestion];
        if (item) {
          event.preventDefault();
          selectSuggestion(item.getAttribute("data-symbol"));
        }
      }
    });
  }

  document.addEventListener("click", (event) => {
    if (!els.symbol || !els.suggestions) return;
    const wrapper = els.symbol.closest(".symbol-field-wrap");
    if (wrapper && !wrapper.contains(event.target)) closeSuggestions();
  });

  if (els.windowSelect) {
    els.windowSelect.addEventListener("change", () => {
      if (state.loading) return;
      if (!state.loadedSelection) return;

      const windowEntry = CU.windowForId(els.windowSelect.value);
      if (!windowEntry) return;

      if (windowEntry.window === state.loadedSelection.window) return;

      // One guarded fetch updates the chart and the analysis together; the
      // RequestGuard in runAnalysis aborts any in-flight older request so a
      // stale response can never overwrite the newer selection.
      const nextSelection = {
        ...state.loadedSelection,
        window: windowEntry.window,
        period: windowEntry.period,
        interval: windowEntry.interval,
      };
      runAnalysis(nextSelection, { preserveResults: true, force: true });
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
      if (state.lastData) {
        renderChartNote(
          state.mapped,
          state.lastData,
          state.volumeSeries ? !els.volumeToggle.disabled : false
        );
      }
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

  if (els.resetBtn) {
    els.resetBtn.addEventListener("click", resetSearch);
  }

  window.addEventListener("themechange", updateChartTheme);
}

document.addEventListener("DOMContentLoaded", () => {
  if (!CU) {
    setStatus(
      "error",
      "Chart helpers failed to load, so the analyser cannot start. Check that chart-utils.js is present and reload."
    );
    return;
  }

  const restored = restoreFromUrl();
  wireEvents();
  autorunSelection(restored);
});
