"""Persist provider labels and attachment-review flags across worker restarts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_channel_metadata"
down_revision = "0007_system_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_messages", sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("channel_messages", "metadata_json")
