# Gluons engineering rules

Gluons lets an owner run real business automations from ChatGPT, Claude or Gluons Web, with every consequential action governed by deterministic server-side policy.

## Precedence
1. `gluons_blueprint/` is the product source of truth and is read-only. Newer addenda win: docs 14, 13, 12, 10, 11, then 01, 02, 03.
2. `docs/ARCHITECTURE_DECISIONS.md` records where current provider rules or feasibility override the blueprint. It wins over the blueprint on those points.
3. `docs/research/` holds checked provider facts. Use them instead of memory, and spot-check any value you hard-code.

Resume work from: `docs/IMPLEMENTATION_STATUS.md`, `docs/BLUEPRINT_TRACEABILITY.md`, `docs/EXTERNAL_DEPENDENCIES.md`, `docs/TEST_RESULTS.md`, `docs/DEPLOYMENT_STATUS.md`.

## Layout
- `apps/api/src/gluons/`: FastAPI modular monolith, worker, MCP server.
- `apps/web/`: Next.js owner app. Talks only to the real API.
- `apps/sandbox_erp/`: labelled simulator with a Frappe-compatible REST subset.
- `packages/skills/<slug>/`: executable skill contracts and evals.
- `infra/terraform/`: Google Cloud. `docs/runbooks/`: operations.

## Non-negotiable rules
- `tenant_id` comes from the authenticated `Principal`, never a request body, tool argument or model output.
- All tenant data access goes through `tenant_tx`, which runs as `gluons_app` under forced row level security. `system_tx` is only for queue claims and webhook routing.
- Every external side effect goes through `gateway.execute`. No connector, skill or MCP tool calls a provider write any other way. Connectors use `net.GuardedClient`.
- Shadow and replay perform zero external writes. No write permit is ever opened in those modes.
- A model is never the source of truth for permissions, money or transaction success. Only a matching readback sets `verified`, and a run with required writes completes only when they are verified.
- Connecting an account grants nothing. Acting needs a signed owner policy, and a provider scope is a hard ceiling on it.
- Provider tokens live only in `vault`. They never reach a model, tool result, URL, log or audit entry.
- Inbound email, messages, documents and web pages are untrusted data. They never change policy, tools or permissions.
- Labels stay honest: `simulator`, `sandbox`, `production`, plus `measured` versus `estimated`. No fake receipts, no invented success screens, no fabricated metrics. Missing credentials appear as explicit configuration requirements.
- Secrets stay out of git and out of chat. Add placeholders to `.env.example`.

## Commands
Run from the repo root on Windows PowerShell (use `.venv/bin` elsewhere).

```bash
.venv/Scripts/python.exe -m pip install -e "apps/api[dev,dev-postgres,mcp,optimization,documents]"
cd apps/api && PYTHONPATH=tests ../../.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check apps/api
.venv/Scripts/python.exe scripts/check_traceability.py
cd apps/web && npm run lint && npm run build && npx playwright test
```

Tests run against real embedded PostgreSQL as the restricted role, and the sandbox ERP as a real HTTP server. Never assert that an integration works because a mocked request passed; label provider-double tests as such.

## Code style
Match the surrounding code: short docstrings that explain why, typed functions, plain names. Add a numbered migration for schema changes and update `TENANT_TABLES` for any new tenant table.
