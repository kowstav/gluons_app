# Local operation and recovery

Start with `scripts/dev.py` and the README instructions. The console displays local
URLs and log paths, never secret values. File mail is local delivery only. Stop with
Ctrl+C. Do not publicly expose the ERP simulator or its fault-injection endpoints.

If a worker stops, restart it with the same database. Expired queue/run leases are
reclaimed. Inspect task receipts before retrying a consequential action. A pending
receipt triggers provider lookup; an ambiguous outcome must be reconciled rather
than manually resent. Pause/cancel prevent new actions; completed effects persist.

For an incident, engage the business emergency stop, inspect task diagnosis and
verified receipts, then reconnect or correct policy through the owner app. Never
fix the incident by editing audit history or bypassing the gateway. Releasing the
emergency stop requires the authorized owner/admin.

For hosted recovery, restore a Cloud SQL backup/PITR into an isolated staging
instance, apply the matching schema, check tenant RLS and audit-chain integrity,
and reconcile provider transactions before resuming workers. A database restore
does not reverse email, purchases or other externally committed effects. This drill
has not been performed against GCP; it is a required pre-production gate.
