# Historical ResearchCouncil implementation contract

> **Archived design record — not the current implementation specification.**
> The dated text below is preserved for context, including its former model
> defaults, navigation, API examples and ownership rules. Do not use those
> historical requirements to override current code or operating guides.
>
> Current references: [research workflow](research-workflow.md),
> [task model routing](task-model-routing.md),
> [shared company memory](shared-company-memory.md),
> [setup](setup.md) and [domain glossary](../CONTEXT.md).
> Runtime contracts live in [schemas](../backend/app/schemas.py),
> [API routes](../backend/app/api/) and
> [navigation](../frontend/src/navigationModel.ts).

## Preserved September 2026 contract

Status: historical implementation contract, 12 September 2026, with the following approved replacement for new research cases on 13 September 2026.

The current workflow is defined in [the research guide](research-workflow.md).
New questions and selected Reddit posts use Chief of Staff (A00, Astra Ultra),
Researcher discovery and synthesis (A01/A03, Luna Max), code calculations, and
CIO (A11, Astra Ultra). One material public evidence gap can receive a bounded
continuation in the same case. New cases have no separate PM approval chain or
automatic specialist task tree. The question journal and its Recommend,
Watchlist, or Decline decisions are primary; office inspection is optional.
Four Codex calls may run concurrently, with at most three used by Reddit work.
These decisions supersede the earlier role, routing, UI and concurrency
requirements below. Historical role IDs and records remain readable.

The remaining sections preserve the earlier implementation choices and shared
interfaces. Record deviations and external limitations honestly.

## Delivery and ownership

- Deliver a React/TypeScript/Three.js browser application served locally by a Python FastAPI backend. SQLite and private files are the canonical local store. Do not publish or push. A local HTML interface with a running backend is approved.
- GPT execution uses the authenticated Codex CLI and existing ChatGPT subscription exclusively. No GPT API access, API SDK, API key route, or paid fallback. Ollama is an optional disconnected adapter; do not download models automatically.
- GPT-5.6 Luna Max agents implement source. GPT-6 Astra Ultra owns architectural choices and review. This build staffing rule is separate from configurable application runtime models. Initial runtime firm default is `codex / gpt-5.6-luna / max`, default profile inherits it, and explicit initial A10/A11 role overrides are `codex / gpt-6-astra / ultra`. These have passed the local schema probe described below; users can change supported runtime settings through the menu.
- Preserve `exp/` unchanged and exclude it, personal records, caches, generated outputs, and runtime state from the shareable source. Disable the existing Pages deployment workflow; do not publish an alternative. Do not put the user's balances into code, public docs, fixtures, tests, or generated bundles.
- Root owns launch/package integration, private seed creation, and end-to-end QA. Frontend owner modifies `frontend/` only. Backend owner modifies `backend/` and backend tests only. Request changes to shared contracts before deviating.

## Structure and runtime

```
frontend/                    React + TypeScript + Vite + R3F + Drei
backend/app/                 FastAPI/Pydantic, sqlite3, Decimal calculations
backend/tests/               meaningful workflow/invariant/provider tests
docs/                        architecture and public setup/acceptance records
scripts/                     install, start, export, backup/restore
data/                        private, gitignored, permissions restricted
data/portfolio-seed.json      optional private, user-reported opening observations
data/road2m.sqlite3           canonical database
data/evidence/               immutable content-addressed source files
data/workers/                constrained per-attempt Codex working directories
```

Python 3.11+ and Node 20+; pin compatible React/R3F versions. Backend listens on `127.0.0.1:8000`; Vite development port 5173 proxies `/api` to 8000. Production backend serves `frontend/dist/` and index fallback after API routes. Frontend always uses relative `/api` URLs. One documented start command after installation. No Docker required.

Use one persistent SQLite connection per operation, WAL, foreign keys, transactions, and a bounded background executor. Persist events before broadcasting/polling them. Workers continue if a panel closes. A restart marks orphaned running attempts interrupted and pending review/retry explicit. Global default limits: two hosted top-level tasks and one Ollama generation. No recursive whole-firm delegation.

## Shared conventions

- JSON uses snake_case. IDs are opaque strings. Dates use UTC ISO 8601 or null with a missing reason. Decimal financial values use strings; never floating-point ledger arithmetic. Percent rates are fraction strings, e.g. `"0.025"`.
- Namespace is `real | demo | simulation`. Every persistent research/evidence/task record carries namespace; APIs reject cross-namespace evidence use. The interface uses the real workspace; isolated demo records remain supported internally for tests and compatibility. Scenarios remain simulation records even when linked to real theses.
- Task status is `idle | queued | running | waiting_for_evidence | waiting_for_review | blocked | failed | cancelled | completed | interrupted`. Outputs separately report `completed | insufficient_evidence | needs_review`. Pause is a `paused` flag on firm/run/task; not a fabricated execution status.
- Envelope for an error: `{ "error": { "code": "...", "message": "...", "details": {} } }`. Expected unavailable/auth/quota/data conditions are visible states, not invented outputs. All mutation JSON requests include `X-Road2M-Client: local-ui`; backend validates Origin/Host, restricts CORS to configured loopback frontend origins, and rejects missing custom headers. No provider credential material enters responses/logs.
- List endpoints return `{ "items": [...] }`. API responses may add fields; frontend must tolerate additions. Poll snapshots after reconnect; SSE can be at-least-once and consumers deduplicate sequence IDs.

## Core frontend records

```ts
type Namespace = 'real' | 'demo' | 'simulation';
type Status = 'idle'|'queued'|'running'|'waiting_for_evidence'|'waiting_for_review'|'blocked'|'failed'|'cancelled'|'completed'|'interrupted';
type ModelConfig = { provider: 'codex'|'ollama'; model: string; reasoning_effort: string|null; profile: string };
type Agent = {
  id: string; name: string; title: string; mandate: string;
  location: string; status: Status; paused: boolean;
  current_task: Task|null; queued_count: number; output_count: number;
  model: ModelConfig; model_source: 'task_override'|'role_override'|'profile'|'firm_default';
};
type Task = {
  id: string; run_id: string; agent_id: string; namespace: Namespace;
  title: string; status: Status; paused: boolean;
  started_at: string|null; updated_at: string; completed_at: string|null;
  elapsed_seconds: number|null; blocking_reason: string|null;
  progress_message: string|null; input_refs: string[]; output_id: string|null;
  attempt_id: string|null; resolved_model: ModelConfig|null;
};
type Source = {
  id: string; namespace: Namespace; title: string; source_type: string;
  url: string|null; publication_at: string|null; observed_at: string|null;
  retrieved_at: string; content_hash: string; version: number;
  supersedes_id: string|null; content: string; coverage: string;
  freshness: string; error: string|null;
};
type Output = {
  id: string; task_id: string; agent_id: string; namespace: Namespace;
  version: number; status: 'completed'|'insufficient_evidence'|'needs_review';
  title: string; summary: string; analysis: string;
  fact_claims: {claim: string; value: string|null; unit: string|null; period: string|null; source_ref: string; locator: string}[];
  assumptions: string[]; calculations: {label: string; value: string|null; unit: string|null; formula: string; missing_reason: string|null}[];
  counterarguments: string[]; missing_data: string[];
  proposed_action: string; invalidation_conditions: string[]; next_review_at: string|null;
  source_refs: string[]; provider: string; model: string; reasoning_effort: string|null;
  prompt_version: string; schema_version: string; created_at: string;
};
```

Roles are fixed: A00 Chief of Staff, A01 Universe Manager, A02 Filing Reviewer, A03 Fundamental Analyst, A04 Technical Analyst, A05 Entry Analyst, A06 Holdings Monitor, A07 Simulation Analyst, A08 Ownership & Public Filings, A09 Macro Analyst, A10 Portfolio Manager, A11 Chief Investment Officer. Titles/mandates come from backend. Backend does not prescribe geometry coordinates; frontend maps stable IDs to locations.

## Shared API contract

### Office, agents, runs and events

- `GET /api/health` → `{status: 'ok', version, backend_time, database: 'ready', live_available: boolean}`.
- `GET /api/office?namespace=real` → `{namespace, agents: Agent[], paused: boolean, connection: {status: 'connected', last_event_at: string|null}, event_cursor: number, portfolio_summary: {accounts: number, positions: number, currencies: string[]}, provider_status: string}`.
- `GET /api/agents/{id}?namespace=real` → `{agent: Agent, queue: Task[], outputs: Output[], evidence: Source[], history: TaskEvent[]}`.
- `POST /api/runs` body `{question: string, namespace: 'real'|'demo', horizon: '1m'|'3m'|'event', ticker: string|null, source_ids: string[], model_override: ModelConfig|null, idempotency_key: string}` → HTTP 202 `{run_id: string, reused: boolean, tasks: Task[]}`. Repeated keys return original run. Cache reuse must visibly retain the original model/source versions and rationale. New source versions invalidate dependent records only.
- `GET /api/runs/{id}` → `{id, namespace, question, horizon, status: Status, paused, tasks: Task[], outputs: Output[], reuse_rationale: string|null, created_at, updated_at}`.
- `POST /api/runs/{id}/messages` body `{message, idempotency_key}` → `{event_id, run_id}`; auditable follow-up retaining completed work and account snapshot.
- `POST /api/control` body `{scope: 'firm'|'run'|'task', id: string|null, action: 'pause'|'resume'|'cancel'}` → `{ok: true, affected: number}`. Firm id is null. Pause stops new dispatch; in-flight work can finish with honest state. Cancellation records a request and prevents a late result from changing a final cancelled state.
- `POST /api/tasks/{id}/cancel` is an alias to task cancellation.
- `GET /api/events?namespace=real&after=0` is SSE, event name `task_event`, event ID is sequence number, data is a TaskEvent. Heartbeats are SSE comments; they are not work activity.
- `TaskEvent = {sequence_id: number, event_id, namespace, run_id: string|null, task_id: string|null, attempt_id: string|null, emitted_at, type: string, payload: {agent_id?: string, status?: Status, message?: string, output_id?: string, [key: string]: unknown}}`. Observable event types include queued, started, progress, evidence_added, output_validated, review_requested, blocked, failed, cancel_requested, cancelled, completed, interrupted, paused, resumed, instruction, settings_changed. Never expose provider reasoning/internal thought events; progress describes observable milestones.
- `GET /api/outputs/{id}` → `{output: Output, sources: Source[]}`.

### Models

- `GET /api/providers` → `{items: [{id: 'codex'|'ollama', name, available: boolean, status: string, reason: string|null, billing_route: 'chatgpt_subscription'|'local', models: [{id, name, available, reason: string|null, reasoning_efforts: string[], capabilities: {structured_output: boolean, streaming: boolean, cancellation: boolean, tools: boolean, images: boolean}}]}]}`. Unsupported or unvalidated models are disabled with reasons. No invented discovery endpoint.
- `GET /api/model-policy` → `{firm_default: ModelConfig, active_profile: string, profiles: {id, name, available: boolean, reason: string|null, config: ModelConfig}[], role_overrides: Record<string, ModelConfig>, history: {changed_at, scope, agent_id: string|null, previous: unknown, current: unknown}[]}`.
- `PUT /api/model-policy` body `{scope: 'firm'|'role'|'profile', agent_id: string|null, config: ModelConfig|null, profile: string|null}` → same shape as GET. Null role config clears override. Unsupported settings reject explicitly. Precedence: task/run override → role override → active profile → firm default. Default profile must inherit the firm default unless deliberately configured; a stale profile must not silently hide a changed firm setting.
- `POST /api/providers/{id}/preflight` body `{model: string|null, reasoning_effort: string|null, execute: boolean}` → `{available, status, reason, model, reasoning_effort, actual_execution: boolean}`. Read-only discovery does not spend allowance. Executing preflight uses a minimal schema probe and records observed validation.

### Portfolio, evidence, coverage and memory

- `GET /api/portfolio?namespace=real` → `{accounts: [{id, name, account_type, reconciliation_status: 'user_reported'|'reconciled'|'unconfirmed', observed_at: string|null, source: string, balances: [{currency, amount: string}], interest_rate: string|null, interest_rate_period: string|null, interest_compounding: string|null}], positions: [{id, account_id, symbol, quantity: string, currency: string|null, cost_basis: string|null, observed_at: string|null, source}], missing_data: string[], risk_settings: {max_position_weight: string|null, max_sector_weight: string|null, cash_floor: string|null, account_restrictions: string[]}}`.
- `PUT /api/risk-settings` body includes `namespace` and the four risk settings above → `{risk_settings, changed_at}`. User changes are audited; no default fabricated risk budget or Canadian instrument eligibility. Missing limits/account values cause sizing to defer, without preventing qualitative research.
- `POST /api/imports` JSON body `{namespace: 'real'|'demo', kind: 'evidence'|'transactions'|'balances', title, content: string, source_url: string|null, publication_at: string|null, observed_at: string|null, supersedes_id: string|null, idempotency_key}` → `{id, status: 'imported'|'needs_review', source_id: string|null, duplicate: boolean, issues: string[]}`. Document text/Markdown/CSV import is dependable baseline; HTML is sanitized/extracted. Spreadsheet/binary-PDF extraction is optional unless actually implemented. For account CSV define supported headers and stage ambiguity; never guess duplicate fills or missing currencies. Source replacement creates a new immutable version.
- `GET /api/sources?namespace=real` → `{items: Source[]}`. `GET /api/sources/{id}` → Source.
- `POST /api/sources/sec` body `{namespace: 'real'|'demo', cik: string, form: string|null}` → `{items: Source[], status, reason: string|null}`. SEC submissions plus supported filing import/XBRL; identify requests, cache, bound response size, honor rate limits. Missing network/data is explicit. Arbitrary user URLs must not provide unrestricted server-side fetch/SSRF; baseline imports accept content. SEC URLs are allowlisted.
- `GET /api/coverage?namespace=real` → `{items: [{id, symbol, sector, status: 'considered'|'watch'|'active_research'|'rejected'|'held', reason, reopen_when, updated_at}], sectors: {name, coverage: string, gap: string|null}[]}`. All standard equity sectors plus gold/crypto must appear, even as explicit gaps.
- `PUT /api/coverage/{symbol}` body `{namespace, sector, status, reason, reopen_when}` → updated coverage record.
- `GET /api/memory/search?namespace=real&q=&kind=all` → `{items: [{id, kind: 'fact'|'research'|'decision'|'source'|'simulation', title, excerpt, updated_at, namespace, source_refs: string[], related_ids: string[]}]}`; allowed kind values include all. Search never returns simulation for real by default.
- `GET /api/decisions?namespace=real` → `{items: [{id, run_id, agent_id, disposition: 'accept'|'reject'|'defer'|'recommend', rationale, dissent: string[], risk_results: {check, status, detail}[], output_id, supersedes_id: string|null, created_at}]}`.
- `GET /api/export?namespace=real&format=json` → downloadable JSON archive including accounts, evidence/source content, research versions, decisions, model history and relationships. Support Markdown export as `format=markdown`. No auth, secrets, raw worker logs, or internal provider thoughts in exports. Backups use SQLite backup API plus referenced evidence, with documented restore validation.

### Simulation and monitoring

- `POST /api/simulations` body `{title, question, horizon: string, participants: number, rounds: number, seed: number, initial_price: string, shock_percent: string, source_ids: string[], use_llm: boolean, idempotency_key}` → HTTP 202 `{simulation_id, status}`. Bounds: 6–12 participants, 5–10 rounds; initial_price > 0 and shock_percent > -100. Source references may cite real sources but all generated records stay simulation.
- `GET /api/simulations` → `{items: [{id, title, status, created_at}]}`.
- `GET /api/simulations/{id}` → `{id, title, namespace: 'simulation', status, assumptions: string[], limits: string[], inputs: object, participants: object[], rounds: {round: number, price: string, actions: object[], explanation: string}[], summary, model: ModelConfig|null, recorded_responses: object[], versions: object, created_at}`.
- `POST /api/simulations/{id}/replay` body `{}` → `{simulation_id, matches: boolean, replay: object}`. Exact replay uses recorded validated actions/responses, not a repeated hosted call. Fresh reruns are new IDs. Rules-only baseline must work disconnected. Simulation never claims market probabilities.
- `GET /api/monitoring?namespace=real` → `{items: [{id, name, enabled, timezone, interval_minutes, last_run_at: string|null, next_run_at: string|null, catch_up_policy, condition, source_ids: string[]}], process_required: true}`.
- `POST /api/monitoring` body `{namespace, name, enabled, timezone, interval_minutes, condition, source_ids}` → stored rule. MVP monitoring supports dependency changes and opt-in interval research scans; price alerts require a real configured quote source. Default disabled, catch-up `run_once`, configured timezone. Display sleep/process limitations. No third-party notifications are sent.

## Research and execution semantics

A question routes through A00, only relevant analyst roles, A10, deterministic risk checks, then A11. Each role has a distinct schema-bound mandate; all roles must be wired and selectable. A00 routing is persisted with reuse rationale. A10 has maximum two targeted revision rounds and explicit accept/reject/defer. Retain dissent. Insufficient evidence generates an attributed abstention, not a fabricated market claim. Imported source content and user account observations must remain distinguishable from model judgments.

An analyst packet contains only relevant source excerpts with stable source IDs/locators, explicit account snapshot ID, dates, allowed instructions, and output schema. Code supplies calculations (Decimal ledger, share-count distinction, issuance numerator/denominator, technical indicators when sourced series exists, risk checks); prose cannot mutate the ledger. Validate every returned source reference, schema, units, and required fields. Critical missing/ambiguous share counts produce needs_review. Unsupported event/options data is unavailable. Self-confidence is not validation.

Record task attempts with provider/model/effort and prompt/schema/code versions, evidence versions, source dates and observable usage. Changing settings only affects later dispatch. Reuse keys include inputs, source versions, relevant settings and freshness; show reused origin. Imports, run creation, and final commits must be idempotent. A cancelled or superseded attempt cannot overwrite an accepted terminal result.

## Codex subscription boundary

The installed CLI path may be discovered/configured; do not assume it exists on another machine. Existing inspected environment has Codex 0.153.4. On 12 September 2026 root verified both `gpt-5.6-luna` with `model_reasoning_effort="max"` and `gpt-6-astra` with `model_reasoning_effort="ultra"`: exit 0, schema-valid connection response and observed turn.completed usage. This establishes compatibility on this environment, not on every installation and not yet a complete research run.

- Preflight `codex login status` must explicitly indicate ChatGPT authentication. Fail closed otherwise. Invoke with validated argv, never shell interpolation. Force the documented `forced_login_method="chatgpt"` setting, preserve supported stored auth, and use `--ignore-user-config` so custom providers/config cannot silently select a different billing route.
- Scrub API/provider-key variables and custom OpenAI endpoint variables from the child environment. Do not copy/read/parse raw auth contents into the application. No fallback to API authentication. Login is an external user CLI action; the app only explains a concrete blocker if unavailable.
- Use supported noninteractive `exec --json --ephemeral --ignore-rules --skip-git-repo-check --output-schema <schema> --output-last-message <result> -m <validated-model> -s read-only` with a private minimal per-attempt working directory. Validate exact availability of flags/config on startup. Setting and schema filenames are controlled by backend, not request strings.
- Root's successful probes additionally used `-a never -c features.shell_tool=false -c features.multi_agent=false -c web_search="disabled" -c project_doc_max_bytes=0`. Use these controls along with ignored user config/rules and minimal worker directories. No MCP servers or inherited external integrations may be supplied. Reject any unexpected tool request/event and invalidate that attempt. Read-only filesystem mode alone is insufficient isolation. Model only needs the provided packet and structured output for this MVP; source retrieval/calculation belongs to backend code.
- Do not assume `ultra` is a portable reasoning parameter: this local CLI probe supports the actual configuration above. Runtime menu exposes only validated CLI effort/model combinations; re-preflight on other installations. Keep `features.multi_agent=false` so runtime reviews are bounded and do not spawn recursive whole-firm work. Preserve the actual requested/resolved mode without claiming nested delegation occurred. Distinguish unsupported mode from unavailable authentication; no silent downgrade/relabeling.
- Parse observable item/task lifecycle events only; suppress reasoning/internal-thought content and raw tool commands. User panels show queued, provider started, evidence validated, output saved, review requested, etc. Record actual usage when emitted; unknown quota/cost stays null. A clean process exit without a valid final schema artifact is failure.

## Office and accessibility decisions

Default scene is actual 3D procedural cutaway architecture: stone/wood, muted green, glass, warm light, simple agents. Include floor/walls/desks, eight analyst desks, Chief of Staff reception, simulation lab, archive, boardroom, and separate PM/CIO enclosed rooms with doors/nameplates/occupants. Click agent/desk/nameplate opens identical record. No purchased assets or remote texture dependency. Camera reset and bounded zoom; reduced motion stops continuous animation.

Persistent command/search/model menu/connection/pause controls. Agent inspector tabs: Overview, Current task, Queue, Outputs, Evidence, History, Model. Open complete output in a readable HTML view. State comes from backend; never animate fabricated progress. The interface has no Demo tab or automatic sample-data fallback. Disconnected state shows last-seen/stale. Every action and output remains accessible through keyboard/2D fallback, including no-WebGL environments.

Portfolio, memory, coverage, simulation, monitoring and settings panels are ordinary HTML workspace panels accessible from the office. Show CAD/USD separately; no guessed FX valuation, net worth, compounded interest, or share value without dated inputs.

## Verification and honest completion

Meaningful backend tests must cover duplicate imports/commits, amended-source invalidation, model precedence and attempt snapshots, restart recovery, late cancellation results, namespace separation, evidence citations, missing units/share-count ambiguity, risk gate and ledger immutability, exact simulation replay, subscription-only auth rejection, and export/backup restore. Frontend browser QA must exercise all 12 selections, private offices, submitting work, outputs, settings, keyboard access, private account disclosure defaults, and no-WebGL fallback.

Run one real GPT research task using dated supplied/imported evidence and observe persisted output, actual model attribution, source trail, backend events, PM/risk/CIO disposition and restart persistence. A live external blocker is reported exactly; mock/provider fixtures do not establish live acceptance. AC report explicitly distinguishes passed, failed, blocked and untested. Optional local-model/live-feed absence is visible, never represented as a successful connection.

## Review decisions: computation, validation and monitoring

These additive clarifications resolve implementation defects identified during integration review. They do not waive PRD requirements.

### Numerical outputs

Extend each Calculation with `operation` (`null | ratio | return | moving_average | issuance_assets | issuance_shares | issuance_per_share`), `input_fact_indices` (indices into that output's fact_claims), and nullable integer `window`. The existing display fields remain stable.

Before output commit, hashing, caching or PM/CIO review, code discards model-supplied calculation values and normalizes every calculation through a fixed operation allowlist. Do not evaluate generated formulas. Code produces the formula and result. Unsupported operations or unverified inputs produce `value: null`, a concrete `missing_reason`, and `needs_review`/missing-data status; they never preserve a plausible generated number as computed.

- Ratio takes numerator and denominator; zero/missing denominator is unavailable.
- Return takes starting and ending price and computes `(end-start)/start`.
- Moving average takes ordered, dated same-currency observations and a complete window. A partial window is not labeled as the full-window indicator.
- Issuance operations take existing assets, current outstanding shares, cash raised and issue price. Code updates assets and the share denominator together. Weighted-average EPS shares cannot substitute for current outstanding shares; nonpositive issue prices and invalid denominators are unavailable.
- Inputs must be finite and have verified source references, units, periods and locators. Ratio dimensions support equal units or currency/shares; unsupported unit scales/dimensions and mixed currencies remain unresolved until explicitly supported.

Number original source lines in each model packet and use stable `Lx`/`Lx-Ly` numerical fact locators. Resolve the exact attempt-supplied source version and validate numeric tokens using Decimal (with explicit supported formatting normalization), not substring matching. The value 10 must not be accepted merely because the passage contains 100. Unsupported locators remain proposed/unresolved. A model's `completed` status alone must never mark all extracted facts `validated`.

### Portfolio proposals and risk

An optional structured proposal has nullable `account_id`, `symbol`, `sector`, `target_position_weight`, `sector_weight_after`, and `cash_weight_after`. Weights are hypothesis fractions. Code validates their ranges, consistency and configured hard limits; it does not parse allocations from free text. A limit failure forces defer even if CIO prose recommends an allocation.

Cost basis is never a current market valuation. An account row, one balance, or a known share quantity does not establish complete account value. Preserve each balance/position observation ID, date, status, source and currency in the frozen run snapshot. Account composition, dated position market values, and currency conversions must be explicit for sizing. User-confirmed observations may establish facts; do not invent broker reconciliation. Unknown composition, missing dates, mixed currencies without dated FX, or incomplete valuations keep allocation sizing deferred. Hypothetical weight-versus-limit checks can still run and remain labeled as proposal checks.

Proposal account IDs must exist in the snapshot. A sector weight cannot be smaller than its proposed constituent position weight, and sector plus cash weights cannot exceed one. Nonempty account restrictions without an implemented deterministic eligibility check cause defer. Do not interpret a ticker as proof of Canadian account eligibility.

### Candidate models and local request origins

Supported model/effort candidates and actually validated pairs are separate states. Read-only preflight reports authentication/candidate readiness with `actual_execution: false` and an explicit exact-pair `validated` flag. Executing preflight must work for an unvalidated supported candidate; never require a prior success to run the first probe. A research request authorizes a bounded first-use schema probe for its selected pair. Do not spend allowance merely from startup/discovery. Preserve the same shared generation limit around probe and work.

The backend validates both loopback client and Host. Allowed hostnames are exact `localhost`, `127.0.0.1`, or `[::1]` with a valid port. For mutation requests, accept the validated request's actual same-origin scheme/host/port plus explicitly configured development origins. An arbitrary Host remains rejected even when the client connection is loopback. An allowed custom local port must not fail merely because it differs from port 8000.

### Monitor semantics and lifecycle

Extend monitoring with `mode: interval_research | source_change`, default `interval_research`. The condition field is a research instruction, not executable code or an automatically understood price predicate. Source-change rules require same-namespace sources and compare version/lineage hashes; unchanged inputs cause no model work. No configured quote source means quote-triggered alerts are unavailable.

Provide `PUT /api/monitoring/{id}` with `{enabled: boolean}` and an audited namespace-safe update. Validate source references on rule creation. Claiming a due occurrence writes a durable pending occurrence with stable schedule/due idempotency key. Remove it only after its run is durably created; a crash between claim and run creation must not lose the scan. Resume at most one missed occurrence after sleep, avoid overlapping unfinished runs for a rule, and preserve pending/queued state while the firm is paused. `last_run_at` reflects actual run creation/dispatch rather than claim time.

### Explicit retry after a blocker

Add `/api/control` action `retry` for task/run scope only. It applies to failed, blocked or interrupted work, records `retry_requested`, preserves completed outputs and old attempt history, and queues a new attempt under the current resolved model settings. The run's original account/source snapshot remains unchanged. Downstream unfinished work resumes once its dependency succeeds. Resume remains the operation for paused work. Cancelled work stays terminal and requires a new run; completed work is not silently rerun. Explicit user retry and bounded automatic retries have distinct audit records.

### Immutable refreshes and effective review decisions

Source amendments mark dependent historical outputs stale and exclude them from current cache reuse. Preserve their original task, output, source and attempt links. A refresh resolves the latest source-lineage heads and creates a new run/snapshot or explicit derived work with a supersession link. Never reset an old task to queued while retaining its old source packet and claiming to have refreshed the evidence.

A PM revision round includes a response from the named relevant analyst(s), followed by a new PM review that depends on those answers. Repeating the PM alone is not an analyst revision. At most two rounds are allowed. Preserve the final accept/reject/defer disposition and dissent; unresolved/rejected PM work cannot become a CIO allocation recommendation.

Normalize arithmetic, fact-validation status, the PM gate and risk checks before committing the effective output. The saved output and decision journal must agree on defer/reject status. Keep the original model proposal as an audited proposal when necessary, but do not display it as the effective recommendation after code has rejected it.

### Simulation integration

The optional model packet includes each selected participant's permitted observable round-one price/shock state, opening cash/shares/NAV and constraints. Delayed participants do not receive the shock observation withheld by their information set. Retain source title/URL/publication/observation/retrieval metadata in snapshots; bound prompt excerpts explicitly while retaining complete local evidence.

Track simulation background tasks through shutdown and restart. An orphaned running simulation becomes explicitly failed/interrupted, without silently repeating a hosted call. Incomplete replay returns a normal 409 state error. Restore requires all background writers to stop.

Expose A07's `current_simulation` and `related_simulations` as additive agent-detail fields, and link the actual scenario report from its inspector. Simulation lifecycle events identify A07. All such records keep their Simulation label and namespace; exposing work in the office does not promote it into real research evidence.
