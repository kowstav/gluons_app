# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `legal_matter_intake`

## Candidate reusable components

- **[Chatwoot](https://github.com/chatwoot/chatwoot)** — Customer-support inbox and human handoff  
  Public license indication: MIT. [Evidence](https://github.com/chatwoot/chatwoot). Current revision and dependencies require audit.
- **[Docling](https://github.com/docling-project/docling)** — Extract typed structure from PDFs and scanned business files  
  Public license indication: MIT code; model licenses separate. [Evidence](https://github.com/docling-project/docling). Current revision and dependencies require audit.
- **[Novu core](https://github.com/novuhq/novu)** — Multichannel owner notices, approvals and task status notifications  
  Public license indication: MIT core; enterprise proprietary. [Evidence](https://github.com/novuhq/novu). Current revision and dependencies require audit.

## Gluons-specific work still required
Lawyer-controlled conflict search, ethical duties, consent and engagement letter

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **LOW_REGULATED**. This does not imply full parity with the startup category reference.
