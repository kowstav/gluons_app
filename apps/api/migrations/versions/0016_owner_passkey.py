"""Owner passkeys and session-bound decision challenges."""
import sqlalchemy as sa
from alembic import op

revision = "0016_owner_passkey"
down_revision = "0015_email_sign_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_passkeys",
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "passkey_challenges",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(40), sa.ForeignKey("identity_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("decision_hash", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_passkey_challenges_user_id", "passkey_challenges", ["user_id"])
    op.create_index("ix_passkey_challenges_expires_at", "passkey_challenges", ["expires_at"])
    for table in ("owner_passkeys", "passkey_challenges"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY own_passkey ON owner_passkeys FOR ALL TO gluons_app
        USING (user_id = current_setting('app.user_id', true))
        WITH CHECK (user_id = current_setting('app.user_id', true))""")
    op.execute("""CREATE POLICY own_passkey_challenge ON passkey_challenges FOR ALL TO gluons_app
        USING (user_id = current_setting('app.user_id', true) OR
               (current_setting('app.passkey_cleanup', true) = '1' AND expires_at <= now()))
        WITH CHECK (user_id = current_setting('app.user_id', true))""")
    op.execute("GRANT SELECT, INSERT, UPDATE ON owner_passkeys TO gluons_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON passkey_challenges TO gluons_app")


def downgrade() -> None:
    op.drop_table("passkey_challenges")
    op.drop_table("owner_passkeys")
