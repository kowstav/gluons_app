# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `property_valuation_reports`

## Candidate reusable components

- **[Docling](https://github.com/docling-project/docling)** — Extract typed structure from PDFs and scanned business files  
  Public license indication: MIT code; model licenses separate. [Evidence](https://github.com/docling-project/docling). Current revision and dependencies require audit.
- **[Meilisearch CE](https://github.com/meilisearch/meilisearch)** — Catalog and task search in skills/knowledge  
  Public license indication: MIT community; EE BUSL/commercial. [Evidence](https://github.com/meilisearch/meilisearch). Current revision and dependencies require audit.

## Gluons-specific work still required
Licensed appraiser process, permitted comps, valuation research/legal sign-off

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **LOW_REGULATED**. This does not imply full parity with the startup category reference.
