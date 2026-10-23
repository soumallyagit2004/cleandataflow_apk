"""Tests for the additive news and feedback endpoints."""

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
import news_data
from news_data import NewsUnavailable, fetch_market_news, clear_news_cache, _parse_rss

client = TestClient(main.app)

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Markets-Economic Times</title>
    <link>https://economictimes.indiatimes.com/markets</link>
    <item>
      <title>Nifty ends higher as banks rally</title>
      <link>https://economictimes.indiatimes.com/markets/stocks/news/nifty-ends-higher/articleshow/1.cms</link>
      <pubDate>Fri, 23 Oct 2026 15:30:00 +0530</pubDate>
    </item>
    <item>
      <title>Rupee steady against dollar</title>
      <link>https://economictimes.indiatimes.com/markets/forex/rupee-steady/articleshow/2.cms</link>
      <pubDate>Fri, 23 Oct 2026 14:00:00 +0530</pubDate>
      <source>Economic Times</source>
    </item>
    <item>
      <title>Duplicate headline</title>
      <link>https://economictimes.indiatimes.com/markets/stocks/news/nifty-ends-higher/articleshow/1.cms</link>
      <pubDate>Fri, 23 Oct 2026 13:00:00 +0530</pubDate>
    </item>
    <item>
      <title>Relative link must be skipped</title>
      <link>/markets/relative.cms</link>
      <pubDate>Fri, 23 Oct 2026 12:00:00 +0530</pubDate>
    </item>
  </channel>
</rss>
"""


class TestNewsParsing:
    def test_parses_real_feed_fields_and_skips_bad_links(self):
        feed_title, articles = _parse_rss(SAMPLE_RSS)
        assert feed_title == "Markets-Economic Times"
        assert len(articles) == 2
        first = articles[0]
        assert first["title"] == "Nifty ends higher as banks rally"
        assert first["link"].startswith("https://economictimes.indiatimes.com/")
        assert first["publisher"] == "Economic Times"
        assert first["publishedAt"] == "2026-10-23T10:00:00Z"
        assert articles[1]["publishedAt"] == "2026-10-23T08:30:00Z"

    def test_parse_rejects_xml_without_channel(self):
        try:
            _parse_rss(b"<root></root>")
        except NewsUnavailable as exc:
            assert "channel" in str(exc).lower()
        else:  # pragma: no cover - explicit failure path
            raise AssertionError("expected NewsUnavailable")


class TestNewsEndpoint:
    def test_endpoint_returns_backend_payload(self):
        fake = {
            "status": "success",
            "source": "Economic Times — Markets",
            "sourceUrl": "https://economictimes.indiatimes.com/markets",
            "publisher": "Economic Times",
            "fetchedAt": "2026-10-23T10:05:00Z",
            "cached": False,
            "cacheTtlSeconds": 300,
            "articles": [
                {
                    "title": "Nifty ends higher as banks rally",
                    "link": "https://economictimes.indiatimes.com/markets/stocks/news/nifty-ends-higher/articleshow/1.cms",
                    "publisher": "Economic Times",
                    "publishedAt": "2026-10-23T10:00:00Z",
                }
            ],
            "note": "test",
        }
        with patch("main.fetch_market_news", return_value=fake) as mocked:
            response = client.get("/api/news?limit=6")
        assert response.status_code == 200
        data = response.json()
        assert data["articles"][0]["title"] == "Nifty ends higher as banks rally"
        assert data["fetchedAt"] == "2026-10-23T10:05:00Z"
        assert data["articles"][0]["publishedAt"] == "2026-10-23T10:00:00Z"
        mocked.assert_called_once_with(limit=6)

    def test_endpoint_reports_unavailable_instead_of_fake_news(self):
        with patch(
            "main.fetch_market_news",
            side_effect=NewsUnavailable("upstream timeout"),
        ):
            response = client.get("/api/news")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "temporarily unavailable" in detail
        assert "No headlines were invented" in detail

    def test_cache_avoids_duplicate_downloads(self):
        clear_news_cache()
        try:
            with patch.object(
                news_data, "_download_feed", return_value=SAMPLE_RSS
            ) as download:
                first = fetch_market_news(limit=1)
                second = fetch_market_news(limit=2)
            assert download.call_count == 1
            assert first["cached"] is False
            assert second["cached"] is True
            assert len(first["articles"]) == 1
            assert len(second["articles"]) == 2
            assert second["fetchedAt"] == first["fetchedAt"]
        finally:
            clear_news_cache()


class TestFeedbackEndpoint:
    def test_valid_feedback_is_stored(self, tmp_path: Path):
        target = tmp_path / "feedback.jsonl"
        with patch.object(main, "FEEDBACK_FILE", target):
            response = client.post(
                "/api/feedback",
                json={
                    "name": "A Student",
                    "email": "student@example.com",
                    "rating": 5,
                    "message": "The source and freshness labels are useful.",
                    "page": "home",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["id"]
        assert "ephemeral" in data["persistence"]
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert "The source and freshness labels are useful." in lines[0]

    def test_rating_out_of_range_is_rejected(self, tmp_path: Path):
        with patch.object(main, "FEEDBACK_FILE", tmp_path / "feedback.jsonl"):
            response = client.post(
                "/api/feedback",
                json={"rating": 6, "message": "This message is long enough."},
            )
        assert response.status_code == 422

    def test_short_message_and_bad_email_are_rejected(self, tmp_path: Path):
        target = tmp_path / "feedback.jsonl"
        with patch.object(main, "FEEDBACK_FILE", target):
            short = client.post(
                "/api/feedback",
                json={"rating": 4, "message": "tiny"},
            )
            bad_email = client.post(
                "/api/feedback",
                json={
                    "rating": 4,
                    "message": "This message is long enough.",
                    "email": "not-an-email",
                },
            )
        assert short.status_code == 422
        assert bad_email.status_code == 422
        assert not target.exists()

    def test_feedback_storage_failure_is_honest(self, tmp_path: Path):
        target = tmp_path / "missing" / "feedback.jsonl"
        with patch.object(main, "FEEDBACK_FILE", target):
            with patch.object(Path, "mkdir", side_effect=OSError("read-only")):
                response = client.post(
                    "/api/feedback",
                    json={"rating": 4, "message": "This message is long enough."},
                )
        assert response.status_code == 503
        assert "Nothing was submitted" in response.json()["detail"]

    def test_cors_preflight_allows_post(self):
        response = client.options(
            "/api/feedback",
            headers={
                "Origin": "http://localhost:5500",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert "POST" in response.headers.get("access-control-allow-methods", "")
