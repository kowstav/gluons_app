# Local reference API — demonstration only

Requires Python 3.12+. From repository root:

```bash
pip install -r reference_api/requirements.txt
uvicorn reference_api.app:app --reload
pytest -q tests/
```

Inspect `http://localhost:8000/docs`. This API keeps events in memory and requires only a demonstration `Idempotency-Key`. **It has no authentication, no durable storage, no Cloud Tasks, no OAuth, no real MCP transport, no ElevenLabs connection, and no external API execution. Never deploy to public production.** Its purpose is to document task semantics before writing the secure implementation described in the architecture plan.

Production must replace this with OIDC/OAuth, PostgreSQL, RLS, event outbox, durable workers, billable cost records, verified HMAC webhooks and policy enforcement.
