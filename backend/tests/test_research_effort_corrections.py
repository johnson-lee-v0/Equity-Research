import json
import sqlite3

import pytest

from backend.app.research.learning import _research_effort


@pytest.fixture
def effort_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tasks(id TEXT, run_id TEXT);
        CREATE TABLE task_attempts(task_id TEXT, usage_json TEXT);
        CREATE TABLE events(sequence_id INTEGER PRIMARY KEY, namespace TEXT,
                            run_id TEXT, type TEXT, payload_json TEXT);
        INSERT INTO tasks VALUES('review', 'case');
        INSERT INTO task_attempts VALUES('review', '{"total_tokens": 100}');
    """)
    yield conn
    conn.close()


def receipt(conn, status, *, correction_id="correction", usage=None, namespace="real", run_id="case"):
    conn.execute("INSERT INTO events(namespace,run_id,type,payload_json) VALUES(?,?,?,?)", (
        namespace, run_id, "investment_valuation_input_correction", json.dumps({
            "correction_attempt_id": correction_id, "status": status, "usage": usage,
        }),
    ))


def test_completed_correction_counts_once_after_reuse_or_duplicate_terminal_receipt(effort_db):
    receipt(effort_db, "started")
    receipt(effort_db, "completed", usage={"total_tokens": 20, "cached_input_tokens": 10})
    receipt(effort_db, "completed", usage={"total_tokens": 20, "cached_input_tokens": 10})
    first = _research_effort(effort_db, "case", "real")
    second = _research_effort(effort_db, "case", "real")
    assert first == second
    assert first["measured_tokens"] == 120
    assert first["attempts"] == first["attempts_with_token_usage"] == 2
    assert first["valuation_correction_attempts"] == 1
    assert first["usage_incomplete"] is False


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_correction_without_usage_is_counted_but_not_treated_as_zero_cost(effort_db, status):
    receipt(effort_db, "started")
    receipt(effort_db, status)
    effort = _research_effort(effort_db, "case", "real")
    assert effort["attempts"] == 2
    assert effort["attempts_with_token_usage"] == 1
    assert effort["measured_tokens"] == 100
    assert effort["usage_incomplete"] is True


def test_only_matching_started_provider_calls_contribute(effort_db):
    receipt(effort_db, "completed", usage={"total_tokens": 500})
    receipt(effort_db, "started", namespace="demo")
    receipt(effort_db, "completed", namespace="demo", usage={"total_tokens": 600})
    receipt(effort_db, "started", run_id="another_case")
    receipt(effort_db, "completed", run_id="another_case", usage={"total_tokens": 700})
    effort = _research_effort(effort_db, "case", "real")
    assert effort["measured_tokens"] == 100
    assert effort["attempts"] == 1
    assert effort["valuation_correction_attempts"] == 0


def test_failed_correction_preserves_usage_if_provider_reported_it(effort_db):
    receipt(effort_db, "started")
    receipt(effort_db, "failed", usage={"total_tokens": 25})
    receipt(effort_db, "cancelled")
    assert _research_effort(effort_db, "case", "real")["measured_tokens"] == 125


def test_no_reported_usage_remains_unknown(effort_db):
    effort_db.execute("UPDATE task_attempts SET usage_json=NULL")
    receipt(effort_db, "started")
    assert _research_effort(effort_db, "case", "real")["measured_tokens"] is None
