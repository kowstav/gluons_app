"""Roles, forced row level security, append-only history and queue grants.

Isolation is a property of the database, so a missing WHERE clause in application
code cannot leak another tenant's rows.

`gluons_app`    serves requests. Row level security always applies.
`gluons_system` has BYPASSRLS. Used only to claim queue jobs and route webhooks.

Revision ID: 0002_security
Revises: 0001_schema
"""
from __future__ import annotations

import os

from alembic import op
from psycopg import sql
from sqlalchemy import text

from gluons.db.models import TENANT_TABLES

revision = "0002_security"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

APPEND_ONLY = ("audit_events", "run_events", "usage_events", "approval_decisions")
# Readable with no tenant selected: catalog and sign-in tables only.
GLOBAL_READ_WRITE = (
    "users",
    "user_tokens",
    "identity_sessions",
    "oauth_clients",
    "oauth_authorization_requests",
)
GLOBAL_READ_ONLY = ("skill_versions", "skill_evidence", "model_descriptors", "model_eval_results")
NULLABLE_TENANT_TABLES = ("jobs", "outbox", "webhook_inbox")


def _create_role(name: str, password_env: str, dev_password: str, *options: str) -> None:
    # Hosted environments create the role (with a Secret Manager password) in Terraform
    # before migrating. Local databases get a throwaway password.
    password = os.environ.get(password_env)
    if not password and os.environ.get("GLUONS_DEV_ROLE_PASSWORDS"):
        password = dev_password
    connection = op.get_bind()
    if connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": name}).first():
        return
    # Compose identifiers and password literals with the PostgreSQL driver. Quotes
    # and dollar delimiters inside a secret cannot become migration SQL.
    credential = sql.SQL("LOGIN PASSWORD {} ").format(sql.Literal(password)) if password else sql.SQL("NOLOGIN ")
    statement = sql.SQL("CREATE ROLE {} ").format(sql.Identifier(name)) + credential + sql.SQL(" ".join(options))
    with connection.connection.driver_connection.cursor() as cursor:
        cursor.execute(statement)


def upgrade() -> None:
    _create_role("gluons_app", "GLUONS_APP_ROLE_PASSWORD", "gluons_app_dev", "NOBYPASSRLS", "NOSUPERUSER")
    _create_role("gluons_system", "GLUONS_SYSTEM_ROLE_PASSWORD", "gluons_system_dev", "NOBYPASSRLS", "NOSUPERUSER")
    op.execute("GRANT USAGE ON SCHEMA public TO gluons_app, gluons_system")

    # organizations: visible when selected, or when the current user is a member.
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY org_member_read ON organizations FOR SELECT USING (
          id = current_setting('app.tenant_id', true)
          OR EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.tenant_id = organizations.id
              AND m.user_id = current_setting('app.user_id', true)
              AND m.removed_at IS NULL
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY org_selected_write ON organizations
        FOR INSERT WITH CHECK (id = current_setting('app.tenant_id', true))
        """
    )
    op.execute(
        """
        CREATE POLICY org_selected_update ON organizations
        FOR UPDATE USING (id = current_setting('app.tenant_id', true))
        WITH CHECK (id = current_setting('app.tenant_id', true))
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON organizations TO gluons_app")

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        if table == "memberships":
            op.execute(
                """
                CREATE POLICY tenant_or_self ON memberships USING (
                  tenant_id = current_setting('app.tenant_id', true)
                  OR user_id = current_setting('app.user_id', true)
                ) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
                """
            )
        else:
            op.execute(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
                """
            )
        privileges = "SELECT, INSERT" if table in APPEND_ONLY else "SELECT, INSERT, UPDATE, DELETE"
        op.execute(f"GRANT {privileges} ON {table} TO gluons_app")

    # Jobs, outbox and inbox may carry no tenant. The app role sees only its tenant's rows.
    for table in NULLABLE_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO gluons_app")

    for table in GLOBAL_READ_WRITE:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO gluons_app")
    for table in GLOBAL_READ_ONLY:
        op.execute(f"GRANT SELECT ON {table} TO gluons_app")

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gluons_system")
    op.execute("REVOKE UPDATE, DELETE ON audit_events, run_events, usage_events, approval_decisions FROM gluons_system")

    # Defence in depth: even the table owner cannot rewrite history.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION gluons_reject_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'history table % is append-only', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in APPEND_ONLY:
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION gluons_reject_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION gluons_reject_mutation()
            """
        )


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
    op.execute("DROP FUNCTION IF EXISTS gluons_reject_mutation")
    for table in (*TENANT_TABLES, *NULLABLE_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_or_self ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_member_read ON organizations")
    op.execute("DROP POLICY IF EXISTS org_selected_write ON organizations")
    op.execute("DROP POLICY IF EXISTS org_selected_update ON organizations")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")
