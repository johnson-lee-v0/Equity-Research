# Earnings materials and numerical context

The latest-earnings recipe (`earnings.v2`) collects the material published for an
earnings event, then puts historical numbers beside management's commentary. It
runs from a ticker without requiring the reader to locate documents or write new
research prompts.

## Evidence collection

The collector checks the call transcript, earnings release, financial supplement,
presentation, prepared remarks, shareholder letter, earnings announcement 8-K and
exhibits. A period-matched 10-Q or 10-K is another possible source; its publication
is not a prerequisite for reading the earnings package. Narrative comparison is
an optional enrichment when comparable current and previous reports are readable.

Every accepted document is fetched through the existing bounded public-source
connector and archived in Evidence. Issuer identity and event date/period must
match. A document on a shared investor-relations CDN needs a verified issuer page
linking to the exact document, with that link page retained as provenance. An
unreadable PDF or unavailable supplement remains an explicit coverage note.
Collection follows observed links across at most four issuer pages and two hops,
then uses a narrow locator pass. One bounded recovery pass can recover a failed
search without discarding sources already verified.
For an Item 2.02 earnings 8-K, the collector follows actual HTML/PDF exhibit links
within that exact accession. It retains the wrapper as provenance for a supplement
that states its quarter but does not repeat the issuer name or announcement date.

`materials` describes the latest event's available sources. `comparison_gaps`
and `material_gaps` explain optional coverage. Historical sources are kept in the
trend package with their own reporting periods, rather than represented as new
material for the latest quarter.

## Historical trend analyst

An eighth workflow agent follows collection. It seeks the latest six quarters and
the last five completed fiscal years, plus earlier guidance for those years. It
can follow observed transcript archive links or use bounded source discovery.
Fetched issuer identity, publication dates and fiscal periods are verified before
source text enters extraction. A separate extraction pass has web and command
tools disabled and receives only archived source excerpts.
Retries can reuse a completed extraction only when the configured model, output
schema and entire prompt match, allowing a one-to-one rename of immutable source
IDs. This comparison includes every supplied passage, date and fiscal period.
Reuse writes its own provenance record and reruns the current numerical checks;
changed evidence requires fresh extraction.
Raw extraction receipts are independent of the numerical validator version. A
validator-only correction rechecks matching archived candidates without another
model call. Compatibility with older receipts still requires the exact archived
content, model, schema, prompt and document metadata; it cannot admit changed text.

The supported measures are separately identified: US/Canada renewal rate,
worldwide renewal rate, paid-household membership growth, quarterly net-sales or
revenue growth, reported gross/operating margins, management-reported annual
capex, and SEC cash purchases of property, plant and equipment. Coverage depends
on what each issuer actually reports. The schema can be extended with further
explicit metric definitions; it does not treat every percentage as interchangeable.
When year-end calls lack a numerical forecast, one bounded follow-up checks up to
four missing years' first-quarter calls and runs a small capex-only extraction.
The total historical document limit still applies. Unverifiable guidance remains
missing instead of borrowing a later forecast or inventing an initial estimate.

Each accepted observation retains its value, unit, fiscal period, actual/guidance
classification, exact quote, source ID, URL and publication date. Validation checks
that the quoted number and period appear in the retained source. Missing periods
remain missing bars, not zero. Quarterly, annual and year-to-date measures must not
be combined. Fiscal labels are preserved even when they differ from calendar years.
Qualifiers such as “approximately” and “under” remain visible. A bounded amount
is not treated as an exact actual for a forecast-error calculation.
Directional wording binds the sign to the quoted number: “down 1 percent” means
−1%, not +1%. Reported and currency-neutral growth remain separate. An explicit
issuer statement that quarterly revenue was flat on a reported basis can support
0% at the issuer's reported precision, with its original quotation retained.

SEC cash purchases of PP&E are a separate basis and are not automatically equated
with a company's broader capex measure. This prevents a misleading guidance
comparison that subtracts unlike definitions.

Annual spending now starts with the shared SEC companyfacts archive, before
narrative extraction. The same verified archive can supply later valuation
preparation without a second request that day. The direct cash-PP&E concept
endpoint remains a fallback. The deterministic projector in
[`capex_facts.py`](../backend/app/research/capex_facts.py) retains the first
eligible filing vintage, exact JSON quotations, accession, annual reporting
dates and explicit USD units. Its filing cutoff is bounded by both the research
date and source observation date. A 10-K filed after the earnings call can be
used when it was available by that research cutoff. Quarterly or year-to-date
cash flows are never treated as annual actuals.

For issuers that explicitly define reported capex to include finance-lease
principal, the projector can calculate cash PP&E plus `FinanceLeasePrincipalPayments`.
Both operands must share the issuer, reporting start/end, accession, filing date
and form. Conflicting operands are rejected. A retained, hash-verified statement
from that same fiscal year's closing call or release must establish the
definition; today's definition cannot silently be applied to an older year.
The chart labels this a **calculated actual**, retaining both source quotations,
the formula and the issuer's definition. Guidance comparisons require matching
explicit lease-inclusive wording. Cash spending remains a separate series.

Before leaving a reported period blank, the trend agent makes one bounded
investor-relations / earnings-exhibit recovery for missing net-sales growth and
gross margin, missing annual cash spending, and missing management annual actuals
when that measure has been observed. Annual recovery also requests issuer-hosted
annual reports and 10-K cash-flow/capex tables rather than assuming a closing
call states full-year spending. Supported release tables are parsed
deterministically. An explicit annual management statement can be parsed when
it names USD, the annual period and the actual amount; unknown layouts remain
parser gaps. A gross
margin calculated as `(net sales − merchandise costs) / net sales × 100` retains
the exact statement rows, inputs and rounding. A latest cash-flow release can
supply the current annual cash-PP&E amount before the 10-K/XBRL feed arrives;
its currency is independently bound to the exact comparative USD fact in the
archived SEC concept. A missing bar explains whether collection or parsing is
unresolved. Completed historical quarters never become projections.

Diluted EPS follows the same distinction between missing evidence and a parser
gap. When a release labels the statement only in dollars, its exact same-table
comparative EPS can establish currency through an independently bound SEC fact
with the same issuer, value, dates and GAAP diluted basis. Both source hashes and
the currency proof remain in the assessment. This permits a newly reported
annual EPS to supersede an older filing without assuming that a US listing means
USD. Quarter-only release tables can use exact prior-quarter SEC comparatives.
The supported monthly vertical layout keeps every date, EPS cell and percentage
change column aligned. Month-based periods use actual calendar starts and ends;
quarterly EPS is never treated as annual EPS. Explicit non-USD or conflicting
currency labels cannot be replaced by a USD comparative match.

## Guidance versus actual spending

The capex view distinguishes completed-year actuals from future guidance. Dashed
bars show forecasts; ranges retain both endpoints, with their midpoint used for
the bar height and variance calculation. The track-record table pairs the earliest
captured dated guidance with the same fiscal year's actual spending on the same
measure. Guidance published at or after the actual result is excluded.

“Earliest captured” does not claim to be the issuer's first forecast. Captured
revisions are retained. Actual minus guidance midpoint is the displayed variance;
a positive difference means more was spent than planned. The summary counts years
above, below and within the guidance range. This sample describes historical
forecast errors, not the accuracy of a new forecast.

Explanations come from quoted management statements about spending changes,
project timing, investment mix or costs. General growth commentary is not proof
of the cause of a historical forecast miss. Where a cause cannot be established,
the reader says so instead of attributing deliberate underestimation.
The comparison exposes publication dates and later captured guidance so a reader
can inspect how expectations changed. A later observation is not automatically
called a revision when the forecast amount is unchanged.

## Reader and saved reviews

The former “For your thesis” column is replaced by a shared trend explorer.
Growth, margins, earnings/cash and CapEx/guidance categories retain every supplied
series; unfamiliar saved measures remain under Other saved figures. This is a
read-only presentation layer, not an expansion of the live collector’s supported
metric contract. Metric selectors keep different definitions separate, bar labels
show the numbers, and Values & sources exposes the evidence table. Source-bound
notes remain visible for actuals as well as forecasts, including unusual tax items
and accounting-basis differences. Named numerical calculation inputs and their
retained source links are inspectable. An incomplete year’s review cutoff is
labeled “Unreported as of,” never as an actual-result publication date. Charts include a zero baseline,
retain negative values and provide accessible descriptions. The complete call,
question/answer navigation and exact source jumps remain available.

The call becomes readable while historical research is still running. Historical
collection failure leaves the call and available materials intact and retryable.
An interrupted search for optional earnings attachments is also retryable. A
completed search with an unavailable document is a coverage note, not a gate.
Earlier `earnings.v1` packages remain readable without rewriting their original
sources or any linked investment-research case. “Refresh materials & trends” starts
a new current-version review. New ticker requests run the full recipe automatically.
`POST /api/research-workflows/runs/{id}/refresh-sources` creates a separate source
coverage revision for the same verified earnings event. It verifies the original
source hashes and any linked assessment snapshot, retains call analysis, retries
missing official filings and earnings exhibits, and reprojects retained SEC
financial facts. Historical extraction candidates are revalidated against their
original content hashes, metadata, namespace and schema, without another model
call. The revision freezes its research cutoff at creation; the previous cutoff
is retained as provenance. Direct offline replay defaults to the prior cutoff.
It then reruns primary recovery, filing comparison, context and publication.
The old package and its research handoff remain unchanged. Optional
bounded candidate URLs still undergo ordinary fetched issuer/date/period checks;
without them, the source locator finds candidates automatically. Checking for a
new earnings event continues to use the normal latest-earnings recipe.
An explicit empty candidate list skips primary-source discovery, allowing an
archive/fetch-only correction without model usage. Missing current-year actuals
caused by an older earnings-date cutoff can therefore be repaired from an
already-retained later filing. No historical research decision is rewritten.

The targeted regression checks are
[`test_capex_facts.py`](../backend/tests/test_capex_facts.py) and
[`test_earnings_primary.py`](../backend/tests/test_earnings_primary.py).

The resulting source package is passed to the existing investment-research flow,
including trend sources and optional coverage notes. It continues to honor the
firm's paused state and does not alter holdings or place trades.

## Earnings estimates and valuation context

The price-target card adds a separate fiscal-year diluted EPS bridge. Solid bars
are reported standalone quarters; amber outlined bars are estimates for quarters
that have not been reported. Once three comparable quarters are available, the
remaining quarter uses prior-year seasonality and the growth in those reported
quarters. The calculation, assumptions, reporting dates and source references are
visible. This is a model estimate, not management guidance.

A missing historical observation is labeled “Evidence missing.” It is not called
an unreported quarter, converted to zero or filled with a forecast. A completed
year retains four reported quarters when available and the separately reported
annual EPS, whose weighted share count and rounding may differ from their sum.

Historical P/E, current reported earnings and the future price target remain
distinct. The valuation view shows the selected scenario's implied value on
current earnings, its future target, and the years between the earnings baseline
and forecast period. See [Valuation history and earnings bridges](valuation-history.md)
for source binding, arithmetic and coverage limits.
