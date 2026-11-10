"""Add a versioned, separately controlled business Passport."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_business_passport"
down_revision = "0010_installs_and_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("business_passport", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("organizations", sa.Column("passport_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("organizations", "passport_version")
    op.drop_column("organizations", "business_passport")
