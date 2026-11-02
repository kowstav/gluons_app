# Internal interfaces between modules

Each module owner implements these exact signatures, so modules built in parallel fit together. All functions take a verified `Principal` first, enforce permissions themselves, and return plain JSON-serialisable dicts. REST routes and MCP tools are thin wrappers over them. Errors are `GluonsError` with a canonical code.

## Connections: `gluons.connections.service`
```python
begin_session(principal, provider: str, *, capabilities: list[str], automation_plan_id: str | None = None) -> dict
    # {session_id, setup_url, expires_at, status: "requires_external_authorization", provider, next_step}
session_status(principal, session_id: str) -> dict       # pending|authorized|needs_action|failed|expired, no tokens
list_connections(principal) -> list[dict]                # provider, account_display, scopes, status, environment, health
verify_connection(principal, connection_id: str) -> dict
confirm_account(principal, connection_id: str) -> dict   # owner confirms the displayed external identity
disconnect(principal, connection_id: str) -> dict        # revokes, fences dependents to waiting_external, reports effects
```

## Telephony: `gluons.telephony.service`
```python
begin_route(principal, mode: str, *, number_hint: str | None, country: str | None, automation_id: str | None) -> dict
    # {route_id, state, required_owner_steps, ...}. mode: new_number | carrier_forwarding | sip_pbx
route_status(principal, route_id: str) -> dict
verify_test_call(principal, route_id: str) -> dict
```

## Models: `gluons.model_gateway`
```python
complete_structured(*, tenant_id, purpose, schema: type[BaseModel], system: str, user: str, automation_id=None,
                    run_id=None, step_id=None, skill_id=None, model_policy: dict | None = None) -> StructuredResult
    # StructuredResult: value (validated), model_identifier, usage, cost_usd, latency_ms, attempts
    # Raises GluonsError("MODEL_UNAVAILABLE") when no permitted model is configured. Callers then
    # use a deterministic fallback or route to a person. They never fabricate output.
is_configured(tenant_id: str, purpose: str) -> bool
list_models(principal) -> list[dict]
evaluate_candidate(principal, skill_id: str, provider: str, model_id: str) -> dict
set_skill_model(principal, automation_id: str, provider: str, model_id: str, expected_version: int) -> dict
```

## Trials and reports: `gluons.trials`, `gluons.reports`
```python
trials.get_report(principal, trial_id: str) -> dict
trials.complete(principal, trial_id: str) -> dict
reports.operational(principal, *, period_days: int = 30) -> dict
reports.roi(principal, automation_id: str | None = None) -> dict   # measured and estimated kept separate
```

## Knowledge: `gluons.knowledge`
```python
add_document(principal, *, title: str, body: str, kind: str = "note", source: str | None = None) -> dict
list_documents(principal) -> list[dict]
search(tenant_id: str, query: str, *, limit: int = 5) -> list[dict]   # used by voice tools and skills
```

## Business launch: `gluons.launch`
```python
create_plan(principal, *, goal: str, country: str | None, product_category: str | None,
            budget_usd: float | None, fulfillment_model: str | None) -> dict
get_plan(principal, plan_id: str) -> dict
start(principal, plan_id: str, expected_version: int) -> dict
timeline(principal, plan_id: str) -> dict
pause(principal, plan_id, expected_version) -> dict
resume(principal, plan_id, expected_version) -> dict
cancel(principal, plan_id, expected_version) -> dict
evidence(principal, plan_id: str) -> dict
```

## Assistants: `gluons.oauth.service`
```python
list_bindings(principal) -> list[dict]
revoke_binding(principal, binding_id: str) -> dict       # revokes that binding and its tokens only
```

## Field representative: `gluons.assist`
```python
request_session(principal) -> dict
consent(principal, session_id: str) -> dict              # owner only
end_session(principal, session_id: str, report: dict) -> dict
```

## Skill step extension
A skill's domain code lives in `gluons/skills/domain/<module>.py` and registers functions with `@domain("name")`. A function receives `(ctx: StepContext, node: Node)` and returns a `StepOutcome`. It reaches external systems only through `ctx.action(...)`. See `skills/domain/orders.py` for the pattern and `packages/skills/multichannel_order_entry/contract.yaml` for the graph.

## Adding a connector
Create `gluons/connectors/<provider>.py`: define typed input models, call `register_action`, implement `ActionConnector`, call `register_connector`. Use `GuardedClient` with an explicit host allowlist. `prepare` must only read. `lookup` must find an earlier attempt after an ambiguous failure, or raise `ProviderError("ambiguous", ...)` when the provider cannot, which sends the run to a person. `verify` must read independently of the write response.

## REST routes
Add a module in `gluons/api/routes/` exposing `router`. Discovery is automatic.

## Schema changes
Migrations are reserved so parallel work does not fork the chain. Edit only your own file, and add any new tenant table to `TENANT_TABLES` plus its RLS policy and grants in that migration:

| Migration | Owner |
|---|---|
| `0003_assistants.py` | MCP and OAuth |
| `0004_connections.py` | Connectors and channels |
| `0005_skills.py` | Skills and launch |
| `0006_models.py` | Models, trials, knowledge, assist |
