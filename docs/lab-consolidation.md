# Congress and strategy research consolidation

The primary FastAPI service now owns congressional disclosures, reported-position reconstruction, historical research, and custom strategy replay. The main research application consumes `/api/labs`; the Congress Lab Vite server and `positions_api.py` are no longer required to use these workflows.

## Reviewed applications and retained workflows

The review covered `exp/congress-lab/app/page.tsx`, its active Research, Simulator and Data & methods components, the local positions API, the ledger and source-evidence views, and the Python engines those views call. The old `exp/index.html` is the completed investment research desk; its archival migration is handled by the unified research library. PRD HTML is documentation, and HTML in downloaded evidence, dependency installations, runtime snapshots and browser extraction caches is data rather than an additional application.

The unified app retains:

- Official House and Senate disclosure records with search, chamber/member/owner/action/date filters, bounded pages, original row evidence and CSV export.
- Conservative reported-position reconstruction, including unknown owner/account identities, partial sales, source corrections and availability limits. Disclosed dollar ranges are not converted into actual share counts or portfolio weights.
- A shared cash simulator: twenty-security rotation, next-session high entry, legacy delayed-open replay, fixed 1%/2%/5% sizing, prior-outcome quarter Kelly where supported, repeat purchases, sale/hold rules, fees, audited prices, cash diagnostics, source exclusions, source-lot attribution and persistent result history.
- Earlier filing-day and below-trade-low experiments, explicitly labeled conditional. Later-sale hindsight exclusions are optional and default off. A date-only sale must be filed before the entry date to veto that opening purchase. Actual publication timestamps supersede earlier filing dates when available.
- Seven saved research snapshots: overnight portfolios, overnight capacity, cash portfolios, rotation, the 72-rule search and its follow-up, and matched-stock research. Their original populations, assumptions, period comparisons, uncertainty and provenance are preserved. A normalized view supplies charts and comparison rows without recalculating saved results.
- Exact daily overnight execution details from the original immutable SQLite ledger, checked against the research snapshot checksum.
- Read-only status of the existing disclosure collector. The unified service starts no second collector, does not change the established external schedule and sends no messages or trades.

## Active implementation

`backend/app/research/lab_engine/` contains only the retained replay, price-validation, reported-position, CSV and daily-ledger code. Batch exporters, downloading, OCR, packaging, UI-framework scaffolding and duplicate servers are not imported. Active Python imports do not depend on the ignored prototype tree.

`lab_data.py` imports original normalized records into an indexed SQLite store. Original row payloads and compact API/engine projections use lossless compression. Price caches are gzip-compressed without changing their decompressed bytes; raw-response checksums, source symbol/currency checks, reviewed corporate-action boundaries and primary-source hashes remain mandatory. Research snapshots remain unchanged on disk.

Run the one-time import from the repository root:

```sh
.venv/bin/python scripts/import-labs.py
```

The default destination is `<evidence_dir>/labs/congress`; durable replay results are under `<evidence_dir>/labs/backtests`. Both are included in the existing evidence backup tree. Import uses a staging directory and atomic rename, refuses to replace a different populated target, and leaves the original archive untouched. Repeated import calls return the installed manifest. Use `--refresh` to verify and install an updated generation; the prior generation is retained under `labs/congress-revisions`. The UI uses guarded `POST /api/labs/import` with a fixed server-side source path for the same operation. Source checksum stability, generation counts and SQLite integrity are checked before replacement; a failed import leaves the previous dataset usable. A fresh machine needs the private evidence backup or the original data archive; code-only clones correctly show data unavailable.

The initial local migration preserved 96,465 disclosures, 344 source profiles, seven research snapshots, 5,152 source-bound price-cache files and the 128 MB overnight trade ledger. Only the source metadata needed by active views is retained. The complete extraction/research archive remains at its original location for historical reproducibility.

## API contract

- `POST /api/labs/import`: `{refresh: false}` for first import or `{refresh: true}` for a refreshed local generation. No caller-supplied filesystem paths.
- `GET /api/labs/catalog`: availability, dataset dates and coverage, politicians, strategy definitions, saved studies, collector status.
- `GET /api/labs/congress/records`: compact paginated records. Filters: `chamber`, `politician` (comma-separated IDs or `all`), `owner`, `action`, `date`, `search`, `eligible`, `offset`, `limit`.
- `GET /api/labs/congress/records/{id}`: complete original row and publication-availability evidence.
- `GET /api/labs/congress/export.csv`: same filters, all matching rows; spreadsheet formula prefixes are escaped.
- `GET /api/labs/congress/positions`: same filters plus status, with conservative reconstruction notes and counts.
- `GET /api/labs/congress/watch`: status only, no side effects.
- `GET /api/labs/research/{id}`: original research payload plus `view` with title, summary, cases, metrics, curves, diagnostics, notes and findings.
- `GET /api/labs/research-trades?case=...&date=...`: verified overnight day ledger.
- `POST /api/labs/backtests`: validated replay request, protected by the application's existing same-origin write dependency.
- `GET /api/labs/backtests` and `GET /api/labs/backtests/{id}`: persistent run summaries and complete results.

Backtests retain $100,000 starting capital and a 10% ticker purchase cap. `fee_bps` adds 0–100 basis points per side to every supported engine. The benchmark is SPY on the matching covered sessions. All runs show source exclusions, stale marks, missing symbols, retrospective assumptions and conditional/hindsight controls. This is a research simulator, not an order-execution system.

## Verification

`python -m pytest backend/tests/test_labs*.py -q` (71 checks, including 60 original pure engine regressions) exercises lossless import, filters/pagination, evidence IDs, CSV formula neutralization, write guards, future-disclosure/same-day availability, missing-price exclusion, fees in all three engines, rotation capacity, cash/P&L/fee reconciliation and prior-only Kelly training.

A real-data replay for Marjorie Taylor Greene, 2025-01-02 through 2026-09-04, fixed 2%, twenty-position rotation, next-low sales and 10 bps fees reproduced the original engine exactly for daily curves, orders, closed lots, holdings, fees, executed purchase count and headline metrics (268 purchase entries, ending value $98,588.3253). Results were written to a disposable results directory, and the comparison receipt is `tmp/labs-verification/selected-real-data-replay.json`. The source archive and previous research results were not changed.

A full all-profile default replay (2020-01-02 through 2026-09-04) also completed in 26 seconds: 12,697 executed purchases, maximum 20 holdings, no remaining pending requests, and both cash and aggregate lot P&L reconciled within $0.00004. Receipt: `tmp/labs-verification/all-profiles-replay.json`. The new importer derives the available horizon from the audited SPY calendar; future refreshed data can extend it without editing the engines. Saved requests omit options irrelevant to the chosen strategy, and include explicit executed-rule descriptions.

A John Boozman Senate next-session-high replay over 2025-01-02 through 2026-09-04 also reproduced the original engine exactly for metrics, curves, orders, closed lots, holdings, fees and executed count (158 entries; ending value $129,740.6358). Source filing timestamps with an explicitly recorded `America/New_York` assumption remain eligible; their assumptions are counted and disclosed, while naive verified-public timestamps remain invalid. The reviewed NVDA disclosure filed March 6 executes March 7. Receipt: `tmp/labs-verification/senate-real-data-replay.json`. Installed engine projections retain this timezone field; earlier imported projections recover it from the lossless original row.
