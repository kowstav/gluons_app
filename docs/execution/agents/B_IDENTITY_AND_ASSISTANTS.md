# Agent B — identity and assistant continuity

Own EX-04, EX-06 and EX-13. Read `identity.py`, `oauth/service.py`, `oauth/routes.py`, `mcp/server.py`, `mcp/operations.py`, auth routes, `authz.py`, assistant setup/research and existing identity/OAuth/MCP tests before editing.

## Order of work

1. Prove the existing email/MFA → assistant OAuth path against one actual supported host on an authorized HTTPS endpoint. Record host/surface/version/account constraints and callback/resource/refresh behavior. Do not wait for Google sign-in to perform this baseline proof.
2. Agree the external identity-link and login-intent schema with A/C. Add Google sign-in using maintained protocol validation, preserving current canonical users, membership, MFA/recovery and session controls.
3. Add explicit authenticated linking/unlinking with anti-takeover checks. Keep assistant authorization and provider connections separate.
4. Wire new setup/intake tools through shared services only after C/F contracts land. Preserve existing tool names and thin wrappers.
5. Prove a second client reads/pauses the same org task after independent authorization; revoke one client and verify the other remains independent.

## Acceptance

- Cover AUTH-01 through AUTH-05 and MCP-01/02 in the verification guide.
- Wrong issuer/audience/nonce/state/PKCE, replay, callback swapping, email collisions and cross-org linking fail safely.
- Each request checks current membership and scopes; role downgrade/removal cannot leave a privileged cached token path.
- Closing browser/chat preserves the correct durable intent; reopening does not create another merchant or authorize a different org.
- No Google login grants Gmail scopes. No assistant token exposes provider credentials. An ID token is not accepted as MCP access.
- Readiness and setup tool outputs are bounded and accurate; high-risk browser-only approvals return owner handoffs.
- Existing email login, verification, password reset, TOTP/recovery and session revocation regressions pass.

## Ownership and limits

A owns shared schema/config. E owns auth UI components. Coordinate the exact response/error/state contract before either codes the UI. C owns setup persistence. Do not replace the current authorization server or add parallel user/binding stores. Passkeys are a subsequent bounded package after stable origin/RP/recovery design and first-user evidence; do not casually add bespoke WebAuthn crypto. Sign in with ChatGPT is optional and provider-eligibility gated.

Focused tests: `test_core_identity.py`, `test_assistant_oauth.py`, `test_assistant_oauth_hardening.py`, `test_mcp_server.py`, `test_mcp_tools_contract.py`, security/account tests; full backend suite after shared-auth changes. Real-host evidence is separate from these tests.
