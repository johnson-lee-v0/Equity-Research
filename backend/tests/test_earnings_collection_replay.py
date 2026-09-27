"""Offline collection replay across durable checkpoints and source amendments.

The one-shot identity branches live in test_earnings_sources.py. This fixture
joins those branches through the public collector and persisted workflow store,
so retries cannot quietly turn stale proof into trusted company identity.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.discovery import FetchedSource
from backend.app.research.earnings_sources import EarningsAcquisition
from backend.app.research.source_archive import archive_public_page
from backend.app.research.workflows import WorkflowStore


FIXTURE = Path(__file__).parent / "fixtures/earnings_identity_replay/separate_ir_domain.json"


@pytest.fixture
def replay(tmp_path, monkeypatch):
    data = json.loads(FIXTURE.read_text())
    urls, now = data["urls"], data["now"]
    config = Settings(data_dir=tmp_path, shared_memory_vault_path=tmp_path / "memory-vault")
    for module, clock in (
        ("backend.app.research.earnings_sources", "_now"),
        ("backend.app.research.workflows", "utc_now"),
        ("backend.app.memory.repository", "utc_now"),
        ("backend.app.db", "utc_now"),
    ):
        monkeypatch.setattr(f"{module}.{clock}", lambda: now)

    def forbid_network(*args, **kwargs):
        raise AssertionError("Collection replay must not access the network")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)
    pages = {}
    for key, body in data["pages"].items():
        url = urls[key]
        pages[url] = FetchedSource(
            url, url, body["content"], url, now, body.get("error"),
            links=tuple(body["links"]),
        )
    for key in ("directory", "submissions"):
        url = urls[key]
        pages[url] = FetchedSource(url, url, json.dumps(data[key]), url, now)
    fetched, discovered = [], []

    def fetch(url, **kwargs):
        assert kwargs == {"max_bytes": config.max_source_bytes, "timeout": 20.0}
        fetched.append(url)
        assert url in pages, f"Add an explicit fixture for unexpected fetch: {url}"
        return pages[url]

    def discover(stage, prompt, schema):
        discovered.append(stage)
        if stage == "event":
            return deepcopy(data["event"])
        if stage == "issuer_domain":
            # Simulate the original model's inability to find a working link.
            return {"issuer_page_url": "", "alternate_issuer_page_urls": []}
        raise AssertionError(f"Unexpected discovery stage in identity replay: {stage}")

    def reopen():
        # A new repository and acquisition instance cannot reuse an in-memory
        # company object or page cache from the previous collection.
        repo = Repository(config=config)
        collector = EarningsAcquisition(
            repo, None, config, fetcher=fetch, discoverer=discover,
            today=date.fromisoformat(now[:10]),
        )
        return repo, collector

    return SimpleNamespace(
        data=data, urls=urls, pages=pages, fetched=fetched, discovered=discovered,
        reopen=reopen,
    )


def collect_and_checkpoint(replay):
    repo, collector = replay.reopen()

    async def collect():
        company = await collector.resolve(replay.data["ticker"])
        return company, await collector.locate(company)

    company, event = asyncio.run(collect())
    store = WorkflowStore(repo)
    workflow_id = store.create(replay.data["ticker"])
    store.step(workflow_id, "resolve", "completed", output=company)
    store.step(workflow_id, "locate", "completed", output=event)
    # This replay stops at verified event collection; it makes no claim to have
    # collected a transcript, valuation or full investment decision.
    store.finish(workflow_id, "partial", {"company": company, "event": event})
    return repo, workflow_id, company, event


def test_blocked_sec_separate_ir_chain_survives_restart_without_identity_search(replay):
    repo, workflow_id, company, event = collect_and_checkpoint(replay)
    urls = replay.urls
    assert company["website"] == company["investor_website"] == ""
    assert event["verification"] == "primary_release"
    assert event["period_end"] == "2025-06-30"
    assert replay.discovered == ["event"]
    assert replay.fetched == [
        urls[key] for key in (
            "directory", "submissions", "blocked_hint", "release", "about", "leadership", "ir"
        )
    ]
    receipt = event["issuer_domain_verification"]
    assert receipt["issuer_page_url"] == urls["leadership"]
    assert receipt["investor_website"] == urls["ir"]
    assert receipt["verified_at"] == replay.data["now"]
    archived = repo.source_packet("real", [event["release_source_id"]])
    assert archived[0]["content"] == replay.pages[urls["release"]].content

    replay.fetched.clear()
    replay.discovered.clear()
    reopened_repo, collector = replay.reopen()
    checkpoint = WorkflowStore(reopened_repo).get(workflow_id)
    assert next(step["output"] for step in checkpoint["steps"] if step["id"] == "locate") == event

    async def recollect():
        current = await collector.resolve(replay.data["ticker"])
        return current, await collector.locate(current)

    current, repeated = asyncio.run(recollect())
    assert current["identity_reuse"]["issuer_domain_workflow_id"] == workflow_id
    assert repeated["issuer_domain_verification"] == receipt
    assert repeated["release_source_id"] == event["release_source_id"]
    assert replay.discovered == ["event"]  # Latest event is still checked anew.
    assert replay.fetched == [urls["submissions"], urls["release"]]


@pytest.mark.parametrize("backlink_still_exists", [True, False], ids=["rediscover", "reject"])
def test_amended_corporate_proof_requires_fresh_link_verification(replay, backlink_still_exists):
    repo, _workflow_id, _company, old_event = collect_and_checkpoint(replay)
    urls = replay.urls
    previous = old_event["issuer_domain_verification"]["sources"][0]
    replacement = replace(
        replay.pages[urls["leadership"]],
        content="Novum Corporation updated leadership and governance information.",
        links=(urls["ir"],) if backlink_still_exists else (),
    )
    replay.pages[urls["leadership"]] = replacement
    # Use the real immutable amendment path, not a malformed hand-built receipt
    # or a direct SQL corruption of the old source.
    amendment = archive_public_page(repo, replacement, namespace="real", scope="offline-replay")
    assert amendment["status"] == "updated"
    assert amendment["source_id"] != previous["source_id"]
    replay.fetched.clear()
    replay.discovered.clear()
    _repo, collector = replay.reopen()
    current = asyncio.run(collector.resolve(replay.data["ticker"]))
    assert current["investor_website"] == ""
    assert "issuer_domain_verification" not in current

    if backlink_still_exists:
        event = asyncio.run(collector.locate(current))
        assert event["verification"] == "primary_release"
        assert event["issuer_domain_verification"]["sources"][0]["source_id"] == amendment["source_id"]
        assert replay.discovered == ["event"]
    else:
        with pytest.raises(ValueError, match="issuer or SEC URL"):
            asyncio.run(collector.locate(current))
        assert replay.discovered == ["event", "issuer_domain"]
    assert urls["leadership"] in replay.fetched
    assert urls["blocked_hint"] in replay.fetched
    assert urls["directory"] not in replay.fetched  # CIK is freshly SEC-confirmed.
