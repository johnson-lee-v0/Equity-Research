# Portfolio-manager review

Review dated 17 September 2026. These findings motivated the manager upgrade.
Implementation status and acceptance evidence are recorded in
[the completion ledger](manager-upgrade-progress.md).

ResearchCouncil already has the foundations of a useful research journal:
open questions, source-bound Reddit screening, retained evidence, versioned
decisions, local market calculations and a visible research history. Its
weakest link is converting that work into a defensible allocation decision.
More agents or more reports would not solve that problem.

## What the manager must be able to decide

An idea should answer: What is the market missing? Why should it reprice, and
when? What is the reasonable entry? What happens under an adverse outcome?
Why is this preferable to another idea or retaining cash? How much capital is
appropriate in this portfolio? What observation changes the decision?

The front page should contain a concise investment memo with those answers.
Detailed evidence, financial arithmetic, model work and historical corrections
belong immediately behind it, available for inspection without dominating it.

## Priority changes

| Priority | Finding | Required change |
| --- | --- | --- |
| Critical | A citation or matching number can be mistaken for verified financial support. | Bind the fact to issuer, metric, period, currency, scale and accounting/share basis. Separate what a source asserts from independent corroboration. |
| Critical | A proposed valuation can look authoritative before its factual inputs have been validated. | Compute valuation in code from supported historical inputs and explicitly labeled assumptions. Show a suitable EPS/DCF/EV or underlying-asset method, scenario sensitivity and limitations. |
| Critical | Buying capacity, proposed allocation and executed holdings can be conflated. | Keep maximum permitted quantity, recommended quantity, funding readiness and confirmed holdings distinct. Apply adverse-fill, loss, portfolio and short feasibility checks. |
| High | An investment judgment can be obscured by operational or funding blockers. | Keep one verdict, separate readiness states, and a material blocker with an owner and next action. Missing evidence is not automatically a bearish judgment. |
| High | Old prices or event dates can remain prominent without a clear review obligation. | Use observation-date freshness, dated quotes, overdue reviews, source refresh and distinct review episodes. A scheduled event date is not proof it occurred. |
| High | Several candidates are hard to compare as capital-allocation alternatives. | Compare entry, horizon, payoff, downside basis, portfolio impact, next review and principal unknown together. Distinguish alternatives from an intended basket. |
| High | Research history does not by itself establish whether the process improved decisions. | Freeze prospective paper baselines, include declined ideas, compare appropriate dated benchmarks, retain unavailable results, and review thesis/catalyst resolution separately from price. |
| Medium | Repetitive Reddit posts can consume research capacity without adding information. | Cluster related theses conservatively and show transparent research priority. Keep opposing views and new material evidence distinct; never rank simply by popularity. |
| Medium | A long evidence list makes the interface look complete without identifying the decisive evidence. | Collapse the full evidence ledger and surface a few material facts, opposing evidence, uncertainty and the reason for the call. |

## Keep, reduce and defer

Keep the bounded Chief of Staff → Researcher → CIO process, with financial
calculations in code. Preserve immutable source archives, concise stage
rationales, the distinction between Reddit leads and verified facts, and local
privacy controls.

Reduce duplicate status badges, repeated summaries, raw identifiers, expanded
fact lists and workflow animation on the decision page. Keep the office
optional. Remove compiler-confirmed dead code and obsolete branches only when
their historical compatibility purpose has been checked.

Keep historical-return simulation secondary. Its distribution describes its
assumptions; it does not independently establish fair value or the chance a
thesis succeeds. Do not add more specialist agents, opaque idea scores,
automatic orders or additional simulation personalities to improve the
appearance of sophistication.

The release bar is a small set of complete, traceable cases: a supported
recommendation, a blocked idea, a short with complete feasibility checks, an
overdue review, and an honestly measured paper outcome. A large number of
completed tasks or citations is not a substitute for passing those cases.

## Assessment after the upgrade

The tool is useful as a local idea-analysis and review journal. Its strongest
feature is traceability: the user can inspect the source, stated assumption,
calculation, failed requirement and subsequent correction behind a decision.
The manager memo now makes the proposed investment argument reviewable without
opening each agent's report. A missing input remains a research limitation,
not proof that the investment itself is bad.

It is not yet an established investment process. The next priority is a small
prospective cohort of real ideas evaluated with the strengthened contract,
including recommendations, watchlist decisions and declines. Record the
decision and benchmark before the outcome is known, then review both price
performance and whether the original thesis or catalyst was correct. Synthetic
acceptance cases test software behavior; they do not demonstrate investment skill.

Keep the following limits visible during that trial:

- A cited assertion is not automatically independently corroborated. Material
  disagreements and unavailable primary evidence still require investigation.
- Scenario values depend on their assumptions. Historical simulation does not
  calibrate the probability of a future thesis or provide an independent target.
- Quote freshness, liquidity, spreads, transaction costs, borrow and comparable
  benchmark data must be assessed for the actual instrument and horizon.
  Unavailable inputs must not become zero-cost or frictionless assumptions.
- Portfolio sizing is a proposal under recorded constraints. It does not
  establish actual execution, suitability or a complete institutional risk model.
- The paused archived cases are retrospective reviews of their original inputs;
  they are not fresh research or current entry recommendations.

Do not add more agents or another dashboard until that trial identifies a
specific recurring failure. Prioritize better source coverage, clearer failed
requirements and measurable decision quality over more generated analysis.
