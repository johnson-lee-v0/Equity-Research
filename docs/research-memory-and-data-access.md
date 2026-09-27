# Research memory and SEC data access

Reviewed September 26, 2026. This describes application-owned research memory,
not the memory of the Codex conversation used to develop the application.

## Current memory

Each research generation starts a fresh, ephemeral Codex CLI execution. The
application constructs its context explicitly from retained records. It does
not rely on an agent remembering the previous CLI session.

| Layer | Retained material | Purpose |
| --- | --- | --- |
| Evidence archive | Original documents, source IDs, hashes, versions, dates and parsed observations | Check quotes and numbers against the actual source used |
| Research database | Runs, tasks, attempts, outputs, unresolved gaps and decision revisions | Resume work and inspect why a decision was reached |
| Task memory | Relevant source excerpts and historical conclusions selected for a particular task | Avoid starting every question with no prior context |
| Calculation and extraction records | Prepared financial inputs, historical series, calculator outputs and extraction caches | Reuse evidence and calculations with their provenance |

`Repository.prepare_task_memory` selects within the same namespace and ranks
linked cases, matching tickers and, when no ticker is present, overlapping
question terms. It scans at most 200 recent runs, retains at most 40 relevant
prior runs as candidates, and selects at most eight memory items with a
24,000-character excerpt budget. Individual output excerpts are limited to
6,000 characters. This is deterministic selection, not semantic-vector search.
General archive search uses SQLite FTS5 lexical search with a LIKE fallback.

Source freshness and supersession are checked. Previous model outputs are
labelled historical opinions; they are not upgraded into facts by reuse. Reused
company research requires a current-price refresh before relying on that price.
Financial calculation inputs are restricted to the current case's frozen source
packet rather than importing another case's financial assumptions as facts.

The excerpt limit is not a whole-prompt limit. Metadata, archived source text
and previous outputs can be included separately. NKE's measured final-review
memory block was 33,323 characters, inside a prompt exceeding 520,000 characters.
More storage alone would not address that duplication and selection problem.

## Obsidian's role

Obsidian stores Markdown notes in a local folder, so a future adapter can export
a readable company page, earnings notes, decision history and source links.
User-written thesis notes could be imported as explicitly attributed opinions.
They must not overwrite archived evidence, dates, verified facts or calculations.

The recommended authoritative store remains SQLite plus the evidence archive.
An Obsidian vault would be an optional reading/editing surface, not a replacement
for source-version checks, task scheduling or calculation validation. No vault,
plugin, synchronization or note import was created by this review.

Recommended retrieval improvements are compact per-company summaries, separate
fact/opinion/gap records, source-version-aware extraction caches, and one total
context budget across memory, evidence and prior outputs. Add semantic retrieval
only if measured keyword/ticker retrieval misses relevant passages. Retrieve the
complete relevant question-and-answer exchange when its qualifications matter.

## Reference patterns

- [AI Hedge Fund snapshots](https://github.com/virattt/ai-hedge-fund/blob/778e6bb3c6608ca2ee491383c9561af1909e3d95/hedge_fund/features/snapshot.py)
  and [response cache](https://github.com/virattt/ai-hedge-fund/blob/778e6bb3c6608ca2ee491383c9561af1909e3d95/hedge_fund/llm/cache.py)
  demonstrate reuse of compact, unchanged financial inputs. Our cache keys must
  also account for evidence versions, instructions, calculation rules and any
  changing price/event inputs that the particular task consumes.
- [Dexter evidence storage](https://github.com/virattt/dexter/blob/534f6be455523c5926eba72bdaf4f914555be5c9/src/utils/tool-result-storage.ts)
  keeps large results outside the main prompt and permits selective reads.
  Its [memory service](https://github.com/virattt/dexter/blob/534f6be455523c5926eba72bdaf4f914555be5c9/src/memory/index.ts)
  combines editable Markdown notes with SQLite keyword search and optional
  embeddings. Defaults include six retrieved results and a 2,000-token startup
  memory budget. Local embeddings or keyword-only retrieval are possible.
- [TradingAgents decision memory](https://github.com/TauricResearch/TradingAgents/blob/35543d0248bf89fcb92b17a15858ad0c0e940687/tradingagents/decision_log.py)
  reads a local Markdown log, selecting five settled same-ticker decisions and
  three cross-ticker lessons. Historical reads filter outcomes by when they
  became known. Its past lessons are context; they do not establish current
  prices or facts for another issuer. This current implementation differs from
  older versions using per-agent vector memory.
- [Obsidian's storage documentation](https://obsidian.md/help/data-storage)
  describes its local Markdown vault format and metadata cache.

These are verified implementation patterns, not evidence of a faster equivalent
earnings review or a reason to relax this application's source checks.

## SEC EDGAR and paid filing APIs

The app already reads SEC submissions and XBRL endpoints. The
[official read APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
do not require an API key. EDGAR Next's filer APIs are a different product for
submitting/managing filings, not what this research application needs.

A commercial adapter such as [sec-api.io](https://sec-api.io/docs) could provide
filing search, downloads, section extraction and XBRL conversion. It may improve
acquisition reliability and reduce custom parsing, but its documentation is not
a measured success guarantee for the inaccessible NKE documents. Original
accession, issuer, reporting/publication dates, source URL and archived content
must remain bound to every imported observation. It would not replace issuer
release or earnings-transcript collection, nor fix oversized model prompts and
repeated synthesis timeouts.

The local configuration review found the default SEC contact placeholder still
in use. Configure a real contact identity and follow SEC access guidance before
attributing every HTTP 403 to a need for a paid vendor. This is a configuration
finding, not proof of the cause of NKE's HTTP 403 responses. No contact value was
invented, subscription purchased or paid connector enabled.
