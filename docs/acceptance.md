# ResearchCouncil acceptance record

The sections below retain the history of earlier release checks. The latest
research-process upgrade is recorded first; older test counts refer to their
own checkpoints.

## ResearchCouncil public-source preparation · 13 September 2026

The public app is named ResearchCouncil. The interface uses one research
workspace, with no Demo tab and no automatic sample-data fallback when the
backend is unavailable. Existing storage identifiers remain compatible.

Saved account and holding details are hidden by default. An explicit local
reveal mounts those records; closing it removes their rendered contents.
Browser checks verified this behavior and confirmed that public market prices,
Watchlist conditions and all four saved cases remain readable. A fresh database
contains no real accounts, balances, positions or research runs. Display privacy
does not sanitize the private backend database.

The production frontend builds and all 307 backend tests pass. A separate clean
frontend checkout also passed a frozen dependency install and build with pnpm
11.19.0 installed into a temporary npm prefix under Node 24. The pinned package
manager requires Node 22.13 or newer; setup checks that minimum and avoids the
older bundled Corepack loader. The dependency lockfile remains unchanged.
Source packaging
excludes private environment files, databases, evidence, logs, backups, exports,
prototypes and the original private brief. The release gate checks publication
files and reachable publication history against values collected privately from
local account observations and credentials, reporting only paths and hit counts.
The public repository is prepared from an isolated source archive; the original
local Git object store is never copied into it. No hosted-site deployment is
configured. The local research workspace remains paused.

## Lean question journal · 13 September 2026

New cases use Chief of Staff, Researcher discovery/synthesis and CIO, with
local market calculations. The focused workflow checks cover one bounded
public evidence continuation in the same case, reuse of the pending CIO task,
targeted continuation prompts, candidate preservation, and source archival
after an auditable retry. Model prompts keep public collection separate from
private account context.

Decision checks cover immutable attempt inputs, versioned corrections,
instrument identity conflicts, source-bound OHLC prices, supported watch
conditions and deterministic account sizing. Original reports, evidence and
earlier decision revisions remain intact when a code correction creates a new
current view. Current blockers are displayed separately from historical
discovery requests.

All 301 backend tests pass, and the production frontend builds successfully.
The actual broad-question provider packet was also captured in a disposable
database without another model call: current macro sources were present in
the prompt, citation schema and frozen attempt, along with recent dated table
rows, instrument identity and four market frequencies. More than 100 explicit
source references no longer silently lose their newest additions.
Browser checks verified question
cards, return navigation, current Watchlist prices and catalyst dates, source
inspection, recorded objections, decision revisions and optional office
inspection. The disposable local launcher, frontend serving, backup, export,
restore and graceful shutdown check passed after the latest schema migration.
All four live cases completed through authenticated Codex CLI calls: one open
macro question and three retained Reddit posts. Chief of Staff and CIO outputs
used GPT-6 Astra Ultra; Researcher outputs used GPT-5.6 Luna Max. The acceptance
check verified completed tasks, current decisions, evaluated candidates,
observable Watchlist conditions, no extra case trees and at most one public
collection continuation per case. The outcomes were Watchlist judgments and an
instrument-identity decline; none justified an immediate entry or share count.
These live cases therefore exercise Watchlist and Decline, while deterministic
tests cover the supported-entry and approved-budget requirements for Recommend.

Source access remained a real constraint: some regulator and issuer downloads
were unavailable. Those gaps remain visible and unverified allegations were
not promoted to facts. The macro case successfully retained current Treasury
and Federal Reserve evidence after correcting source handoff and dated-table
excerpt issues. Reporting lags and conditional scenario results remain
explicit in the CIO report.

Independent Astra Ultra review accepted all four current investment judgments
against the archived evidence and frozen attempt inputs. One deferred planning
inconsistency remains visible: unapproved model prose proposed a funding account
that differs from the account selected by the saved long-term policy. No entry,
share quantity or transfer was issued. Account reconciliation remains required
before sizing; this prose does not override the saved account policy.

The local workspace was left paused with no running attempts. A watch check
while paused performed no work and dispatched no follow-up research. Private
evidence, portfolio records and test-case records remain excluded from the
source distribution.

## Research process, memory and Reddit upgrade · 12 September 2026

The current release passes 135 backend tests, TypeScript checks, the production
frontend build, and the disposable launcher/backup/export/restore smoke check.
Independent Astra Ultra review used temporary databases and test doubles to
verify repair completion through PM/CIO, source-version citations, pause and
cancellation, one active repair per root, two-round exhaustion, namespace
isolation, and concurrent Reddit reservations. Manual repair insertion and its
ledger now commit together. Plain missing-data reports also create owned gaps.

Cross-question memory retrieves bounded related sources and historical opinions.
Its focused checks cover current source heads, invalidation, real/demo isolation,
linked repair context and current-price refresh requirements. Effective sources
appear in both the model citation schema and the saved attempt snapshot.

A07 executes reproducible local price scenarios, with no model call for the
calculation. Market pipeline checks cover symbol/currency binding, completed
bars, weekly aggregation, unavailable history and replay. Browser checks used
clearly labelled samples in a private database to verify base/bear/bull charts,
terminal comparison, pending CIO state, expandable memory and source versions.
Historical question cards and their CIO summaries remain readable.

An actual authenticated Codex CLI Luna Max run validated the expanded structured
output contract after code-owned scenario objects were removed from the model's
writable schema. This used the ChatGPT subscription route, with no GPT API key.
Fresh installs default A00/A10/A11 to Astra Ultra and specialist work to Luna Max;
the existing local A00 policy was updated accordingly.

Production checks preserve all original portfolio rows, 57 outputs, 277 claims,
60 attempts and nine source records. No sample questions or market observations
were imported into the production archive. Compatibility migration 012 handles
both earlier Reddit settings-table shapes; injected failure after table deletion
rolled back fully, and repeat startup retained the saved revision. Tests now put
the module-level default application in temporary storage before importing it.

Reddit settings are saved for r/wallstreetbets, the past seven days followed by
new posts. No Alpaca or PRAW credentials were found in the experiment folder or
checked private configuration locations. Live provider reads and complete Reddit
history coverage therefore remain unverified; the interface reports missing
credentials and unknown coverage. Listing limits or deleted posts may still
prevent complete history once credentials are supplied. General fundamental
valuation formulas remain outside the numeric price gate described in
[the workflow guide](research-workflow.md).

Private verification records live under `runtime/qa/research-process/` and are
excluded from the shareable source archive. The application stays local on
port 8000; temporary browser-test servers were stopped. The pre-existing large
frontend bundle and upstream Starlette deprecation warnings remain informational.

## Earlier question journal release

Updated 12 September 2026 from the private, gitignored live-run, decision,
semantic-repeat, and observed-check records plus the current local test and
browser runs. This is a scoped release handoff. The current deterministic
suite, corrected browser checks, and both corrected sale and IPO PM/CIO paths
pass with their evidence limits recorded below.

The observed checks include a target-Mac install and build, launcher shutdown,
backend and browser checks, a source-backed Codex run, deterministic fixtures,
simulation replay, and an integrated backup/restore. Prior live observations
and the remaining evidence limits are called out below the table.

## Question journal and office update

The question-flow update groups repeated runs by workspace and normalized
original question while retaining each verbatim request. The run API returns
the complete saved task flow, dependencies, attempts, reports, review records,
and operational events. Report execution, evidence status, and investment
decision are separate fields. Historical claims receive archive-check metadata
without rewriting their original output payloads or hashes.

The copied-history API check found six runs grouped into two questions. The two
latest completed research runs retained their full 10-task and 16-task flows,
14 and 24 dependency edges, and the correct main CIO reports. Their CIO claims
exposed validation reasons and cited excerpts. Migration checks preserved all
48 original outputs, 192 claims, 51 attempts, the original source records, and
all portfolio and ledger rows.

A live saved-result explanation was tested through the authenticated Codex CLI
using Astra Ultra in a private copy of the history. It completed in 139 seconds
with one new A00 task and output, no new sources, and no change to the main CIO
answer, evidence status, or decision. Repeating the same request reused the
same task. The production journal was not changed by this check.

Browser checks on the copied history verified all twelve desk selections,
selected-question role assignments, report links, and focused inspectors.
The PM, CIO and Fundamental Analyst were also selected in the optional 3D
scene. The journal showed the IPO revisions in execution order, the actual
PM questions and analyst responses, and both the failed attempt and its
successful retry. A cited source opened with the selected retained lines
highlighted. Coverage showed eight tickers and thirteen named coverage gaps;
the source filter returned six archived sources immediately. Simulation and
monitoring views correctly reported their empty state. No research, portfolio,
simulation, or monitoring mutation was submitted in these browser checks.

The disposable launcher, frontend, backup, namespace export, restore and
graceful shutdown smoke check passed for this update. Production migration
checks preserved all original portfolio, evidence, report and attempt rows.

Final verification: 69 backend tests passed, TypeScript and the production
frontend build passed, and fresh production Luna Max and Astra Ultra Codex
preflights both executed and validated. The running production browser showed
the correct original-question cards, current answers and older cancelled
history; current attention counts excluded closed parent runs. Real/Demo
switching cleared scoped state, follow-up drafts survived ordinary refresh,
and office refresh retained the selected question. Source choices displayed
readable titles. The older CIO review linked to the PM report available at
its start with an explicit historical-inference label; new reviews persist the
exact supplied report. Astra Ultra's final acceptance review found no remaining
release blockers in these patches.

A final laptop-width visual check also removed the empty inspector column
from journal and other pages without an inspector. The rebuilt production
journal used the available width in the 1265-pixel browser viewport.

The final source ZIP contains 84 files, including 11 executable launch/helper
files, with no private-data scan hits. Its final inventory and hash are recorded
privately in `runtime/qa/question-flow/final-package-check.json`. The backend
remains local on port 8000; the temporary QA servers were stopped.

These checks are recorded privately under `runtime/qa/question-flow/`, which
is excluded from source packages. The broader prior release checkpoints below
remain historical observations rather than a substitute for this update's
verification.

## Packaging and local operations

The packaging work provides:

- `install.command` and `scripts/install.sh` for Python/Node setup and the
  production frontend build.
- `start.command` and `scripts/start.sh` for FastAPI on
  `http://127.0.0.1:8000`, with a loopback health check and PID marker.
- `scripts/backup.sh`, `scripts/restore.sh`, and `scripts/export.sh` backed by
  `scripts/road2m_data.py`.
- `scripts/package-source.sh` for a source-only ZIP that excludes local state,
  private data, credentials, generated output, and the original private brief
  while preserving executable launch and helper scripts.
- `scripts/smoke.sh` for a disposable launcher, frontend, backup, export, and
  restore check.
- A disabled Pages workflow and a CI workflow that validates/builds source
  without provider credentials or publishing.
- Git-ignored private data, runtime, build, prototype, and original-brief
  paths; sanitized public product brief at `docs/PRD-public.html`.

Restore is a stopped-service maintenance operation. The live application API
returns `409` for restore requests and directs the operator to stop the server
and use the local script.

## Verification commands

Run these from the repository root after installation:

```sh
./scripts/install.sh
./scripts/start.sh
curl --fail http://127.0.0.1:8000/api/health
./scripts/smoke.sh
./scripts/backup.sh
./scripts/export.sh --namespace demo --format json
./scripts/package-source.sh /tmp/ResearchCouncil-source-check.zip
./.venv/bin/pytest backend/tests -q
pnpm --dir frontend build
```

The source-package command writes a ZIP with paths rooted under `ResearchCouncil/` and
excludes `data/`, `runtime/`, `exp/`, `.venv/`, dependency and build
directories, caches, logs, SQLite files, local environment files, private
data overrides, and the original root `PRD.html`. It retains executable mode
bits for `install.command`, `start.command`, and shell helpers. The final
source-package checkpoint records an 84-file archive with 11 executable files
and no private-scan hits. The authorized replacement of
`runtime/release/ResearchCouncil-source.zip` uses:

```sh
./scripts/package-source.sh runtime/release/ResearchCouncil-source.zip --replace-release
```

The detailed private archive scan is recorded in
`runtime/qa/question-flow/final-package-check.json`.

Stop the server before testing restore:

```sh
./scripts/restore.sh data/backups/road2m-backup-YYYYMMDDTHHMMSSZ.tar.gz --confirm-stopped
```

The target-Mac checks used the bundled Node/Python runtimes and the locked
pnpm toolchain, built the frontend, started the app on a loopback port, got an
HTML 200 response, and verified graceful SIGTERM cleanup. The disposable
launcher smoke also passed. An integrated backup and restore into a separate
QA directory retained the SQLite records, evidence references, research
outputs, decisions, simulations, and event history with integrity checks
passing.

The prior normal production launch on port 8000 returned HTML 200 and a
healthy API response. Foreign host/path requests were rejected, the live
restore endpoint returned the documented stopped-service 409, and both Luna
and Astra production preflights executed successfully and validated. These
observations remain recorded as a prior live checkpoint; the corrected final
sale and IPO runs are documented below.

The prior startup repro also passed: an immediate reload followed by opening
Portfolio showed the persisted currency-formatted observations and position
quantity directly, with no blank first snapshot. Navigating away and back
continued to show the same formatted records.

A fresh 71-file public source copy was installed from scratch with Python 3.12,
Node 24, and pnpm 11.19. Its frozen dependency install, production build, and
copied disposable smoke check passed. The original brief, old prototypes,
private runtime/data, generated build caches, and dependency directories were
excluded from that copy.

The prior source-backed Codex check completed six roles using the authenticated
ChatGPT subscription route: A00/A01/A02/A03 ran with the configured Luna
model, A10/A11 ran with the configured Astra model, and every output cited the
same historical public evidence; publication/observation dates unknown.
PM and CIO both deferred because current market, valuation, and portfolio
inputs were missing and strict validation prevented an allocation proposal.
The real transaction ledger remained unchanged. Repeating the same question
was semantically reused as the exact prior run without a new attempt. Current
rules-only and assisted simulations replayed exactly; the assisted prompt used
one provider call.

The prior backend checkpoint had 57 passing tests. Its frontend build
passed. That source-package check recorded 77 files, 11 executable
files, and no private-scan hits in
`runtime/qa/question-routing/source-package-check.json`. The current browser
checkpoint verified the actual sale question's A01 candidate cards, including
archive-available and archive-unavailable states, and opened the local UNH
source button to retained content with its hash and timestamp. Two corrected
runs also completed clean pause and controlled restart/resume checks while
preserving seven stored output hashes in
`runtime/qa/question-routing/post-resume-output-hashes.json`.

The current running backend passed both the Luna Max and Astra Ultra
preflights. Exact per-attempt citation enums blocked a reproduced source-ID
typo; the failed attempt and raw provider result remained preserved, and the
same task was retried as a new attempt. The latest immutable checkpoint
preserved 15 prior output rows.

The sale final acceptance record in
`runtime/qa/question-routing/sale-final-acceptance.json` completed 10/10
tasks. All five candidates were retained through CIO, 8/8 CIO claims were
validated, PM requested zero revisions, unsupported numeric prices remained
null, and the final disposition was defer with no allocation. The citation
retry acceptance record in `runtime/qa/question-routing/citation-retry-acceptance.json`
preserved the original 20 outputs and unchanged portfolio and ledger, retained
the failed typo attempt and raw result, and recorded a distinct successful
retry with 7/7 valid facts. The final browser display check in
`runtime/qa/question-routing/final-browser-display-check.json` confirmed
queued A11 Current task and Model tabs show Awaiting dispatch, running A09
shows the actual Astra Ultra attribution, and the sale CIO report and local
UNH source viewer no longer have office labels overlapping them. It also
confirmed three IPO CIO candidate cards, actual Astra Ultra attribution, no
office-label overlap in the IPO report, and citation access to the local issuer
source.

The IPO final acceptance record in
`runtime/qa/question-routing/ipo-final-acceptance.json` completed 16/16
tasks and outputs with three retained candidates. The follow-up PM validated
9/9 claims, the CIO validated 5/5 claims, and there were zero second-round
revision requests or tasks; the preserved first revision round remains part
of the record. All actual outputs in both corrected runs used Codex GPT-6
Astra Ultra. Both final CIO decisions deferred because current price,
valuation, exposure, and risk inputs were missing; neither produced an
allocation, unsupported dollar entry, or sizing. Candidate lists remain
visible with their evidence availability states.

The final preservation check in
`runtime/qa/question-routing/final-preservation-check.json` found four
unchanged account rows, four unchanged balance observations, one unchanged
position, zero transactions, and all 20 original outputs unchanged. The six
queued descendants of the two superseded QA runs were cancelled through the
existing API controls; their saved outputs remain preserved and the original
user runs were left untouched. The cleanup is recorded in
`runtime/qa/question-routing/superseded-qa-descendant-cancellation.json`.

The prior browser run observed
the unselected-desk SSE update in 3.3 seconds, all twelve mouse selections in
addition to the prior twelve keyboard selections, the accessible list
fallback, the reduced-motion toggle, and PM/CIO selection in the 3D view.

## Acceptance status

| ID | Acceptance scenario | Status | Observed evidence or remaining check |
| --- | --- | --- | --- |
| AC-FINAL | Final live sale and IPO PM/CIO acceptance | Observed pass, scoped final acceptance | Astra’s final acceptance review recorded no blockers. The sale run completed 10/10 tasks with five candidates and 8/8 CIO claims; the IPO run completed 16/16 tasks with three candidates, 9/9 follow-up PM claims, and 5/5 CIO claims. Both final CIO decisions deferred with no allocation because required price, valuation, exposure, and risk inputs remained unavailable. |
| AC-01 | Launch on the target Mac | Observed pass | Install/build, loopback launch, health response, shutdown cleanup, disposable smoke, prior normal port-8000 launch, and the immediate reload repro passed. |
| AC-02 | Inspect the default office | Observed pass, prior checkpoint | Prior browser QA observed the office, separate PM/CIO rooms, role inspectors, currency-formatted private observations after startup, and explicit unknown fields. |
| AC-03 | Select each role by mouse and keyboard | Observed pass, prior checkpoint | All twelve mouse selections and the prior twelve keyboard selections showed the correct inspector; PM and CIO were also selected in the 3D view. The accessible list fallback exposed every record. |
| AC-04 | Submit a real research task through GPT | Observed pass, final corrected runs | The sale question completed 10/10 tasks and the IPO question completed 16/16 tasks. Candidate cards retained five sale candidates and three IPO candidates, with source provenance and evidence availability visible. All actual outputs used Astra Ultra. |
| AC-05 | Change global default while a task runs | Observed pass, fixture | Independent backend test froze the held attempt and dispatched a later task under the changed policy. |
| AC-06 | Apply an agent and one-run override | Observed pass, fixture | Independent backend test verified role and task precedence plus resolved settings. |
| AC-07 | Select a second available model/provider | Observed pass for Codex models, final checkpoint | Luna Max and Astra Ultra preflights both passed; the corrected sale and IPO runs used actual Codex GPT-6 Astra Ultra outputs, and the browser display check showed the running A09 attribution. Ollama remains optional and unconfigured. |
| AC-08 | Exhaust quota or remove provider availability | Observed pass, fixture | Auth and quota blocker fixtures produced blocked work without output, execution, or paid fallback; live quota exhaustion was not performed. |
| AC-09 | Import a filing with ambiguous share-count fields | Observed pass, fixture | Source locators and units were retained; weighted-average shares could not satisfy the current-outstanding calculation gate. |
| AC-10 | Run a thesis through PM and CIO | Observed pass, scoped evidence limits | The sale PM requested zero revisions; its CIO validated 8/8 claims and deferred with no allocation. The IPO follow-up PM validated 9/9 claims with zero second-round requests/tasks; its CIO validated 5/5 claims and also deferred with no allocation. Unsupported entry prices, dollar values, and sizing remained absent or null. |
| AC-11 | Repeat an unchanged question, then amend evidence | Observed pass | The same question semantically reused the exact completed run without creating a new attempt; amendment tests preserve historical output and create a dependent refresh with the new source head. Exact per-attempt citation enums blocked a reproduced source-ID typo; the failed attempt and raw result remained preserved, the original 20 outputs and portfolio/ledger stayed unchanged, and a distinct retry recorded 7/7 valid facts. |
| AC-12 | Restart during work and reconnect the browser | Observed pass, current checkpoint | Two corrected runs completed clean pause and controlled restart/resume checks while preserving seven stored output hashes; the prior A03 interruption/retry and unselected-desk SSE evidence remain recorded. |
| AC-13 | Reimport the same transaction file | Observed pass | Repeated transaction import retained one transaction and no cross-namespace transaction. |
| AC-14 | Cancel a task and receive a late result | Observed pass, fixture | Late cancellation discarded the result, retained the terminal task state, and emitted the cancellation event. |
| AC-15 | Run and replay a scenario | Observed pass | Current rules-only and assisted prompt-v2 simulations, including the one-call assisted run, replayed exactly with matching results. |
| AC-16 | Remove optional market/option data | Observed unavailable state | Optional quotes, options, and SEC retrieval are unconfigured; the remaining dependent-claim journey is not established by live data. |
| AC-17 | Open demo and real work | Observed pass, scoped prior checkpoint | Demo and real namespaces remained visibly separated through the prior browser workflow and namespace guard checks; demo records contain no personal account values. |
| AC-18 | Inspect browser responses, logs, and repository | Observed pass, final package and display checks | The final 77-file package check recorded 11 executable files and no private-scan hits, including no private paths, runtime/data trees, dependency/build directories, original brief, machine paths, exact private values, or credential-pattern matches. The final browser display check found no office-label overlap in the sale or IPO CIO reports or local source viewers. |
| AC-19 | Backup, restore, and export | Observed pass | Settings backup UI was browser-verified; integrated archive/restore retained records, evidence, outputs, decisions, simulations, and event history. Restore requires the stopped service. |
| AC-20 | Use reduced motion and WebGL fallback | Observed pass for reduced-motion and accessible List view; WebGL/performance untested | Reduced-motion preference and the accessible List view for all records passed; forced WebGL-disabled browser behavior and target FPS/performance benchmarking were not measured. |

## Remaining untested or unavailable scope

The prior Astra, parser, frontend, semantic-reuse, namespace, simulation,
monitoring, and browser integration review items have recorded evidence above.
The current deterministic suite, frontend build, candidate cards, source
viewer, corrected pause/restart/resume checks, and both sale and IPO PM/CIO
acceptance paths pass with explicit evidence limits. Current price, valuation,
portfolio exposure, candidate-specific filings, and other optional inputs
remain unavailable where recorded, so both final CIO decisions defer with no
allocation. The requested 30–50-case benchmark and target performance
measurement were not performed. The final source archive was checked and the
release ZIP was regenerated from the source-only packaging command.

Ollama, live quote/option feeds, and SEC contact are unavailable or
unconfigured in this environment. They must remain visible as unavailable and
must not be represented as successful research inputs.

No performance result should be inferred from the launcher smoke or the short
deterministic fixtures.
