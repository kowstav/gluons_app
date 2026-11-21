"""Remember the last accepted time-based code step so each code works once."""
import sqlalchemy as sa
from alembic import op

revision = "0015_mfa_single_use"
down_revision = "0014_external_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_last_step", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("users", "mfa_last_step")
