# Research assessment latency audit

Measured 2026-09-25 from the local SQLite event/attempt history and retained provider artifacts. Baseline case: `run_ea0ad973f7044ee393560b83eb39546e`, earnings package `wf_0cc1db44db924884bbce32987e516c26`. These are observed timings, not estimates of a future optimized run.

## The critical path

| Stage | UTC start → finish | Elapsed | Outcome |
|---|---|---:|---|
| A00 routing | 21:29:39 → 21:30:43 | 1m 04s | Already-known ticker/archive routed |
| A01 discovery, first attempt | 21:30:43 → 21:31:39 | 56s | Search-query limit exceeded |
| Recovery gap | 21:31:39 → 21:35:59 | 4m 20s | Explicit retry |
| A01 discovery, second attempt | 21:35:59 → 21:37:41 | 1m 42s | Same limit exceeded |
| Recovery gap | 21:37:41 → 21:47:00 | 9m 19s | Archived-evidence recovery introduced |
| A03 synthesis, including local pre-review | 21:47:00 → 21:59:02 | 12m 02s | Output saved at 21:58:57; 15/15 fact claims remained proposed |
| A11 first attempt | 21:59:02 → 22:07:10 | 8m 08s | One question returned; five-question contract rejected it |
| Recovery gap | 22:07:10 → 22:13:48 | 6m 38s | Explicit retry after schema correction |
| A11 second attempt | 22:13:48 → 22:21:45 | 7m 57s | Saved five-question decision, but no price target |

The research run took **52m 06s**: **31m 49s** inside attempt intervals and **20m 17s** between attempts. There was at most one second between attempt creation and provider admission. The four-slot global provider pool was not the bottleneck; serial dependencies, repeated work, long generations and repair waits were.

Successful provider-reported usage (not a measurement of unique prompt size):

| Stage | Input tokens | Output tokens | Reported reasoning output tokens |
|---|---:|---:|---:|
| A00 | 130,830 | 1,783 | 805 |
| A03 | 120,353 | 39,296 | 28,645 |
| A11 second attempt | 445,160 | 15,113 | 6,459 |

A00/A11 used Astra Ultra; A01/A03 used Luna Max. Failed calls have no saved usage. Provider usage may include internal turns; these counts must not be presented as exact unique prompt-token counts. The A03/CIO schema files were about 31 KB and the A00 schema about 44 KB.

## Evidence collection before the research run

The package started at 20:22:32. Its first partial publication took 5m 28s. Two development/recovery passes took 12m 01s and 5m 37s. Final package publication was 20:56:06; handoff was 21:29:39. The 33m 33s between final publication and handoff was development/interaction time, not retrieval or model runtime.

The overall recorded interval from the first package start to final research completion was 1h 59m 13s. It is not a clean production benchmark: implementation, user interaction and repairs occurred during it.

Cold trend extraction is material: the 11-document pass took 11m 53s and emitted 39,431 output tokens, including 31,912 reported reasoning tokens. A later three-document capex repair took 4m 45s, mostly reasoning. The initial two-document extraction took 2m 58s. The final 11-document retry reused an identical extraction in under a second. Exact prompt/model/schema/source-identity caching already exists; do not replace it with an unverified summary cache.

The extraction seam now uses a content-hash/version cache per document and metric schema, revalidating retained observations before composing a new multi-quarter series. At most three document extractions run concurrently across workflows sharing the provider registry. The existing global provider pool still applies. Recompute only new/amended documents or a different extraction scope; retain year, measure, geography, unit and actual/guidance distinctions. There is no need to re-run a large extractor over eleven unchanged calls to add one quarter. Historical URL discovery/fetch behavior remains unchanged.

Cache keys bind the complete original source text, URL/title/period/date/kind metadata, namespace, model settings, schema, extraction instruction and validation version. The rolling latest-event date is not an extraction dependency: each worker extracts its own source period, then the current event's validator rebuilds the series. A source-ID rename is allowed only for identical inputs. Changed text outside the model's excerpt also invalidates the cache. Every extraction retains its provider receipt; cache hits record the original attempt. Failed documents do not discard successful siblings, and cancellation awaits active children before returning.

Deterministic tests demonstrate six documents starting at concurrency three, a later seven-document quarter requiring only one new generation, source-ID rebinding, and identical resulting series/explanation projections to serial assembly. They also cover changed source/model/schema metadata, rejected cached numerical mismatches, partial-failure retry, the shared three-call limit across two workflows, and cancellation without stray generations. These tests establish scheduling and evidence invariants; they are not a live model latency benchmark.

## Why output was not defensible enough

All fifteen A03 claims were proposed, not validated. Several combined multiple metrics, periods or geographies in one `value`; the source-local binder correctly refused them. CIO then inherited missing/unusable EPS and valuation assumptions, and returned a null target. Missing portfolio sizing inputs also appeared alongside valuation gaps even though account sizing is not necessary to estimate issuer value.

The saved failed CIO result contains only one question. Its schema permitted that. The current provider schema has `minItems: 5`; this specific fault is already corrected. Current code also adds atomic-claim instructions, complete latest-call coverage and a source-bound historical earnings context. These improvements should be reused and measured, not counted as newly implemented optimizations.

## Faster assessment architecture

For a reciprocally linked, hash-verified single-company earnings package:

1. Code resolves the known route and compiles unchanged source evidence. Do not pay for A00 to rediscover intent or A01 to repeat a public search after the earnings collector has already resolved the company and event.
2. In parallel, prepare current market context, validated earnings/valuation inputs and any narrowly identified missing source. Retain a receipt for the exact sources, hashes, versions and omissions.
3. Feed one final research/CIO generation the five required questions, the full latest call including every question/answer exchange and management qualifier, historical trend observations and a small atomic fact register. The model selects explicit forecast assumptions; code calculates the scenarios. Independent deterministic binding and the local review remain outside the model.
4. Require a dated base target plus bear/bull values, formula, historical anchors, normalization adjustments, forecast assumptions and multiple rationale. Forecasts and multiples are explicitly modeled assumptions; they do not require pretending that an analyst's estimate is a reported fact. An unsupported source anchor triggers targeted acquisition/repair before publication, not a completed answer with a missing target. Keep personal portfolio sizing separate.
5. Publish only after arithmetic, source-version bindings, all five questions, target rationale and citations pass. Reuse the saved library/revision/trend paths.

The implementation seams are `verified_earnings_package` / `build_earnings_context`, the workflow handoff and task builder, the pre-provider evidence projection, the existing Decimal valuation service, and the canonical decision store. A generic free-form/reddit universe request still needs discovery; this deterministic shortcut is not authorization to guess its ticker or bypass screening.

Acceptance requires a new measured live assessment and a visible reasoned target. Merely reducing prompt bytes or passing unit tests is insufficient proof of end-to-end speed or financial quality.

## First fast-path verification and continuation policy

The first new COST A11 attempt (`att_6761d9bd75d844188c0ecef18041d49b`) ran from 2026-09-26 00:23:40 to 00:34:55 UTC: 11m 15s. It produced a calculated target but requested four remaining gaps: incomplete call coverage, financial bindings/filings, personal position/risk inputs, and a current quote with original locator/connector identity. The old materiality ranking selected the quote and appended another A01 → A03 → A11 sequence. This is not yet a clean final benchmark: the packet's omitted call passages and timestamp issues are being corrected separately.

`assessment_continuation.py` now defers automatic research replay for execution-only gaps after independently recalculating a complete target from the attempt's frozen prior fact/source allowlist and verifying the earnings archive. The gap descriptions remain in the ledger and canonical limitations. Mixed requests containing EPS, filings, full-call, issuer-conflict or other financial requirements retain ordinary repair eligibility. Unknown requests also retain that route. A quote/portfolio limitation cannot consume the one repair slot ahead of a substantive financial gap. The policy does not change local review, publication, pause, trading or sizing controls.

End-to-end deterministic tests cover one-call target publication with unresolved execution limitations, substantive-gap continuation in the same output, and rejection of missing targets, wrong candidates, outside-allowlist facts, non-assessment runs and changed archives.

## Full-call validation findings

The next full-call trial (`run_0918b09c673f442b8297428a683d2bda`, 00:55:07–01:07:12 UTC) prepared all fourteen analyst exchanges and seven validated financial/operating observations in a 103,759-character compiled packet. Preparation took nine seconds. Both hosted attempts produced prices only in prose and omitted `candidate_briefs`; the publication barrier correctly refused both. They took 5m 50s and 6m 06s. This failed trial is not a successful latency benchmark.

The provider schema had permitted an empty candidate array and nullable valuation. The verified earnings contract now requires one candidate and explicit scenario objects before prose, with a short executive synthesis. Independent source binding and calculator validation remain the publication authority. Provider usage is recorded before validation so future rejected outputs retain their consumption receipts; missing historical receipts have not been inferred or backfilled.

The stricter-schema trial (`run_e33f6963d68f4f7896628d23e66a0724`, 01:18:39–01:42:22 UTC) also failed and is not a completed benchmark. Its first 8m 35s proposal had a usable valuation but changed a baseline label from `annual period ended 2025-08-31` to `FY2025, annual period ended 2025-08-31`. The independent gate rejected that altered metadata. A full-review correction consumed its 15-minute timeout. Several question answers also reached their hard character bounds and ended mid-sentence. The next provider contract fixes the historical input to its exact archived representation and requests complete answers well below the field limit, while retaining the original value/source/period validators. Historical failed attempts are preserved.

Future attempts record actual prompt and schema character counts and hashes at the provider boundary, without logging private prompt text or provider reasoning. Those counts complement the compiled-source receipt and provider-reported cumulative token usage; they are different measurements.

The next measured prompt was 412,225 characters despite a 103,759-character compiled evidence context. The generic fact read model attached a full SEC JSON line to each of two annual EPS observations, then repeated those observations in prior outputs and financial seeds. A read-only projection of the real packet removed 211,160 characters in 0.031 seconds, predicting a 201,065-character prompt. All complete-call evidence (80,496 characters), historical trend context (12,804 characters), fact identities and validation metadata remained unchanged. The only substituted fields were four redundant `excerpt` values, each replaced by its already-bound 155-character SEC record after independent revalidation. This is a measured input-size reduction, not a measured live generation speedup. See [the projection receipt](qa/assessment-provider-projection-cost-2026-09-26.json).

The two empty-candidate attempts had NULL usage despite completed provider generations: target validation raised before the success-only usage write. Returned provider usage is now persisted immediately after generation without changing lifecycle state, and later `finish_attempt` calls with unknown usage preserve that receipt. Tests cover rejected typed output and both target-validation retries. Historical NULL values remain unknown; usage from adapters that fail before returning a result is outside this narrow fix.

## Completed full-call verification

`run_5d1294c72ebf4cec919f7d8f12122233` completed from 2026-09-26 01:51:08 to 02:10:14 UTC: **19m 06s**, including its corrective review. Three deterministic preparation stages took eight seconds. The first Codex review took 9m 45s and was correctly rejected because it declared operating-context facts and an unverified quote as financial calculation operands. The correction took 9m 13s and retained only the validated SEC annual EPS operand. This is two Codex generations, not a one-generation benchmark. The next-run schema now restricts numerical valuation references to the verified financial operand pool; operating context remains available through narrative citations.

The completed review retained the full latest release and all fourteen analyst exchanges, seven saved observations and five complete question answers. Independent Decimal recalculation and the live Library/Results views agree on USD **667.16 bear / 959.39 base / 1,176.16 bull**. The base calculation is `18.21 × 1.12² × 42 = 959.390208`: dated FY2025 diluted EPS projected two annual steps to FY2027, with a separate twelve-month price horizon. Growth rates and exit multiples remain explicit, reasoned analyst assumptions. The newest filing/currency binding and peer/historical multiple calibration are still gaps; the report does not turn them into facts.

This was a warm reassessment of already acquired materials. Compared with the earlier 52m 06s assessment, elapsed time fell by 33 minutes and the result now includes a reproducible target. Cold acquisition/trend extraction was not included. The measured 51.2% duplicate-input reduction and financial-operand schema guard were installed after this run, so no live time saving is attributed to them. Failed development trials above remain part of the audit.

Read-only acceptance evidence is saved under `runtime/qa/assessment-pipeline/verified-live.json`, with the displayed target in `verified-target.png`. Scenario switching and the saved transcript/source links were verified in the browser. The company Library and Results show the same canonical values; unrelated queued research remains paused.

The saved assessment is a watchlist research result, not a jointly approved recommendation. Its pre-review local receipt is present, but its post-review receipt is unavailable: that separate reconstruction path had dropped the frozen issuer document required to validate the SEC EPS row. The subsequent source-packet fix restores that exact related evidence without broadening the selected fact allowlist. The historical unavailable receipt has not been rewritten or presented as a successful local review. Full packet projection now passes in a read-only replay; its local-model token budget and final resolution remain separate from the verified price calculation.
