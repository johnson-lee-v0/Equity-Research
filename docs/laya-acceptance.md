# Laya and the five-question workflow — acceptance record

21 September 2026. Implementation and local acceptance checks completed.
The real-model test failures and financial validation limits remain explicit
below; this record is not a claim of investment accuracy or production reliability.

## Implemented behavior

New investment cases answer five code-owned questions: opportunity, valuation,
catalyst, downside and portfolio action. Each displays selected supporting or
contradicting facts, material unknowns and links to retained source lines.
Historical decisions retain their original contract and presentation.

A case defaults to one candidate, permits at most three, and shares a hard
15-fact budget across initial work, selected reused memory and continuation.
Initial collection allows five queries and six archived pages; the one public
evidence continuation allows one query and three pages. Provider schemas,
collection boundaries and commit validation enforce those limits. Over-budget
work is rejected with an explicit reason; evidence is never silently truncated.
These are acceptance bounds, not a promise of exact token cost. The CLI may
report search metadata after performing an action, which cannot be refunded.

The pinned English Laya model runs locally in an isolated reusable process.
It classifies a compact five-question evidence packet before Astra, and then
assesses Astra's reasoned response. Reddit intake also receives an advisory
classification, while the original retained post still reaches Astra. Laya
cannot independently discard a post, identify an instrument or verify a fact.

Astra's CIO judgment uses the authenticated Codex CLI with GPT-6 Astra Ultra.
Missing participation, unresolved disagreement, invalid evidence or an oversized
packet blocks Recommend. A reasoned override may resolve a disagreement.
Watchlist still needs an observable condition; Decline remains an explicit
judgment. Existing valuation, freshness, instrument and portfolio controls
remain in force. No trade execution is added.

Append-only receipts bind each local classification to its namespace, attempt,
proposal and exact selected evidence. Frozen pre-review facts must also pass
the later Astra attempt's own source/ownership boundary and review-date checks.
Invalid evidence produces an unavailable receipt before inference. Recovery
reuses completed work, finishes only missing candidate reviews and avoids
repeated CIO ledger entries.

## Validation evidence

| Layer | Evidence | What it establishes |
| --- | --- | --- |
| Backend | Final full suite: 533 passed, with one existing dependency deprecation warning. | Synthetic integration and boundary coverage; not financial performance. |
| Frontend | TypeScript and production build passed. | Build compatibility; the existing large-bundle warning remains. |
| Browser | Five question cards, source links, separate model judgments, private sizing disclosures and explicit unavailable states inspected. Compact navigation fits the narrow viewport without horizontal overflow or hiding the decision heading. | Rendered behavior on isolated synthetic cases. |
| Local model | Actual pinned weights completed both production-path classifications at 463 and 492 encoded tokens; a canonical five-question decision with resolved review was saved and the run completed. Astra's response in this test was deterministic. | Real local inference, receipt persistence and final projection; not real generative judgment or accuracy. |
| Codex handoff | An actual Astra Ultra call consumed the real local review and returned a reasoned Watchlist override. | Real model-to-model handoff through the production CLI adapter. |
| Discovery | A live one-query Codex lookup passed with the installed CLI's start/completion event protocol. | Bounded discovery transport, including delayed query metadata. |
| Independent review | Astra Ultra approved fact budgets, namespace/model policy, prospective lineage, recovery, financial gate previews, frozen evidence and response bindings. Its final targeted review passed 43 tests with no remaining blockers. | Scoped implementation review, not investment validation. |
| Preservation | After normal local startup, value-blind fingerprints of all original columns and rows across 11 record tables matched; firm remains paused. The migration adds an empty review-receipt column to historical attempt inputs. The final source-archive privacy check is recorded below. | Original research, account and portfolio records remain intact. |

The source-package privacy gate passed across all 181 manifest files and 181
archive entries, with no matching private values or credential findings. The
archive excludes the private database, original brief, credentials, model
weights and QA artifacts. No repository or website was published by this work.

The real generative handoff used an explicitly fictional ABC fixture with
synthetic filings, market and portfolio data; A00/A01/A03 were fixture outputs.
Its successful Astra call took about nine minutes and reported 51,844 input
tokens and 13,967 output tokens, including 7,768 reasoning tokens. Astra rejected
the assumed earnings persistence, valuation and loss basis. The complete
post-review packet exceeded Laya's context limit, so the saved case correctly
reported unavailable review and withheld Recommend.

That earlier run subsequently hit a recovery defect. The defect was fixed and
covered by regression tests; recovery then reached a public continuation that
the isolated fixture did not supply. It was not a completed full-run acceptance.
Two preceding Astra attempts timed out at 300 and 900 seconds. These failures
are retained as test evidence rather than omitted from the record.

The final actual-Astra repeat used the same fictional fixture with its
unavailable public collection capability stated explicitly. It executed and
reported 41,456 input tokens and 15,254 output tokens, but copied saved fact
identifiers into fields reserved for new output-local aliases. Runtime
validation correctly rejected it before a canonical decision was published.
The five-question generation schema now limits new aliases to c1–c15 while
leaving saved-fact reference fields intact. Its focused tests pass; no
additional live retry is claimed. This was not a
fresh real-market investment assessment or a test of the Reddit backlog.

The original backend baseline was 437 passes and two fixed-date failures.
Those fixtures now use their intended September evaluation clock; production
freshness policy was not relaxed. Source and receipt tests include wrong issuer,
stale observations, changed sources, unsupplied prior facts, failed local
inference, disagreement, pause/cancel, interrupted persistence and attempted
provider-authored review metadata.

## Runtime and quality limits

Model: `convaiinnovations/laya`, revision
`1c5edc17a7acd8701df6fc341c0d179f1c62c982`.
SDK revision: `573e5b62696ba441230cd6be71d593331b5d23af`.
The installer verifies approved model files and checkpoint hashes, disables
implicit download authentication and keeps dependencies outside the app venv.
Inference uses local weights and offline library settings; the process is not
an operating-system network sandbox. Opening saved pages does not run inference
or download a model.

The English checkpoint supports only 512 total encoded tokens per question,
including the state and options, with a 192-token head limit. The runtime
measures the real tokenizer input before the SDK can truncate it. Oversize,
missing model, timeout and malformed responses are explicit unavailable states.
Valid compact packets fit, but longer reasoning or material unknowns can still
make a review unavailable. The application does not delete those unknowns to
force agreement.

A three-example natural-language Reddit smoke set produced one correct label.
This is too small to estimate accuracy, but it exposed misclassification and
rules out claiming reliable autonomous screening. Likewise, resolved synthetic
reviews do not establish financial calibration. Scores are classifier outputs,
not market probabilities, and two models agreeing is not independent evidence
that an investment thesis is true.

Before relying on investment recommendations, evaluate a prospectively frozen
set of actual cases and measure whether Laya catches additional errors, whether
its unavailable/disagreement rate is useful, and the cost and latency per case.
The [investment-manager review](manager-review-2026-09-21.md) identifies separate
economic decision rules that this integration does not claim to solve.

Local installation and operation are documented in [setup](setup.md); no hosted
model API or published website is required. Private QA data, model files,
credentials and original portfolio records are excluded from source packaging.

Primary references: [official model](https://huggingface.co/convaiinnovations/laya)
and [official runtime](https://github.com/NandhaKishorM/laya).
