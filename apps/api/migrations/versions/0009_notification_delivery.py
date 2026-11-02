"""Record when an urgent notification was emailed, so a retry never repeats it."""
import sqlalchemy as sa
from alembic import op

revision = "0009_notification_delivery"
down_revision = "0008_channel_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("delivered_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("notifications", "delivered_at")
