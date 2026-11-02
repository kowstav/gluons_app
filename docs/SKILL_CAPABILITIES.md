# Skill capability evidence

All 31 blueprint skills are registered. Registration is not proof that a business workflow is implemented. The original order skill has an executable provider-action graph tested against the labelled local ERP simulator. The other 30 skills currently execute preparation and validation only; their entire external workflow remains blocked. No skill has been verified with a real provider sandbox or production account in this stage.

## What executes locally

- All 30 preparation contracts produce schema-checked review packets, hashes of supplied sources, missing-data questions, safety flags and an explicit `not_executed` checklist for every pending blueprint step. They have no provider-action allowlist and advertise replay/shadow only. Runtime activation also rejects draft-only contracts.
- Inventory planning computes ingredient shortfalls and estimated purchasing costs from supplied demand, stock and on-order quantities. It does not forecast demand or place purchases.
- Bookkeeping and accounting preparation match exact invoice reference, legal entity, currency and amount, flag duplicate/ambiguous matches and never post entries.
- Hardware procurement compares landed quote costs within each currency, flags part substitutions and MOQ mismatches, and does not certify supplier reliability or authorize a purchase.
- Home-service dispatch uses OR-Tools to propose assignments satisfying supplied skills, certification expiry, service area, shifts, job windows, travel separation and non-overlap. A separate verifier checks the result. Emergency inputs stop routine assignment. The travel matrix is supplied by the owner; there is no live mapping or dispatch integration.

Other domains produce input-readiness and review checklists; they do not fabricate appraisals, tax returns, legal conclusions, hiring decisions, calls, customer messages or commercial outcomes.

## Catalog

| Skill | Executable scope | Remaining release gate |
|---|---|---|
| `abandoned_cart_retention` | Replay/shadow preparation only | shopify; domain-specific action graph and real provider evidence |
| `accounting_practice_operations` | Replay/shadow preparation only | customer_accounting_system; domain-specific action graph and real provider evidence |
| `bookkeeping_reconciliation` | Replay/shadow preparation only | erpnext; domain-specific action graph and real provider evidence |
| `candidate_screening` | Replay/shadow preparation only | customer_ats; domain-specific action graph and real provider evidence |
| `commercial_insurance_submission` | Replay/shadow preparation only | customer_broker_system; domain-specific action graph and real provider evidence |
| `construction_estimates` | Replay/shadow preparation only | customer_estimating_system; domain-specific action graph and real provider evidence |
| `crm_reactivation_and_hygiene` | Replay/shadow preparation only | customer_crm; domain-specific action graph and real provider evidence |
| `custom_legacy_workflow` | Replay/shadow preparation only | customer_approved_api; domain-specific action graph and real provider evidence |
| `dental_reception_and_claims` | Replay/shadow preparation only | customer_practice_system; domain-specific action graph and real provider evidence |
| `ecommerce_returns` | Replay/shadow preparation only | shopify; domain-specific action graph and real provider evidence |
| `field_inspection_reports` | Replay/shadow preparation only | customer_inspection_system; domain-specific action graph and real provider evidence |
| `field_service_opportunity_detection` | Replay/shadow preparation only | customer_crm; domain-specific action graph and real provider evidence |
| `freight_order_to_cash` | Replay/shadow preparation only | customer_tms; domain-specific action graph and real provider evidence |
| `hardware_procurement` | Replay/shadow preparation only | erpnext; domain-specific action graph and real provider evidence |
| `home_service_dispatch` | Replay/shadow preparation only | customer_dispatch_system; domain-specific action graph and real provider evidence |
| `lead_research_and_outreach` | Replay/shadow preparation only | customer_crm; domain-specific action graph and real provider evidence |
| `legal_matter_intake` | Replay/shadow preparation only | customer_case_management; domain-specific action graph and real provider evidence |
| `local_creator_campaigns` | Replay/shadow preparation only | gmail; domain-specific action graph and real provider evidence |
| `multichannel_order_entry` | Order extraction/matching, approval, idempotent ERP HTTP write and readback | Real ERPNext sandbox, Gmail OAuth/provider send verification and live operational evidence |
| `outbound_shipping_management` | Replay/shadow preparation only | customer_shipping_provider; domain-specific action graph and real provider evidence |
| `paid_ad_campaign_operations` | Replay/shadow preparation only | customer_ad_account; domain-specific action graph and real provider evidence |
| `property_maintenance` | Replay/shadow preparation only | customer_property_system; domain-specific action graph and real provider evidence |
| `property_valuation_reports` | Replay/shadow preparation only | licensed_property_feed; domain-specific action graph and real provider evidence |
| `restaurant_inventory_planning` | Replay/shadow preparation only | customer_pos; domain-specific action graph and real provider evidence |
| `restaurant_voice_ordering` | Replay/shadow preparation only | elevenlabs; domain-specific action graph and real provider evidence |
| `shopping_guidance` | Replay/shadow preparation only | shopify; domain-specific action graph and real provider evidence |
| `social_media_operations` | Replay/shadow preparation only | customer_social_account; domain-specific action graph and real provider evidence |
| `tax_return_preparation` | Replay/shadow preparation only | licensed_tax_software; domain-specific action graph and real provider evidence |
| `title_closing_support` | Replay/shadow preparation only | customer_title_system; domain-specific action graph and real provider evidence |
| `website_organic_growth` | Replay/shadow preparation only | customer_cms; domain-specific action graph and real provider evidence |
| `workflow_discovery` | Replay/shadow preparation only | consented_process_log; domain-specific action graph and real provider evidence |

## Commerce launch playbook

`gluons.launch` persists a 14-item plan, research brief, signed owner planning approval, source evidence and audit timeline. It supports pause, resume, cancel, tenant isolation, optimistic versions and expiry-safe renewal. It does not claim research was performed when it only prepared a brief. The next completed checkpoint requires a provider-checked and owner-confirmed Shopify connection. Shopify storefront, supplier outreach/commitments, payment/KYC, checkout, publication and fulfillment remain blocked. A clicked link, owner statement or model output cannot verify KYC or a live store. Planning approval authorizes no external spending.

## Evaluation evidence

`apps/api/tests/test_skill_catalog.py`: 76 tests passed with real PostgreSQL, including all 30 preparation graphs through the durable worker and duplicate-event/no-provider-receipt assertions. The 90 JSON fixture cases in `packages/skills/*/evals.json` run actual preparation code; they certify local preparation only. They are separate from the original blueprint text-only examples, which are not reported as provider tests.

`apps/api/tests/test_business_launch.py`: 13 tests passed, covering persistent plans, signed owner approval, assistant-scope rejection, payload binding, tenant isolation, cancellation, expiry renewal and unverified provider handoffs. Existing `test_order_vertical.py`: 9 tests passed against the labelled HTTP ERP simulator.

Additional domain tests cover inventory arithmetic, exact reconciliation, quote comparison, expired certifications, travel/window/shift constraints, independent schedule verification, emergency routing and scope-promotion prevention. Provider outages cannot be meaningfully verified for missing provider adapters; their release gates remain open.

## Document extraction

`skills/domain/documents.py` extracts authorized local bytes and returns source/text hashes, untrusted-content flags and PDF page spans. UTF-8 text and actual pypdf text extraction are tested, including image-only-page warnings. An actual MarkItDown HTML conversion is tested without following document links. File-size, output-size, page-count, decoded-PDF-content and archive-expansion limits reject oversized input; private-root path checks reject absolute paths, traversal, URLs, alternate streams and symlink escapes. Office conversion is optional and explicitly returns `CONFIGURATION_REQUIRED` when its format extra is missing. No tenant upload endpoint or arbitrary MCP file path is exposed by this wrapper. Hosted ingestion still needs isolated parser resource limits and authorized artifact storage.

## Upstream boundary

OR-Tools `9.15.6755` was already installed and is wrapped by `apps/api/src/gluons/skills/domain/dispatch.py`; Google owns the optimization implementation. Source: [google/or-tools](https://github.com/google/or-tools), [Apache-2.0 license](https://github.com/google/or-tools/blob/stable/LICENSE), [official scheduling API guidance](https://developers.google.com/optimization/scheduling/job_shop). Checked 2026-11-02. The optional dependency must remain pinned to the tested version. Document extraction wraps the already-installed MarkItDown `0.1.9` ([MIT license](https://github.com/microsoft/markitdown/blob/main/LICENSE)) and pypdf `6.21.4` ([official extraction documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html), BSD-3-Clause package metadata). No model, cloud API or OCR dependency is invoked. This stage did not import Activepieces, Docling, invoice2data, Browser Use, OSRM, VROOM, n8n or Windmill code.

No cloud resources or live provider actions were created by these tests.
