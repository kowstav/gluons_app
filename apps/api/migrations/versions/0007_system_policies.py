"""Explicit worker policies instead of a superuser-only BYPASSRLS attribute.

Cloud SQL customers cannot create superusers. The application role remains forced
through tenant RLS; only the separately credentialled worker/router role receives
these policies. Append-only table grants and history triggers still apply.

Revision ID: 0007_system_policies
Revises: 0006_models
"""
from alembic import op
from sqlalchemy import text

from gluons.db.models import TENANT_TABLES

revision = "0007_system_policies"
down_revision = "0006_models"
branch_labels = None
depends_on = None
TABLES = ("organizations", *TENANT_TABLES, "jobs", "outbox", "webhook_inbox")


def upgrade() -> None:
    connection = op.get_bind()
    if connection.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname='gluons_system'")).scalar():
        # Upgrade legacy local installs; fresh Cloud SQL installs never use it.
        op.execute("ALTER ROLE gluons_system NOBYPASSRLS")
    for table in TABLES:
        op.execute(f"CREATE POLICY system_access ON {table} TO gluons_system USING (true) WITH CHECK (true)")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS system_access ON {table}")
    # Never silently restore the wider BYPASSRLS role attribute on downgrade.
