# Unified local research workspace

The application entry point is `frontend/index.html`, built into
`frontend/dist/index.html` and served by `./start.command` at
`http://127.0.0.1:8000`. The current investment workflow and the retained lab
engines share this application and backend. There are no embedded legacy sites
or additional application servers to start.

The September 26 update consolidates the main navigation into Research,
Watchlist, Portfolio, Congress and Strategy testing. See
[Research experience](research-experience.md) for the current case layout,
plain-language call reading, optional reviews and comparable valuation.

## Reviewed local interfaces

| Interface | Role | Consolidated location |
| --- | --- | --- |
| `frontend/index.html` and `frontend/src/` | Current investment process, saved cases, evidence, portfolio, watches, Reddit, simulations, office and settings | Existing workflows retained in the unified navigation |
| `exp/index.html`, `desk.js`, `road2m_store.py` | Original investment findings and durable revision history | Library; original database copied consistently into the private evidence archive |
| `exp/runtime/pre-rebuild/index.html`, `exp/app.js` | Superseded original research dashboard and historical seed | Reviewed as migration context; no runtime dependency |
| `exp/congress-lab/app/page.tsx` and its component views | Congress disclosures, reported positions, custom strategy simulator and saved research studies | Congress and Strategies; minimal Python replay and validation modules extracted into `backend/app/research/lab_engine/` |
| `PRD.html`, `docs/PRD-public.html` | Private and sanitized product briefs | Documentation, not competing application entry points |

Other HTML under archived research, evidence and runtime directories consists
of downloaded sources, generated builds, dependency documentation and previous
QA snapshots. These are not separate active products. Original prototypes are
retained as local migration archives; the application build does not import
their JavaScript, UI component libraries or servers. The existing disclosure
collector's status is read separately because its cadence is externally owned.

## Retained core behavior

The investment process continues to use its existing provider routing, source
archive, five-question decisions, portfolio constraints, watch conditions,
Reddit intake, settings and persisted case history. Archived desk findings are
explicitly historical and can start a new review without rewriting the old
conclusion or replaying old queued worker jobs.

Congress uses the existing normalized disclosure archive, preserving unknown
owners, uncertain identities, excluded instruments and source locators. Saved
disclosure ranges do not become invented share counts or current holdings.
Strategy testing retains shared cash, fixed or historical Kelly sizing where
supported, disclosure delays, repeat purchases, conditional entry/exit rules,
the 20-position rotation, price validation, benchmark curves, fees, exclusions,
orders and saved research. Conditional experiments retain their timing caveats.

The active lab code is the replay/calculation dependency closure and indexed
data access. Batch exporters, duplicate web servers, cloud deployment machinery,
and the old site's unused UI component collection are not runtime dependencies.
Heavy office rendering is loaded on demand instead of with the initial research
page. New features have small route/service/panel boundaries and share the
existing local request guard and frontend request utility.

## Document research

Transcript analysis identifies speaker turns, sentiment cues, named entities
and business themes with sentence evidence. It is a deterministic English NLP
heuristic, not a validated prediction of investment returns or a claim that a
language model read the document. Negation and speaker distinctions are handled
where recognizable; unusual formatting and ambiguous names need review.

Filing comparison matches narrative within SEC item sections, omits financial
statement sections and numeric table rows, and normalizes financial amounts and
reporting dates for comparison. Original before/after wording remains available.
Qualitative changes to financial commentary, risk, outlook and controls are
retained. The section distinction follows the
[SEC Form 10-Q structure](https://www.sec.gov/files/form10-q.pdf).
Missing item headings are reported as an excerpt comparison rather than a
full-filing extraction. Similarity matches are suggestions, not judgments of
legal or accounting materiality.

Original document inputs, hashes, method labels and computed results are saved
locally; analyses can be reopened or exported. The deterministic analysis does
not require an external model. The optional plain-language reading aid uses
the configured local Codex provider and retains source-bound quotes separately.
Text, HTML and readable PDF import are supported;
scanned or encrypted PDFs fail explicitly if readable text is unavailable.

## Private data and launch

`scripts/import-labs.py` imports allowlisted disclosure records, saved studies,
audited price caches and policy evidence into `data/evidence/labs/`. Lossless
compressed records are indexed in SQLite; retained price-source checksums still
cover their original bytes. A completed import is reused on later invocations.

`scripts/import-research-library.py DATABASE` snapshots the original desk
database into `data/evidence/research-library/`. The source database is opened
read-only; no old worker runs or pending tasks are transferred to the current
investment queue. Run this command again to refresh the retained snapshot.

Document analyses live in `data/evidence/document-analysis/`; new lab test
results live in `data/evidence/labs/backtests/`. Full backup and restore already
cover the evidence tree. The database-only snapshot in Settings is still a
database-only snapshot; use `scripts/backup.sh` for all research artifacts.

To install and build use `./install.command`, then `./start.command`. The
optional Laya installation and Codex sign-in apply to new model-driven
investment cases; saved research, document NLP and historical strategy tests
remain available without them.

## Verification (September 25, 2026)

- Full backend suite: **632 passed**. TypeScript checks and the production Vite
  build passed. The large office renderer remains a separate, lazy-loaded bundle.
- Browser checks covered all unified navigation destinations, document file
  import, saved analysis restoration, source evidence, disclosure filters,
  pagination, historical revisions, strategy execution and run comparison.
  Desktop sidebar scrolling and narrow-screen navigation were checked; the
  document page had no horizontal page overflow at a 375px content width.
- The migrated archive contains **96,465 disclosures**, **344 profiles** and
  **7 studies**. All 29 original desk findings, 34 packet revisions and 141
  history events were retained; table-level logical hashes matched the source.
- Real House and Senate strategy replays matched the original engine's
  accounting and ledgers. The broad replay covered all 344 profiles. Explicit
  Senate timestamp assumptions are preserved and exposed in result diagnostics.
- A real 2.18MB 10-Q had zero self-comparison changes; an edited financial
  amount was ignored while an edited internal-control negation was retained.
  Synthetic examples also verified sentiment negation and speaker evidence.
- Mutation guards, persistence across application restart, import integrity
  and preservation of missing/unknown disclosure owners have regression tests.

Browser-generated examples and test runs use a separate QA database. The main
workspace retains its existing paused state and contains no synthetic analyses.
Pre-migration and post-import full backups remain under `data/backups/`.
