"""Verified-subject account links, using local identities and no Google provider."""
from urllib.parse import parse_qs, urlsplit

import pytest

from gluons import external_identity_intents as intents
from gluons import external_identity_links as links
from gluons import google_identity, identity
from gluons.core import GluonsError, utcnow
from gluons.db.models import User
from gluons.db.session import public_tx
from helpers import PASSWORD, World


def verified(subject, email):
    return google_identity.VerifiedIdentity("https://accounts.google.com", subject, email, "Synthetic")


def linking_intent(token):
    started = intents.begin(purpose="link", client_id="synthetic-client",
                            redirect_uri="https://api.example.invalid/v1/auth/google/callback", session_token=token)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    return intents.consume(state=state, browser=started.browser, session_token=token)


def test_matching_email_never_implicitly_links_and_subject_is_unique(world):
    other = World.create("Other business")
    first = identity.login(world.email, PASSWORD)
    second = identity.login(other.email, PASSWORD)
    provider = verified("synthetic-subject", world.email)
    with pytest.raises(GluonsError):
        links.sign_in(provider)
    links.link(linking_intent(first.token), provider, session_token=first.token)
    issued = links.sign_in(verified("synthetic-subject", other.email))
    assert issued.user_id == world.user_id
    with pytest.raises(GluonsError):
        links.link(linking_intent(second.token), provider, session_token=second.token)
    with pytest.raises(GluonsError):
        links.sign_in(verified("different-subject", world.email))
    with public_tx() as session:
        session.get(User, world.user_id).mfa_enabled_at = utcnow()
    mfa_session = links.sign_in(provider)
    assert mfa_session.mfa_required and identity.resolve_session(mfa_session.token).active_tenant_id is None


def test_revoked_session_and_last_login_protection(world):
    issued = identity.login(world.email, PASSWORD)
    intent = linking_intent(issued.token)
    identity.logout(issued.token)
    with pytest.raises(GluonsError):
        links.link(intent, verified("subject-revoked", world.email), session_token=issued.token)
    fresh = identity.login(world.email, PASSWORD)
    provider = verified("subject-active", world.email)
    links.link(linking_intent(fresh.token), provider, session_token=fresh.token)
    with public_tx() as session:
        user = session.get(User, world.user_id)
        saved_hash, user.password_hash = user.password_hash, None
    with pytest.raises(GluonsError):
        links.unlink(session_token=fresh.token)
    with public_tx() as session:
        session.get(User, world.user_id).password_hash = saved_hash
    links.unlink(session_token=fresh.token)
    with pytest.raises(GluonsError):
        links.sign_in(provider)
    other = World.create("Other business")
    other_session = identity.login(other.email, PASSWORD)
    with pytest.raises(GluonsError):
        links.link(linking_intent(other_session.token), provider, session_token=other_session.token)
    links.link(linking_intent(fresh.token), provider, session_token=fresh.token)
    assert links.sign_in(provider).user_id == world.user_id
