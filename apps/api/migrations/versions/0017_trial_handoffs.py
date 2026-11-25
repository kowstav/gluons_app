"""Store the explicitly configured destination of a bounded trial handoff."""
import sqlalchemy as sa
from alembic import op

revision = "0017_trial_handoffs"
down_revision = "0016_owner_passkey"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automations", sa.Column("trial_handoff_target_id", sa.String(40)))
    op.create_foreign_key("fk_automation_trial_handoff_target", "automations", "automations",
                          ["trial_handoff_target_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_automation_trial_handoff_target", "automations", type_="foreignkey")
    op.drop_column("automations", "trial_handoff_target_id")
