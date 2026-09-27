"""Small, public RSS reading shelf. It never reads or writes the research ledger."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
import re
import threading
import time
from typing import Callable
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx


@dataclass(frozen=True)
class NewsFeed:
    name: str
    url: str
    article_hosts: tuple[str, ...]


FEEDS = (
    NewsFeed("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins", ("marketwatch.com",)),
    NewsFeed("Bloomberg", "https://www.bloomberg.com/feeds/markets/news.rss", ("bloomberg.com",)),
    NewsFeed("The Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", ("wsj.com",)),
)
MAX_FEED_BYTES = 1_000_000
CACHE_SECONDS = 600
RETRY_SECONDS = 60
MAX_STALE_SECONDS = 86_400


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_feed(feed: NewsFeed) -> bytes:
    # Only code-owned URLs are fetched. No redirects, cookies, input URLs or paid API.
    chunks = bytearray()
    deadline = time.monotonic() + 12
    with httpx.Client(timeout=8, follow_redirects=False, headers={"User-Agent": "ResearchCouncil RSS reader/1.0", "Accept": "application/rss+xml, application/xml, text/xml"}) as client:
        with client.stream("GET", feed.url) as response:
            response.raise_for_status()
            if int(response.headers.get("content-length", "0")) > MAX_FEED_BYTES:
                raise ValueError("RSS feed exceeds size limit")
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if time.monotonic() > deadline:
                    raise ValueError("RSS request exceeded time limit")
                if len(chunks) > MAX_FEED_BYTES:
                    raise ValueError("RSS feed exceeds size limit")
    return bytes(chunks)


def parse_feed(data: bytes, feed: NewsFeed, now: datetime) -> list[dict]:
    if len(data) > MAX_FEED_BYTES or b"\x00" in data or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.I):
        raise ValueError("Unsupported RSS document")
    root = ElementTree.fromstring(data)
    items = []
    for row in root.findall("./channel/item")[:100]:
        title = " ".join(unescape(re.sub(r"<[^>]*>", " ", row.findtext("title") or "")).split())[:300]
        raw_url = (row.findtext("link") or "").strip()
        try:
            url = urlsplit(raw_url)
            host = (url.hostname or "").lower()
            published = parsedate_to_datetime(row.findtext("pubDate") or "")
            if published.tzinfo is None:
                continue
            published = published.astimezone(timezone.utc)
            if not title or url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
                continue
            if not any(host == allowed or host.endswith("." + allowed) for allowed in feed.article_hosts):
                continue
            # Archived or future-dated stories must not look like today's headlines.
            if published < now - timedelta(days=7) or published > now + timedelta(hours=1):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        canonical_url = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
        items.append({"id": sha256(canonical_url.encode()).hexdigest()[:20], "title": title,
                      "url": raw_url, "source": feed.name, "published_at": _iso(published)})
    return items


class MarketNewsService:
    """Single-flight, bounded cache shared by all visitors to this local process."""

    def __init__(self, fetch: Callable[[NewsFeed], bytes] = fetch_feed, clock: Callable[[], datetime] = _utc_now):
        self._fetch, self._clock = fetch, clock
        self._lock = threading.Lock()
        self._items: list[dict] = []
        self._fetched: datetime | None = None
        self._next_attempt: datetime | None = None
        self._message: str | None = None
        self._failed = False

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            if self._next_attempt is None or now >= self._next_attempt:
                def read(feed: NewsFeed):
                    try:
                        return parse_feed(self._fetch(feed), feed, now)
                    except (httpx.HTTPError, ValueError, ElementTree.ParseError, OSError):
                        return []
                with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
                    results = list(pool.map(read, FEEDS))
                unique = {item["id"]: item for batch in results for item in batch}
                items = sorted(unique.values(), key=lambda item: (item["published_at"], item["id"]), reverse=True)[:6]
                self._failed = not items
                if items:
                    self._items, self._fetched = items, now
                    self._next_attempt = now + timedelta(seconds=CACHE_SECONDS)
                    self._message = "Some news feeds are unavailable." if any(not batch for batch in results) else None
                else:
                    self._next_attempt = now + timedelta(seconds=RETRY_SECONDS)
                    self._message = "News feeds are temporarily unavailable."
            age = (now - self._fetched).total_seconds() if self._fetched else float("inf")
            usable = bool(self._items) and age <= MAX_STALE_SECONDS
            return deepcopy({
                "status": "stale" if usable and self._failed else "fresh" if usable else "unavailable",
                "items": self._items if usable else [],
                "fetched_at": _iso(self._fetched) if self._fetched else None,
                "message": self._message,
                "feeds": [{"name": feed.name, "url": feed.url} for feed in FEEDS],
            })
