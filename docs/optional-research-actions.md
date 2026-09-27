# Optional research controls and intervening events

The initial investment case now checks issuer press releases and current reports in addition to its earnings package. After that case completes, the reader may request a challenge or retry one evidence item. Neither optional action changes the original recommendation, reruns pricing, closes the original gap automatically, or starts another research loop.

## What happened between reports

Before the initial A03 synthesis/preparation, `interim_events` resolves the issuer against SEC submissions and checks events from the latest periodic report's **covered period end** through the case's cutoff. Starting at the covered period end includes updates issued before the report was filed. Filing date, event date, publication date and retrieval time remain distinct.

The bounded pass fetches up to eight recent 8-K/8-K/A/6-K documents and searches for up to six issuer releases or substantive company-event updates. It includes non-earnings developments such as guidance changes, leadership, financing, acquisitions and regulatory updates. Candidates must identify the issuer, remain on its verified domain or SEC CIK path, match the date window and bind a proposed release quote to independently fetched text. Discovery snippets alone never enter evidence.

The receipt at `investment_process.interim_events[]` includes the baseline filing, cutoff, exact source IDs and content hashes, event metadata, acquisition checks and gaps. No matches does not mean no events happened. Missing periodic metadata uses a labelled 180-day window; a stale reporting baseline is capped at 400 days with the uncovered period noted. Repeating the same case reuses and verifies its frozen receipt. A new case gets a new check. Historical completed cases remain unchanged until a new review is requested.

## Challenge the ticker

The user-triggered challenge adapts the opposing-researcher pattern from [TradingAgents](https://github.com/TauricResearch/TradingAgents). It uses the existing local Codex provider; TradingAgents is not installed and its trading/portfolio controls are not used.

The fixed graph is one bounded source lookup, an argument for the thesis, an argument against it, and a resolution. It allows at most four model calls, two individual search queries, four web actions and three independently fetched candidate pages per pass. The resolution explains the strongest supported points on both sides, unresolved questions and what evidence would change the view. A weak evidence packet must remain inconclusive.

## Retry this evidence

The user selects exactly one saved gap ID or source ID. The server resolves its scope; the UI cannot supply a new broad research instruction. A gap retry sends only that gap to discovery, without the original question, other gap descriptions, the original conclusion or unrelated archived documents. A source retry sends only that retained source as its initial evidence. Both use one query, at most three web actions, three independently fetched candidate pages and two model calls: discovery plus a scoped review.

The result distinguishes supported, partial and unresolved findings. It does not automatically mark a case gap resolved. Private account inputs and nonpublic evidence are ineligible; a source from another case or namespace is rejected. A resolved gap or one already in progress cannot start another pass.

## Durable execution and evidence boundaries

`POST /api/runs/{run_id}/research-actions` accepts `{namespace, kind, idempotency_key, gap_id?, source_id?}`. `kind` is `challenge` or `evidence_retry`; retry requires exactly one identifier. Creation requires a completed initial case. `GET` at the same path returns eligibility, saved gaps and sources, and the optional pass receipts. Ordinary `/api/runs` excludes optional children; `include_actions=true` includes them for diagnostics.

Each action has a separate child run and append-only stage receipts. Request keys, including a second key that reused the same active scope, keep their mapping after completion. The original case's source and output bindings are retained. Quotes must match archived content exactly; source IDs must be in the action packet. Optional retrieval imports changed content as a separate observation without amending shared sources or waking other case refreshes. Provider packets state omitted sources and truncated source coverage explicitly.

The normal scheduler owns provider capacity, authentication, cancellation, pause, restart recovery and usage accounting. A paused firm does not dispatch a newly queued action. Its existing **Run once** control can authorize that selected child without waking other jobs. There is no automatic retry, debate loop or follow-up fanout. If the provider fails, returns an invalid citation, or a late result arrives after cancellation, the failure stays visible and the original case is unchanged.

This is evidence assistance, not an assurance that a model's interpretation is correct. Exact quote validation establishes textual provenance; it does not prove every interpretation or causal claim.
