"""Representative Reddit screening decisions through the repository gate."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.schemas import AgentOutputPayload, ModelConfig, RedditTriage, RoutingPlan, RunCreate


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")


def make_repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def reddit_post(
    key: str,
    *,
    title: str,
    body: str,
    flair: str | None = None,
) -> dict[str, Any]:
    post: dict[str, Any] = {
        "post_id": f"t3_{key}",
        "subreddit": "wallstreetbets",
        "title": title,
        "body": body,
        "created_at": "2026-09-12T00:00:00Z",
        "permalink": f"https://www.reddit.com/r/wallstreetbets/comments/{key}/",
        "score": 3,
    }
    if flair is not None:
        post["flair"] = flair
    return post


def ingest_result(post: dict[str, Any]) -> dict[str, Any]:
    return {
        "posts": [post],
        "status": "ok",
        "error": None,
        "metadata": {
            "retrieved_at": "2026-09-12T00:00:00Z",
            "pagination_stopped_reason": "listing_exhausted",
            "last_consumed_cursor": post["post_id"],
        },
    }


def screen_reddit_post(
    repository: Repository,
    post: dict[str, Any],
    plan: RoutingPlan,
) -> tuple[dict[str, Any], str, str]:
    repository.ingest_reddit_result(ingest_result(post), namespace="real")
    item = next(
        item
        for item in repository.intake_status(namespace="real", limit=10)["items"]
        if item["external_id"] == post["post_id"]
    )
    source_id = str(item["versions"][-1]["source_id"])
    source_before = repository.sources("real", source_id)[0]

    dispatch = repository.dispatch_reddit_backlog(namespace="real", max_dispatches=1, max_cost=1, item_id=item["id"])
    assert len(dispatch["dispatched"]) == 1
    run_id = str(dispatch["dispatched"][0]["run_id"])
    routing_task = next(task for task in repository.tasks_for_run(run_id) if task["kind"] == "routing")
    attempt = repository.create_attempt(
        routing_task["id"],
        CONFIG,
        {source_id: {"version": source_before["version"], "hash": source_before["content_hash"]}},
    )
    output = repository.commit_output(
        routing_task["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Reddit screening",
            summary="The retained submission was screened.",
            analysis="The decision is bounded to the immutable Reddit source.",
            routing_plan=plan,
        ),
        "real",
        CONFIG,
    )
    consumed = repository.consume_routing_plan(run_id, plan, routing_output_id=output["id"])

    source_after = repository.sources("real", source_id)[0]
    assert source_after["content_hash"] == source_before["content_hash"]
    assert source_after["content"] == source_before["content"]
    return consumed, run_id, source_id


def task_kinds(repository: Repository, run_id: str) -> set[str]:
    return {str(task["kind"]) for task in repository.tasks_for_run(run_id)}


def test_actual_thesis_claim_and_reason_with_bound_excerpt_expands_research(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    post = reddit_post(
        "thesis",
        title="TSLA thesis: factory utilization can lift margins",
        body="TSLA can grow free cash flow because factory utilization is rising.",
    )
    plan = RoutingPlan(
        intent="research",
        horizon="event",
        tickers=["TSLA"],
        selected_analysts=["A03"],
        rationale="The post states a business claim and gives a supporting reason.",
        reddit_triage=RedditTriage(
            classification="thesis",
            reason="The author states an investment outlook with a supporting operating reason.",
            thesis_summary="Factory utilization could support TSLA free cash flow growth.",
            evidence_excerpt=post["body"],
            tickers=["TSLA"],
        ),
    )

    consumed, run_id, _ = screen_reddit_post(repository, post, plan)

    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "thesis"
    assert consumed["routing_plan"]["tickers"] == ["TSLA"]
    assert task_kinds(repository, run_id) == {"routing", "universe_discovery", "research_synthesis", "cio_review"}


def test_yolo_flair_and_dollar_ticker_expand_and_route_tsla(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    post = reddit_post(
        "yolo-tsla",
        title="All in",
        body="I bought calls on $TSLA.",
        flair="YOLO",
    )
    plan = RoutingPlan(
        intent="research",
        horizon="event",
        tickers=["TSLA"],
        selected_analysts=["A03"],
        rationale="The YOLO trade names a literal ticker.",
        reddit_triage=RedditTriage(
            classification="yolo_ticker",
            reason="The retained flair identifies a YOLO trade and the body names a ticker.",
            evidence_excerpt="YOLO",
            tickers=["TSLA"],
        ),
    )

    consumed, run_id, _ = screen_reddit_post(repository, post, plan)

    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "yolo_ticker"
    assert consumed["routing_plan"]["tickers"] == ["TSLA"]
    assert repository.run_record(run_id)["ticker"] == "TSLA"
    assert "research_synthesis" in task_kinds(repository, run_id)


def test_hype_without_a_thesis_is_visible_skip_without_specialists(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    post = reddit_post("hype", title="$TSLA to the moon", body="Buy $TSLA. No explanation.")
    plan = RoutingPlan(
        intent="research",
        tickers=["TSLA"],
        selected_analysts=["A03"],
        rationale="The source is hype without an investment reason.",
        reddit_triage=RedditTriage(
            classification="skip",
            reason="Bare ticker hype has no thesis or supporting reason.",
            evidence_excerpt=post["title"],
            tickers=["TSLA"],
        ),
    )

    consumed, run_id, _ = screen_reddit_post(repository, post, plan)

    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "skip"
    assert task_kinds(repository, run_id) == {"routing"}


def test_yolo_without_an_identifiable_ticker_cannot_expand_specialists(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    post = reddit_post("yolo-no-ticker", title="YOLO all in", body="I am all in tomorrow.", flair="YOLO")
    plan = RoutingPlan(
        intent="research",
        tickers=[],
        selected_analysts=["A03"],
        rationale="The post has no identifiable ticker literal.",
        reddit_triage=RedditTriage(
            classification="yolo_ticker",
            reason="The post claims a YOLO trade but names no identifiable ticker.",
            evidence_excerpt="YOLO",
            tickers=[],
        ),
    )

    consumed, run_id, _ = screen_reddit_post(repository, post, plan)

    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "skip"
    assert "Screening unavailable" in consumed["routing_plan"]["reddit_triage"]["reason"]
    assert task_kinds(repository, run_id) == {"routing"}


def test_missing_triage_cannot_expand_a_spoofed_ticker_route(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    post = reddit_post("missing-triage", title="$TSLA idea", body="TSLA has a catalyst.")
    plan = RoutingPlan(
        intent="research",
        tickers=["TSLA"],
        selected_analysts=["A03"],
        rationale="Spoofed route without a screening decision.",
    )

    consumed, run_id, _ = screen_reddit_post(repository, post, plan)

    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "skip"
    assert task_kinds(repository, run_id) == {"routing"}


def test_forged_excerpt_cannot_expand_a_thesis_route(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    post = reddit_post("forged-excerpt", title="$TSLA idea", body="The post contains only this sentence.")
    plan = RoutingPlan(
        intent="research",
        tickers=["TSLA"],
        selected_analysts=["A03"],
        rationale="The route tries to attach an unsupported excerpt.",
        reddit_triage=RedditTriage(
            classification="thesis",
            reason="The post allegedly contains an investment thesis.",
            thesis_summary="Unsupported summary.",
            evidence_excerpt="Forged excerpt from another post.",
            tickers=["TSLA"],
        ),
    )

    consumed, run_id, _ = screen_reddit_post(repository, post, plan)

    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "skip"
    assert task_kinds(repository, run_id) == {"routing"}


def test_metadata_only_excerpt_cannot_expand_a_thesis_route(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    post = reddit_post("metadata-excerpt", title="$TSLA idea", body="TSLA has a catalyst.")
    plan = RoutingPlan(
        intent="research",
        tickers=["TSLA"],
        selected_analysts=["A03"],
        rationale="The route quotes provider metadata instead of author text.",
        reddit_triage=RedditTriage(
            classification="thesis",
            reason="The post allegedly contains an investment thesis.",
            thesis_summary="Unsupported summary.",
            evidence_excerpt='"score": 3',
            tickers=["TSLA"],
        ),
    )

    consumed, run_id, _ = screen_reddit_post(repository, post, plan)

    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "skip"
    assert task_kinds(repository, run_id) == {"routing"}


def test_ordinary_user_route_does_not_require_reddit_triage(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run, _ = repository.create_run(
        RunCreate(
            question="Review the ordinary TSLA research request.",
            namespace="real",
            idempotency_key="ordinary-user-route-1",
        ),
        [("A00", "routing", "Route the ordinary user request.", [])],
        allow_semantic_reuse=False,
    )
    routing_task = run["tasks"][0]
    attempt = repository.create_attempt(routing_task["id"], CONFIG, {})
    plan = RoutingPlan(
        intent="research",
        horizon="event",
        tickers=["TSLA"],
        selected_analysts=["A03"],
        research_queries=["Find dated public TSLA evidence."],
        rationale="The ordinary user request needs bounded research.",
    )
    output = repository.commit_output(
        routing_task["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Ordinary route",
            summary="The ordinary route was saved.",
            analysis="This is a user-originated route.",
            routing_plan=plan,
        ),
        "real",
        CONFIG,
    )

    consumed = repository.consume_routing_plan(run["run_id"], plan, routing_output_id=output["id"])

    assert "reddit_triage" not in consumed["routing_plan"]
    assert consumed["routing_plan"]["tickers"] == ["TSLA"]
    assert "fundamental_review" in task_kinds(repository, run["run_id"])


def test_reddit_cluster_keeps_same_thesis_together_and_opposite_side_separate(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    posts = [
        reddit_post("cluster-long-a", title="$TSLA margin thesis", body="TSLA can rise because factory utilization lifts margins."),
        reddit_post("cluster-long-b", title="TSLA margin thesis", body="TSLA can rise because factory utilization lifts margins."),
        reddit_post("cluster-short", title="$TSLA margin short", body="TSLA may fall because factory utilization is deteriorating."),
    ]
    for value in posts:
        repository.ingest_reddit_result(ingest_result(value), namespace="real")

    items = repository.intake_status(namespace="real", limit=20)["items"]
    by_id = {item["external_id"]: item for item in items}
    assert by_id["t3_cluster-long-a"]["cluster_key"] == by_id["t3_cluster-long-b"]["cluster_key"]
    assert by_id["t3_cluster-long-a"]["cluster_key"] != by_id["t3_cluster-short"]["cluster_key"]
    assert set(by_id["t3_cluster-long-a"]["cluster_member_ids"]) == {
        by_id["t3_cluster-long-a"]["id"], by_id["t3_cluster-long-b"]["id"]
    }
    assert by_id["t3_cluster-short"]["reuse"]["status"] == "none"


def test_reddit_priority_exposes_duplicate_evidence_delta_and_dispatches_distinct_first(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    duplicate = reddit_post("priority-duplicate", title="TSLA margin thesis", body="TSLA can rise because factory utilization lifts margins.")
    distinct = reddit_post("priority-distinct", title="AMD catalyst", body="AMD can rise because a new product launch improves demand.")
    repository.ingest_reddit_result(ingest_result(duplicate), namespace="real")
    repository.ingest_reddit_result(ingest_result(distinct), namespace="real")
    duplicate_item = next(item for item in repository.intake_status(namespace="real", limit=20)["items"] if item["external_id"] == "t3_priority-duplicate")
    repository.persist_reddit_triage(
        "real",
        duplicate_item["id"],
        RedditTriage(
            classification="thesis",
            reason="The retained post states a supported thesis shape.",
            thesis_summary="Factory utilization can lift TSLA margins.",
            evidence_excerpt=duplicate["body"],
            tickers=["TSLA"],
        ),
    )
    first = repository.dispatch_reddit_backlog(namespace="real", max_dispatches=1, max_cost=1)
    assert first["dispatched"]
    first_id = first["dispatched"][0]["item_id"]
    second = next(item for item in repository.intake_status(namespace="real", limit=20)["items"] if item["id"] != first_id)
    assert second["priority"]["components"]["distinct_thesis"] is True
    assert second["priority"]["components"]["expected_research_cost"] in {"unknown", "high"}

    related = reddit_post("priority-related", title="TSLA margin thesis", body="TSLA can rise because factory utilization lifts margins and exports improve.")
    repository.ingest_reddit_result(ingest_result(related), namespace="real")
    related_item = next(item for item in repository.intake_status(namespace="real", limit=20)["items"] if item["external_id"] == "t3_priority-related")
    repository.persist_reddit_triage(
        "real",
        related_item["id"],
        RedditTriage(
            classification="thesis",
            reason="The retained post states a supported thesis shape.",
            thesis_summary="Factory utilization can lift TSLA margins.",
            evidence_excerpt=related["body"],
            tickers=["TSLA"],
        ),
    )
    related_item = next(item for item in repository.intake_status(namespace="real", limit=20)["items"] if item["external_id"] == "t3_priority-related")
    assert related_item["reuse"]["status"] == "duplicate"
    assert related_item["reuse"]["current_item_id"] == first_id
    assert related_item["reuse"]["blind_reuse_blocked"] is True
    assert "evidence delta" in related_item["reuse"]["evidence_delta"]
