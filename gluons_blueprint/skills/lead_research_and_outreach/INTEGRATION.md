# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `lead_research_and_outreach`

## Candidate reusable components

- **[Activepieces Community core](https://github.com/activepieces/activepieces)** — Reuse individual pieces/connectors and integration abstractions; do not depend on EE  
  Public license indication: MIT core; enterprise modules proprietary. [Evidence](https://www.activepieces.com/docs/about/license). Current revision and dependencies require audit.
- **[Twenty CRM](https://github.com/twentyhq/twenty)** — Test CRM using documented API; optional customer-side system  
  Public license indication: AGPL-3.0 core; some MIT SDKs/EE. [Evidence](https://github.com/twentyhq/twenty/blob/main/LICENSE). Current revision and dependencies require audit.
- **[Presidio](https://github.com/data-privacy-stack/presidio)** — PII detection and redaction ahead of model calls/logs  
  Public license indication: MIT. [Evidence](https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md). Current revision and dependencies require audit.

## Gluons-specific work still required
Permissioned lead data, opt-out, enrichment rights, email deliverability

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **MEDIUM**. This does not imply full parity with the startup category reference.
