"""Reserve global identity-link and browser-bound intent records for EX-06."""
import sqlalchemy as sa
from alembic import op

revision = "0014_external_identity"
down_revision = "0013_passport_consents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("external_identity_links",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=False), sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("issuer", "subject", name="uq_external_identity_subject"),
        sa.UniqueConstraint("user_id", "issuer", name="uq_external_identity_user_issuer"))
    op.create_index("ix_external_identity_links_user_id", "external_identity_links", ["user_id"])
    op.create_table("external_identity_intents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_hash", sa.String(64), nullable=False), sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("verifier_sealed", sa.Text(), nullable=False), sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("session_id", sa.String(40), sa.ForeignKey("identity_sessions.id", ondelete="CASCADE")),
        sa.Column("outer_request_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("(purpose = 'login' AND user_id IS NULL AND session_id IS NULL) OR "
                           "(purpose = 'link' AND user_id IS NOT NULL AND session_id IS NOT NULL)",
                           name="ck_external_identity_intent_purpose"))
    op.create_index("ix_external_identity_intents_user_id", "external_identity_intents", ["user_id"])
    # Identity records are global, but access is narrower than the general users table.
    # The identity service sets these transaction-local values only after validating
    # the authenticated session or verified provider subject/browser-bound state.
    op.execute("ALTER TABLE external_identity_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE external_identity_links FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY identity_link_read ON external_identity_links FOR SELECT TO gluons_app USING (
        user_id = current_setting('app.user_id', true) OR
        (issuer = current_setting('app.identity_issuer', true) AND subject = current_setting('app.identity_subject', true)))""")
    op.execute("""CREATE POLICY identity_link_insert ON external_identity_links FOR INSERT TO gluons_app
        WITH CHECK (user_id = current_setting('app.user_id', true))""")
    op.execute("""CREATE POLICY identity_link_update ON external_identity_links FOR UPDATE TO gluons_app
        USING (user_id = current_setting('app.user_id', true)) WITH CHECK (user_id = current_setting('app.user_id', true))""")
    op.execute("ALTER TABLE external_identity_intents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE external_identity_intents FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY identity_intent_access ON external_identity_intents FOR ALL TO gluons_app
        USING ((state_hash = current_setting('app.identity_state_hash', true) AND
                browser_hash = current_setting('app.identity_browser_hash', true)) OR
               (current_setting('app.identity_cleanup', true) = '1' AND expires_at < now()))
        WITH CHECK (state_hash = current_setting('app.identity_state_hash', true) AND
                    browser_hash = current_setting('app.identity_browser_hash', true))""")
    op.execute("GRANT SELECT, INSERT, UPDATE ON external_identity_links TO gluons_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON external_identity_intents TO gluons_app")


def downgrade() -> None:
    op.drop_table("external_identity_intents")
    op.drop_table("external_identity_links")
