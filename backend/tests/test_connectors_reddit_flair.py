from __future__ import annotations

from datetime import datetime, timezone

from backend.app.research.connectors import (
    ProviderCredentials,
    RedditConnector,
)


UTC = timezone.utc


def reddit_credentials() -> ProviderCredentials:
    return ProviderCredentials(
        reddit_client_id="reddit-id-for-flair-test",
        reddit_client_secret="reddit-secret-for-flair-test",
        reddit_user_agent="road2m-flair-test/1.0",
    )


class FlairSubmission:
    def __init__(self, ident: str, *, flair_marker: object = None, include_flair: bool = True) -> None:
        self.name = ident
        self.subreddit = type("Sub", (), {"display_name": "wallstreetbets"})()
        self.title = "Title"
        self.selftext = "Body"
        self.permalink = f"/r/wallstreetbets/comments/{ident}/"
        self.created_utc = datetime(2026, 9, 12, tzinfo=UTC).timestamp()
        self.score = 3
        if include_flair:
            self.link_flair_text = flair_marker


class FlairSubreddit:
    def __init__(self, submissions: list[FlairSubmission]) -> None:
        self.submissions = submissions
        self.calls: list[tuple[int, object]] = []

    def new(self, *, limit: int, params: object = None):
        self.calls.append((limit, params))
        return iter(self.submissions)


class FlairReddit:
    def __init__(self, submissions: list[FlairSubmission]) -> None:
        self.read_only = False
        self.sub = FlairSubreddit(submissions)

    def subreddit(self, name: str) -> FlairSubreddit:
        assert name == "wallstreetbets"
        return self.sub


def fetch(submissions: list[FlairSubmission]):
    fake = FlairReddit(submissions)
    connector = RedditConnector(
        reddit_credentials(),
        reddit_factory=lambda **kwargs: fake,
        now=lambda: datetime(2026, 9, 12, tzinfo=UTC),
    )
    return connector.new_posts(max_posts=len(submissions), max_pages=1, batch_size=100), fake


def test_reddit_yolo_link_flair_is_captured_and_serialized() -> None:
    result, fake = fetch([FlairSubmission("yolo", flair_marker="YOLO")])

    post = result.posts[0]
    assert post.flair == "YOLO"
    assert post.to_dict()["flair"] == "YOLO"
    assert '"flair": "YOLO"' in result.canonical_source_text()
    assert fake.sub.calls == [(100, None)]


def test_reddit_absent_link_flair_preserves_legacy_serialization() -> None:
    result, _ = fetch([FlairSubmission("legacy", include_flair=False)])

    post = result.posts[0]
    assert post.flair is None
    assert "flair" not in post.to_dict()
    assert '"flair"' not in result.canonical_source_text()


class BrokenFlair:
    def __str__(self) -> str:
        raise RuntimeError("provider value cannot be rendered")


def test_reddit_flair_is_safe_for_malformed_and_large_values() -> None:
    malformed, _ = fetch([FlairSubmission("malformed", flair_marker=BrokenFlair())])
    oversized, _ = fetch([FlairSubmission("oversized", flair_marker="X" * 500)])

    assert malformed.posts[0].flair is None
    assert "flair" not in malformed.posts[0].to_dict()
    assert oversized.posts[0].flair == "X" * 200
    assert len(oversized.posts[0].to_dict()["flair"]) == 200
