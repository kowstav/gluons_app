# EX-01 provider proof preparation

This is an opt-in preparation path. A collector result is a report of a Gluons gateway receipt, not independent verification of a real provider or permission to perform a write. The current integration branch has passed CI; merge of EX-00 to main is still pending. The actual participating business, inbound channel, and system of record remain founder decisions.

## Before any external action

An owner must separately approve a bounded test in a private operational record. Record the participating business and account, environment, exact action, synthetic fixture and destination, maximum number of effects, time window, budget, responsible operator, stop control, and owner escalation route. Confirm that the connection has the required scopes and a signed owner policy and any payload-bound approval. A manifest, connection, this script, or a successful login does not grant authority. Do not use a real customer or send a message without the applicable explicit owner authorization.

The [example manifest](fixtures/gmail-proof.example.json) is deliberately unapproved, has no recipient or mailbox, and is not executable. Keep the filled owner grant and private references outside Git. Its maximum is a proposed limit, not permission to send.

The founder selected Gmail `email.send` as the first connector proof. This does not select an inbound order channel or a system of record. Gmail connections currently default to the `production` environment even for a test mailbox; do not relabel a real Gmail account as a provider sandbox. A Gmail Sent-mail readback does not prove recipient delivery. Provider credentials must remain in Gluons vault or the approved secret store, never in fixtures, source, command arguments, or evidence artifacts.

## Read-only collection

After an authorized run exists, an owner with `tasks:read` can collect a limited summary using the existing authenticated API. Supply the Gluons browser session securely through the process environment as `GLUONS_PROOF_SESSION`; do not paste it into chat, a command line, or a file. The tool uses the default `gluons_session` cookie name, sends a single GET to `/v1/runs/{run_id}`, rejects redirects, and accepts HTTPS origins or HTTP loopback only. It makes no provider calls and never creates or retries a run. Example arguments (replace all placeholders; obtain the session through an approved local process):

```powershell
.venv/Scripts/python.exe scripts/provider_proof.py --api-origin https://api.example.invalid --run-id run_REPLACE --provider gmail --action email.send --environment production
```

The tool rejects a missing or ambiguous matching receipt and inconsistent verified records. Output contains only an allowlisted environment, action, status, verification flag and presence flags; it deliberately excludes raw payloads, provider IDs, receipt data, warning text, account details and session secrets. `gateway_verified` reflects the stored gateway result; `independent_provider_evidence` remains `not_collected` and `review_status` remains `pending`. A simulator result must stay labelled simulator. An empty or delayed lookup is not proof of absence or permission to resend.

## Evidence review and next step

Keep raw provider responses, exact account/resource identifiers, request IDs, consent, and independent provider queries in approved private evidence storage, not the public repository or default CI artifacts. Record a sanitized summary with source commit, timestamp, environment, provider/API and skill versions, fixture reference, expected and actual outcomes, assertions, authorized reviewer, and stable private evidence reference. Compare the independently queried account and meaningful fields against the authorized fixture and the gateway receipt. Capture duplicate, out-of-order, lost-response, delayed-readback, revoke and budget cases separately; unresolved outcomes stay pending or require human reconciliation.

This collector and its mocked HTTP tests establish only the read-only collection boundary. EX-01 is not complete until a runnable bounded Gmail test procedure and evidence capture are prepared, with explicit owner authorization before any write. Real Gmail proof and an end-to-end order workflow are later gates.

The existing order skill only sends its confirmation after a verified order. A Gmail-only proof must not bypass that graph, fabricate an order receipt, or call the connector directly; its authorized gateway-backed invocation and isolated test setup still need to be implemented and reviewed.
