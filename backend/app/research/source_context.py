"""Read explicit archive metadata and excerpt sources with original locators.

These pure helpers are shared by market validation and provider preparation.
They never infer issuer or instrument identity from a source title or prose.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


def structured_source_metadata(source: dict[str, Any]) -> dict[str, Any]:
    """Read only explicit source/header metadata, never titles or prose."""
    allowed = {
        "provider", "source_type", "status", "capability", "timeframe", "symbols", "symbol", "ticker",
        "currency", "currency_code", "currency_basis", "feed", "adjustment", "endpoint", "requested_symbol", "retrieved_at", "latest_bar_timestamp",
    }
    metadata: dict[str, Any] = {}
    for key in ("structured_metadata", "metadata"):
        candidate = source.get(key)
        if isinstance(candidate, dict):
            metadata.update({name: candidate[name] for name in allowed if name in candidate})
    for name in allowed:
        if name in source:
            metadata[name] = source[name]
    lines = str(source.get("content") or "").splitlines()
    if len(lines) > 1:
        try:
            header = json.loads(lines[1])
        except (TypeError, ValueError):
            header = {}
        if isinstance(header, dict):
            nested = header.get("metadata")
            if isinstance(nested, dict):
                metadata.update({name: nested[name] for name in allowed if name in nested})
            metadata.update({name: header[name] for name in allowed if name in header})
    return metadata


def number_source_lines(content: str, *, max_lines: int = 600, max_chars: int = 24_000) -> str:
    """Bound source context while preserving the source's real line numbers.

    Market archives put a small canonical header before a long chronological
    row stream.  Sending the first rows makes a current-price question stale,
    while renumbering a tail excerpt breaks every citation.  Keep the header
    (with bulky metadata compacted), then keep the newest rows and label each
    selected line with its original locator.
    """
    lines = content.splitlines() or [content]
    line_count = len(lines)

    def compact_header(line: str, original_index: int) -> str:
        if original_index != 2:
            return line
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError):
            return line
        if not isinstance(parsed, dict):
            return line
        # Keep provenance and the fields needed to bind rows to an instrument
        # and observation window.  Drop nested coverage/freshness diagnostics
        # from the prompt; they remain durable in the source archive.
        keep = {
            "provider", "source_type", "status", "capability", "metadata",
        }
        compact = {key: parsed[key] for key in keep if key in parsed}
        metadata = compact.get("metadata")
        if isinstance(metadata, dict):
            metadata_keep = {
                "timeframe", "symbols", "symbol", "ticker", "currency",
                "currency_code", "feed", "adjustment", "retrieved_at",
                "latest_bar_timestamp", "oldest_bar_timestamp", "endpoint",
                "coverage_complete",
            }
            compact["metadata"] = {
                key: metadata[key]
                for key in metadata_keep
                if key in metadata
            }
        return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    # Market archives are chronological row streams.  Detect them from the
    # canonical header and always retain the newest rows when either the line
    # or character budget is exceeded.  This matters for a 276-line daily
    # archive with a very large metadata field: a simple character truncation
    # would otherwise leave the model with stale prices and no current row.
    try:
        header = json.loads(lines[1]) if line_count > 1 else {}
    except (TypeError, ValueError):
        header = {}
    source_type = str(header.get("source_type") or "").casefold() if isinstance(header, dict) else ""
    market_stream = source_type in {"market_bars", "derived_weekly_market_bars"}
    compact_required = line_count > max_lines or sum(len(line) + 1 for line in lines) > max_chars

    def render(index: int) -> str:
        line = compact_header(lines[index - 1], index)
        return f"L{index}: {line}"

    if not compact_required:
        rendered = [render(index) for index in range(1, line_count + 1)]
        # A single ordinary filing line can still exceed the prompt cap.  It
        # is kept as a true-locator prefix rather than silently renumbered.
        return "\n".join(rendered)[:max_chars]

    def dated_numeric_row(line: str) -> tuple[datetime, int, str] | None:
        """Recognize a tabular row whose first cell is a complete date.

        A date at the start of a line plus several numeric cells is a much
        stronger table signal than merely finding dates in a page.  Requiring
        the remainder to contain only numeric/missing-value cells also keeps
        dated prose and navigation text on the ordinary head-preserving path.
        """
        date_match = re.match(
            r"^\s*(?P<date>(?:\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4}))(?!\d)",
            line,
        )
        if not date_match:
            return None
        date_text = date_match.group("date")
        parsed_date: datetime | None = None
        date_format = ""
        for candidate_format, candidate_name in (
            ("%Y-%m-%d", "iso"),
            ("%Y/%m/%d", "iso"),
            ("%m/%d/%Y", "us"),
        ):
            try:
                parsed_date = datetime.strptime(date_text, candidate_format)
                date_format = candidate_name
                break
            except ValueError:
                continue
        if parsed_date is None:
            return None
        remainder = line[date_match.end():].replace("−", "-").strip()
        # A second date usually means this is prose or a comparison row, not
        # one record in a single date-indexed table.
        if re.search(
            r"(?<!\d)(?:\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})(?!\d)",
            remainder,
        ):
            return None
        numeric_pattern = re.compile(
            r"[-+]?(?:(?:\d{1,3}(?:,\d{3})+)|(?:\d+))(?:\.\d+)?%?"
        )
        missing_pattern = re.compile(r"(?:N/?A|NA|--+|—|-)", re.IGNORECASE)
        numeric_cells = numeric_pattern.findall(remainder)
        if len(numeric_cells) < 2:
            return None
        residue = numeric_pattern.sub("", remainder)
        residue = missing_pattern.sub("", residue)
        # Permit ordinary table separators and footnote marks, but reject
        # alphabetic labels/sentence fragments so a dated paragraph cannot be
        # mistaken for a row stream.
        residue = re.sub(r"[\s,;|/*†‡()\[\]{}<>.=:_]+", "", residue)
        if residue:
            return None
        return parsed_date, len(numeric_cells), date_format

    def dated_numeric_table() -> tuple[int, int, bool] | None:
        """Find one unambiguous, monotonic dated numeric row block."""
        row_info: dict[int, tuple[datetime, int, str]] = {}
        for index, line in enumerate(lines, start=1):
            row = dated_numeric_row(line)
            if row is not None:
                row_info[index] = row
        runs: list[tuple[int, int, bool]] = []
        index = 1
        while index <= line_count:
            if index not in row_info:
                index += 1
                continue
            start = index
            _, column_count, date_format = row_info[index]
            end = index
            while end + 1 in row_info:
                _, next_column_count, next_date_format = row_info[end + 1]
                if next_column_count != column_count or next_date_format != date_format:
                    break
                end += 1
            if end - start + 1 >= 3:
                dates = [row_info[item][0] for item in range(start, end + 1)]
                ascending = all(left < right for left, right in zip(dates, dates[1:]))
                descending = all(left > right for left, right in zip(dates, dates[1:]))
                if ascending or descending:
                    runs.append((start, end, ascending))
            index = end + 1
        # Multiple plausible blocks are deliberately left to the ordinary
        # excerpt path: choosing one would bind the provider to an arbitrary
        # table on a page.
        return runs[0] if len(runs) == 1 else None

    table = None if market_stream else dated_numeric_table()
    if table is not None:
        table_start, table_end, ascending = table
        # Keep the source title and a small window immediately before the
        # first row.  The window catches a table heading and column header
        # without retaining hundreds of navigation lines above it.
        prefix_indexes: list[int] = []
        for index in range(1, min(2, line_count) + 1):
            prefix_indexes.append(index)
        for index in range(max(1, table_start - 8), table_start):
            if index not in prefix_indexes:
                prefix_indexes.append(index)
        prefix_indexes.sort()

        # Footnotes and explanatory text immediately following the row block
        # are useful context.  Keep a short bounded window; later page chrome
        # remains available in the immutable archive.
        note_indexes: list[int] = []
        for index in range(table_end + 1, min(line_count, table_end + 8) + 1):
            if lines[index - 1].strip():
                note_indexes.append(index)
        note_indexes = note_indexes[:6]

        prefix_values = [render(index) for index in prefix_indexes]
        # A page can put a long explanatory paragraph directly after the
        # table.  Bound that paragraph before reserving row space, while
        # retaining its true line locator and opening text.
        note_values: list[str] = []
        note_budget = min(4_000, max(0, max_chars // 4))
        for index in note_indexes:
            if note_budget <= 0:
                break
            value = render(index)
            if len(value) > note_budget:
                value = value[:note_budget]
            note_values.append(value)
            note_budget -= len(value) + 1
        compaction_note = "[source context compacted; omitted original lines outside the retained table excerpt; cite only supplied original locators]"
        separator_cost = 1
        prefix_cost = sum(len(value) + separator_cost for value in prefix_values)
        note_cost = sum(len(value) + separator_cost for value in note_values)
        note_cost += len(compaction_note) + separator_cost

        # Select from the newest end of the table.  The final output is put
        # back into source order so the line sequence remains readable for
        # both ascending and descending source tables.
        row_indexes: list[int] = []
        row_used = 0
        row_limit = max(1, max_lines - len(prefix_values) - len(note_values) - 1)
        newest_first = range(table_end, table_start - 1, -1) if ascending else range(table_start, table_end + 1)
        available = max_chars - prefix_cost - note_cost
        for index in newest_first:
            value = render(index)
            cost = len(value) + separator_cost
            if row_indexes and (row_used + cost > available or len(row_indexes) >= row_limit):
                break
            if not row_indexes and available <= 0:
                # The normal budgets always leave room for a row, but retain
                # the newest locator if a caller supplies an exceptionally
                # small character budget.
                row_indexes.append(index)
                break
            if not row_indexes and row_used + cost > available:
                row_indexes.append(index)
                row_used += min(cost, max(0, available))
                break
            row_indexes.append(index)
            row_used += cost
        row_indexes.sort()

        selected_values = prefix_values + [render(index) for index in row_indexes] + note_values
        # The omission marker is intentionally after the source/title and
        # heading window, before the selected rows, so the gap in locators is
        # visible without interrupting the chronological row order.
        insertion = len(prefix_values)
        selected_values.insert(insertion, compaction_note)
        compacted = "\n".join(selected_values)
        return compacted[:max_chars]

    header_count = min(2, line_count)
    header_rows = [render(index) for index in range(1, header_count + 1)]
    header_text = "\n".join(header_rows)
    used = len(header_text) + (1 if header_text else 0)
    if market_stream:
        # Reserve space for the omission note and fill from newest to oldest.
        # The final source row is selected first, so current-price context is
        # retained even when only a few rows fit the character budget.
        note_reserve = len("[source context compacted; omitted original lines L3-L999999; cite only supplied original locators]") + 1
        tail_candidates: list[str] = []
        tail_indexes: list[int] = []
        for index in range(line_count, header_count, -1):
            value = render(index)
            if used + len(value) + 1 + note_reserve > max_chars:
                break
            tail_candidates.append(value)
            tail_indexes.append(index)
            used += len(value) + 1
            if len(tail_indexes) >= max(1, max_lines - header_count):
                break
        selected = header_rows + list(reversed(tail_candidates))
        tail_start = min(tail_indexes) if tail_indexes else line_count + 1
        note = ""
        if tail_start > header_count + 1:
            note = f"[source context compacted; omitted original lines L{header_count + 1}-L{tail_start - 1}; cite only supplied original locators]"
        if note:
            # Insert after the canonical header.  If the note cannot fit, the
            # rows remain correctly located and the omission is still obvious
            # from the non-contiguous L-number sequence.
            note_cost = len(note) + 1
            if len("\n".join(selected)) + note_cost <= max_chars:
                selected.insert(header_count, note)
        return "\n".join(selected)[:max_chars]

    # Filing and narrative sources keep their original head for intuitive
    # citations.  When line count is the limiting factor, add the tail only
    # after the head; no fabricated line numbers are introduced.
    head_limit = min(line_count, max(1, max_lines - header_count))
    indexes = list(range(1, head_limit + 1))
    if line_count > max_lines:
        tail_count = max(1, max_lines - header_count - len(indexes))
        indexes += list(range(max(header_count + 1, line_count - tail_count + 1), line_count + 1))
    selected: list[str] = []
    used = 0
    for index in indexes:
        value = render(index)
        if selected and used + len(value) + 1 > max_chars:
            break
        selected.append(value)
        used += len(value) + 1
    if len(selected) < line_count:
        selected.append("[source context compacted; cite only supplied original locators]")
    return "\n".join(selected)[:max_chars]

