# Shared company memory

The app maintains an actual Obsidian-compatible Markdown vault at
`data/memory-vault` by default. Set `ROAD2M_SHARED_MEMORY_VAULT_PATH` to choose
another local folder. Open that folder as a vault in Obsidian, or browse its
linked notes in the app's Memory tab. Obsidian does not have to be installed for
the app or its agents to use the vault. No cloud sync or hosted embedding service
is involved.

## What agents share

The same namespace and ticker determine the company workspace. There is no
separate private copy of the company evidence per agent.

| Note | Contents and authority |
| --- | --- |
| Company | Compact index of latest earnings, dated facts, unresolved questions and earlier reasoning |
| Source | Original URL, ledger source ID, immutable version/hash, dates and a short opening excerpt |
| Fact | Historical observation, period, value/unit, source link, exact locator and ledger validation status |
| Earnings | Saved review summary, limitations and material links; interpretation rather than new evidence |
| Opinion | Earlier agent conclusion, author, output hash, run and cited sources |
| Gap | Unresolved question, owner, retry condition and last outcome |
| User note | Attributed human opinion, even if its frontmatter claims to be a validated fact |

Files have YAML-compatible frontmatter and real Obsidian `[[wikilinks]]`.
Company notes have ticker aliases, so a note can link to `[[COST]]` or `[[NKE]]`.
Folders separate `real`, `demo` and `simulation` namespaces. Generated notes
include stable note IDs; refreshes do not create duplicate copies of unchanged
records. The ledger retains historical records and remains authoritative for
verification and calculations.

`SharedMemoryService.retrieve(task_id)` refreshes that task's company files,
then ranks notes by agent role and task terms. Research and decision packets use
at most ten items and **12,000 characters including metadata**, not just excerpt text.
Repeated claims with the same source, period and value are presented once.
No full filings or transcripts are copied into this additional memory packet.

Each retrieval checks namespace, ticker, knowledge/publication/observation dates,
the task's `as_of`, current source heads, hashes and source versions. Invalidated
or superseded records are excluded. Source references not already attached to
the task are labelled historical leads that require verification and attachment;
memory never silently expands the citation packet. User notes and earlier
conclusions remain opinions. Discovery-role retrieval excludes those private
notes and opinions. Current prices still require refresh.

The application workflow freezes the selected packet with its provider attempt.
This lets an agent see which material is already available and where to retrieve
it while keeping the original evidence checks in force.

The web-capable source finder receives a separate public projection: at most
eight primary-source URLs and four fixed topic labels for earlier evidence gaps.
It selects these from up to twenty eligible notes within the same 12,000-character
retrieval budget. Personal documents, user notes, opinion prose and unverified
sharing URLs are excluded. Query-bearing URLs are omitted rather than changed.
The source/version receipt stays local; these leads still require ordinary
fetching and verification before they can become evidence. A targeted retry
cannot use memory to expand its assigned search scope.

Ticker-first earnings collection also reads a bounded index of the five latest
same-namespace earnings identity checkpoints. A retained CIK is only a lead:
the collector fetches SEC submissions again and requires the exact requested
ticker and CIK to match before skipping the ticker directory and model identity
search. It tries at most three distinct retained CIKs, then falls back to normal
discovery. Fresh SEC metadata supplies the company name, current filing list and
website fields; old filing lists are never reused as current.

When SEC leaves the IR website blank, a prior corporate-to-IR proof may supply
that identity after its namespace, ticker, CIK, source heads, versions and hashes
revalidate. This check runs again before locating an earnings release. Changed
proofs lose authority; a fresh SEC-provided IR website takes precedence. The
collector still discovers the latest earnings event and fetches its release on
every new collection. Saved dates and old releases never establish the latest
quarter. These identity leads come directly from the research ledger, not from
Markdown facts, user notes or opinions.

## Personal notes

Create a Markdown file under `User Notes`, for example:

```markdown
---
namespace: real
ticker: COST
title: My membership thesis
---

I want to understand whether paid membership growth can offset lower renewals.

Related company: [[COST]]
```

The Memory graph reads these files directly. Refreshing the graph's derived
ledger notes preserves personal notes and existing Obsidian settings. If a user
edits a generated note, their file remains intact as an attributed opinion; a
new generated copy receives the ledger truth and generated wikilinks point to
that fresh copy. Previous untouched generated revisions are stored under
`.road2m/history`. Deleted ledger records remain as local archival files but are
not returned by the app graph or retrieved as available evidence.

A same-ticker personal note can be included as opinion context in that ticker's
research or decision task. Imported note text is data, not an instruction channel.

## Exploring the notebook

Memory opens as a 3D map. Companies anchor clusters of notes; colors identify
sources, facts, analysis and unanswered questions. Lines represent resolved
wikilinks. Spatial distance is a browsing aid, not an evidence score or a measure
of investment importance. Selection does not rearrange the map.

- Drag to rotate, scroll to zoom, and right-drag to pan. Touch supports one-finger
  rotation and two-finger pinch/pan. With the canvas focused, arrow keys rotate,
  `+`/`-` zoom and `0` resets the view.
- Company shortcuts, **Focus selected** and **Reset view** help navigate. Select
  a note to read it, follow its links, or return to the previous note. **Read
  selected note** brings the reader into view on smaller screens.
- **Connections only** isolates the selection and its direct neighbors among
  the loaded notes, retaining their positions. Search, company and type filters
  remain server-side; counts describe the displayed subset. A linked note
  outside those filters remains readable and offers **Find this note in the map**.
- **List** provides the same note navigation without a canvas. A keyboard-accessible
  list is also available below the map. Graphics failures switch to the list.
  The scene rests when unused and respects the reduced-motion preference.

Browsing, selecting and filtering only read memory. **Refresh memory** is still
the explicit action that exports updated ledger notes into the vault.

## Service contract

`backend/app/memory/shared.py` owns the projection and retrieval service:

- `sync(namespace="real", tickers=None)` explicitly exports ledger state and
  returns write/unchanged/preserved counts plus vault location and sync time.
- `graph(namespace="real", ticker=None, kind=None, query="", limit=300)` reads
  actual files and resolved wikilinks. It returns nodes, edges, global filter
  facets, total matching count and a truncation flag. Maximum graph size is 1,000.
- `note(note_id, namespace="real")` returns Markdown, frontmatter and connected
  note IDs, or `None` if it is absent from the specified namespace.
- `retrieve(task_id, max_chars=12000, max_items=10, frozen_source_versions=None)`
  returns bounded, gated context. The workflow supplies its resolved immutable
  version/hash map; if omitted, the service reads the task's current attempt.
  An unfrozen or mismatched source remains a lead, never an attached citation.
- `Repository.prepare_shared_memory(...)` is the workflow's retrieval hook;
  it stays outside the existing task-memory database write transaction.

Graph and note reads do not write files. Sync uses an exclusive local file lock,
an internally consistent database read snapshot, atomic writes and preserved
revisions. Paths remain inside the vault; symlinked files are not imported or
overwritten. A manifest tracks generated hashes, but source eligibility is
rechecked against the ledger at retrieval time.

## Verification

`frontend/tests/memory-graph.test.mjs` verifies deterministic 3D clustering,
actual-link placement, finite bounds and node separation up to 1,000 notes.
`frontend/tests/memory-explorer.test.mjs` covers the accessible reader, controls,
selection, asynchronous filter responses and off-map note behavior.
`frontend/tests/memory-webgl.test.mjs` covers graphics availability and precise
node picking. These tests do not replace checking the rendered WebGL scene in
a browser.

`backend/tests/test_shared_memory_vault.py` covers real vault files and links,
source/period provenance, idempotence and revision retention, user-edit
preservation, role privacy, namespace/ticker isolation, full-packet budgets,
as-of/source-amendment exclusion, non-expansion of citation authority, search and
filters, symlink/path boundaries and removal of stale ledger projections.

On a disposable copy of the retained COST/NKE database on September 26, 2026,
export produced 122 notes and 364 resolved links. Initial export took 0.090s,
graph reading 0.021s, and unchanged refresh 0.064s. An existing NKE decision's
memory retrieval took 0.083s. These are local memory-operation measurements,
not a benchmark of a complete research run.
