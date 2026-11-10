"""Global external identity records cannot silently link subjects or sessions."""
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from gluons.core import new_id, utcnow
from gluons.db.models import ExternalIdentityIntent, ExternalIdentityLink
from gluons.db.session import public_tx, user_tx
from helpers import World


def test_subject_remains_unique_even_after_revocation(world):
    other = World.create("Other account")
    with user_tx(world.user_id) as session:
        session.add(ExternalIdentityLink(id=new_id("eil"), user_id=world.user_id,
                    issuer="https://accounts.google.com", subject="synthetic-subject", revoked_at=utcnow()))
    with pytest.raises(IntegrityError), user_tx(other.user_id) as session:
        session.add(ExternalIdentityLink(id=new_id("eil"), user_id=other.user_id,
                    issuer="https://accounts.google.com", subject="synthetic-subject"))
        session.flush()
    with pytest.raises(IntegrityError), user_tx(world.user_id) as session:
        session.add(ExternalIdentityLink(id=new_id("eil"), user_id=world.user_id,
                    issuer="https://accounts.google.com", subject="another-subject"))
        session.flush()
    with user_tx(other.user_id) as session:
        assert session.scalars(select(ExternalIdentityLink)).all() == []
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_issuer', :issuer, true), set_config('app.identity_subject', :subject, true)"),
                        {"issuer": "https://accounts.google.com", "subject": "synthetic-subject"})
        assert session.scalar(select(ExternalIdentityLink)).user_id == world.user_id
        changed = session.execute(text("UPDATE external_identity_links SET revoked_at = NULL WHERE subject = :subject"),
                                  {"subject": "synthetic-subject"})
        assert changed.rowcount == 0


def test_login_and_link_intents_have_distinct_session_requirements(world):
    values = {"state_hash": "a" * 64, "browser_hash": "b" * 64, "nonce_hash": "c" * 64,
              "verifier_sealed": "synthetic-sealed-value", "expires_at": utcnow() + timedelta(minutes=10)}
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), set_config('app.identity_browser_hash', :browser, true)"),
                        {"state": "a" * 64, "browser": "b" * 64})
        session.add(ExternalIdentityIntent(id=new_id("eii"), purpose="login", **values))
    with pytest.raises(IntegrityError), public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), set_config('app.identity_browser_hash', :browser, true)"),
                        {"state": "d" * 64, "browser": "b" * 64})
        session.add(ExternalIdentityIntent(id=new_id("eii"), purpose="link", user_id=world.user_id,
                    **{**values, "state_hash": "d" * 64}))
        session.flush()
    with pytest.raises(IntegrityError), public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), set_config('app.identity_browser_hash', :browser, true)"),
                        {"state": "e" * 64, "browser": "b" * 64})
        session.add(ExternalIdentityIntent(id=new_id("eii"), purpose="login", user_id=world.user_id,
                    **{**values, "state_hash": "e" * 64}))
        session.flush()
    with public_tx() as session:
        assert session.scalars(select(ExternalIdentityIntent)).all() == []
        session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), set_config('app.identity_browser_hash', :browser, true)"),
                        {"state": "a" * 64, "browser": "wrong-browser"})
        assert session.scalars(select(ExternalIdentityIntent)).all() == []
        session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), set_config('app.identity_browser_hash', :browser, true)"),
                        {"state": "a" * 64, "browser": "b" * 64})
        assert session.scalar(select(ExternalIdentityIntent).where(ExternalIdentityIntent.state_hash == "a" * 64)) is not None
        assert session.scalar(text("SELECT has_table_privilege('gluons_app', 'external_identity_intents', 'DELETE')"))
        assert not session.scalar(text("SELECT has_table_privilege('gluons_app', 'external_identity_links', 'DELETE')"))


def test_intent_cleanup_context_only_sees_expired_rows():
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), set_config('app.identity_browser_hash', :browser, true)"),
                        {"state": "f" * 64, "browser": "b" * 64})
        session.add(ExternalIdentityIntent(id=new_id("eii"), purpose="login", state_hash="f" * 64,
            browser_hash="b" * 64, nonce_hash="c" * 64, verifier_sealed="synthetic-sealed-value", expires_at=utcnow() - timedelta(seconds=1)))
    with public_tx() as session:
        session.execute(text("SELECT set_config('app.identity_cleanup', '1', true)"))
        visible = session.scalars(select(ExternalIdentityIntent)).all()
        assert visible and all(item.expires_at < utcnow() for item in visible)
