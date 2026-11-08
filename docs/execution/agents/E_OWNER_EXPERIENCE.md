# Agent E — owner experience and connection room

Own EX-07 and UI support for B/C/F. Read `apps/web/AGENTS.md` and relevant installed Next.js documentation, the actual app routing, `console.tsx`, `operations.tsx`, `lib/api.ts`, current owner-flow tests and agreed backend contracts. Build inside the existing app; do not introduce a parallel demo or replacement dashboard.

## Deliverables

1. One focused connection/setup room driven by the real API: selected business, known profile fields, missing questions, provider cards, pending owner actions and resumable progress.
2. Separate but coherent sign-in, assistant consent, provider account connection and business-action approval views. Plain language explains who is being authorized and what changes.
3. Internal draft preview and exact external-action diff, recipient/account/amount where applicable, expiry and receipt view.
4. Mobile/desktop interrupted-flow recovery, existing-account reuse, pending provider review, retry/reconnect and explicit unsupported-provider paths.
5. Instrumentation for active setup time, repeated fields, help requests, completion and owner comprehension without logging sensitive form content.

## Acceptance

- UI-01 through UI-03 and the UI portions of SETUP-01/02/03 and COM-01 pass against the real API/Postgres.
- Business/account identity remains visible at consequential steps; multiple businesses do not silently switch after redirect.
- Browser close, sign-in expiry, denied consent, failed provider callback and pending KYC have useful recovery paths.
- Missing credentials display actionable configuration/access needs; no dead-end fake connect button or simulated success.
- Verified connection, approved action, external draft, live shop and delivery use distinct evidence-backed language.
- The owner can pause/revoke and inspect what has already happened. Do not imply undo for irreversible effects.
- Keyboard/focus/error announcements, responsive layout and loading/empty/error states work. Test actual mobile width and browser handoffs, not just CSS snapshots.
- No provider token or sensitive Passport field enters client bundles, URLs, analytics or logs.

## Coordination

Own shared frontend files exclusively while assigned. Extract targeted components to reduce conflicts when useful; avoid broad visual redesign. B supplies identity/assistant errors, C setup/profile views, F readiness. Use contract fixtures for early design, but release tests must hit actual services. No frontend-only state determines authorization or completion.

Run typecheck/build and focused Playwright journeys, then the integration browser suite. The old two broad browser tests do not cover all new redirect and recovery cases; add meaningful scenarios instead of only changing snapshots. Record screenshots with environment labels and synthetic data only.
