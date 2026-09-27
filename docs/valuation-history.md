# Valuation history and earnings bridges

The earnings assessment stores numerical context with the canonical valuation.
Results and the Research Library read the same saved object. A historical review
keeps its original sources, figures and calculations; opening it does not fetch
today's prices or overwrite its assumptions.

## Sources and ownership

`valuation_context.py` compiles a verified earnings workflow, its retained issuer
release, SEC diluted-EPS records and archived raw daily prices. Source identity,
namespace, content hash and version are checked against the frozen assessment
packet. `valuation_history.py` handles reported-as-of EPS and historical P/E;
`earnings_forecast.py` builds the quarterly bridge. The provider may propose
growth and valuation assumptions, but cannot provide this trusted context itself.

An investor-relations URL is not sufficient evidence of primary authority.
`earnings_primary_policy.py` requires the saved workflow's verified SEC issuer
identity, completed acquisition stages and exact primary-release receipt. It
rechecks issuer, report date, period, source URL, content hash and archive version.
Changed, superseded or unrelated sources cannot inherit that receipt. The same
policy serves source packets, memory and evidence-gap checks.

The full canonical context includes sources and calculation components. Its
provider projection omits repeated historical EPS component lists to reduce input
size, while preserving every sample's date, price, EPS, multiple and source IDs.

## Historical traded P/E

The historical chart samples the last completed trading session of each month
over the requested five years. The current month uses its latest completed
session. Every point is:

`raw closing share price / trailing GAAP diluted EPS available on that date`

EPS uses the latest available annual report, or an explicit rolling bridge:

`last annual diluted EPS + current year-to-date EPS − comparable prior year-to-date EPS`

The annual/YTD bridge is an approximation because diluted share weights can
differ. Every dated observation retains its component values, fiscal periods,
publication cutoff and source references. Publication dates without intraday
timestamps become available on the following day to avoid using earnings before
they were public.

The chart labels sampled low, median and high; these are not daily extrema.
Missing or ambiguous earnings, nonpositive trailing EPS and known unreconciled
splits exclude the affected sample. Gaps break the chart line and remain listed
with their reasons. A shorter retained history is explicitly disclosed. Raw
prices avoid dividend and future split adjustments. Share-basis checks use the
separately retained corporate-action evidence described below.

Historical trailing P/E and a selected P/E on forecast earnings use different
earnings bases. The card displays this distinction; it does not present a
historical percentile as proof that a forecast multiple is justified.

## Corporate actions and share bases

`valuation_corporate_actions.py` retrieves forward splits, reverse splits and
stock dividends from Alpaca using the existing market-data credentials. It
archives the public response, request dates and source hash, with at most three
pages of 1,000 records. A seven-year request covers five years of prices plus
the earlier EPS components. Complete same-day archives are reused. A successful,
fully paginated empty response establishes provider coverage with no returned
actions; missing, malformed or partial responses do not.

The parser uses each action's **ex-date**, not its processing date, to establish
the share-basis boundary. Requests are filtered by processing dates, and the
provider cautions that corporate-action reporting can be delayed.
[Alpaca corporate-actions documentation](https://docs.alpaca.markets/us/reference/corporateactions-1).

Historical P/E is withheld when an EPS component was published before a split
or stock dividend and the price comes afterward. Later reported EPS vintages
can restore a compatible comparison. The engine does not automatically apply
the action's ratio to financial statements. Retained ratios are evidence for
review, not permission to invent a restatement.

When coverage is absent or incomplete, the context says so. Independently of
provider coverage, an unexplained raw-close change exceeding 40% between
nearby sessions is treated as a share-basis review gap. This conservative rule
can exclude a genuine large market move; it does not call the move a split or
infer an adjustment factor. Ordinary Costco history remains available when
there are no such changes.

## Reported quarters and the missing-quarter estimate

The quarterly bridge uses standalone GAAP diluted EPS with exact fiscal dates,
including common 12/12/12/16-week retail calendars. It does not difference
cumulative YTD EPS into purported reported quarters.

For an incomplete year, the forecast is:

`prior-year quarter EPS × (1 + growth in comparable reported quarters)`

The growth comparison requires a complete set of comparable reported quarters.
If the prior standalone Q4 is absent, an annual-minus-Q1–Q3 residual may supply
the seasonal assumption. It is explicitly labeled as a modeled residual because
different diluted share counts mean it is not reported Q4 EPS. Already reported
quarters missing from the archive stay evidence gaps; they are never projected.

Solid bars show actuals. Amber outlined bars show projections. The table retains
the formula, rationale and sources. A reported full-year value is preferred to
the sum of quarterly EPS when the year is complete; the rounding/share-weight
difference remains explained.

Around splits, original reported quarterly values remain visible with an
unreconciled-share-basis explanation. The engine withholds sums and seasonal
forecasts that would combine incompatible EPS figures. A separately reported
annual figure published after the relevant action can still supply the
full-year total. Forecasting resumes when retained reports provide compatible
quarterly vintages; no quarterly EPS is silently split-adjusted by code. The
context also flags unresolved earnings-baseline share bases for the valuation
review, without changing the future-target formula.

## Implied value today and the future target

For a supported P/E method, the calculator shows two different valuations:

- **Implied value today:** current reported trailing diluted EPS × the selected
  scenario's P/E. No growth or discount rate is applied.
- **Future target:** earnings forecast for the stated fiscal period × that
  scenario's P/E, with the growth assumptions and full calculation shown.

The first is an estimate on current earnings, not a live quote or a discounted
future target. Both require a matching currency and positive reported EPS.
Selecting bear, base or bull changes the displayed P/E and today's implied value
together. The current EPS figure, earnings period and component sources remain
visible beside the numeric multiplication.

The growth exponent counts years from the historical EPS baseline to forecast
earnings. The holding horizon counts time from the assessment date to the target.
For example, moving FY2025 earnings to FY2027 is two annual growth steps even
when the investment horizon is 12 months. A fresh FY2026 baseline projecting to
FY2027 uses one step. These periods are separately labeled and checked rather
than assuming the exponent always equals the holding horizon.

## Verification

Ordinary single-company investment reviews use the same validated financial
compiler as the earnings shortcut. The analyst receives up to twelve prepared
financial facts, leaving room for three additional material claims within the
five-question contract. Code commits the prepared facts without letting the
analyst rewrite them; the final reviewer references their saved fact IDs.
Latest annual losses cannot be replaced by an older profitable year to make
P/E available. The selected method must fit the business: P/E, sales, EBITDA
with a complete equity bridge, or explicitly named equity/book-value evidence.
Unavailable operands remain explicit gaps; future growth and multiples are
identified as analyst assumptions.

The ordinary review freezes its cutoff after earnings, financial, interim-event
and market collection. Analyst retries reuse those archived observations.
They do not refetch market evidence after the frozen cutoff. Complete usable
financial inputs require a calculated final target, with one bounded correction
opportunity; the user's holding horizon remains independent of the financial
forecast span. Source hashes, versions and the compiler receipt stay attached
to the provider attempt.

Focused backend tests cover publication cutoffs, TTM arithmetic, currency
mismatches, retail fiscal quarters, missing-quarter projections, annual EPS
rounding, source corruption, issuer-release provenance, bounded corporate-action
pagination, complete empty coverage, ex-date boundaries, raw-price
discontinuities and mixed-share-basis quarter bridges. Frontend model tests
cover actual/projection/missing labels, losses and zero EPS, missing historical
months, saved source relationships and current-EPS versus forecast-EPS pricing.
Production compilation checks both Research Library and Results integrations.
`test_investment_valuation.py` exercises ordinary analyst-to-reviewer fact IDs,
source collection timing and frozen retries, missing/secondary evidence, a
loss-year baseline, and SEC company-facts-to-final-decision paths for P/S,
EV/EBITDA and P/book. These tests use synthetic archived evidence and provider
responses; they do not certify live coverage for a particular issuer.
