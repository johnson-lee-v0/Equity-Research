# ResearchCouncil

ResearchCouncil is one local research workspace for investment questions,
Congress disclosures, strategy testing, earnings transcripts and quarterly
filing comparisons. A React interface uses one Python FastAPI service, SQLite
memory and a private evidence archive. It supports research and paper decisions.
It never places trades. The optional 3D office loads only when opened.

Explore the [public browser demo](https://johnson-lee-v0.github.io/Equity-Research/)
to try an earnings walkthrough, pricing controls and 3D memory without installing
anything. Its Cedar Workshop example is entirely fictional: original teaching
figures, authored questions and browser calculations, with no live research or
market feed. Original publishers are linked instead of republishing their content.
The agent loop and personal research remain local; see the
[public demo guide](docs/public-demo.md) for the build and deployment boundary.

**Education and research only.** This project is not personalized investment
advice or a recommendation to buy, sell or hold a security. Returns are not
guaranteed; investments can lose their entire value. Data, calculations and model
outputs may be wrong or stale. Independently verify current primary sources and
assumptions before relying on research, and seek qualified advice when appropriate.
The software license does not establish the accuracy or suitability of research.

Open **http://127.0.0.1:8000** after starting the app. This is the single application
entry point; the older research desk and Congress site are no longer required
to use the consolidated features. This is a local application, so its HTML needs
the included server rather than being opened directly as a `file://` page.

The main sections are:

- **Research** — questions and results in one case, including earnings,
  evidence, pricing and decisions. Use the Reddit origin filter for ideas from
  Reddit; **Earnings & materials** and **Company history** are research subviews.
- **Watchlist** — entry prices, catalysts and dated review conditions.
- **Portfolio** — positions and account context used by investment checks.
- **Congress** — searchable disclosures, reported positions, source details
  and collection status.
- **Strategy testing** — disclosure replays, configurable experiments and saved
  historical studies.
- **Memory** — a searchable graph of shared company notes and their sources,
  backed by a local Obsidian-compatible vault.

Office and Settings remain secondary tools. Old Documents, Results, Reddit and
Library bookmarks open the matching content inside the current navigation.

Document NLP runs locally with transparent rules; it needs no model download or
API key. Its labels and text matches are review aids, with source passages and
method limitations alongside results. TXT, HTML and readable digital PDFs are
supported. Scanned PDFs require text extraction/OCR elsewhere first.

Automatic earnings discovery and historical numerical extraction use the existing
authenticated Codex provider. Transcript annotations and filing comparisons use
local rules. Sources are fetched and archived, and numerical observations are
checked against their quoted passages and reporting periods before display.
Charts distinguish actual results from guidance; capex comparisons show earlier
guidance against subsequent spending and retain management's explanations.
A not-yet-published filing is optional coverage, so available earnings findings
remain usable. An investment review reuses the verified package, prepares financial
inputs locally and asks Codex for one five-question assessment with reasoned
bear/base/bull price targets. The calculator verifies the inputs and arithmetic
before the research case publishes them. **Reassess with saved materials** keeps
earlier revisions and reuses unchanged evidence.
See the [agent architecture and operating guide](docs/earnings-workflows.md),
[assessment pipeline](docs/assessment-pipeline.md) and
[measured latency audit](docs/research-latency-audit.md).

Existing prototype data can be retained with the idempotent Congress importer
and the research-desk snapshot importer:

```sh
.venv/bin/python scripts/import-labs.py
.venv/bin/python scripts/import-research-library.py ~/.codex/road2m/ideas.sqlite3
```

Imports leave the original archives intact. The resulting datasets, historical
findings, document analyses and saved backtests live inside `data/evidence/`, so
the existing full backup/restore commands include them. They are private data,
not part of a shareable source package. See
[the consolidation record](docs/unified-research.md) for the retained scope.

The generative model route is the authenticated Codex CLI through an existing
ChatGPT subscription. New investment reviews pair Astra with Laya, an
open-weight classifier that runs locally after a separate model installation.
No paid model API or cloud database is required. Saved local records remain readable when Codex is
not signed in; new GPT research requires an available authenticated provider.
The local interface uses one research workspace with no automatic sample-data
fallback. The public demo is a separate build and never replaces local records.

## Run it locally

Requirements: Python 3.11+, Node.js `>=22.13.0`, pnpm 11.19.0 for the
committed frontend lockfile, and the Codex CLI for GPT-backed work. The
installer can place pnpm in the project-local `.runtime/pnpm` prefix when no
usable pinned executable is already available; Corepack is not required.
From a checkout, run:

```sh
./install.command
./scripts/install-laya.sh
codex login
./start.command
```

The app is served at [http://127.0.0.1:8000](http://127.0.0.1:8000). Keep the
launcher terminal open while using it and press Ctrl-C to stop the server. A
terminal-only flow is available with `./scripts/install.sh` and
`./scripts/start.sh`. Full setup, runtime selection, Codex authentication,
troubleshooting, and data operations are documented in
[docs/setup.md](docs/setup.md).

## Ask an open question

Use the command bar to submit any research question in plain language. Every
question starts with the Chief of Staff (A00), which creates a routing plan;
you do not need to provide a ticker or choose a fixed horizon. The composer
keeps model and effort as its optional run controls, while the workspace and
office controls remain available elsewhere in the app.

When the plan calls for research, the bounded discovery step gathers public
HTML, text pages or readable digital PDFs and archives the retrieved sources in the local private
evidence store. The output shows the source trail and any research candidates
with their rationale and verification state. A discovery candidate is a
research lead kept separate from the CIO's decision, so its presence does not
create a position or an investment recommendation.

New cases use Chief of Staff intake, source discovery, fundamental research
and CIO review. The [task model policy](docs/task-model-routing.md) assigns
models by responsibility and defines supported overrides. Local code provides
market indicators, scenario checks and share calculations. A material missing
public fact can receive one bounded continuation within the same case.
Informational questions can finish at intake.

Investment research answers five questions for each selected candidate:

1. What is the opportunity, and what is the market missing?
2. What is it worth versus the entry price and alternatives?
3. What can change the outcome, and by when?
4. What would prove us wrong, and how could we lose money?
5. What should we do now, and does it fit the portfolio?

The case shares a budget of 15 distinct research facts across its stages and
up to three candidates. Each answer highlights at most two supporting facts
and one contradicting fact, with source links and material unknowns. Additional
calculation inputs remain available in the audit and count toward the same
fact budget. A case that needs more evidence reports that limitation.

Laya classifies compact evidence packets before Astra's judgment and reviews
its response afterward. The saved case shows whether they agree, a disagreement
was resolved, or review remains pending or unavailable. Classifier confidence
does not establish an investment probability or verify a fact. Existing
evidence, valuation, freshness and portfolio checks still govern publication of
a recommendation. Historical cases retain their original format and are not
labelled as jointly reviewed unless that review actually ran.

Laya is experimental in this financial workflow. Its English model accepts
only 512 encoded tokens per question, including the evidence and answer
choices. A material packet that cannot fit is reported as unavailable without
truncation. Local integration tests demonstrate execution and evidence
controls, not investment accuracy; model agreement is not independent proof
of a thesis. See the [acceptance record](docs/laya-acceptance.md).

## Follow a question

The question journal retains the original question or Reddit title. Open a
case to see its current candidate decisions: Recommend, Watchlist, or Decline.
Recommend requires a supported investment argument, asset-appropriate
valuation/payoff, entry and exit conditions, dated review, and valid proposed
allocation;
Watchlist requires an actionable price or catalyst condition. Missing inputs
and execution failures are shown separately from the investment judgment.
Maximum permitted shares are shown separately from the recommended allocation.
A funding check does not confirm that an order was placed.

Open a report to inspect its saved analysis, assumptions, counterarguments,
missing data, and citations. Claim checks show whether the cited archive text
supports the recorded value; they do not certify that the source itself is
true. Citation links open the retained source with its cited lines highlighted.
Market research also checks the provider's current ticker identity against the
company being discussed. An explicit mismatch blocks price-based conclusions
and share sizing until the instrument is corrected.
The flow connects the original claim, collected evidence, Researcher
assessment, numeric checks, and CIO decision. It shows saved rationale and
failure points without exposing private model reasoning.

The office provides selectable role cards and keeps detailed desk inspection
optional. A selected question gives the roles context, including their recorded
work and outputs. Market scenarios run within ordinary research; the separate
participant simulation remains an optional experiment.

The research process also exposes memory reuse, assigned evidence gaps,
market-data provenance and price scenarios. The **Memory** tab shows the shared
company graph, backed by a local Obsidian-compatible Markdown vault. See
[shared company memory](docs/shared-company-memory.md) for source checks,
personal notes and opening the vault in Obsidian, and
[task model routing](docs/task-model-routing.md) for the current Luna/Sol/Astra
assignments through the local Codex CLI.
See [how the research process works](docs/research-workflow.md) for the
distinction between saved memory, current evidence, scenario outcomes, entry
ranges, and future price targets.

Open **Research** and select a case to read a processed Reddit post's
saved conclusion, CIO brief, and research flow. The **Reddit** origin filter
shows ideas that started from Reddit. A finished
report may recommend waiting for evidence; completion does not imply a buy.
You can also paste a Reddit submission URL into the question box to retain
and screen that specific post without starting a historical collection.
The local backend processes up to three Reddit cases concurrently
and reserves Codex capacity for a user
question. Parallel processing shortens the wait without reducing total token
usage. The backend must remain running for the backlog to advance.

Lifecycle records track observable price conditions and dated evidence or
catalyst reviews across watched, recommended and explicitly held ideas, and
declined ideas with reopening conditions. A met condition queues a bounded review of the same case;
it does not place an order or assume a catalyst occurred. A firm pause also
pauses watch monitoring. Settings keep proposed position limits separate from
approved limits; changes apply to new cases and existing case snapshots retain
the policy used for their calculations. Frozen paper baselines retain the
original decision and benchmark choice. Later observations can compare
price-only outcomes, including declined ideas, without rewriting the original
call or treating hypothetical returns as trading performance.

## Data safety

Fresh installs contain no personal accounts or balances. The published source
does not include the build owner's portfolio, research history or credentials.
Account amounts are hidden by default in the interface; this display setting
does not remove private records from the local backend or make a private
database suitable for publication.

Personal records and imported evidence live below the private, gitignored
`data/` directory. The setup script creates it with owner-only permissions.
The original local product brief is kept as `PRD.html` for the build owner but
is excluded from the shareable source; the public, sanitized brief is
[docs/PRD-public.html](docs/PRD-public.html). The earlier prototypes under
`exp/` are preserved locally and excluded from the application source.

Use the included commands for durable data handling:

```sh
./scripts/backup.sh
./scripts/restore.sh data/backups/road2m-backup-YYYYMMDDTHHMMSSZ.tar.gz --confirm-stopped
./scripts/export.sh --namespace real --format json
./scripts/smoke.sh  # disposable local launcher/backup/restore check
```

The settings page can create a database-only SQLite snapshot. For a full,
restorable archive including checksummed evidence, run `./scripts/backup.sh`
and use the returned `.tar.gz` path with the restore command. Restore requires
the server to be stopped, validates the archive and schema, and keeps a
recoverable pre-restore copy. Exports omit credentials, raw worker logs, and
internal provider thoughts. Restore is a local maintenance command; the live
application API returns `409` for restore requests and directs you to stop the
server before using the script.

## Repository layout

- `frontend/` — React, TypeScript, Vite, and the interactive 3D office.
- `backend/` — FastAPI, Pydantic, SQLite persistence, providers, and workers.
- `backend/migrations/` — durable schema migrations.
- `scripts/` — install, start, smoke, backup, restore, and export commands.
- `docs/` — operating guides, architecture and dated acceptance records.
- `data/` — local runtime state; never commit it.

For development, start with [the agent guide](AGENTS.md) and
[the domain glossary](CONTEXT.md). Current operating guides take precedence over
the explicitly historical [implementation contract](docs/implementation-contract.md).

GitHub Actions validates the source and local frontend/backend without provider
credentials. A separate Pages workflow builds and publishes only the public demo
from `frontend/dist/demo`, including its generated dependency license notices.
The FastAPI service, Codex agent loop, private ledger and source archive are not
hosted on Pages. See [public demo deployment](docs/public-demo.md).
The public build contains no news snapshot or scheduled RSS collection. The local
news reader remains available subject to the source providers' terms.

## Scope and limitations

Source imports and explicit missing-data states are supported when an external
connector is unavailable. Quotes, options, filings, and model availability are
reported with their observation/retrieval state; the system does not invent a
price, probability, account total, or investment result. The local process
must remain running for task monitoring and schedules.

## License

Original project code is licensed under the [MIT License](LICENSE),
copyright 2026 Johnson Lee. Third-party software dependencies, model weights,
assets and data remain subject to their own licenses and terms. This includes
issuer filings and transcripts, market data, news/RSS content and quoted
excerpts; the MIT license does not relicense that material. Source links and
attribution do not grant additional redistribution rights.

The public demo links to its complete build-generated third-party notices and
MIT license. See [content and dependency scope](docs/public-demo.md#content-and-licenses)
for the published boundary and limitations; previous Git history is unchanged.
