"""Local, evidence-linked NLP for transcripts and narrative filing comparisons.

These are deterministic lexical and text-alignment tools, not an LLM or a
trained financial classifier. Originals and full sentence evidence are kept in
the private analysis record so the user can inspect every interpretation.
"""
from __future__ import annotations

import base64
import binascii
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import uuid

from .pdf_extract import extract_pdf

VERSION = "local-document-nlp.v3"
FILING_COMPARISON_VERSION = "local-filing-comparison.v2"
TRANSCRIPT_CONTEXT_VERSION = "transcript-context.v2"
MAX_TEXT = 1_000_000
MAX_BYTES = 5_000_000


class _ReadableHTML(HTMLParser):
    """Read visible HTML without executing it or discarding layout-table prose."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        hidden = tag in {"head", "script", "style", "noscript", "ix:hidden"} or "hidden" in attrs_dict or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs_dict.get("style") or "", re.I))
        if self.hidden_depth:
            if tag not in {"br", "hr", "img", "input", "meta", "link", "wbr"}:
                self.hidden_depth += 1
            return
        if hidden:
            self.hidden_depth = 1
            return
        if tag in {"p", "div", "br", "li", "tr", "table", "section", "h1", "h2", "h3", "h4", "hr"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("  ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden_depth:
            self.hidden_depth -= 1
            return
        if tag in {"p", "div", "li", "tr", "table", "section", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def plain_text(value: str) -> str:
    if len(value) > MAX_BYTES:
        raise ValueError("Document input exceeds the 5,000,000 character limit.")
    if re.search(r"<(?:html|body|p|div|table|span|h[1-6]|br|ix:)\b", value, re.I):
        parser = _ReadableHTML()
        parser.feed(value)
        value = "".join(parser.parts)
    value = value.replace("\u00a0", " ").replace("\u200b", "").replace("\r", "\n")
    result = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines() if line.strip())
    if len(result) > MAX_TEXT:
        raise ValueError("Extracted document text exceeds the 1,000,000 character limit.")
    return result


def import_document(filename: str, encoded: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("The uploaded document is not valid base64.") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Import a nonempty document of at most 5 MB.")
    suffix = Path(filename).suffix.casefold()
    warnings: list[str] = []
    if suffix == ".pdf" or raw.startswith(b"%PDF-"):
        extracted = extract_pdf(raw)
        content = extracted["content"]
        warnings = extracted["metadata"].get("warnings", [])
        fmt = "pdf"
    elif suffix in {".txt", ".text", ".html", ".htm", ".md"}:
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                content = raw.decode("utf-16") if raw[:2] in {b"\xff\xfe", b"\xfe\xff"} else raw.decode("cp1252")
            except UnicodeDecodeError as exc:
                raise ValueError("Save the document as UTF-8 text or HTML before importing.") from exc
        fmt = "html" if suffix in {".html", ".htm"} else "text"
    else:
        raise ValueError("Supported imports are TXT, HTML, HTM, Markdown and text-based PDF.")
    text = plain_text(content)
    if not re.search(r"[A-Za-z]{3}", text):
        raise ValueError("The document contains no readable text.")
    return {"filename": Path(filename).name, "format": fmt, "text": text, "warnings": warnings}


# Word boundaries prevent accidental matches inside names, e.g. Stronghold.
_POSITIVE = {"strong": 1, "strength": 1, "stronger": 1, "robust": 1, "improved": 1, "improvement": 1, "improving": 1, "growth": 1, "growing": 1, "opportunity": 1, "opportunities": 1, "optimistic": 2, "confident": 1, "resilient": 1, "momentum": 1, "outperform": 2, "outperformed": 2, "record": 1, "accelerated": 1, "accelerating": 1, "benefit": 1, "successful": 1, "success": 1, "favorable": 1, "profitable": 1}
_NEGATIVE = {"weak": -1, "weakness": -1, "weaker": -1, "decline": -1, "declined": -1, "declining": -1, "deterioration": -2, "deteriorated": -2, "uncertain": -1, "uncertainty": -1, "risk": -1, "risks": -1, "headwind": -1, "headwinds": -1, "pressure": -1, "pressures": -1, "challenging": -1, "challenges": -1, "disappointing": -2, "shortfall": -2, "adverse": -1, "slowdown": -1, "slowing": -1, "impairment": -1, "loss": -1, "losses": -1, "litigation": -1, "constrained": -1, "difficult": -1, "default": -2, "breach": -2}
_NEGATIONS = {"no", "not", "never", "neither", "without", "hardly", "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't", "cannot", "can't", "couldn't", "won't", "wouldn't"}
_STOP_SCOPE = {"but", "however", "although", "yet", "nevertheless"}
_THEMES = {
    "Demand and growth": r"\b(?:demand|growth|customers?|orders?|backlog|revenue|sales)\b",
    "Margins and costs": r"\b(?:margins?|costs?|expenses?|efficiency|productivity|profitability)\b",
    "Guidance and outlook": r"\b(?:guidance|outlook|expect|expects|forecast|anticipate|anticipates|projected)\b",
    "Capital and liquidity": r"\b(?:liquidity|cash|debt|dividend|buyback|repurchase|financing|capital expenditure)\b",
    "Products and innovation": r"\b(?:product|products|launch|innovation|artificial intelligence|AI|research|development)\b",
    "Risk and regulation": r"\b(?:risk|risks|regulation|regulatory|litigation|tariff|tariffs|compliance|uncertainty)\b",
    "Operations and supply": r"\b(?:supply|capacity|inventory|manufacturing|operations|sourcing)\b",
}
_ROLES = r"\b(?:chief\s+[\w ]{0,25}officer|ceo|cfo|coo|cto|president|chairman|chairwoman|director|vice president|[es]?vp|treasurer|investor relations|analyst|operator)\b"
_TITLE_START = re.compile(r"^(?:(?:senior|executive|managing|independent|non-executive|lead|equity|research|financial|corporate|deputy|group)\s+)*(?:chief\b|[a-z]?vp\b|ceo\b|cfo\b|coo\b|cto\b|president\b|vice president\b|chair(?:man|woman|person)?\b|director\b|treasurer\b|investor relations\b|analyst\b)", re.I)
_PERSON_NAME = re.compile(r"[A-Z][\w.'’\-]+(?:\s+(?:(?:de|van|von|der|da|del)\s+)?[A-Z][\w.'’\-]+){1,4}")
_ENTITY_OPENERS = frozenset("""
acquisition acquisitions additionally adjusted all also although any are based because both
business can capital cash certain compared compensation competition competitive continued cost
costs could currently customer customers demand despite development did do does domestic
during each earnings every expense expenses finally following further furthermore given good
gross growth guidance had has have how however including income interest international
inventory investment investments is its less let liquidity looking management manufacturing
many margin margins more most moving net operating other overall performance please price
prices pricing product production products profit profits recently regulation regulatory
research restructuring revenue revenues sales service services should some such supply tariff
tariffs thank their there these they those total turning was welcome were what when where
which while who why will would you your
""".split()) | _POSITIVE.keys() | _NEGATIVE.keys()



def sentiment(text: str) -> dict[str, Any]:
    tokens = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?|[,;.!?]", text.casefold().replace("’", "'"))
    cues: list[dict[str, Any]] = []
    context: list[str] = []
    for token in tokens:
        if token in {",", ";", ".", "!", "?"} or token in _STOP_SCOPE:
            context = []
            continue
        weight = _POSITIVE.get(token, _NEGATIVE.get(token, 0))
        if weight:
            preceding = context[-5:]
            negations = sum(word in _NEGATIONS for word in preceding)
            # "not only strong" is additive, not a denial of strength.
            if "not" in preceding and "only" in preceding[preceding.index("not") + 1:]:
                negations -= 1
            negated = bool(negations % 2)
            if negated:
                weight *= -1
            cues.append({"term": token, "polarity": "positive" if weight > 0 else "negative", "negated": negated, "weight": weight})
        context.append(token)
    total = sum(cue["weight"] for cue in cues)
    denominator = sum(abs(cue["weight"]) for cue in cues)
    score = round(total / denominator, 3) if denominator else 0.0
    return {"label": "positive" if score > .15 else "negative" if score < -.15 else "neutral", "score": score, "cues": cues}


def _sentences(text: str) -> list[str]:
    # Protect common abbreviations and decimal points while separating prose.
    text = re.sub(r"\b(Mr|Mrs|Ms|Dr|Inc|Corp|Co|Ltd|vs|e\.g|i\.e)\.", lambda m: m[0].replace(".", "\u2024"), text, flags=re.I)
    text = re.sub(r"(?<=\d)\.(?=\d)", "\u2024", text)
    text = re.sub(r"\b(?:[A-Za-z]\.){2,}", lambda m: m[0].replace(".", "\u2024"), text)
    return [part.strip().replace("\u2024", ".") for part in re.split(r"(?<=[.!?])\s+(?=[\"“'‘(A-Z0-9])", text) if part.strip()]


def _aggregate(sentences: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["sentiment"]["label"] for row in sentences)
    cues = [cue for row in sentences for cue in row["sentiment"]["cues"]]
    total = sum(cue["weight"] for cue in cues)
    denominator = sum(abs(cue["weight"]) for cue in cues)
    score = round(total / denominator, 3) if denominator else 0.0
    return {"label": "positive" if score > .15 else "negative" if score < -.15 else "neutral", "score": score, "sentences": len(sentences), **{label: counts[label] for label in ("positive", "negative", "neutral")}}


def _person_name(label: str) -> bool:
    return bool(_PERSON_NAME.fullmatch(label) and not _TITLE_START.match(label)
                and label.split()[0].casefold() not in _ENTITY_OPENERS
                and label.casefold() not in {"data source", "corporate participants", "conference participants", "call participants"})


def _title_role(title: str) -> str:
    if re.search(r"\banalyst\b", title, re.I):
        return "analyst"
    # Director, VP and President can be issuer or brokerage titles. Keep them
    # unknown unless an executive function or question introduction resolves it.
    if re.search(r"\b(?:chief\s+[\w ]{0,25}officer|ceo|cfo|coo|cto|chairman|chairwoman|treasurer|investor relations)\b", title, re.I):
        return "management"
    return "unknown"


def _speaker(line: str, known: dict[str, str]) -> tuple[str, str, str] | None:
    line = line.strip()
    colon = re.match(r"^([^:]{1,95}):\s*(.*)$", line)
    label, rest = (colon[1], colon[2]) if colon else (line, "")
    if label in known:
        return label, known[label], rest
    if re.fullmatch(r"operator|moderator", label, re.I):
        return label.title(), "operator", rest
    role = re.search(_ROLES, label, re.I)
    match = re.match(r"^([A-Z][\w.'’\-]+(?:\s+[A-Z][\w.'’\-]+){1,3})(?:\s*[-–—,|]\s*|\s*\(|\s*$)", label)
    if match and (role or colon):
        name = match[1].strip()
        if not _person_name(name):
            return None
        role_name = known.get(name, _title_role(label[match.end(1):]))
        return name, role_name, rest
    return None


def _transcript_headings(lines: list[str]) -> tuple[dict[int, tuple[str, str, str]], set[int]]:
    """Resolve inline and adjacent name/title labels from the same evidence.

    Role-only lines are metadata, never people. Operator introductions provide
    additional evidence for ambiguous titles such as Managing Director.
    """
    headings: dict[int, tuple[str, str, str]] = {}
    title_lines: set[int] = set()
    known: dict[str, str] = {}
    for index, line in enumerate(lines):
        if index in title_lines:
            continue
        match = _speaker(line, {})
        if match is None and _person_name(line) and index + 1 < len(lines) and _TITLE_START.match(lines[index + 1]):
            match = (line, _title_role(lines[index + 1]), "")
            title_lines.add(index + 1)
        if match:
            headings[index] = match
            name, role, _ = match
            if role != "unknown" or name not in known:
                known[name] = role

    current_role = "unknown"
    for index, line in enumerate(lines):
        match = headings.get(index) or _speaker(line, known)
        if match:
            _, current_role, line = match
        introduction = re.search(r"\bquestion\b.{0,100}?\bfrom\s+(?:(?:the\s+)?line\s+of\s+)?(?:(?:mr|ms|mrs|dr)\.?\s+)?", line, re.I) if current_role == "operator" else None
        if introduction:
            for name in sorted(known, key=len, reverse=True):
                # Only the person introduced as the questioner gains this
                # role. A short name must not match a longer person's name,
                # including when that longer identity has no known label.
                if re.match(rf"{re.escape(name)}(?=\s+(?:with|of|from|at)\b|\s*[,.;:!?]|\s*$)", line[introduction.end():], re.I):
                    known[name] = "analyst"
                    break

    # A later label may omit its title. Keep the same source-established role.
    for index, line in enumerate(lines):
        if index in title_lines:
            continue
        match = headings.get(index) or _speaker(line, known)
        if match:
            name, role, rest = match
            headings[index] = (name, known.get(name, role), rest)
    return headings, title_lines


def _entities(sentences: list[dict[str, Any]], speakers: dict[str, str], ticker: str) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    seen_evidence: dict[tuple[str, str], set[int]] = defaultdict(set)
    excluded = {"We", "Our", "The", "This", "That", "It", "I", "As", "And", "But", "In", "On", "At", "For", "Thank", "Thanks", "Yes", "No", "Today", "Good", "Now", "Operator", "Question", "Answer", "First", "Second", "Third", "Fourth", "Quarter", "Fiscal", "Year", "CEO", "CFO", "COO", "Q", "A", "U", "S"}
    places = {"United States", "United Kingdom", "Europe", "China", "India", "Japan", "Canada", "Asia", "Germany", "France", "Australia", "Latin America", "North America", "Middle East", "Mexico", "Taiwan"}
    def add(name: str, kind: str, row: dict[str, Any], mentions: int = 1) -> None:
        key = (name.casefold(), kind)
        item = found.setdefault(key, {"name": name, "type": kind, "mentions": 0, "evidence": []})
        item["mentions"] += mentions
        if row["id"] not in seen_evidence[key]:
            seen_evidence[key].add(row["id"])
            item["evidence"].append({"sentence_id": row["id"], "text": row["text"], "speaker": row["speaker"]})
    for row in sentences:
        text = row["text"]
        if row["speaker"] != "Unattributed":
            add(row["speaker"], "speaker", row)
        matches = list(re.finditer(r"\b(?:[A-Z][a-zA-Z0-9&’'\-]*)(?:\s+(?:of|and|the|&)?\s*[A-Z][a-zA-Z0-9&’'\-]*){0,4}\b", text))
        for match in matches:
            name = match[0].strip()
            # Strip sentence-openers while keeping named organizations/products.
            name = re.sub(r"^(?:The|Our|We|In|At|And|But|For|With|From)\s+", "", name)
            if name in excluded or len(name) < 2 or re.fullmatch(r"Q[1-4]|FY\d*", name):
                continue
            if " " not in name and not re.search(r"[A-Za-z0-9]", text[:match.start()]) and not name.isupper() and name not in places:
                # Suppress ordinary sentence-openers, but retain single proper
                # names such as Microsoft even on their first mention.
                if name.casefold() in _ENTITY_OPENERS:
                    continue
            kind = "person" if name in speakers else "location" if name in places else "organization" if re.search(r"\b(?:Inc|Corp|Corporation|Limited|Ltd|LLC|Group|Bank|Technologies|Company)\b", name) else "named entity"
            add(name, kind, row)
        for match in re.finditer(r"\$([A-Z]{1,8})\b", text):
            add(match[1], "ticker", row)
        if ticker and re.search(rf"\b{re.escape(ticker)}\b", text, re.I):
            add(ticker.upper(), "ticker", row)
    return sorted(found.values(), key=lambda x: (-x["mentions"], x["name"]))


def _reading_turns(content: str) -> list[dict[str, Any]]:
    """Keep speaker turns, including utterances too short for lexical analysis."""
    lines = content.splitlines()
    headings, title_lines = _transcript_headings(lines)
    turns: list[dict[str, Any]] = []
    speaker, role, section = "Unattributed", "unknown", "Prepared remarks"
    current: dict[str, Any] | None = None
    for index, line in enumerate(lines):
        if index in title_lines:
            continue
        if re.match(r"^(?:ResearchCouncil archived PDF|Original SHA256:|Extraction:|\[PDF page |\[Text extraction unavailable)", line):
            continue
        if re.fullmatch(r"(?:questions?\s*(?:and|&|-)\s*answers?|q\s*&\s*a)(?: session)?[.:]?", line, re.I):
            section, current = "Q&A", None
            continue
        if re.fullmatch(r"(?:prepared|opening|closing) remarks|presentation", line, re.I):
            section, current = "Prepared remarks", None
            continue
        match = headings.get(index)
        if match:
            speaker, role, line = match
            current = None
            if role == "analyst":
                section = "Q&A"
        if not line:
            continue
        if current is None:
            current = {"id": f"turn-{len(turns) + 1}", "speaker": speaker, "role": role, "section": section, "text": "", "sentence_ids": [], "_sentences": []}
            turns.append(current)
        current["text"] += ("\n" if current["text"] else "") + line
        # Match the original analyzer's line-level sentence boundaries. Do not
        # rerun sentiment or change sentence IDs when reading an older record.
        current["_sentences"].extend(_sentences(line))
    return turns


def _analyst_acknowledgement(text: str, names: set[str]) -> bool:
    """Only recognize explicit pleasantries, never shorten a real question."""
    words = re.sub(r"[^a-z' ]+", " ", text.casefold())
    words = " ".join(words.split())
    recipients = {name.casefold() for name in names} | {name.split()[0].casefold() for name in names}
    recipient = "(?:" + "|".join(re.escape(name) for name in sorted(recipients, key=len, reverse=True)) + ")" if recipients else r"(?!)"
    phrase = rf"(?:thank you(?: very much)?(?: both| guys| {recipient})?|thanks(?: so much| very much)?(?: guys| {recipient})?|okay|ok|great|got it|that's helpful|that is helpful|understood|appreciate (?:it|the (?:color|colour|detail|context|help))|good afternoon|good morning|good evening|hello|hi)"
    return bool(re.fullmatch(rf"{phrase}(?:\s+{phrase})*", words))


def enrich_transcript(result: dict[str, Any], text: str | None = None) -> dict[str, Any]:
    """Add a versioned reading projection without rewriting a saved analysis.

    A response means a labeled management turn follows the analyst's question;
    it does not establish that management resolved the issue. Unknown speakers
    are never assigned a business role. Raw input is essential for complete
    text because historical sentence analysis omitted short utterances.
    """
    prior_context = result.get("reading_context") or {}
    if text is None and prior_context.get("full_text_available"):
        text = prior_context.get("transcript_text")
    full_text_available = isinstance(text, str) and bool(text.strip())
    sentences = [dict(row) for row in result.get("sentences", [])]
    for row in sentences:
        row.pop("turn_id", None)
        row.pop("exchange_id", None)
        row.pop("statement_kind", None)
    if full_text_available:
        content = plain_text(text)
        # Plain-text originals retain every word, heading and short utterance.
        # Imported HTML is exposed as readable text, with the original source
        # still available from the private archive.
        transcript_text = content if re.search(r"<(?:html|body|p|div|table|span|h[1-6]|br|ix:)\b", text, re.I) else text
        turns = _reading_turns(content)
        indexed: dict[tuple[str, str], deque[int]] = defaultdict(deque)
        for index, row in enumerate(sentences):
            indexed[(" ".join(row["text"].split()), row.get("speaker", "Unattributed"))].append(index)
        for turn in turns:
            for sentence in turn.pop("_sentences"):
                key = (" ".join(sentence.split()), turn["speaker"])
                if indexed[key]:
                    row = sentences[indexed[key].popleft()]
                    row["turn_id"] = turn["id"]
                    turn["sentence_ids"].append(row["id"])
    else:
        # This is a clearly labeled fallback, never advertised as a complete
        # transcript. Contiguous evidence can still be read in conversation.
        turns = []
        for row in sentences:
            identity = (row.get("speaker", "Unattributed"), row.get("role", "unknown"), row.get("section", "Prepared remarks"))
            if not turns or identity != (turns[-1]["speaker"], turns[-1]["role"], turns[-1]["section"]):
                turns.append({"id": f"turn-{len(turns) + 1}", "speaker": identity[0], "role": identity[1], "section": identity[2], "text": "", "sentence_ids": []})
            turn = turns[-1]
            turn["text"] += ("\n" if turn["text"] else "") + row["text"]
            turn["sentence_ids"].append(row["id"])
            row["turn_id"] = turn["id"]
        transcript_text = "\n\n".join(f"{turn['speaker']}\n{turn['text']}" for turn in turns)

    exchanges: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    speaker_names = {turn["speaker"] for turn in turns if turn["role"] == "management"}
    for turn in turns:
        role = turn["role"]
        turn["exchange_id"] = None
        turn["statement_kind"] = "operator" if role == "operator" else "management_remark" if role == "management" else "unattributed"
        if turn["section"] != "Q&A":
            active = None
        if role == "analyst" and not _analyst_acknowledgement(turn["text"], speaker_names):
            active = {"id": f"exchange-{len(exchanges) + 1}", "question_turn_ids": [turn["id"]], "answer_turn_ids": [], "context_turn_ids": [], "sentence_ids": [], "answered": False}
            exchanges.append(active)
            turn["statement_kind"] = "analyst_question"
        elif role == "unknown":
            # An unlabeled speaker might be the next analyst. Crossing that
            # boundary would attach a later answer to the wrong question.
            active = None
        elif active is not None:
            if role == "management":
                active["answer_turn_ids"].append(turn["id"])
                active["answered"] = True
                turn["statement_kind"] = "management_answer"
            else:
                active["context_turn_ids"].append(turn["id"])
        if active is not None:
            turn["exchange_id"] = active["id"]
            active["sentence_ids"].extend(turn["sentence_ids"])
        if role == "operator" and re.search(r"\b(?:no (?:more|further) questions|(?:concludes?|ends?) (?:the |our |today's )?(?:call|conference|question|q&a)|conclusion of (?:the |our )?(?:call|question))\b", turn["text"], re.I):
            active = None
    by_id = {turn["id"]: turn for turn in turns}
    for row in sentences:
        turn = by_id.get(row.get("turn_id"))
        row["turn_id"] = turn["id"] if turn else None
        row["exchange_id"] = turn["exchange_id"] if turn else None
        row["statement_kind"] = turn["statement_kind"] if turn else "operator" if row.get("role") == "operator" else "management_remark" if row.get("role") == "management" else "unattributed"
    return {**result, "sentences": sentences, "reading_context": {"version": TRANSCRIPT_CONTEXT_VERSION, "transcript_text": transcript_text, "full_text_available": full_text_available, "turns": turns, "exchanges": exchanges}}


def analyze_transcript(text: str, ticker: str = "") -> dict[str, Any]:
    content = plain_text(text)
    if len(re.findall(r"\w+", content)) < 10:
        raise ValueError("Add at least ten words of earnings-call transcript to analyze.")
    turns = _reading_turns(content)
    known = {turn["speaker"]: turn["role"] for turn in turns}
    rows: list[dict[str, Any]] = []
    for turn in turns:
        for sentence in turn["_sentences"]:
            if len(re.findall(r"\w+", sentence)) < 3:
                continue
            rows.append({"id": len(rows) + 1, "text": sentence, "speaker": turn["speaker"], "role": turn["role"], "section": turn["section"], "sentiment": sentiment(sentence)})
    if not rows:
        raise ValueError("No readable transcript sentences were found.")
    # Operator boilerplate must not drive the overall business tone.
    substantive = [row for row in rows if row["role"] != "operator"]
    aggregate = _aggregate(substantive)
    by_speaker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_speaker[row["speaker"]].append(row)
    speakers = [{"name": name, "role": items[0]["role"], "sentiment": _aggregate(items)} for name, items in by_speaker.items()]
    themes = []
    for name, pattern in _THEMES.items():
        evidence = [{"sentence_id": row["id"], "text": row["text"], "speaker": row["speaker"]} for row in rows if re.search(pattern, row["text"], re.I)]
        if evidence:
            themes.append({"name": name, "mentions": len(evidence), "evidence": evidence})
    return enrich_transcript({"method": VERSION + " · lexical sentiment and rule-based entity candidates", "limitations": ["English-language local rules do not infer intent, sarcasm or investment outcomes. Sentiment is a lexical signal, not a probability or recommendation.", "Entity labels are candidates; capitalization and speaker labels can be ambiguous. Inspect linked evidence.", "Operator remarks are excluded from the overall score. Speaker roles are inferred from supplied labels and operator question introductions; ambiguous speakers remain unknown."], "summary": f"{len(rows)} sentences across {len(speakers)} speakers; overall lexical tone is {aggregate['label']} ({aggregate['positive']} positive, {aggregate['negative']} negative, {aggregate['neutral']} neutral substantive sentences).", "sentiment": aggregate, "speakers": speakers, "entities": _entities(rows, known, ticker), "themes": sorted(themes, key=lambda item: -item["mentions"]), "sentences": rows, "characters": len(content), "source_hash": hashlib.sha256(text.encode()).hexdigest()}, text)


_ITEM = re.compile(r"^item\s+(\d+[a-z]?)\s*[.\-:–—]?\s*(.*)$", re.I)
_FINANCIAL = re.compile(r"^(?:condensed\s+)?(?:consolidated\s+)?(?:unaudited\s+)?(?:financial statements|balance sheets?|statements? of (?:operations|income|earnings|cash flows?|stockholders|shareholders|comprehensive))", re.I)
_PART = re.compile(r"^part\s+(IV|III|II|I|[1-4])\b\s*[.\-:–—]?\s*(.*)$", re.I)
_RISK_CUES = r"\b(?:material(?:ly)?|risk|risks|uncertainty|uncertain|adverse|litigation|default|covenant|breach|impairment|weakness|going concern|cybersecurity|regulatory|liquidity|disruption|disruptions)\b"
_MODAL_CUES = r"\b(?:may|might|could|will|expect|expects|expectation|no longer|not|unable|unlikely|likely|ceased|suspended|increased|decreased|significant(?:ly)?)\b"


def _numeric_row(line: str) -> bool:
    numbers = re.findall(r"(?<![A-Za-z])\(?[-+]?\d[\d,.%]*\)?", line)
    words = re.findall(r"[A-Za-z]+", line)
    # Prose containing a verb remains eligible even with many quantities.
    has_verb = bool(re.search(r"\b(?:was|were|is|are|has|have|had|increased|decreased|grew|declined|expect|expects|believe|believes|resulted|reflects|remain|remains|will|may|could)\b", line, re.I))
    is_sentence = len(words) >= 5 and bool(re.search(r"[.!?][\"’']?$", line))
    return bool(numbers) and not has_verb and not is_sentence and (len(numbers) >= 2 and len(words) <= 12 or not words)


def _canonical(text: str) -> str:
    text = text.casefold().replace("’", "'")
    # Regulatory/accounting references and hyphenated product identifiers
    # carry meaning independent of financial figures (Section 301, GPT-4).
    references: list[str] = []
    def protect(match: re.Match) -> str:
        references.append(match[0])
        return " reference" + chr(0xE000 + len(references) - 1) + " "
    text = re.sub(r"\b(?:section|rule|topic|standard|asc|ifrs|form)\s+[a-z]*\d[\w.\-]*", protect, text)
    text = re.sub(r"[$€£¥]\s*[-+]?\d[\d,.]*\s*(?:m|bn|b|k)\b", " [number] ", text)
    text = re.sub(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:,?\s+\d{4})?\b", " [date] ", text)
    text = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", " [date] ", text)
    text = re.sub(r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\b|\bq[1-4]\b", " [quarter] ", text)
    text = re.sub(r"(?<![a-z0-9-])(?:[$€£¥]\s*)?\(?[-+]?\d[\d,]*(?:\.\d+)?\)?(?:\s*%|\s+(?:million|billion|thousand|trillion|percent|basis points))?(?![a-z])", " [number] ", text)
    for index, reference in enumerate(references):
        text = text.replace("reference" + chr(0xE000 + index), reference)
    text = re.sub(r"[\W_]+", " ", text)
    return " ".join(text.split())


def _filing_form(value: str, supplied: str | None = None) -> str | None:
    """Resolve the form without mistaking references to an older filing for it."""
    if supplied:
        normalized = supplied.upper().replace(" ", "").removesuffix("/A")
        normalized = {"10K": "10-K", "10Q": "10-Q"}.get(normalized, normalized)
        if normalized not in {"10-K", "10-Q"}:
            raise ValueError("Filing comparison supports Form 10-K or Form 10-Q.")
        return normalized
    content = plain_text(value)
    cover = re.search(r"^\s*(?:SEC\s+)?FORM\s+(10[- ]?[KQ])(?:/A)?\s*$", content[:30_000], re.I | re.M)
    if cover:
        return "10-K" if cover[1][-1].upper() == "K" else "10-Q"
    # These item numbers distinguish the forms even when the cover is omitted.
    if re.search(r"^item\s+(?:7[A]?|8|9[A-B]?)\b", content, re.I | re.M):
        return "10-K"
    if re.search(r"^item\s+2\b[^\n]*management|^item\s+1\b[^\n]*financial statements", content, re.I | re.M):
        return "10-Q"
    return None


def _filing_units(value: str, form: str | None = None) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    content = plain_text(value)
    lines = content.splitlines()
    exclusions: Counter[str] = Counter()
    units: list[dict[str, Any]] = []
    part, section, skip_financial, seen_item = "I", "Narrative", False, False
    skip_administrative = False
    consumed_headings: set[int] = set()
    consumed_toc: set[int] = set()
    buffer: list[str] = []
    def flush() -> None:
        if not buffer:
            return
        paragraph = " ".join(buffer)
        buffer.clear()
        for sentence in _sentences(paragraph):
            if len(re.findall(r"[A-Za-z]+", sentence)) < 4:
                exclusions["short_or_numeric"] += 1
                continue
            units.append({"text": sentence, "section": section, "key": _canonical(sentence)})
    for index, line in enumerate(lines):
        if index in consumed_toc:
            exclusions["table_of_contents"] += 1
            continue
        if index in consumed_headings:
            exclusions["headings"] += 1
            continue
        # PDF import provenance is metadata, never filing commentary.
        if re.match(r"^(?:ResearchCouncil archived PDF|Original SHA256:|Extraction:|\[PDF page |\[Text extraction unavailable)", line):
            continue
        if re.fullmatch(r"\d+|page\s+\d+(?:\s+of\s+\d+)?|table of contents", line, re.I):
            exclusions["boilerplate"] += 1
            continue
        part_match = _PART.match(line)
        item = _ITEM.match(line)
        # SEC TOC rows often end in a page number; don't start a section there.
        toc = ((item and item[2].strip()) or (part_match and part_match[2].strip())) and bool(re.search(r"(?:\.{2,}|\s)\d+\s*$", line))
        if toc:
            exclusions["table_of_contents"] += 1
            continue
        # Layout tables/PDFs can split a TOC row into item, title and page.
        # Require a following item/part (or EOF) so a real page-top title is
        # not discarded merely because a page number follows it.
        if item:
            next_index = index + 1
            title_index = None
            if not item[2].strip() and next_index < len(lines) and not (_ITEM.match(lines[next_index]) or _PART.match(lines[next_index])):
                title_index = next_index
                next_index += 1
            if next_index < len(lines) and re.fullmatch(r"\d+", lines[next_index]) and (next_index + 1 == len(lines) or _ITEM.match(lines[next_index + 1]) or _PART.match(lines[next_index + 1])):
                consumed_toc.add(next_index)
                if title_index is not None:
                    consumed_toc.add(title_index)
                exclusions["table_of_contents"] += 1
                continue
        if part_match and len(line) < 120:
            flush()
            part = {"1": "I", "2": "II", "3": "III", "4": "IV"}.get(part_match[1].upper(), part_match[1].upper())
            skip_financial = False
            section = f"Part {part}"
            skip_administrative = False
            continue
        if item and len(line) < 200:
            flush()
            number, heading = item[1].upper(), item[2].strip()
            seen_item = True
            if form == "10-K":
                # SEC excerpts often omit PART headings. Annual item numbers
                # determine the part, including Item 1A in Part I.
                item_number = int(re.match(r"\d+", number)[0])
                part = "I" if item_number < 5 else "II" if item_number < 10 else "III" if item_number < 15 else "IV"
            skip_administrative = (form == "10-K" and number in {"15", "16"}) or (form != "10-K" and part == "II" and number == "6")
            # Standalone Item 1 followed by a heading is common in SEC HTML.
            if not heading and index + 1 < len(lines) and len(lines[index + 1]) < 180 and not (_ITEM.match(lines[index + 1]) or _PART.match(lines[index + 1])) and not re.search(r"[.!?]$", lines[index + 1]):
                heading = lines[index + 1]
                consumed_headings.add(index + 1)
            if form != "10-K" and part == "I" and ("legal proceedings" in heading.casefold() or "risk factors" in heading.casefold()):
                part = "II"
            section = f"Part {part} · Item {number}" + (f" · {heading}" if heading else "")
            skip_financial = (form == "10-K" and number == "8") or (form != "10-K" and part == "I" and number == "1" and (not heading or bool(re.search(r"financial|unaudited|condensed|consolidated", heading, re.I))))
            continue
        if re.fullmatch(r"signatures?", line, re.I):
            flush()
            skip_administrative = True
        if skip_administrative:
            exclusions["exhibits_and_signatures"] += 1
            continue
        if _FINANCIAL.match(line) and len(line) < 160 and (not seen_item or re.match(r"Part I · Item 1(?: ·|$)", section)):
            flush()
            skip_financial = True
            exclusions["financial_statement_lines"] += 1
            continue
        # Excerpts without item numbers can still return to narrative sections.
        if re.match(r"^(?:management[’']?s discussion|risk factors|legal proceedings|liquidity and capital resources|controls and procedures|quantitative and qualitative disclosures)", line, re.I) and len(line) < 180:
            flush()
            # Keep SEC Item identity stable when subsection titles change.
            if not seen_item:
                skip_financial = False
                section = line
            continue
        if skip_financial:
            exclusions["financial_statement_lines"] += 1
            continue
        if _numeric_row(line):
            flush()
            exclusions["numeric_table_rows"] += 1
            continue
        if len(line) < 90 and not re.search(r"[.!?]$", line) and (line.isupper() or len(line.split()) < 9 and line.istitle()):
            flush()
            exclusions["headings"] += 1
            # Section identity is the SEC item; subheadings may be renamed.
            continue
        # Join soft-wrapped plain text lines, preserving complete sentences.
        buffer.append(line)
        if re.search(r"[.!?][\"’']?$", line):
            flush()
    flush()
    if seen_item:
        exclusions["cover_and_preamble"] += sum(row["section"] == "Narrative" for row in units)
        units = [row for row in units if row["section"] != "Narrative"]
    if not units:
        raise ValueError("No comparable narrative was found. Include MD&A, risk factors or another commentary section.")
    return units, dict(exclusions), (f"SEC item sections recognized ({form or 'form unspecified'})" if seen_item else "No SEC item headings found; treating supplied text as narrative excerpts.")


def _section_key(section: str, form: str | None = None) -> str:
    match = re.match(r"Part (IV|III|II|I) · Item (\d+[A-Z]?)", section)
    if match and form:
        # Annual and quarterly MD&A/risk sections have different SEC item
        # identities. Only their known semantic equivalents are aligned.
        if form == "10-K":
            mapping = {
                "1": "Business", "1A": "Risk factors",
                "1B": "Unresolved staff comments", "1C": "Cybersecurity",
                "2": "Properties", "3": "Legal proceedings", "7": "MD&A",
                "7A": "Market risk", "9A": "Controls and procedures",
            }
        else:
            mapping = {
                "I:2": "MD&A", "I:3": "Market risk",
                "I:4": "Controls and procedures", "II:1": "Legal proceedings",
                "II:1A": "Risk factors",
            }
        key = match[2] if form == "10-K" else f"{match[1]}:{match[2]}"
        if key in mapping:
            return mapping[key]
    # Excerpts may have only a heading. Avoid matching every occurrence of
    # 'risk' (MD&A often mentions risks without becoming Risk Factors).
    heading = section[match.end():].strip(" ·") if match else section
    patterns = (
        (r"^management[’']?s?\s+discussion|^md&a\b", "MD&A"),
        (r"^risk factors\b", "Risk factors"),
        (r"^legal proceedings\b", "Legal proceedings"),
        (r"^quantitative and qualitative disclosures", "Market risk"),
        (r"^controls and procedures", "Controls and procedures"),
    )
    for pattern, key in patterns:
        if re.search(pattern, heading, re.I):
            return key
    return match[0] if match else section.casefold()


def compare_filings(
    previous_text: str, current_text: str, *,
    previous_form: str | None = None, current_form: str | None = None,
) -> dict[str, Any]:
    previous_form = _filing_form(previous_text, previous_form)
    current_form = _filing_form(current_text, current_form)
    previous, old_exclusions, old_note = _filing_units(previous_text, previous_form)
    current, new_exclusions, new_note = _filing_units(current_text, current_form)
    old_sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
    new_sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in previous:
        old_sections[_section_key(row["section"], previous_form)].append(row)
    for row in current:
        new_sections[_section_key(row["section"], current_form)].append(row)
    old_only, new_only = sorted(old_sections.keys() - new_sections.keys()), sorted(new_sections.keys() - old_sections.keys())
    missing_core = {"previous": sorted({"MD&A", "Risk factors"} - old_sections.keys()) if previous_form else [], "current": sorted({"MD&A", "Risk factors"} - new_sections.keys()) if current_form else []}
    coverage_notes = []
    if previous_form and current_form and previous_form != current_form:
        coverage_notes.append("Forms differ: only known equivalent narrative sections are aligned. Annual and quarterly disclosure scope differs; additions and removals do not by themselves establish new or withdrawn disclosure.")
    if old_only or new_only:
        coverage_notes.append("Section coverage differs. Passages in a section found in only one input are additions/removals within the supplied text, not evidence that the company introduced or withdrew that disclosure. Previous-only sections: " + (", ".join(old_only) or "none") + ". Current-only sections: " + (", ".join(new_only) or "none") + ".")
    for label, missing in missing_core.items():
        if missing:
            coverage_notes.append(f"{label.capitalize()} input has no extracted narrative for {', '.join(missing)}. Check the original filing and extraction coverage; this comparison cannot establish that the disclosure is absent.")
    counts = Counter({"added": 0, "removed": 0, "changed": 0, "unchanged": 0, "numeric_only": 0, "excluded": sum(old_exclusions.values()) + sum(new_exclusions.values())})
    changes: list[dict[str, Any]] = []
    def change(kind: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
        old, new = (before or {}).get("text", ""), (after or {}).get("text", "")
        cues = sorted(set(re.findall(_RISK_CUES + "|" + _MODAL_CUES, old + " " + new, re.I)), key=str.casefold)
        risk = re.search(_RISK_CUES, old + " " + new, re.I) or re.search(r"risk factors|market risk", (after or before)["section"], re.I)
        significance = "Risk language — review" if risk else "Outlook or commitment language — review" if cues else "Narrative change"
        counts[kind] += 1
        changes.append({"id": len(changes) + 1, "type": kind, "section": (after or before)["section"], "before": old, "after": new, "significance": significance, "cues": cues, "previous_section": (before or {}).get("section"), "current_section": (after or {}).get("section"), "section_alignment": "missing_in_previous" if section in new_only else "missing_in_current" if section in old_only else "matched"})
    for section in dict.fromkeys([*new_sections, *old_sections]):
        before, after = old_sections.get(section, []), new_sections.get(section, [])
        # Match exact normalized prose as a multiset: moved paragraphs are
        # unchanged, and repeated boilerplate is not lost to a set operation.
        by_key: dict[str, list[int]] = defaultdict(list)
        for i, row in enumerate(before):
            by_key[row["key"]].append(i)
        used_old, used_new = set(), set()
        for j, row in enumerate(after):
            available = by_key.get(row["key"], [])
            if available:
                i = available.pop(0)
                used_old.add(i)
                used_new.add(j)
                if before[i]["text"] != row["text"] and re.search(r"\d", before[i]["text"] + row["text"]):
                    counts["numeric_only"] += 1
                else:
                    counts["unchanged"] += 1
        remaining_old = [(i, row) for i, row in enumerate(before) if i not in used_old]
        remaining_new = [(j, row) for j, row in enumerate(after) if j not in used_new]
        # Token-index candidates avoid a quadratic comparison of full filings.
        inverted: dict[str, set[int]] = defaultdict(set)
        for i, row in remaining_old:
            for word in set(row["key"].split()) - {"the", "a", "of", "to", "and", "in", "number", "we", "our", "is", "for", "on", "as"}:
                inverted[word].add(i)
        proposals: list[tuple[float, int, int]] = []
        for j, row in remaining_new:
            candidates: Counter[int] = Counter()
            for word in set(row["key"].split()):
                candidates.update(inverted.get(word, set()))
            for i, overlap in candidates.most_common(12):
                if overlap < 2:
                    continue
                ratio = SequenceMatcher(None, before[i]["key"], row["key"], autojunk=False).ratio()
                if ratio >= .52:
                    proposals.append((ratio, i, j))
        for ratio, i, j in sorted(proposals, reverse=True):
            if i not in used_old and j not in used_new:
                change("changed", before[i], after[j])
                used_old.add(i)
                used_new.add(j)
        for j, row in remaining_new:
            if j not in used_new:
                change("added", None, row)
        for i, row in remaining_old:
            if i not in used_old:
                change("removed", row, None)
    return {"method": FILING_COMPARISON_VERSION + " · section-aware normalized narrative alignment", "limitations": ["Financial statement sections, numeric table rows and numeric/date-only edits are excluded. Qualitative MD&A, liquidity and risk wording is retained.", "Pairs are text-similarity candidates, not a legal or accounting materiality judgment. Review original source wording; section moves may appear as additions and removals.", old_note, new_note, *coverage_notes], "summary": f"{counts['changed']} changed, {counts['added']} added and {counts['removed']} removed narrative passages. {counts['numeric_only']} numeric/date-only changes and {counts['excluded']} non-narrative lines excluded.", "counts": dict(counts), "changes": changes, "exclusions": {"previous": old_exclusions, "current": new_exclusions}, "coverage": {"matched_sections": sorted(old_sections.keys() & new_sections.keys()), "previous_only_sections": old_only, "current_only_sections": new_only, "cross_form": bool(previous_form and current_form and previous_form != current_form)}, "documents": {"previous": {"narrative_sentences": len(previous), "source_hash": hashlib.sha256(previous_text.encode()).hexdigest(), "section_detection": old_note, "form": previous_form, "sections": sorted(old_sections), "missing_core_sections": missing_core["previous"]}, "current": {"narrative_sentences": len(current), "source_hash": hashlib.sha256(current_text.encode()).hexdigest(), "section_detection": new_note, "form": current_form, "sections": sorted(new_sections), "missing_core_sections": missing_core["current"]}}}


class AnalysisStore:
    """Atomic, private local records; analysis history never touches research DB."""
    def __init__(self, evidence_dir: Path):
        self.directory = Path(evidence_dir) / "document-analysis"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def save(self, kind: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        record = {"id": uuid.uuid4().hex, "kind": kind, "created_at": datetime.now(timezone.utc).isoformat(), **{key: value for key, value in request.items() if key not in {"text", "previous_text", "current_text"}}, "result": result, "inputs": {key: value for key, value in request.items() if key in {"text", "previous_text", "current_text"}}}
        if not record.get("title"):
            record["title"] = "Earnings transcript" if kind == "transcript" else "Filing narrative comparison"
        path = self.directory / (record["id"] + ".json")
        fd, temporary = tempfile.mkstemp(prefix=".analysis-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return record

    def get(self, identifier: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            return None
        try:
            return json.loads((self.directory / (identifier + ".json")).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def history(self, limit: int = 100, *, namespace: str | None = None) -> list[dict[str, Any]]:
        items = []
        paths = sorted(self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in paths:
            if len(items) >= limit:
                break
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if namespace is not None and record.get("namespace", "real") != namespace:
                    continue
                items.append({key: value for key, value in record.items() if key not in {"result", "inputs"}} | {"summary": record["result"]["summary"]})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(items, key=lambda item: item["created_at"], reverse=True)
