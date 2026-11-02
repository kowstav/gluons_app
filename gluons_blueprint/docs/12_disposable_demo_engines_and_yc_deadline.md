# 12 — Disposable demo engines: fastest path to an honest YC demonstration

**Status: implementation plan, not a claim of working integrations. Updated 2026-11-02.**

## 0. Decision

We accept the founder's distinction: **demo runtime is disposable, Gluons' user experience, agent-facing API, business permissions, task records and reproducible tests are durable.** We may deploy mature upstream software for a short-lived prototype and replace it later. However, a short-lived or non-revenue deployment is not automatically excluded from its license. Judge each use by the code's terms and deployment architecture, not the planned date of replacement.

**Three usage lanes:**

| Lane | Audience | Recommended policy | Software candidates |
|---|---|---|---|
| A — local/internal evaluation | Founders and engineers using fictional/sandbox data | Use source-available code only where its internal-evaluation terms allow. Do not strip notices; keep it isolated and document exactly what it does. | n8n self-host, Windmill self-host, Activepieces CE, Frappe/ERPNext |
| B — recorded YC demonstration | Screen recording of genuine actions run against test accounts; reviewers see recording, not backend credentials | Make an accurate disclosure of any temporarily borrowed engine; do not assume the absence of subscription fees itself authorizes a commercial demonstration. Prefer permissive implementations when uncertain. | Activepieces CE, Python adapter services, OR-Tools, Docling, ElevenLabs SDK and paid voice service |
| C — publicly accessible demo or customer pilot | External people can interact with Gluons and their own connected data | Treat as externally provided product. Use license-cleared components or obtain written authorization for restricted software, and observe AGPL obligations if applicable. | Activepieces CE core, Gluons own control plane, permissive libraries and official APIs |

**Source checks (2026-11-02):** n8n's Sustainable Use License limits use to internal business purposes or non-commercial use and restricts offering functionality to customers; review specific deployment with n8n if uncertain. Windmill states its open-source edition uses AGPLv3, and its white-label embedding requires a special license. Activepieces states its community core uses MIT, while enterprise/embedded builder features are separately licensed. Never deploy an enterprise-only package under a community license. Sources: https://github.com/n8n-io/n8n-docs/blob/main/docs/n8n-community-license/README.md ; https://www.windmill.dev/brand ; https://www.windmill.dev/docs/misc/white_labelling ; https://www.activepieces.com/docs/about/license . This is engineering compliance guidance, not legal advice.

## 1. Chosen fast demo architecture

```text
Claude or ChatGPT (if remote tool integration configured) / Gluons UI
         |
      Gluons MCP gateway and owner control API     [OUR CODE]
         |
       Task ledger + owner approvals + task status  [OUR CODE, Postgres]
         |
      Typed temporary execution adapter             [OUR CODE]
         |          |           |           |
    Activepieces   Docling     OR-Tools     ElevenLabs
     CE runner      parser     optimizer    cloud voice + SDK
         |          |           |           |
        Gmail test inbox / ERPNext sandbox / Google Sheets test records
         |
    Gluons external receipt verification + evidence report [OUR CODE]
```

If the MIT Activepieces runner is difficult to expose safely before the deadline, **use direct Python actions** rather than spending hours getting a general workflow editor to work. The YC demo does not need the vendor's editor, and should never present it as Gluons' original technology. It needs the Gluons UI and demonstrably real business actions.

There should be **no separate n8n/Windmill customer-facing login** in the YC demo. If these engines are evaluated, they run only in a restricted internal environment, and the Gluons UI must not misleadingly imply that proprietary workflow code was developed in-house.

## 2. Create a replaceable execution boundary first

The business-facing API is unchanged regardless of what engine is underneath:

```
POST /v1/automations/{skill_id}/install
POST /v1/tasks                  # create; returns task_id
POST /v1/tasks/{id}/start-trial # historical_replay, shadow, supervised
GET  /v1/tasks/{id}
POST /v1/tasks/{id}/approve
POST /v1/tasks/{id}/pause
POST /v1/tasks/{id}/resume
POST /v1/tasks/{id}/cancel
GET  /v1/tasks/{id}/evidence
```

Define a `BackendAdapter` with `submit(run, action)`, `status(external_run_id)`, `cancel(external_run_id)` and `receipts(external_run_id)`. Use implementations `ActivepiecesDemoAdapter`, `PythonActionsAdapter`, and later `GluonsNativeAdapter`. Each adapter MUST emit the same event envelope and carry a backend/version field. The data model should never store only a runner-specific workflow ID.

Minimal event envelope:

```json
{
  "tenant_id":"demo_merchant",
  "task_id":"tsk_abc",
  "run_id":"run_1",
  "skill_id":"multichannel_order_entry",
  "engine":"activepieces-demo",
  "engine_version":"pinned-commit-or-image-digest",
  "action":"erp.order.create_draft",
  "mode":"supervised",
  "policy_id":"policy_1",
  "idempotency_key":"demo_merchant:email_12:erp_draft",
  "input_hash":"sha256-of-approved-canonical-payload",
  "state":"waiting_for_approval",
  "provider_receipt":null,
  "verified":false
}
```

The policy gate executes **outside** the borrowed engine. If the engine is restarted, fails, or changes, Gluons must still know whether the task was approved, whether an external write occurred, and whether it can safely retry. A task is *completed* only after an authoritative readback from the connected sandbox confirms the expected result.

## 3. Repo choices optimized for speed

| Capability | First choice | Why | Fallback | Keep for end product? |
|---|---|---|---|---|
| App integrations | Activepieces CE / versioned pieces | MIT core and reusable adapters; do not use EE embedding/admin features | Official vendor SDKs | Possibly |
| Demo workflow runner | Activepieces CE or simple Python orchestrator | Fast branches/webhooks without inventing a full workflow editor | Python + Google Cloud Tasks | Replaceable |
| Local workflow research | n8n Community (internal-only, permitted cases) | Useful existing templates and examples | Activepieces CE | No by default |
| Script/job engine | Windmill OSS (license-compliant standalone use) | Good code-first runner | Python + Cloud Run jobs | No by default |
| Docs | Docling + MarkItDown | PDFs and attachments | Direct text/PDF parsing | Yes if license checked |
| Invoices | invoice2data | Extraction templates | Docling + schemas | Yes |
| Dispatch | Google OR-Tools + OSRM | Optimization with constraints and routes | OR-Tools with fixed test matrix | Yes |
| CRM/ERP proof | ERPNext sandbox | Real CRUD API and real readback | Small test ERP designed and labeled as simulator | Customer integrations later |
| Voice | ElevenLabs SDK + ElevenLabs paid voice agent | Real call/voice workflow using hosted services | Text/audio playback clearly labeled non-live | SDK yes; service separately paid |
| Agent bridge | MCP SDK | Standard tool invocation | Gluons native chat + own API | Yes |

**No claim of feature parity:** these repos deliver components, not the entire intellectual or operational content of the 30 competing startups. Gluons still needs catalog aliases, business policies, domain exceptions, permissions, and integration-specific mappings.

## 4. Shortest credible YC demo: 3 live capabilities, 1 shared control plane

**Use sandbox identities, synthetic customers, and authorized owned accounts, but make real API calls and collect real provider receipts.**

### Track A: order entry (the anchor proof)
1. Seed ERPNext sandbox with `ACME-Restaurant` and catalog SKUs `FISH-1`, `RICE-2`, and customer-specific aliases.
2. Send a sample PO email or PDF to a test Gmail inbox. Prefer authorized Gmail connection; alternatively post a webhook using a signed test fixture and clearly state this is webhook ingestion.
3. Receive event. Write immutable `event_id` and a task record. Use Docling/MarkItDown to parse the attachment.
4. A model generates a structured `OrderDraft` using a strict schema with evidence spans and confidence scores.
5. Deterministic matching resolves every SKU and unit; if ambiguous, hold for human review.
6. UI shows the extracted lines and exact ERP payload, with an **Approve** action.
7. On approval, submit to ERPNext **sandbox**, check idempotency key, then fetch the created order by ID and store timestamped receipt.
8. Owner asks the AI assistant to report the task status; result matches the dashboard.

### Track B: real ElevenLabs call
1. Configure an ElevenLabs test agent and test phone number/SIP route, with explicit consent and no uncontrolled outbound dialing.
2. Connect the voice agent's tool to `POST /v1/voice/submit-order-intent` after verifying webhook signatures/secret.
3. Caller orders an item from the same ERP catalog. Use the same skill to resolve the item. Show the resulting order **draft**; require human approval before write.
4. Capture ElevenLabs conversation ID, call cost estimate and Gluons task ID. Never imply a call completed if only text simulation occurred.

### Track C: dispatch optimization
1. Seed 8 technicians, 15 jobs and travel-time matrix, with explicit skills, operating hours and customer time windows.
2. Use OR-Tools to produce candidate assignments satisfying hard constraints. Compare against fixed baseline on feasibility/travel cost, and show where real-world metrics are *estimated*.
3. Let the owner approve one assignment to a sandbox dispatch table. Read back to verify it.
4. Show that the Gluons agent can pause *dispatch only*, leaving order intake running.

**Critical YC narrative:** owner asks assistant → Gluons discovers skill → owner sees permissions and trial → agent coordinates real work → Gluons pauses/approves/reports independently → same infrastructure runs order processing, calls and dispatch.

## 5. Demo honesty and audit requirements

- Label upstream libraries and external services as dependencies, not Gluons-developed inventions.
- Distinguish **recorded real sandbox transactions**, **simulated data**, **live calls**, and **UI-only simulations** clearly.
- Use fake customer details or controlled test accounts. Never place personal business data in screenshots without consent.
- Keep screenshot-ready proof: input event, extracted output, approval, provider request ID, verified provider readback, cost/latency trace, pause test.
- A functional demo should work from a clean environment using documented credentials (not hardcoded secrets), and a repeatable seed script.
- Do not claim 31 live automations based only on 31 specifications.

## 6. What to build today, with an explicit stop rule

**Gate 1 — Reliable proof of external action.** Gluons-owned task ID, controlled approval, ERPNext sandbox write, independent readback. If runner integration fails, immediately swap to `PythonActionsAdapter`; do not debug an upstream platform for hours.

**Gate 2 — Conversational control.** MCP `list_automations`, `create_task`, `get_task`, `approve_task`, `pause_task`, `get_evidence`; if ChatGPT/Claude connection is not enabled, use Gluons native chat UI to show the same backend and disclose which client was used.

**Gate 3 — Multiplicity.** Real ElevenLabs call + OR-Tools dispatch on same Gluons API, or the strongest one of these if provider onboarding blocks the other. Avoid claimed live interactions that aren't live.

**Gate 4 — Record evidence.** Save 2–4 minutes of product demo footage separate from the founder-only application video, using genuine task execution and visible confirmations.

YC confirms that its **founder video must be one minute with founders talking, not product footage**; its product demo is submitted separately. Source: https://www.ycombinator.com/video . As checked 2026-11-02, YC Winter 2027's on-time application deadline is **Nov 2, 2026 at 8 PM PT, equivalent to Nov 3, 2026 at 9:30 AM IST**. Verify https://www.ycombinator.com/apply before submitting.

## 7. Deliberately postpone (without dropping from long-term design)

The 31-skill library, usage-based model router, self-hosted privacy mode, field-rep onboarding, enterprise OAuth, model benchmarking, and full GCP hardening all remain in the architectural specification. They should **not** block submission of a strong, truthful demo of the general Gluons execution concept. This is a scheduling/verification choice, not a reduction in the product vision.

## 8. Exit strategy after YC demo

Keep all runner-specific configuration under `demo_backends/`, including checked-in workflow definitions, pinned image digests, license dossiers and reference traces. The permanent product APIs, task ledger, approvals and event log must not contain dependencies on runner-specific workflow schema. Implement `GluonsNativeAdapter` and rerun identical regression/evidence tests against the replacement. Retire each demo engine only after parity tests pass and downstream receipts reconcile. Revoke test tokens and phone routes after recorded demonstration.
