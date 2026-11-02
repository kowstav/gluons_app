# 11 — Repository inventory and 31-skill implementation mapping

**Diligence date:** 2026-11-02. This is a candidate catalog, not a certified dependency lockfile. No upstream source has been imported, run or integrated in Gluons. Public repository evidence and licensing are provisional until approved commit audits.

## A. Candidate repositories

| Repository | License/status | Planned integration | Use |
|---|---|---|---|
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MIT | `embed` | Expose Gluons tool/resource API to external assistants |
| [Activepieces Community core](https://github.com/activepieces/activepieces) | MIT core; enterprise modules proprietary | `selective_embed` | Reuse individual pieces/connectors and integration abstractions; do not depend on EE |
| [Temporal Python SDK](https://github.com/temporalio/sdk-python) | MIT | `optional` | Optional alternative durable runtime if GCP Workflows falls short |
| [LangGraph](https://github.com/langchain-ai/langgraph) | MIT | `embed` | Stateful supervised planning and agent composition, not external write authority |
| [Docling](https://github.com/docling-project/docling) | MIT code; model licenses separate | `embed` | Extract typed structure from PDFs and scanned business files |
| [MarkItDown](https://github.com/microsoft/markitdown) | MIT | `embed` | Normalize Office/PDF/web documents to text for downstream extraction |
| [invoice2data](https://github.com/invoice-x/invoice2data) | MIT | `embed` | Extract structured fields from invoices with deterministic templates |
| [Google OR-Tools](https://github.com/google/or-tools) | Apache-2.0 | `embed` | Assignment, scheduling, purchasing and routing optimization |
| [VROOM](https://github.com/VROOM-Project/vroom) | BSD-2-Clause | `sidecar` | Vehicle route optimization service for dispatch and deliveries |
| [OSRM](https://github.com/Project-OSRM/osrm-backend) | BSD-2-Clause | `sidecar` | Travel-time estimates and geospatial routing |
| [Chatwoot](https://github.com/chatwoot/chatwoot) | MIT | `optional_sidecar` | Customer-support inbox and human handoff |
| [Novu core](https://github.com/novuhq/novu) | MIT core; enterprise proprietary | `optional_sidecar` | Multichannel owner notices, approvals and task status notifications |
| [ElevenLabs Python SDK](https://github.com/elevenlabs/elevenlabs-python) | MIT SDK; hosted service paid | `embed` | Create/configure voice sessions and send verified tool callbacks |
| [Browser Use](https://github.com/browser-use/browser-use) | MIT Python library; hosted cloud service paid | `optional` | Last-resort browser interaction where permitted APIs absent |
| [Ollama](https://github.com/ollama/ollama) | MIT software; model licenses separate | `optional_sidecar` | Local inference for private/customer-hosted setups |
| [Langfuse core](https://github.com/langfuse/langfuse) | MIT core; ee proprietary | `optional_sidecar` | LLM traces and evaluation evidence |
| [Presidio](https://github.com/data-privacy-stack/presidio) | MIT | `embed` | PII detection and redaction ahead of model calls/logs |
| [Meilisearch CE](https://github.com/meilisearch/meilisearch) | MIT community; EE BUSL/commercial | `optional_sidecar` | Catalog and task search in skills/knowledge |
| [ERPNext](https://github.com/frappe/erpnext) | GPL-3.0 | `sandbox_or_customer_system` | Open test ERP with orders, purchasing, accounting, inventory |
| [Twenty CRM](https://github.com/twentyhq/twenty) | AGPL-3.0 core; some MIT SDKs/EE | `sandbox_or_customer_system` | Test CRM using documented API; optional customer-side system |
| [Cal.com](https://github.com/calcom/cal.com) | AGPL-3.0 core; enterprise commercial | `sandbox_or_customer_system` | Reference scheduling service via API if authorized |
| [Mautic](https://github.com/mautic/mautic) | GPL-3.0 | `sandbox_or_customer_system` | Reference customer-side campaign engine, import allowed patterns |
| [n8n](https://github.com/n8n-io/n8n) | Sustainable Use License; enterprise | `reference_only` | Study interaction ideas or license separately |
| [Windmill](https://github.com/windmill-labs/windmill) | AGPL source; distributed CE has extra restrictions | `reference_only` | Study architecture, not vendor embedded SaaS engine |

## B. Mapping to all 31 original skills

**Component mapping, not workflow parity.** Each row also needs its own vertical business rules, external partner access, approvals, production tests and domain expertise.

| Skill | Reusable repositories | Still missing | Fit |
|---|---|---|---|
| `multichannel_order_entry` | Docling, MarkItDown, Activepieces Community core, ERPNext | Customer alias resolution, exact catalog match, ERP idempotent write, customer approval | HIGH |
| `restaurant_inventory_planning` | ERPNext, Google OR-Tools, Docling, invoice2data | Forecasting model, recipe conversion, procurement rules, real POS sync | HIGH |
| `restaurant_voice_ordering` | ElevenLabs Python SDK, Activepieces Community core, ERPNext | Live telephony number, menu knowledge, POS transaction integration, call routing | HIGH |
| `property_maintenance` | Chatwoot, Novu core, Activepieces Community core | Tenant/property/vendor entities, compliance, spending approval, contractor dispatch | MEDIUM |
| `freight_order_to_cash` | Docling, Google OR-Tools, OSRM, Activepieces Community core | Carrier-specific TMS/POD integration, rate contract reconciliation, notification rules | HIGH |
| `title_closing_support` | Docling, MarkItDown, Browser Use | Authorized property-record access, state-specific legal review, closing software integration | LOW_REGULATED |
| `workflow_discovery` | LangGraph, Browser Use, Presidio | Consented desktop telemetry and proof of repetition, policy, generated integration tests | MEDIUM |
| `ecommerce_returns` | Activepieces Community core, Novu core, Chatwoot | Shopify returns/order APIs, carrier labels, refund rules, fraud checks | HIGH |
| `shopping_guidance` | Meilisearch CE, LangGraph, Activepieces Community core | Merchant catalog grounding, stock correctness, live storefront plugin | MEDIUM |
| `local_creator_campaigns` | Activepieces Community core, Novu core | Creator opt-in, matching data, contracted scheduling, platform-compliant messaging | MEDIUM |
| `field_service_opportunity_detection` | Twenty CRM, LangGraph, Activepieces Community core | Service-history enrichment, customer consent, fair upsell policy, CRM integration | MEDIUM |
| `field_inspection_reports` | Docling, MarkItDown, Presidio | Capture app, geotagged/photos evidence, inspector template/legal approval | MEDIUM |
| `construction_estimates` | Docling, Google OR-Tools | Drawing-level quantity takeoff, measurement accuracy, estimator sign-off | LOW_REGULATED |
| `property_valuation_reports` | Docling, Meilisearch CE | Licensed appraiser process, permitted comps, valuation research/legal sign-off | LOW_REGULATED |
| `bookkeeping_reconciliation` | invoice2data, Docling, ERPNext | Bank feeds, ledger matching, tax rules, reconciliation controls, CPA review | HIGH |
| `tax_return_preparation` | Docling, MarkItDown, invoice2data | Jurisdiction-specific forms, licensed tax reviewer, tax software restrictions | LOW_REGULATED |
| `accounting_practice_operations` | invoice2data, Docling, Activepieces Community core | Client ledger mapping, payroll portal rights, HMRC/agency rules and approvals | MEDIUM |
| `legal_matter_intake` | Chatwoot, Docling, Novu core | Lawyer-controlled conflict search, ethical duties, consent and engagement letter | LOW_REGULATED |
| `commercial_insurance_submission` | Docling, invoice2data, Browser Use | Carrier-specific forms/risk rules, licensing, approved submissions | LOW_REGULATED |
| `candidate_screening` | Activepieces Community core, Chatwoot, Novu core | ATS adapter, fairness evaluation, applicant consent and interview scripts | MEDIUM |
| `dental_reception_and_claims` | ElevenLabs Python SDK, Novu core, Activepieces Community core | Practice management APIs, eligibility services, protected health data safeguards | LOW_REGULATED |
| `hardware_procurement` | ERPNext, Docling, Google OR-Tools | Vendor quotes, BOM normalization, customer approvals, vendor payment systems | MEDIUM |
| `website_organic_growth` | Browser Use, MarkItDown, Activepieces Community core | Google Search Console OAuth, CMS publishing rules, SEO tests | MEDIUM |
| `lead_research_and_outreach` | Activepieces Community core, Twenty CRM, Presidio | Permissioned lead data, opt-out, enrichment rights, email deliverability | MEDIUM |
| `crm_reactivation_and_hygiene` | Activepieces Community core, Twenty CRM, LangGraph | Record dedup and merge policy, consent, outbound message guardrails | MEDIUM |
| `paid_ad_campaign_operations` | Activepieces Community core, LangGraph | Platform ad APIs and advertiser permissions, budgets, creatives, human approval | MEDIUM |
| `social_media_operations` | Activepieces Community core, Novu core | Official platform APIs, brand approval, authentic account practices | LOW_PLATFORM_RESTRICTED |
| `abandoned_cart_retention` | Activepieces Community core, Novu core, Chatwoot | Shopify cart/order API, consent policy, campaign caps, attribution | MEDIUM |
| `outbound_shipping_management` | ERPNext, OSRM, VROOM, Activepieces Community core | Carrier/warehouse APIs, labels, tracking, refund/claim policy | HIGH |
| `custom_legacy_workflow` | Browser Use, LangGraph, Activepieces Community core | Customer-specific application permission, fragile UI selectors, approvals, anti-bot terms | MEDIUM |
| `home_service_dispatch` | Google OR-Tools, VROOM, OSRM, Activepieces Community core | Technician skills, priorities, real-time availability, CRM/field-service writebacks | HIGH |

## C. Legal and operational screening

**Permissive** does not mean unreviewed code can run in production. For MIT/Apache/BSD, preserve copyright and notices; audit transitive dependencies, generated code, pretrained models, data licenses, trademarks, and optional enterprise components.

**Copyleft** applications such as ERPNext (GPL), Twenty (AGPL), Cal.com (AGPL), and Mautic (GPL) can be useful as *independently operated or customer-owned systems accessed through official APIs*. Embedding, modifying, combining and offering them as network services needs project-specific legal review; no automatic blanket assumption about linking is made.

**Restricted source-available** examples should not be bundled by default. In particular, the n8n FAQ expressly requires a commercial license for products allowing users to create or configure n8n workflows through an AI agent; Windmill has restrictions on wrapping and reselling its distributed Community Edition.

## D. Replacement and verification process

1. Review registry candidate, evidence and proposed deployment architecture.
2. Inspect actual LICENSE/NOTICE at an immutable commit, nested directory licenses, and dependencies; seek legal review for uncertainty.
3. Inspect update/security activity and ensure reproducible Docker or package builds; reject abandoned or unmaintained code for high-risk workflows.
4. Fork or import **only** modules needed, preserving attribution; pin exact commit SHA and store license artifacts / SBOM.
5. Expose one Gluons typed action through an adapter; forbid raw vendor credential access from LLM.
6. Run against fixtures and mock vendor sandboxes; assert read-only shadow and authorization before writes.
7. Perform true external API readback verification and measure errors, compute cost, operator intervention, latency.
8. Document upstream replacement plan, patch cadence, and abort/rollback procedure.
9. Mark a skill LIVE only after real integration proof. Never show the company reference as a claimed partner.

## E. Source evidence links

The `repository_registry.json` field `license_evidence` contains original upstream evidence references; recheck the live current license at the pinned revision before distributing.

