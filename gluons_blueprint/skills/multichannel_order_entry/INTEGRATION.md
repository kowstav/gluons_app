# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `multichannel_order_entry`

## Candidate reusable components

- **[Docling](https://github.com/docling-project/docling)** — Extract typed structure from PDFs and scanned business files  
  Public license indication: MIT code; model licenses separate. [Evidence](https://github.com/docling-project/docling). Current revision and dependencies require audit.
- **[MarkItDown](https://github.com/microsoft/markitdown)** — Normalize Office/PDF/web documents to text for downstream extraction  
  Public license indication: MIT. [Evidence](https://github.com/microsoft/markitdown/blob/main/LICENSE). Current revision and dependencies require audit.
- **[Activepieces Community core](https://github.com/activepieces/activepieces)** — Reuse individual pieces/connectors and integration abstractions; do not depend on EE  
  Public license indication: MIT core; enterprise modules proprietary. [Evidence](https://www.activepieces.com/docs/about/license). Current revision and dependencies require audit.
- **[ERPNext](https://github.com/frappe/erpnext)** — Open test ERP with orders, purchasing, accounting, inventory  
  Public license indication: GPL-3.0. [Evidence](https://docs.frappe.io/legal/others/license-and-trademark). Current revision and dependencies require audit.

## Gluons-specific work still required
Customer alias resolution, exact catalog match, ERP idempotent write, customer approval

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **HIGH**. This does not imply full parity with the startup category reference.
