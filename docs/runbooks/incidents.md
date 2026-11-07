# Incidents

Start with containment. A business owner or admin can engage the emergency stop in Gluons Web, which blocks new external actions for that business. A stop never undoes an action that already happened.

Never fix an incident by editing audit history, resending a write by hand, or calling a provider outside the gateway.

## Alerts

| Alert | First check | Then |
|---|---|---|
| API 5xx | Logs for `/ready` failures and the request ID | Check Cloud SQL connections. Roll back if a release caused it. |
| Worker not woken | Cloud Scheduler state and the `/internal` route's OIDC audience | Run the worker job once by hand. Leases expire, so work resumes. |
| Database disk above 85 percent | Table sizes, then `jobs` and `webhook_inbox` growth | Add disk before it fills. Writes stop when it does. |
| Pub/Sub dead letters | The dead-letter topic's messages | Postgres stays the queue authority, so replay by publishing, not by editing rows. |
| Uptime | Which of API or web failed | Check the load balancer and the last deploy. |

## Stuck or failed work

| Symptom | Meaning | Action |
|---|---|---|
| Run shows `reconciliation_required` | A write's result is unknown | Let `reconcile.sweep` look it up. Never resend while a receipt is pending. |
| Incident opened from a dead letter | A job used up its retries | Read the diagnosis, fix the cause, then resolve the incident. |
| Connection `needs_reauth` | A provider refused the stored token, found by an action or the 6-hourly health check | The owner reconnects the same account, which keeps its history. Grants stay separate and are checked again. |
| Many `RATE_LIMITED` answers | One address passed a per-route limit in `ratelimit.py` | Check `GLUONS_TRUSTED_PROXY_HOPS` first. A wrong value makes every user share one address. |
| Shopify `app/uninstalled` | The merchant removed the app | Nothing to fix. The owner reinstalls from Shopify to reconnect. |
| Gmail `watch_expired` | Renewal stopped | Check the grant, the Pub/Sub topic and the connection, then let `gmail.maintain` renew it. |
| WhatsApp event `quarantined` | The connection was unavailable when it was processed | Reconnect, then requeue the inbox row after checking the event is still wanted. |
| Phone route `awaiting_carrier_evidence` | The call reached the agent, but forwarding is unproven | The owner supplies carrier evidence. A successful call does not prove forwarding. |

## Suspected credential or tenant exposure

1. Engage the emergency stop for each affected business.
2. Rotate the secret with `docs/runbooks/secrets_and_webhooks.md`.
3. Revoke assistant bindings and browser sessions.
4. Export the audit chain, then verify it with the audit verification in Gluons Web.
5. Tell affected owners. Provider reporting and legal notification duties need a person to decide them.

## Restore

Follow the hosted recovery section of `local_and_recovery.md`. A restore does not reverse email, orders or other effects that already reached a provider, so reconcile receipts before resuming workers.
