# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `title_closing_support`

## Candidate reusable components

- **[Docling](https://github.com/docling-project/docling)** — Extract typed structure from PDFs and scanned business files  
  Public license indication: MIT code; model licenses separate. [Evidence](https://github.com/docling-project/docling). Current revision and dependencies require audit.
- **[MarkItDown](https://github.com/microsoft/markitdown)** — Normalize Office/PDF/web documents to text for downstream extraction  
  Public license indication: MIT. [Evidence](https://github.com/microsoft/markitdown/blob/main/LICENSE). Current revision and dependencies require audit.
- **[Browser Use](https://github.com/browser-use/browser-use)** — Last-resort browser interaction where permitted APIs absent  
  Public license indication: MIT Python library; hosted cloud service paid. [Evidence](https://github.com/browser-use/browser-use). Current revision and dependencies require audit.

## Gluons-specific work still required
Authorized property-record access, state-specific legal review, closing software integration

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **LOW_REGULATED**. This does not imply full parity with the startup category reference.
