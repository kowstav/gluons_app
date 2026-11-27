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

## Verify an isolated backup restore

During the approved backup window, save a private witness from the same snapshot. Record two
distinct test tenant IDs, the first tenant's final audit hash, and a verified receipt from a
signed policy within that tenant. Keep the receipt's recorded environment label. Restore the
backup to an isolated target, then have the migration operator confirm the restored Alembic
revision matches the backup. Keep API and worker services disconnected from this target and
keep provider secrets out of the verification environment.

Set `GLUONS_RESTORE_DATABASE_URL` to the isolated target using the restricted `gluons_app`
role. For a Cloud SQL test, put the target's verified private IP in `server_address` so the
tool can distinguish two instances that use the same database name. Save the witness JSON
in private operator storage. `server_address` may be omitted for a local socket test.

```json
{
  "database": "isolated_database_name",
  "server_address": "192.0.2.5",
  "tenant_id": "first_witness_tenant_id",
  "other_tenant_id": "second_witness_tenant_id",
  "audit_head": "expected_audit_hash_from_backup",
  "receipt_id": "verified_receipt_id_from_backup",
  "receipt_environment": "sandbox"
}
```

Run `gluons verify-restore --witness <private-witness.json>`. It uses a read-only SQL
transaction and reports Boolean checks for the restored target, enforced RLS, tenant
isolation, audit chain, verified receipt and its saved policy record. A failed check
prevents acceptance. The report contains no witness IDs or database credentials.

Review queued jobs and reconcile provider activity before approving a separate cutover or
worker restart. Email, purchases and other committed provider transactions survive a
database restore. GitHub exercises a logical PostgreSQL backup and restore with synthetic
simulator evidence. The isolated Cloud SQL drill remains a required pre-production gate.
See [Cloud SQL backup restoration](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/restoring).
