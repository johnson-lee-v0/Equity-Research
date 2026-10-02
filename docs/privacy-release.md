# Source publication privacy gate

The source packager creates an archive under the selected product name (default:
`ResearchCouncil/`) and runs `scripts/release_privacy.py` before accepting it. The
archive directory name is independent of the public repository name. The packager
excludes local databases, exports, backups, runtime state, temporary files,
credentials, environment files, generated builds and archive files such as `.tar`,
`.tar.gz`, `.tgz` and `.zip`.

The gate reads local SQLite databases and selected local configuration files in
read-only mode. It derives opaque markers from real account observations, account
identifiers and credential values, then scans each manifest file and archive
entry. It can also inspect a prepared publication tree and checks reachable Git
heads, tags and remotes, including commit/tag messages and author/tagger metadata.
It reads the exact staged index blobs, even when a working file has subsequently
been cleaned or deleted; unmerged entries and unscanned submodule gitlinks fail
the gate. Its JSON result reports paths,
categories and counts; it does not print private values, matched content or
value-derived fingerprints.

## Numeric matching policy

Money observations match complete numeric literals by value. An observation must
not match the digits inside a different decimal, comma-grouped number, dependency
version, IP address or CSS dimension. Equivalent complete spellings, including
comma grouping, trailing zeros and scientific notation, are compared numerically.
Accounting-negative amounts retain their sign. High-precision observations are
not silently rounded to a nearby public constant.

Whole-valued observations with an absolute value below 1,000 need an associated
account or financial context. A bare short integer is common in a timeout, UI
constant or test count, so its occurrence alone does not establish a leak. The
context check covers directly associated labels and currency prefixes, tabular
account records (including quoted CSV) and nested account objects, rather than
exempting particular files or private values. For example, a cash
balance or position quantity is relevant context; an unrelated `max_length` is
not. A whole-valued decimal such as `47.00` follows the same rule as `47`.
Flag-like whole values with an absolute value below two are excluded from numeric
markers consistently, including spellings with trailing decimal zeros.

Fractional observations and whole values of 1,000 or more remain global numeric
checks. Credentials and personal identifiers retain their existing boundary-aware
text matching without the short-number context exemption. The same matching policy
applies to source files, prepared trees, archive entries and Git contents.
[Privacy tests](../backend/tests/test_release_privacy.py) use synthetic private
inputs to cover matching, exclusions and reports that do not disclose values.

## Create and inspect a publication candidate

Create an archive with the normal local inputs present:

```sh
./scripts/package-source.sh /tmp/ResearchCouncil-source.zip
```

A clean install with no local database or configuration must explicitly acknowledge
that missing comparison data:

```sh
./scripts/package-source.sh /tmp/ResearchCouncil-source.zip --allow-empty-private-inputs
```

Use `--project-name NAME` or `ROAD2M_PUBLIC_PROJECT_NAME=NAME` to select a different
single-component archive directory. Internal `ROAD2M_*` compatibility keys and
local data paths remain unchanged.

If the gate finds a prohibited path or a matching private marker, the packager
removes the candidate archive and exits nonzero. Review the implicated source
without printing its private matches, then create and inspect a new candidate.
Do not bypass a finding by adding a private value or affected file to an exception
list. In CI, `--allow-empty-private-inputs` is appropriate for the clean runner;
it does not substitute for comparing a local release candidate against the
owner's actual private inputs.

## What a passing check means

A pass is screening evidence, not proof that all content is safe to publish.
The scanner recognizes selected account/configuration inputs and supported numeric
and text formats. It does not understand every sentence or serialization, cannot
identify private information absent from those inputs, and may miss a short
amount whose account relationship is implicit or distant. It can also flag an
independently authored public number that equals an account observation.

Preserve source-only packaging, inspect the exact publication tree and Git history,
and review authored demo material and attribution before pushing. Local Codex
checkpoint trees and unreachable Git objects are outside the publication-history
scope; publish only the reviewed branch or tag, never mirror the local repository.
The
[public demo artifact guard](../scripts/check_public_demo.py) adds a separate check
for the static site: only its intended assets and required license notices are
accepted; news snapshots and the local research runtime are rejected. Neither a source scan nor a
frontend build means the Pages deployment has succeeded; see the
[public demo guide](public-demo.md) for that separate verification.
