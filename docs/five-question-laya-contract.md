# Five-question research and Laya/Astra review

Architecture decision: 21 September 2026. This supplements the manager contract. It applies to new lean research cases marked `research_contract="five-questions.v1"` in their code-owned frozen run/attempt inputs. Historical research and projections remain unchanged. Implementation and synthetic verification leave research and dispatch paused.

## Five questions

Each selected candidate has exactly these five keys, in this order. The question text is code-owned; the answer is a research judgment, not a fact certified by a classifier.

| Key | Question |
| --- | --- |
| `opportunity` | What is the opportunity, and what is the market missing? |
| `valuation` | What is it worth versus the entry price and alternatives? |
| `catalyst` | What can change the outcome, and by when? |
| `downside` | What would prove us wrong, and how could we lose money? |
| `portfolio_action` | What should we do now, and does it fit the portfolio? |

Unknown consensus, future outcomes, borrow terms and benchmark returns remain unknown. A scheduled thesis review is not evidence that a catalyst will occur. Valuation and sizing remain deterministic calculations under the existing contract.

## Reduce actual research work

Keep A00 routing, A01 discovery, A03 synthesis and A11 assessment. Add no researcher or autonomous branch. A00/A01 receive the five questions before collection and look for evidence that could change their answers, including an opposing explanation.

- Default to one candidate; permit at most three when the request requires alternatives. Enforce this after normalizing nested/top-level candidate aliases as well as in the new provider schema. Do not silently drop additional requested candidates: report the bounded comparison scope.
- Initial discovery: at most five targeted queries and six fetched public pages per case. Existing deterministic market collection stays bounded and separate. The one existing evidence continuation targets one named decision-changing gap, at most one query and three fetched pages. Do not spend that continuation on private inputs, future events or unsupported capabilities.
- Hard case budget: at most **15 distinct research facts**, including reused prior facts selected into this case and every material observed input required by its valuation, thesis and price calculations. This is a case budget, not 15 per candidate or per stage. Code calculations, raw archived bars and explicit forecast assumptions are not additional research facts.
- Each question references at most two supporting facts and one contradicting fact. A fact may answer several questions without being duplicated. Required valuation inputs remain in the detailed audit even when they are not one of a question's three displayed facts; they still count toward the case budget.
- A03 uses the available budget; A11 normally references its committed facts and may add only the remaining budget. Repeating an A03 fact is not a way to obtain extra capacity. A01 remains URL discovery with zero fact claims.
- Apply limits in the attempt-specific JSON schema **and** commit validation, using the frozen contract and prior fact pool. Reject an over-budget output with bounded corrective feedback; never truncate facts or silently discard calculation dependencies. When the material case cannot be supported within the budget, retain an explicit scope/evidence limitation and do not publish Recommend. No automatic budget expansion or continuation loop.
- Keep answers concise (800 characters each), implications at most 400 characters and at most two material unknowns per question. New A03/A11 narrative analysis is a short synthesis (at most 4,000 characters), not a second long report. Legacy schemas remain readable with their former limits.

## Public interfaces

Add provider proposal `key_questions` to `CandidateDecisionBrief` and its compatible aggregate path. For this contract require five unique keys exactly once after normalization:

```text
KeyQuestionProposal {
  key: opportunity|valuation|catalyst|downside|portfolio_action,
  answer: string,
  decision_implication: string,
  supporting_claim_ids: string[0..2],
  contradicting_claim_ids: string[0..1],
  unknowns: string[0..2]
}
```

Provider proposals do not contain verification flags or Laya results. Extend the existing reference resolver to include `contradicting_claim_ids`; supporting IDs already use its recognized field name. Apply the same namespace, supplied prior fact/output, local alias and exact frozen source-version/hash rules. A question with unresolved or semantically invalid references remains incomplete; counting a reference is not validation.

Add `research_contract: string|null` to the canonical case. Add `key_questions` (default empty for historical cases) and `joint_review` to the canonical candidate:

```text
KeyQuestion {
  key, question, answer, decision_implication,
  evidence_status: complete|partial|unavailable,
  verified_facts: [{
    fact_id, role: support|contradiction, claim, value, unit, period,
    source_ref, locator, semantic_status, freshness
  }],
  unknowns: string[]
}

JointDecisionReview {
  status: not_run|agreed|resolved|pending|unavailable,
  laya_model: string|null,
  laya_revision: string|null,
  laya_outcome: recommend|watchlist|decline|needs_evidence|null,
  astra_outcome: recommend|watchlist|decline|needs_evidence|null,
  resolution: string,
  disagreements: [{question_key, reason}],
  reviewed_at: string|null
}
```

All displayed verified facts and `evidence_status` are projected by code from repository validations and current decision-as-of freshness. Unsupported references belong in unknowns/blockers, not in `verified_facts`. `complete` describes evidence coverage for the answer, not certainty that the investment thesis is true. Keep detailed source-version/binding checks in the existing evidence audit. Schema generation must update frontend API types.

## Real local Laya participation

Use the verified general `convaiinnovations/laya` model, pinned to an immutable revision; do not use the unrelated typed-workflow variant as a finance model. Run actual model weights in an isolated local runtime/venv with a reused worker process. Do not add Torch to the main application environment or represent deterministic heuristics as Laya inference. The adapter is a classification service, not a replacement for the existing generative provider interface.

The runtime accepts bounded text plus explicit choices and returns the actual chosen option and model scores. Respect its tokenizer limits (512 total encoded tokens per question, including the state and question/options, with a 192-token head limit); measure the complete encoded input. Never silently truncate. Oversize, missing model, invalid output and timeout return `unavailable` with a reason. Cache only by model revision, exact input hash and ordered choice definitions. Run inference outside SQLite write transactions with bounded concurrency and timeout. No remote inference, automatic download on a GET, shell-built prompts or private text in process arguments/logs.

Use three bounded integration stages, without creating an agent:

1. For Reddit intake only, classify the retained post before A00 using explicit thesis / ticker-only YOLO / noise / uncertain options. Give this actual recorded result to Astra's existing Chief of Staff assessment. This is advisory intake evidence: it cannot auto-discard a post, establish ticker identity, authorize dispatch, or bypass existing screening permissions. A00 still handles the original retained post under its existing policy. A long post that cannot fit without dropping material content produces an unavailable classifier assessment, not a silently truncated input or automatic rejection.
2. After A03's facts are committed and code-validated, make one disposition classification per candidate before A11. Its designed compact packet represents all five questions, selected decisive canonical facts including contradictions, material unknowns and deterministic gate/valuation summaries. It is explicitly a selected-evidence assessment, not a rereading of every archive. Use existing short answer/implication fields and exact selected fact values/units/periods; do not invent a classifier-generated fact summary. If the complete designed packet exceeds token limits, return unavailable rather than truncating. Choices are Recommend, Watchlist, Decline and Needs evidence. Never infer an investment probability or price forecast from classification scores.
3. Supply that disposition to the existing A11 Astra call. Astra must address material disagreements and reference the facts behind its judgment. Provider field `laya_response` contains `{position: agree|override|unable, reason, fact_claim_ids, question_keys}`; this is Astra's response, never a code-owned model receipt. After validating Astra's proposed output, run one bounded Laya resolution classification per candidate. Its choices are `accept_resolution`, `disagreement_remains`, and `insufficient_evidence`, against Astra's response and the same frozen selected evidence. A reasoned override can therefore be accepted; equal votes are not required. Oversize resolution input is explicitly unavailable.

The ordinary single-candidate case uses two local classifications, or three with Reddit intake. An explicit three-candidate comparison uses at most six, or seven including intake; batch those records through the same worker, without extra research-generation calls. Do not describe a multi-candidate batch as only one inference. If the final disagreement remains, show `joint_review.status=pending`, identify the question/reason and block Recommend pending review. There is no automatic second Astra call in this implementation. Missing or failed Laya participation is `unavailable`, not Decline or fabricated agreement. An explicit supported Decline remains a recorded Astra judgment with the unresolved joint-review status visible; Watchlist still requires an observable trigger.

For new five-question cases A11 uses the existing Codex CLI route with `gpt-6-astra` at the established Ultra decision setting. Reddit A00's joint intake assessment also uses Astra. Composer overrides may still apply to other stages. An incompatible override at an Astra-required stage cannot silently bypass this contract: report the required setting explicitly. Do not add API-key billing or substitute another model while labeling the result Astra.

## Persistence and authority

Persist real classifier receipts in a new append-only, namespace-scoped `decision_model_reviews` table, keyed to run, attempt, candidate, phase and exact input hash. Retain model ID/revision, ordered choices, runtime version/device, token counts, scores/result, fact IDs/source versions and failure reason. Freeze the pre-Astra receipt IDs in `attempt_decision_inputs`; persist the post-Astra receipt against the validated proposal hash. Repeated identical requests may reuse a recorded receipt without overwriting it.

The orchestrator creates receipts through the local adapter. `AgentOutputPayload` must reject provider-authored `joint_review`, Laya scores, evidence verification or receipt fields. Canonical projection consumes only matching repository receipts, never a provider's claim that Laya agreed. Post-review inputs must be prepared using the same normalized proposal and frozen reference resolution that commit/projection uses; input/hash mismatch is unavailable, not accepted review. Perform inference before the final commit transaction, then verify the receipt binding during persistence.

Add two contract-specific recommendation checks: `five_question_evidence` and `joint_decision_review`. They supplement every existing semantic, freshness, identity, valuation, sizing and portfolio gate. Five answers or classifier approval can never waive an existing failure. Missing current-contract fields block new recommendations; do not retroactively block historical projections merely because their contract predates this change. Existing sources, attempts, outputs, portfolio snapshots, baselines and case revisions remain immutable.

## Implementation boundaries

- `backend/app/schemas.py`: proposal/canonical question and review types, Astra response; additive legacy defaults.
- `backend/app/research/decision_questions.py` (new): question definitions, budget/reference validation, verified question projection, compact classifier packets and joint-review policy.
- Local runtime adapter/install utility supplied by the runtime worker: actual weights, pinned revision, isolated worker, health/status and token-safe classification.
- New migration and `backend/app/memory/repository.py`: append-only review receipts, contract-aware commit budget enforcement, normalization/read-model preservation and namespace export.
- `backend/app/agents/roles.py` and `backend/app/orchestration/workflow.py`: new-run contract marker, five-question collection prompts, bounded queries/pages/provider schemas, pre/post classifier integration, frozen receipts and A11 Astra requirement. Preserve pause checks before each inference/generation and after awaited calls.
- `backend/app/research/fact_references.py`, `decisions.py`, `case_store.py`: all question references resolve under existing rules; code-owned question/review projection and additive gates.
- `frontend/src/panels/DecisionWorkspace.tsx`, generated types and scoped CSS: five question cards for the selected candidate, a concise Laya/Astra review line, expandable evidence and existing detailed memo. Legacy cases retain the current historical UI. All new free text and private derived fields use existing privacy disclosures. No headline fact-count badge or financial-confidence percentage.

## Acceptance

Use synthetic isolated cases plus a real-weight local inference smoke test. Verify exactly five unique questions; global 15-fact/three-candidate limits at schema and commit; no silent truncation; reused IDs and contradiction refs preserve source/namespace boundaries; forged review receipts fail; missing/stale/wrong-issuer facts stay blocked; model disagreement/unavailability cannot publish Recommend; supported agreement and accepted reasoned override can pass alongside all existing gates. Verify pause mid-inference prevents the next model call, and GETs never execute inference/downloads. UI shows five concise questions, source-linked decisive facts, explicit unknowns and both model roles without exposing private values.

Baseline note: before these changes, 21 September's suite had 437 passes and two date-sensitive fixture failures (instrument identity shared-budget test and ambiguous-input calculation commit test). Pin their intended synthetic clock explicitly; do not weaken production freshness to accommodate the calendar change.
