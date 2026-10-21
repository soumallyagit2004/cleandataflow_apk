const API_BASE = "http://localhost:8000";

// ---------- Backend connection test (index.html) ----------
const testBtn = document.getElementById("testBtn");
const responseDiv = document.getElementById("response");

if (testBtn) {
  testBtn.addEventListener("click", async () => {
    responseDiv.textContent = "Connecting&hellip;";
    responseDiv.className = "";

    try {
      const response = await fetch(`${API_BASE}/api/health`);
      const data = await response.json();

      responseDiv.textContent = JSON.stringify(data, null, 2);
      responseDiv.className = "success";
    } catch (error) {
      responseDiv.textContent = "Error: Could not connect to backend.";
      responseDiv.className = "error";
      console.error(error);
    }
  });
}

// ---------- Start Analysis button (index.html) ----------
const startBtn = document.getElementById("startBtn");

if (startBtn) {
  startBtn.addEventListener("click", () => {
    window.location.href = "stock-analysis.html";
  });
}

// ---------- Stocks page: fetch and render indices ----------
async function loadIndices() {
  const container = document.getElementById("indices-container");
  const loading = document.getElementById("loading");
  const errorEl = document.getElementById("error");

  if (!container) return; // not on stocks page

  try {
    const res = await fetch(`${API_BASE}/api/indices`);
    if (!res.ok) throw new Error(`Backend returned ${res.status}`);
    const data = await res.json();
    const indices = data.indices || [];

    loading.classList.add("hidden");

    if (indices.length === 0) {
      container.innerHTML = "<p>No indices available from NSE India right now.</p>";
      container.classList.remove("hidden");
      return;
    }

    container.innerHTML = indices.map((idx) => {
      const changeClass = (idx.change || 0) >= 0 ? "change-positive" : "change-negative";
      return `
        <div class="index-card" data-symbol="${escapeHtml(idx.symbol)}">
          <h3>${escapeHtml(idx.name)}</h3>
          <p class="symbol">${escapeHtml(idx.symbol)}</p>
          <p class="last">Last: ${fmtNumber(idx.last)}</p>
          <p class="change ${changeClass}">${fmtSigned(idx.change)} (${fmtSigned(idx.percentChange)}%)</p>
        </div>
      `;
    }).join("");

    // Add click handlers
    container.querySelectorAll(".index-card").forEach((card) => {
      card.addEventListener("click", () => {
        const symbol = card.getAttribute("data-symbol");
        loadConstituents(symbol);
      });
    });

    container.classList.remove("hidden");
  } catch (err) {
    loading.classList.add("hidden");
    errorEl.textContent = "Error: Could not load indices from backend. Is the FastAPI server running?";
    errorEl.classList.remove("hidden");
    console.error(err);
  }
}

// ---------- Stocks page: fetch and render constituents ----------
async function loadConstituents(indexSymbol) {
  const container = document.getElementById("indices-container");
  const detail = document.getElementById("stock-detail");
  const titleEl = document.getElementById("detailTitle");
  const metaEl = document.getElementById("detailMeta");
  const countEl = document.getElementById("detailCount");
  const listEl = document.getElementById("stocksList");
  const errorEl = document.getElementById("error");

  if (!detail) return;

  errorEl.classList.add("hidden");
  container.classList.add("hidden");
  detail.classList.remove("hidden");

  // Show loading state in detail
  titleEl.textContent = "Loading&hellip;";
  metaEl.textContent = "";
  countEl.textContent = "";
  listEl.innerHTML = "<li>Loading stocks&hellip;</li>";

  try {
    const res = await fetch(`${API_BASE}/api/indices/${encodeURIComponent(indexSymbol)}/constituents`);
    if (!res.ok) {
      if (res.status === 404) {
        throw new Error(`"${indexSymbol}" is not available from NSE India (e.g. Sensex is a BSE index).`);
      }
      throw new Error(`Backend returned ${res.status}`);
    }
    const data = await res.json();

    titleEl.textContent = `${data.index.name} (${data.index.symbol})`;
    const market = data.marketStatus || {};
    metaEl.textContent = `Last: ${fmtNumber(data.index.last)}  |  Change: ${fmtSigned(data.index.change)} (${fmtSigned(data.index.percentChange)}%)  |  Market: ${market.marketStatus || "unknown"}  |  Data: ${data.timestamp || "NSE India"}`;
    countEl.textContent = `Constituent stocks: ${data.constituentCount}`;

    if (data.constituents.length === 0) {
      listEl.innerHTML = "<li>No constituent stocks returned by NSE India.</li>";
      return;
    }

    listEl.innerHTML = data.constituents.map((s) => {
      const changeClass = (s.change || 0) >= 0 ? "change-positive" : "change-negative";
      const analysisHref = `stock-analysis.html?symbol=${encodeURIComponent(s.symbol)}&exchange=NSE`;
      return `
        <li>
          <a class="stock-link" href="${analysisHref}" title="Open stock analysis for ${escapeHtml(s.symbol)}"><strong>${escapeHtml(s.symbol)}</strong></a>: ${fmtNumber(s.last)}
          <span class="change ${changeClass}">(${fmtSigned(s.change)}, ${fmtSigned(s.percentChange)}%)</span>
        </li>
      `;
    }).join("");
  } catch (err) {
    titleEl.textContent = "Error";
    metaEl.textContent = "";
    countEl.textContent = "";
    listEl.innerHTML = `<li style="color:#dc3545">${escapeHtml(err.message)}</li>`;
    console.error(err);
  }
}

// ---------- Stocks page: back button ----------
const backBtn = document.getElementById("backBtn");

if (backBtn) {
  backBtn.addEventListener("click", () => {
    document.getElementById("stock-detail").classList.add("hidden");
    document.getElementById("indices-container").classList.remove("hidden");
  });
}

// ---------- Helpers ----------
function fmtNumber(n) {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtSigned(n) {
  if (n === null || n === undefined) return "—";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function escapeHtml(text) {
  if (!text) return "";
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ---------- Initialize ----------
if (document.getElementById("indices-container")) {
  loadIndices();
}
