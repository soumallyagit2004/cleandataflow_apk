"""Market-news feed for the Clean Data Flow homepage.

The Economic Times publishes a public RSS feed for its markets section. This
module fetches that feed server-side (so no third-party key or secret ever
reaches the browser), parses the real items, and caches the result briefly.

Nothing in this module invents headlines, publishers, timestamps or links. If
the feed cannot be reached or parsed, ``NewsUnavailable`` is raised and the API
returns an error so the frontend can show an honest unavailable state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
from typing import Any
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
SOURCE_URL = "https://economictimes.indiatimes.com/markets"
SOURCE_NAME = "Economic Times — Markets"
PUBLISHER = "Economic Times"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

CACHE_TTL_SECONDS = 300
MAX_ITEMS = 12

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"articles": None, "expires": 0.0, "fetched_at": None}


class NewsUnavailable(Exception):
    """Raised when the real news feed cannot provide usable articles."""


def _download_feed() -> bytes:
    request = urllib.request.Request(
        FEED_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if getattr(response, "status", 200) != 200:
            raise NewsUnavailable(f"The news feed returned HTTP {response.status}.")
        return response.read()


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_published(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return _iso_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_rss(xml_bytes: bytes, limit: int = MAX_ITEMS) -> tuple[str, list[dict[str, Any]]]:
    """Parse real RSS items into the response shape used by the frontend."""

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise NewsUnavailable(f"The news feed was not valid XML: {exc}") from exc

    channel = root.find("channel")
    if channel is None:
        raise NewsUnavailable("The news feed did not contain an RSS channel.")

    feed_title = (channel.findtext("title") or SOURCE_NAME).strip()

    articles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        if not link.startswith(("https://", "http://")):
            # A relative or malformed link would be a dead link in the UI.
            continue
        if link in seen:
            continue
        seen.add(link)

        articles.append(
            {
                "title": title,
                "link": link,
                "publisher": (item.findtext("source") or PUBLISHER).strip() or PUBLISHER,
                "publishedAt": _parse_published(item.findtext("pubDate")),
            }
        )
        if len(articles) >= limit:
            break

    if not articles:
        raise NewsUnavailable("The news feed returned no usable articles.")

    return feed_title, articles


def fetch_market_news(limit: int = 6, force: bool = False) -> dict[str, Any]:
    """Return recent real market headlines with publication and retrieval times.

    ``fetchedAt`` is when this backend retrieved the feed; each article's
    ``publishedAt`` is the publisher's own publication timestamp. The two are
    deliberately separate so the UI never presents a retrieval time as a
    publication time.
    """

    requested = max(1, min(int(limit), MAX_ITEMS))
    now = time.time()

    with _cache_lock:
        cached = _cache["articles"]
        if (
            cached is not None
            and not force
            and now < float(_cache["expires"] or 0)
        ):
            return _payload(cached, requested, cached=True, fetched_at=_cache["fetched_at"])

        xml_bytes = _download_feed()
        feed_title, articles = _parse_rss(xml_bytes, MAX_ITEMS)

        fetched_at = _iso_utc(datetime.now(timezone.utc))
        _cache.update(
            {
                "articles": articles,
                "expires": now + CACHE_TTL_SECONDS,
                "fetched_at": fetched_at,
            }
        )

        payload = _payload(articles, requested, cached=False, fetched_at=fetched_at)
        payload["feedTitle"] = feed_title
        return payload


def _payload(
    articles: list[dict[str, Any]],
    limit: int,
    *,
    cached: bool,
    fetched_at: str | None,
) -> dict[str, Any]:
    return {
        "status": "success",
        "source": SOURCE_NAME,
        "sourceUrl": SOURCE_URL,
        "publisher": PUBLISHER,
        "fetchedAt": fetched_at,
        "cached": cached,
        "cacheTtlSeconds": CACHE_TTL_SECONDS,
        "articles": [dict(article) for article in articles[:limit]],
        "note": (
            "Headlines are published by Economic Times and link to the original "
            "articles. Clean Data Flow does not verify or endorse them."
        ),
    }


def clear_news_cache() -> None:
    """Reset the in-memory news cache (used by tests)."""

    with _cache_lock:
        _cache.update({"articles": None, "expires": 0.0, "fetched_at": None})
