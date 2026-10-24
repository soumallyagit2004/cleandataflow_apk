# Clean Data Flow — Stock Analyser

Clean Data Flow is a data-driven stock-analysis website: real historical
OHLCV data from Yahoo Finance, explainable technical indicators, honest
anomaly detectors, and premium glassmorphism UI — vanilla HTML/CSS/JS on the
frontend and Python FastAPI on the backend. No price is ever invented: when a
detector or provider cannot answer, the UI says so instead of guessing.

---

## 1. What it does

| Page (`frontend/`) | Function |
|---|---|
| `index.html` | Market overview: NSE index cards and market breadth from real `/api/indices` data |
| `stocks.html` | NSE index explorer with constituent tables |
| `stock-analysis.html` | The Stock Analyser: chart + indicators + anomaly detectors, timeframe switching (1D, 5D, 1M, 3M, 6M, 1Y, 5Y) |

Detectors (all explainable, all with honest availability/limitation metadata):

* SMA, EMA, RSI, Fibonacci retracement, Bollinger Bands (with squeeze detection and breakout markers)
* EMA Deviation
* Volume Spike, Volatility Anomaly, Volume-Price Divergence —
  **and walk-forward historical versions** of these indicators that mark the exact
  historical candles, causally
* Local Outlier Factor (LOF), Transformer anomaly (loaded via Phase 2) —
  latest-complete-bar only (declared honestly on the page)
* NSE open interest via the official NSE F&O end-of-day bhavcopy
  (`/api/market/oi`)

## 2. Project structure

```
backend/
  main.py          FastAPI routes (health, market/history, market/oi, indices,
                   index constituents, news, feedback)
  market_data.py   Yahoo Finance provider + period/interval validation
  analysis.py      Indicators, detectors, historical walk-forward generators
  oi_data.py       Official NSE F&O bhavcopy parsing
  data/            Local data: feedback.jsonl (+ the leftover local app.db, gitignored)
                   (never committed; *db also gitignored)
  tests/           pytest suite (endpoints + analysis + OI + market data)
  requirements.txt Pinned dependencies
  Procfile         Production web process (Render/Railway/Koyeb-style hosts)
  .python-version  Python 3.12
  .env.example     Placeholder environment configuration (no secrets)
frontend/
  index.html       Market overview
  stocks.html      Index explorer
  stock-analysis.html  The analyser page
  config.js        The single place the backend URL (window.CDF_API_BASE) is set
  script.js        Shared runtime: API base, theme, home/stocks logic
  analysis.js      Analyser page logic (requests, auto-complete, timeframes)
  chart-utils.js   Chart rendering, markers, request guard, URL builder
  style.css        Glassmorphism design system (dark + light themes)
  vendor/          Vendored TradingView Lightweight Charts v4.2.3 (+ license)
  tests/           Node chart-utils tests + check_frontend.py structure check
frontend/vercel.json  Static hosting configuration (cache + security headers)
```

## 3. Prerequisites

* Python 3.12 (anything ≥ 3.10 works; 3.12 is what production targets)
* Any Python web host or a terminal
* The frontend is fully static — no build step

## 4. Backend installation (local)

```bat
cd backend
python -m pip install -r requirements.txt
```

## 5. Running locally (unchanged commands)

Backend (terminal 1):

```bat
cd C:\Users\naska\OneDrive\Desktop\newapk\backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Frontend (terminal 2):

```bat
cd C:\Users\naska\OneDrive\Desktop\newapk\frontend
python -m http.server 5500
```

Local pages:

* <http://localhost:5500/>
* <http://localhost:5500/stocks.html>
* <http://localhost:5500/stock-analysis.html>

Local backend health check (exact contract, do not change):

```
GET http://127.0.0.1:8000/api/health
{"status":"success","message":"Clean Data Flow backend is running"}
```

## 6. Environment variables

### 6a. Required for production

| Variable | Where | Meaning |
|---|---|---|
| `ALLOWED_ORIGINS` | backend | **Must set in production.** Comma-separated list of frontend origins allowed by CORS. Unset = local dev only (localhost/127.0.0.1). Example: `https://your-site.vercel.app`. |
| `PORT` | backend (host) | Procfile hosts supply it automatically via `$PORT`; only needed when running uvicorn by hand. |

### 6b. Security hardening (recommended for production)

| Variable | Default | Meaning |
|---|---|---|
| `CDF_RATE_LIMIT_ENABLED` | `1` (on) | Per-IP request rate limiter. Set `0` to disable (only for local testing). |
| `CDF_ENABLE_DOCS` | `1` (visible) | Set `0` to hide `/docs`, `/redoc` and `/openapi.json` on a public deployment. |
| `CDF_FORCE_HSTS` | `0` (off) | Set `1` to add `Strict-Transport-Security` headers — only after the domain is HTTPS-only. |
| `CDF_TRUST_PROXY_HEADERS` | `0` (off) | Set `1` on hosts (e.g. Render) whose proxy **always** rewrites `X-Forwarded-For`, so each real visitor gets their own rate-limit bucket. |
| `CDF_TRUSTED_HOSTS` | unset | Comma-separated list of allowed `Host` header values (e.g. `your-backend.onrender.com`). Prevents host-header injection. |
| `FEEDBACK_FILE` | `backend/data/feedback.jsonl` | Override the feedback storage path. On hosts with **ephemeral filesystems** (Render free tier) this file is **lost on every redeploy** — see Section 11 for the workaround. |

### 6c. Market data providers (optional)

| Variable | Where | Meaning |
|---|---|---|
| `CDF_TR_CACHE` | backend | `0`/`false`/`off` disables the Transformer result cache (default: enabled). |
| `CDF_TR_CACHE_TTL` | backend | Transformer cache entry lifetime in seconds (default `900`). Correctness never depends on it: any new bar retrains via the cache key. |
| `TWELVEDATA_API_KEY` | backend | Optional. Enables the Twelve Data provider. Server-side only. Free "Basic" plan: 8 credits/min, 800/day — **US markets/forex/crypto only; India needs a paid plan** (see Section 7). |
| `ALPHAVANTAGE_API_KEY` | backend | Optional. Enables the Alpha Vantage provider. Server-side only. Free tier: **25 requests/day, 5/min**; verified for BSE daily/weekly/monthly. |
| `TWELVEDATA_ENABLE_INDIA` | backend | Default `0`. Set `1` only with a Twelve Data plan that genuinely covers NSE/BSE. |
| `ALPHAVANTAGE_ENABLE_NSE` | backend | Default `0`. Set `1` only after live-verifying NSE history with your own key. |
| `ALPHAVANTAGE_ENABLE_INTRADAY` | backend | Default `0`. Alpha Vantage intraday stays off; daily/weekly/monthly are the verified capability. |
| `CDF_MARKET_ROUTING` | backend | `round_robin` (default), `weighted` or `first_available` distribution across the enabled providers. |
| `CDF_PROVIDER_WEIGHT_<NAME>` | backend | Weighted-mode weights (`_YAHOO`, `_TWELVEDATA`, `_ALPHAVANTAGE`; defaults 60/25/15). |
| `CDF_ENABLE_YAHOO` / `CDF_ENABLE_TWELVEDATA` / `CDF_ENABLE_ALPHAVANTAGE` | backend | Per-provider on/off overrides (default `1`; missing credentials already disable a provider). |
| `TWELVEDATA_MAX_REQUESTS_PER_MINUTE` | backend | Client-side credit budget (default `8`, the free plan limit). |
| `ALPHAVANTAGE_MAX_REQUESTS_PER_DAY` / `_PER_MINUTE` | backend | Client-side quota budgets (defaults `25` / `5`). |
| `CDF_MARKET_CACHE` | backend | `0` disables the shared market cache entirely (default: enabled). |
| `CDF_MARKET_CACHE_MAX_ENTRIES` | backend | Shared cache LRU bound (default `512`). |
| `CDF_MARKET_STALE_FALLBACK` | backend | When every provider fails, serve an existing cache entry clearly labelled stale (default `1`). |
| `CDF_MARKET_STALE_MAX_HOURS` | backend | Maximum age of a stale fallback payload in hours (default `24`). |
| — | frontend | There are **no secrets in the frontend**. The backend URL is the only frontend setting: `frontend/config.js` → `window.CDF_API_BASE`. |

Copy `backend/.env.example` to `backend/.env` if you want file-based env config
locally (never commit the real `.env`).

## 7. Multi-provider market data

Market-data requests are no longer hard-wired to Yahoo Finance. A central
manager (`market_manager.py`) routes every request to exactly one eligible
provider, with automatic fallback when a provider fails — different symbols,
exchanges, intervals or history windows are never treated as interchangeable,
and no request ever fetches the same candles from multiple providers.

### Providers, verified coverage and defaults

| Provider | Verified coverage (from official docs) | Default state | Notes |
|---|---|---|---|
| **Yahoo Finance** (`yfinance`) | What shipped originally: NSE (`.NS`) and BSE (`.BO`) via exchange suffixes; intervals 1m…1mo (`INTERVAL_PERIODS` in `market_data.py`), including Yahoo-only extras (2m, 90m, 5d, 3mo). | Always on, priority default | Unchanged behaviour, same `source`/`freshness` strings. |
| **Twelve Data** | Free "Basic" plan: 8 credits/min, 800/day, but only **3 market groups (US/forex/crypto)** — India needs the paid Ultra plan. Adapter supports intervals 1m, 5m, 15m, 30m, 60m/1h, 1d, 1wk, 1mo. | **India disabled by default** (`TWELVEDATA_ENABLE_INDIA=0`). Activate only with a plan that covers NSE/BSE. Plan-coverage errors are classified at runtime and the manager falls back on the same request. |
| **Alpha Vantage** | Free tier: **25 req/day, 5/min**. Official India examples verify **BSE daily/weekly/monthly** (`RELIANCE.BSE`). `.NSE` is NOT verified (official docs only show BSE; third-party reports of NSE failing); free-tier intraday for India is plan-dependent. | **Enabled for BSE daily/weekly/monthly** once a key exists; **NSE disabled by default** (`ALPHAVANTAGE_ENABLE_NSE=0`), **intraday disabled by default** (`ALPHAVANTAGE_ENABLE_INTRADAY=0`). |

Nothing pretends a provider can do what it cannot: capability checks run
*before* a request is sent, unsupported stays disabled, and errors are mapped
honestly (auth → hard cooldown, quota → short cooldown, plan/market gaps →
capability disable, bad payloads → try the next provider).

### Routing, quota awareness, fallback

* Routing modes: `round_robin` (default, rotates per exchange+interval slot),
  `weighted` (deterministic weighted rotation via `CDF_PROVIDER_WEIGHT_*`,
  defaults 60/25/15), `first_available` (registry order, fallback only).
* Client-side quota budgeting for keyed providers (Twelve Data 8/min;
  Alpha Vantage 25/day + 5/min) — a provider whose budget is spent is skipped
  for that request instead of burning rejected hits upstream.
* Health tracking: consecutive-failure backoff (30 s → 300 s cap at ≥3
  failures), 120 s cooldown after rate-limit hits, 6 h cooldown after an auth
  rejection. A smoothed latency average feeds diagnostics (`snapshot()`).
* Recovery-fallback order: one provider at a time, both failures recorded in
  the response/error text. Parallel calls are not needed in the normal flow.

### Shared cache (no external infrastructure; plain process memory + LRU)

* Keys contain the exact `(symbol, exchange, interval, period)` request.
* TTLs follow Indian market hours (09:15–15:30 IST Mon–Fri): in-session
  intraday 60 s, daily+ 15 min; after close until the next session start,
  capped at 12 h (intraday) / 72 h (daily). Weekends never renew.
* Single-flight: concurrent identical requests share one upstream call.
* Failed fetches are never cached as data. When *every* provider fails and a
  previous entry exists, it is served under the explicit stale policy and
  labelled: `stale: true`, `freshness: "stale: …"`, `source: "… — STALE
  FALLBACK"` — never represented as live. `CDF_MARKET_STALE_FALLBACK=0`
  restores the historical all-fail → 502 behaviour.
* Cache hits honestly keep the original `fetchedAt`; `lastDataTimestamp`
  stays the provider's own candle stamp. Analysis is always recomputed from
  the returned bars, so any dataset change automatically re-runs every
  indicator (the Transformer cache keys on bar content and retrains on new
  bars — unchanged).

### Response contract (unchanged keys, additive fields)

`/api/market/history` returns the same keys as before (`symbol`,
`providerSymbol`, `exchange`, `source`, `interval`, `fetchedAt`,
`lastDataTimestamp`, `freshness`, `bars`, plus `analysis`/
`historical_anomalies`), and adds two optional diagnostics:
`providerChain` (providers attempted, in order) and — only on stale
fallback — `stale: true`. The frontend reads the existing fields and
ignores the new ones; no frontend change is required.

### What is NOT verified (and therefore stays off)

* **Twelve Data for NSE/BSE** on the free plan — the plan does not include
  India. The adapter is implemented and covered by mocked tests, but no live
  request with a real key has ever been made here, so without your own
  verification it stays gated (`TWELVEDATA_ENABLE_INDIA`).
* **Alpha Vantage `.NSE`** — unverified (BSE only is documented). Gate:
  `ALPHAVANTAGE_ENABLE_NSE`.
* **Alpha Vantage intraday for India** — plan-dependent per the docs. Gate:
  `ALPHAVANTAGE_ENABLE_INTRADAY`.
* Yahoo remains the default because it serves NSE+BSE without any key; other
  providers activate only as optional fallback capacity.

## 8. Running tests

Backend (from `backend/`):

```bat
python -m pytest tests -q
```

Frontend (from the repo root):

```bat
node frontend\tests\chart-utils.test.js
python frontend\tests\check_frontend.py
```

## 9. Deployment architecture

The backend serves the frontend **from the same origin** — this is the safest
and simplest architecture:

* **Backend → any Python host that runs a Procfile web process** (Render,
  Railway, Koyeb, Fly.io, …). The backend's Procfile starts uvicorn, which
  automatically serves the `frontend/` folder as static files from the same
  URL. The frontend never touches localhost; it talks to its own origin.
* **Frontend → Vercel** (optional, split deploy): keeps the static files
  on the CDN edge for fastest load times, but requires setting `ALLOWED_ORIGINS`
  on the backend and `window.CDF_API_BASE` in `config.js` to the backend's
  public URL. The CORS allow-list on the backend is the security boundary.

> **Free-tier warning:** Render free web services share 0.1 CPU / 512 MB,
> sleep after 15 idle minutes (≈1 min wake-up), and have **ephemeral
> filesystems** — `feedback.jsonl` is reset on every redeploy. See
> Section 11 for the persistent-disk workaround.

## 10. Deployment commands

### Option A — Single-origin (recommended for beginners)

Everything runs from one backend host; no `config.js` edit or CORS config
needed.

1. Push this repo to GitHub.
2. Render dashboard → *New → Web Service* → connect the repo →
   **Root Directory: `backend`** (not the repo root), Runtime: *Python 3*,
   instance type: Free.
   Render auto-detects `requirements.txt`, `.python-version` and the
   `Procfile`; no manual build/start commands needed.
3. After deploy, verify `https://<your-backend>.onrender.com/api/health`
   returns `{"status":"success","message":"Clean Data Flow backend is running"}`.
4. Open `https://<your-backend>.onrender.com/` in a browser — the frontend
   loads from the same URL.

Optional hardening env vars to set in the Render dashboard:

| Variable | Recommended value |
|---|---|
| `CDF_ENABLE_DOCS` | `0` (hides API docs from public) |
| `CDF_FORCE_HSTS` | `1` (HTTPS-only browsers) |
| `CDF_TRUST_PROXY_HEADERS` | `1` (Render's proxy always rewrites X-Forwarded-For) |
| `CDF_TRUSTED_HOSTS` | `your-backend.onrender.com` |

### Option B — Split deploy (Vercel frontend + Render backend)

1. Deploy the **backend first** (same Render steps above).
2. Note the backend URL (e.g. `https://clean-data-flow-api.onrender.com`).
3. Edit `frontend/config.js` → change:
   ```js
   window.CDF_API_BASE = "https://clean-data-flow-api.onrender.com";
   ```
   ⚠️ **This is the most common deployment mistake.** If `config.js` still
   says `http://localhost:8000` after you deploy the frontend, every browser
   will fail to reach the backend.
4. Deploy the **frontend**: Vercel dashboard → *New Project* → import this
   repo → **Root Directory: `frontend`**, Framework: *Other*, no build
   command, output directory `.` → Deploy.
5. Note the frontend URL (e.g. `https://clean-data-flow.vercel.app`).
6. In the **backend** Render dashboard, add the environment variable:
   `ALLOWED_ORIGINS` = `https://clean-data-flow.vercel.app`
   (no trailing slash; comma-separate if you have multiple URLs).
   Redeploy the backend. Without this, browsers will block all API calls.
7. Verify `https://clean-data-flow.vercel.app/` loads and the "Test backend
   connection" button returns the health success message.

### Order matters (split deploy only)

Deploy backend → set `config.js` → deploy frontend → set `ALLOWED_ORIGINS`
on backend → redeploy backend. Both sides must agree or browsers will block
API calls ("Cannot reach the … backend" messages).

## 11. Common errors and fixes

| Symptom | Fix |
|---|---|
| Page says "Cannot reach the FastAPI backend …" locally | The backend is not running: start it with the Section-5 uvicorn command. |
| Page says "Cannot reach the backend service" on deployed site | The Render backend may be sleeping or restarting. For split deploys (Option B), verify `config.js` points to the public backend URL and `ALLOWED_ORIGINS` is configured. Single-origin (Option A) uses same-origin relative URLs automatically. |
| API calls blocked by CORS | The backend's `ALLOWED_ORIGINS` does not contain the frontend URL. Set it (comma-separate multiple) and redeploy the backend. |
| `502` on `/api/market/history` | Every enabled market-data provider failed or is rate-limited; retry shortly. If an old cache entry exists, it is served clearly labelled `stale` (set `CDF_MARKET_STALE_FALLBACK=0` to restore the plain-502 behaviour). No dummy data is ever returned. |
| `/api/market/oi` says data unavailable | True honesty: the official NSE F&O bhavcopy publishes after the session; weekends/holidays have no file. |
| 5Y analysis very slow (Transformer) | First call per symbol/timeframe trains once (~6 s workstation); identical repeat calls are served from the result cache. Still slow every time? `CDF_TR_CACHE=0` may be set in the environment — see Section 12. |
| `429 Too Many Requests` | The per-IP rate limiter is active (30 market requests/min, 5 feedback posts/min, 120 other API requests/min). Wait a moment and retry. On a shared office IP all users share one bucket. |
| Feedback disappears after redeploy | The host filesystem is ephemeral (Render free tier). Set `FEEDBACK_FILE` to a path on a **persistent disk** (Render paid plan → Disks → mount at `/mnt/data`; set `FEEDBACK_FILE=/mnt/data/feedback.jsonl`). |

## 12. Known limitations

* **Latest-only neural detector.** The Transformer scores only the
  latest complete bar (retraining per historical candle would be prohibitively
  expensive). It loads via a non-blocking Phase 2 background request so chart and
  indicators appear instantly. The analyser labels it clearly.
* **Transformer cost.** ~6 s of CPU on a workstation per analysed symbol for
  extended timeframes (history capped at 180 bars; extended-history requests
  use a reduced 120-epoch budget so totals stay bounded).
* **Transformer result cache.** Because the training is fully deterministic
  (fixed seed, no shuffling), identical calls (same bars + parameters) reuse
  the first result instead of retraining: ~6 s cold → ~0.001–0.05 s warm per
  identical symbol/timeframe. The bounded cache holds ≤ 64 entries for 15
  minutes by default (`CDF_TR_CACHE_TTL`); it is keyed on the exact returned
  bars, the call parameters and an algorithm-version fingerprint, so any new
  bar or changed history retrains automatically — correctness never depends
  on the TTL. Unavailable results are never cached and `CDF_TR_CACHE=0`
  disables caching entirely. Duplicate concurrent identical calls may each
  train once; the lock is never held during training.
* **Free-tier hosting reality.** A Render free web service runs on shared
  0.1 CPU / 512 MB, sleeps after 15 idle minutes (≈1 min wake-up), covers
  750 instance-hours/month per workspace, and gets 5 GB bandwidth. The
  Transformer request can take 30 s+ on such an instance; a Starter
  ($7/month, 0.5 vCPU) instance removes the slowdown. Free tiers are hosts'
  current offers and can change — check the host's pricing page.
* **Data honesty.** Layouts Yahoo or NSE publish can change without notice;
  every unavailable state is reported instead of papered over.
* **Open interest is end-of-day** NSE-only and never live/intraday.

## 13. Pointing the public frontend at the public backend

In one line: set `window.CDF_API_BASE` in `frontend/config.js` to the
backend's public `https://...` URL and redeploy the frontend. That constant is
read by every page via `frontend/script.js`; `config.js` is the only place to
edit.
