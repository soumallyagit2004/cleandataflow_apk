/*
 * Clean Data Flow — shared page runtime.
 *
 * Responsibilities:
 *  - one configurable API base URL and honest request errors
 *  - the dark/light theme toggle (persisted, applied consistently)
 *  - responsive navigation and the optional desktop pointer effects
 *  - homepage: real index cards, breadth, a real-data hero snapshot,
 *    market news, this-device recent analysis and real feedback submission
 *  - Stocks page: indices, constituent search/sort and honest states
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

function parseDateValue(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function fmtLocalDateTime(value) {
  const date = parseDateValue(value);
  if (!date) return value ? String(value) : "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* NSE session timestamps are provider-specific strings. Display the string the
   backend returned; only reformat values that are unambiguously parseable. */
function fmtSessionTimestamp(value) {
  if (!value) return "timestamp unavailable";
  const text = String(value);
  if (/T.*Z$/.test(text) || /^\d{4}-\d{2}-\d{2}/.test(text)) {
    return fmtLocalDateTime(text);
  }
  return text;
}

function fmtPublishedTime(value) {
  const date = parseDateValue(value);
  if (!date) return "Publication time unavailable";
  return `Published ${date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

function timeAgo(value) {
  const date = parseDateValue(value);
  if (!date) return "";
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  return fmtLocalDateTime(value);
}

function periodLabelSafe(period) {
  if (window.ChartUtils && typeof window.ChartUtils.periodLabel === "function") {
    return window.ChartUtils.periodLabel(period);
  }
  return String(period || "—");
}

function intervalLabelSafe(interval) {
  if (window.ChartUtils && typeof window.ChartUtils.intervalLabel === "function") {
    return window.ChartUtils.intervalLabel(interval);
  }
  return String(interval || "—");
}

function reducedMotion() {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/* Static display aliases for well-known NSE tickers. The NSE constituent
   endpoint returns exchange identifiers (for example "INFYEQN"), not company
   names. These aliases are fixed reference labels, not index membership or
   market data; unknown symbols fall back to an honest "NSE equity" label. */
const COMPANY_ALIASES = {
  "RELIANCE": "Reliance Industries",
  "TCS": "Tata Consultancy Services",
  "HDFCBANK": "HDFC Bank",
  "ICICIBANK": "ICICI Bank",
  "INFY": "Infosys",
  "HINDUNILVR": "Hindustan Unilever",
  "ITC": "ITC",
  "SBIN": "State Bank of India",
  "BHARTIARTL": "Bharti Airtel",
  "KOTAKBANK": "Kotak Mahindra Bank",
  "LT": "Larsen & Toubro",
  "AXISBANK": "Axis Bank",
  "BAJFINANCE": "Bajaj Finance",
  "ASIANPAINT": "Asian Paints",
  "MARUTI": "Maruti Suzuki India",
  "HCLTECH": "HCL Technologies",
  "SUNPHARMA": "Sun Pharmaceutical Industries",
  "TITAN": "Titan Company",
  "ULTRACEMCO": "UltraTech Cement",
  "WIPRO": "Wipro",
  "NESTLEIND": "Nestlé India",
  "ONGC": "Oil & Natural Gas Corporation",
  "NTPC": "NTPC",
  "POWERGRID": "Power Grid Corporation of India",
  "M&M": "Mahindra & Mahindra",
  "TATAMOTORS": "Tata Motors",
  "TATASTEEL": "Tata Steel",
  "ADANIENT": "Adani Enterprises",
  "ADANIPORTS": "Adani Ports and Special Economic Zone",
  "COALINDIA": "Coal India",
  "BAJAJFINSV": "Bajaj Finserv",
  "TECHM": "Tech Mahindra",
  "HINDALCO": "Hindalco Industries",
  "GRASIM": "Grasim Industries",
  "INDUSINDBK": "IndusInd Bank",
  "DRREDDY": "Dr. Reddy's Laboratories",
  "CIPLA": "Cipla",
  "BRITANNIA": "Britannia Industries",
  "EICHERMOT": "Eicher Motors",
  "HEROMOTOCO": "Hero MotoCorp",
  "BAJAJ-AUTO": "Bajaj Auto",
  "DIVISLAB": "Divi's Laboratories",
  "APOLLOHOSP": "Apollo Hospitals Enterprise",
  "JSWSTEEL": "JSW Steel",
  "SBILIFE": "SBI Life Insurance",
  "HDFCLIFE": "HDFC Life Insurance",
  "TATACONSUM": "Tata Consumer Products",
  "BPCL": "Bharat Petroleum Corporation",
  "SHRIRAMFIN": "Shriram Finance",
  "LTIM": "LTIMindtree",
  "TRENT": "Trent",
  "BEL": "Bharat Electronics",
  "ETERNAL": "Eternal (formerly Zomato)",
  "DMART": "Avenue Supermarts",
  "PIDILITIND": "Pidilite Industries",
  "GODREJCP": "Godrej Consumer Products",
  "DABUR": "Dabur India",
  "HAVELLS": "Havells India",
  "SIEMENS": "Siemens India",
  "ABB": "ABB India",
  "DLF": "DLF",
  "VEDL": "Vedanta",
  "GAIL": "GAIL (India)",
  "IRCTC": "Indian Railway Catering & Tourism Corporation",
  "POLYCAB": "Polycab India",
  "DIXON": "Dixon Technologies",
  "MOTHERSON": "Samvardhana Motherson International",
  "TVSMOTOR": "TVS Motor Company",
  "CHOLAFIN": "Cholamandalam Investment and Finance",
  "BANKBARODA": "Bank of Baroda",
  "PNB": "Punjab National Bank",
  "CANBK": "Canara Bank",
  "IDFCFIRSTB": "IDFC First Bank",
  "PAYTM": "One 97 Communications (Paytm)",
  "NYKAA": "FSN E-Commerce Ventures (Nykaa)",
  "IRFC": "Indian Railway Finance Corporation",
  "RVNL": "Rail Vikas Nigam",
  "HAL": "Hindustan Aeronautics",
  "BHEL": "Bharat Heavy Electricals",
  "SAIL": "Steel Authority of India",
  "TATAPOWER": "Tata Power",
  "LICI": "Life Insurance Corporation of India",
  "JIOFIN": "Jio Financial Services",
  "MAXHEALTH": "Max Healthcare Institute",
  "INDIGO": "InterGlobe Aviation (IndiGo)",
  "LUPIN": "Lupin",
  "AUROPHARMA": "Aurobindo Pharma",
  "TORNTPHARM": "Torrent Pharmaceuticals",
  "ALKEM": "Alkem Laboratories",
  "TATACOMM": "Tata Communications",
  "TATAELXSI": "Tata Elxsi",
  "MPHASIS": "Mphasis",
  "PERSISTENT": "Persistent Systems",
  "COFORGE": "Coforge",
  "OFSS": "Oracle Financial Services Software",
  "IDEA": "Vodafone Idea",
  "ADANIGREEN": "Adani Green Energy",
  "ADANIPOWER": "Adani Power",
  "ATGL": "Adani Total Gas",
  "JINDALSTEL": "Jindal Steel & Power",
  "NATIONALUM": "National Aluminium Company",
  "YESBANK": "Yes Bank",
  "BANDHANBNK": "Bandhan Bank",
  "FEDERALBNK": "Federal Bank",
  "AUBANK": "AU Small Finance Bank",
  "RBLBANK": "RBL Bank",
  "UNIONBANK": "Union Bank of India",
  "INDIANB": "Indian Bank",
  "MAHABANK": "Bank of Maharashtra",
  "CENTRALBK": "Central Bank of India",
  "UCOBANK": "UCO Bank",
  "BANKINDIA": "Bank of India",
  "PFC": "Power Finance Corporation",
  "RECLTD": "REC",
  "IREDA": "Indian Renewable Energy Development Agency",
  "NHPC": "NHPC",
  "SJVN": "SJVN",
  "TORNTPOWER": "Torrent Power",
  "JSWENERGY": "JSW Energy",
  "OIL": "Oil India",
  "PETRONET": "Petronet LNG",
  "IGL": "Indraprastha Gas",
  "GUJGASLTD": "Gujarat Gas",
  "CONCOR": "Container Corporation of India",
  "INDHOTEL": "Indian Hotels Company",
  "JUBLFOOD": "Jubilant FoodWorks",
  "VBL": "Varun Beverages",
  "UBL": "United Breweries",
  "PAGEIND": "Page Industries",
  "KALYANKJIL": "Kalyan Jewellers India",
  "BSE": "BSE",
  "CDSL": "Central Depository Services",
  "CAMS": "Computer Age Management Services",
  "ANGELONE": "Angel One",
  "MOTILALOFS": "Motilal Oswal Financial Services",
  "MANAPPURAM": "Manappuram Finance",
  "MUTHOOTFIN": "Muthoot Finance",
  "SBICARD": "SBI Cards and Payment Services",
  "HDFCAMC": "HDFC Asset Management Company",
  "ICICIGI": "ICICI Lombard General Insurance",
  "ICICIPRULI": "ICICI Prudential Life Insurance",
  "POLICYBZR": "PB Fintech (Policybazaar)",
  "DELHIVERY": "Delhivery",
  "SUNTV": "Sun TV Network",
  "PVRINOX": "PVR INOX",
};

/**
 * NSE's constituent payload exposes "identifier" (symbol + series, e.g.
 * HDFCBANKEQN), not a company name. Show a known fixed alias when available;
 * otherwise show an honest series label instead of a garbled identifier.
 */
function displayConstituentName(stock) {
  const symbol = String(stock && stock.symbol ? stock.symbol : "").trim();
  const alias = COMPANY_ALIASES[symbol.toUpperCase()];
  if (alias) return alias;

  const name = String(stock && stock.name ? stock.name : "").trim();
  if (!name) return "NSE equity";
  if (name.toUpperCase() === symbol.toUpperCase()) return "NSE equity";
  const withoutSeries = name.replace(/(EQN|EQ|BE|BZ)$/i, "");
  if (!withoutSeries || withoutSeries.toUpperCase() === symbol.toUpperCase()) {
    return "NSE equity";
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

async function postJson(url, body, timeoutMs = 20000) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(url, {
      method: "POST",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }

    if (!response.ok) {
      let detail = `The backend returned HTTP ${response.status}.`;
      if (payload && typeof payload.detail === "string") {
        detail = payload.detail;
      } else if (payload && Array.isArray(payload.detail)) {
        detail = payload.detail
          .map((item) => (item && item.msg ? item.msg : String(item)))
          .join(" ");
      }
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
   Responsive navigation
   ========================================================================== */

function initNav() {
  const toggle = byId("navToggle");
  const links = byId("primaryNav");
  const nav = document.querySelector(".site-nav");
  if (!toggle || !links || !nav) return;

  const setOpen = (open) => {
    nav.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute(
      "aria-label",
      open ? "Close navigation menu" : "Open navigation menu"
    );
  };

  toggle.addEventListener("click", () => {
    setOpen(toggle.getAttribute("aria-expanded") !== "true");
  });

  links.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setOpen(false));
  });

  document.addEventListener("click", (event) => {
    if (
      toggle.getAttribute("aria-expanded") === "true" &&
      !nav.contains(event.target)
    ) {
      setOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
      toggle.focus();
    }
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 880) setOpen(false);
  });
}

/* ==========================================================================
   Ambient depth canvas (custom lightweight Canvas renderer, no WebGL needed)
   ========================================================================== */

function initAmbientCanvas() {
  const canvas = byId("ambientCanvas");
  if (!canvas) return;
  if (reducedMotion()) {
    canvas.style.display = "none";
    return;
  }

  let context;
  try {
    context = canvas.getContext("2d");
  } catch (error) {
    return;
  }
  if (!context) return;

  const state = {
    width: 0,
    height: 0,
    dpr: 1,
    particles: [],
    rings: [
      { radius: 0.30, tiltX: 0.52, tiltZ: 0.22, speed: 0.010, phase: 0.4, color: "56, 189, 248" },
      { radius: 0.40, tiltX: 1.18, tiltZ: -0.35, speed: -0.007, phase: 1.6, color: "167, 139, 250" },
      { radius: 0.48, tiltX: 0.88, tiltZ: 0.10, speed: 0.005, phase: 3.2, color: "34, 211, 238" },
    ],
    lastFrame: 0,
    themeLight: false,
  };

  function resize() {
    state.dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    state.width = window.innerWidth;
    state.height = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(state.width * state.dpr));
    canvas.height = Math.max(1, Math.floor(state.height * state.dpr));
    canvas.style.width = `${state.width}px`;
    canvas.style.height = `${state.height}px`;
    context.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);

    const count = state.width < 720 ? 34 : state.width < 1200 ? 58 : 78;
    if (state.particles.length !== count) {
      state.particles = Array.from({ length: count }, () => ({
        x: (Math.random() - 0.5) * 2.4,
        y: (Math.random() - 0.5) * 2.1,
        z: 0.35 + Math.random() * 2.4,
        drift: 0.012 + Math.random() * 0.03,
        size: 0.6 + Math.random() * 1.5,
        hue: Math.random() < 0.28 ? "167, 139, 250" : "56, 189, 248",
      }));
    }
  }

  function project(x, y, z) {
    const perspective = 1 / (1 + z * 0.55);
    return {
      x: state.width / 2 + x * Math.min(state.width, state.height) * 0.46 * perspective,
      y: state.height / 2 + y * Math.min(state.width, state.height) * 0.46 * perspective,
      scale: perspective,
    };
  }

  function drawRing(ring, seconds) {
    const angle = seconds * ring.speed + ring.phase;
    const base = Math.min(state.width, state.height) * ring.radius;
    const cosZ = Math.cos(ring.tiltZ);
    const sinZ = Math.sin(ring.tiltZ);
    const cosX = Math.cos(ring.tiltX);
    const sinX = Math.sin(ring.tiltX);
    const segments = 84;
    let started = false;

    context.beginPath();
    for (let i = 0; i <= segments; i += 1) {
      const theta = (i / segments) * Math.PI * 2;
      const px = Math.cos(theta) * base;
      const py = Math.sin(theta) * base * 0.42;

      // Rotate the flat ellipse in depth, then project it.
      const rx = px * cosZ - py * sinZ;
      const ry = px * sinZ + py * cosZ;
      const ry2 = ry * cosX;
      const rz = ry * sinX + Math.sin(theta + angle) * base * 0.18;
      const p = project(rx / base * 0.9, ry2 / base * 0.9, 1.2 + rz / base);

      if (!started) {
        context.moveTo(p.x, p.y);
        started = true;
      } else {
        context.lineTo(p.x, p.y);
      }
    }
    context.strokeStyle = `rgba(${ring.color}, ${state.themeLight ? 0.10 : 0.15})`;
    context.lineWidth = 1;
    context.stroke();

    // A brighter arc gives the rings a subtle metallic highlight.
    context.beginPath();
    for (let i = 0; i <= segments; i += 1) {
      const theta = (i / segments) * Math.PI * 2;
      const px = Math.cos(theta) * base;
      const py = Math.sin(theta) * base * 0.42;
      const rx = px * cosZ - py * sinZ;
      const ry = px * sinZ + py * cosZ;
      const ry2 = ry * cosX;
      const rz = ry * sinX + Math.sin(theta + angle) * base * 0.18;
      const p = project(rx / base * 0.9, ry2 / base * 0.9, 1.2 + rz / base);
      if (i === 0) context.moveTo(p.x, p.y);
      else context.lineTo(p.x, p.y);
    }
    context.strokeStyle = `rgba(${ring.color}, ${state.themeLight ? 0.16 : 0.24})`;
    context.lineWidth = 1.6;
    context.setLineDash([2, 10]);
    context.stroke();
    context.setLineDash([]);
  }

  function draw(seconds) {
    state.themeLight = currentTheme() === "light";
    context.clearRect(0, 0, state.width, state.height);

    const glow = context.createRadialGradient(
      state.width * 0.72,
      state.height * 0.34,
      0,
      state.width * 0.72,
      state.height * 0.34,
      Math.max(state.width, state.height) * 0.6
    );
    glow.addColorStop(0, state.themeLight ? "rgba(56, 189, 248, 0.05)" : "rgba(56, 189, 248, 0.075)");
    glow.addColorStop(0.55, state.themeLight ? "rgba(167, 139, 250, 0.025)" : "rgba(167, 139, 250, 0.04)");
    glow.addColorStop(1, "rgba(0, 0, 0, 0)");
    context.fillStyle = glow;
    context.fillRect(0, 0, state.width, state.height);

    state.rings.forEach((ring) => drawRing(ring, seconds));

    state.particles.forEach((particle) => {
      particle.z -= particle.drift * 0.01;
      if (particle.z < 0.22) {
        particle.z = 2.7;
        particle.x = (Math.random() - 0.5) * 2.4;
        particle.y = (Math.random() - 0.5) * 2.1;
      }
      const p = project(particle.x, particle.y, particle.z);
      if (p.x < -10 || p.x > state.width + 10 || p.y < -10 || p.y > state.height + 10) {
        return;
      }
      const alpha = Math.min(0.5, 0.5 * p.scale);
      context.beginPath();
      context.arc(p.x, p.y, particle.size * p.scale, 0, Math.PI * 2);
      context.fillStyle = `rgba(${particle.hue}, ${alpha})`;
      context.fill();
    });
  }

  function loop(timestamp) {
    window.requestAnimationFrame(loop);
    if (document.hidden) return;
    if (timestamp - state.lastFrame < 33) return; // ~30fps is plenty for ambience
    state.lastFrame = timestamp;
    draw(timestamp / 1000);
  }

  resize();
  window.addEventListener("resize", resize, { passive: true });
  window.requestAnimationFrame(loop);
}

/* ==========================================================================
   Optional pointer glow + particle trail (desktop, fine pointers only)
   ========================================================================== */

function initPointerGlow() {
  const layer = document.querySelector(".background-layer");
  if (!layer || typeof window.matchMedia !== "function") return;

  const finePointer = window.matchMedia("(pointer: fine)").matches;
  if (!finePointer || reducedMotion()) return;

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

function initCursorTrail() {
  if (typeof window.matchMedia !== "function") return;
  if (!window.matchMedia("(pointer: fine)").matches || reducedMotion()) return;

  const canvas = document.createElement("canvas");
  canvas.className = "cursor-fx";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);

  let context;
  try {
    context = canvas.getContext("2d");
  } catch (error) {
    canvas.remove();
    return;
  }
  if (!context) {
    canvas.remove();
    return;
  }

  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  let width = 0;
  let height = 0;
  let pointer = { x: -100, y: -100, active: false };
  let particles = [];
  let frame = null;
  let lastSpawn = 0;
  let idleTimer = null;

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function spawn() {
    if (!pointer.active) return;
    particles.push({
      x: pointer.x + (Math.random() - 0.5) * 8,
      y: pointer.y + (Math.random() - 0.5) * 8,
      vx: (Math.random() - 0.5) * 0.35,
      vy: 0.12 + Math.random() * 0.35,
      life: 1,
      size: 1 + Math.random() * 2,
      violet: Math.random() < 0.3,
    });
    if (particles.length > 18) particles.shift();
  }

  function draw() {
    context.clearRect(0, 0, width, height);
    if (pointer.active) {
      const glow = context.createRadialGradient(
        pointer.x, pointer.y, 0,
        pointer.x, pointer.y, 72
      );
      glow.addColorStop(0, "rgba(103, 232, 249, 0.12)");
      glow.addColorStop(0.45, "rgba(56, 189, 248, 0.05)");
      glow.addColorStop(1, "rgba(56, 189, 248, 0)");
      context.fillStyle = glow;
      context.beginPath();
      context.arc(pointer.x, pointer.y, 72, 0, Math.PI * 2);
      context.fill();
    }

    particles.forEach((particle) => {
      particle.life -= 0.028;
      particle.x += particle.vx;
      particle.y += particle.vy;
      if (particle.life <= 0) return;
      context.beginPath();
      context.arc(particle.x, particle.y, particle.size * particle.life, 0, Math.PI * 2);
      context.fillStyle = particle.violet
        ? `rgba(167, 139, 250, ${0.24 * particle.life})`
        : `rgba(103, 232, 249, ${0.28 * particle.life})`;
      context.fill();
    });

    particles = particles.filter((particle) => particle.life > 0);
    if (particles.length || pointer.active) {
      frame = window.requestAnimationFrame(draw);
    } else {
      frame = null;
    }
  }

  function ensureLoop() {
    if (frame === null) frame = window.requestAnimationFrame(draw);
  }

  window.addEventListener(
    "pointermove",
    (event) => {
      pointer = { x: event.clientX, y: event.clientY, active: true };
      if (document.hidden) return;
      const now = performance.now();
      if (now - lastSpawn > 22) {
        lastSpawn = now;
        spawn();
      }
      ensureLoop();

      // Stop requesting frames once the pointer has been still for a moment.
      if (idleTimer !== null) window.clearTimeout(idleTimer);
      idleTimer = window.setTimeout(() => {
        idleTimer = null;
        pointer.active = false;
        ensureLoop();
      }, 700);
    },
    { passive: true }
  );

  document.addEventListener("pointerleave", () => {
    pointer.active = false;
    if (idleTimer !== null) {
      window.clearTimeout(idleTimer);
      idleTimer = null;
    }
    ensureLoop();
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) ensureLoop();
  });

  resize();
  window.addEventListener("resize", resize, { passive: true });
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
      '<p class="text-muted" style="grid-column:1/-1;margin:0;">Indices could not be loaded. Use Retry above. No values were invented.</p>';
  }
  const retry = byId("homeRetry");
  if (retry) retry.addEventListener("click", homeErrorRetry);
}

function changeDirection(change) {
  const value = toFiniteNumber(change);
  if (value === null || value === 0) return "";
  return value > 0 ? "change-positive" : "change-negative";
}

function changeArrow(change) {
  const value = toFiniteNumber(change);
  if (value === null || value === 0) return "";
  return value > 0 ? "▲" : "▼";
}

function directionWord(change) {
  const value = toFiniteNumber(change);
  if (value === null) return "change unavailable";
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "unchanged";
}

function rangeMarkup(item) {
  const low = toFiniteNumber(item.low);
  const high = toFiniteNumber(item.high);
  const last = toFiniteNumber(item.last);
  if (low === null || high === null || last === null || high <= low) return "";
  const position = Math.max(0, Math.min(100, ((last - low) / (high - low)) * 100));
  return `
    <span class="range-track" aria-hidden="true">
      <span class="range-fill" style="width:${position.toFixed(1)}%"></span>
      <span class="range-marker" style="left:${position.toFixed(1)}%"></span>
    </span>
    <span class="range-labels" aria-hidden="true">
      <span>Low ${escapeHtml(fmtNumber(low))}</span>
      <span>High ${escapeHtml(fmtNumber(high))}</span>
    </span>
  `;
}

function indexCardMarkup(item, options) {
  const settings = options || {};
  const change = toFiniteNumber(item.change);
  const percent = toFiniteNumber(item.percentChange);
  const directionClass = changeDirection(change);
  const arrow = changeArrow(change);
  const name = String(item.name || item.symbol || "NSE index").trim();
  const symbol = String(item.symbol || "").trim();
  const subtitle =
    !symbol || name.toUpperCase() === symbol.toUpperCase() ? "NSE index" : symbol;
  const lastText = fmtNumber(item.last);
  const changeText =
    change === null
      ? "Change unavailable"
      : `${fmtSigned(change)} (${fmtSigned(percent)}%)`;
  const aria = `${name}, last ${lastText}, ${directionWord(change)} by ${change === null ? "an unavailable amount" : fmtSigned(change)}, ${percent === null ? "percentage change unavailable" : `${fmtSigned(percent)} percent`}. ${settings.actionLabel || ""}`;
  const foot = `${escapeHtml(fmtSessionTimestamp(settings.timestamp))}`;
  const inner = `
    <span class="index-card-top">
      <span class="index-name">${escapeHtml(name)}</span>
      <span class="index-symbol">${escapeHtml(subtitle)}</span>
    </span>
    <span class="index-value-row">
      <span class="index-value">${escapeHtml(lastText)}</span>
      <span class="change-chip ${directionClass}">
        <span aria-hidden="true">${arrow}</span>
        ${percent === null ? "—" : `${escapeHtml(fmtSigned(percent))}%`}
      </span>
    </span>
    <span class="index-change ${directionClass}">${escapeHtml(changeText)}</span>
    ${rangeMarkup(item)}
    <span class="index-foot">
      <span class="session-dot" aria-hidden="true"></span>
      NSE session · ${foot}
    </span>
  `;

  if (settings.href) {
    return `
      <a class="index-card index-card-rich" href="${escapeAttr(settings.href)}" aria-label="${escapeAttr(aria)}">
        ${inner}
        <span class="index-card-cta" aria-hidden="true">Open constituents →</span>
      </a>
    `;
  }

  return `
    <button type="button" class="index-card index-card-rich" data-symbol="${escapeAttr(symbol)}"
      aria-label="${escapeAttr(aria)}">
      ${inner}
      <span class="index-card-cta" aria-hidden="true">Show constituents →</span>
    </button>
  `;
}

function renderHomeBreadth(indices) {
  const panel = byId("breadthPanel");
  if (!panel) return;

  const usable = indices.filter(
    (item) => toFiniteNumber(item.percentChange) !== null
  );

  if (!usable.length) {
    panel.innerHTML =
      '<p class="text-muted" style="margin:0;">Breadth is unavailable because the provider did not return change values. Nothing was inferred.</p>';
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
  const timestamp = payload && payload.timestamp ? payload.timestamp : null;

  const meta = byId("indicesMeta");
  if (meta) {
    meta.textContent = indices.length
      ? `${indices.length} NSE indices · session ${fmtSessionTimestamp(timestamp)}`
      : "No indices returned for this session";
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
    .map((item) =>
      indexCardMarkup(item, {
        timestamp,
        href: `stocks.html?index=${encodeURIComponent(item.symbol || "")}`,
        actionLabel: "Open constituents.",
      })
    )
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
   Hero market snapshot — real /api/market/history data only
   ========================================================================== */

let heroChartInstance = null;

function heroPalette() {
  const light = currentTheme() === "light";
  return light
    ? {
        text: "#475569",
        grid: "rgba(15, 23, 42, 0.07)",
        border: "rgba(15, 23, 42, 0.16)",
        volumeUp: "rgba(21, 128, 61, 0.30)",
        volumeDown: "rgba(185, 28, 28, 0.26)",
      }
    : {
        text: "#8fa3c0",
        grid: "rgba(148, 163, 184, 0.08)",
        border: "rgba(148, 163, 184, 0.18)",
        volumeUp: "rgba(34, 197, 94, 0.34)",
        volumeDown: "rgba(248, 113, 113, 0.30)",
      };
}

function setHeroUnavailable(message) {
  const holder = byId("heroChart");
  const state = byId("heroChartState");
  const freshness = byId("heroFreshness");
  const last = byId("heroLast");
  const change = byId("heroChange");
  const note = byId("heroPriceNote");
  const source = byId("heroSource");
  const fetched = byId("heroFetched");
  const count = byId("heroAnomalyCount");
  const anomalyNote = byId("heroAnomalyNote");

  if (holder) {
    holder.innerHTML = `<div class="hero-chart-error" role="status">${escapeHtml(
      message
    )}<br><span>No illustrative prices were substituted.</span></div>`;
  }
  if (state) state.remove();
  if (freshness) {
    freshness.textContent = "Unavailable";
    freshness.className = "source-pill is-unavailable";
  }
  if (last) last.textContent = "—";
  if (change) {
    change.textContent = "No data";
    change.className = "hero-change";
  }
  if (note) note.textContent = "The real market snapshot could not be loaded.";
  if (source) source.textContent = "Source unavailable";
  if (fetched) fetched.textContent = "Nothing was fabricated";
  if (count) count.textContent = "—";
  if (anomalyNote) anomalyNote.textContent = "No data to evaluate";
}

async function initHeroChart() {
  const holder = byId("heroChart");
  if (!holder) return;

  if (!window.LightweightCharts || !window.ChartUtils) {
    setHeroUnavailable(
      "The local chart bundle or chart helpers could not be loaded, so the real snapshot was not drawn."
    );
    return;
  }

  let payload;
  try {
    payload = await fetchJson(
      `${API_BASE}/api/market/history?symbol=RELIANCE&exchange=NSE&period=3mo&interval=1d`,
      90000
    );
  } catch (error) {
    console.error("Hero snapshot failed:", error);
    const described = describeRequestError(error, "Loading the market snapshot");
    setHeroUnavailable(described.message);
    return;
  }

  const mapped = window.ChartUtils.mapBars(payload && payload.bars);
  if (!mapped || !mapped.bars.length) {
    setHeroUnavailable(
      "The backend returned no usable OHLCV bars for this window, so the snapshot could not be drawn."
    );
    return;
  }

  const lastBar = mapped.bars[mapped.bars.length - 1];
  const previous = mapped.bars.length > 1 ? mapped.bars[mapped.bars.length - 2] : null;
  const changeValue = previous ? lastBar.close - previous.close : null;
  const changePercent =
    previous && previous.close !== 0
      ? ((lastBar.close - previous.close) / previous.close) * 100
      : null;

  const title = byId("heroChartTitle");
  const last = byId("heroLast");
  const change = byId("heroChange");
  const note = byId("heroPriceNote");
  const freshness = byId("heroFreshness");
  const source = byId("heroSource");
  const fetched = byId("heroFetched");
  const count = byId("heroAnomalyCount");
  const anomalyNote = byId("heroAnomalyNote");

  if (title) {
    title.textContent = `${payload.symbol || "RELIANCE"} · ${payload.exchange || "NSE"}`;
  }
  if (last) last.textContent = fmtNumber(lastBar.close);
  if (change) {
    const positive = changeValue !== null && changeValue > 0;
    const negative = changeValue !== null && changeValue < 0;
    change.textContent =
      changeValue === null
        ? "Previous bar unavailable"
        : `${changeArrow(changeValue)} ${fmtSigned(changeValue)} (${fmtSigned(changePercent)}%)`;
    change.className = `hero-change ${
      positive ? "change-positive" : negative ? "change-negative" : ""
    }`;
  }
  if (note) {
    note.textContent = `Latest daily bar from ${mapped.bars.length} returned bars · ${window.ChartUtils.formatTime(
      lastBar.timestamp,
      payload.interval || "1d"
    )}`;
  }
  if (freshness) {
    freshness.textContent = "Daily bars · not live";
    freshness.className = "source-pill is-historical";
    freshness.title =
      "Daily bars can include the current, still-forming session while the market is open. They are not tick-by-tick live prices.";
  }
  if (source) {
    source.textContent = `Source: ${payload.source || "provider not specified"}`;
  }
  if (fetched) {
    fetched.textContent = payload.fetchedAt
      ? `Fetched ${fmtLocalDateTime(payload.fetchedAt)}`
      : "Fetch time unavailable";
  }

  // Real historical anomaly markers, if the backend returned any.
  let markerResult = null;
  try {
    markerResult = window.ChartUtils.buildAnomalyMarkers(
      payload.analysis,
      mapped.bars,
      payload.historical_anomalies
    );
  } catch (error) {
    markerResult = null;
  }
  const markers = markerResult && Array.isArray(markerResult.markers)
    ? markerResult.markers
    : [];
  if (count) {
    const marked = markerResult ? Number(markerResult.markedCandles) || 0 : 0;
    count.textContent = markers.length ? String(marked || markers.length) : "0";
  }
  if (anomalyNote) {
    if (markers.length) {
      anomalyNote.textContent = `${markers.length} placed detector point${
        markers.length === 1 ? "" : "s"
      } in this window`;
    } else if (payload.historical_anomalies === null) {
      anomalyNote.textContent = "Historical marker series unavailable";
    } else {
      anomalyNote.textContent = "No historical flags in this window";
    }
  }

  holder.innerHTML = "";
  const palette = heroPalette();
  const library = window.LightweightCharts;
  const width = Math.max(280, holder.clientWidth || 560);
  const height = Math.max(220, holder.clientHeight || 260);

  const chart = library.createChart(holder, {
    width,
    height,
    layout: {
      background: { type: library.ColorType.Solid, color: "rgba(0,0,0,0)" },
      textColor: palette.text,
      fontFamily: "Inter, Segoe UI, sans-serif",
      fontSize: 10,
    },
    grid: {
      vertLines: { color: palette.grid },
      horzLines: { color: palette.grid },
    },
    crosshair: {
      mode: library.CrosshairMode.Normal,
      vertLine: { color: "rgba(148, 163, 184, 0.4)", width: 1, style: 3, labelBackgroundColor: "#0f172a" },
      horzLine: { color: "rgba(148, 163, 184, 0.4)", width: 1, style: 3, labelBackgroundColor: "#0f172a" },
    },
    rightPriceScale: {
      borderColor: palette.border,
      scaleMargins: { top: 0.12, bottom: 0.30 },
    },
    timeScale: {
      borderColor: palette.border,
      timeVisible: false,
      secondsVisible: false,
      rightOffset: 3,
      barSpacing: 6,
    },
    handleScroll: false,
    handleScale: false,
    localization: {
      priceFormatter: (price) => window.ChartUtils.formatPrice(price, 2),
    },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#22d3ee",
    downColor: "#a78bfa",
    borderUpColor: "#67e8f9",
    borderDownColor: "#c4b5fd",
    wickUpColor: "rgba(103, 232, 249, 0.7)",
    wickDownColor: "rgba(196, 181, 253, 0.7)",
    priceLineVisible: false,
    lastValueVisible: true,
  });

  candleSeries.setData(
    mapped.bars.map((bar) => ({
      time: bar.time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }))
  );

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
    priceLineVisible: false,
    lastValueVisible: false,
  });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  volumeSeries.setData(
    mapped.bars
      .filter((bar) => bar.volume !== null)
      .map((bar) => ({
        time: bar.time,
        value: bar.volume,
        color: bar.close >= bar.open ? palette.volumeUp : palette.volumeDown,
      }))
  );

  if (markers.length) {
    candleSeries.setMarkers(
      markers.map((marker) => Object.assign({}, marker, { text: "" }))
    );
  }

  chart.timeScale().fitContent();
  heroChartInstance = chart;

  holder.setAttribute(
    "aria-label",
    `Real ${mapped.bars.length}-bar daily ${payload.symbol} price and volume chart from ${payload.source || "the backend"}. Latest close ${fmtNumber(
      lastBar.close
    )}. ${markers.length} anomaly marker${markers.length === 1 ? "" : "s"} shown.`
  );

  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(() => {
      if (!heroChartInstance) return;
      heroChartInstance.applyOptions({
        width: Math.max(280, holder.clientWidth || width),
        height: Math.max(220, holder.clientHeight || height),
      });
    });
    observer.observe(holder);
  }

  window.addEventListener("themechange", () => {
    const next = heroPalette();
    if (!heroChartInstance) return;
    heroChartInstance.applyOptions({
      layout: { textColor: next.text },
      grid: {
        vertLines: { color: next.grid },
        horzLines: { color: next.grid },
      },
      rightPriceScale: { borderColor: next.border },
      timeScale: { borderColor: next.border },
    });
  });
}

/* ==========================================================================
   Latest market news — real backend RSS proxy
   ========================================================================== */

function newsErrorState(error) {
  const status = byId("newsStatus");
  const grid = byId("newsGrid");
  if (!status) return;
  const described = describeRequestError(error, "Loading market news");
  const message =
    error && error.status === 503
      ? "Market news is temporarily unavailable from the publisher feed. No headlines were invented."
      : described.message;
  status.className = "news-status is-error";
  status.innerHTML = `${escapeHtml(message)} <button type="button" class="retry-btn" id="newsRetry">Retry</button>`;
  if (grid) grid.innerHTML = "";
  const retry = byId("newsRetry");
  if (retry) retry.addEventListener("click", initMarketNews);
}

function renderNews(payload) {
  const grid = byId("newsGrid");
  const status = byId("newsStatus");
  const source = byId("newsSource");
  const retrieved = byId("newsRetrieved");
  if (!grid || !status) return;

  const articles = Array.isArray(payload && payload.articles)
    ? payload.articles.filter(
        (article) =>
          article &&
          typeof article.title === "string" &&
          /^https?:\/\//.test(String(article.link || ""))
      )
    : [];

  status.className = "news-status";
  if (source) {
    source.textContent = payload && payload.source
      ? `${payload.source} · publisher feed`
      : "Publisher feed via the backend";
  }
  if (retrieved) {
    const bits = [];
    if (payload && payload.fetchedAt) {
      bits.push(`Retrieved by the backend ${fmtLocalDateTime(payload.fetchedAt)}`);
    }
    if (payload && payload.cached) bits.push("served from a short backend cache");
    bits.push("Publication times belong to the publisher.");
    retrieved.textContent = bits.join(" · ");
  }

  if (!articles.length) {
    status.innerHTML = "The publisher feed returned no usable headlines. Nothing was fabricated.";
    grid.innerHTML = "";
    return;
  }

  status.textContent = "";
  status.className = "news-status hidden";

  grid.innerHTML = articles
    .slice(0, 6)
    .map(
      (article, index) => `
        <article class="news-card${index === 0 ? " news-card-featured" : ""}">
          <a class="news-link" href="${escapeAttr(article.link)}" target="_blank" rel="noopener noreferrer">
            <span class="news-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span>
            <h3>${escapeHtml(article.title)}</h3>
            <span class="news-open" aria-hidden="true">Read at source ↗</span>
          </a>
          <div class="news-meta">
            <span class="news-publisher">${escapeHtml(article.publisher || "Publisher")}</span>
            <span class="news-dot" aria-hidden="true"></span>
            <time datetime="${escapeAttr(article.publishedAt || "")}">${escapeHtml(
              fmtPublishedTime(article.publishedAt)
            )}</time>
          </div>
        </article>
      `
    )
    .join("");
}

async function initMarketNews() {
  const grid = byId("newsGrid");
  const status = byId("newsStatus");
  if (!grid || !status) return;

  status.className = "news-status";
  status.innerHTML = '<span class="spinner" aria-hidden="true"></span> Loading real headlines…';
  grid.innerHTML = "";

  try {
    const payload = await fetchJson(`${API_BASE}/api/news?limit=6`, 25000);
    renderNews(payload);
  } catch (error) {
    console.error("Market news failed:", error);
    newsErrorState(error);
  }
}

/* ==========================================================================
   Recent analyses — genuine local device history
   ========================================================================== */

const RECENT_ANALYSES_KEY = "clean-data-recent-analyses";
const RECENT_LIMIT = 8;

function readRecentAnalyses() {
  try {
    const raw = localStorage.getItem(RECENT_ANALYSES_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item) =>
          item &&
          typeof item.symbol === "string" &&
          item.symbol.trim() &&
          typeof item.savedAt === "string"
      )
      .sort((a, b) => {
        const left = parseDateValue(a.savedAt);
        const right = parseDateValue(b.savedAt);
        return (right ? right.getTime() : 0) - (left ? left.getTime() : 0);
      })
      .slice(0, RECENT_LIMIT);
  } catch (error) {
    return [];
  }
}

function recordRecentAnalysis(entry) {
  if (!entry || typeof entry.symbol !== "string" || !entry.symbol.trim()) return;
  const symbol = entry.symbol.trim().toUpperCase();
  const exchange = entry.exchange === "BSE" ? "BSE" : "NSE";
  const period = String(entry.period || "3mo");
  const interval = String(entry.interval || "1d");
  const key = [symbol, exchange, period, interval].join("|");

  const record = {
    symbol,
    exchange,
    period,
    interval,
    source: typeof entry.source === "string" ? entry.source : "",
    fetchedAt: entry.fetchedAt || null,
    barTimestamp: entry.barTimestamp || null,
    lastClose: toFiniteNumber(entry.lastClose),
    changePercent: toFiniteNumber(entry.changePercent),
    savedAt: new Date().toISOString(),
  };

  try {
    const existing = readRecentAnalyses().filter(
      (item) =>
        [item.symbol, item.exchange, item.period, item.interval].join("|") !== key
    );
    const next = [record, ...existing].slice(0, RECENT_LIMIT);
    localStorage.setItem(RECENT_ANALYSES_KEY, JSON.stringify(next));
  } catch (error) {
    /* localStorage may be unavailable; analysis still works. */
  }
}

function renderRecentAnalyses() {
  const grid = byId("recentGrid");
  if (!grid) return;
  const entries = readRecentAnalyses();
  const empty = byId("recentEmpty");
  if (empty) empty.classList.toggle("hidden", entries.length > 0);

  if (!entries.length) {
    grid.innerHTML = "";
    return;
  }

  grid.innerHTML = entries
    .slice(0, 4)
    .map((entry) => {
      const direction = changeDirection(entry.changePercent);
      const params = new URLSearchParams({
        symbol: entry.symbol,
        exchange: entry.exchange || "NSE",
        period: entry.period || "3mo",
        interval: entry.interval || "1d",
        autorun: "1",
      });
      return `
        <article class="recent-card">
          <div class="recent-top">
            <span class="recent-symbol">${escapeHtml(entry.symbol)}</span>
            <span class="exchange-pill">${escapeHtml(entry.exchange || "NSE")}</span>
          </div>
          <p class="recent-window">${escapeHtml(periodLabelSafe(entry.period))} · ${escapeHtml(
            intervalLabelSafe(entry.interval)
          )}</p>
          <div class="recent-values">
            <span class="recent-price">${entry.lastClose === null ? "—" : fmtNumber(entry.lastClose)}</span>
            <span class="change-chip ${direction}">${
              entry.changePercent === null ? "—" : `${escapeHtml(fmtSigned(entry.changePercent))}%`
            }</span>
          </div>
          <p class="recent-meta">
            Last saved close · ${escapeHtml(timeAgo(entry.savedAt))}
          </p>
          <a class="recent-open" href="stock-analysis.html?${params.toString()}">
            Open analysis →
          </a>
        </article>
      `;
    })
    .join("");
}

/* ==========================================================================
   Feedback form — real POST /api/feedback submission
   ========================================================================== */

function setStarRating(value) {
  const rating = byId("ratingValue");
  const stars = document.querySelectorAll("#starRating .star");
  const hint = byId("ratingHint");
  if (rating) rating.value = String(value);
  stars.forEach((star) => {
    const starValue = Number(star.getAttribute("data-value"));
    const active = starValue <= value;
    star.classList.toggle("is-active", active);
    star.setAttribute("aria-checked", String(starValue === value));
    star.setAttribute("tabindex", starValue === value || (!value && starValue === 1) ? "0" : "-1");
  });
  if (hint) {
    hint.textContent = value ? `${value} of 5 star${value === 1 ? "" : "s"} selected` : "Select 1–5 stars";
  }
}

function initStarRating() {
  const group = byId("starRating");
  if (!group) return;
  const stars = Array.from(group.querySelectorAll(".star"));
  if (!stars.length) return;

  setStarRating(0);

  stars.forEach((star) => {
    const value = Number(star.getAttribute("data-value"));
    star.addEventListener("click", () => setStarRating(value));
    star.addEventListener("mouseenter", () => {
      stars.forEach((item) => {
        item.classList.toggle(
          "is-preview",
          Number(item.getAttribute("data-value")) <= value
        );
      });
    });
    star.addEventListener("mouseleave", () => {
      stars.forEach((item) => item.classList.remove("is-preview"));
    });
    star.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = Math.min(5, value + 1);
        setStarRating(next);
        const target = group.querySelector(`.star[data-value="${next}"]`);
        if (target) target.focus();
      } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
        event.preventDefault();
        const next = Math.max(1, value - 1);
        setStarRating(next);
        const target = group.querySelector(`.star[data-value="${next}"]`);
        if (target) target.focus();
      }
    });
  });
}

function setFeedbackStatus(kind, message) {
  const status = byId("feedbackStatus");
  if (!status) return;
  status.className = `feedback-status ${kind}`;
  status.textContent = message;
}

function initFeedbackForm() {
  const form = byId("feedbackForm");
  if (!form) return;

  const name = byId("feedbackName");
  const email = byId("feedbackEmail");
  const message = byId("feedbackMessage");
  const rating = byId("ratingValue");
  const submit = byId("feedbackSubmit");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!message || !rating) return;

    const ratingNumber = Number(rating.value);
    const messageText = message.value.trim();
    const emailText = email ? email.value.trim() : "";

    message.removeAttribute("aria-invalid");

    if (!ratingNumber) {
      setFeedbackStatus("error", "Choose a star rating from 1 to 5 before submitting.");
      const firstStar = document.querySelector("#starRating .star");
      if (firstStar) firstStar.focus();
      return;
    }
    if (messageText.length < 10) {
      message.setAttribute("aria-invalid", "true");
      setFeedbackStatus("error", "Please write at least 10 characters so the feedback is useful.");
      message.focus();
      return;
    }
    if (emailText && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailText)) {
      if (email) email.setAttribute("aria-invalid", "true");
      setFeedbackStatus("error", "The email address does not look valid. It is optional, so you can leave it blank.");
      if (email) email.focus();
      return;
    }

    if (submit) {
      submit.disabled = true;
      submit.textContent = "Submitting…";
      submit.setAttribute("aria-busy", "true");
    }
    setFeedbackStatus("pending", "Storing feedback on the backend…");

    try {
      const payload = await postJson(
        `${API_BASE}/api/feedback`,
        {
          name: name ? name.value.trim() : "",
          email: emailText,
          rating: ratingNumber,
          message: messageText,
          page: "home",
        },
        20000
      );

      setFeedbackStatus(
        "success",
        `Feedback stored on the backend at ${fmtLocalDateTime(
          payload && payload.storedAt
        )}. It was not emailed${
          payload && payload.persistence ? ` — ${payload.persistence}` : "."
        }`
      );
      form.reset();
      setStarRating(0);
      if (name) name.removeAttribute("aria-invalid");
      if (email) email.removeAttribute("aria-invalid");
      message.removeAttribute("aria-invalid");
    } catch (error) {
      console.error("Feedback submission failed:", error);
      let detail = error && error.message ? error.message : "The request failed.";
      if (error instanceof TypeError) {
        detail = `Cannot reach the FastAPI backend at ${API_BASE}. Your feedback was not submitted.`;
      } else if (error && error.name === "TimeoutError") {
        detail = "The backend did not answer in time. Your feedback was not submitted.";
      }
      setFeedbackStatus("error", detail);
    } finally {
      if (submit) {
        submit.disabled = false;
        submit.textContent = "Submit feedback";
        submit.removeAttribute("aria-busy");
      }
    }
  });

  [name, email, message].forEach((field) => {
    if (!field) return;
    field.addEventListener("input", () => field.removeAttribute("aria-invalid"));
  });
}

/* ==========================================================================
   Stocks page
   ========================================================================== */

const INDEX_PREVIEW_COUNT = 12;

const stocksState = {
  indices: [],
  indicesTimestamp: null,
  loading: false,
  filter: "",
  sort: "default",
  showAllIndices: false,
  selectedIndex: null,
  constituents: [],
  stockFilter: "",
  stockSort: "default",
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

  const filtered = filteredIndices();
  const total = stocksState.indices.length;
  const query = stocksState.filter.trim();
  const showAll = stocksState.showAllIndices || Boolean(query);
  const visible = showAll ? filtered : filtered.slice(0, INDEX_PREVIEW_COUNT);

  if (count) {
    count.textContent = query
      ? `${filtered.length} matching of ${total} indices`
      : showAll
        ? `${total} indices`
        : `${Math.min(INDEX_PREVIEW_COUNT, total)} of ${total} indices`;
  }

  const toggle = byId("indicesToggle");
  if (toggle) {
    const canToggle = !query && filtered.length > INDEX_PREVIEW_COUNT;
    toggle.classList.toggle("hidden", !canToggle);
    toggle.setAttribute("aria-expanded", String(stocksState.showAllIndices));
    toggle.textContent = stocksState.showAllIndices
      ? `Show first ${INDEX_PREVIEW_COUNT}`
      : `Show all ${filtered.length} indices`;
  }

  if (!visible.length) {
    container.innerHTML = `
      <div class="empty-state">
        <p><strong>No indices match your filter.</strong></p>
        <p class="text-muted">Clear the search box to see all ${stocksState.indices.length} NSE indices. Nothing was hidden permanently.</p>
      </div>
    `;
    container.classList.remove("hidden");
    return;
  }

  container.innerHTML = visible
    .map((item) =>
      indexCardMarkup(item, {
        timestamp: stocksState.indicesTimestamp,
        actionLabel: "Open constituents.",
      })
    )
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
    stocksState.indicesTimestamp = payload ? payload.timestamp : null;
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

    const requested = new URLSearchParams(window.location.search).get("index");
    if (requested) {
      loadConstituents(requested);
    }
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

function sortConstituents(list) {
  const sorted = [...list];
  if (stocksState.stockSort === "gainers") {
    sorted.sort(
      (a, b) =>
        (toFiniteNumber(b.percentChange) ?? -Infinity) -
        (toFiniteNumber(a.percentChange) ?? -Infinity)
    );
  } else if (stocksState.stockSort === "losers") {
    sorted.sort(
      (a, b) =>
        (toFiniteNumber(a.percentChange) ?? Infinity) -
        (toFiniteNumber(b.percentChange) ?? Infinity)
    );
  } else if (stocksState.stockSort === "price") {
    sorted.sort(
      (a, b) => (toFiniteNumber(b.last) ?? -Infinity) - (toFiniteNumber(a.last) ?? -Infinity)
    );
  } else if (stocksState.stockSort === "name") {
    sorted.sort((a, b) =>
      String(a.symbol || "").localeCompare(String(b.symbol || ""))
    );
  }
  return sorted;
}

function filteredConstituents() {
  const query = stocksState.stockFilter.trim().toUpperCase();
  const source = !query
    ? stocksState.constituents
    : stocksState.constituents.filter((stock) => {
        const symbol = String(stock.symbol || "").toUpperCase();
        const name = `${stock.name || ""} ${displayConstituentName(
          stock
        )}`.toUpperCase();
        return symbol.includes(query) || name.includes(query);
      });
  return sortConstituents(source);
}

function renderConstituents() {
  const listEl = byId("stocksList");
  const shown = byId("stockShown");
  if (!listEl) return;

  const all = stocksState.constituents;
  const visible = filteredConstituents();

  if (shown) {
    shown.textContent = all.length
      ? `${visible.length} of ${all.length} stocks`
      : "No stocks available";
  }

  if (!all.length) {
    listEl.innerHTML =
      '<li class="text-muted stock-empty">NSE India returned no constituent stocks for this index. Nothing was fabricated.</li>';
    return;
  }

  if (!visible.length) {
    listEl.innerHTML = `
      <li class="stock-empty">
        <strong>No stocks match “${escapeHtml(stocksState.stockFilter.trim())}”.</strong>
        <span class="text-muted">Clear the stock filter to see all ${all.length} constituents.</span>
      </li>
    `;
    return;
  }

  listEl.innerHTML = visible
    .map((stock) => {
      const change = toFiniteNumber(stock.change);
      const percent = toFiniteNumber(stock.percentChange);
      const direction = changeDirection(change);
      const symbol = String(stock.symbol || "");
      const href = `stock-analysis.html?symbol=${encodeURIComponent(symbol)}&exchange=NSE`;
      return `
        <li>
          <a class="stock-row" href="${href}"
            aria-label="Analyse ${escapeAttr(symbol)} on NSE">
            <span class="stock-symbol">${escapeHtml(symbol)}</span>
            <span class="stock-name" title="NSE identifier: ${escapeAttr(
              stock.name || symbol
            )}">${escapeHtml(displayConstituentName(stock))}</span>
            <span class="stock-price">${fmtNumber(stock.last)}</span>
            <span class="change ${direction}">
              ${change === null ? "—" : `${escapeHtml(fmtSigned(change))} (${escapeHtml(fmtSigned(percent))}%)`}
            </span>
            <span class="stock-action" aria-hidden="true">Analyse →</span>
          </a>
        </li>
      `;
    })
    .join("");
}

async function loadConstituents(indexSymbol) {
  if (!indexSymbol) return;

  const container = byId("indices-container");
  const detail = byId("stock-detail");
  const titleEl = byId("detailTitle");
  const metaEl = byId("detailMeta");
  const countEl = byId("detailCount");
  const listEl = byId("stocksList");
  const filterEl = byId("stockFilter");
  const sortEl = byId("stockSort");

  if (!detail || !container || !listEl) return;

  stocksState.selectedIndex = indexSymbol;
  stocksState.constituents = [];
  stocksState.stockFilter = "";
  stocksState.stockSort = "default";
  if (filterEl) filterEl.value = "";
  if (sortEl) sortEl.value = "default";
  hideStocksError();
  container.classList.add("hidden");
  detail.classList.remove("hidden");

  if (titleEl) titleEl.textContent = indexSymbol;
  if (metaEl) metaEl.textContent = "Loading constituent stocks…";
  if (countEl) countEl.textContent = "";
  if (byId("stockShown")) byId("stockShown").textContent = "";
  listEl.innerHTML = '<li class="loading" style="min-height:80px;">Loading stocks…</li>';

  detail.scrollIntoView({
    behavior: reducedMotion() ? "auto" : "smooth",
    block: "start",
  });

  try {
    const data = await fetchJson(
      `${API_BASE}/api/indices/${encodeURIComponent(indexSymbol)}/constituents`,
      30000
    );

    const summary = data && data.index ? data.index : {};
    const constituents = Array.isArray(data && data.constituents)
      ? data.constituents.filter((stock) => stock && stock.symbol)
      : [];
    const market = (data && data.marketStatus) || {};
    stocksState.constituents = constituents;

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
        `Source: NSE India${data && data.timestamp ? ` (${fmtSessionTimestamp(data.timestamp)})` : ""}`;
    }

    if (countEl) {
      countEl.textContent = `${constituents.length} constituent stocks`;
    }

    renderConstituents();
    detail.scrollIntoView({
      behavior: reducedMotion() ? "auto" : "smooth",
      block: "start",
    });
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
  const stockFilter = byId("stockFilter");
  const stockSort = byId("stockSort");

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

  const indicesToggle = byId("indicesToggle");
  if (indicesToggle) {
    indicesToggle.addEventListener("click", () => {
      stocksState.showAllIndices = !stocksState.showAllIndices;
      renderIndices();
    });
  }

  if (stockFilter) {
    stockFilter.addEventListener("input", () => {
      stocksState.stockFilter = stockFilter.value;
      renderConstituents();
    });
  }

  if (stockSort) {
    stockSort.addEventListener("change", () => {
      stocksState.stockSort = stockSort.value;
      renderConstituents();
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
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete("index");
        window.history.replaceState(null, "", url.pathname + url.search);
      } catch (error) {
        /* embedded browsers may reject history updates */
      }
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
      message.textContent = `${symbol} added. Symbols are stored in this browser only; prices are fetched from the backend when you open an analysis.`;
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
  initNav();
  initAmbientCanvas();
  initPointerGlow();
  initCursorTrail();
  initHomepageButtons();

  if (byId("overviewGrid")) {
    loadHomeIndices();
  }

  if (byId("heroChart")) {
    initHeroChart();
  }

  if (byId("newsGrid")) {
    initMarketNews();
  }

  if (byId("recentGrid")) {
    renderRecentAnalyses();
  }

  if (byId("feedbackForm")) {
    initStarRating();
    initFeedbackForm();
  }

  if (byId("indices-container")) {
    initStocksPage();
  }

  if (byId("wishlistList")) {
    initWishlistPage();
  }
});
