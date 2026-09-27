# Public demo and local research

The [public demo](https://johnson-lee-v0.github.io/Equity-Research/) lets a visitor
explore the investment process without installing the backend. The
[source repository](https://github.com/johnson-lee-v0/Equity-Research) contains the
local application and the separate demo entry.

## What a visitor can try

The META walkthrough is a **September 26, 2026 snapshot**. Its latest reported
quarter is Q2 FY2026, ended June 30 and reported July 29. It links to the issuer's
release and deck, shows quarter-only CapEx through $31.078 billion, and retains
the Q3 FY2025 $19.374 billion observation. The separate $50.918 billion first-half
figure is cumulative, not a quarterly bar.

A **Since earnings: Muse** section covers the September 8 personal-agent launch.
It separates the later catalyst from Q2 results, attributes product/privacy claims
to Meta, and presents upside, risk and the evidence needed to test the thesis as
interpretations. Paid conversion and standalone product economics remain unknown.
Source context is collapsed by default. Financial/demo content changes only when
this authored snapshot is reviewed; the RSS schedule does not refresh these facts.

A fictional ExampleCo calculator demonstrates P/E, P/S, EV/EBITDA and P/NAV, with
implied value today and a twelve-month scenario. Its generated five-year multiple
histories are labeled synthetic; P/book is not presented as P/NAV. Memory uses the
local app's 3D explorer with public earnings and Muse notes. Watchlist, Portfolio
and Congress demonstrate the next action in the flow.

The challenge and evidence-retry controls explain what happens in the local app;
they do not run models in a visitor's browser. Personal accounts, saved research
runs, portfolio balances, credentials, worker logs and archived source files are
never inputs to the demo build. Fresh research still requires the
[local setup](setup.md), including an authenticated Codex CLI.

## Build and preview

From a source checkout with Python, Node and the pinned pnpm installed:

```sh
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend run build:demo
python -m pip install 'httpx==0.28.1'
python scripts/export_market_news.py --output frontend/dist/demo/market-news.json
python scripts/check_public_demo.py frontend/dist/demo
python -m http.server 8080 --bind 127.0.0.1 --directory frontend/dist/demo
```

Open `http://127.0.0.1:8080/`. The demo uses relative asset paths and hash navigation
so the same output works below the repository path on GitHub Pages. The ordinary
`pnpm --dir frontend run build` continues to build the local application. Do not
upload the ordinary `frontend/dist` directory as a Pages artifact.

The compile-time `__PUBLIC_DEMO__` flag chooses `frontend/src/demo/DemoApp.tsx`.
The public dependency closure excludes the local application shell and event
stream. The static artifact allows only its HTML, JavaScript, CSS, favicon and
public headline snapshot; source maps and arbitrary exports are rejected.
[Demo tests](../frontend/tests/public-demo.test.mjs) check that build boundary,
calculator arithmetic and demo-source attribution. They do not independently
certify the truth of issuer statements or investment assumptions.

## News freshness

Six headline tiles use free MarketWatch bulletins, Bloomberg Markets and Wall
Street Journal Markets RSS. Headlines retain their publisher, publication date
and original article link. Full articles follow each publisher's access terms.
The build's small Python reader fetches the feeds; visitors fetch only the public
JSON file, so no browser RSS proxy or API key is needed. No model call is involved.

The Pages workflow requests a fresh snapshot on relevant pushes, manual runs and
a six-hour schedule. GitHub can delay scheduled runs. The interface displays the
snapshot check time, marks snapshots older than 24 hours as saved headlines, and
stops displaying snapshots older than seven days. A failed clean build fetch shows
news as unavailable; it does not fabricate six replacement headlines. When the
exporter is rerun against an existing output, it can retain a successful snapshot
for at most 24 hours with a stale label. The full local reader has a separate
ten-minute cache, described in the [research workflow](research-workflow.md).

## Publish with GitHub Pages

For `johnson-lee-v0/Equity-Research`, configure **Settings → Pages → Build and
deployment → Source: GitHub Actions**. The
[Pages workflow](../.github/workflows/pages.yml) can then run from the Actions page
using **Publish public demo → Run workflow**, or from a relevant push to `main`.
Forks must deliberately update the workflow repository guard and public links
before enabling their own site.

The build job has read-only repository permissions. Only the deployment job
receives `pages: write` and `id-token: write`, uses the `github-pages` environment,
and deploys the inspected `frontend/dist/demo` artifact with GitHub's official
[Pages actions](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
It never uploads the repository root, local build, backend, database or evidence
archive. No provider credentials or private environment files are required.

Publishing source remains a separate boundary: use
`scripts/package-source.sh OUTPUT.zip` to create a source-only archive and run the
existing value-blind privacy gate against local private inputs before publishing.
CI checks the tracked public source and exact Pages artifact again. Its explicit
`--allow-empty-private-inputs` option acknowledges that a clean GitHub runner has
no private local database; that CI check does not replace the pre-publication
comparison against the owner's actual private data.

A green local build proves only that the artifact can be built. Confirm the
GitHub Actions deployment succeeds and open the deployed URL before claiming the
public site is live.
