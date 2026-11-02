# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `workflow_discovery`

## Candidate reusable components

- **[LangGraph](https://github.com/langchain-ai/langgraph)** — Stateful supervised planning and agent composition, not external write authority  
  Public license indication: MIT. [Evidence](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/pyproject.toml). Current revision and dependencies require audit.
- **[Browser Use](https://github.com/browser-use/browser-use)** — Last-resort browser interaction where permitted APIs absent  
  Public license indication: MIT Python library; hosted cloud service paid. [Evidence](https://github.com/browser-use/browser-use). Current revision and dependencies require audit.
- **[Presidio](https://github.com/data-privacy-stack/presidio)** — PII detection and redaction ahead of model calls/logs  
  Public license indication: MIT. [Evidence](https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md). Current revision and dependencies require audit.

## Gluons-specific work still required
Consented desktop telemetry and proof of repetition, policy, generated integration tests

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **MEDIUM**. This does not imply full parity with the startup category reference.
