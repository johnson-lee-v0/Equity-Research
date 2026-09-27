"""Review receipts preserve SEC facts' independently frozen issuer evidence."""
from __future__ import annotations

import json
import asyncio

import pytest

from backend.app.research.earnings_financials import sec_eps_observations
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.decision_questions import candidate_proposal_hash
from backend.app.schemas import AgentOutputPayload, CandidateDecisionBrief, AstraLayaResponse, FactClaim, ImportRequest, RunCreate
from backend.tests.test_assessment_evidence import RELEASE, sec_source, seed
from backend.tests.test_five_question_commit_path import _DeterministicLaya
from backend.tests.test_laya_frozen_review import _five_question_rows
from backend.tests.test_laya_review_boundaries import MODEL, _attempt_fact, _record_review, _repo


def sec_review_case(tmp_path, *, with_candidate=False):
    repo = _repo(tmp_path)
    raw, metadata = sec_source()
    ids = {}
    for name, content, url in [("sec", raw, metadata["url"]), ("release", RELEASE, "https://investor.example.com/results")]:
        ids[name] = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title=f"ABC {name}",
            source_url=url, content=content, idempotency_key=f"sec-review-{name}"))["source_id"]
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET publication_at=?,observed_at=?,retrieval_at=?", ("2026-02-01T00:00:00Z",) * 3)
    run, _ = repo.create_run(RunCreate(namespace="real", ticker="ABC", question="Review SEC earnings with archived issuer evidence",
        source_ids=list(ids.values()), idempotency_key="sec-review-run",
        research_contract="five-questions.v1" if with_candidate else None),
        [("A03", "research_synthesis", "Seed source-bound EPS", []), ("A11", "cio_review", "Review source-bound EPS", ["research_synthesis"])],
        allow_semantic_reuse=False)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of=? WHERE id=?", ("2026-09-25T00:00:00Z", run["run_id"]))
    versions = {source["id"]: {"version": source["version"], "hash": source["content_hash"]} for source in repo.source_packet("real", list(ids.values()))}
    a03_task, a11_task = run["tasks"]
    a03 = repo.create_attempt(a03_task["id"], MODEL, versions)["attempt_id"]
    observation = sec_eps_observations(raw, metadata, issuer="ABC", cik="1234", as_of="2026-09-25")[0]
    claim = FactClaim.model_validate(seed(observation, ids["sec"]) | {"claim_id": "c1"})
    output = repo.commit_output(a03_task["id"], a03, AgentOutputPayload(status="completed", title="Source-bound EPS",
        summary="Annual diluted EPS with independently archived issuer identity.", analysis="The SEC row and release identify the same company.",
        source_refs=list(ids.values()), fact_claims=[claim], candidate_briefs=[CandidateDecisionBrief(
            ticker="ABC", stance="watch", entry_advice="Review sourced EPS.", key_questions=_five_question_rows("c1"))] if with_candidate else []), "real", MODEL)
    fact, binding = _attempt_fact(repo, output["id"], a03)
    assert fact["semantic_status"] == "supported" and fact["freshness"] == "fresh"
    a11 = repo.create_attempt(a11_task["id"], MODEL, versions)["attempt_id"]
    repo.record_attempt_decision_inputs(a11, {"prior_output_ids": [output["id"]], "prior_fact_ids": [fact["fact_id"]]})
    return repo, run["run_id"], a03, a11, ids, versions, binding


def test_sec_eps_can_be_revalidated_for_pre_and_post_receipts(tmp_path):
    repo, run_id, a03, a11, _, _, binding = sec_review_case(tmp_path)
    for phase, attempt in [("pre_a11", a03), ("post_astra", a11)]:
        receipt = _record_review(repo, run_id=run_id, attempt_id=attempt, candidate_key="ABC", phase=phase,
            proposal_hash="a" * 64, fact_bindings=[binding], tag=phase)
        assert receipt["status"] == "ok"
        assert json.loads(receipt["fact_bindings_json"])[0]["fact_id"] == binding["fact_id"]


@pytest.mark.parametrize("mutation", ["omitted", "frozen_hash", "frozen_version", "raw_content", "version_content", "foreign_namespace", "superseded"])
def test_related_identity_source_must_be_present_exact_current_and_in_namespace(tmp_path, mutation):
    repo, run_id, _, a11, ids, versions, binding = sec_review_case(tmp_path)
    release = ids["release"]
    with repo.db.transaction(immediate=True) as conn:
        if mutation == "omitted":
            versions.pop(release)
        elif mutation == "frozen_hash":
            versions[release]["hash"] = "0" * 64
        elif mutation == "frozen_version":
            versions[release]["version"] = 2
        elif mutation == "raw_content":
            conn.execute("UPDATE sources SET original_content=original_content || ? WHERE id=?", (" altered identity", release))
        elif mutation == "version_content":
            conn.execute("UPDATE source_versions SET content=content || ? WHERE source_id=?", (" altered version", release))
        elif mutation == "foreign_namespace":
            conn.execute("UPDATE sources SET namespace='demo' WHERE id=?", (release,))
        elif mutation == "superseded":
            # Existing SEC row acts only as an amendment marker in this
            # adversarial fixture; no contents or frozen hashes are changed.
            conn.execute("UPDATE sources SET supersedes_source_id=? WHERE id=?", (release, ids["sec"]))
        conn.execute("UPDATE task_attempts SET source_versions_json=? WHERE id=?", (json.dumps(versions), a11))
    with pytest.raises(ValueError, match="review (?:frozen source packet|fact binding failed current semantic or freshness)"):
        _record_review(repo, run_id=run_id, attempt_id=a11, phase="post_astra", proposal_hash="b" * 64,
                       fact_bindings=[binding], tag=mutation)


def test_unbound_archive_identity_cannot_rescue_omitted_frozen_release(tmp_path):
    repo, run_id, _, a11, ids, versions, binding = sec_review_case(tmp_path)
    # The complete, correct release still exists in the namespace. It must
    # not be searched merely because the direct SEC source needs identity.
    versions.pop(ids["release"])
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE task_attempts SET source_versions_json=? WHERE id=?", (json.dumps(versions), a11))
    with pytest.raises(ValueError, match="failed current semantic or freshness"):
        _record_review(repo, run_id=run_id, attempt_id=a11, phase="post_astra", proposal_hash="c" * 64,
                       fact_bindings=[binding], tag="no-archive-search")


def projection_case(tmp_path):
    repo, run_id, a03, a11, ids, versions, binding = sec_review_case(tmp_path, with_candidate=True)
    pre = _record_review(repo, run_id=run_id, attempt_id=a03, phase="pre_a11", fact_bindings=[binding])
    repo.freeze_decision_review_ids(a11, [pre["id"]])
    engine = Orchestrator(repo, object(), repo.config, laya_runtime=_DeterministicLaya())
    return repo, engine, run_id, a11, ids, versions, binding


def project_pre(repo, engine, run_id, a11, versions, sources=None):
    return engine._frozen_pre_projection(run_id, "real", repo.attempt_decision_inputs(a11), candidate_key="ABC",
        current_versions=versions, review_attempt_id=a11, review_as_of=repo.run_record(run_id)["as_of"],
        review_sources=sources if sources is not None else repo.source_packet("real", list(versions)))


def test_frozen_pre_projection_uses_related_issuer_source_without_extra_facts(tmp_path):
    repo, engine, run_id, a11, _, versions, binding = projection_case(tmp_path)
    projection = project_pre(repo, engine, run_id, a11, versions)
    assert projection is not None
    assert set(projection[1]) == {binding["fact_id"]}
    assert projection[1][binding["fact_id"]]["semantic_status"] == "supported"
    assert len(projection[2]) == 5


@pytest.mark.parametrize("mutation", ["omitted", "frozen_hash", "frozen_version", "raw_content", "version_content", "foreign_namespace", "superseded", "packet_content", "packet_extra"])
def test_frozen_projection_requires_exact_full_packet_for_issuer_binding(tmp_path, mutation):
    repo, engine, run_id, a11, ids, versions, _ = projection_case(tmp_path)
    sources = repo.source_packet("real", list(versions))
    release = ids["release"]
    with repo.db.transaction(immediate=True) as conn:
        if mutation == "omitted":
            versions.pop(release)
            sources = [s for s in sources if s["id"] != release]
        elif mutation == "frozen_hash":
            versions[release]["hash"] = "0" * 64
        elif mutation == "frozen_version":
            versions[release]["version"] = 2
        elif mutation == "raw_content":
            conn.execute("UPDATE sources SET original_content=original_content || ? WHERE id=?", (" changed", release))
        elif mutation == "version_content":
            conn.execute("UPDATE source_versions SET content=content || ? WHERE source_id=?", (" changed", release))
        elif mutation == "foreign_namespace":
            conn.execute("UPDATE sources SET namespace='demo' WHERE id=?", (release,))
        elif mutation == "superseded":
            conn.execute("UPDATE sources SET supersedes_source_id=? WHERE id=?", (release, ids["sec"]))
        elif mutation == "packet_content":
            next(s for s in sources if s["id"] == release)["content"] += " injected"
        elif mutation == "packet_extra":
            sources.append(sources[0] | {"id": "src_outside_frozen_packet"})
        conn.execute("UPDATE task_attempts SET source_versions_json=? WHERE id=?", (json.dumps(versions), a11))
    try:
        projection = project_pre(repo, engine, run_id, a11, versions, sources)
    except ValueError:
        projection = None  # A namespace violation can fail at the earlier resolver.
    assert projection is None


def test_post_review_can_append_real_resolution_without_rewriting_unavailable_receipt(tmp_path):
    repo, engine, run_id, a11, ids, _, binding = projection_case(tmp_path)
    task = next(t for t in repo.tasks_for_run(run_id) if t["agent_id"] == "A11")
    response = AstraLayaResponse(position="agree", reason="The canonical EPS supports a bounded review.",
        fact_claim_ids=[binding["fact_id"]], question_keys=[q["key"] for q in _five_question_rows(binding["fact_id"])])
    candidate = CandidateDecisionBrief(ticker="ABC", stance="watch", entry_advice="Review sourced EPS.",
        key_questions=_five_question_rows(binding["fact_id"]), laya_response=response)
    payload = AgentOutputPayload(status="completed", research_contract="five-questions.v1", title="Bounded local review",
        summary="Review source-bound EPS.", analysis="Keep the immutable numerical evidence.", source_refs=list(ids.values()),
        candidate_briefs=[candidate], laya_response=response)
    repo.control("run", run_id, "run_once")
    out = repo.commit_output(task["id"], a11, payload, "real", MODEL)
    raw_before = repo.immutable_output_payload(out["id"], "real")
    old = _record_review(repo, run_id=run_id, attempt_id=a11, phase="post_astra", status="unavailable", result=None,
        proposal_hash=candidate_proposal_hash(candidate.model_dump(mode="json")), tag="historical-unavailable")
    task = repo.task(task["id"])
    asyncio.run(engine._run_post_astra_laya(run_id, task, out["id"], payload))
    reviews = repo.decision_model_reviews(run_id, namespace="real", phase="post_astra")
    assert len(reviews) == 2
    assert next(r for r in reviews if r["id"] == old["id"])["status"] == "unavailable"
    fresh = next(r for r in reviews if r["id"] != old["id"])
    assert fresh["status"] == "ok"
    assert fresh["fact_bindings"][0]["fact_id"] == binding["fact_id"]
    assert [call["phase"] for call in engine.laya_runtime.calls] == ["post_astra"]
    assert repo.immutable_output_payload(out["id"], "real") == raw_before
