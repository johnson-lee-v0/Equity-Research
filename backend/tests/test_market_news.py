from datetime import datetime, timedelta, timezone
from html import escape

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.market_news import create_market_news_router
from backend.app.research.market_news import FEEDS, MAX_FEED_BYTES, MarketNewsService, parse_feed

NOW = datetime(2026, 9, 26, 15, tzinfo=timezone.utc)


def rss(rows):
    items = ''.join(f'<item><title>{escape(title)}</title><link>{escape(url)}</link><pubDate>{date}</pubDate></item>' for title, url, date in rows)
    return f'<rss><channel>{items}</channel></rss>'.encode()


def date(hours=0):
    return (NOW - timedelta(hours=hours)).strftime('%a, %d %b %Y %H:%M:%S GMT')


def feed_data(feed):
    host = feed.article_hosts[0]
    return rss([(f'Headline {i}', f'https://{host}/story/{i}', date(i)) for i in range(10)])


def test_parse_skips_unsafe_links_old_and_future_stories_and_cleans_headline():
    data = rss([
        ('<b>A & B</b>', 'https://www.marketwatch.com/story/current?rss=1', date()),
        ('Old', 'https://marketwatch.com/old', date(200)),
        ('Future', 'https://marketwatch.com/future', date(-2)),
        ('Javascript', 'javascript:alert(1)', date()),
        ('Wrong host', 'https://marketwatch.com.evil.example/story', date()),
        ('Credentials', 'https://user:password@marketwatch.com/story', date()),
        ('No date', 'https://marketwatch.com/nodate', ''),
    ])
    rows = parse_feed(data, FEEDS[0], NOW)
    assert len(rows) == 1
    assert rows[0]['title'] == 'A & B'
    assert rows[0]['source'] == 'MarketWatch'
    assert rows[0]['published_at'] == '2026-09-26T15:00:00Z'


@pytest.mark.parametrize('data', [b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss/>', b'x' * (MAX_FEED_BYTES + 1), '<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss/>'.encode('utf-16')])
def test_rejects_entity_expansion_and_oversized_xml(data):
    with pytest.raises(ValueError):
        parse_feed(data, FEEDS[0], NOW)


def test_latest_six_deduplicated_headlines_are_cached_and_returned_by_value():
    calls = []
    def fetch(feed):
        calls.append(feed.url)
        return feed_data(feed)
    service = MarketNewsService(fetch, lambda: NOW)
    result = service.snapshot()
    assert result['status'] == 'fresh'
    assert len(result['items']) == 6
    assert len({row['id'] for row in result['items']}) == 6
    assert [row['published_at'] for row in result['items']] == sorted([row['published_at'] for row in result['items']], reverse=True)
    result['items'].clear()
    assert len(service.snapshot()['items']) == 6
    assert len(calls) == len(FEEDS)


def test_failed_refresh_preserves_recent_news_marks_it_stale_and_throttles_retries():
    clock = [NOW]
    calls = []
    def fetch(feed):
        calls.append(feed.url)
        if clock[0] > NOW:
            raise httpx.ConnectError('offline')
        return feed_data(feed)
    service = MarketNewsService(fetch, lambda: clock[0])
    original = service.snapshot()
    clock[0] += timedelta(minutes=11)
    stale = service.snapshot()
    assert stale['status'] == 'stale'
    assert stale['items'] == original['items']
    assert stale['fetched_at'] == original['fetched_at']
    clock[0] += timedelta(seconds=10)
    service.snapshot()
    assert len(calls) == len(FEEDS) * 2
    clock[0] += timedelta(days=2)
    assert service.snapshot()['status'] == 'unavailable'
    assert service.snapshot()['items'] == []


def test_unavailable_feed_never_invents_six_headlines():
    service = MarketNewsService(lambda _: b'<rss><channel/></rss>', lambda: NOW)
    result = service.snapshot()
    assert result['items'] == []
    assert result['fetched_at'] is None
    assert result['status'] == 'unavailable'


def test_route_is_read_only_and_has_no_caller_controlled_feed_url():
    service = MarketNewsService(feed_data, lambda: NOW)
    app = FastAPI()
    app.include_router(create_market_news_router(service))
    with TestClient(app) as client:
        response = client.get('/api/market-news?url=http://localhost/private')
        assert response.status_code == 200
        assert len(response.json()['items']) == 6
        assert client.post('/api/market-news').status_code == 405
    assert list(app.openapi()['paths']['/api/market-news']) == ['get']
