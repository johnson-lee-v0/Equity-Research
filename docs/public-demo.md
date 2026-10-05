# Public demo and local research

The public-demo build restores the **complete original META research
example**, not the smaller portfolio calculator. It includes the earnings
walkthrough, source-linked call review, five research questions, comparable-multiple
valuation controls, decision framework, watchlist and connected 3D/text
notebook. Portfolio and Congress remain explicit empty public views: no personal
holdings, balances or private disclosures are included. A failed 3D download
switches the notebook to its complete text list; a failed research-entry download
shows a reload action instead of clearing the page. The text reader's connected
notes include every relationship in the graph, in both directions.

The [public URL](https://johnson-lee-v0.github.io/Equity-Research/) serves the
latest successful Pages deployment. Confirm its commit and inspect the live
page after publication; a local build is not deployment evidence.

## Acknowledgment before interaction

`frontend/src/demo/AcknowledgedDemo.tsx` shows a native modal notice on entry.
The visitor must check an unchecked acknowledgment and press **I understand —
open META** before the original `DemoApp` is mounted. Escape or **Back to
overview** leaves the interactive example closed. The overview can reopen the
notice. Acceptance is page-local React state: it is not stored, transmitted or
reused after a reload. The public demo has no account or trading connection.

The notice explains education/research use, the absence of personalized advice
or recommendations, stale or erroneous inputs, hypothetical scenarios, and the
possibility of total investment loss. It disclaims accuracy and suitability
warranties without promising legal immunity. **Acknowledgment is not a release
of liability or a waiver of legal rights.** It also does not grant permission
to republish third-party content. Persistent notices remain visible in the app
and alongside valuation controls after the modal closes.

## Agent engineering first, with an earnings-first example

The public presentation leads with the **end-to-end multi-agent research
workflow**, not NLP as a standalone project identity. After acknowledgment,
**Explore the agent workflow** uses the existing `#research` route and scrolls
to a visible, non-collapsed workflow overview before the META case. It covers
Chief of Staff intake, public source discovery and collection, earnings and
fundamental research, deterministic validation and valuation, CIO challenge
and review, and retained company memory. Agent roles describe responsibilities;
they do not imply a separate model execution for every stage. The overview links
to the canonical [research workflow](research-workflow.md).

The META walkthrough remains open by default. **Inspect earnings-call
analysis** is an explicit secondary action, and the call review still appears
before the reported financial snapshot and trend explorer. Business themes
and cautionary-language views retain analyst questions, management answers,
speaker context, original transcript pages and explicit open questions. NLP is
one inspectable component of the larger engineering system, not its headline.

`#earnings-call` opens the call review; `#earnings` is an alias for the same first
step. `#financials` opens its reported-results section. `#valuation` goes directly
to the third walkthrough step. `#five-questions` and `#decision` select the other
research steps. These routes do not bypass acknowledgment. Ordinary section
routes, including `#research` and `#strategy-testing`, remain available.

The static call fixture declares `analysisType: 'editorial_reading'`: its twelve
editorial topics across seven analyst exchanges are source-bound paraphrases,
not output from a live NLP execution or an automated sentiment score. The
presentation illustrates the local app's research workflow without claiming
the public example generated these classifications, executed agent jobs, or
replays a recorded autonomous research run. “Still unclear”
records an editorial follow-up, not a statement or promise from management.

Valuation starts with the supported multiple method, META's own historical
ratios and an editable target-multiple assumption. The public comparison is
five annual issuer observations per available metric, **not a verified peer
set**. Historical averages do not silently become the target. Growth appears in
a closed **Optional: test growth sensitivity** disclosure; it changes the
twelve-month scenario, not today's reported-baseline implied value. Sensitivity
edits do not rewrite the published decision or watchlist. See the
[comparable-valuation contract](comparable-valuation.md) for source, accounting
basis and peer-verification boundaries in the full local application.

## Real data versus assumptions

The original case uses Q2 FY2026 results (period ended June 30; released July 29),
a September 25, 2026 market close, and a September 26 dated research snapshot.
It is historical evidence, not a live quote or a claim to current research.
The September 8 Muse announcement is a later event, not an explanation for
already-reported Q2 revenue. Undisclosed standalone product economics remain
explicit evidence gaps.

Financial data and source-bound summaries were checked against issuer releases,
filings and the official call transcript. Growth rates, valuation multiples,
scenario weights and review thresholds are authored assumptions, not reported
facts or expected returns. The arithmetic stays deterministic in code. Date,
unit and source labels must remain visible; do not relabel these observations
as current when restoring or changing presentation.

The original news component depended on an absent snapshot. The restored static
build links to original publishers instead of inventing a news feed or connecting
to the private API. No RSS refresh is scheduled.

## Content and licenses

Original project code is under the root [MIT license](../LICENSE). Third-party
software, source material and provider observations retain their own terms.
Issuer summaries are editorial paraphrases, with a small number of attributed
short quotations linked to their original transcript pages. No full transcript,
source archive or provider dataset is bundled.

The original five-row historical ratio excerpt retains StockAnalysis attribution.
Its [terms](https://stockanalysis.com/terms-of-use/) distinguish attributed snippet
reuse from full republication and competing database/product use. The intended
public use still deserves a rights review before publication; the acknowledgment
supplies no additional permission. Financial facts and the expression presenting
them should not be conflated. The [US Copyright Office](https://www.copyright.gov/help/faq/faq-protect.html)
distinguishes facts from expression, and its [fair-use guidance](https://www.copyright.gov/fair-use/)
does not establish a universally safe quotation length. Tests on excerpt lengths
are technical checks, not legal permission tests. No issuer/provider endorsement
or affiliation is implied.

`LICENSE.txt` and `THIRD_PARTY_NOTICES.txt` remain required build outputs. The
latter is generated from packages represented in Vite's final chunks and emitted
assets, including transitive dependencies and build-injected helpers. A dependency
without a discoverable license fails the build. The pinned React Three Fiber
fallback license remains version- and hash-checked in
`frontend/dependencyNotices.mjs` and `frontend/third-party-licenses/`.

Original modules were recovered from commit
`fd4dbd1fa1b8662977f349326bcb4c6ad8f7d0f6`. The external preserved originals were
not modified. The fictional modules remain available as historical source but
are not imported by the restored public entry. Prior commits, releases, cached
deployments and the separate full local workspace were not rewritten.

## Build and inspect

```sh
pnpm --dir frontend install --frozen-lockfile
node --test frontend/tests/*.test.mjs
pnpm --dir frontend run build:demo
python scripts/check_public_demo.py frontend/dist/demo
python -m http.server 8080 --bind 127.0.0.1 --directory frontend/dist/demo
```

The compile-time `__PUBLIC_DEMO__` flag selects the acknowledgment wrapper;
acceptance opens the original Meta demo. Relative assets and hash navigation
support GitHub Pages repository paths. The ordinary `build` still builds the
private local application; never upload that output as the demo.

The artifact guard permits only HTML, JS, CSS, favicon and required license files.
It requires the Meta entry and acknowledgment copy, rejects local API/runtime,
live feed and telemetry markers, and rejects source maps, databases, source
archives and symlinks. Source/dependency tests separately check that private
application modules and storage/telemetry are not imported. These tests establish
technical boundaries, not exhaustive legal compliance or investment accuracy.

## Requests, privacy and publication

Demo controls use browser memory and static calculations. They do not send
questions, positions or acknowledgments to a service. No application analytics,
external fonts, RSS proxy or market-data API is used. Hosting providers can keep
ordinary request logs. External source links open the provider's site and are
subject to its policies; the demo makes no third-party retention promise.

The [Pages workflow](../.github/workflows/pages.yml) builds and inspects
`frontend/dist/demo`, never the repository root, backend, database or archive.
There is no scheduled feed job. Build access remains read-only; only deployment
receives Pages permissions. Publish only when authorized, and verify the Actions
deployment and deployed URL before claiming the live site has changed.
