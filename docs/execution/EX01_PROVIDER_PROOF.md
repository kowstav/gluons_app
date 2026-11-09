# EX-01 provider proof preparation

This is an opt-in preparation path. A collector result is not permission to perform a write. EX-00 was merged into main on November 9; this work remains on the separate EX-01 branch. The actual participating business, inbound channel, and system of record remain founder decisions.

## Before any external action

An owner must separately approve a bounded test in a private operational record. Record the participating business and account, environment, exact action, synthetic fixture and destination, maximum number of effects, time window, budget, responsible operator, stop control, and owner escalation route. Confirm that the connection has the required scopes and a signed owner policy and any payload-bound approval. A manifest, connection, this script, or a successful login does not grant authority. Do not use a real customer or send a message without the applicable explicit owner authorization.

The policy proposal API accepts an optional timezone-aware `expires_at`; the existing signature binds the deadline and the gateway rejects an expired policy. This permits a time-bounded grant, but does not by itself impose a one-message limit or replace the exact owner approval.

The [example manifest](fixtures/gmail-proof.example.json) is deliberately unapproved, has no recipient or mailbox, and is not executable. Keep the filled owner grant and private references outside Git. Its maximum is a proposed limit, not permission to send.

The founder selected Gmail `email.send` as the first connector proof. This does not select an inbound order channel or a system of record. Gmail connections currently default to the `production` environment even for a test mailbox; do not relabel a real Gmail account as a provider sandbox. A Gmail Sent-mail readback does not prove recipient delivery. Provider credentials must remain in Gluons vault or the approved secret store, never in fixtures, source, command arguments, or evidence artifacts.

## Read-only collection

After an authorized run exists, an owner with `tasks:read` can collect a limited summary using the authenticated API. Supply the Gluons browser session securely through the process environment as `GLUONS_PROOF_SESSION`; do not paste it into chat, a command line, or a file. The tool uses the default `gluons_session` cookie name, rejects redirects, and accepts HTTPS origins or HTTP loopback only. By default it sends one GET to `/v1/runs/{run_id}` and does not contact Gmail. Example arguments (replace all placeholders; obtain the session through an approved local process):

```powershell
.venv/Scripts/python.exe scripts/provider_proof.py --api-origin https://api.example.invalid --run-id run_REPLACE --provider gmail --action email.send --environment production
```

The tool rejects a missing or ambiguous matching receipt and inconsistent verified records. Output contains only an allowlisted environment, action, status, verification flag and presence flags; it deliberately excludes raw payloads, provider IDs, receipt data, warning text, account details and session secrets. `gateway_verified` reflects the stored gateway result. A simulator result must stay labelled simulator. An empty or delayed lookup is not proof of absence or permission to resend.

The explicit `--independent-readback` option sends a second GET to an owner-only Gluons endpoint. It rechecks the Google subject and verified email against the confirmed connection, verifies the connection did not change, then queries the existing Sent message and compares the saved recipient, subject, body, Message-ID, message ID and Sent label. The endpoint returns only booleans and a scope label; it does not change the receipt. The result can be `matched` or `mismatch`, and `review_status` remains `pending`. It is a fresh provider readback through the same adapter, not a separate implementation or proof of delivery. It may refresh an expired OAuth token; it cannot open a business write permit, send, or retry. Do not use this option until the owner has chosen the account and run to inspect. Provider responses and raw messages are not stored by this collector.

## Evidence review and next step

Keep raw provider responses, exact account/resource identifiers, request IDs, consent, and independent provider queries in approved private evidence storage, not the public repository or default CI artifacts. Record a sanitized summary with source commit, timestamp, environment, provider/API and skill versions, fixture reference, expected and actual outcomes, assertions, authorized reviewer, and stable private evidence reference. Compare the independently queried account and meaningful fields against the authorized fixture and the gateway receipt. Capture duplicate, out-of-order, lost-response, delayed-readback, revoke and budget cases separately; unresolved outcomes stay pending or require human reconciliation.

## Isolated self-test setup

The repository includes an opt-in local fixture skill, `gmail_self_proof`. It submits a fixed synthetic message to the confirmed Gmail account through the normal gateway, with owner approval and readback. It does not fabricate an order or prove delivery. Its action is `proof.gmail.self_send`; provider-double tests are not evidence of a Google result. The fixture requires a separate loopback PostgreSQL workspace and is disabled in hosted environments.

Before starting, configure a Google OAuth Web client for the dedicated test account and register `http://localhost:18700/v1/oauth/gmail/callback`. Provide `GLUONS_GOOGLE_CLIENT_ID`, `GLUONS_GOOGLE_CLIENT_SECRET`, and separate non-default base64-encoded 32-byte `GLUONS_VAULT_KEK_B64` and `GLUONS_SIGNING_KEY_B64` through an approved local environment. Do not reuse production keys or put secrets in Git, chat, or command arguments. Install the locked backend and web dependencies. Start a fresh named workspace:

```powershell
.venv/Scripts/python.exe scripts/gmail_proof_dev.py --workspace gmail-trial --web
```

The launcher creates isolated data and starts the API and owner web app on localhost ports 18700 and 13700, with the worker disabled. It does not submit an event or send a message. Verify the owner's account using the local file outbox in `.gluons-data/gmail-proof-gmail-trial/mail`. In the isolated owner interface, create a business, connect Gmail with both reading and sending scopes, verify and confirm the exact test account, and create the **Isolated Gmail self-test** draft. In its configuration tab, set `connections.email` to the confirmed connection ID. In permissions, propose a supervised policy for only `proof.gmail.self_send`, that connection ID, and the confirmed account as the only email recipient, expiring within 24 hours. Review all displayed fields and sign the exact policy, then activate supervised mode.

Only after the owner separately authorizes the exact self-addressed test as described above, stop the launcher and restart it with `--resume --web --worker`. The worker may resume previously approved jobs in that workspace. Submit `{}` as the manual input, inspect the resulting R3 owner approval and approve only if the fixed recipient and synthetic contents match the authorization. The automation-wide idempotency key permits at most one effect; a timeout or missing readback is not permission to resend. Stop the local services after the test and retain private material according to the approved evidence process.

The collector accepts `--action proof.gmail.self_send` for this receipt. Its optional independent readback is still a fresh query through the same Gmail adapter, and the collector does not replace independent owner review. Real Gmail proof and an end-to-end order workflow remain later gates until dated, sanitized evidence is reviewed.
