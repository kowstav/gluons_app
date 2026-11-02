# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `paid_ad_campaign_operations`

## Candidate reusable components

- **[Activepieces Community core](https://github.com/activepieces/activepieces)** — Reuse individual pieces/connectors and integration abstractions; do not depend on EE  
  Public license indication: MIT core; enterprise modules proprietary. [Evidence](https://www.activepieces.com/docs/about/license). Current revision and dependencies require audit.
- **[LangGraph](https://github.com/langchain-ai/langgraph)** — Stateful supervised planning and agent composition, not external write authority  
  Public license indication: MIT. [Evidence](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/pyproject.toml). Current revision and dependencies require audit.

## Gluons-specific work still required
Platform ad APIs and advertiser permissions, budgets, creatives, human approval

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **MEDIUM**. This does not imply full parity with the startup category reference.
