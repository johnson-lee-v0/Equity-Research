# ResearchCouncil backend

The backend is a local FastAPI service. It stores the canonical state in a
SQLite database under `data/`, keeps source content in `data/evidence/`, and
binds to loopback when launched by the project runner.

Install `backend/requirements.txt` into the project Python 3.11+ environment,
then run:

```sh
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Mutating requests from the local interface carry
`X-Road2M-Client: local-ui`. The real provider route uses the installed Codex
CLI with the existing ChatGPT login; no API-key or paid-provider route is
implemented. Run `POST /api/providers/codex/preflight` for an observable model
schema probe before relying on a combination. Ollama is optional and remains
disconnected until the local daemon and model are present.

Namespaces are `real`, `demo`, and `simulation`. Source content is treated as
untrusted evidence and cannot provide executable instructions. Recommendations
are persisted paper decisions and never mutate executed transactions.
