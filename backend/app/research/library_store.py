"""Durable company capture and a source-bound Research Library read model.

Research outputs and historical packets remain immutable. This store records
where a company entered the engine and projects their latest saved answers.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..db import NAMESPACES, json_loads, utc_now
from .decision_questions import QUESTION_TEXT, project_key_questions
from .fact_references import resolve_fact_references

# Preparation compiles evidence for the reviewer; it is not an investment answer.
# Keep the immutable output in the run ledger, but omit it from reading revisions.
_ANALYSIS_OUTPUT_FILTER = "NOT EXISTS (SELECT 1 FROM task_attempts prep WHERE prep.id=o.attempt_id AND o.agent_id='A03' AND prep.provider='deterministic' AND prep.model='earnings-evidence-compiler.v1')"

_PLACEHOLDERS = {"UNKNOWN", "NONE", "NULL", "N/A", "NA", "TBD", "ALL", "MARKET", "UNIVERSE"}
_CITATION_GROUP = re.compile(r"\[[^\]\n]*\bsrc_[A-Za-z0-9_\-]+[^\]\n]*\]|\([^()\n]*\bsrc_[A-Za-z0-9_\-]+[^()\n]*\)")


def _company_name(value: Any, ticker: str) -> str:
    name = _CITATION_GROUP.sub("", str(value or "")).strip()
    if name.upper() in _PLACEHOLDERS | {ticker.upper(), "NOT AVAILABLE", "UNAVAILABLE", "NOT RECORDED"}:
        return ""
    return name


def _reader_uncertainty(value: str) -> str:
    readable = re.sub(r"reference [A-Za-z][A-Za-z0-9_.:\-]* is unresolved or stale", "Some supporting evidence could not be verified or is out of date", value)
    readable = re.sub(r"\bfact_[A-Za-z0-9_\-]+\b", "unverified supporting evidence", readable)
    readable = readable.replace("Unresolved evidence:", "Evidence limits:")
    return "; ".join(dict.fromkeys(part.strip() for part in readable.split(";") if part.strip()))


def normalize_ticker(value: Any) -> str | None:
    ticker = str(value or "").strip().upper().removeprefix("$")
    if ticker.endswith((".", "-")):
        return None
    if ticker in _PLACEHOLDERS or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", ticker):
        return None
    return ticker


def mentioned_tickers(text: str) -> list[str]:
    """Conservative capture before routing resolves a company's identity.

    Explicit cashtags/exchange labels and uppercase symbols in a research
    question are retained. Common financial/prose acronyms are excluded;
    downstream routing captures any additional resolved company symbols.
    """
    text = str(text or "").strip()
    blocked = {"NASDAQ", "NYSE", "TSX", "AMEX", "AI", "API", "CEO", "CFO", "CIO", "COO", "ETF", "ETFS", "EPS", "SEC", "USD", "CAD", "EUR", "GBP", "FY", "Q", "PE", "FCF", "ROIC", "EBITDA", "GAAP", "NON", "US", "USA", "UK", "GDP", "IR", "IPO", "RPO", "SBC", "THE", "AND", "OR", "FOR", "IS", "IT", "A", "I", "IN", "AT", "OF", "ON", "TO", "YES", "NO", "ALL", "WHY", "WHAT", "HOW", "WHEN", "NOT", "BUY", "SELL", "HOLD", "WAIT", "TODAY", "NOW", "PLEASE", "HELP", "REVIEW", "STOCK", "SHARES"}
    blocked.update({"NLP", "ID", "U.S", "U.K", "N.Y", "YOY", "CAGR", "ROE", "ROI", "TTM", "DCF", "NWC", "MDA", "MD&A", "FOMC", "CPI", "PPI", "FX", "QOQ", "PDF", "HTML", "JSON", "CSV", "QA", "II", "III", "IV", "HTTP", "HTTPS", "PM", "YOLO"})
    found = re.findall(r"\$([A-Za-z][A-Za-z0-9.\-]{0,14})\b", text)
    found += re.findall(r"\b(?:NASDAQ|NYSE|TSX|AMEX)\s*:\s*([A-Za-z][A-Za-z0-9.\-]{0,14})\b", text, re.I)
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", text) and text not in blocked:
        found.append(text)
    else:
        # Long generated handoff prompts contain source extracts and internal
        # IDs. Only the request paragraph supplies implicit company mentions;
        # explicit cashtags anywhere remain meaningful and routing resolves
        # further company identities without treating quoted acronyms as stock.
        context = text.split("\n", 1)[0]
        financial = re.compile(r"\b(?:research|reassess|assess|review|compare|analy[sz]e|analysis|investigate|earnings?|stock|shares?|ticker|invest(?:ing|ment)?|valuation|portfolio|thesis|opportunity|business|company|companies|buy|sell|hold|expensive|cheap)\b", re.I)
        for match in re.finditer(r"(?<![A-Za-z0-9_\-])([A-Z][A-Z0-9.\-]{0,5})(?![A-Za-z0-9_])", context):
            symbol = match.group(1).rstrip(".")
            if symbol in blocked or re.fullmatch(r"(?:Q[1-4]|FY\d+|\d+[QK]|A\d{2})", symbol):
                continue
            if financial.search(context[max(0, match.start() - 80):match.end() + 80]):
                found.append(symbol)
    return list(dict.fromkeys(symbol for item in found if (symbol := normalize_ticker(item))))


def register_ticker(conn: sqlite3.Connection, namespace: str, ticker: str, *, origin: str,
                    origin_ref: str, name: str = "", created_at: str | None = None,
                    run_id: str | None = None, workflow_id: str | None = None) -> str | None:
    """Capture once per company and origin, in the caller's transaction.

    The optional run/workflow links must belong to the same namespace. A retry
    preserves the first-seen time and cannot create a second company record.
    """
    if namespace not in NAMESPACES:
        raise ValueError("Unknown library namespace.")
    symbol = normalize_ticker(ticker)
    if not symbol:
        return None
    for table, identifier in (("runs", run_id), ("research_workflow_runs", workflow_id)):
        if identifier and not conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND namespace=?", (identifier, namespace)).fetchone():
            raise ValueError("Library reference is unavailable in this namespace.")
    identifier = "lib_" + hashlib.sha256(f"{namespace}:{symbol}".encode()).hexdigest()[:24]
    when = created_at or utc_now()
    # A model's instrument field can contain an inline citation; the retained
    # output keeps it, while a company name stays readable in the directory.
    name = _company_name(name, symbol)
    conn.execute("""INSERT INTO research_library_entries(id,namespace,ticker,name,created_at,updated_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(namespace,ticker) DO UPDATE SET
        name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE research_library_entries.name END,
        created_at=min(research_library_entries.created_at,excluded.created_at),
        updated_at=max(research_library_entries.updated_at,excluded.updated_at)""",
                 (identifier, namespace, symbol, name or "", when, when))
    # Resolve the persisted ID, including stores migrated from earlier tooling.
    identifier = conn.execute("SELECT id FROM research_library_entries WHERE namespace=? AND ticker=?", (namespace, symbol)).fetchone()[0]
    conn.execute("""INSERT INTO research_library_mentions(entry_id,origin,origin_ref,run_id,workflow_id,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(entry_id,origin,origin_ref) DO UPDATE SET
        run_id=coalesce(excluded.run_id,research_library_mentions.run_id),
        workflow_id=coalesce(excluded.workflow_id,research_library_mentions.workflow_id),
        created_at=min(research_library_mentions.created_at,excluded.created_at),
        updated_at=max(research_library_mentions.updated_at,excluded.updated_at)""",
                 (identifier, origin, origin_ref, run_id, workflow_id, when, when))
    return identifier


def _objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def output_companies(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Only explicit ticker fields count; ordinary uppercase prose does not."""
    found: dict[str, str] = {}
    rows = [payload]
    for key in ("candidate_briefs", "research_candidates", "candidates"):
        rows.extend(_objects(payload.get(key)))
    decision = payload.get("decision_brief") or {}
    if isinstance(decision, dict):
        rows.append(decision)
        rows.extend(_objects(decision.get("candidate_briefs")))
    canonical = payload.get("canonical_decision") or {}
    if isinstance(canonical, dict):
        rows.extend(_objects(canonical.get("candidates")))
    route = payload.get("routing_plan") or {}
    if isinstance(route, dict):
        for ticker in route.get("tickers") or []:
            if isinstance(ticker, str):
                rows.append({"ticker": ticker})
    for row in rows:
        ticker = normalize_ticker(row.get("ticker") or row.get("symbol"))
        if ticker:
            found[ticker] = str(row.get("issuer") or row.get("name") or row.get("instrument") or found.get(ticker, ""))
    return list(found.items())


def _safe_url(value: Any) -> str | None:
    value = str(value or "").strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value if parts.scheme in {"http", "https"} and parts.hostname and not parts.username else None


def _source(raw: dict[str, Any], number: int) -> dict[str, Any]:
    return {"number": number, "id": raw.get("id") or raw.get("source_id"),
            "url": _safe_url(raw.get("url")), "title": raw.get("title") or raw.get("label") or "Saved source",
            "publisher": raw.get("publisher"), "published_at": raw.get("publication_at") or raw.get("published_at"),
            "quote": raw.get("quote") or raw.get("source_quote") or "", "locator": raw.get("locator") or "",
            **({"source_version": raw["version"]} if raw.get("version") is not None else {}),
            **({"content_hash": raw["content_hash"]} if raw.get("content_hash") else {})}


def normalize_question(raw: dict[str, Any], *, identifier: str, origin: str,
                       source_lookup: dict[str, dict[str, Any]] | None = None,
                       as_of: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Number only sources explicitly attached to this answer or its facts.

    Historical question-level citations support the entire answer. Modern
    inline source tokens retain their original placement. A run bibliography
    never becomes a citation merely because it is available to the model.
    """
    answer = str(raw.get("answer") or "")
    source_lookup = source_lookup or {}
    sources: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    unavailable: list[str] = []

    def add_source(record: dict[str, Any], key: str) -> int:
        if key not in index:
            index[key] = len(sources) + 1
            sources.append(_source(record, index[key]))
        elif record.get("locator"):
            current = sources[index[key] - 1]
            locators = list(dict.fromkeys(filter(None, [current.get("locator"), record["locator"]])))
            current["locator"] = "; ".join(locators)
        return index[key]

    def reference(source_id: str, detail: dict[str, Any] | None = None) -> int | None:
        source = source_lookup.get(source_id)
        if not source:
            if source_id and source_id not in unavailable:
                unavailable.append(source_id)
            return None
        # Facts can add a locator/quote but cannot replace archived URL/title.
        record = dict(source)
        if detail:
            for key in ("locator", "quote", "source_quote"):
                if detail.get(key):
                    record[key] = detail[key]
        return add_source(record, source_id)

    declared: list[int] = []
    for source in _objects(raw.get("sources")):
        source_id = source.get("id") or source.get("source_id")
        if source_id:
            number = reference(str(source_id), source)
        elif origin == "historical" and _safe_url(source.get("url")):
            number = add_source(source, source["url"])
        else:
            number = None
        if number and number not in declared:
            declared.append(number)
    for fact in _objects(raw.get("verified_facts")):
        number = reference(str(fact.get("source_ref") or ""), fact)
        if number and number not in declared:
            declared.append(number)
    for source_id in raw.get("source_refs") or []:
        number = reference(str(source_id))
        if number and number not in declared:
            declared.append(number)

    segments: list[dict[str, Any]] = []
    cursor = 0
    # Include a whole original citation group, which can contain two sources.
    for match in _CITATION_GROUP.finditer(answer):
        if match.start() > cursor:
            segments.append({"text": answer[cursor:match.start()], "citation_numbers": []})
        numbers = []
        for source_id in dict.fromkeys(re.findall(r"\bsrc_[A-Za-z0-9_\-]+", match.group())):
            locator_match = re.search(re.escape(source_id) + r"\s+((?:(?!\bsrc_)[^;\]\)])+)", match.group())
            number = reference(source_id, {"locator": locator_match.group(1).strip()} if locator_match else None)
            if number:
                numbers.append(number)
        segments.append({"text": "" if numbers else "[Source unavailable]", "citation_numbers": numbers})
        cursor = match.end()
    if cursor < len(answer):
        segments.append({"text": answer[cursor:], "citation_numbers": []})
    if not segments:
        segments = [{"text": answer, "citation_numbers": []}]
    # A source explicitly associated with the question is an answer-level
    # citation, not a claim that one selected sentence contains it verbatim.
    inline_numbers = {n for segment in segments for n in segment["citation_numbers"]}
    remaining = [n for n in declared if n not in inline_numbers]
    if remaining:
        segments[-1]["citation_numbers"].extend(remaining)
    result = {"id": identifier, "question": str(raw.get("question") or QUESTION_TEXT.get(raw.get("key"), "Saved research finding")),
              "answer": answer, "answer_segments": segments, "sources": sources,
              "status": raw.get("evidence_status") or raw.get("verdict") or ("answered" if answer else "pending"),
              "as_of": as_of, "origin": origin, "run_id": run_id}
    for key in ("uncertainty", "decisionImpact", "managementAnswer", "decision_implication", "unknowns"):
        if raw.get(key):
            result[key] = raw[key]
    if isinstance(result.get("unknowns"), list):
        originals = result["unknowns"]
        readable = list(dict.fromkeys(_reader_uncertainty(value) if isinstance(value, str) else value for value in originals))
        if readable != originals:
            result["audit"] = {"unknowns": originals}
            result["unknowns"] = readable
    if isinstance(result.get("uncertainty"), str):
        readable = _reader_uncertainty(result["uncertainty"])
        if readable != result["uncertainty"]:
            result.setdefault("audit", {})["uncertainty"] = result["uncertainty"]
            result["uncertainty"] = readable
    if origin != "historical" and isinstance(result.get("managementAnswer"), dict):
        management = dict(result["managementAnswer"])
        supplied = management.get("source") or {}
        source_id = (supplied.get("id") or supplied.get("source_id") or supplied.get("source_ref")) if isinstance(supplied, dict) else None
        if source_id and source_id in source_lookup:
            management["source"] = _source(source_lookup[source_id] | {"locator": management.get("locator") or supplied.get("locator")}, 0)
        else:
            management["source"] = None
            if supplied:
                result["citation_gaps"] = ["Management's cited source is outside the retained research packet."]
        result["managementAnswer"] = management
    if unavailable:
        result["citation_gaps"] = ["A cited source is not retained in this library namespace."]
    return result


def historical_questions(packet: Any, *, prefix: str = "historical") -> list[dict[str, Any]]:
    if not isinstance(packet, dict):
        return []
    return [normalize_question(q, identifier=f"{prefix}-{i}", origin="historical", as_of=packet.get("researchedAt"))
            for i, q in enumerate(_objects(packet.get("questions"))) if q.get("question")]


class LibraryStore:
    def __init__(self, repo: Any, evidence_dir: Path | None = None):
        self.repo = repo
        self.evidence_dir = evidence_dir

    def backfill(self) -> int:
        """Recover earlier entry points without inventing research conclusions."""
        with self.repo.db.transaction(immediate=True) as conn:
            before = conn.execute("SELECT count(*) FROM research_library_entries").fetchone()[0]
            for row in conn.execute("SELECT id,namespace,ticker,request,created_at FROM runs").fetchall():
                allowed = {ticker for item in [row["ticker"], *mentioned_tickers(row["request"])] if (ticker := normalize_ticker(item))}
                # Correct only our automatic request captures. Explicit
                # candidate, document, workflow, followup and portfolio links
                # are independent evidence and must survive reconciliation.
                captures = conn.execute("SELECT m.entry_id,e.ticker FROM research_library_mentions m JOIN research_library_entries e ON e.id=m.entry_id WHERE m.origin IN ('research','question') AND m.origin_ref=? AND m.run_id=? AND e.namespace=?", (row["id"], row["id"], row["namespace"])).fetchall()
                for capture in captures:
                    if capture["ticker"] not in allowed:
                        conn.execute("DELETE FROM research_library_mentions WHERE entry_id=? AND origin IN ('research','question') AND origin_ref=? AND run_id=?", (capture["entry_id"], row["id"], row["id"]))
                for ticker in allowed:
                    register_ticker(conn, row["namespace"], ticker, origin="research", origin_ref=row["id"], run_id=row["id"], created_at=row["created_at"])
            for row in conn.execute("SELECT id,namespace,ticker,created_at,result_json FROM research_workflow_runs").fetchall():
                result = json_loads(row["result_json"], {})
                register_ticker(conn, row["namespace"], row["ticker"], origin="earnings", origin_ref=row["id"], workflow_id=row["id"], created_at=row["created_at"], name=(result.get("company") or {}).get("name", ""))
            for row in conn.execute("SELECT id,namespace,ticker,created_at FROM research_items").fetchall():
                register_ticker(conn, row["namespace"], row["ticker"], origin="coverage", origin_ref=row["id"], created_at=row["created_at"])
            for row in conn.execute("SELECT id,namespace,symbol,observed_at FROM positions").fetchall():
                register_ticker(conn, row["namespace"], row["symbol"], origin="portfolio", origin_ref=row["id"], created_at=row["observed_at"])
            if self.evidence_dir:
                for path in (self.evidence_dir / "document-analysis").glob("*.json"):
                    try:
                        record = json_loads(path.read_text(encoding="utf-8"), {})
                    except OSError:
                        continue
                    namespace = record.get("namespace", "real")
                    if namespace in NAMESPACES and record.get("id"):
                        register_ticker(conn, namespace, record.get("ticker"), origin="document", origin_ref=record["id"], created_at=record.get("created_at"))
            for row in conn.execute("SELECT o.id,o.provenance,o.payload_json,o.created_at,t.run_id FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE o.status<>'cancelled_late'").fetchall():
                for ticker, name in output_companies(json_loads(row["payload_json"], {})):
                    register_ticker(conn, row["provenance"], ticker, origin="candidate", origin_ref=row["id"], run_id=row["run_id"], name=name, created_at=row["created_at"])
            conn.execute("DELETE FROM research_library_entries WHERE NOT EXISTS (SELECT 1 FROM research_library_mentions m WHERE m.entry_id=research_library_entries.id)")
            after = conn.execute("SELECT count(*) FROM research_library_entries").fetchone()[0]
        return after - before

    def list(self, namespace: str = "real") -> list[dict[str, Any]]:
        with self.repo.db.operation() as conn:
            rows = conn.execute("SELECT * FROM research_library_entries WHERE namespace=? ORDER BY updated_at DESC,id", (namespace,)).fetchall()
            results = []
            for row in rows:
                runs, workflows = self._activity(conn, dict(row))
                latest = max(runs[:1] + workflows[:1], key=lambda item: (item["created_at"], item["id"]), default={})
                summary = self._saved_summary(conn, row["ticker"], runs)
                times = [row["updated_at"]] + [r["updated_at"] for r in runs + workflows]
                results.append(dict(row) | {"name": self._entry_name(conn, dict(row)), "archived": False, "live": True, "historical": False,
                    "disposition": latest.get("status") or "captured", "status": latest.get("status") or "captured",
                    "summary": summary or (self._display_question(latest, row["ticker"]) if latest.get("request") else "Earnings review saved." if workflows else "Company captured for research."),
                    "updated_at": max(times), "researched_at": latest.get("finished_at"),
                    "revisions": self._revision_count(conn, runs, row["ticker"]), "run_id": runs[0]["id"] if runs else None})
        return results

    @staticmethod
    def _entry_name(conn, entry):
        name = _company_name(entry["name"], entry["ticker"])
        if name:
            return name
        # Earlier model outputs may have replaced an issuer with its ticker.
        # Recover a retained descriptive name in the read model; the original
        # model output and library registration history are left untouched.
        for row in conn.execute("SELECT result_json FROM research_workflow_runs WHERE namespace=? AND ticker=? ORDER BY created_at DESC", (entry["namespace"], entry["ticker"])):
            company = json_loads(row[0], {}).get("company") or {}
            name = _company_name(company.get("name") or company.get("issuer"), entry["ticker"])
            if name:
                return name
        for row in conn.execute("SELECT o.payload_json FROM outputs o JOIN tasks t ON t.id=o.task_id JOIN research_library_mentions m ON m.run_id=t.run_id WHERE o.provenance=? AND m.entry_id=? ORDER BY o.created_at DESC", (entry["namespace"], entry["id"])):
            for symbol, candidate_name in output_companies(json_loads(row[0], {})):
                name = _company_name(candidate_name, symbol)
                if symbol == entry["ticker"] and name:
                    return name
        return ""

    @staticmethod
    def _display_question(run, ticker):
        if str(run.get("origin_ref") or "").startswith("workflow:"):
            return f"What changed for {ticker} after its latest earnings?"
        if str(run.get("request") or "").startswith("Investigate these saved document findings for "):
            return f"What do the latest saved documents tell us about {ticker}?"
        return str(run.get("request") or "")

    @staticmethod
    def _saved_summary(conn, ticker, runs):
        if not runs:
            return ""
        marks = ",".join("?" for _ in runs)
        rows = conn.execute(f"SELECT o.payload_json FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id IN ({marks}) AND o.agent_id IN ('A03','A11') AND o.status<>'cancelled_late' AND {_ANALYSIS_OUTPUT_FILTER} ORDER BY o.created_at DESC, CASE o.agent_id WHEN 'A11' THEN 1 ELSE 0 END DESC,o.rowid DESC", [r["id"] for r in runs]).fetchall()
        for row in rows:
            payload = json_loads(row["payload_json"], {})
            candidates = _objects(payload.get("candidate_briefs")) + _objects((payload.get("decision_brief") or {}).get("candidate_briefs"))
            candidate = next((c for c in candidates if normalize_ticker(c.get("ticker")) == ticker), None)
            text = (candidate or {}).get("entry_advice") or (candidate or {}).get("rationale")
            if not text and (candidate or not candidates and (normalize_ticker(payload.get("ticker")) == ticker or len(runs) == 1 and normalize_ticker(runs[0].get("ticker")) == ticker)):
                text = payload.get("summary")
            if text:
                return _CITATION_GROUP.sub("", str(text)).strip()
        return ""

    @classmethod
    def _revision_count(cls, conn: sqlite3.Connection, runs: list[dict[str, Any]], ticker: str) -> int:
        if not runs:
            return 0
        marks = ",".join("?" for _ in runs)
        rows = conn.execute(f"SELECT o.id,o.payload_json,o.conclusion,o.provenance,t.run_id FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id IN ({marks}) AND o.agent_id IN ('A03','A11') AND o.status<>'cancelled_late' AND {_ANALYSIS_OUTPUT_FILTER}", [r["id"] for r in runs]).fetchall()
        run_lookup = {run["id"]: run for run in runs}
        count = 0
        for row in rows:
            projected = 0
            for case in conn.execute("SELECT payload_json FROM case_decision_versions WHERE output_id=? AND namespace=?", (row["id"], row["provenance"])):
                case_payload = json_loads(case["payload_json"], {})
                candidate = next((c for c in _objects(case_payload.get("candidates")) if normalize_ticker(c.get("ticker")) == ticker), None)
                if candidate and cls._candidate_questions(candidate):
                    projected += 1
            if projected:
                count += projected
                continue
            payload = json_loads(row["payload_json"], {})
            questions, _ = cls._output_questions(payload, run_lookup[row["run_id"]], ticker, row["conclusion"])
            if questions:
                count += 1
        return count

    @staticmethod
    def _activity(conn: sqlite3.Connection, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        runs = [dict(r) for r in conn.execute("""SELECT id,ticker,request,status,created_at,updated_at,finished_at,error,origin,origin_ref,namespace,input_snapshot_json FROM runs
            WHERE namespace=? AND (upper(ticker)=? OR id IN
            (SELECT run_id FROM research_library_mentions WHERE entry_id=? AND run_id IS NOT NULL))
            ORDER BY created_at DESC,id DESC""", (entry["namespace"], entry["ticker"], entry["id"]))]
        from .investment_process import process_view
        for run in runs:
            run["investment_process"] = process_view(conn, run)
            run.pop("input_snapshot_json", None)
        workflows = [dict(r) for r in conn.execute("""SELECT id,workflow,ticker,status,created_at,updated_at,research_run_id FROM research_workflow_runs
            WHERE namespace=? AND ticker=? ORDER BY created_at DESC,id DESC""", (entry["namespace"], entry["ticker"]))]
        return runs, workflows

    def detail(self, identifier: str, namespace: str = "real") -> dict[str, Any] | None:
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT * FROM research_library_entries WHERE namespace=? AND id=?", (namespace, identifier)).fetchone()
            if not row:
                return None
            entry = dict(row)
            entry["name"] = self._entry_name(conn, entry)
            runs, workflows = self._activity(conn, entry)
            packets = []
            for run in runs:
                # Code-owned case questions carry verified fact associations.
                cases = conn.execute("SELECT id,payload_json,created_at,output_id,revision FROM case_decision_versions WHERE namespace=? AND run_id=? ORDER BY revision DESC", (namespace, run["id"])).fetchall()
                represented = set()
                for case in cases:
                    payload = json_loads(case["payload_json"], {})
                    candidate = next((c for c in _objects(payload.get("candidates")) if normalize_ticker(c.get("ticker")) == entry["ticker"]), None)
                    if candidate:
                        questions = self._candidate_questions(candidate)
                        if questions:
                            sources = self._output_sources(conn, case["output_id"], namespace)
                            packets.append(self._packet(case["id"], questions, sources, case["created_at"], run["id"], candidate.get("rationale") or payload.get("summary", ""), candidate=candidate, canonical=True, revision=case["revision"]) | {"_order": (3, case["revision"])})
                            represented.add(case["output_id"])
                outputs = conn.execute(f"SELECT o.*,o.rowid AS output_order FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=? AND o.provenance=? AND o.agent_id IN ('A03','A11') AND o.status<>'cancelled_late' AND {_ANALYSIS_OUTPUT_FILTER} ORDER BY o.created_at DESC,o.version DESC", (run["id"], namespace)).fetchall()
                for output in outputs:
                    if output["id"] in represented:
                        continue
                    payload = json_loads(output["payload_json"], {})
                    sources = self._output_sources(conn, output["id"], namespace)
                    facts = self.repo.output_dict(output)["fact_claims"]
                    inputs = self.repo.attempt_decision_inputs(output["attempt_id"]) or {}
                    payload, _ = resolve_fact_references(self.repo, output, payload, facts, inputs, list(sources.values()))
                    questions, candidate = self._output_questions(payload, run, entry["ticker"], output["conclusion"])
                    questions = self._project_questions(questions, payload.get("fact_claims") or [])
                    if questions:
                        packets.append(self._packet(output["id"], questions, sources, output["created_at"], run["id"], candidate.get("entry_advice", "") if candidate else output["conclusion"], candidate=candidate) | {"_order": (2 if output["agent_id"] == "A11" else 1, output["output_order"])})
            packets.sort(key=lambda p: (p["at"], p["_order"]), reverse=True)
            for packet in packets:
                packet.pop("_order")
            latest = packets[0] if packets else None
            mentions = [dict(m) for m in conn.execute("SELECT origin,origin_ref,run_id,workflow_id,created_at,updated_at FROM research_library_mentions WHERE entry_id=? ORDER BY created_at DESC", (identifier,))]
        questions = latest["questions"] if latest else []
        selected_run_id = latest["run_id"] if latest else runs[0]["id"] if runs else None
        # Recovery receipts are revalidated by the repository. Keep these
        # run-level limitations separate from answers and their citations:
        # saved conclusions cannot silently make source coverage complete.
        coverage_by_run = {}
        for run_id in {p["run_id"] for p in packets} | ({selected_run_id} if selected_run_id else set()):
            coverage_by_run[run_id] = list(dict.fromkeys(
                gap.strip()
                for receipt in self.repo.earnings_archive_fallbacks(run_id)
                for gap in receipt.get("gaps", [])
                if isinstance(gap, str) and gap.strip()
            ))
        for packet in packets:
            packet["coverage_gaps"] = coverage_by_run.get(packet["run_id"], [])
        if not questions and runs:
            questions = [{"id": f"pending-{runs[0]['id']}", "question": self._display_question(runs[0], entry["ticker"]),
                          "answer": "", "answer_segments": [], "sources": [], "status": runs[0]["status"],
                          "run_id": runs[0]["id"], "origin": "research", "as_of": runs[0]["updated_at"]}]
        return {"idea": {"id": identifier, "updated_at": entry["updated_at"], "data": {"ticker": entry["ticker"], "name": entry["name"], "packet": latest["data"] if latest else {}}},
                "packets": packets, "events": [], "historical": False, "live": True, "namespace": namespace,
                "ticker": entry["ticker"], "runs": runs, "workflows": workflows, "questions": questions, "mentions": mentions,
                "coverage_gaps": coverage_by_run.get(selected_run_id, [])}

    @staticmethod
    def _packet(identifier, questions, sources, as_of, run_id, summary, *, candidate=None, canonical=False, revision=None):
        normalized = [normalize_question(q, identifier=f"{identifier}-{i}", origin="research", source_lookup=sources, as_of=as_of, run_id=run_id) for i, q in enumerate(questions)]
        data = {"summary": summary, "researchedAt": as_of, "questions": normalized}
        if candidate:
            # A target belongs to this exact case revision. Analyst prose or
            # proposed assumptions must never be promoted to a calculated value.
            valuation = candidate.get("valuation") if canonical else None
            future_target = candidate.get("future_target")
            valuation = valuation if isinstance(valuation, dict) else None
            future_target = future_target if isinstance(future_target, dict) else None
            refs = []

            def collect_refs(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "source_refs" and isinstance(child, list):
                            refs.extend(ref.strip() for ref in child if isinstance(ref, str) and ref.strip())
                        elif isinstance(child, (dict, list, str)):
                            collect_refs(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_refs(child)
                elif isinstance(value, str):
                    # Scenario reasoning can cite operating context without
                    # declaring it a financial calculation operand. Resolve
                    # those citations only from this revision's frozen pool.
                    refs.extend(re.findall(r"\bsrc_[A-Za-z0-9_\-]+\b", value))

            collect_refs(valuation if valuation is not None else future_target)
            refs = list(dict.fromkeys(refs))
            retained = [sources[ref] for ref in refs if ref in sources]
            data.update({"valuation": valuation, "future_target": future_target,
                         "horizon": candidate.get("horizon"), "ticker": candidate.get("ticker"),
                         "target_provenance": "canonical_case" if canonical else "saved_analyst_target",
                         "decision_revision": revision,
                         "target_sources": [_source(source, i + 1) for i, source in enumerate(retained)],
                         "target_citation_gaps": [f"The saved valuation references {ref}, which is not available in this revision's evidence packet." for ref in refs if ref not in sources]})
            if canonical and isinstance(candidate.get("payoff"), dict):
                data["payoff"] = candidate["payoff"]
        return {"id": identifier, "at": as_of, "data": data, "questions": normalized, "run_id": run_id}

    @classmethod
    def _output_questions(cls, payload, run, ticker, conclusion):
        """Select company-specific saved answers without projecting facts.

        Detail and directory revision counts share this selection, so a
        company merely mentioned during routing does not acquire another
        company's revisions.
        """
        decision = payload.get("decision_brief") or {}
        candidates = _objects(payload.get("candidate_briefs")) + _objects(decision.get("candidate_briefs"))
        candidate = next((c for c in candidates if normalize_ticker(c.get("ticker")) == ticker), None)
        if candidate and not candidate.get("key_questions") and len({normalize_ticker(c.get("ticker")) for c in candidates}) == 1 and decision.get("key_questions"):
            candidate = candidate | {"key_questions": decision["key_questions"]}
        if not candidate and not candidates and normalize_ticker(decision.get("ticker") or payload.get("ticker") or run["ticker"]) == ticker:
            candidate = (decision or payload).copy()
            if not candidate.get("key_questions") and payload.get("key_questions"):
                candidate["key_questions"] = payload["key_questions"]
        questions = cls._candidate_questions(candidate) if candidate else []
        if not questions and normalize_ticker(run["ticker"]) == ticker and not candidates:
            questions = [{"question": cls._display_question(run, ticker), "answer": payload.get("analysis") or conclusion}]
        return questions, candidate

    @staticmethod
    def _candidate_questions(candidate: dict[str, Any]) -> list[dict[str, Any]]:
        questions = _objects(candidate.get("key_questions"))
        if questions:
            return questions
        # Older candidates are already organized by decision concern. Turning
        # their saved fields into questions changes presentation, not analysis.
        definitions = (("portfolio_action", ("entry_advice", "rationale", "entry_plan")),
                       ("valuation", ("target_price_basis", "target_price_missing_reason")),
                       ("catalyst", ("catalysts",)), ("downside", ("risks", "invalidation_conditions", "invalidation")))
        rows = []
        for key, fields in definitions:
            paragraphs = []
            for field in fields:
                value = candidate.get(field)
                if isinstance(value, str) and value.strip():
                    paragraphs.append(value)
                elif isinstance(value, list):
                    paragraphs.extend(str(v) for v in value if isinstance(v, str) and v.strip())
            if paragraphs:
                rows.append({"key": key, "answer": "\n\n".join(dict.fromkeys(paragraphs))})
        return rows

    @staticmethod
    def _project_questions(questions, facts):
        if not any(q.get("supporting_claim_ids") or q.get("contradicting_claim_ids") or q.get("verified_facts") for q in questions):
            return questions
        projected = {q["key"]: q for q in project_key_questions(questions, facts)}
        return [projected.get(q.get("key"), q) for q in questions]

    @staticmethod
    def _output_sources(conn, output_id, namespace):
        row = conn.execute("SELECT a.source_versions_json FROM outputs o JOIN task_attempts a ON a.id=o.attempt_id WHERE o.id=? AND o.provenance=?", (output_id, namespace)).fetchone()
        versions = json_loads(row[0], {}) if row else {}
        if not isinstance(versions, dict):
            versions = {v["id"]: v for v in _objects(versions) if v.get("id")}
        sources = {}
        for source_id, version in versions.items():
            if not isinstance(version, dict) or version.get("version") is None:
                continue
            fingerprint = version.get("hash") or version.get("content_hash")
            source = conn.execute("SELECT id,url,title,publisher,publication_at,locator,content_hash FROM sources WHERE id=? AND namespace=?", (source_id, namespace)).fetchone()
            if not source or not fingerprint:
                continue
            retained = conn.execute("SELECT 1 FROM source_versions WHERE source_id=? AND version_no=? AND content_hash=?", (source_id, version["version"], fingerprint)).fetchone()
            if not retained and not (str(version["version"]) == "1" and source["content_hash"] == fingerprint):
                continue
            sources[source_id] = dict(source) | {"content_hash": fingerprint, "version": version["version"]}
        return sources
