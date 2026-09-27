# ResearchCouncil local setup

ResearchCouncil runs as a local FastAPI server with a built React frontend. The server
binds to `127.0.0.1:8000`, keeps SQLite and evidence under a private `data/`
directory, and does not require Docker, cloud hosting, or a paid API.

## Prerequisites

- macOS or another Unix-like system with Python 3.11 or newer.
- Node.js `>=22.13.0` (required by the pinned pnpm 11.19.0 CLI and compatible
  with Vite 7).
- pnpm 11.19.0 for the committed frontend lockfile. The installer can use the
  bundled pnpm fallback, a project runtime, or `ROAD2M_PNPM_BIN`; otherwise it
  installs the pinned CLI under `.runtime/pnpm` with an ordinary npm prefix.
- The Codex CLI, signed in through the existing ChatGPT subscription. Ollama is
  optional and is not downloaded by the installer.
- A separate local Laya installation for the new five-question joint investment
  review. Its model weights and inference dependencies stay below `.runtime/`
  and are excluded from source distribution.

The setup scripts first look for a project-local runtime, then the standard
Codex bundled runtime under the current user's home directory, then normal
`python3`/`node` on `PATH`. They do not contain a machine-specific absolute
home directory. Set `ROAD2M_DATA_DIR` when the private data directory should
live somewhere else.

## Install

From a clone of the repository:

```sh
./install.command
```

The same setup is available from a terminal:

```sh
./scripts/install.sh
```

Install the local classifier once:

```sh
./scripts/install-laya.sh
```

This downloads the pinned general English Laya checkpoint and installs its
runtime separately from the application's Python environment. Model download
requires an internet connection; classification uses local weights. The app
does not download models while opening a page, and Laya does not need an API
key. Saved research remains readable when Laya is absent; a new joint review
reports its missing participation instead of claiming that the models agree.

The pinned English checkpoint has a 512-token limit for the complete encoded
question, evidence and choices. The runtime checks this before inference and
reports unavailable when the packet does not fit. It does not discard material
evidence to obtain a vote. This general classifier has not been calibrated for
financial decisions; its scores are not market probabilities. The isolated
runtime uses local weights and offline library settings, but it is not an
operating-system network sandbox.

Setup creates `.venv/`, installs the backend dependency manifest when one is
present, installs the frontend dependency lockfile with pnpm 11.19.0, builds
`frontend/dist/`, and creates owner-only `data/` subdirectories. If a future
checkout uses an npm or yarn lockfile instead, the installer selects that
manager. A checkout without a lockfile falls back to npm for development; add
and commit a lockfile before sharing a release. CI always uses the committed
lockfile and frozen installation.

The installer does not sign in or read Codex credentials. After installing the
Codex CLI, sign in explicitly:

```sh
codex login
codex login status
```

Choose the ChatGPT subscription route when prompted. Do not configure an API
key for ResearchCouncil. A missing login leaves saved local records readable and is
shown as an unavailable provider for GPT tasks. The interface does not switch
to sample data when a provider or the local backend is unavailable.

## Start

Double-click `start.command` in Finder, or run:

```sh
./scripts/start.sh
```

The server opens `http://127.0.0.1:8000` on macOS after `/api/health` answers.
Keep the terminal window open while using the app; press Ctrl-C to stop it.
Shutdown allows up to five seconds for active SSE/client cleanup before the
launcher reaps the server process.
To use another loopback port without changing the source:

```sh
ROAD2M_PORT=8010 ROAD2M_OPEN_BROWSER=0 ./scripts/start.sh
```

The app serves the production bundle from `frontend/dist/`. Development mode
is available for frontend work with Vite on port 5173; the normal user flow is
the single FastAPI launch above.

After setup, run the disposable packaging smoke check when you want to verify
the launcher and data tools without touching the real `data/` directory:

```sh
./scripts/smoke.sh
```

## Ask an open question

Open the app and enter a question in the command bar. Questions are accepted
in plain language and are always sent to the Chief of Staff (A00) first. The
Chief of Staff creates the routing plan and can derive the research horizon
from the question, so the composer does not require a ticker, fixed horizon,
or manually selected source. Its only optional run selectors are the model
and reasoning effort; office inspection stays separate in the workspace.

For research questions, the bounded discovery step searches only the public
pages selected for that request, accepts HTML or text content, and archives
retrieved sources locally below the private `data/` directory. The output
keeps discovery candidates, their rationales, source links, and verification
state visible even when no allocation is made. Candidates are research leads;
CIO allocation decisions remain a separate later step.

New investment cases organize the research around five decision questions and
a shared 15-fact budget. Astra remains the decision model through Codex CLI;
the local Laya classifier supplies a separate assessment and resolution check.
Model/effort overrides for other work do not silently replace Astra in a joint
investment review. The full contract and its explicit limits are documented in
[the five-question workflow](five-question-laya-contract.md).

The public application is named ResearchCouncil. Existing `ROAD2M_*` environment
variables, the `X-Road2M-Client` header and local data filenames retain their
original internal identifiers so existing installations continue to work.

## Private data and safe operations

`data/` contains the local database, imported evidence, worker directories,
backups, and exports. It is ignored by git and the setup command applies
owner-only permissions. Never copy a real portfolio observation, provider
credential, or raw worker log into source control. Synthetic test fixtures use
an isolated internal namespace; the public interface has no Demo tab. New
installs start without personal portfolio data. Display privacy is separate
from publication privacy: hiding amounts in the browser does not sanitize a
database, export, backup or screenshot containing other private information.

Create a consistent SQLite plus evidence archive:

```sh
./scripts/backup.sh
```

The Settings page's **Create database snapshot** action writes a SQLite
database-only snapshot. It is not a full restore archive. To include evidence,
run `./scripts/backup.sh` and use the returned `.tar.gz` path below.

To choose the destination explicitly:

```sh
./scripts/backup.sh --output data/backups/road2m-backup.tar.gz
```

The archive includes a manifest, SQLite backup produced through SQLite's
backup API, and the evidence tree with checksums. Existing files are never
overwritten.

Restore only after stopping the server with Ctrl-C:

```sh
./scripts/restore.sh data/backups/road2m-backup-YYYYMMDDTHHMMSSZ.tar.gz --confirm-stopped
```

Restore is deliberately disabled through the live application API, which
returns `409` with the stopped-service instructions. Run the command above
from a terminal after the server has stopped; this prevents active writers
from racing the replacement database.

Restore rejects unsafe archive paths, checksum mismatches, malformed SQLite,
foreign-key failures, missing core tables, and missing referenced evidence. It
creates a recoverable `data/backups/pre-restore-*` safety copy before replacing
the active database and evidence directory. The `--confirm-stopped` flag is
required, and the script also checks ResearchCouncil's local PID marker.

Export a namespace in an open format:

```sh
./scripts/export.sh --namespace real --format json
./scripts/export.sh --namespace real --format markdown
```

Exports include the selected accounts, evidence/source content, research,
decisions, task history, model metadata, and relationships. Credential fields,
raw worker logs, and internal provider thoughts are omitted. Default exports
are written below the private `data/exports/` directory; use `--output` for a
different destination.

## Alpaca and Reddit credentials

Connector credentials belong in the backend environment or the private
`data/config/providers.json` file. They are not entered into the browser or
included in the source ZIP. The recognized environment names include
`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `APCA_API_KEY_ID`,
`APCA_API_SECRET_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and
`REDDIT_USER_AGENT`.

For a private JSON configuration, use these field names and replace the
placeholder text locally:

```json
{
  "alpaca_api_key_id": "YOUR_ALPACA_KEY_ID",
  "alpaca_api_secret_key": "YOUR_ALPACA_SECRET",
  "reddit_client_id": "YOUR_REDDIT_CLIENT_ID",
  "reddit_client_secret": "YOUR_REDDIT_CLIENT_SECRET",
  "reddit_user_agent": "YOUR_DESCRIPTIVE_REDDIT_USER_AGENT"
}
```

Keep the directory and file accessible only to your local user. Restart the
backend after changing its environment. Standard PRAW configuration uses
`client_id`, `client_secret`, and `user_agent` in a `praw.ini` section. Reading
public submissions through PRAW requires these application credentials; it
does not require enabling posting or voting. See the
[PRAW read-only setup guide](https://praw.readthedocs.io/en/stable/getting_started/quick_start.html).

Enable Alpaca retrieval when launching the backend:

```sh
ROAD2M_ENABLE_MARKET_CONNECTORS=1 ./scripts/start.sh
```

To keep these operational choices across restarts, place the allowlisted
settings in the checkout's private root `.env` file:

```dotenv
ROAD2M_ENABLE_MARKET_CONNECTORS=1
ROAD2M_ENABLE_REDDIT_INTAKE=0
ROAD2M_ALPACA_DATA_FEED=iex
```

Process environment values take precedence over the root `.env` values. The
default keeps market connectors disabled and uses the exchange-limited IEX
feed. Set `ROAD2M_ALPACA_DATA_FEED=sip` only when the Alpaca account has SIP
access; IEX data must not be described as consolidated US-market coverage.

View retained Reddit ideas under **Research → All research**, using the
**Reddit** origin filter. Expand **Reddit posts awaiting research** for intake
status and retained posts. The intake preference and both continuation
checkpoints are saved in the local database. The monitor checks once a minute while the
backend runs. The first history cutoff remains fixed across restarts; later
polls check the newest posts and retain overflow batches for continuation.
The connection status distinguishes missing credentials, configured but
unchecked credentials, and an observed successful read. Saving settings
alone does not validate provider access.

Open **Research → All research** and set **Progress** to **Completed** for
finished cases. A retained Reddit post's **View current decision** button opens
its saved conclusion and research flow. The **In progress** filter shows cases
whose research is still underway.

By default, three Reddit cases can progress together. A case and its bounded
evidence continuation share one post slot. The Codex provider allows four
simultaneous generations, with at most three used by Reddit background work,
leaving capacity for a user question. New user questions take priority over
admitting more Reddit posts. A five-second local dispatch check fills freed
slots; Reddit network polling stays on its one-minute cadence. Parallelism
changes elapsed time, not the total research or token budget.

These optional private `.env` settings control the bounds:

```dotenv
ROAD2M_REDDIT_PARALLEL_LIMIT=3
ROAD2M_CODEX_GLOBAL_CONCURRENCY=4
ROAD2M_CODEX_BACKGROUND_CONCURRENCY=3
```

The post and global limits are bounded from 1 to 8. The background limit is
bounded from 0 to 7 and capped below the global limit to preserve user
capacity. A background limit of zero prevents background generations from
starting. Restart the backend after changing these values.

Alpaca feed access depends on the account's entitlements. Historical bars
retain the selected feed and corporate-action adjustment mode. Pagination is
required for large requests, and its limit applies across symbols rather than
independently to each symbol. See
[Alpaca's historical bars reference](https://docs.alpaca.markets/us/reference/stockbars).

The seven-day Reddit intake window is a requested coverage window. The inbox
reports retrieved coverage and any listing limit or access failure separately.
Neither an empty result nor a completed poll proves that every historical or
deleted submission was accessible. The local service must stay running for
new-post polling and queued research to continue.

## Troubleshooting

SEC source requests use `ROAD2M_SEC_USER_AGENT`. Put an application description
containing your real contact email in the private project-root `.env`, or export
it in the backend's process environment, then restart the backend. Process
environment takes precedence over `.env`; the configured contact is sent only
to SEC domains. Keep the private `.env` out of source control and shared logs.
This identifies the client; it does not guarantee that SEC permits the network
or every document request.

All SEC requests share a process-wide limit of four request starts per second.
A 403 or 429 starts a cooldown for the affected SEC host, initially at least
60 seconds, with repeated denials increasing it to ten minutes. `Retry-After`
can extend that pause up to one hour. Calls during the cooldown fail promptly
instead of retrying the denied host. A blocked `www.sec.gov` archive does not
disable working `data.sec.gov` facts. The diagnostic distinguishes an explicit
undeclared-automation message from a stated rate limit; an unspecified denial
remains an unspecified denial. Error pages never become evidence. Collection
can still use verified issuer releases and exact public filing copies.

If setup reports an old interpreter, install Python 3.11+ and Node.js
`>=22.13.0` and rerun it. If the frontend build cannot find pnpm 11.19.0 and
npm is unavailable to install the project-local fallback, install that version
or set `ROAD2M_PNPM_BIN` to its executable path. The scripts never download a
local language model.

If the app reports an unavailable Codex provider, check `codex login status`
and confirm the ChatGPT subscription route. Provider availability is an honest
runtime state; it is not replaced with fabricated research output or a paid API
fallback.

For a clean local data directory, stop the server and set a new
`ROAD2M_DATA_DIR` before starting. Keep the old directory until any needed
backup or export has been checked.
