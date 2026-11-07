# Gluons

Owner-controlled business automation with a real PostgreSQL control plane, FastAPI,
durable workers, authenticated MCP and a responsive Next.js owner application.
The authoritative product specification is in [gluons_blueprint](gluons_blueprint/README.md).

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -c requirements.lock -e "apps/api[dev,dev-postgres,mcp,optimization,documents]"
npm ci --prefix apps/web
.venv/Scripts/python.exe scripts/dev.py
```

Open http://localhost:3000 and register. Local verification and recovery emails are
written to `.gluons-data/mail`; open the link in the latest message. No real email
is sent by local authentication. API documentation: http://localhost:8000/docs.
The runner starts PostgreSQL, migrations, all 31 skill contracts, the worker, and
the explicitly labelled ERP simulator. Ctrl+C stops application services.

Alternatively, `docker compose up --build` starts the local containers. Compose
configuration is validated; container builds and runtime remain separately unverified.

## Verify

```powershell
.venv/Scripts/python.exe -m pytest apps/api/tests
.venv/Scripts/python.exe -m ruff check apps/api
.venv/Scripts/python.exe scripts/check_traceability.py
npm run build --prefix apps/web
cd apps/web
npm run test:e2e
```

Browser tests launch an isolated real API/PostgreSQL instance. They cover desktop
and mobile owner flows without performing external provider actions.

## Capability boundaries

All 31 skills are discoverable. Order processing has a tested simulator write and
independent readback; the other 30 have executable draft preparation, validation
and selected domain calculations. They cannot activate provider actions merely
because their contract parses. Real ERPNext and other providers need their own
sandbox tests. See [skill capabilities](docs/SKILL_CAPABILITIES.md).

Gmail OAuth and verified-send code require your own Google OAuth configuration.
Model providers require configured credentials plus reviewed descriptors and
passing model-specific evaluations. No provider integration is live-verified by
this repository's provider-double tests. Never paste credentials into chat.

Read [implementation status](docs/IMPLEMENTATION_STATUS.md),
[external dependencies](docs/EXTERNAL_DEPENDENCIES.md),
[assistant setup](docs/ASSISTANT_SETUP.md), [account connections](docs/ACCOUNT_CONNECTIONS.md) and
[deployment status](docs/DEPLOYMENT_STATUS.md) before enabling external actions.
