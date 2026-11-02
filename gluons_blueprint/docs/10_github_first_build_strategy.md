# 10 — GitHub-first implementation strategy: reuse 24 repositories across 31 skills

**Status (2026-11-02): ARCHITECTURAL PLAN / NO UPSTREAM CODE INSTALLED.** This document revises the prior greenfield build. It does not claim the proprietary automation products mentioned earlier have published their own code or that the listed components recreate all their features.

> **Demo-only exception (November 2, 2026):** `docs/12_disposable_demo_engines_and_yc_deadline.md` supersedes the blanket `reference_only` deployment recommendation for internal prototypes where actual upstream terms allow their use. It does *not* authorize restricted source in an external Gluons SaaS. Keep all such engines isolated and replaceable.

## 1. Decision: adapters over forks; a control plane over a workflow editor

The product's valuable proprietary layer should be Gluons' owner-facing agent compatibility, business task state, human approvals, evaluation/trial records, usage ledger and cross-workflow knowledge. Reuse the rest selectively:

- **Pull in a library** when it is permissively licensed, small enough, and provides a deterministic building block (Docling, invoice2data, OR-Tools, MCP SDK).
- **Run an isolated sidecar** when it is a large independent engine (VROOM/OSRM, potentially Chatwoot or Novu). Communicate over a narrow authenticated API.
- **Connect to software through its official APIs** when it is a business system that customers already use (ERPNext, Twenty, Cal.com, Mautic). Do not assume open source means its code must be merged into Gluons.
- **Borrow interface and operational ideas only** from projects with restricted source-available licenses (n8n, Windmill) unless appropriate contracts are obtained. Do not copy source or workflow templates with unverified licensing.
- **Write Gluons-specific logic** for approval rules, idempotency, activity permissions, ROI calculation, safe cancellation, customer-specific business constraints and exact external receipts. Open-source packages don't solve these automatically.

The 31 skills remain the *product taxonomy*. Repository candidates are *implementation components*, not new customer-facing subscriptions. A single generic action wrapper allows multiple underlying libraries without changing the owner's experience.

## 2. Recommended dependency architecture

```text
ChatGPT / Claude / open-source agent / Gluons UI
               |
    MCP + Gluons task control API
               |
     authn/authz -> policy -> budget
               |
   durable Gluons task state / approvals
               |
      typed action adapter dispatch
       +-------+-------+---------+
       |       |       |         |
    documents  connectors  routing  voice
    Docling    Activepieces OR-Tools ElevenLabs
    invoice2data official APIs   VROOM
       |       |       |         |
       +-------+-------+---------+
               |
       authoritative vendor readback
               |
     audit, trial scores, billing, report
```

**Do not put a second full workflow engine under the product during deadline-sensitive development.** Retain the prior Google Cloud Workflows/Cloud Tasks design for durability. Temporal (MIT SDK) is an alternative if later evaluation shows the GCP approach cannot meet long-running human-approval and event-sourcing requirements. Use one execution authority per task.

**Recommended short integration path**:
1. MCP Python SDK to expose a handful of Gluons actions.
2. `invoice2data` + `Docling` + `MarkItDown` for PDFs, forms and email attachments.
3. Selected MIT Activepieces core pieces or *official service SDKs* to execute authorized actions. Verify each package's local license and API suitability; integrating a package is not equivalent to self-hosting the entire Activepieces product.
4. OR-Tools plus optional VROOM/OSRM sidecar for technically defensible dispatch planning.
5. ElevenLabs official Python SDK + paid ElevenLabs voice service for calling; calls require verified numbers, routing, consent and charge caps.
6. Langfuse core or OpenTelemetry as trace layer (redaction and tenancy isolation); do not include vendor EE modules.
7. Optional local Ollama deployment for customer's private agent, with data-movement disclosure and model-license audit.

## 3. Single action contract for all imported projects

Do not let external language-model instructions or a GitHub library invoke write APIs directly. Every action receives:

```json
{
  "tenant_id": "tenant_demo",
  "task_id": "task_001",
  "run_id": "run_002",
  "skill_id": "multichannel_order_entry",
  "action": "erp.order.create_draft",
  "mode": "shadow",
  "idempotency_key": "tenant_demo:event_55:create_draft",
  "policy_version": 3,
  "connection_id": "sandbox-erp-1",
  "input": {"customer_id":"C-17","items":[{"sku":"FISH-1","qty":2}]},
  "expected_external_effect": "create order draft"
}
```

The adapter must return a typed `action_id`, `status`, `receipt`, `provider_name`, `provider_object_id` when written, `cost`, `latency`, `warnings` and, crucially, a `verified` flag that stays **false** until a readback query confirms the external result.

Execution requirements:
- Action names and schemas are allowlisted by skill; no arbitrary shell or HTTP URL supplied by an LLM.
- Mode `historical_replay`/`shadow` forbids **all external writes and outward contact**. In these modes, a result may contain a preview but no external receipt.
- Mode `supervised` stages writes until an authorized human approves the exact payload and provider.
- Mode `live` may perform only whitelisted actions with a valid scoped grant, current policy, spend budget and approved consent.
- Write actions check an idempotency store before execution. On ambiguous timeouts, query provider state rather than issuing another request blindly.
- Calls and outbound messages are treated as irreversible side effects. Cancellation prevents *future* actions but cannot retract sent communications.
- Per-tenant connector credentials stay in GCP Secret Manager. Never feed provider credentials into prompts, files or logs.

## 4. Practical module/sidecar boundary

- **Python extractor service on Cloud Run:** `docling`, `invoice2data`, `markitdown`; accepts GCS signed object IDs or authenticated uploaded bytes, returns normalized structured fields and provenance. Container has no financial write permissions.
- **Optimization service on Cloud Run:** OR-Tools/Python for skill-constrained technician assignment; optional VROOM/OSRM as GKE or Cloud Run worker sidecar depending on dataset/CPU and route-preprocessing needs. Responds with candidates + constraints, never self-dispatches technicians.
- **Connector action service on Cloud Run:** one adapter per service (Gmail, ERPNext, Shopify, etc.); could reuse license-cleared Activepieces pieces where appropriate. Enforces human approval and provider verification.
- **Voice gateway on Cloud Run:** ElevenLabs webhook authorization, transcript-event intake, normalizes conversation turns; calls the same connector action service. Voice provider service is still separately billed.
- **Task controller + MCP on Cloud Run:** task setup and status, registry and approval endpoints, no direct third-party write privileges.
- **Source-of-truth task log in Cloud SQL:** idempotency, approvals, run progress, external object IDs, credential scopes, metering and retry history.

**No arbitrary cloned repository should execute within the task controller's trust boundary.** For browser automation, run sandboxed workers, restrict egress, enforce runtime caps, and use officially supported APIs when available. Browser Use is a fallback and subject to each website's policies.

## 5. One full composite demo using reused code (not mere simulations)

**Set up one business sandbox** (an ERPNext sandbox works as a demo system, with GPL obligations respected; alternatively build a small proprietary ERP simulator and clearly disclose it). Seed products, customers, previous orders and SKU aliases.

1. Gmail/authorized demo mailbox receives a sample PO PDF. The message becomes an event and task ID.
2. `Docling` or `MarkItDown` extracts document content; `invoice2data` can help with invoice-shaped formats, but **does not itself solve arbitrary PO extraction**. A skill-specific schema processor and model resolve order lines.
3. A catalog matcher validates exact SKUs, availability, units and customer identity; low confidence produces a review task.
4. Gluons records order drafts in **shadow** mode and computes line-item accuracy on known labelled documents; no ERP writes.
5. Owner tells Claude/ChatGPT to enter **supervised** mode. A human approves one precise draft.
6. Gluons action adapter creates the order in sandbox ERPNext using an approved integration, and verifies its order ID by a fresh API read.
7. An independent ElevenLabs live test call places another order, which goes through the **same order-matching and approval pipeline**. Calling service is paid, not a GitHub-cloned voice product.
8. Owner asks the assistant for both tasks' statuses, cost and evidence. Owner pauses the voice-triggered automation. Verify subsequent events do not cause new writes.
9. Independently demonstrate OR-Tools dispatch optimization using labeled technician-job fixtures, with route timings from self-hosted/authorized mapping data. **Clearly label route suggestions as a separate test** unless linked to real field-service software.

This demo proves reusable infrastructure and persistent control: multiple sources, a shared skill, a real external transaction, ElevenLabs voice, and assistant-independent task ownership. Showing 31 cards does not substitute for these receipts.

## 6. Dependency intake procedure (no exceptions)

For each proposed repository:

1. Verify the repository's current official identity, actual LICENSE/NOTICE **at the selected commit**, and license for every imported directory, embedded model, and transitive package.
2. Run `git ls-remote https://github.com/OWNER/REPO.git HEAD` and pin a reviewed 40-character commit SHA; save it in `repos/repository_registry.json` after tests. Never deploy an unreviewed floating `main` branch.
3. Keep a copy of original notices, LICENSE files, image/build provenance and the exact Git revision in the dependency dossier.
4. Examine issues, release recency, maintainer responsiveness, disclosed vulnerabilities and supported runtime. A repo with many stars isn't automatically a production-ready SDK.
5. Install in a separate isolated sandbox, without live business keys or network privileges by default. Run unit tests and the published README smoke test.
6. Run SBOM and dependency scanners (`syft`, `grype` or `osv-scanner`, `pip-audit`, `npm audit` as applicable), plus secret scanning and container scanning; resolve severe findings or select another package.
7. Build a typed adapter with an outbound allowlist, input limits, token/time budgets, payload validation, and response parsing. Do not expose arbitrary repo functions through MCP.
8. Pass Gluons standard tests: duplicate inbound events, injection in documents, missing keys, failed auth, 429/500, partial write, owner cancellation, budget limits, cross-tenant isolation, verified readback, zero side effects in shadow.
9. Test a vendor **sandbox account** against actual API behavior and record upstream version.
10. Promote status: `candidate` -> `audited` -> `adapter_tested` -> `sandbox_verified` -> `production_verified`; preserve audit traces and support rollback.

## 7. Licensing matrix: explicit high-risk exclusions

| Software | Current public terms and direction | Product decision |
|---|---|---|
| Activepieces | Community core MIT, EE commercial | Candidate for selectively reusing permitted core pieces; no EE code |
| Docling / invoice2data / OR-Tools / MCP SDK | MIT or Apache, with dependency/model qualifications | High priority as code components |
| Browser Use / ElevenLabs SDK | MIT SDK/library, separate vendor service fees or website terms | Optional wrapper, not free unlimited hosted agent service |
| ERPNext / Mautic | GPLv3 | Use as independent integration targets or lawful deployments; no casual code merging |
| Twenty / Cal.com | AGPLv3 and possible EE restrictions | Prefer documented APIs and compliant customer-owned instances; legal review before hosted derivation |
| n8n | Sustainable Use License | **Do not bundle:** commercial arrangement required for customers or their agents to build/configure workflows |
| Windmill | Mixed AGPL, Apache, proprietary CE limits | **Do not wrap/distribute CE for Gluons SaaS without commercial/legal clearance** |

Source details: `docs/11_github_repository_inventory.md` and `repos/repository_registry.json`. This is technical screening, not legal advice.

## 8. Fastest responsible execution order (dependency-driven, not an MVP scope limitation)

**Track A — verify the substrate:** lock GCP auth/task state, MCP SDK connection, exact policy checks, metering and audit receipts. Reuse existing dashboard and reference API only as simulations until real auth is working.

**Track B — integrate common code:** implement and test Docling/MarkItDown; invoice2data, OR-Tools, Activepieces-approved connector pieces, ElevenLabs SDK, optional Langfuse core. These modules cover multiple different automation skills.

**Track C — wire a complete live loop:** customer order inbox -> extraction -> validation -> review -> real sandbox ERP write -> readback -> owner notification -> ongoing monitoring/cancel. Add ElevenLabs live phone entry to the **same** skill.

**Track D — parallel breadth:** implement other 31 skill mappings based on dependency readiness, but display them as `prototype`, `requires_connector`, `requires_domain_review`, or `verified` accurately; regulated tax/legal/health workflows must remain professional-reviewed.

**Track E — YC proof:** record an uninterrupted live action trace plus actual task ID, external transaction receipt, call receipt, permission approval and cost. Avoid fake logos, claims of partner affiliation, simulated live actions or unverified accuracy/ROI.

## 9. What this approach saves, and what it does not

Reusing code eliminates substantial engineering for document parsing, routing algorithms, base connectors, dashboards, and agent SDK integration. It **does not** eliminate external app approval, customer OAuth, long-running consistency, industry expertise, model evaluation, error recovery, data protection or regulated approvals. Treat open source as acceleration of *components*, not the guarantee that each competitor's entire proprietary service is publicly implementable.

## 10. Code organization changes to the prior blueprint

```text
repos/repository_registry.json         # sourced repo shortlist and licensing flags
repos/skill_to_repo_map.json           # explicit reuse plan for all 31 skill categories
skills/*/INTEGRATION.md                # per-skill reuse candidates and remaining custom work
docs/10_github_first_build_strategy.md # engineering playbook
docs/11_github_repository_inventory.md # machine-derived mapping summary
scripts/audit_repo_registry.py         # offline consistency/commercial-license guard
adapters/reference_contract.py         # executable adapter contract (no vendor writes)
tests/test_repo_intake.py              # registry and contract tests
```

## 11. Repository monitoring commands

Run `python scripts/check_upstream_github.py` from the package root with a read-only `GITHUB_TOKEN` set in the environment to inspect fresh public repository metadata. It emits JSON lines containing archive status, last push date, star counts, and GitHub's top-level license identifier. The output is **not** a dependency license audit or a version lock; dual-license and subdirectory licenses must be checked from source at the chosen commit.

Before deployment, lock every selected dependency to an immutable commit or version and use the repository's original notice. Use `.github/workflows/verify-plan.yml` for CI of Gluons-local reference contracts and maps. In production, pin third-party GitHub Actions themselves to reviewed immutable SHAs and add software-composition analysis, static scanning and real integration tests.
