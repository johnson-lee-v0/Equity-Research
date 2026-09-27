"""Incremental trend extraction changes scheduling, never evidence rules."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.earnings_sources import EarningsAcquisition
from backend.app.research.earnings_trends import EarningsTrends, METRICS, _build_series, _guidance_review


EVENT = {"fiscal_period": "Q4 FY2026", "period_end": "2026-08-30", "earnings_date": "2026-09-24"}


def document(index, *, source_id=None):
    year, quarter = 2025 + index // 4, index % 4 + 1
    period = f"Q{quarter} FY{year}"
    quote = f"Our US and Canada renewal rate was {90 + index / 10:.1f}%."
    text = f"{period}. {quote} For FY{year + 1} we expect capital expenditures of $7.5 billion. Capital expenditures increased because we accelerated new locations."
    return {"source_id": source_id or f"source-{index}", "url": f"https://example.com/call-{index}", "title": f"Example {period} earnings", "fiscal_period": period, "period_end": None, "published_at": f"{year}-09-01", "kind": "release", "content": text}


class Provider:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.peak = 0
        self.fail = set()

    @staticmethod
    def payload(evidence):
        row = evidence[0]
        import re
        value = float(re.search(r"renewal rate was ([\d.]+)%", row["content"])[1])
        return {"observations": [{"source_id": row["source_id"], "metric": "renewal_us_canada", "value": value, "period": row["fiscal_period"], "frequency": "quarterly", "kind": "actual", "low": None, "high": None, "quote": f"Our US and Canada renewal rate was {value:.1f}%.", "period_quote": row["fiscal_period"]}], "capex_explanations": [{"source_id": row["source_id"], "period": row["fiscal_period"].split()[-1], "quote": "Capital expenditures increased because we accelerated new locations."}]}

    async def execute(self, attempt_id, prompt, model, schema, workdir, **kwargs):
        evidence = json.loads(prompt.rsplit("\n", 1)[1])
        assert len(evidence) == 1 and kwargs["discovery_stage"] is False
        self.calls.append(evidence[0]["source_id"])
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(.015)
            if evidence[0]["source_id"] in self.fail:
                raise RuntimeError("Transient one-document extraction failure")
            payload = self.payload(evidence)
            (workdir / "output-schema.json").write_text(json.dumps(schema))
            (workdir / "result.json").write_text(json.dumps(payload))
            return SimpleNamespace(payload=payload, usage={"input_tokens": 200, "output_tokens": 90})
        finally:
            self.active -= 1


class Registry:
    def __init__(self):
        self.codex = Provider()
        self.global_gate = asyncio.Semaphore(4)

    @asynccontextmanager
    async def generation_slot(self, provider, origin=None):
        assert provider == "codex" and origin == "earnings"
        async with self.global_gate:
            yield


def service(tmp_path):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path, enable_market_connectors=False, enable_reddit_intake=False)
    repo = Repository(config=config)
    registry = Registry()
    acquisition = EarningsAcquisition(repo, registry, config)
    return EarningsTrends(acquisition), registry.codex


def test_three_bounded_extractions_and_next_quarter_reuses_unchanged_documents(tmp_path):
    trends, provider = service(tmp_path)
    docs = [document(index) for index in range(2, 8)]
    async def scenario():
        cold = await trends._extract(copy.deepcopy(docs), EVENT)
        assert provider.peak == 3 and len(provider.calls) == 6
        assert not cold["extraction_gaps"]
        next_docs = [row | {"source_id": "renamed-" + row["source_id"]} for row in docs] + [document(8)]
        warm = await trends._extract(copy.deepcopy(next_docs), EVENT | {"fiscal_period": "Q1 FY2027", "earnings_date": "2027-01-01"})
        assert len(provider.calls) == 7  # Only the new quarter reaches a provider.
        assert sum(bool(row.get("cached_attempt")) for row in warm["document_extractions"]) == 6
        assert {row["source_id"] for row in warm["observations"]} == {row["source_id"] for row in next_docs}
        assert provider.active == 0
        # Compare the resulting charts and explanation/guidance projections
        # with the same source observations assembled serially.
        expected = {"observations": [], "capex_explanations": []}
        for row in next_docs:
            extracted = provider.payload([row])
            for key in expected:
                expected[key].extend(extracted[key])
        period = EVENT | {"fiscal_period": "Q1 FY2027", "earnings_date": "2027-01-01"}
        actual_points, actual_why, rejected = trends._validate_observations(warm, next_docs, period)
        expected_points, expected_why, expected_rejected = trends._validate_observations(expected, next_docs, period)
        assert rejected == expected_rejected == 0
        assert _build_series(actual_points, period, []) == _build_series(expected_points, period, [])
        assert _guidance_review(actual_points["capex"], actual_why) == _guidance_review(expected_points["capex"], expected_why)
    asyncio.run(scenario())


def test_full_source_mutation_metadata_model_schema_and_capex_mode_invalidate_cache(tmp_path, monkeypatch):
    trends, provider = service(tmp_path)
    doc = document(7)
    async def scenario():
        await trends._extract([copy.deepcopy(doc)], EVENT)
        await trends._extract([copy.deepcopy(doc)], EVENT)
        assert len(provider.calls) == 1
        # The appended text may fall outside selected windows; the complete
        # source hash must still invalidate the cache.
        await trends._extract([doc | {"content": doc["content"] + "\nAmended source context."}], EVENT)
        await trends._extract([doc | {"published_at": "2026-09-02"}], EVENT)
        await trends._extract([copy.deepcopy(doc)], EVENT, capex_only=True)
        assert len(provider.calls) == 4
        model, policy = trends.acquisition.repo.resolve_model("A01")
        changed_effort = "medium" if model.reasoning_effort != "medium" else "high"
        monkeypatch.setattr(trends.acquisition.repo, "resolve_model", lambda *args, **kwargs: (model.model_copy(update={"reasoning_effort": changed_effort}), policy))
        await trends._extract([copy.deepcopy(doc)], EVENT)
        assert len(provider.calls) == 5
        from backend.app.research import earnings_trends
        schema = copy.deepcopy(earnings_trends._EXTRACTION_SCHEMA)
        schema["properties"]["observations"]["maxItems"] = 159
        monkeypatch.setattr(earnings_trends, "_EXTRACTION_SCHEMA", schema)
        await trends._extract([copy.deepcopy(doc)], EVENT)
        assert len(provider.calls) == 6
    asyncio.run(scenario())


def test_cached_numbers_are_revalidated_and_invalid_quote_never_reaches_chart(tmp_path):
    trends, provider = service(tmp_path)
    doc = document(7)
    async def scenario():
        first = await trends._extract([copy.deepcopy(doc)], EVENT)
        directory = trends.acquisition.config.evidence_dir / "research-workflows" / "trend-extraction" / first["document_extractions"][0]["attempt_id"]
        path = directory / "result.json"
        payload = json.loads(path.read_text())
        payload["observations"][0]["value"] = 99.9
        path.write_text(json.dumps(payload))
        cached = await trends._extract([copy.deepcopy(doc)], EVENT)
        assert len(provider.calls) == 1
        points, _, rejected = trends._validate_observations(cached, [doc], EVENT)
        assert rejected == 1 and not any(points.values())
    asyncio.run(scenario())


def test_validation_upgrade_reuses_legacy_candidates_and_recovers_negative_growth(tmp_path, monkeypatch):
    from backend.app.research import earnings_trends
    trends, provider = service(tmp_path)
    doc = document(7)
    quote = "Quarterly revenues were $11.0 billion, down 1 percent on a reported basis and down 4 percent on a currency-neutral basis."
    doc["content"] += " " + quote
    original_payload = provider.payload
    def payload(evidence):
        result = original_payload(evidence)
        result["observations"] = [{**result["observations"][0], "metric": "net_sales_growth", "value": -1, "quote": quote}]
        return result
    monkeypatch.setattr(provider, "payload", payload)
    current_key = trends._document_cache_key
    def legacy_key(*args, **kwargs):
        return current_key(*args, **(kwargs | {"legacy_validation_version": "earnings-trends.validation.v3"}))
    async def scenario():
        with monkeypatch.context() as old:
            old.setattr(earnings_trends, "VALIDATION_VERSION", "earnings-trends.validation.v3")
            old.setattr(trends, "_document_cache_key", legacy_key)
            first = await trends._extract([copy.deepcopy(doc)], EVENT)
        directory = trends.acquisition.config.evidence_dir / "research-workflows" / "trend-extraction" / first["document_extractions"][0]["attempt_id"]
        provenance_path = directory / "provenance.json"
        legacy_provenance = json.loads(provenance_path.read_text())
        legacy_provenance.pop("document_content_hash")  # Real archived v3 manifests predate this field.
        provenance_path.write_text(json.dumps(legacy_provenance))
        original_archive = provenance_path.read_text()
        renamed = doc | {"source_id": "renamed-source"}
        migrated = await trends._extract([copy.deepcopy(renamed)], EVENT)
        assert len(provider.calls) == 1
        assert migrated["document_extractions"][0]["cached_attempt"] == first["document_extractions"][0]["attempt_id"]
        points, _, rejected = trends._validate_observations(migrated, [renamed], EVENT)
        assert rejected == 0
        assert points["net_sales_growth"][0]["value"] == -1
        assert points["net_sales_growth"][0]["quote"] == quote
        assert points["net_sales_growth"][0]["source_id"] == renamed["source_id"]
        assert provenance_path.read_text() == original_archive
        # Subsequent validator changes use the new raw-candidate key directly.
        monkeypatch.setattr(earnings_trends, "VALIDATION_VERSION", "future-validation-test")
        again = await trends._extract([copy.deepcopy(renamed)], EVENT)
        assert len(provider.calls) == 1 and again["document_extractions"][0]["cached_attempt"]
        # A changed full source must never enter via the legacy fallback.
        await trends._extract([renamed | {"content": renamed["content"] + " Amended outside the metric excerpt."}], EVENT)
        assert len(provider.calls) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["row_source_id", "manifest_source_id", "content_hash", "prompt_hash", "source_url", "model", "namespace"])
def test_candidate_cache_rejects_broken_source_and_provenance_bindings(tmp_path, corruption):
    trends, provider = service(tmp_path)
    doc = document(7)
    async def scenario():
        first = await trends._extract([copy.deepcopy(doc)], EVENT)
        directory = trends.acquisition.config.evidence_dir / "research-workflows" / "trend-extraction" / first["document_extractions"][0]["attempt_id"]
        provenance_path = directory / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        if corruption == "row_source_id":
            path = directory / "result.json"
            payload = json.loads(path.read_text())
            payload["observations"][0]["source_id"] = "a-different-source"
            path.write_text(json.dumps(payload))
        elif corruption == "manifest_source_id":
            provenance["source_documents"][0]["source_id"] = "a-different-source"
        elif corruption == "source_url":
            provenance["source_documents"][0]["url"] = "https://different.example.com/"
        elif corruption == "model":
            provenance["model"]["model"] = "different-model"
        elif corruption == "namespace":
            provenance["namespace"] = "different-namespace"
        else:
            provenance["document_content_hash" if corruption == "content_hash" else "prompt_hash"] = "0" * 64
        provenance_path.write_text(json.dumps(provenance))
        second = await trends._extract([copy.deepcopy(doc)], EVENT)
        assert len(provider.calls) == 2
        assert not second["document_extractions"][0].get("cached_attempt")
    asyncio.run(scenario())


def test_failed_document_retains_successful_siblings_and_retry_uses_their_cache(tmp_path):
    trends, provider = service(tmp_path)
    docs = [document(index) for index in range(3, 7)]
    provider.fail.add(docs[1]["source_id"])
    async def scenario():
        partial = await trends._extract(copy.deepcopy(docs), EVENT)
        assert len(partial["observations"]) == 3 and len(partial["extraction_gaps"]) == 1
        provider.fail.clear()
        completed = await trends._extract(copy.deepcopy(docs), EVENT)
        assert len(completed["observations"]) == 4 and not completed["extraction_gaps"]
        assert len(provider.calls) == 5 and provider.active == 0
    asyncio.run(scenario())


def test_composition_keeps_later_document_evidence_and_does_not_use_one_global_row_cap():
    trends = EarningsTrends(None)
    docs = [document(6), document(7)]
    first = Provider.payload([docs[0]])["observations"][0]
    second = Provider.payload([docs[1]])["observations"][0]
    points, _, rejected = trends._validate_observations({"observations": [first] * 160 + [second], "capex_explanations": []}, docs, EVENT)
    assert rejected == 0
    assert {item["period"] for item in points["renewal_us_canada"]} == {"Q3 FY2026", "Q4 FY2026"}


def test_two_workflows_share_the_three_document_admission_limit(tmp_path):
    trends, provider = service(tmp_path)
    another = EarningsTrends(trends.acquisition)
    async def scenario():
        await asyncio.gather(trends._extract([document(index) for index in range(2, 6)], EVENT), another._extract([document(index) for index in range(6, 10)], EVENT))
        assert provider.peak == 3 and provider.active == 0
    asyncio.run(scenario())


def test_cancelled_collection_does_not_leave_active_generations_or_cache_receipts(tmp_path):
    trends, provider = service(tmp_path)
    async def scenario():
        task = asyncio.create_task(trends._extract([document(index) for index in range(2, 9)], EVENT))
        while provider.active < 3:
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert provider.active == 0
        directory = trends.acquisition.config.evidence_dir / "research-workflows" / "trend-extraction"
        records = [json.loads(path.read_text()) for path in directory.glob("*/provenance.json")]
        assert records and all(row["status"] == "cancelled" for row in records)
        assert not list((directory / "document-cache").glob("*.json"))
    asyncio.run(scenario())
