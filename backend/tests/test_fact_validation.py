"""Adversarial regressions for source-bound fact validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import _fact_claim_validation
from backend.app.memory.repository import Repository
from backend.app.research.fact_validation import validate_fact_claim
from backend.app.schemas import AgentOutputPayload, FactClaim, ImportRequest, ModelConfig, RunCreate


SOURCE = {
    "filing": "\n".join(
        [
            "ACME Corporation | Income Statement | 2026 | Revenue | USD | 42",
            "ACME Corporation | Income Statement | 2025 | Revenue | USD | 41",
            "ACME Corporation | Income Statement | 2026 | Operating expenses | USD | 9",
            "OTHERCO Corporation | Balance Sheet | 2024 | Expenses | EUR | 42",
            "OTHERCO Corporation | Balance Sheet | 2024 | Shares outstanding | 42 shares",
        ]
    )
}


def _claim(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "claim": "ACME 2026 revenue",
        "value": "42",
        "unit": "USD",
        "period": "2026",
        "source_ref": "filing",
        "locator": "L1-L5",
    }
    value.update(updates)
    return value


def test_positive_claim_has_one_bound_row_and_separate_provenance_states() -> None:
    result = validate_fact_claim(_claim(), SOURCE, source_metadata={"filing": {"source_type": "sec_filing", "is_untrusted": True}})

    assert result["validation_status"] == "validated"
    assert result["citation_match"] == "matched"
    assert result["assertion_type"] == "issuer_assertion"
    assert result["corroboration_status"] == "not_assessed"
    assert result["extraction"]["metric"] == "revenue"


@pytest.mark.parametrize(
    "updates",
    [
        # The same numeric token exists, but only under OTHERCO.
        {"claim": "OTHERCO 2026 revenue"},
        # The value belongs to ACME's 2025 row, not the claimed year.
        {"claim": "ACME 2025 revenue", "value": "42", "period": "2025"},
        # EUR appears on a different issuer/metric row.
        {"unit": "EUR"},
        # Expenses and revenue deliberately share the value 42 in the packet.
        {"claim": "ACME 2026 expenses"},
        # A table assertion cannot relabel an income-statement row.
        {"table": "Balance Sheet"},
    ],
)
def test_swapped_issuer_year_unit_metric_or_table_stays_proposed(updates: dict[str, object]) -> None:
    result = validate_fact_claim(_claim(**updates), SOURCE)

    assert result["validation_status"] == "proposed"
    assert result["citation_match"] == "unmatched"
    assert "bound" in result["validation_reason"]


def test_structured_source_row_prevents_same_value_field_swap() -> None:
    source = {"structured": '{"issuer":"ACME","period":"2026","currency":"USD","revenue":"42","expenses":"99"}'}
    revenue = _claim(locator="L1", source_ref="structured", claim="ACME 2026 revenue")
    expenses = _claim(locator="L1", source_ref="structured", claim="ACME 2026 expenses")
    assert validate_fact_claim(revenue, source)["validation_status"] == "validated"
    assert validate_fact_claim(expenses, source)["validation_status"] == "proposed"


def test_source_version_mismatch_cannot_validate_a_citation() -> None:
    result = validate_fact_claim(
        _claim(source_version=2),
        SOURCE,
        source_metadata={"filing": {"source_type": "sec_filing"}},
        source_versions={"filing": {"version": 1, "hash": "old"}},
    )

    assert result["validation_status"] == "proposed"
    assert result["citation_match"] == "unmatched"
    assert "version" in result["validation_reason"]


def test_impossible_calendar_period_cannot_match_a_valid_source_date() -> None:
    result = validate_fact_claim(
        _claim(period="2025-99-31", locator="L1"),
        {"filing": "ACME revenue for 2025-12-31 was USD 42"},
    )

    assert result["validation_status"] == "proposed"
    assert result["citation_match"] == "unmatched"


@pytest.mark.parametrize(
    ("claim", "source"),
    [
        (
            _claim(
                claim="ACME 2026 revenue",
                value="200",
                period="2026-06-30",
                subject="ACME",
                metric="revenue",
            ),
            "ACME revenue USD100 and expenses USD200 for 2026-06-30",
        ),
        (
            _claim(
                claim="ACME 2026 revenue",
                value="30",
                period="2026-06-30",
                subject="ACME",
                metric="revenue",
            ),
            "ACME revenue USD30 for 2026-06-30",
        ),
        (
            _claim(
                claim="ACME 2026 revenue",
                value="42",
                period="2026",
                subject="ACME",
                metric="revenue",
            ),
            "ACME revenue USD42 in 2025 and 2026",
        ),
    ],
)
def test_prose_numeric_binding_rejects_multiple_facts_or_date_tokens(claim: dict[str, object], source: str) -> None:
    result = validate_fact_claim({**claim, "locator": "L1"}, {"filing": source})

    assert result["validation_status"] == "proposed"
    assert result["semantic_status"] in {"ambiguous", "mismatch"}


def test_gaap_basis_does_not_match_non_gaap_adjusted_row() -> None:
    claim = _claim(
        claim="ACME adjusted diluted EPS",
        value="1.2",
        unit="USD/share",
        period="2026",
        subject="ACME",
        metric="eps",
        basis="gaap",
    )
    result = validate_fact_claim(
        {**claim, "locator": "L1"},
        {"filing": "ACME diluted EPS USD/share 1.2 non-GAAP adjusted for 2026"},
    )

    assert result["validation_status"] == "proposed"
    assert result["semantic_status"] in {"ambiguous", "mismatch"}


def test_exact_text_quote_cannot_cross_issuer_without_bound_context() -> None:
    claim = {
        "claim": "ACME revenue grew",
        "value": "revenue grew",
        "unit": "text",
        "period": "2026",
        "subject": "ACME",
        "metric": "revenue",
        "source_ref": "filing",
        "locator": "L1",
        "source_quote": "OTHERCO revenue grew in 2026.",
    }
    result = validate_fact_claim(claim, {"filing": "OTHERCO revenue grew in 2026."})

    assert result["validation_status"] == "proposed"
    assert result["semantic_status"] == "mismatch"
    assert "issuer, metric and period" in result["validation_reason"]


def test_repository_adapter_keeps_structured_validation_fields() -> None:
    result = _fact_claim_validation(_claim(), SOURCE)

    assert result["validation_status"] == "validated"
    assert result["citation_match"] == "matched"
    assert result["corroboration_status"] == "not_assessed"


def test_ohlc_json_rows_keep_exact_field_binding() -> None:
    source = {
        "bars": "\n".join(
            [
                "Retained market bars",
                '{"source_type":"market_bars","metadata":{"currency":"USD"}}',
                '{"symbol":"BE","currency":"USD","timestamp":"2026-09-10T04:00:00Z","complete":true,"open":"259.86","high":"270.98","low":"257.24","close":"258.47","volume":"323093"}',
            ]
        )
    }
    valid = {
        "claim": "BE historical daily low",
        "value": "257.24",
        "unit": "USD/share",
        "period": "2026-09-10",
        "source_ref": "bars",
        "locator": "L3",
    }
    swapped = {**valid, "claim": "BE historical daily low", "value": "270.98"}
    assert validate_fact_claim(valid, source)["validation_status"] == "validated"
    assert validate_fact_claim(swapped, source)["validation_status"] == "proposed"


def test_commit_overwrites_provider_semantic_projection(tmp_path: Path) -> None:
    repository = Repository(config=Settings(data_dir=tmp_path, project_root=Path(__file__).resolve().parents[2], codex_timeout_seconds=30))
    imported = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="ACME filing",
            content="ACME 2026 Revenue USD 42",
            source_url="https://issuer.example/acme",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="fact-projection-source",
        )
    )
    run, _ = repository.create_run(
        RunCreate(
            question="Review ACME revenue",
            namespace="real",
            horizon="1m",
            ticker="ACME",
            source_ids=[imported["source_id"]],
            idempotency_key="fact-projection-run",
        ),
        [("A03", "fundamental_review", "Review", [])],
    )
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")
    task_id = run["tasks"][0]["id"]
    attempt = repository.create_attempt(task_id, config, {imported["source_id"]: {"version": 1}})
    claim = FactClaim(
        claim="ACME 2026 revenue",
        value="42",
        unit="USD",
        period="2026",
        source_ref=imported["source_id"],
        locator="L1",
        semantic_status="supported",
        matched_excerpt="provider fabricated excerpt",
        source_quote="provider fabricated quote",
        source_version="999",
        freshness="fresh",
    )
    committed = repository.commit_output(
        task_id,
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="ACME revenue",
            summary="Sourced revenue",
            analysis="The filing reports revenue.",
            fact_claims=[claim],
            source_refs=[imported["source_id"]],
        ),
        "real",
        config,
    )
    saved = repository.output_with_sources(committed["id"])["output"]["fact_claims"][0]
    assert saved["semantic_status"] == "mismatch"
    assert saved["matched_excerpt"] is None
    assert saved["source_quote"] is None
    assert saved["source_version"] == "999"
    assert saved["freshness"] == "unknown"


def test_explicit_binding_context_requires_scale_basis_and_statement_row() -> None:
    source = {
        "filing": "\n".join(
            [
                "ACME Income Statement",
                "ACME | 2026 | Revenue | USD millions | 42",
                "ACME | 2026 | Operating expenses | USD millions | 42",
                "ACME Balance Sheet",
                "ACME | 2026 | Weighted-average diluted shares | shares | 9",
            ]
        )
    }
    valid = {
        "claim": "ACME revenue",
        "value": "42",
        "unit": "USD",
        "scale": "millions",
        "currency": "USD",
        "period": "2026",
        "subject": "ACME",
        "metric": "revenue",
        "statement_type": "income_statement",
        "source_quote": "ACME | 2026 | Revenue | USD millions | 42",
        "source_ref": "filing",
        "locator": "L1-L3",
    }
    result = validate_fact_claim(valid, source)
    assert result["validation_status"] == "validated"
    assert result["semantic_status"] == "supported"
    assert all(check["status"] == "pass" for check in result["binding_checks"] if check["key"] in {"issuer", "metric", "unit", "scale", "currency", "period", "statement_type"})

    wrong_scale = {**valid, "scale": "thousand"}
    assert validate_fact_claim(wrong_scale, source)["validation_status"] == "proposed"

    wrong_quote = {**valid, "source_quote": "ACME | 2026 | Revenue | EUR | 42"}
    assert validate_fact_claim(wrong_quote, source)["semantic_status"] == "mismatch"

    wrong_share_basis = {
        "claim": "ACME outstanding shares",
        "value": "9",
        "unit": "shares",
        "period": "2026",
        "subject": "ACME",
        "metric": "shares",
        "basis": "outstanding",
        "source_ref": "filing",
        "locator": "L5",
    }
    assert validate_fact_claim(wrong_share_basis, source)["validation_status"] == "proposed"


def test_ambiguous_fixed_width_multicolumn_table_is_not_ordinally_guessed() -> None:
    source = {
        "table": "\n".join(
            [
                "ACME Revenue table",
                "                 FY2026          FY2025",
                "Revenue           42              41",
            ]
        )
    }
    claim = {
        "claim": "ACME revenue",
        "value": "42",
        "unit": "USD",
        "period": "2026",
        "subject": "ACME",
        "metric": "revenue",
        "source_ref": "table",
        "locator": "L1-L3",
    }
    result = validate_fact_claim(claim, source)
    assert result["validation_status"] == "proposed"
    assert result["semantic_status"] in {"ambiguous", "mismatch"}


def test_table_row_issuer_cannot_be_inherited_from_heading() -> None:
    source = {
        "table": "\n".join(
            [
                "ACME Revenue table",
                "USD                 2026        2025",
                "OTHERCO revenue     42          41",
            ]
        )
    }
    claim = {
        "claim": "ACME revenue",
        "value": "42",
        "unit": "USD",
        "period": "2026",
        "subject": "ACME",
        "metric": "revenue",
        "source_ref": "table",
        "locator": "L1-L3",
    }

    result = validate_fact_claim(claim, source)

    assert result["validation_status"] == "proposed"
    assert result["semantic_status"] in {"ambiguous", "mismatch"}


def test_table_row_share_basis_cannot_be_inherited_from_heading() -> None:
    source = {
        "table": "\n".join(
            [
                "ACME outstanding shares table",
                "shares                                2026        2025",
                "Weighted-average diluted shares       42          41",
            ]
        )
    }
    claim = {
        "claim": "ACME outstanding shares",
        "value": "42",
        "unit": "shares",
        "period": "2026",
        "subject": "ACME",
        "metric": "shares",
        "basis": "outstanding",
        "source_ref": "table",
        "locator": "L1-L3",
    }

    result = validate_fact_claim(claim, source)

    assert result["validation_status"] == "proposed"
    assert result["semantic_status"] in {"ambiguous", "mismatch"}


def test_export_keeps_learning_and_document_metadata_namespace_scoped(tmp_path: Path) -> None:
    repository = Repository(config=Settings(data_dir=tmp_path, project_root=Path(__file__).resolve().parents[2]))
    run_ids: dict[str, str] = {}
    source_ids: dict[str, str] = {}
    for namespace in ("real", "demo"):
        imported = repository.import_evidence(
            ImportRequest(
                namespace=namespace,
                kind="evidence",
                title=f"{namespace} filing",
                content=f"{namespace} source",
                idempotency_key=f"export-fact-{namespace}",
            )
        )
        source_ids[namespace] = imported["source_id"]
        run, _ = repository.create_run(
            RunCreate(
                question=f"{namespace} export fixture",
                namespace=namespace,
                idempotency_key=f"export-fact-run-{namespace}",
            ),
            [("A03", "fundamental_review", "Review", [])],
        )
        run_ids[namespace] = run["run_id"]

    with repository.db.transaction(immediate=True) as conn:
        for namespace in ("real", "demo"):
            baseline_id = f"baseline-{namespace}"
            now = f"2026-09-{17 if namespace == 'real' else 16:02d}T00:00:00Z"
            conn.execute(
                "INSERT INTO idea_baselines(id,namespace,run_id,decision_revision,candidate_key,ticker,direction,outcome,baseline_json,frozen_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (baseline_id, namespace, run_ids[namespace], 1, f"candidate-{namespace}", "ACME", "long", "watchlist", "{}", now),
            )
            conn.execute(
                "INSERT INTO idea_outcomes(id,baseline_id,idempotency_key,payload_json,observed_at) VALUES(?,?,?,?,?)",
                (f"outcome-{namespace}", baseline_id, f"outcome-{namespace}", '{"status":"complete"}', now),
            )
            conn.execute(
                "INSERT INTO idea_lifecycle_events(id,namespace,run_id,candidate_key,state,decision_revision,reason,idempotency_key,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (f"lifecycle-{namespace}", namespace, run_ids[namespace], f"candidate-{namespace}", "watchlist", 1, "fixture", f"lifecycle-{namespace}", now),
            )
            conn.execute(
                "INSERT INTO source_documents(source_id,namespace,original_hash,original_bytes,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
                (source_ids[namespace], namespace, f"hash-{namespace}", b"immutable pdf", json.dumps({"page_count": 1, "media_type": "application/pdf"}), now),
            )

    real = repository.export("real")
    demo = repository.export("demo")
    assert {row["id"] for row in real["records"]["idea_baselines"]} == {"baseline-real"}
    assert {row["id"] for row in real["records"]["idea_outcomes"]} == {"outcome-real"}
    assert {row["id"] for row in real["records"]["idea_lifecycle_events"]} == {"lifecycle-real"}
    assert real["records"]["source_documents"][0]["metadata_json"] == {"page_count": 1, "media_type": "application/pdf"}
    assert "original_bytes" not in real["records"]["source_documents"][0]
    assert {row["id"] for row in demo["records"]["idea_baselines"]} == {"baseline-demo"}
    assert {row["id"] for row in demo["records"]["idea_outcomes"]} == {"outcome-demo"}
    assert source_ids["demo"] not in json.dumps(real)
    json.dumps(real)
