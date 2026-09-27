# ResearchCouncil domain glossary

Use these terms consistently in prompts, code and the interface. Current
behavior is described in the [research workflow](docs/research-workflow.md);
this glossary does not define another workflow or model policy.

| Term | Meaning |
| --- | --- |
| Idea / lead | A starting point from a user, Reddit, Congress or monitored company. It has not yet established a supported investment conclusion. |
| Source | Retained original material with issuer identity, provenance, dates and an immutable content version/hash. A successful download alone does not verify a claim. |
| Fact | A dated observation supported by a specific source version and locator, with the metric, period, value and unit established where applicable. Verification means support in retained evidence, not a guarantee that the publisher is correct. |
| Opinion | An interpretation by an agent or person. Earlier conclusions and edited memory notes remain opinions, even when they quote facts. |
| Assumption / projection | An explicitly labeled input or estimate about an unknown outcome. Code may calculate a projection from reported facts without turning it into a reported fact. |
| Evidence packet | The selected source versions, observations and context supplied to an attempt. It is bounded and frozen for that attempt. |
| Shared memory | Reusable company context drawn from the ledger and linked vault. A remembered source outside the frozen citation set is a lead to verify and attach, not automatic citation authority. |
| Case / run | A persisted investigation of the original question, with its tasks, candidates, source lineage and decision revisions. |
| Task / attempt | A task is a unit of work; an attempt is a recorded execution of that task, retaining its inputs, model identity where relevant, and outcome. |
| Retry | Another execution of a specific failed or incomplete task or evidence check. It retains prior attempts and respects that step's scope and budget. It is different from opening a case or starting unrelated research. |
| Earnings workflow | The durable collection and analysis of an issuer's latest reported earnings event. Its package can be reused by a case and can retain explicit material gaps. |
| Reporting period | The fiscal quarter or year described by an observation. Its period end, publication date and retrieval time are separate dates. |
| As-of / evidence cutoff | The time boundary used to decide what information an analysis could use. Retrieving an old observation again does not make it current. |
| Holding horizon | The requested interval from the assessment date to the investment outcome being evaluated, such as 12 months. It is not the number of growth steps from a historical financial baseline. |
| Forecast span | The interval from a dated financial baseline to the projected financial period. This determines applicable growth steps; the target's holding horizon stays explicit. |
| Entry price / target price | An entry price is a condition for considering a position now or later. A target price is a conditional future valuation for a stated horizon. Neither implies an executed trade. |
| Completion | An execution outcome indicating the required processing finished. It can coexist with a Watchlist or Decline decision and disclosed optional gaps. |
| Coverage | Which required and optional materials, periods and metrics were actually available and checked. Completion does not assert full coverage. |
| Decision / revision | The supported investment judgment and its conditions. A revision preserves earlier judgments and source lineage rather than rewriting the original result. |
| Execution failure | A collection, provider, validation or processing problem. It is separate from a negative investment judgment. |
| Namespace | The boundary separating real, demo and simulation records. Shared memory and citations must respect it. |
