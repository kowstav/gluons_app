> **RELEASE BLOCKER:** The owner experience must implement the complete provider-connection and task-action permission journey in `13_permissions_connections_and_mobile_onboarding.md`. "Yes" in chat is not Gmail/Meta OAuth consent or call-forwarding permission. On mobile, the authoritative fallback is Gluons' responsive web/PWA because ChatGPT custom MCP connections are currently web-only.

# 01 — Gluons complete product requirements and interaction design

## Mission and customer promise
Gluons converts general-purpose AI assistants into reliable, auditable business automation operators. A business owner chooses a goal in ordinary language, connects existing systems once, receives expert-curated automation plans, proves them in a bounded trial, chooses an autonomy level, and manages every subsequent task through the assistant or the Gluons app.

**Promise:** Discover → Understand → Connect → Test → Approve → Run → Observe → Improve → Pause/Cancel. Existing model subscriptions can provide *interactive conversation* where the host supports tools; standalone execution is metered separately.

### Scope: final-platform capability, not an MVP-only promise
The platform supports 31 independently authored skills spanning restaurant operations, food distribution, property maintenance, logistics, real estate, procurement, retail, marketing, recruiting, accounting, legal admin, healthcare admin, and cross-business discovery. Each skill is a catalog definition with provider-dependent readiness (`spec_only`, `simulated`, `sandbox_integrated`, `production_verified`). All 31 are part of product scope; rollout cannot call them all live until connectivity, specialist validation, and compliance gates pass.

### Primary personas
- **Owner/operator:** Distrustful or moderately familiar with AI; wants results and a stop button, avoids complex settings.
- **Office operations manager:** Checks exceptions, trains procedures, reviews metrics and run history.
- **Technical administrator:** Authorizes OAuth connectors, maps fields, reviews API scopes and data retention.
- **External accountant or specialist:** Approves licensed/regulated activities but never automatically gains global business rights.
- **Field onboarding representative:** Demonstrates and installs with delegated onboarding rights; cannot view customer secrets, self-grant production permissions or spend customer funds.
- **Automation engineer/expert reviewer:** Curates a vertical skill and signs off on evaluation evidence and production readiness.

## Information architecture / main screens
1. **Home — Business Pulse.** KPI deltas, all currently running automations, pending approvals, alert feed, trial recommendations, cost this month, top supported next opportunities.
2. **Chat/Agent Bridge.** The user's own preferred assistant plus a native Gluons chat fallback. Tool status and rich approval cards, each with a link into dashboard. Never claim the assistant is always online.
3. **Explore.** Search by operational problem ('missed calls', 'slow orders') and industry, not just technical automation names. Catalog has availability states, real prerequisites, task action surface, expected data sharing, trial mode and price estimate.
4. **Connect.** Connection health, scopes, expiry, last sync, tenant, data location, permitted actions; one place to revoke access.
5. **Automation Designer.** Natural-language goal → inspectable state graph, input and output contracts, human checkpoints, budgets, fallback route, model policies; compare 'your rule' vs 'recommended rule' with a diff.
6. **Trial Studio.** Replay/shadow/supervised modes with assumptions and baseline metrics. Explicitly distinguish observed from estimated benefit.
7. **Operations.** Task cards grouped by running / waiting for approval / paused / failed / completed; timeline of actions and verified receipts; retry/cancel and human handoff.
8. **Approval Inbox.** Approvals show actual before/after business record, cost, source evidence, expiration, maker-checker separation. Group small, low-risk approvals if explicitly enabled.
9. **Cost & Models.** Transparent provider cost, estimated vs actual, per-skill and per-task budgets, model lineup with eval score, latency, region, security mode; allow owner preference but enforce skill minimum reliability.
10. **Proof & ROI.** Baseline evidence, corrections, time-study methodology, cost, measured / estimated benefits, uncertainties and trial conversion controls.
11. **Organization & Trust.** Roles, teams, contacts, workspace settings, skill versions, incident history, data export, retention, model providers, policy controls, support.
12. **Representative Mode.** One guided 15-minute onsite diagnosis, permissioned trial with synthetic or explicitly consented sample data, setup completion checklist; no access to real credentials.

## Universal interactions
- "Find ways to automate my business" → context discovery based on connected sources and optional questionnaire (ask only necessary questions), return 3-5 grounded opportunities ranked by feasibility and measurable value.
- "Start" → show prerequisite checklist, cost ceiling, external side effects, default shadow mode; owner verifies config; server persists task.
- "Join a task" → read current task status, add owner instruction or approval; no mutable system prompt injection or unsafe changing of live steps.
- "Pause" → prohibit scheduling future reversible steps; already-active external transaction may finish; disclose in UI.
- "Cancel" → mark task cancellation requested, stop queued actions, reconcile in-flight effects, propose compensations where possible, revoke task-scoped credentials when requested; do not claim the effects are undone.
- "Try another model" → test candidate model on skill's frozen evaluation suite; show reliability/cost delta before user selection. Never swap to a model failing risk-tier minimums.
- "Teach it my policy" → request an explicit policy diff and approval; separate owner-specific preferences from immutable platform/legal constraints.
- "Switch assistants" → authenticate new MCP host to same tenant and per-action permissions. No task migration necessary because Gluons owns persistent state.

## Task state machine
`draft -> configuring -> ready -> trial_replay/trial_shadow -> awaiting_activation -> active -> waiting_external/waiting_approval -> active -> completed` with side branches `paused`, `failed_retryable`, `failed_terminal`, `cancel_requested`, `cancelled`, `reconciliation_required`. State transitions are version checked and audit logged. Task may be a *continuous recurring automation* containing many independent job runs, each with own state.

## Trial proof design
Three controlled trial modes: **replay** on historical data; **shadow** on live incoming events but absolutely no external writes/messages; **supervised** on live operations with action-level approval. Define baseline method and units before trial. Report denominators, ground-truth availability, errors, manual effort, actual external costs, projected and observed benefit separately. No 'savings' claims without the relevant evidence. Provide clear expiration, cancellation, and automatic disconnection choices.

## Industry skill experience
Each `skills/<slug>/` contains `SKILL.md` (expert procedure), `workflow.yaml` (versioned workflow and policy), `evals.json` (failure scenarios). A runtime skill certificate will contain passing eval hashes, third-party connector versions, domain expert review, supported jurisdictions, supported modes and last verification date. Generic skill content is not equivalent to being certified for production.

## Model marketplace
Models grouped as `fast_cheap`, `balanced`, `reasoning_high`, `vision`, `speech`, `private_local`. Provider adapters: Google Vertex AI/Gemini, OpenAI API, Anthropic API, vetted third parties, local Ollama or vLLM via customer VPC/GCP GPU. Record actual cost per operation using vendor metering; do not promise unsupported model compatibility. Display data transfer region, caching policy and provider's actual retention options. No sensitive prompt data in operational logs.

## Owner-facing pricing design — hypothesis, not researched demand
Offer base platform subscription (workspace, controls, skill access), transparent task execution usage charges, optional implementation assistance and capped free trials. Avoid forced subscriptions to each vertical workflow. Separate vendor pass-through costs (telephony, WhatsApp, paid data, shipping, carrier etc.) and implementation charges with explicit estimates. Test (a) base + usage, (b) value-tier bundle + usage cap, (c) field-rep assisted setup fee paid by Gluons for qualification. Do not freeze pricing without measured conversion, COGS and retention.

## Feature quality bar
- First meaningful automation discovery in 90 seconds from a sample business; no requirement to provide production credentials to explore.
- Once owner authorizes valid connections, all irreversible calls show policy and approval context.
- Standard running tasks can be listed, paused, resumed and cancellation-requested from every supported UI.
- Task history is consistent across hosts and dashboards within bounded freshness targets.
- The same task is not accidentally duplicated by webhook retries.
- Accessibility: keyboard operation, readable contrast, semantic state labels, plain-language disclosures, multilingual UI.
- Reliability SLOs start after field measurements and must exclude neither failures nor escalations. Set candidate availability >=99.9% for control plane, then measure and publish actual.

## Explicit non-goals / limits
Not every industry process can be automated without licensed professionals, vendor authorization, restricted data processing or real humans. Don't imply self-hosting an LLM means third-party data never leaves the tenant. Existing ChatGPT/Claude subscriptions pay for host conversation, not Gluons background inference. No autonomous tax filing, legal representation, clinical decisions, binding insurance, funds transfers, unapproved ad spending, discriminatory hiring, or purchases without explicit authorized controls.
