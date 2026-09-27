"""Historical valuation displays must use the assessment's frozen sources."""
import hashlib
import json

from backend.app.research.valuation_context import compile_valuation_context, compact_valuation_context
from backend.app.schemas import ImportRequest
from backend.tests.test_assessment_pipeline import case


def test_context_rejects_unfrozen_supplemental_price_source(case):
    repo, rid, _, sid, _, _ = case
    extra = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Supplemental history",
        content="A retained supplementary observation.", source_url="https://example.org/history",
        idempotency_key="supplemental-history"))["source_id"]
    sources = repo.source_packet("real", [sid, extra])
    assert compile_valuation_context(repo, rid, sources, as_of="2026-09-26") is None
    repo.append_run_sources(rid, [extra], reason="Test retained supplement")
    assert compile_valuation_context(repo, rid, sources, as_of="2026-09-26") is not None


def test_context_rejects_changed_supplement_even_when_current_hash_matches(case):
    repo, rid, _, sid, _, _ = case
    extra = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Supplemental history",
        content="Original archived observation.", source_url="https://example.org/history",
        idempotency_key="supplemental-history"))["source_id"]
    repo.append_run_sources(rid, [extra], reason="Test retained supplement")
    frozen = json.loads(repo.run_record(rid)["input_snapshot_json"])["source_versions"]
    sources = repo.source_packet("real", [sid, extra])
    changed = next(row for row in sources if row["id"] == extra)
    changed.update(content="A different observation.", version=2)
    changed["content_hash"] = hashlib.sha256(changed["content"].encode()).hexdigest()
    assert compile_valuation_context(repo, rid, sources, as_of="2026-09-26", frozen_versions=frozen) is None


def test_context_accepts_exact_attempt_hash_format(case):
    repo, rid, _, sid, _, _ = case
    sources = repo.source_packet("real", [sid])
    frozen = {row["id"]: {"hash": row["content_hash"], "version": row["version"]} for row in sources}
    assert compile_valuation_context(repo, rid, sources, as_of="2026-09-26", frozen_versions=frozen) is not None
    frozen[sid]["version"] = 2
    assert compile_valuation_context(repo, rid, sources, as_of="2026-09-26", frozen_versions=frozen) is None


def test_prompt_compaction_keeps_every_month_and_leaves_full_report_intact():
    full = {"historical_pe": {"points": [dict(date=f"2025-{month:02}-28", pe=30+month, ttm_eps=10,
        source_refs=["prices", "eps"], eps_components=[{"value": 10, "source_refs": ["eps"]}]) for month in range(1, 13)]}}
    compact = compact_valuation_context(full)
    assert len(compact["historical_pe"]["points"]) == 12
    assert all(point["source_refs"] == ["prices", "eps"] for point in compact["historical_pe"]["points"])
    assert all("eps_components" not in point for point in compact["historical_pe"]["points"])
    assert all(point["eps_components"] for point in full["historical_pe"]["points"])
