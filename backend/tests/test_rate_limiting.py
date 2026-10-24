"""Tests for the per-IP rate limiter (RateLimitStore, RateLimitMiddleware,
and _classify_rate_limit).

These tests do NOT open sockets or touch live data.  The store tests exercise
the decision logic directly; the middleware tests use FastAPI's TestClient to
verify the full HTTP integration.
"""

import time
from fastapi.testclient import TestClient

from main import (
    RateLimitStore,
    RateLimitMiddleware,
    _classify_rate_limit,
    app,
)


# ---------------------------------------------------------------------------
# RateLimitStore unit tests
# ---------------------------------------------------------------------------


class TestRateLimitStoreAllowsUpToLimit:
    def test_allows_exactly_limit_requests_in_window(self):
        store = RateLimitStore()
        now = time.monotonic()
        limit, window = 3, 60
        for _ in range(limit):
            result = store.check("ip:1.2.3.4", "api", limit, window, now)
            assert result is None, "requests within limit must be allowed"

    def test_blocks_on_limit_plus_one(self):
        store = RateLimitStore()
        now = time.monotonic()
        limit, window = 3, 60
        for _ in range(limit):
            store.check("ip:1.2.3.4", "api", limit, window, now)
        retry_after = store.check("ip:1.2.3.4", "api", limit, window, now)
        assert retry_after is not None, "request over limit must be blocked"
        assert retry_after > 0

    def test_window_slides_so_old_hits_expire(self):
        store = RateLimitStore()
        limit, window = 2, 10
        t0 = time.monotonic()
        for _ in range(limit):
            store.check("ip:1.2.3.4", "api", limit, window, t0)
        # One tick after the window expires the slot is free again.
        t_after = t0 + window + 1
        result = store.check("ip:1.2.3.4", "api", limit, window, t_after)
        assert result is None, "request after window expiry must be allowed"


class TestRateLimitStoreBucketIsolation:
    def test_different_ips_have_independent_budgets(self):
        store = RateLimitStore()
        now = time.monotonic()
        limit, window = 1, 60
        store.check("ip:A", "api", limit, window, now)
        blocked = store.check("ip:A", "api", limit, window, now)
        assert blocked is not None
        allowed = store.check("ip:B", "api", limit, window, now)
        assert allowed is None

    def test_different_buckets_have_independent_budgets(self):
        store = RateLimitStore()
        now = time.monotonic()
        limit, window = 1, 60
        store.check("ip:X", "market", limit, window, now)
        blocked = store.check("ip:X", "market", limit, window, now)
        assert blocked is not None
        allowed = store.check("ip:X", "api", limit, window, now)
        assert allowed is None


# ---------------------------------------------------------------------------
# _classify_rate_limit unit tests
# ---------------------------------------------------------------------------


class TestClassifyRateLimit:
    def test_health_endpoint_is_exempt(self):
        assert _classify_rate_limit("/api/health", "GET") is None
        assert _classify_rate_limit("/api/health", "HEAD") is None
        assert _classify_rate_limit("/health", "GET") is None
        assert _classify_rate_limit("/health", "HEAD") is None

    def test_options_preflight_is_exempt(self):
        assert _classify_rate_limit("/api/market/history", "OPTIONS") is None

    def test_static_path_is_exempt(self):
        assert _classify_rate_limit("/index.html", "GET") is None
        assert _classify_rate_limit("/", "GET") is None

    def test_feedback_post_classified_as_feedback_bucket(self):
        result = _classify_rate_limit("/api/feedback", "POST")
        assert result is not None
        assert result[0] == "feedback"

    def test_market_history_classified_as_market_bucket(self):
        result = _classify_rate_limit("/api/market/history", "GET")
        assert result is not None
        assert result[0] == "market"

    def test_indices_classified_as_market_bucket(self):
        assert _classify_rate_limit("/api/indices", "GET")[0] == "market"
        assert _classify_rate_limit("/api/indices/NIFTY%2050/constituents", "GET")[0] == "market"

    def test_news_classified_as_market_bucket(self):
        assert _classify_rate_limit("/api/news", "GET")[0] == "market"

    def test_unknown_api_path_classified_as_default_bucket(self):
        result = _classify_rate_limit("/api/unknown", "GET")
        assert result is not None
        assert result[0] == "api"

    def test_feedback_bucket_is_tighter_than_api_bucket(self):
        """feedback (5/min) must be more restrictive than default api (120/min)."""
        feedback = _classify_rate_limit("/api/feedback", "POST")
        api = _classify_rate_limit("/api/unknown", "GET")
        assert feedback is not None and api is not None
        assert feedback[1] < api[1], (
            "feedback must be more tightly limited than generic API"
        )


# ---------------------------------------------------------------------------
# Middleware HTTP integration tests
# ---------------------------------------------------------------------------


def _mini_app_with_store(store, enabled=True):
    """Return a (FastAPI, TestClient) pair that uses the given store."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as TC

    mini = FastAPI()

    @mini.get("/api/market/history")
    def _market():
        return {"ok": True}

    @mini.get("/api/health")
    def _health():
        return {"status": "success", "message": "Clean Data Flow backend is running"}

    @mini.post("/api/feedback")
    def _feedback():
        return {"status": "success"}

    mini.add_middleware(RateLimitMiddleware, enabled=enabled, store=store)
    return mini, TC(mini)


class TestRateLimitMiddlewareHttp:
    def test_market_history_429_after_limit(self):
        store = RateLimitStore()
        now = time.monotonic()
        # Exhaust the 30-req/min market budget.
        for _ in range(30):
            store.check("ip:testclient", "market", 30, 60, now)

        _, tc = _mini_app_with_store(store)
        resp = tc.get("/api/market/history")
        assert resp.status_code == 429
        data = resp.json()
        assert "Too many requests" in data.get("detail", "")
        assert int(resp.headers.get("retry-after", "0")) >= 1

    def test_health_never_rate_limited(self):
        """Health endpoint must return 200 even when api bucket is exhausted."""
        store = RateLimitStore()
        now = time.monotonic()
        for _ in range(200):
            store.check("ip:testclient", "api", 120, 60, now)

        _, tc = _mini_app_with_store(store)
        resp = tc.get("/api/health")
        assert resp.status_code == 200

    def test_disabled_middleware_never_blocks(self):
        """CDF_RATE_LIMIT_ENABLED=0 must pass all requests through."""
        store = RateLimitStore()
        now = time.monotonic()
        for bucket in ("market", "feedback", "api"):
            for _ in range(500):
                store.check("ip:testclient", bucket, 1, 60, now)

        _, tc = _mini_app_with_store(store, enabled=False)
        resp = tc.get("/api/market/history")
        assert resp.status_code == 200

    def test_429_response_has_retry_after_header(self):
        store = RateLimitStore()
        now = time.monotonic()
        for _ in range(5):
            store.check("ip:testclient", "feedback", 5, 60, now)

        _, tc = _mini_app_with_store(store)
        resp = tc.post("/api/feedback")
        assert resp.status_code == 429
        assert "retry-after" in resp.headers
        assert int(resp.headers["retry-after"]) >= 1

    def test_cors_options_preflight_not_blocked(self):
        """CORS preflights must not consume a rate-limit slot or return 429."""
        tc = TestClient(app)
        for _ in range(50):
            resp = tc.options(
                "/api/market/history",
                headers={
                    "Origin": "http://localhost:5500",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert resp.status_code != 429, (
                f"preflight must not be rate-limited (got {resp.status_code})"
            )

    def test_different_ips_get_independent_limits(self):
        """Requests from different IPs must not share a rate-limit bucket."""
        store = RateLimitStore()
        now = time.monotonic()
        # Exhaust the market budget for ip:A only.
        for _ in range(30):
            store.check("ip:1.1.1.1", "market", 30, 60, now)

        # ip:B (testclient default) must still be allowed.
        _, tc = _mini_app_with_store(store)
        # TestClient peer address is usually 127.0.0.1, a separate bucket from ip:1.1.1.1
        resp = tc.get("/api/market/history")
        assert resp.status_code == 200
