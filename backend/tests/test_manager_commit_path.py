"""Synthetic manager decisions through the actual persistence boundary."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.case_store import CaseDecisionStore
from backend.app.schemas import AgentOutputPayload, FactClaim, ImportRequest, ModelConfig, RunCreate
from backend.tests.test_investment_engine import _v2_payload, _snapshot, _short_v2_case


def create_manager_case(data_dir, direction="long", *, reference="local", repository=None):
    repo=repository or Repository(config=Settings(project_root=Path(__file__).resolve().parents[2],data_dir=Path(data_dir),enable_market_connectors=False,enable_reddit_intake=False))
    now=datetime.now(timezone.utc).replace(microsecond=0)
    recent=(now-timedelta(hours=2)).isoformat()
    quote_date=(now-timedelta(days=1)).date().isoformat()
    period=f"{now.year-1}-12-31"
    payload,snapshot,original_sources=_short_v2_case() if direction=="short" else (_v2_payload(),_snapshot(),{})
    ticker="SYN" if direction=="short" else "ABC"
    candidate=payload["candidate_briefs"][0]
    filing=repo.import_evidence(ImportRequest(namespace="real",kind="evidence",idempotency_key="manager-filing-"+direction,title=ticker+" synthetic issuer filing",source_url="https://www.sec.gov/Archives/synthetic-manager-"+direction,publication_at=f"{now.year}-01-01",content=f"{ticker} Inc reported GAAP diluted EPS 8 USD/share for the year ended {period}."))["source_id"]
    market=repo.import_evidence(ImportRequest(namespace="real",kind="evidence",idempotency_key="manager-market-"+direction,title=ticker+" synthetic quote",source_url="https://data.alpaca.markets/v2/stocks/"+ticker+"/bars",observed_at=quote_date,content=f"{ticker} low price 90 USD/share on {quote_date}.\n{ticker} high price 100 USD/share on {quote_date}."))["source_id"]
    replacements={"short-filing":filing,"filing":filing,"short-market":market,"market":market,"fact-short-eps":"eps_baseline","fact-eps":"eps_baseline","2025-12-31":period,"2026-09-16":recent}
    def replace(value):
        if isinstance(value,dict): return {key:replace(item) for key,item in value.items()}
        if isinstance(value,list): return [replace(item) for item in value]
        return replacements.get(value,value) if isinstance(value,str) else value
    payload,snapshot=replace(payload),replace(snapshot)
    candidate=payload["candidate_briefs"][0]
    candidate["entry_zone"].update(as_of=quote_date,basis="A conditional entry within the retained daily low/high range.")
    candidate["action_plan"]["review_at"]=(now+timedelta(days=30)).isoformat()
    candidate["action_plan"]["catalyst_events"]=[]
    candidate["thesis"]["market_expectation"]="Research inference: the current price assumes flat fundamentals."
    fields=set(FactClaim.model_fields)
    fact={key:value for key,value in payload["fact_claims"][0].items() if key in fields and key not in {"semantic_status","binding_checks","matched_excerpt","source_version","freshness"}}
    fact["claim_id"]="eps_baseline"
    payload["fact_claims"]=[fact]+[{"claim_id":"quote_"+metric,"claim":ticker+" "+metric,"subject":ticker,"metric":metric,"value":price,"unit":"USD/share","currency":"USD","period":quote_date,"source_ref":market,"locator":f"L{index}"} for index,(metric,price) in enumerate((("low","90"),("high","100")),1)]
    payload.update(title="Synthetic manager "+direction+" review",source_refs=[filing,market],decision_disposition="recommend")
    run,_=repo.create_run(RunCreate(question="Synthetic manager "+direction+" investment",ticker=ticker,namespace="real",source_ids=[filing,market],idempotency_key="manager-case-"+direction+"-"+reference),[("A03","research_synthesis","Synthetic prior evidence",[]),("A11","cio_review","Synthetic committee review",["research_synthesis"])])
    sources=repo.source_packet("real",[filing,market])
    versions={s["id"]:{"hash":s["content_hash"],"version":s["version"]} for s in sources}
    model=ModelConfig(provider="codex",model="gpt-6-astra",reasoning_effort="ultra")
    prior_attempt=repo.create_attempt(run["tasks"][0]["id"],model,versions)
    prior=repo.commit_output(run["tasks"][0]["id"],prior_attempt["attempt_id"],AgentOutputPayload(status="completed",title="Synthetic filing facts",summary="Retained baseline",analysis="Synthetic dated filing baseline",fact_claims=[FactClaim.model_validate(fact)],source_refs=[filing]),"real",model)
    prior_fact=repo.output_with_sources(prior["id"])["output"]["fact_claims"][0]["fact_id"]
    if reference in {"prior","unsupplied"}:
        def remap(value):
            if isinstance(value,dict): return {key:remap(item) for key,item in value.items()}
            if isinstance(value,list): return [remap(item) for item in value]
            return prior_fact if value=="eps_baseline" else value
        payload=remap(payload)
        payload["fact_claims"]=payload["fact_claims"][1:]
    attempt=repo.create_attempt(run["tasks"][1]["id"],model,versions)
    repo.record_attempt_decision_inputs(attempt["attempt_id"],{"portfolio_snapshot":snapshot,"account_snapshot_id":"synthetic-frozen","deterministic_market":snapshot["deterministic_market"],"prior_output_ids":[prior["id"]] if reference=="prior" else [],"prior_fact_ids":[prior_fact] if reference=="prior" else []})
    committed=repo.commit_output(run["tasks"][1]["id"],attempt["attempt_id"],AgentOutputPayload.model_validate(payload),"real",model)
    decision=CaseDecisionStore(repo).persist(run["run_id"],committed["id"])
    return repo,decision,committed


@pytest.mark.parametrize("direction",["long","short"])
@pytest.mark.parametrize("reference",["local","prior"])
def test_complete_manager_decision_survives_real_commit(tmp_path,direction,reference):
    repo,decision,output=create_manager_case(tmp_path,direction,reference=reference)
    candidate=decision["candidates"][0]
    assert candidate["outcome"] == "recommend", [(b["key"],b["reason"]) for b in candidate["material_blockers"]]
    assert candidate["recommendation_gate"]["status"] == "pass"
    assert candidate["sizing"]["recommended_shares"] == 5
    assert not decision["fact_reference_audit"]["unresolved"]
    assert all(ref.startswith("fact_") for ref in candidate["thesis"]["supporting_claim_ids"])
    assert CaseDecisionStore(repo).persist(decision["run_id"],output["id"]) == decision


def test_unsupplied_prior_fact_cannot_enter_new_decision(tmp_path):
    _,decision,_=create_manager_case(tmp_path,reference="unsupplied")
    assert decision["candidates"][0]["outcome"] != "recommend"
    assert decision["fact_reference_audit"]["unresolved"]


def test_duplicate_alias_and_reserved_server_alias_fail_at_provider_boundary():
    common={"claim":"Synthetic","claim_id":"same","source_ref":"source","locator":"L1"}
    with pytest.raises(ValueError,match="unique"):
        AgentOutputPayload(status="completed",title="Duplicate",summary="Duplicate",analysis="Duplicate",fact_claims=[common,common])
    with pytest.raises(ValueError,match="reserved"):
        FactClaim.model_validate(common | {"claim_id":"fact_reserved"})


def test_fact_reference_resolution_rejects_changed_frozen_source_and_missing_allowlist(tmp_path):
    from backend.app.research.fact_references import resolve_fact_references
    repo,decision,output=create_manager_case(tmp_path,reference="prior")
    with repo.db.operation() as conn:
        row=conn.execute("SELECT * FROM outputs WHERE id=?",(output["id"],)).fetchone()
        attempt=conn.execute("SELECT source_versions_json FROM task_attempts WHERE id=?",(row["attempt_id"],)).fetchone()
    inputs=repo.attempt_decision_inputs(row["attempt_id"])
    facts=repo.output_dict(row)["fact_claims"]
    payload=json.loads(row["payload_json"])
    sources=repo.source_packet("real",list(json.loads(attempt[0])))
    for field in ("content_hash","version"):
        changed=deepcopy(sources)
        for source in changed:
            source[field]="different-version" if field=="content_hash" else 999
        projected,audit=resolve_fact_references(repo,row,payload,facts,inputs,changed)
        assert audit["unresolved"] and not audit["resolved"]
        assert all(fact["semantic_status"] == "unavailable" for fact in projected["fact_claims"])
    projected,audit=resolve_fact_references(repo,row,payload,facts,{**inputs,"prior_fact_ids":[]},sources)
    assert audit["unresolved"] and len(projected["fact_claims"])==2
    assert projected["candidate_briefs"][0]["_fact_reference_errors"]
    assert payload==json.loads(row["payload_json"])
