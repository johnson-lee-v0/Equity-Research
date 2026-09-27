"""Narrow, deterministic binding of comparative diluted-EPS release tables.

This is a parser, not a general numeric matcher. A value is eligible only when
the issuer, table, per-share section, column dates and column durations are
unambiguous. Unrecognized layouts are left for targeted evidence acquisition.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from calendar import monthrange
from decimal import Decimal, InvalidOperation
import json
import hashlib
import re
from typing import Any, Mapping
from urllib.parse import urlsplit


VERSION = "earnings-financial-tables.v1"
MONTHLY_LAYOUT_VERSION = "monthly-vertical-eps.v1"
_DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}\b", re.I)
_TABLE = re.compile(r"^(?:CONDENSED\s+)?CONSOLIDATED STATEMENTS? OF (?:INCOME|EARNINGS|OPERATIONS)\s*(?:\(UNAUDITED\))?$", re.I)
_EPS = re.compile(r"^(?:NET (?:INCOME|EARNINGS) PER (?:COMMON )?SHARE|(?:BASIC AND DILUTED )?EARNINGS PER (?:COMMON )?SHARE)\s*:?$", re.I)
_ISSUER = re.compile(r"\((?:Nasdaq|NYSE|NASDAQ Global Select Market)(?:\s+(?:Global Select Market|Global Market))?\s*:\s*([A-Z][A-Z0-9.-]{0,14})\s*\)", re.I)
_NUMBER = re.compile(r"(?<![A-Za-z0-9])(?:\(?-?\d+(?:,\d{3})*(?:\.\d+)?\)?)(?![A-Za-z0-9])")
_MONTH_DURATION = re.compile(r"^(THREE|SIX|NINE|TWELVE|3|6|9|12)\s+MONTHS\s+ENDED$", re.I)
_NUMERIC_DATE = re.compile(r"\d{1,2}/\d{1,2}/20\d{2}")
_CELL_NUMBER = re.compile(r"(?:[+\-−]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)|\((?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)\))")


def _currency_markers(header: str) -> tuple[set[str], bool]:
    """Distinguish an unqualified dollar sign from explicit denominations."""
    named = {"Canadian": "CAD", "Australian": "AUD", "Hong Kong": "HKD", "New Zealand": "NZD", "Singapore": "SGD", "U.S.": "USD", "US": "USD", "United States": "USD"}
    codes = set(re.findall(r"\b(?:USD|CAD|AUD|EUR|GBP|JPY|HKD|NZD|SGD|CHF|CNY|INR|ZAR)\b", header.upper()))
    remainder = header
    for name, code in named.items():
        pattern = rf"\b{re.escape(name)}\s+dollars\b"
        if re.search(pattern, header, re.I):
            codes.add(code)
            remainder = re.sub(pattern, "dollars", remainder, flags=re.I)
    # A named but unsupported denomination must not become USD through a
    # coincidental matching SEC value. Inspect each line separately so a
    # preceding statement heading is not mistaken for a dollar qualifier.
    unknown = False
    for prefix in re.findall(r"\b([A-Za-z][A-Za-z.]*)\$", header):
        prefix = prefix.upper()
        if prefix in {"US", "U.S."}:
            codes.add("USD")
        elif prefix not in codes:
            # C$, A$, HK$ and other alphabetic prefixes explicitly qualify
            # the dollars. An unsupported prefix is never a bare-$ gap.
            unknown = True
    for line in remainder.splitlines():
        for match in re.finditer(r"\b([A-Za-z][A-Za-z.-]*)[ \t]+dollars\b", line, re.I):
            if match[1].casefold() not in {"in", "of", "and", "all", "reported", "expressed"}:
                unknown = True
    if re.search(r"[€£¥₹₩]|\b(?:euros?|yen|yuan|renminbi|pounds?|rupees?|pesos?|francs?|kron[aeor]+|rands?|reporting currency|currency denomination)\b", remainder, re.I):
        unknown = True
    if any(code not in codes for code in re.findall(r"\b([A-Z]{3})\b\s*(?:in\s+)?(?:millions|thousands|billions|/shares?)\b", remainder)):
        unknown = True
    return codes, unknown


def _currency(header: str) -> str | None:
    # A bare dollar sign is ambiguous. Do not turn CAD/AUD/HKD into USD just
    # because the issuer is US-listed or uses US GAAP.
    currencies, unsupported = _currency_markers(header)
    return next(iter(currencies)) if len(currencies) == 1 and not unsupported else None


def _currency_inference_allowed(header: str) -> bool:
    currencies, unsupported = _currency_markers(header)
    return not currencies and not unsupported


def _value(raw: str) -> str | None:
    try:
        value = Decimal(raw.replace(",", "").replace("(", "-").replace(")", ""))
        return format(value, "f") if value.is_finite() else None
    except InvalidOperation:
        return None


def _monthly_eps_table(lines: list[str], start: int, stop: int, eps_index: int, issuer: str, content: str) -> tuple[list[dict], list[str]]:
    """Bind a vertical monthly table, including explicit percentage columns.

    Date/value alignment is positional and complete. No cell is taken from
    narrative text or the later weighted-average diluted share-count row.
    """
    header_stop = min(eps_index, start + 40)
    durations = [(index, _MONTH_DURATION.fullmatch(lines[index].strip())) for index in range(start + 1, header_stop)]
    durations = [(index, match[1].upper()) for index, match in durations if match]
    duration_map = {"THREE": 3, "SIX": 6, "NINE": 9, "TWELVE": 12}
    if len(durations) not in {1, 2}:
        raise ValueError("The monthly diluted-EPS table needs one or two explicit reporting durations.")
    date_lines = [index for index in range(start + 1, header_stop) if _NUMERIC_DATE.fullmatch(lines[index].strip())]
    if len(date_lines) != len(durations) * 2 or durations[-1][0] >= date_lines[0]:
        raise ValueError("The monthly diluted-EPS date/duration columns could not be aligned exactly.")
    cursor = date_lines[0]
    columns, dates, months, month_lines = [], [], [], []
    for duration_line, raw_months in durations:
        length = duration_map.get(raw_months, int(raw_months) if raw_months.isdigit() else 0)
        for _ in (0, 1):
            while cursor < header_stop and not lines[cursor].strip():
                cursor += 1
            if cursor >= header_stop or not _NUMERIC_DATE.fullmatch(lines[cursor].strip()):
                raise ValueError("The monthly diluted-EPS date cells are missing or interrupted.")
            try:
                end = datetime.strptime(lines[cursor].strip(), "%m/%d/%Y").date()
            except ValueError as exc:
                raise ValueError("The monthly diluted-EPS table contains an invalid reporting date.") from exc
            if end.day != monthrange(end.year, end.month)[1]:
                raise ValueError("The monthly diluted-EPS table needs explicit calendar month-end dates; do not infer fiscal-week starts.")
            columns.append(("value", len(dates)))
            dates.append(end)
            months.append(length)
            month_lines.append((duration_line, cursor))
            cursor += 1
        while cursor < header_stop and not lines[cursor].strip():
            cursor += 1
        if cursor < header_stop and re.fullmatch(r"(?:%\s*)?Change", lines[cursor].strip(), re.I):
            columns.append(("percent_change", None))
            cursor += 1
    if any(dates[i].year != dates[i + 1].year + 1 or dates[i].month != dates[i + 1].month for i in range(0, len(dates), 2)):
        raise ValueError("The monthly diluted-EPS comparative dates are inconsistent.")
    if any(index >= cursor for index in date_lines):
        raise ValueError("The monthly diluted-EPS header contains extra date cells.")
    eps_stop = next((i for i in range(eps_index + 1, min(stop, eps_index + 60)) if re.search(r"shares (?:used|outstanding)|weighted.average|dividends|^CONSOLIDATED", lines[i], re.I)), min(stop, eps_index + 60))
    diluted = [i for i in range(eps_index + 1, eps_stop) if re.fullmatch(r"Diluted\s*:?", lines[i].strip(), re.I)]
    if len(diluted) != 1:
        raise ValueError("The monthly per-share section needs exactly one separate diluted-EPS row.")
    row = diluted[0]
    cells = [(i, lines[i].strip()) for i in range(row + 1, eps_stop) if lines[i].strip()]
    cell_index, amounts, value_lines = 0, [], []
    for kind, _ in columns:
        if cell_index < len(cells) and cells[cell_index][1] == "$":
            if kind != "value":
                raise ValueError("A percentage-change cell cannot be a currency amount.")
            cell_index += 1
        if cell_index >= len(cells):
            raise ValueError("The monthly diluted-EPS row contains extra or missing cells.")
        line_index, token = cells[cell_index]
        inline_percent = token.endswith("%")
        numeric = token[:-1].strip() if inline_percent else token
        if not _CELL_NUMBER.fullmatch(numeric):
            raise ValueError("The monthly diluted-EPS row contains an unrecognized or malformed cell.")
        cell_index += 1
        percent = inline_percent
        if cell_index < len(cells) and cells[cell_index][1] == "%":
            if percent:
                raise ValueError("The monthly diluted-EPS row has a repeated percentage marker.")
            percent = True
            cell_index += 1
        if (kind == "percent_change") != percent:
            raise ValueError("The monthly diluted-EPS value and percentage-change cells do not match the headers.")
        if kind == "value":
            amounts.append(_value(numeric.replace("−", "-")))
            value_lines.append(line_index)
    if cell_index != len(cells) or len(amounts) != len(dates) or any(value is None for value in amounts):
        raise ValueError("The monthly diluted-EPS row contains extra or missing cells.")
    header = "\n".join(lines[start:cursor])
    currency = _currency(header)
    gaap = bool(re.search(r"(?<!non-)(?<!non )\b(?:U\.S\.\s+)?GAAP\b", content, re.I)) and not re.search(r"non[- ]GAAP|adjusted", header, re.I)
    gaps = []
    if not gaap:
        gaps.append("The release does not establish the accounting basis for the diluted-EPS table.")
    if not currency:
        gaps.append("The release uses an ambiguous dollar symbol; acquire a matching dated SEC EPS fact with an explicit reporting currency.")
    observations = []
    for i, end in enumerate(dates):
        first_month = end.year * 12 + end.month - months[i]
        begin = date(first_month // 12, first_month % 12 + 1, 1)
        duration_line, date_line = month_lines[i]
        observations.append({
            "issuer": issuer, "subject": issuer, "metric": "eps", "value": amounts[i],
            "unit": f"{currency}/share" if currency else None, "currency": currency,
            "basis": "GAAP diluted" if gaap else None,
            "period": f"{months[i]} months ended {end.isoformat()}",
            "period_start": begin.isoformat(), "period_end": end.isoformat(),
            "duration_months": months[i], "duration_weeks": ((end - begin).days + 1) / 7,
            "comparison": "current" if i % 2 == 0 else "prior",
            "statement_type": "consolidated statements of income",
            "locator": f"L{start + 1}-L{cells[-1][0] + 1}", "source_quote": "\n".join(lines[row:cells[-1][0] + 1]),
            "proof": {"parser": VERSION, "layout_parser": MONTHLY_LAYOUT_VERSION, "currency_inference_allowed": _currency_inference_allowed(header), "table_line": start + 1,
                "duration_line": duration_line + 1, "date_line": date_line + 1, "metric_line": eps_index + 1,
                "value_line": value_lines[i] + 1, "column": i + 1, "table_column": next(j + 1 for j, col in enumerate(columns) if col == ("value", i))},
        })
    return observations, gaps


def release_eps_observations(content: str) -> dict[str, Any]:
    """Read supported 2- or 4-column duration/date income-statement tables.

    Every returned observation is independently reproducible from the source.
    ``currency`` may remain unresolved, in which case it is not a usable fact.
    No forecast, annualization, split adjustment or normalization is inferred.
    """
    lines = content.splitlines()
    issuer_matches = set(_ISSUER.findall(content))
    if len(issuer_matches) != 1:
        return {"version": VERSION, "observations": [], "gaps": ["An unambiguous exchange-listed issuer is required in the retained earnings release."]}
    issuer = next(iter(issuer_matches)).upper()
    observations: list[dict[str, Any]] = []
    gaps: list[str] = []
    for start, line in enumerate(lines):
        if not _TABLE.fullmatch(line.strip()):
            continue
        stop = next((index for index in range(start + 1, min(len(lines), start + 400)) if re.match(r"^(?:CONDENSED\s+)?CONSOLIDATED (?:BALANCE|STATEMENTS?)", lines[index].strip(), re.I)), min(len(lines), start + 400))
        eps_index = next((index for index in range(start + 1, stop) if _EPS.fullmatch(lines[index].strip())), None)
        if eps_index is None:
            continue
        if any(_MONTH_DURATION.fullmatch(lines[index].strip()) for index in range(start + 1, min(eps_index, start + 40))):
            try:
                monthly, missing = _monthly_eps_table(lines, start, stop, eps_index, issuer, content)
                observations.extend(monthly)
                gaps.extend(missing)
            except ValueError as exc:
                gaps.append(str(exc))
            continue
        header_end = min(eps_index, start + 14)
        date_rows = [(index, _DATE.findall(lines[index])) for index in range(start + 1, header_end) if len(_DATE.findall(lines[index])) in {2, 4}]
        duration_rows = [(index, re.findall(r"\b(\d{1,2})\s+Weeks?\s+Ended\b", lines[index], re.I)) for index in range(start + 1, header_end) if re.search(r"\b\d{1,2}\s+Weeks?\s+Ended\b", lines[index], re.I)]
        if len(date_rows) != 1 or len(duration_rows) != 1:
            gaps.append("The diluted-EPS table needs one explicit aligned date header and one reporting-duration header.")
            continue
        date_index, raw_dates = date_rows[0]
        duration_index, durations = duration_rows[0]
        if duration_index >= date_index or len(durations) * 2 != len(raw_dates):
            gaps.append("The diluted-EPS table duration/date columns could not be aligned exactly.")
            continue
        try:
            dates = [datetime.strptime(value.title(), "%B %d, %Y").date() for value in raw_dates]
        except ValueError:
            gaps.append("The diluted-EPS table contains an invalid reporting date.")
            continue
        weeks = [int(value) for value in durations for _ in (0, 1)]
        if any(not 8 <= value <= 53 for value in weeks) or any(dates[index] <= dates[index + 1] or not 350 <= (dates[index] - dates[index + 1]).days <= 378 for index in range(0, len(dates), 2)):
            gaps.append("The comparative diluted-EPS columns have inconsistent dates or durations.")
            continue
        # The first Diluted row under the per-share heading is EPS; the later
        # diluted share-count row is deliberately outside this bounded section.
        eps_stop = next((index for index in range(eps_index + 1, stop) if re.search(r"shares (?:used|outstanding)|weighted.average", lines[index], re.I)), min(stop, eps_index + 5))
        diluted = [(index, re.sub(r"^Diluted\s*:?\s*", "", lines[index].strip(), flags=re.I)) for index in range(eps_index + 1, eps_stop) if re.match(r"^Diluted\b", lines[index].strip(), re.I)]
        if len(diluted) != 1:
            continue
        row_index, row = diluted[0]
        raw_values = _NUMBER.findall(row)
        residue = _NUMBER.sub("", row)
        if len(raw_values) != len(dates) or re.sub(r"[\s$|*†‡]+", "", residue):
            gaps.append("The diluted-EPS row contains extra or missing cells.")
            continue
        header = "\n".join(lines[start:date_index + 1])
        currency = _currency(header)
        # A conventional consolidated income statement plus an explicit GAAP
        # statement supports the accounting basis. A non-GAAP table does not.
        gaap = bool(re.search(r"(?<!non-)(?<!non )\b(?:U\.S\.\s+)?GAAP\b", content, re.I)) and not re.search(r"non[- ]GAAP|adjusted", header, re.I)
        if not gaap:
            gaps.append("The release does not establish the accounting basis for the diluted-EPS table.")
        if not currency:
            gaps.append("The release uses an ambiguous dollar symbol; acquire a matching dated SEC EPS fact with an explicit reporting currency.")
        for index, raw in enumerate(raw_values):
            value = _value(raw)
            if value is None:
                continue
            end = dates[index]
            begin = end - timedelta(days=weeks[index] * 7 - 1)
            observations.append({
                "issuer": issuer, "subject": issuer, "metric": "eps", "value": value,
                "unit": f"{currency}/share" if currency else None, "currency": currency,
                "basis": "GAAP diluted" if gaap else None,
                "period": f"{weeks[index]} weeks ended {end.isoformat()}",
                "period_start": begin.isoformat(), "period_end": end.isoformat(),
                "duration_weeks": weeks[index], "comparison": "current" if index % 2 == 0 else "prior",
                "statement_type": "consolidated statements of income",
                "locator": f"L{start + 1}-L{row_index + 1}", "source_quote": lines[row_index],
                "proof": {"parser": VERSION, "currency_inference_allowed": _currency_inference_allowed(header), "table_line": start + 1, "duration_line": duration_index + 1, "date_line": date_index + 1, "metric_line": eps_index + 1, "value_line": row_index + 1, "column": index + 1},
            })
    identities: dict[tuple, list[dict[str, Any]]] = {}
    for observation in observations:
        identities.setdefault((observation["issuer"], observation["period_start"], observation["period_end"]), []).append(observation)
    result = []
    for matches in identities.values():
        if len(matches) == 1:
            result.extend(matches)
        else:
            gaps.append("Multiple diluted-EPS tables describe the same period; resolve the accounting basis before valuation.")
    return {"version": VERSION, "observations": result, "gaps": list(dict.fromkeys(gaps))}


def bind_release_eps_claim(claim: Mapping[str, Any], content: str) -> dict[str, Any] | None:
    """Reparse the source; never accept a caller's parser/proof/status fields."""
    if str(claim.get("metric") or "").casefold() != "eps" or claim.get("scale"):
        return None
    for item in release_eps_observations(content)["observations"]:
        if not item["currency"] or not item["basis"]:
            continue
        exact = ("value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote")
        if str(claim.get("subject") or "").upper() != item["issuer"] or any(str(claim.get(key) or "") != str(item[key]) for key in exact):
            continue
        return item
    return None


def bound_release_eps_observations(source_id: str, sources: Mapping[str, str], metadata: Mapping[str, Mapping[str, Any]], versions: Mapping[str, Any], *, as_of: str | None = None) -> dict[str, Any]:
    """Resolve a release's dollar denomination from its exact comparative EPS.

    An otherwise unambiguous statement may label its currency only as dollars.
    Its prior-year column can establish the denomination when that same issuer,
    GAAP diluted measure, value and complete date interval independently match
    a frozen SEC fact. The proof applies only within the same statement table;
    a US listing, nearby dollar amount or differently dated fact never suffices.
    """
    content = sources.get(source_id, "")
    parsed = release_eps_observations(content)
    frozen = versions.get(source_id)
    expected_hash = (frozen.get("hash") or frozen.get("content_hash")) if isinstance(frozen, Mapping) else None
    if not expected_hash or hashlib.sha256(content.encode()).hexdigest() != expected_hash:
        return parsed
    proofs: dict[int, list[dict[str, Any]]] = {}
    for item in parsed["observations"]:
        if item.get("currency") or not item["proof"].get("currency_inference_allowed") or item.get("basis") != "GAAP diluted" or item.get("comparison") != "prior":
            continue
        for sec_id, raw in sources.items():
            try:
                concept = json.loads(raw)
                cik = str(concept["cik"])
            except (ValueError, TypeError, KeyError):
                continue
            for sec in sec_eps_observations(raw, metadata.get(sec_id, {}), issuer=item["issuer"], cik=cik, as_of=as_of, include_quarterly=True):
                if item["period_start"] != sec["period_start"] or item["period_end"] != sec["period_end"] or Decimal(item["value"]) != Decimal(sec["value"]):
                    continue
                bound = bind_sec_eps_claim(sec | {"source_ref": sec_id}, sources, metadata, versions, as_of=as_of)
                if bound:
                    proofs.setdefault(item["proof"]["table_line"], []).append({
                        "source_id": sec_id, "currency": sec["currency"], "period_start": sec["period_start"],
                        "period_end": sec["period_end"], "value": sec["value"], "source_quote": sec["source_quote"],
                        "proof": bound["proof"],
                    })
    observations = []
    for item in parsed["observations"]:
        anchors = proofs.get(item["proof"]["table_line"], [])
        currencies = {anchor["currency"] for anchor in anchors}
        if not item.get("currency") and item["proof"].get("currency_inference_allowed") and len(currencies) == 1:
            currency = next(iter(currencies))
            item = item | {"currency": currency, "unit": currency + "/share", "proof": item["proof"] | {
                "currency_binding": "same_table_exact_comparative_sec_eps", "currency_sources": anchors,
            }}
        observations.append(item)
    gaps = [gap for gap in parsed["gaps"] if not ("ambiguous dollar" in gap and observations and all(item.get("currency") for item in observations))]
    return parsed | {"observations": observations, "gaps": gaps}


def bind_cross_source_release_eps_claim(claim: Mapping[str, Any], sources: Mapping[str, str], metadata: Mapping[str, Mapping[str, Any]], versions: Mapping[str, Any], *, as_of: str | None = None) -> dict[str, Any] | None:
    if claim.get("metric") != "eps" or claim.get("scale"):
        return None
    parsed = bound_release_eps_observations(str(claim.get("source_ref") or ""), sources, metadata, versions, as_of=as_of)
    fields = ("value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote", "subject")
    return next((row for row in parsed["observations"] if row.get("currency") and row.get("basis") and all(str(claim.get(key) or "") == str(row[key]) for key in fields)), None)


def sec_eps_observations(content: str, metadata: Mapping[str, Any], *, issuer: str, cik: str, as_of: str | None = None, include_quarterly: bool = False) -> list[dict[str, Any]]:
    """Extract annual US-GAAP diluted EPS from a SEC company-concept archive.

    The caller must bind the issuer to CIK through the verified company
    package. The validator repeats that binding against an archived issuer
    identity record; provider-authored proof metadata is never sufficient.
    """
    try:
        value = json.loads(content)
        parsed = urlsplit(str(metadata.get("url") or ""))
        expected_cik = int(cik)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, dict) or value.get("taxonomy") != "us-gaap" or value.get("tag") != "EarningsPerShareDiluted" or value.get("cik") != expected_cik:
        return []
    if parsed.scheme != "https" or parsed.hostname != "data.sec.gov" or parsed.path != f"/api/xbrl/companyconcept/CIK{expected_cik:010d}/us-gaap/EarningsPerShareDiluted.json":
        return []
    units = value.get("units")
    if not isinstance(units, dict):
        return []
    observations = []
    quoted_rows = {}
    for match in re.finditer(r"\{[^{}]*\}", content):
        try:
            quoted_rows.setdefault(json.dumps(json.loads(match[0]), sort_keys=True), (match[0], match.start()))
        except ValueError:
            continue
    for unit, rows in units.items():
        if not re.fullmatch(r"[A-Z]{3}/shares", unit) or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("form") not in ({"10-K", "10-K/A", "10-Q", "10-Q/A"} if include_quarterly else {"10-K", "10-K/A"}) or not row.get("accn"):
                continue
            try:
                begin = datetime.strptime(row["start"], "%Y-%m-%d").date()
                end = datetime.strptime(row["end"], "%Y-%m-%d").date()
                filed = datetime.strptime(row["filed"], "%Y-%m-%d").date()
                numeric = _value(str(row["val"]))
            except (ValueError, KeyError, TypeError):
                continue
            duration = (end - begin).days + 1
            if not (56 <= duration <= 378 if include_quarterly else 350 <= duration <= 378) or filed < end or (as_of and row["filed"] > as_of[:10]) or numeric is None:
                continue
            # Keep the exact serialized observation as the quotation. A
            # reserialized approximation would not be a source quote.
            quote, offset = quoted_rows.get(json.dumps(row, sort_keys=True), (None, 0))
            if quote is None or len(quote) > 2000:
                continue
            first = content.count("\n", 0, offset) + 1
            last = content.count("\n", 0, offset + len(quote) - 1) + 1
            observations.append({
                "issuer": issuer.upper(), "subject": issuer.upper(), "metric": "eps", "value": numeric,
                "unit": unit.replace("/shares", "/share"), "currency": unit[:3], "basis": "GAAP diluted",
                "period": f"{'annual ' if duration >= 350 else ''}period ended {end.isoformat()}", "period_start": begin.isoformat(), "period_end": end.isoformat(),
                "duration_weeks": duration / 7,
                "statement_type": "SEC US-GAAP diluted EPS", "locator": f"L{first}" if first == last else f"L{first}-L{last}", "source_quote": quote,
                "proof": {"parser": VERSION, "cik": f"{expected_cik:010d}", "entity_name": value.get("entityName"), "tag": value["tag"], "unit": unit, "accession": row["accn"], "filed_at": row["filed"]},
            })
    # Newer filed values supersede earlier observations of the same annual
    # period. Conflicting rows on the latest filing date remain ambiguous.
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for item in observations:
        grouped.setdefault((item["period_start"], item["period_end"], item["currency"]), []).append(item)
    selected = []
    for rows in grouped.values():
        latest = max(row["proof"]["filed_at"] for row in rows)
        current = [row for row in rows if row["proof"]["filed_at"] == latest]
        if len({row["value"] for row in current}) == 1:
            selected.append(current[0])
    return sorted(selected, key=lambda row: row["period_end"], reverse=True)


def bind_sec_eps_claim(claim: Mapping[str, Any], sources: Mapping[str, str], metadata: Mapping[str, Mapping[str, Any]], versions: Mapping[str, Any], *, as_of: str | None = None) -> dict[str, Any] | None:
    """Bind a SEC EPS field plus issuer identity in a second frozen source.

    Raw SEC concepts use CIK/entityName rather than exchange tickers. The
    ticker must appear in an independently retained release whose named
    income-statement issuer matches that SEC entity, with both source hashes
    frozen into the same attempt. A title or caller-supplied identity is not
    enough to establish that relationship.
    """
    sid = str(claim.get("source_ref") or "")
    content = sources.get(sid, "")
    if claim.get("metric") != "eps" or claim.get("scale"):
        return None
    expected = versions.get(sid)
    expected_hash = (expected.get("hash") or expected.get("content_hash")) if isinstance(expected, Mapping) else None
    if not expected_hash or hashlib.sha256(content.encode()).hexdigest() != expected_hash:
        return None
    try:
        concept = json.loads(content)
        cik = str(concept["cik"])
        entity = str(concept["entityName"])
    except (ValueError, KeyError, TypeError):
        return None
    def identity(value: str) -> tuple[str, ...]:
        legal = {"corporation", "corp", "inc", "incorporated", "company", "co", "limited", "ltd", "plc", "new"}
        return tuple(word for word in re.findall(r"[a-z0-9]+", value.casefold()) if word not in legal)
    issuer = str(claim.get("subject") or "").upper()
    if not issuer or not identity(entity):
        return None
    issuer_source = None
    for other_id, raw in sources.items():
        expected = versions.get(other_id)
        frozen_hash = (expected.get("hash") or expected.get("content_hash")) if isinstance(expected, Mapping) else None
        if not frozen_hash or hashlib.sha256(raw.encode()).hexdigest() != frozen_hash:
            continue
        if set(value.upper() for value in _ISSUER.findall(raw)) != {issuer}:
            continue
        lines = raw.splitlines()
        names = [lines[index - 1].strip() for index, line in enumerate(lines) if index and _TABLE.fullmatch(line.strip())]
        if any(identity(name) == identity(entity) for name in names):
            issuer_source = other_id
            break
    if not issuer_source:
        return None
    for row in sec_eps_observations(content, metadata.get(sid, {}), issuer=issuer, cik=cik, as_of=as_of, include_quarterly=True):
        fields = ("value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote")
        if all(str(claim.get(key) or "") == str(row[key]) for key in fields):
            return row | {"proof": row["proof"] | {"issuer_source_id": issuer_source}}
    return None
