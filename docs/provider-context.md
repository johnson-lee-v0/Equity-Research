# Preparing evidence for a model

[`prepare_provider_context`](../backend/app/orchestration/provider_context.py)
is the single entry point for preparing the bounded research evidence view.
The orchestrator calls it **after** saving the full decision inputs for the
attempt. It returns a new context dictionary and does not modify its inputs,
read or write the database, fetch documents, calculate valuations, or call a
model.

The module owns:

- Compact market indicators and scenario aggregates. Full observations and
  simulation paths stay in the saved research inputs.
- Compact copies of previous decisions, including bounded watch information.
- Source priority: interim events, latest transcripts, the current discovery
  handoff, instrument identity, selected market data, then older evidence.
- Evidence content limits and space reserved for earnings and valuation context.
- Original citation locators and explicit omitted-source/partial-call coverage.

The evidence budget is not a token limit or a guarantee about the size of the
entire prompt. It retains the existing 320,000-character evidence allocation
with a 16,000-character minimum after the earnings reservation. Other prompt
fields and serialization add their own size. This refactor leaves those limits
and the provider's own input checks unchanged.

[`source_context.py`](../backend/app/research/source_context.py) holds the shared
archive helpers: reading explicit structured metadata and excerpting source
text while keeping its original line numbers. Market identity checks use the
same metadata reader as evidence preparation; titles and prose do not establish
instrument identity.

Keep persistence, source verification, financial preparation and provider
execution in their existing services. The projection is a view of verified
inputs, not another source of facts or a second workflow engine.

Focused verification from the repository root:

```sh
.venv/bin/python -m pytest backend/tests/test_provider_context.py backend/tests/test_lean_workflow_contract.py backend/tests/test_earnings_research_context.py backend/tests/test_valuation_provider_projection.py -q
```

The public-boundary tests cover source priority under pressure, reserved space,
transcript coverage and unchanged inputs. Existing workflow tests inspect the
actual provider request and its durable attempt receipts. For collection
failures, use the separate [offline collection replay](collection-replay.md).
