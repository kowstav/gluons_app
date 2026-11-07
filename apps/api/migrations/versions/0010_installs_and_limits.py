"""Shopify-initiated installs, database rate limits, and when a deleted business was purged.

Installs and rate counts exist before any tenant, so neither is tenant scoped: the app role may
read and write them, and only the worker role may delete."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_installs_and_limits"
down_revision = "0009_notification_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shopify_installs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("shop", sa.String(100), nullable=False, index=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_hash", sa.String(64)),
        sa.Column("claim_hash", sa.String(64), unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="authorizing"),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("credential_sealed", sa.Text()),
        sa.Column("shop_identity", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("claimed_tenant_id", sa.String(40)),
        sa.Column("connection_id", sa.String(40)),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("organizations", sa.Column("purged_at", sa.DateTime(timezone=True)))
    for table in ("shopify_installs", "rate_limits"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO gluons_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO gluons_system")


def downgrade() -> None:
    op.drop_column("organizations", "purged_at")
    op.drop_table("rate_limits")
    op.drop_table("shopify_installs")
