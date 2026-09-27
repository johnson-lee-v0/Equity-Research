# Research experience — September 26, 2026

The primary navigation has five sections: **Research, Watchlist, Portfolio,
Congress, Strategy testing**. Office and Settings remain utility controls.
Research combines idea intake, progress and saved decisions. Origin filters
identify user, Reddit, Congress and watchlist research. Existing bookmarks for
Questions, Results, Reddit, Documents and Library resolve within Research;
Evidence and Scenarios no longer occupy the main navigation.

A case keeps its investment sequence: ticker → earnings → five questions →
answers → pricing → decision. The earnings button opens the exact review used
in that assessment, inside the case. Later earnings collections do not replace
that historical evidence. Company names are presentation labels; ticker IDs
select the earnings receipt and announcement window.

## Plain-language earnings reading

Business themes and negative language show short bullets explaining the ask
and management's answer. A bullet expands its attributed original quotes, then
links to the precise passage in the full transcript. Prepared remarks are
labelled separately. Partial answers stay labelled, and negative wording does
not imply the business is deteriorating. A solid amber highlight with dark
text distinguishes the cited passage.

The separate **Question & answer** and **Management remarks** blocks containing
the original words start collapsed. Select their headings to expand the speaker
context and transcript links; plain-language main points remain visible.

The local reader starts with an overview of **every saved business theme**. Each
card previews one retained interpretation and shows the number of discussions;
opening it keeps all questions, management replies, qualifications and matched
passages available. The detailed cards use **Asked**, **Answered**, and **Still
unclear / Answer coverage**. Coverage comes from the saved explanation, not a
new judgment that a claim is true. Explanations from another source hash are not
reused. If no explanation was saved, the original wording stays available in a
closed disclosure instead of generating a new summary on read.

Preview selection favors saved question-and-answer discussions whose exact
source passages match the theme, rather than whichever exchange came first.
For multipart discussions, the preview uses matching question and answer bullets;
when their relationship is ambiguous it keeps the complete pair. Opening a
preview lands on that discussion's page without removing the other discussions.
Unmatched prepared remarks never fill a theme preview; an absent source match
shows an explanation gap with the original discussion still accessible.

The shared `EarningsTrendExplorer` groups supplied series into **Growth**,
**Margins**, **Earnings & cash**, and **CapEx & guidance**. Unfamiliar saved
metrics remain under **Other saved figures**. Every category keeps the metric's
definition, periods, actual/guidance/projection labels and sources; capital
spending retains its guidance-versus-actual comparison. Empty categories explain
what was not collected. This is presentation of saved evidence, not expanded
collection: earnings/cash figures appear only when the supplied package contains
them. The public snapshot and local view can use the same component without a
request to a provider or the private ledger.

Business themes can display related saved figures using the retained topic
mapping, with the latest actual and source beside an expandable chart. These
figures do not establish that management's explanation is correct or that a
different period is comparable. All negative-language speaker filters, search,
discussion ordering, pagination, transcript navigation and downloads remain.
Management commentary and opening-statement source wording also start collapsed;
available saved interpretations remain visible.

Focused coverage lives in
[`earnings-review-overview.test.mjs`](../frontend/tests/earnings-review-overview.test.mjs),
alongside the existing trend, CapEx, transcript-context and opening-highlight
tests. They use synthetic records and do not read or modify saved research.

`TranscriptBriefs` prepares the reading aid through the configured local Codex
provider during new earnings workflows. The reader can explicitly prepare or
retry missing explanations for retained complete transcripts. Ordinary reads
do not run a model. Firm pause and cancellation are respected.

The sidecar cache is keyed by namespace, source hash, speaker turns, exchanges,
theme evidence and method version. It does not rewrite the transcript analysis
or investment decision. Validation requires quotes to match a unique exact
passage in the attributed turn, keeps every question and responding manager,
and rejects invented group IDs or mixed question/answer roles. Exact quotes
establish provenance, not the correctness of a paraphrase; the UI labels the
bullets as interpretations. Missing or oversized discussions retain the
original question and replies with an explicit explanation gap.

Generation is bounded to 60 discussions, 26,000 characters per discussion,
32,000 characters per batch and eight calls. Successful discussions are reused
on retry. No new API key is required. The latest retained COST transcript also
has 37 source-checked bullets for 16 discussions, prepared during this change;
its original saved source and investment conclusion are unchanged.

## Research and valuation additions

- [Optional research actions](optional-research-actions.md) describes primary
  releases/current reports between filings, the post-completion opposing
  review, one-item evidence retries, pause controls and separate receipts.
- [Comparable valuation](comparable-valuation.md) describes sourced P/S,
  EV/EBITDA and NAV/book methods, peer acquisition, current implied values and
  five-year multiple charts with average and ±1/2/3 standard deviations.

The opposing review follows the bull/bear/resolution pattern from
[TradingAgents](https://github.com/TauricResearch/TradingAgents). It runs through
the existing local research engine. It does not install a separate trading
system or place trades.

New acquisition steps apply to new investment reviews. Historical decisions
retain their original numbers and acquisition gaps. Displaying another
supported valuation method does not change the saved decision. Unsupported
financial inputs stay unavailable; book equity is not silently presented as
appraised NAV.

## Verification

The full backend suite passed (1,189 tests), followed by focused checks for
the final share-class/archive safeguards. All 81 frontend tests and the
TypeScript build passed. Browser checks cover the five-section layout,
source-bound COST bullets, expandable quotations and highlighted transcript
links. Synthetic calculations and optional-action controls are exercised in
an isolated local database with a deterministic provider, without spending
model tokens or changing real research. The production build retains the
existing large Office bundle warning.

The live workspace remains paused. The completed COST investment case and
its existing queued work are retained. A database snapshot from before the
local server refresh is stored under `runtime/qa/research-expansion/`.
