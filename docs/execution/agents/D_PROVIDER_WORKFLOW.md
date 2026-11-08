# Agent D — first real provider workflow

Own EX-01/02/03 and provider portions of later commerce work. Read connector/action/gateway contracts, `connections/`, `connectors/gmail.py`, `connectors/erpnext.py`, `orders.py`, `skills/domain/orders.py`, the order contract, channel workers and existing tests.

## First decision

Confirm the actual participating business, inbound channel and authoritative order system with the founder's decision record. The default technical proof is Gmail + real ERPNext sandbox, but do not substitute a new ERP into a customer's workflow without a product decision. If only Gmail is available, finish connector proof and label the end-to-end order gate pending.

## Deliverables

1. Opt-in real-provider harness, safe synthetic fixtures, explicitly bounded action manifest and sanitized evidence format. Credentials stay out of source/chat/artifacts.
2. Real provider connect/identity/scopes/refresh/revoke evidence, then authorized minimal write with independent readback.
3. Event → normalized order → reviewed/approved action → verified external result → owner-visible receipt. Preserve simulator tests as regression tests, not real-provider evidence.
4. Failure matrix for duplicate/out-of-order events, rate limits, expired/revoked grants, delayed readback and ambiguous timeout/crash recovery.
5. Reconnect health and dependent-work fencing, plus precise public-rollout access blockers.

## Acceptance

- FLOW-01 through FLOW-07, SETUP-02 and applicable OPS-04 pass in their required environments.
- Provider connection alone cannot authorize a write; exact owner authority and provider scopes are both enforced.
- Only the gateway opens business write authority. Shadow and replay perform zero external writes.
- `lookup` distinguishes found, definitively absent where the API permits that conclusion, and ambiguous. Search delay is not proof an email was not sent.
- Readback compares account/resource and meaningful fields, not just a success code or echoed request.
- Repeated delivery and process restarts do not create duplicate intended effects or usage charges in the tested cases.
- Real ERPNext fields/custom external-key assumptions are checked against its actual sandbox; the Frappe-compatible simulator is not sufficient.

## Boundaries

Do not wire all channels at once. After the anchor passes, choose WhatsApp or voice based on the pilot's demand and provider access. Real voice must verify during-call availability/reconciliation and fallback; real WhatsApp must verify signup/window/template/eligibility behavior. Preserve the previous Shopify install, privacy, refresh and connection-health fixes.

F owns launch orchestration/readiness; agree exact action receipt fields before carrier/commerce work. A owns shared gateway/schema/config changes. New unsupported systems must stay visibly blocked. Do not remove the fulfillment dispatch receipt prerequisite merely because no connector currently produces it.
