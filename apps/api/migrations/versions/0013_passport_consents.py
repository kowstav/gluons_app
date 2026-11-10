"""Retain owner-reviewed Passport sharing consent with the organization."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_passport_consents"
down_revision = "0012_setup_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("passport_consents", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("organizations", "passport_consents")
