"""Explicit-post intake preserves source identity without collecting a backlog."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.research.connectors import ProviderCredentials, RedditConnector


def connector(submission):
    client = SimpleNamespace(submission=lambda **kwargs: submission, read_only=True)
    credentials = ProviderCredentials(reddit_client_id="test-id", reddit_client_secret="test-secret", reddit_user_agent="test-agent")
    return RedditConnector(credentials, reddit_factory=lambda **kwargs: client, now=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc))


def test_selected_post_retains_empty_yolo_body_and_flair():
    submission = SimpleNamespace(name="t3_abc123", title="Example Energy - YOLO SHORT", selftext="", subreddit=SimpleNamespace(display_name="wallstreetbets"), permalink="/r/wallstreetbets/comments/abc123/example/", created_utc=1789000000, score=7, link_flair_text="YOLO")
    result = connector(submission).fetch_post("https://www.reddit.com/r/wallstreetbets/comments/abc123/example/")
    assert result.capability == "ready"
    assert len(result.posts) == 1
    assert result.posts[0].body == ""
    assert result.posts[0].flair == "YOLO"
    assert result.metadata["selection"] == "explicit_post"


@pytest.mark.parametrize("url", ["http://www.reddit.com/r/wallstreetbets/comments/abc123/", "https://reddit.com.evil.test/r/wallstreetbets/comments/abc123/", "https://user:pass@reddit.com/r/wallstreetbets/comments/abc123/", "https://www.reddit.com/r/wallstreetbets/new/"])
def test_rejects_non_submission_or_foreign_links(url):
    with pytest.raises(ValueError):
        connector(None).fetch_post(url)


def test_failed_lazy_load_is_not_an_empty_successful_post():
    class Missing:
        @property
        def title(self):
            raise RuntimeError("sensitive provider response")
    result = connector(Missing()).fetch_post("https://www.reddit.com/r/wallstreetbets/comments/abc123/")
    assert result.capability == "provider_error"
    assert not result.posts
    assert "sensitive" not in result.error
