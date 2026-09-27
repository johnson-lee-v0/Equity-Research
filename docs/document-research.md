# Document research

Documents opens with the [ticker-driven earnings workflow](earnings-workflows.md),
which discovers and archives the call and matching filings. The **Manual documents**
tab also offers local earnings transcript analysis and filing narrative comparison.
Paste text or import `.txt`, `.md`,
`.html`, `.htm`, or a PDF with selectable text. Files stay on this machine; these
analyses need no model downloads, API key, paid service, or network request.
PDF extraction uses the existing bounded local PDF worker. Scanned PDFs need
text/OCR before import; unreadable pages are reported rather than fabricated.

## Earnings calls

The English-language analyzer assigns lexical sentiment to each sentence,
handles short negation scopes, and groups results by labeled speaker. CEO/CFO
and other management titles, analyst labels, operator labels, and Q&A headings
are recognized. Operator remarks do not affect the overall score. Labeled
speakers retain their roles when their names reappear without titles.

Results include the full sentence text, speaker, section, matched sentiment
terms, candidate named entities, and recurring business themes. Each entity and
theme links to sentence evidence. A score between -1 and 1 expresses the balance
of matched positive and negative terms; it is not a probability or forecast.
Capitalization rules propose entities and can miss or mislabel names. Ambiguous
speaker labels remain unknown. Sarcasm, context and nuanced language require
review of the original evidence.

The reader keeps business themes in a persistent selector and paginates discussions.
Negative language separates management wording from analyst questions; opening a
discussion retains all management answers regardless of their sentiment. The full
transcript includes searchable speaker turns, exact citation jumps, return navigation
and a TXT download. Existing records gain `reading_context` on read without rewriting
the original analysis; records without original text are labeled as retained passages.

## 10-Q comparison

Supply the previous quarter on the left and current quarter on the right.
The comparison recognizes SEC Part I / Part II and Item headings. It excludes
Part I Item 1 financial statements, numeric table rows, page markers, headings,
and numeric/date-only changes. It keeps qualitative commentary in MD&A,
liquidity, controls, legal proceedings and risk factors, including changes in
negation, outlook and risk language. For example, changing revenue from `$5m`
to `$6m` is ignored; changing “no material weakness” to “a material weakness” is
shown. Product and regulatory identifiers such as `GPT-4` and `Section 301`
remain meaningful text.

Repeated and reordered sentences inside the same SEC item are matched before
other passages are aligned by text similarity. Every detected addition,
removal and change shows its complete before/after wording and section. A risk
or outlook flag is a reading aid, not a materiality conclusion. Section moves
can appear as an addition and a removal. HTML layout tables can contain prose,
so they are not blindly discarded. Exclusion counts and section-detection
notes make the filtering visible. Narrative excerpts work without SEC headings;
for full filings, check the section notes and original inputs when formatting
is unusual. This manual tab uses supplied documents; Latest earnings handles automatic
collection and selects the appropriate 10-Q or 10-K.

## Persistence and API

Records are saved atomically as private JSON files under
`<configured evidence_dir>/document-analysis/`; this location is included in
the existing evidence backup/restore workflow. Files contain original inputs,
metadata and complete analysis results. History lists omit full input text;
opening a record restores its result and source inputs. New records do not
modify the existing research database or prior analysis records.

All routes use the existing local-only API boundary. The three POST routes use
the same `X-Road2M-Client` mutation guard as the rest of the workspace.

- `POST /api/document-analysis/import`: `{filename, content_base64}` returns
  `{filename, format, text, warnings}`. Input limit: 5 MB; extracted text limit:
  1,000,000 characters. PDF limits also apply in the shared extraction worker.
- `POST /api/document-analysis/transcript`: `{title?, ticker?, period?, text}`.
- `POST /api/document-analysis/compare`: `{title?, ticker?, previous_period?,
  current_period?, previous_text, current_text}`.
- `GET /api/document-analysis/history?limit=100`: `{items: [...]}` (maximum
  requested limit 1000).
- `GET /api/document-analysis/history/{id}`: complete saved record, including
  `inputs` and `result`.
- `GET /api/document-analysis/history/{id}/transcript.txt`: saved transcript as a
  UTF-8 attachment; an incomplete legacy fallback is explicitly labeled in its
  filename and content.

Analysis responses contain `id`, `kind`, `created_at`, the supplied metadata,
`inputs`, and `result`. Both result types disclose `method`, `limitations` and a
`summary`. Transcript results expose `sentiment`, `speakers`, `entities`,
`themes`, all `sentences`, and `reading_context` with original text, speaker turns
and question/answer exchanges. Comparison results expose `counts`, `changes`,
`exclusions` and source hashes in `documents`. Empty or unreadable inputs fail
explicitly rather than generating an empty successful analysis.

Verification:

```sh
.venv/bin/python -m pytest backend/tests/test_document_intelligence.py
```
