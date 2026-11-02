# Open-source component integration plan

**STATUS: PLANNED; NO UPSTREAM REPOSITORIES INSTALLED OR INTEGRATED.**

Skill: `dental_reception_and_claims`

## Candidate reusable components

- **[ElevenLabs Python SDK](https://github.com/elevenlabs/elevenlabs-python)** — Create/configure voice sessions and send verified tool callbacks  
  Public license indication: MIT SDK; hosted service paid. [Evidence](https://github.com/elevenlabs/elevenlabs-python/blob/main/pyproject.toml). Current revision and dependencies require audit.
- **[Novu core](https://github.com/novuhq/novu)** — Multichannel owner notices, approvals and task status notifications  
  Public license indication: MIT core; enterprise proprietary. [Evidence](https://github.com/novuhq/novu). Current revision and dependencies require audit.
- **[Activepieces Community core](https://github.com/activepieces/activepieces)** — Reuse individual pieces/connectors and integration abstractions; do not depend on EE  
  Public license indication: MIT core; enterprise modules proprietary. [Evidence](https://www.activepieces.com/docs/about/license). Current revision and dependencies require audit.

## Gluons-specific work still required
Practice management APIs, eligibility services, protected health data safeguards

## Implementation gate
1. Verify repository license, dependencies and immutable commit.
2. Build a typed Gluons adapter and integrate only authorized provider scopes.
3. Implement replay/shadow with zero external writes, and supervised approval before real writes.
4. Test duplicate events, prompt injection, provider failures, cancellation and cross-tenant access.
5. Verify external writes by reading the authoritative system; audit model cost, outcomes, and errors.
6. Certify the skill only after domain review, real provider sandbox tests, and operator sign-off.

Candidate suitability: **LOW_REGULATED**. This does not imply full parity with the startup category reference.
