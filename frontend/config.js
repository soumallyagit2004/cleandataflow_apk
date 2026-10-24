/*
 * Clean Data Flow — deployment configuration.
 *
 * window.CDF_API_BASE defines the base URL for backend API requests.
 *
 * Production / Same-origin (Render):
 *   When the FastAPI backend serves both the API and frontend from the same host,
 *   an empty string ("") uses same-origin relative URLs (e.g. "/api/indices"),
 *   ensuring the deployed site calls its own Render backend instead of localhost.
 *
 * Local development:
 *   If opened directly via file:// or via a separate local static server on another
 *   port (e.g. VS Code Live Server on 5500), it automatically falls back to
 *   "http://localhost:8000".
 */

window.CDF_API_BASE = (function () {
  if (typeof window !== "undefined" && window.location) {
    const protocol = window.location.protocol;
    const hostname = window.location.hostname;
    const port = window.location.port;

    // Direct filesystem opening (file://)
    if (protocol === "file:") {
      return "http://localhost:8000";
    }

    // Standalone local frontend dev server running on a non-backend port
    if ((hostname === "localhost" || hostname === "127.0.0.1") && port && port !== "8000") {
      return "http://localhost:8000";
    }

    // Production (e.g. cleandataflow.onrender.com) & unified local server (port 8000)
    // use same-origin relative URLs.
    return "";
  }
  return "";
})();