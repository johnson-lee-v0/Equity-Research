# Replay a collection failure locally

Before retrying a full ticker run, turn the failing collection boundary into a
small offline regression. The saved example covers the META-style failure:
SEC submissions leave the website fields empty, the earnings release uses a
different investor-relations domain, and the suggested SEC exhibit returns 403.
The release links to a corporate company-info page; that page links to leadership;
leadership links back to the investor-relations site.

Run from the repository root:

```sh
.venv/bin/python -m pytest -q backend/tests/test_earnings_collection_replay.py
```

This runs three scenarios against temporary databases and a small synthetic site:

- The public collector resolves the company, verifies the release through the
  observed links, saves checkpoints, and then reuses valid identity after a
  restart. Only latest-event discovery runs; no extra identity search is needed.
- A newly archived corporate-page version invalidates the old proof. If its
  backlink remains present, collection verifies the actual links again and binds
  the replacement source version.
- If that backlink is gone, the old receipt cannot approve the release. A stubbed
  fallback search returns no proof and collection fails explicitly.

The fixture lives in
`backend/tests/fixtures/earnings_identity_replay/separate_ir_domain.json`. Its
company, dates and content are invented. Network connections are blocked, model
discovery is stubbed, the reporting date and identity timestamps are fixed, and
every fetched URL must have an explicit fixture. It does not read the live
database, run Codex, or change saved research.

The replay exercises event collection and durable identity reuse. It is not an
end-to-end ticker benchmark and does not test transcript parsing, document
downloads, valuation or model quality. The individual identity validation
branches remain in `backend/tests/test_earnings_sources.py`; checkpoint scheduling
and downstream handoffs remain in `backend/tests/test_research_workflows.py`.

For another failure, add the smallest synthetic set of pages and discovery
candidates that reproduces it. Start with a public collector or workflow entry
point, assert the user-visible outcome and required source lineage, and reject
unlisted fetches. Preserve the failure as a test before fixing it. Do not copy
private database records or entire downloaded documents into fixtures. Rerun
the narrow replay after the fix; use a live ticker run only when live retrieval
or model behavior still needs verification.
