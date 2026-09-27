# Saved case reader

The UI opens saved Research and Watchlist cases with
`GET /api/runs/{run_id}?namespace=real&view=reader`.
Omitting `view`, or using `view=full`, retains the complete existing response.

Selecting a card immediately shows a loading view with a way back to the list.
An unsuccessful read shows its error and **Try again**, which reloads the saved
case without starting research. Concurrent reads of the same case share one
request; switching cases or returning to the list cancels the pending read.
The request times out after 30 seconds so a stalled connection remains retryable.

The reader changes only a historical output claim's `excerpt` when it exceeds
4,096 characters and that claim already has a shorter, nonempty string in
`matched_excerpt`. It uses the exact matched text already preferred by the
claim viewer. Every claim, citation, locator, validation field, decision,
calculation, source reference and history entry remains available. The sole
available excerpt is never shortened.

`reader_projection.deferred_claim_excerpts` identifies each replacement and
links to the full output endpoint. Opening an output or source still fetches
the complete retained record. This is a response projection: it never changes
archived source text, output bytes or historical decisions.

Some earlier validation records repeat an entire source document in every
claim's excerpt. This avoids transferring those unused copies whenever a card
opens or refreshes. It does not change research execution or model prompts.

The contract lives in
[`project_run_reader`](../backend/app/api/run_reader.py) and its
[API and preservation tests](../backend/tests/test_run_reader.py).

```sh
.venv/bin/python -m pytest backend/tests/test_run_reader.py -q
```
