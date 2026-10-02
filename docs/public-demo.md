# Public demo and local research

The [public demo](https://johnson-lee-v0.github.io/Equity-Research/) demonstrates a
research workflow using **Cedar Workshop, an entirely fictional company**. Its
numbers, questions and conclusions were authored for teaching on October 2, 2026.
They do not describe an issuer, listed security, analyst, market quotation or
actual investment result. This is a static application with browser calculations,
not a live agent session or a source of current financial advice.

## What a visitor can try

- An earnings walkthrough with four invented quarters, a revenue chart and a
  table showing operating profit, operating cash and capital spending.
- Five research questions that separate the example's assumptions from what
  would need independent verification for a real company.
- A valuation sensitivity control: invented EPS × one year of assumed growth ×
  an assumed P/E multiple. Its three teaching scenarios are not probabilities,
  expected returns, performance history or a worst-case loss limit.
- An example watchlist review threshold and an authored opposing argument.
  Neither creates an order, recommendation or personal allocation.
- A connected fictional notebook, with 3D navigation and a text-list fallback.
- Links to original filings, disclosure registers and news publishers. No
  headlines, articles, transcript excerpts or third-party market datasets are
  copied into this public build.

The entry and every financial scenario show visible education/research, advice,
loss and verification notices. Investments can lose their entire value; data,
calculations and model outputs can be wrong or stale. A disclaimer does not
establish that a use is lawful, suitable or accurate.

The local app and its earnings/research capabilities remain separate. It may
store records its operator supplies and use configured providers; its data and
credentials are not inputs to the public build. Public Portfolio and Congress
pages show that boundary without asserting anyone's actual holdings or conflicts.

## Content and licenses

Original project code is under the root [MIT license](../LICENSE). Third-party
software, data, models and source material retain their own licenses and terms.
The public footer links to `LICENSE.txt` and `THIRD_PARTY_NOTICES.txt`. The latter
is generated from packages represented in Vite's final chunks and emitted assets,
including transitive dependencies and build-injected runtime helpers, with their full
license/notice text. A dependency without a discoverable license fails the build.
The inventory is specific to that build and conservatively includes module
records that render no JavaScript, so extracted CSS and required notices are not
lost. It is not a claim that every installed dependency executes in the browser.
For the pinned React Three Fiber version whose npm package omits its license,
the build uses a version- and hash-checked copy from its exact upstream tag;
the copy is in `frontend/third-party-licenses/` and its provenance is pinned in
`frontend/dependencyNotices.mjs`.

The fictional content replaces the prior META demonstration and provider-derived
market/ratio observations. Earlier source modules and their dedicated tests were
preserved outside the publication checkout before removal from the current tree.
The original local workspace was not modified. Previous Git commits, releases,
clones and cached deployments have not been rewritten or withdrawn.

This conservative publication choice avoids relying on uncertain republication
permission for issuer transcript expression or provider datasets. It does not
claim that ordinary financial facts are copyright protected:

- The [SEC's Website Dissemination policy](https://www.sec.gov/about/privacy-information#websites)
  permits copying and redistribution of public SEC website information with
  appropriate citation; it does not permit implying SEC endorsement.
- The [US Copyright Office](https://www.copyright.gov/help/faq/faq-protect.html)
  distinguishes facts from protected expression. Its [fair-use guidance](https://www.copyright.gov/fair-use/)
  gives no universally safe word count. Earlier excerpt-length tests were not a
  legal permission test and are not used as one here.
- [StockAnalysis's terms](https://stockanalysis.com/terms-of-use/) permit
  unmodified, attributed snippets but restrict full republication and competing
  database/product uses. This demo uses no copied provider observations.
- [MarketWatch RSS guidance](https://www.marketwatch.com/site/rss) provides
  conditional headline reuse; [Bloomberg's terms](https://www.bloomberg.com/notices/tos/)
  restrict redistribution. The public demo now links to publishers instead of
  distributing a mixed-feed snapshot. The local RSS reader is unchanged and
  users remain responsible for permitted use of acquired content.

These references describe the reviewed publication boundary, not legal advice
or permission to reuse unrelated material. No affiliation or endorsement by a
linked issuer, publisher, regulator or software author is implied.

## Build and inspect

From a clean checkout with Python, Node.js and pinned pnpm installed:

```sh
pnpm --dir frontend install --frozen-lockfile
node --test frontend/tests/public-demo.test.mjs frontend/tests/dependency-notices.test.mjs
pnpm --dir frontend run build:demo
python scripts/check_public_demo.py frontend/dist/demo
python -m http.server 8080 --bind 127.0.0.1 --directory frontend/dist/demo
```

Open `http://127.0.0.1:8080/`. Relative assets and hash navigation support the
repository path on GitHub Pages. The ordinary `pnpm --dir frontend run build`
continues to build the local application; never upload that output as the demo.

The compile-time `__PUBLIC_DEMO__` flag selects
`frontend/src/demo/SyntheticDemo.tsx`. Tests inspect its dependency closure and
rendered notices. The artifact guard rejects local runtime endpoints, source
maps, news snapshots, private exports and the retired real-company demo payload.
Only HTML, JavaScript, CSS, favicon and the two required license files are allowed.
The separate source/privacy check remains in place. Tests establish these
technical boundaries, not investment accuracy or exhaustive legal compliance.

## Requests and privacy

Demo controls use page state. They do not submit account data, questions or
portfolio information to an application server, and no application analytics,
external fonts, RSS proxy or market-data API is used. Browser assets are served
from the site origin. Its hosting provider may retain ordinary request logs.
Clicking an external link opens that provider's site and is subject to its own
policies. The public site makes no promise about third-party logging or retention.

## Publish with GitHub Pages

For `johnson-lee-v0/Equity-Research`, set **Settings → Pages → Build and deployment
→ Source: GitHub Actions**. The [Pages workflow](../.github/workflows/pages.yml)
runs on relevant pushes or manual dispatch. There is no scheduled RSS refresh.
The build has read-only repository access; only deployment receives `pages:
write` and `id-token: write`. It uploads the inspected `frontend/dist/demo`
artifact, never the repository root, local build, backend, database or archive.

Confirm the Actions deployment and inspect the deployed URL before claiming the
site is updated. A successful local build alone is not deployment evidence.
