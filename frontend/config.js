/*
 * Clean Data Flow — deployment configuration (the only place the backend URL
 * is configured for the site).
 *
 * window.CDF_API_BASE tells every page which backend to call.
 *
 *   Local development (the default here):
 *     keep "http://localhost:8000" and run the backend with:
 *       cd backend
 *       python -m uvicorn main:app --host 127.0.0.1 --port 8000
 *
 *   Production:
 *     replace it with your public backend URL, including https://, for example:
 *       window.CDF_API_BASE = "https://clean-data-flow-api.fly.dev";
 *     No API keys or secrets belong in this file — this backend is public and
 *     read-only, and the CORS allow-list lives on the backend instead
 *     (ALLOWED_ORIGINS environment variable documented in the README).
 */

window.CDF_API_BASE = "http://localhost:8000";