# Comparable valuation

New assessments can select the valuation basis appropriate to the company:

| Method | Intended use | Code-owned future share-price calculation |
| --- | --- | --- |
| `ps_multiple` | Meaningful revenue with no sustainable earnings | Forecast revenue × P/S ÷ diluted shares |
| `ev_ebitda` | Stable, positive operating earnings | (Forecast EBITDA × EV/EBITDA + cash − debt − preferred − minority interests) ÷ diluted shares |
| `nav_multiple` | Asset-heavy companies and financial institutions | Forecast total equity NAV/book equity × P/NAV or P/book ÷ diluted shares |

P/S is an equity multiple, so debt is not subtracted again. Book equity and tangible book equity must be explicitly named; neither is silently labelled appraised NAV. Financial institutions generally require equity-based valuation and asset-quality/return-on-equity review, rather than an operating-company EV bridge. The distinction follows [Damodaran's financial-services valuation discussion](https://pages.stern.nyu.edu/adamodar/New_Home_Page/littlebook/financialsvccompanies.htm).

Every method requires a dated source-bound baseline, unscaled compatible units, output currency, forecast fiscal period, diluted shares and explicit bear/base/bull annual growth and exit-multiple rationales. `forecast_years` describes the baseline-to-forecast interval; `horizon_months` describes the holding period. A 12-month target defaults to one annual growth step. Today's implied value uses the dated reported baseline without growth and source-bound reported balance-sheet/share operands; it is not a live quote or a discounted future target. If the bridge or share count is supplied only as a future assumption, today's value remains unavailable with the missing reported operands named. A forecast repayment or buyback cannot change today's reported capital structure.

Missing debt, preferred claims or minority interests are never interpreted as zero. Explicit future bridge assumptions need their own rationale and remain labelled assumptions. The calculators reject mismatched issuers, dates, scales, currencies, metrics, contradictory inputs and unvalidated facts. When reported EBITDA is absent, exactly matched operating-income and cash-flow D&A facts can support a labelled EBITDA proxy. Its definition must be checked against adjusted EBITDA and peer accounting choices.

## Acquisition and comparisons

The regular earnings preparation adds one bounded SEC company-facts request. Exact original JSON observations are bound to the verified issuer, taxonomy, fiscal dates, currency, source hash and version. The financial compiler passes saved fact IDs into the assessment, without repeating the entire company-facts JSON line for each observation.

When market collection is enabled, the existing local Codex discovery path proposes at most three peer tickers. Each candidate must independently pass SEC ticker/CIK identity verification. Primary financial archives and raw price history are then retained. Code calculates available P/S, EV/EBITDA and P/book comparisons from that evidence; a discovery suggestion is not a verified multiple. One missing peer does not block the rest of the assessment. With market collection unavailable, the comparison gap remains explicit.

Peer observations have their own price dates, reporting dates, sources and accounting basis. Trailing and forward peer observations are not pooled. The investment reviewer chooses an exit multiple with a rationale; the peer median never silently becomes a target. Missing peers, unsupported EBITDA or absent appraisals are shown as gaps, not generated numbers.

The UI displays separately sourced peer observations and permits switching among supported valuation calculations. Changing the displayed method does not change the saved investment decision.

## Historical chart

Own-company history is distinct from peer comparisons. P/E, P/S, EV/EBITDA and P/book use monthly samples in the preceding five years, only with financials reported before the price date. Annual + current YTD − comparable prior YTD supplies trailing duration metrics. Balance-sheet values must be sufficiently recent. Missing periods break the line; later restatements cannot leak into earlier samples.

The chart fits its vertical axis to the observed range and the average ±1, ±2 and ±3 population-standard-deviation lines. It does not force a zero baseline. Dates, sample counts, sources and gaps remain visible. Missing months are excluded, not zero-filled. A shorter retained record is labelled as available samples within the five-year window. The bands describe historical variation, not forecast probabilities. P/book history is accounting equity, not appraised NAV history.

Changes apply to future assessments. Old saved decisions retain their evidence and prices; the existing saved P/E charts can immediately show the new chart presentation without rerunning a model.

## Optional provider history

The SEC company-facts API includes standard-taxonomy observations applying to
the whole entity; it does not expose every class-specific fact from a filing.
The compiler accepts both `us-gaap:CommonStockSharesOutstanding` and
`dei:EntityCommonStockSharesOutstanding`. It never substitutes weighted-average
diluted shares for actual market capitalization. Missing share-class bridges,
total debt, preferred claims or minority interests can therefore leave an
otherwise valid P/S, EV/EBITDA or P/book series unavailable.
[SEC API scope](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

New real-workspace assessments can additionally retain one dated Stock Analysis
quarterly ratios page. This supplies separately labeled provider-computed P/S,
EV/EBITDA, P/book and P/tangible-book series without a new API key or model call.
The provider's quarter-end financials may have been restated; these are **not**
reported-as-of samples and cannot certify financial operands or enter a
point-in-time backtest. No provider value is used to fill a missing SEC sample.
The UI uses one entire series at a time, with separate sample averages/bands.
An available SEC series remains the default; a separate provider option is
offered. Where no SEC sample is available, the provider series supplies the
chart, with attribution and its retrieval date immediately visible.

“Update multiple history” updates only this supplementary market context.
GET `/api/valuation-history/{ticker}` reads the selected namespace's saved
archive. POST `/{ticker}/refresh` is guarded, real-workspace only and reuses a
same-day archive. A refresh checks robots.txt and performs one bounded public
page fetch, validates ticker, currency, exact dates and column alignment, then
stores a new immutable observation. Older decisions, targets and source
versions remain unchanged. Provider failures leave earlier context available.

P/book is an accounting-equity comparison; P/tangible book removes intangible
equity under the provider's definition. Neither is silently relabeled P/NAV.
An actual P/NAV valuation still requires a dated issuer-reported or appraised
NAV with a clearly stated asset/claim basis. Free ratio sites do not supply that
missing appraisal. Provider access follows its
[terms](https://stockanalysis.com/terms-of-use/) and
[robots policy](https://stockanalysis.com/robots.txt); the public META case uses five attributed year-end observations per metric,
not a complete provider table or generated monthly history. Annual sampling is
labeled explicitly and missing years remain gaps; the mean and deviation bands
describe available year-end samples. See [public demo](public-demo.md).

Focused verification: `test_secondary_valuation_history.py` checks vintage and
identity rejection, alignment, missing values, robots denial, reuse, namespace
isolation and actual DEI shares. `valuation-history-provider.test.mjs` checks
visible attribution, accounting-operand separation and refresh controls.
