# ResearchCouncil: investment-manager review

Reviewed 21 September 2026 against source code, the retained decision records,
and a paused local inspection copy of the interface. These are recommendations,
not a claim that the changes below have been implemented. The five-question/Laya integration has a separate acceptance record; its
software checks do not establish investment performance.

ResearchCouncil has a credible foundation for an investment research journal.
Its strongest feature is explaining where a statement came from, what was
calculated, and why a recommendation was blocked. It is stronger at checking
whether a report is supported and an allocation fits limits than at establishing
whether the opportunity deserves capital.

I would use it to organize and challenge ideas. I would require improvements to
the economic decision rules and prospective evaluation before relying on its
recommendations to allocate capital.

The current archive contains four researched cases with eight candidate
decisions: seven Watchlist and one Decline, with no supported entry or future
target recorded. These are older retained cases, subsequently reprojected under
stricter checks; they are not a fresh trial of the current workflow. They show
that unsupported entries are being withheld, but do not demonstrate that the
system can finish a useful investment assessment. The paused database has
paper baselines but no recorded outcome observations. That is consistent with
the pause and is not, by itself, a monitoring defect.

## Keep

- The small Chief of Staff → Researcher → CIO process and calculations in code.
- Retained sources, precise citations, issuer/metric/period checks, explicit
  assumptions and unknowns, and immutable decision history.
- The distinction between a Reddit opinion, a supported source assertion and
  an investment conclusion. A citation match does not prove the source is true.
- Separate investment judgment, research readiness and funding readiness;
  maximum affordable shares must remain distinct from proposed shares.
- Watch conditions, dated reviews, frozen paper baselines and declined ideas.
- Local operation and default privacy of portfolio values.

## Change in priority order

| Priority | What is lacking | Concrete improvement |
| --- | --- | --- |
| 1 | A positive base payoff can satisfy the economic gate without a complete adverse scenario or return hurdle. | Require plausible downside, base and upside assumptions, horizon, material cost treatment and comparison with cash or an explicit strategy hurdle. Do not manufacture scenario probabilities. |
| 1 | The first supported valuation method becomes the displayed method; conflicting methods can receive a generic reconciliation. | Require an explicit primary method and reason. Explain material disagreement. Reordering identical methods must not change the decision. Show what operating result or multiple the current price implies. |
| 1 | Supported facts do not establish that the market is wrong. | Require an expectations gap: market expectation or clearly labeled inference → our different assumption → decisive evidence → valuation impact → disconfirming observation. For Reddit, say which original claim survived investigation. |
| 1 | The proposed share count is a model suggestion checked against capacity, rather than a repeatable allocation policy. | Define starter/full position rules, adverse-scenario portfolio loss, conditions to add/reduce, cash needs and overlapping risks. Show why the proposed quantity is below its maximum. |
| 1 | Watchlist can become a holding area for incomplete research. | Distinguish waiting for a price, waiting for a catalyst and waiting for evidence. Give each idea one decisive condition, an owner, next review and an expiry/reopening rule. A historical low is an alert reference, not automatically a justified entry. |
| 2 | Stated exit and invalidation conditions are not all automatically covered by the monitor. | Show automatic/manual/unavailable coverage for each material price boundary, operating KPI and catalyst. A date passing should prompt review, not certify that an event occurred. |
| 2 | Portfolio checks emphasize position/sector/cash limits, with limited common-risk analysis. | Add simple joint stress cases for shared drivers such as rates, commodity prices or capital spending, plus liquidity and executable-price assumptions. Ten holdings can still depend on one economic outcome. |
| 2 | Outcome tracking exists but has not demonstrated investment skill. | Evaluate a prospectively frozen cohort, retaining failures and declines. Compare predicted operating KPIs with observations, track catalyst timing, distinguish thesis error from valuation error, and check whether the stated conditional entry would actually have occurred. |
| 2 | A small displayed report can still require substantial model work. | Show per-case token use, elapsed time and continuation count. Give the user an explicit research budget and a clear stopping reason; do not imply that five visible questions guarantee cheap or fast research. |

## Simplify the experience

The decision page should answer in one screen: **What should I do, why, at what
price and size, what can I lose, and what changes the call?** Five questions are
a useful presentation framework if the verdict and next action remain above
them. Every material number should open its source or calculation.

The inspected WOOF memo illustrates the remaining friction: it shows an
unavailable valuation and missing thesis fields, but elevates portfolio inputs
as its top blocker. It repeats invalidation language and retains publication
bookkeeping among research gaps. The most important unresolved investment
question should come first; account reconciliation should have its own status.
The journal also describes a not-started paused case as research in progress.
Those messages should accurately identify what is running, waiting or stopped.

Merge Questions and Results into one case journal with source/status filters if
their current separation continues to confuse navigation. Retain Reddit as an
intake queue and Watchlist as an action queue. Keep the office optional for
understanding the architecture; do not make the investment workflow depend on
it. Collapse repeated summaries, long agent transcripts, raw identifiers,
generic blocker lists and the complete evidence ledger.

Keep historical-return simulations as secondary risk context. Their resampled
price paths do not establish business fair value or the probability that a
catalyst succeeds. Do not add more agents, a personality simulation, an opaque
idea score or more asset classes until the existing path proves useful.

## Laya and bounded research

Model agreement is a recorded process result, not independent confirmation of
an investment thesis. Laya's short context and unproven financial classification
quality require explicit unavailable/disagreement states. It should not
silently discard Reddit ideas or certify facts. Evaluate its incremental error
detection before promoting agreement as a reason for user confidence.

Five displayed questions and a compact fact packet are useful. A fixed
15-fact budget shared by three candidates is a resource limit, not a claim that
each candidate received enough research. Prefer one thoroughly assessed idea
with lighter alternatives when the available budget cannot support three
valuations. Missing material inputs must remain visible. A verified fact alone
must not label a question complete when a decision-critical unknown remains.

In one isolated synthetic acceptance case, the actual Astra call took about
nine minutes and reported 51,844 input tokens and 13,967 output tokens. This
is an integration observation, not a representative performance benchmark.
It is enough to make cost and turnaround part of the next product evaluation.

## Release test for usefulness

Use a small prospectively recorded set of real cases containing an actionable
long, an unattractive idea, a price-dependent watch, an evidence-dependent
watch, and a short with actual feasibility evidence. For each, a user should
find the decision within 30 seconds and trace the decisive assumption without
reading all agent reports. Include cases where the system correctly refuses
to size or forecast. This tests usefulness; a small sample does not establish
profitability.

## Code evidence

- `backend/app/research/decisions.py`, `_recommendation_gate`: supported thesis,
  usable payoff and positive directional base case; existing freshness,
  identity, allocation and evidence checks.
- `backend/app/research/valuation.py`, `build_valuation` and `calculate_payoff`:
  first supported method selection, generated reconciliation and unknown costs.
- `backend/app/research/calculations.py`: proposed quantity versus calculated
  maximum, with a required rationale.
- `backend/app/research/portfolio_risk.py`, `check_portfolio_limits`: cash,
  position, sector and holding limits; retained common-theme labels.
- `backend/app/research/watchlist.py`, candidate trigger collection: explicit
  watch triggers and review dates.
- `backend/app/research/learning.py`: frozen close-to-close price-only paper
  comparisons, separate thesis/catalyst labels and retrospective markers.
- `frontend/src/panels/DecisionWorkspace.tsx`: manager memo, comparison, journal,
  nested evidence and five-question presentation under current integration.

The inspection did not resume research, place orders or change original records.
