# Manager workflow upgrade — completion ledger

The detailed requirements are in [the architecture contract](manager-upgrade-contract.md).
This ledger records implementation and acceptance evidence. The product remains
local; this upgrade does not publish a site, place orders, transfer money or
resume Reddit processing. Luna Max workers implemented the changes; Astra Ultra
performed independent decision, evidence and interface reviews.

## Implemented and independently checked

- [x] Bind facts to issuer, metric, period, unit, accounting/share basis and
  retained source version. Ambiguous prose, conflicting table rows and unrelated
  numbers cannot establish support. Preserve historical validation separately
  from current semantic checks.
- [x] Resolve local and previously supplied fact references only within the
  original frozen attempt packet. Missing references or changed source hashes
  cannot silently acquire support from current memory.
- [x] Close evidence gaps only with explicit, fresh, requirement-bound evidence;
  omissions, an agent's wording or an unrelated metric cannot close them.
- [x] Require a complete investment argument before Recommend, including an
  opposing explanation, valuation, directional payoff, entry/exit conditions,
  invalidation and a dated catalyst or review.
- [x] Calculate earnings-multiple, DCF/enterprise-to-equity and asset/NAV methods
  from supported historical inputs and explicit assumptions. Show conditional
  scenarios and sensitivity; do not substitute prices for fair value.
- [x] Distinguish proposed allocation from maximum capacity. Check portfolio
  exposures, shared funding/holding limits, adverse fills and dated short
  feasibility. A recommendation never creates a holding.
- [x] Compare candidates and the cash/benchmark alternatives. Cash calculations
  require frozen dated terms, currency, rate convention and horizon. Missing
  terms stay unavailable. Future benchmark returns are not inferred from history.
- [x] Retain readable PDF originals with page/line provenance and explicit
  extraction failures. Keep immutable source supersession and dated freshness.
- [x] Cluster related Reddit ideas and show transparent priority. Holding
  relevance comes from verified local holdings; unavailable mandate, liquidity
  or catalyst information remains explicitly unknown.
- [x] Retain quotes and their sources through non-firing checks, failures and
  pause. Show overdue/expired reviews and separate review episodes. A pause
  stops subsequent requests within a running check.
- [x] Freeze paper baselines and benchmark choices, include declined ideas,
  preserve asset identity and adjustment continuity, and measure price returns,
  drawdown, missed opportunities and research usage without inventing execution
  or thesis success.
- [x] Lead each case with a manager memo and candidate-specific verdict. Compare
  alternatives; keep evidence, calculations, workflow, history and simulations
  expandable. Watchlist cards select the correct candidate.
- [x] Keep account amounts, proposed allocations, marginal exposures and sizing
  formulas behind explicit local disclosure. Keep the office optional.
- [x] Remove unused demo fixtures, unreachable legacy frontend view code,
  obsolete constructor fallback, unused imports/parameters and superseded
  presentation styles. Preserve historical readers and shared scenario storage.

## Verification

Independent Astra review passed the bounded financial/reference/cash tests,
evidence adversarial tests, and monitoring/learning tests. Positive controls
include saved long and short recommendations using both local and previously
supplied facts. Negative controls include wrong issuer/metric/period/basis,
unsupported valuation inputs, inconsistent aliases, unbound references,
optimistic short fills, stale gap resolution and incorrect cash-rate units.

The frontend passes TypeScript with unused imports, locals and parameters
checked. A production build and actual browser navigation passed. The reviewed
memo has no horizontal overflow or overlapping header; calculated target,
invalidation, proposed allocation and privacy state agree across views.

An isolated paused copy contains a complete long idea, complete short idea,
blocked idea, overdue review and a measured synthetic paper outcome. Read-only
API checks confirm these states without running models or data collection.
The private pre-upgrade backup was restored successfully and original research
records were compared by hash.

## Final integration

- [x] Complete the large saved-case projection check after removing repeated
  per-bar identity scans during paper-baseline creation. The four archived cases
  project in about five seconds; a 600-bar review performs one identity scan
  instead of 600 and returns the same result.
- [x] Repeat the full backend suite, production build and launcher/backup/restore
  smoke check. Final verification passed 439 backend tests, TypeScript,
  the production build and a clean startup/backup/restore/shutdown exercise.
- [x] Append code-only reviews of the four saved cases, verify originals and
  private portfolio records are unchanged, and restart the local service paused.
  Original reports, sources, attempts, facts, frozen inputs, accounts, balance
  observations and positions match their pre-upgrade hashes.
- [x] Complete the final blocker ownership and research-assessment label
  correction, preserving the original decisions and research records. An
  independent 66-test review and nine before/after comparisons confirmed
  unchanged investment fields. The four archived cases received an append-only
  correction after the isolated rehearsal passed.
- [x] Repeat the source-distribution privacy gate after the final documentation
  and implementation edits. The source manifest and 161 archive entries passed
  with no private-value or credential matches. Local data, credentials, original
  private brief and QA artifacts are excluded; nothing was published.

## Deliberate limits

Scenario values are conditional assumptions, not calibrated probabilities.
Unknown fees, taxes, dividends, borrow, FX, identity or account terms remain
unavailable. Digital PDF extraction does not imply OCR. Archived decisions are
not silently rerun with new models; code corrections retain their original
inputs and are retrospective for paper performance. No profitability or actual
trading performance is claimed.
