# NKE run diagnostics — September 26, 2026

The completed NKE case finished at 09:13:38 UTC on September 26, 2026, with a
Watchlist outcome. Its start-to-finish time was 1 hour 52 minutes 48 seconds,
including repairs and retries. The earnings package was ready after 17 minutes
42 seconds. Earlier failed/cancelled cases make the interval from the first
submission to this result approximately 3 hours 15 minutes 34 seconds; subsequent
verification is separate. This is not a clean cold-start benchmark. The app
continues to use the configured local Codex provider and unchanged role profiles.
See `runtime/benchmarks/nke-timing-summary.md` for the timing breakdown and limits.

## Confirmed failures and corrections

- CLI page opens sometimes arrived as an `other` event containing an empty
  display query or a complete URL. Counting those as searches exhausted a limit
  that had not actually been reached. Accounting now distinguishes these observed
  event shapes, preserves the existing limits, and retains bounded diagnostics.
- An explicit single ticker unnecessarily went through generic idea discovery.
  It now prepares verified latest earnings sources before ordinary analysis and
  final review. Broad questions and ambiguous routing retain discovery.
- Separate transcript name/title lines were misread. Analyst job titles became
  management speakers, merging separate questions into one long exchange. The
  parser now uses full speaker labels and operator introductions; uncertain roles
  remain unknown. Corrected NKE input has seven substantive analyst questions.
- A subsequent discovery selected another publisher whose unsupported layout
  put the entire call under the operator. Acquisition now requires attributable
  management speech and an answered analyst exchange. It tries intact archived
  URLs for the same issuer and event before searching again, fetching and
  revalidating each candidate. New cases cannot reuse a package containing a
  transcript that fails the current structural check; frozen history is retained.
- The reading-aid schema omitted the validator's eight-bullet maximum. The model
  produced a large response that could not be accepted. Prompt, schema and
  validation now share the same limits, with explicit gaps for unsupported groups.
- Percentage validation rejected “down 1 percent” as −1% while accepting +1%.
  Direction now binds to the exact number and measure. Normalized period quotes
  and explicitly reported flat quarterly revenue are also supported.
- A vertical monthly income-statement layout put EPS beyond the previous scan
  bound. The bounded parser now aligns durations, dates, EPS and percentage cells,
  retaining actual calendar intervals and unresolved currency where necessary.
- Ordinary company reviews lacked the financial preparation used by earnings
  handoffs. Both now receive source-bound operands and calculator validation.
  Current implied value requires current factual capital-structure inputs; future
  assumptions cannot silently become today's balance sheet.
- Optional peer discovery raised a provider limit error before the researcher
  started, stopping the whole case with no retryable task. Peer failures now
  produce retained gaps and terminal receipts; the core review continues. A
  same-case retry reuses that receipt. Preparation failures retain an actionable
  reason and the pending task, while old generic failures can recover only their
  never-started tail. Completed and cancelled work cannot be reopened this way.
- Capex selection incorrectly used the earnings announcement as its filing
  cutoff. New collections use the frozen research/source-observation cutoff,
  retaining the earnings date and actual filing dates separately. Later guidance
  still cannot count as an earlier forecast. Existing frozen packages are not
  silently rewritten.
- A single-company analyst/reviewer packet repeated the same valuation context
  under both singular and per-ticker fields. Future ordinary packets omit only
  an exactly identical sole-ticker alias after source budgeting, preserving the
  original evidence allowance and frozen records. The observed NKE duplicate
  was approximately 49,098 characters. Numeric packet/block size receipts now
  support diagnosis without storing full prompts or reasoning. This change was
  loaded only for the final-review recovery segment; no comparable speedup has
  been measured.
- The optional capex follow-up found insufficient new evidence, then both
  analysis attempts exceeded the configured 15-minute generation limit. A
  guarded receipt now allows final review of the intact original analysis after
  exhausted optional timeouts. Failed tasks, attempts and the unresolved gap
  remain visible. Original output hashes and all source versions are checked
  again at dispatch, publication and crash recovery; final calculation checks
  remain mandatory. Initial-analysis failures and non-timeout failures are not
  eligible.
- The first final-review response supplied a valid EPS baseline and scenarios,
  but descriptive fields belonging to EV valuation were interpreted as numeric
  aliases and incorrectly rejected the P/E method. EPS now reconciles only its
  own forecast-EPS and P/E aliases; genuine EPS conflicts and all fact bindings
  remain checked. Other methods retain their checks. Prompt instructions also
  distinguish current-EPS value from discounting a future target. The original
  rejected response remains retained; the separate correction attempt completed.
  The generalized alias fix was loaded after that successful retry.

## Measurement records

Immutable attempt and timing records are retained under `runtime/benchmarks`.
`nke-verified-run-2026-09-26.json` measures the fresh ordinary case started after
the first corrections; it was cancelled because of the unsupported publisher
layout. It is not a successful completion benchmark. Earlier failed runs and
isolated protocol probes remain separate.
`nke-complete-run-2026-09-26.json` records the subsequent case with structural
validation and archived-candidate fallback enabled. Its final status and duration
must be read from the record rather than inferred from its filename.
It stopped at 07:39:40 UTC on the optional peer error. The valid earnings package
was retained. `nke-analysis-recovery-2026-09-26.json` measures resumption of that
same case at 07:50:49 UTC, using the saved package. Neither record alone is a
clean end-to-end benchmark; the gap between them is repair time.
That recovery stopped at 08:44:04 UTC after both optional-analysis timeouts.
`nke-final-review-recovery-2026-09-26.json` starts final review of the same case
at 08:46:08 UTC through the guarded recovery receipt, without another analyst
generation. It completed at 09:13:38 UTC. The consolidated terminal report is
`nke-completed-timing-report.json`; segment records retain their original failures.

Stage durations can overlap because source work runs concurrently. Report wall
time for the complete case, rather than adding overlapping stage durations.
Token totals deduplicate attempt IDs; cached input is already included in input
tokens, and missing usage remains unknown.

An isolated MSFT primary-event check completed in 89.59 seconds. It verified
issuer identity and the latest primary release only; it was not a full MSFT
investment review. Synthetic cross-ticker regressions cover the ordinary research
path, share-class aliases, funds, conflicting routes and valuation methods.

The existing COST case, queued COST case and two saved earnings packages are
checked against their original row hashes. Their decisions are not recalculated
as part of diagnosing NKE.

After the source/reader, optional-peer, recovery and packet-duplication
corrections, optional-timeout recovery and method-specific alias checks, the full
backend suite passed 1,480 tests.
The earlier frontend check passed 81 tests, type checking and the production build.
An isolated SEC preflight also confirmed current NKE annual EPS and the exact
comparative currency binding; its files are under
`runtime/benchmarks/nke-financial-preflight` and do not alter app evidence.

## Final result and remaining limitations

The completed case preserves the latest earnings package, five questions and
answers, calculated pricing, a Watchlist conclusion and explicit evidence gaps.
Browser inspection confirmed four reported FY2026 quarters, a separate reported
annual EPS baseline, one forecast growth step, a 12-month holding horizon, and
61 historical P/E observations with an average and ±1/2/3 standard-deviation
lines. The multiple chart's vertical axis does not start at zero.

The canonical price card is correctly calculated, but saved model prose in the
valuation question, rationale and blockers still says numerical targets await
recalculation. Expanded typed evidence also retains an unused 10% discount
assumption. The current-value calculation explicitly applies the scenario
multiple to reported EPS without discounting. Raw historical output has not
been rewritten to hide these contradictions; future prompt instructions clarify
this distinction, but that does not repair the saved narrative.

The earnings package remains partial: filing acquisition and capex/guidance
comparison gaps are visible, and an optional follow-up synthesis timed out twice.
Its failed attempts remain recorded even though the core case completed through
the guarded recovery path. Four archived COST rows were verified unchanged.

For this case, the target gate used the frozen 07:54:08 UTC financial cutoff and
the same 25 archived source versions. The canonical decision labels its as-of
time using output creation at 09:11:58 UTC; all retained sources predate both.
Dedicated preparation metadata is not copied by the attempt-input allowlist,
although the actual context, source versions and calculator binding were checked.
These are retained audit limitations, not a claim of a fully clean result.

Post-completion browser verification also exposed slow office/result reads.
A Python stack confirmed repeated comparable-financial parsing while rebuilding
agent output views. Indexing newline positions once replaces repeated prefix
scans without changing source quotes or locators. On the archived NKE payload,
the parser median fell from 0.7326s to 0.0788s, with all 716 observations
byte-identical. An additional 31 focused tests passed after the full-suite run.
This is a parser measurement, not an end-to-end workflow speedup estimate.
After reload, the selected NKE office read completed in 2.383 seconds and the
health endpoint responded normally. Background research remained paused.
