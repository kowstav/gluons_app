# Gluons — Complete product and engineering blueprint (November 2026)

**What this is:** A researched specification and build package for a universal AI automation infrastructure, designed to be implemented by engineers and demonstrated honestly to YC.

**What this is not:** A live integrated production SaaS. The included browser demo and reference API are sandbox simulations. The 31 authored skills are domain-specific implementation specifications, not claims that third-party ERP, POS, healthcare, insurance, tax or bank integrations exist.

## GitHub-first upgrade (2026-11-02)

The build plan now explicitly reuses existing, license-screened GitHub component candidates wherever feasible: **24 repositories mapped to all 31 skills**. Read **`docs/10_github_first_build_strategy.md`** before `docs/03_build_plan.md`; see **`docs/11_github_repository_inventory.md`** and JSON manifests in `repos/`. All reuse is **planned only**, not code already cloned, audited, deployed, or production-tested. n8n and Windmill are deliberately reference-only without licensing clearance. Run `python scripts/audit_repo_registry.py` to check the local maps (this does not audit upstream code).

## Demo-only acceleration addendum (2026-11-02)

**New decision:** Use temporary, replaceable engines for a private / recorded YC demonstration, when their license permits that deployment. **Read `docs/12_disposable_demo_engines_and_yc_deadline.md` first.** It distinguishes internal testing, recorded demonstrations, and external customer-facing use; defines the execution adapter boundary; and prioritizes genuine ERP sandbox receipts, one ElevenLabs call, and OR-Tools dispatch. Demo-only intent does not itself remove license obligations.


## Business-launch and owner-controlled web architecture (2026-11-02)

**New product contract:** `docs/14_business_launch_owner_portal_and_identity.md` and `playbooks/commerce_business_launch/` extend Gluons to launching an e-commerce business, not only automating an existing one. Gluons Web is the authenticated hub for external account authorization, store ownership, payments/KYC handoffs, supplier outreach permissions, approvals, persistent status, and cancellation. A user can connect multiple assistants and own multiple businesses; **do not rely on a one-chat-to-one-business identity assumption**. Shopify store creation is through merchant signup or partner client-transfer flow, not an invented universal store-creation API. Payment identity verification remains owner/provider-controlled. This meta-playbook is a **design specification, not a working storefront/payment integration**.

## Mandatory external permissions correction (2026-11-02)

**Read `docs/13_permissions_connections_and_mobile_onboarding.md` before all other implementation documents.** Earlier sections mention OAuth and action approvals but omitted the full Google/Meta/telephony authorization and mobile onboarding flows. The new P0 gate distinguishes assistant OAuth, third-party OAuth, owner task grants and telephone-network routing, defines APIs/data models/acceptance tests, and includes a **critical OpenAI limitation: custom MCP connections in ChatGPT are currently web-only**. On mobile, provide the Gluons-owned responsive web/PWA onboarding and control fallback. No one can grant Gmail access or enable carrier call forwarding solely by typing "yes" in chat.

## Contents

- `docs/13_permissions_connections_and_mobile_onboarding.md`: Mandatory P0 account linking, mobile fallback, action grants and phone routing.
- `docs/14_business_launch_owner_portal_and_identity.md`: Cross-assistant owner identity, permanent web authorization hub, full Shopify/supplier/payment/fulfillment orchestration.
- `playbooks/commerce_business_launch/`: Cross-skill meta-playbook specification, workflow graph, safety instructions, evaluation fixtures.
- `docs/01_product_spec.md`: Full product experience, control surfaces, design principles, requirements, pricing and trust model.
- `docs/02_architecture_and_api.md`: Google Cloud architecture, durable task state, MCP, security, provider routing, connectors and API behavior.
- `docs/03_build_plan.md`: Full-workstream construction and test plan with dependencies, roles and acceptance gates.
- `docs/04_catalog.md`: Complete catalog of **31** automation skills, one for each of the prior 30 company categories plus home-service dispatch.
- `docs/05_demo_and_yc.md`: Detailed YC demonstration and customer proof plan.
- `docs/06_research_sources.md`: Primary source URLs and validated architecture assumptions.
- `docs/07_gcp_deployment_playbook.md`: Detailed cloud and provider setup, deployment and operations.
- `docs/08_policy_and_evaluation.md`: Skill certification, risk tiers, agent safety and testing.
- `docs/10_github_first_build_strategy.md`: Repository-first integrations, contract, legal gates, composite demo and build instructions.
- `docs/11_github_repository_inventory.md`: 24 candidate GitHub projects mapped to all 31 Gluons skills.
- `docs/12_disposable_demo_engines_and_yc_deadline.md`: Demo-only runner policy, strongest immediate YC demonstration, replaceable adapters, and exit strategy.
- `repos/`: Citable inventory and implementation matrix in JSON.
- `skills/<name>/INTEGRATION.md`: Per-skill code reuse plan, implementation gaps and acceptance gates.
- `adapters/reference_contract.py`: Safe no-write reference adapter; **not** a vendor connection.
- `docs/09_market_competition_and_moat.md`: Hard-nosed competitive landscape, adoption evidence and differentiation tests.
- `skills/<name>/SKILL.md`: Domain instructions, operating policies, exception paths and evaluation criteria.
- `skills/<name>/workflow.yaml`: Versioned machine-readable workflow metadata.
- `skills/<name>/evals.json`: Safety and reliability test scenarios.
- `schemas/`: Data-model and public tool-contract examples.
- `demo/index.html`: Interactive, clearly labeled **simulation** of the product dashboard. Open locally in a browser.
- `reference_api/`: Optional local demonstration API, not a production orchestrator.

## Account connection readiness (must not misrepresent)

- **Designed:** assistant OAuth, Gmail server OAuth, WhatsApp official onboarding, telephone provisioning/routing, independent owner action grants, mobile PWA fallback and 19 P0 checks.
- **Not yet built:** real provider callbacks, credential store, OAuth verification, production Meta Embedded Signup, call routing verification, working signed owner approval or live provider action gates.
- **Unverified:** ChatGPT custom MCP mobile support; current official guidance says custom MCP connection is **web-only**. The PWA fallback is part of the product contract, not an optional enhancement.

## Critical build guardrails

1. Workflow state and permissions belong to Gluons, never the host AI assistant.
2. No irreversible action without policy and authorization, and no unreviewed financial or regulated sign-off.
3. Shadow mode must generate **zero** external side effects.
4. All incoming third-party data is untrusted and cannot modify policies.
5. A successful LLM response is **not** proof that an external order or booking succeeded; verify against source of truth.
6. Use real, verified actions in a YC demo; clearly identify all simulators and synthetic test inputs.
7. Personal ChatGPT/Claude subscriptions do not fund unattended API inference, telephony, SMS, or cloud services.

## Start here

Read docs `14` → `13` → `12` → `10` → `11` → `01` → `02` → `03`. Review the 31 skill packs. Open `demo/index.html` to walk through the proposed UX. Use `05` for the founder and demo videos.
