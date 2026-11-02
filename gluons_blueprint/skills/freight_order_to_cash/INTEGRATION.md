# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `freight_order_to_cash`

## Candidate reusable components

- **[Docling](https://github.com/docling-project/docling)** — Extract typed structure from PDFs and scanned business files  
  Public license indication: MIT code; model licenses separate. [Evidence](https://github.com/docling-project/docling). Current revision and dependencies require audit.
- **[Google OR-Tools](https://github.com/google/or-tools)** — Assignment, scheduling, purchasing and routing optimization  
  Public license indication: Apache-2.0. [Evidence](https://github.com/google/or-tools). Current revision and dependencies require audit.
- **[OSRM](https://github.com/Project-OSRM/osrm-backend)** — Travel-time estimates and geospatial routing  
  Public license indication: BSD-2-Clause. [Evidence](https://github.com/Project-OSRM/osrm-backend). Current revision and dependencies require audit.
- **[Activepieces Community core](https://github.com/activepieces/activepieces)** — Reuse individual pieces/connectors and integration abstractions; do not depend on EE  
  Public license indication: MIT core; enterprise modules proprietary. [Evidence](https://www.activepieces.com/docs/about/license). Current revision and dependencies require audit.

## Gluons-specific work still required
Carrier-specific TMS/POD integration, rate contract reconciliation, notification rules

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **HIGH**. This does not imply full parity with the startup category reference.
