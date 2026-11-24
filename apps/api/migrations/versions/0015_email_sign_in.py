"""Browser-bound, one-use email sign-in challenges."""
import sqlalchemy as sa
from alembic import op

revision = "0015_email_sign_in"
down_revision = "0014_external_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_sign_in_challenges",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_email_sign_in_challenges_expires_at", "email_sign_in_challenges", ["expires_at"])
    op.execute("ALTER TABLE email_sign_in_challenges ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_sign_in_challenges FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY email_sign_in_challenge_access ON email_sign_in_challenges FOR ALL TO gluons_app
        USING ((id = current_setting('app.email_challenge_id', true) AND
                browser_hash = current_setting('app.email_browser_hash', true)) OR
               (current_setting('app.email_challenge_cleanup', true) = '1' AND expires_at <= now()))
        WITH CHECK (id = current_setting('app.email_challenge_id', true) AND
                    browser_hash = current_setting('app.email_browser_hash', true))""")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON email_sign_in_challenges TO gluons_app")


def downgrade() -> None:
    op.drop_table("email_sign_in_challenges")
