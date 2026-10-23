/*
 * Clean Data Flow — shared page runtime.
 *
 * Responsibilities:
 *  - one configurable API base URL
 *  - the dark/light theme toggle (persisted, applied consistently)
 *  - the optional desktop pointer glow
 *  - homepage index cards and market breadth from real /api/indices data
 *  - Stocks page: indices, constituents, filters and honest error states
 *  - Wishlist: a small localStorage list (symbols only, never prices)
 */

const API_BASE = (() => {
  const configured =
    typeof window !== "undefined" && typeof window.CDF_API_BASE === "string"
      ? window.CDF_API_BASE.trim()
      : "";
  const base = configured || "http://localhost:8000";
  return base.replace(/\/+$/, "");
})();

/* Contextual message for an unreachable backend: the terminal command is only
   useful for local development; deployed sites should instead check config.js. */
const API_HELP = /(localhost|127\.0\.0\.1)([:\/]|$)/.test(API_BASE)
  ? "Start the backend in a second terminal:  cd backend  then  python -m uvicorn main:app --host 127.0.0.1 --port 8000"
  : "Check the configured backend URL (window.CDF_API_BASE in config.js) and that the backend deployment is online.";

/* ==========================================================================
   Small helpers
   ========================================================================== */

function byId(id) {
  return document.getElementById(id);
}

function ready(callback) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", callback, { once: true });
  } else {
    callback();
  }
}

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

function toFiniteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function fmtNumber(value, digits = 2) {
  const number = toFiniteNumber(value);
  if (number === null) return "—";
  return number.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtSigned(value, digits = 2) {
  const number = toFiniteNumber(value);
  if (number === null) return "—";
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

/**
 * NSE's constituent payload exposes "identifier" (symbol + series, e.g.
 * HDFCBANKEQN), not a company name. Show a real company name when one is
 * present; otherwise show the exchange/series honestly instead of repeating
 * a garbled identifier.
 */
function displayConstituentName(stock) {
  const symbol = String(stock && stock.symbol ? stock.symbol : "").trim();
  const name = String(stock && stock.name ? stock.name : "").trim();
  if (!name) return "NSE";
  if (name.toUpperCase() === symbol.toUpperCase()) return "NSE";
  const withoutSeries = name.replace(/(EQN|EQ|BE|BZ)$/i, "");
  if (!withoutSeries || withoutSeries.toUpperCase() === symbol.toUpperCase()) {
    return "NSE";
  }
  return name;
}

/**
 * Fetch JSON with a hard timeout. The browser's TypeError for an unreachable
 * host is preserved so the UI can explain that the backend is not running.
 */
async function fetchJson(url, timeoutMs = 20000) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });

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
      error.payload = payload;
      throw error;
    }

    return payload;
  } catch (error) {
    if (timedOut) {
      const timeoutError = new Error(
        `The backend did not respond within ${Math.round(timeoutMs / 1000)} seconds.`
      );
      timeoutError.name = "TimeoutError";
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function describeRequestError(error, endpointLabel) {
  if (error && error.name === "TimeoutError") {
    return {
      message: `${endpointLabel} timed out.`,
      detail: "The backend is reachable but did not answer in time. Try again.",
    };
  }

  if (error && error.name === "AbortError") {
    return {
      message: `${endpointLabel} was cancelled.`,
      detail: "A newer request replaced this one.",
    };
  }

  if (error instanceof TypeError) {
    return {
      message: "Cannot reach the FastAPI backend.",
      detail: `Expected ${API_BASE}. ${API_HELP}`,
    };
  }

  if (error && error.status === 502) {
    return {
      message: "The backend is running, but the upstream market-data provider failed.",
      detail: `${error.message} No prices were invented. Try again shortly.`,
    };
  }

  if (error && error.status === 404) {
    return {
      message: "The requested index is not available from NSE India.",
      detail: error.message,
    };
  }

  return {
    message: error && error.message ? error.message : `${endpointLabel} failed.`,
    detail: "No market data was changed or invented.",
  };
}

/* ==========================================================================
   Theme
   ========================================================================== */

const THEME_KEY = "clean-data-theme";

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function applyTheme(theme) {
  if (theme === "light") {
    document.documentElement.dataset.theme = "light";
  } else {
    delete document.documentElement.dataset.theme;
  }

  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (error) {
    /* Private mode may deny localStorage; the theme still applies. */
  }

  const moon = byId("moonIcon");
  const sun = byId("sunIcon");
  if (moon) moon.style.display = theme === "light" ? "none" : "block";
  if (sun) sun.style.display = theme === "light" ? "block" : "none";

  window.dispatchEvent(
    new CustomEvent("themechange", { detail: { theme } })
  );
}

function initTheme() {
  const toggle = byId("themeToggle");
  applyTheme(currentTheme());

  if (toggle) {
    toggle.addEventListener("click", () => {
      applyTheme(currentTheme() === "light" ? "dark" : "light");
    });
  }
}

/* ==========================================================================
   Optional pointer glow (desktop, fine pointers, motion allowed only)
   ========================================================================== */

function initPointerGlow() {
  const layer = document.querySelector(".background-layer");
  if (!layer || typeof window.matchMedia !== "function") return;

  const finePointer = window.matchMedia("(pointer: fine)").matches;
  const reducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)"
  ).matches;
  if (!finePointer || reducedMotion) return;

  let x = 50;
  let y = 12;
  let frame = null;

  const paint = () => {
    frame = null;
    layer.style.setProperty("--pointer-x", `${x}%`);
    layer.style.setProperty("--pointer-y", `${y}%`);
  };

  window.addEventListener(
    "pointermove",
    (event) => {
      x = (event.clientX / Math.max(window.innerWidth, 1)) * 100;
      y = (event.clientY / Math.max(window.innerHeight, 1)) * 100;
      if (frame === null) frame = requestAnimationFrame(paint);
    },
    { passive: true }
  );
}

/* ==========================================================================
   Homepage — real index cards and breadth
   ========================================================================== */

const HOME_PRIORITY = [
  "NIFTY 50",
  "NIFTY BANK",
  "NIFTY IT",
  "NIFTY NEXT 50",
  "NIFTY 100",
  "NIFTY MIDCAP 50",
  "NIFTY 500",
  "NIFTY FINANCIAL SERVICES",
];

let homeIndices = [];
let homeLoading = false;

function homeErrorRetry() {
  loadHomeIndices();
}

function showHomeError(error) {
  const box = byId("homeError");
  const grid = byId("overviewGrid");
  if (!box) return;

  const described = describeRequestError(error, "Loading NSE indices");
  box.innerHTML = `
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="8" x2="12" y2="12"></line>
      <line x1="12" y1="16" x2="12.01" y2="16"></line>
    </svg>
    <div class="error-text">
      <span>${escapeHtml(described.message)}</span>
      <span class="error-detail">${escapeHtml(described.detail)}</span>
    </div>
    <button type="button" class="retry-btn" id="homeRetry">Retry</button>
  `;
  box.classList.remove("hidden");
  if (grid) {
    grid.innerHTML =
      '<p class="text-muted" style="grid-column:1/-1;margin:0;">Indices could not be loaded. Use Retry above.</p>';
  }
  const retry = byId("homeRetry");
  if (retry) retry.addEventListener("click", homeErrorRetry);
}

function renderHomeBreadth(indices) {
  const panel = byId("breadthPanel");
  if (!panel) return;

  const usable = indices.filter(
    (item) => toFiniteNumber(item.percentChange) !== null
  );

  if (!usable.length) {
    panel.innerHTML =
      '<p class="text-muted" style="margin:0;">Breadth is unavailable because the provider did not return change values.</p>';
    return;
  }

  const advances = usable.filter((item) => item.percentChange > 0).length;
  const declines = usable.filter((item) => item.percentChange < 0).length;
  const unchanged = usable.length - advances - declines;
  const average =
    usable.reduce((sum, item) => sum + Number(item.percentChange), 0) /
    usable.length;

  const sorted = [...usable].sort(
    (a, b) => Number(b.percentChange) - Number(a.percentChange)
  );
  const top = sorted[0];
  const bottom = sorted[sorted.length - 1];

  panel.innerHTML = `
    <div class="breadth-grid">
      <div class="breadth-stat">
        <span class="breadth-label">Advances</span>
        <strong class="change-positive">${advances}</strong>
      </div>
      <div class="breadth-stat">
        <span class="breadth-label">Declines</span>
        <strong class="change-negative">${declines}</strong>
      </div>
      <div class="breadth-stat">
        <span class="breadth-label">Unchanged</span>
        <strong>${unchanged}</strong>
      </div>
      <div class="breadth-stat">
        <span class="breadth-label">Average change</span>
        <strong class="${average >= 0 ? "change-positive" : "change-negative"}">${fmtSigned(average)}%</strong>
      </div>
    </div>
    <div class="breadth-movers">
      <p><span class="text-muted">Strongest:</span> <strong>${escapeHtml(top.name || top.symbol)}</strong>
        <span class="change-positive">${fmtSigned(top.percentChange)}%</span></p>
      <p><span class="text-muted">Weakest:</span> <strong>${escapeHtml(bottom.name || bottom.symbol)}</strong>
        <span class="${Number(bottom.percentChange) >= 0 ? "change-positive" : "change-negative"}">${fmtSigned(bottom.percentChange)}%</span></p>
    </div>
    <p class="breadth-note">Computed only from the ${usable.length} indices returned by NSE India. Not a recommendation.</p>
  `;
}

function renderHomeIndices(payload) {
  const grid = byId("overviewGrid");
  if (!grid) return;

  const indices = Array.isArray(payload && payload.indices)
    ? payload.indices
    : [];
  homeIndices = indices;

  const meta = byId("indicesMeta");
  if (meta) {
    const when = payload && payload.timestamp ? String(payload.timestamp) : "";
    meta.textContent = when
      ? `${indices.length} NSE indices · session data ${when}`
      : `${indices.length} NSE indices · latest NSE session data`;
  }

  if (!indices.length) {
    grid.innerHTML =
      '<p class="text-muted" style="grid-column:1/-1;margin:0;">NSE India returned no indices for this session. Nothing was fabricated.</p>';
    renderHomeBreadth([]);
    return;
  }

  const bySymbol = new Map(
    indices.map((item) => [String(item.symbol || "").toUpperCase(), item])
  );

  const chosen = [];
  HOME_PRIORITY.forEach((symbol) => {
    const item = bySymbol.get(symbol);
    if (item && !chosen.includes(item)) chosen.push(item);
  });
  indices.forEach((item) => {
    if (chosen.length >= 8) return;
    if (item && !chosen.includes(item)) chosen.push(item);
  });

  grid.innerHTML = chosen
    .slice(0, 8)
    .map((item) => {
      const change = toFiniteNumber(item.change);
      const percent = toFiniteNumber(item.percentChange);
      const direction =
        change === null || change === 0
          ? ""
          : change > 0
            ? "change-positive"
            : "change-negative";

      return `
        <article class="index-card">
          <span class="symbol">${escapeHtml(item.symbol || "NSE index")}</span>
          <p class="value">${fmtNumber(item.last)}</p>
          <p class="change ${direction}">
            ${change === null ? "Change unavailable" : `${fmtSigned(change)} (${fmtSigned(percent)}%)`}
          </p>
          <p class="timestamp">NSE India · latest returned session</p>
        </article>
      `;
    })
    .join("");

  renderHomeBreadth(indices);
}

async function loadHomeIndices() {
  const grid = byId("overviewGrid");
  if (!grid || homeLoading) return;

  homeLoading = true;
  const errorBox = byId("homeError");
  if (errorBox) errorBox.classList.add("hidden");
  grid.innerHTML = '<div class="loading">Loading NSE indices…</div>';

  try {
    const payload = await fetchJson(`${API_BASE}/api/indices`, 25000);
    renderHomeIndices(payload);
  } catch (error) {
    console.error("Homepage index load failed:", error);
    showHomeError(error);
  } finally {
    homeLoading = false;
  }
}

/* ==========================================================================
   Stocks page
   ========================================================================== */

const stocksState = {
  indices: [],
  loading: false,
  filter: "",
  sort: "default",
  selectedIndex: null,
  retryAction: null,
};

function stocksErrorElements() {
  return {
    box: byId("error"),
    message: byId("errorMessage"),
    detail: byId("errorDetail"),
    retry: byId("retryIndices"),
  };
}

function showStocksError(message, detail, retryAction) {
  const els = stocksErrorElements();
  if (!els.box) return;

  if (els.message) els.message.textContent = message;
  if (els.detail) els.detail.textContent = detail || "";
  els.box.classList.remove("hidden");
  stocksState.retryAction = retryAction || null;
  if (els.retry) els.retry.style.display = retryAction ? "" : "none";
}

function hideStocksError() {
  const els = stocksErrorElements();
  if (els.box) els.box.classList.add("hidden");
  stocksState.retryAction = null;
}

function setStocksLoading(isLoading) {
  const loading = byId("loading");
  const container = byId("indices-container");
  const detail = byId("stock-detail");
  const toolbar = byId("indicesToolbar");

  if (loading) {
    loading.classList.toggle("hidden", !isLoading);
    loading.setAttribute("aria-busy", String(isLoading));
  }
  if (isLoading) {
    if (container) container.classList.add("hidden");
    if (detail) detail.classList.add("hidden");
    if (toolbar) toolbar.classList.add("hidden");
  }
}

function sortIndices(list) {
  const sorted = [...list];
  if (stocksState.sort === "gainers") {
    sorted.sort(
      (a, b) =>
        (toFiniteNumber(b.percentChange) ?? -Infinity) -
        (toFiniteNumber(a.percentChange) ?? -Infinity)
    );
  } else if (stocksState.sort === "losers") {
    sorted.sort(
      (a, b) =>
        (toFiniteNumber(a.percentChange) ?? Infinity) -
        (toFiniteNumber(b.percentChange) ?? Infinity)
    );
  } else if (stocksState.sort === "name") {
    sorted.sort((a, b) =>
      String(a.name || a.symbol || "").localeCompare(
        String(b.name || b.symbol || "")
      )
    );
  }
  return sorted;
}

function filteredIndices() {
  const query = stocksState.filter.trim().toUpperCase();
  const source = !query
    ? stocksState.indices
    : stocksState.indices.filter((item) => {
        const symbol = String(item.symbol || "").toUpperCase();
        const name = String(item.name || "").toUpperCase();
        return symbol.includes(query) || name.includes(query);
      });
  return sortIndices(source);
}

function renderIndices() {
  const container = byId("indices-container");
  const count = byId("indicesCount");
  const toolbar = byId("indicesToolbar");
  if (!container) return;

  if (toolbar) toolbar.classList.remove("hidden");

  const visible = filteredIndices();
  if (count) {
    const total = stocksState.indices.length;
    count.textContent =
      visible.length === total
        ? `${total} indices`
        : `${visible.length} of ${total} indices`;
  }

  if (!visible.length) {
    container.innerHTML = `
      <div class="empty-state">
        <p><strong>No indices match your filter.</strong></p>
        <p class="text-muted">Clear the search box to see all ${stocksState.indices.length} NSE indices.</p>
      </div>
    `;
    container.classList.remove("hidden");
    return;
  }

  container.innerHTML = visible
    .map((item) => {
      const change = toFiniteNumber(item.change);
      const percent = toFiniteNumber(item.percentChange);
      const direction =
        change === null || change === 0
          ? ""
          : change > 0
            ? "change-positive"
            : "change-negative";
      const symbol = String(item.symbol || "");
      const name = String(item.name || symbol).trim();
      const subtitle =
        !symbol || name.toUpperCase() === symbol.toUpperCase()
          ? "NSE index"
          : symbol;

      return `
        <button type="button" class="index-card" data-symbol="${escapeAttr(symbol)}"
          aria-label="Show constituents of ${escapeHtml(name)}">
          <h3>${escapeHtml(name)}</h3>
          <p class="symbol">${escapeHtml(subtitle)}</p>
          <p class="last">${fmtNumber(item.last)}</p>
          <p class="change ${direction}">
            ${change === null ? "Change unavailable" : `${fmtSigned(change)} (${fmtSigned(percent)}%)`}
          </p>
        </button>
      `;
    })
    .join("");

  container.querySelectorAll(".index-card").forEach((card) => {
    card.addEventListener("click", () => {
      loadConstituents(card.getAttribute("data-symbol"));
    });
  });

  container.classList.remove("hidden");
}

async function loadIndices() {
  const container = byId("indices-container");
  if (!container || stocksState.loading) return;

  stocksState.loading = true;
  hideStocksError();
  setStocksLoading(true);

  try {
    const payload = await fetchJson(`${API_BASE}/api/indices`, 25000);
    const indices = Array.isArray(payload && payload.indices)
      ? payload.indices.filter((item) => item && item.symbol)
      : [];

    stocksState.indices = indices;
    setStocksLoading(false);

    if (!indices.length) {
      container.innerHTML = `
        <div class="empty-state">
          <p><strong>NSE India returned no index data for this session.</strong></p>
          <p class="text-muted">Nothing was invented. Try again after the next market session.</p>
        </div>
      `;
      container.classList.remove("hidden");
      return;
    }

    renderIndices();
  } catch (error) {
    console.error("Index load failed:", error);
    setStocksLoading(false);
    const described = describeRequestError(error, "Loading indices");
    showStocksError(described.message, described.detail, loadIndices);
    container.innerHTML = "";
    container.classList.add("hidden");
  } finally {
    stocksState.loading = false;
  }
}

async function loadConstituents(indexSymbol) {
  if (!indexSymbol) return;

  const container = byId("indices-container");
  const detail = byId("stock-detail");
  const titleEl = byId("detailTitle");
  const metaEl = byId("detailMeta");
  const countEl = byId("detailCount");
  const listEl = byId("stocksList");

  if (!detail || !container || !listEl) return;

  stocksState.selectedIndex = indexSymbol;
  hideStocksError();
  container.classList.add("hidden");
  detail.classList.remove("hidden");

  if (titleEl) titleEl.textContent = indexSymbol;
  if (metaEl) metaEl.textContent = "Loading constituent stocks…";
  if (countEl) countEl.textContent = "";
  listEl.innerHTML = '<li class="loading" style="min-height:80px;">Loading stocks…</li>';

  try {
    const data = await fetchJson(
      `${API_BASE}/api/indices/${encodeURIComponent(indexSymbol)}/constituents`,
      30000
    );

    const summary = data && data.index ? data.index : {};
    const constituents = Array.isArray(data && data.constituents)
      ? data.constituents
      : [];
    const market = (data && data.marketStatus) || {};

    if (titleEl) {
      const name = String(summary.name || indexSymbol).trim();
      const symbol = String(summary.symbol || indexSymbol).trim();
      titleEl.textContent =
        name.toUpperCase() === symbol.toUpperCase()
          ? name
          : `${name} (${symbol})`;
    }

    if (metaEl) {
      const marketState =
        market.marketStatus || market.market || "session state unavailable";
      metaEl.textContent =
        `Last ${fmtNumber(summary.last)} · Change ${fmtSigned(summary.change)} ` +
        `(${fmtSigned(summary.percentChange)}%) · Market ${marketState} · ` +
        `Source: NSE India${data && data.timestamp ? ` (${data.timestamp})` : ""}`;
    }

    if (countEl) {
      countEl.textContent = `${constituents.length} constituent stocks`;
    }

    if (!constituents.length) {
      listEl.innerHTML =
        '<li class="text-muted">NSE India returned no constituent stocks for this index.</li>';
      return;
    }

    listEl.innerHTML = constituents
      .map((stock) => {
        const change = toFiniteNumber(stock.change);
        const percent = toFiniteNumber(stock.percentChange);
        const direction =
          change === null || change === 0
            ? ""
            : change > 0
              ? "change-positive"
              : "change-negative";
        const symbol = String(stock.symbol || "");
        const href = `stock-analysis.html?symbol=${encodeURIComponent(symbol)}&exchange=NSE`;

        return `
          <li>
            <a class="stock-link" href="${href}" title="Analyse ${escapeHtml(symbol)}">
              <strong>${escapeHtml(symbol)}</strong>
            </a>
            <span class="stock-name" title="NSE identifier: ${escapeAttr(
              stock.name || symbol
            )}">${escapeHtml(displayConstituentName(stock))}</span>
            <span class="stock-price">${fmtNumber(stock.last)}</span>
            <span class="change ${direction}">
              ${change === null ? "—" : `${fmtSigned(change)} (${fmtSigned(percent)}%)`}
            </span>
          </li>
        `;
      })
      .join("");
  } catch (error) {
    console.error("Constituent load failed:", error);
    const described = describeRequestError(
      error,
      `Loading constituents for ${indexSymbol}`
    );
    if (titleEl) titleEl.textContent = indexSymbol;
    if (metaEl) metaEl.textContent = "";
    if (countEl) countEl.textContent = "";
    listEl.innerHTML = "";
    showStocksError(described.message, described.detail, () =>
      loadConstituents(indexSymbol)
    );
  }
}

function initStocksPage() {
  const container = byId("indices-container");
  if (!container) return;

  const filter = byId("indexFilter");
  const sort = byId("indexSort");
  const retry = byId("retryIndices");
  const back = byId("backBtn");

  if (filter) {
    filter.addEventListener("input", () => {
      stocksState.filter = filter.value;
      renderIndices();
    });
  }

  if (sort) {
    sort.addEventListener("change", () => {
      stocksState.sort = sort.value;
      renderIndices();
    });
  }

  if (retry) {
    retry.addEventListener("click", () => {
      const action = stocksState.retryAction;
      if (typeof action === "function") action();
    });
  }

  if (back) {
    back.addEventListener("click", () => {
      const detail = byId("stock-detail");
      if (detail) detail.classList.add("hidden");
      container.classList.remove("hidden");
      hideStocksError();
    });
  }

  loadIndices();
}

/* ==========================================================================
   Wishlist — symbols stored locally, never prices
   ========================================================================== */

const WISHLIST_KEY = "clean-data-wishlist";

function readWishlist() {
  try {
    const raw = localStorage.getItem(WISHLIST_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((item) => typeof item === "string")
      : [];
  } catch (error) {
    return [];
  }
}

function writeWishlist(symbols) {
  try {
    localStorage.setItem(WISHLIST_KEY, JSON.stringify(symbols));
  } catch (error) {
    /* Storage can be unavailable; the on-page list still updates. */
  }
}

function renderWishlist() {
  const list = byId("wishlistList");
  const empty = byId("wishlistEmpty");
  if (!list) return;

  const symbols = readWishlist();
  if (empty) empty.classList.toggle("hidden", symbols.length > 0);

  list.innerHTML = symbols
    .map(
      (symbol) => `
        <li class="wishlist-item">
          <a class="stock-link" href="stock-analysis.html?symbol=${encodeURIComponent(symbol)}&exchange=NSE">
            <strong>${escapeHtml(symbol)}</strong>
          </a>
          <span class="text-muted small">NSE</span>
          <span class="wishlist-actions">
            <a class="stock-link small" href="stock-analysis.html?symbol=${encodeURIComponent(symbol)}&exchange=NSE">Analyse</a>
            <button type="button" data-remove="${escapeAttr(symbol)}">Remove</button>
          </span>
        </li>
      `
    )
    .join("");

  list.querySelectorAll("button[data-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      const symbol = button.getAttribute("data-remove");
      writeWishlist(readWishlist().filter((item) => item !== symbol));
      renderWishlist();
    });
  });
}

function initWishlistPage() {
  const form = byId("addStockForm");
  const input = byId("wishlistSymbol");
  const message = byId("wishlistMessage");

  if (!form || !input) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const symbol = input.value.trim().toUpperCase();

    if (!/^[A-Z0-9.^-]{1,30}$/.test(symbol)) {
      if (message) {
        message.textContent =
          "Enter a valid NSE/BSE symbol using letters, numbers, dots, hyphens or ^.";
        message.className = "selection-note";
      }
      return;
    }

    const symbols = readWishlist();
    if (!symbols.includes(symbol)) {
      symbols.push(symbol);
      writeWishlist(symbols);
    }

    input.value = "";
    if (message) {
      message.textContent = `${symbol} added. Symbols are stored in this browser only; prices are always fetched live.`;
      message.className = "selection-note";
    }
    renderWishlist();
  });

  renderWishlist();
}

/* ==========================================================================
   Homepage test / CTA buttons
   ========================================================================== */

function initHomepageButtons() {
  const testBtn = byId("testBtn");
  const responseDiv = byId("response");

  if (testBtn && responseDiv) {
    testBtn.addEventListener("click", async () => {
      responseDiv.textContent = "Connecting…";
      responseDiv.className = "";
      try {
        const data = await fetchJson(`${API_BASE}/api/health`, 10000);
        responseDiv.textContent = JSON.stringify(data, null, 2);
        responseDiv.className = "response-box success";
      } catch (error) {
        responseDiv.textContent = `Error: Could not connect to ${API_BASE}.`;
        responseDiv.className = "response-box error";
        console.error(error);
      }
    });
  }

  const startBtn = byId("startBtn");
  if (startBtn) {
    startBtn.addEventListener("click", () => {
      window.location.href = "stock-analysis.html";
    });
  }
}

/* ==========================================================================
   Boot
   ========================================================================== */

ready(() => {
  initTheme();
  initPointerGlow();
  initHomepageButtons();

  if (byId("overviewGrid")) {
    loadHomeIndices();
  }

  if (byId("indices-container")) {
    initStocksPage();
  }

  if (byId("wishlistList")) {
    initWishlistPage();
  }
});
