# Teaching Notes API

A small FastAPI/SQLite project for the guide's final end-to-end walkthrough.
It accepts a note and an `Idempotency-Key`. A matching retry returns the same
record; a changed payload with the same key returns 409. Six tests cover create,
read, retry, conflict, validation and missing records.

Use a separate environment in a new working directory:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn notes_app:app --host 127.0.0.1 --port 8001
```

The platform remains on port 8000. This app is a local, single-user teaching
example with no authentication or key expiry. Do not treat it as a production
multi-tenant design. Real applications need owner-scoped operation keys,
authorization, retention and appropriate database/concurrency integration tests.
The end-to-end guide explains how to connect MCP agents, save verified project
knowledge and write reviewed content from that knowledge.
