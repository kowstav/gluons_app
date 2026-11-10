"""Browser-bound identity intents use real restricted-role PostgreSQL; no Google call occurs."""
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, text

from gluons import external_identity_intents as intents
from gluons import identity
from gluons.core import GluonsError, token_hash, utcnow
from gluons.db.models import ExternalIdentityIntent, IdentitySession, OAuthAuthorizationRequest, User
from gluons.db.session import public_tx
from helpers import PASSWORD


def start(**kwargs):
    return intents.begin(client_id="synthetic-client", redirect_uri="https://api.example.invalid/v1/auth/google/callback", **kwargs)


def parameter(started, name):
    return parse_qs(urlsplit(started.authorization_url).query)[name][0]


def bind(session, started, *, browser=None):
    session.execute(text("SELECT set_config('app.identity_state_hash', :state, true), set_config('app.identity_browser_hash', :browser, true)"),
        {"state": token_hash(parameter(started, "state")), "browser": token_hash(browser or started.browser)})


def test_login_intent_is_browser_bound_single_use_and_wipes_verifier():
    started = start(purpose="login")
    with public_tx() as session:
        bind(session, started)
        row = session.scalar(select(ExternalIdentityIntent))
        sealed = row.verifier_sealed
        assert row.state_hash == token_hash(parameter(started, "state")) and row.browser_hash == token_hash(started.browser)
    with pytest.raises(GluonsError):
        intents.consume(state=parameter(started, "state"), browser="wrong-browser-" + "x" * 32)
    result = intents.consume(state=parameter(started, "state"), browser=started.browser)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(result.verifier.encode()).digest()).rstrip(b"=").decode()
    assert result.purpose == "login" and challenge == parameter(started, "code_challenge") and result.verifier not in sealed
    assert result.verifier not in started.authorization_url and result.verifier not in repr(result) and started.browser not in repr(started)
    assert result.nonce_hash == token_hash(parameter(started, "nonce")) and result.user_id is None
    with public_tx() as session:
        bind(session, started)
        row = session.scalar(select(ExternalIdentityIntent))
        assert row.used_at is not None and row.verifier_sealed == ""
    with pytest.raises(GluonsError):
        intents.consume(state=parameter(started, "state"), browser=started.browser)


def test_concurrent_callbacks_consume_once():
    started = start(purpose="login")
    def consume():
        try:
            return intents.consume(state=parameter(started, "state"), browser=started.browser).purpose
        except GluonsError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))
    assert sorted(results) == ["IDENTITY_INTENT_INVALID", "login"]


def test_expired_intent_is_denied_and_cleanup_preserves_active():
    expired = start(purpose="login")
    active = start(purpose="login")
    with public_tx() as session:
        bind(session, expired)
        session.scalar(select(ExternalIdentityIntent)).expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(GluonsError):
        intents.consume(state=parameter(expired, "state"), browser=expired.browser)
    assert intents.purge_expired() >= 1
    assert intents.consume(state=parameter(active, "state"), browser=active.browser).purpose == "login"


def test_link_requires_same_recent_active_session_and_mfa(world):
    first = identity.login(world.email, PASSWORD)
    second = identity.login(world.email, PASSWORD)
    started = start(purpose="link", session_token=first.token)
    with pytest.raises(GluonsError):
        intents.consume(state=parameter(started, "state"), browser=started.browser, session_token=second.token)
    result = intents.consume(state=parameter(started, "state"), browser=started.browser, session_token=first.token)
    assert result.user_id == world.user_id and result.session_id == first.session_id
    revoked = start(purpose="link", session_token=first.token)
    identity.logout(first.token)
    with pytest.raises(GluonsError):
        intents.consume(state=parameter(revoked, "state"), browser=revoked.browser, session_token=first.token)
    with public_tx() as session:
        session.get(IdentitySession, second.session_id).created_at = utcnow() - timedelta(minutes=11)
    with pytest.raises(GluonsError):
        start(purpose="link", session_token=second.token)
    third = identity.login(world.email, PASSWORD)
    with public_tx() as session:
        session.get(User, world.user_id).mfa_enabled_at = utcnow()
    with pytest.raises(GluonsError):
        start(purpose="link", session_token=third.token)
    with public_tx() as session:
        session.get(IdentitySession, third.session_id).mfa_verified_at = utcnow() - timedelta(minutes=1)
    with pytest.raises(GluonsError):
        start(purpose="link", session_token=third.token)


def test_unvalidated_assistant_request_is_not_accepted():
    with pytest.raises(GluonsError):
        start(purpose="login", outer_request_id="https://attacker.example")


def test_assistant_request_is_referenced_without_authorizing_it(world):
    from test_assistant_oauth import authorization
    _, pending = authorization(world)
    started = start(purpose="login", outer_request_id=pending["request_id"])
    result = intents.consume(state=parameter(started, "state"), browser=started.browser)
    assert result.outer_request_id == pending["request_id"]
    with public_tx() as session:
        outer = session.get(OAuthAuthorizationRequest, pending["request_id"])
        assert outer.status == "pending" and outer.user_id is None and outer.tenant_id is None
    expired = start(purpose="login", outer_request_id=pending["request_id"])
    with public_tx() as session:
        session.get(OAuthAuthorizationRequest, pending["request_id"]).expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(GluonsError):
        intents.consume(state=parameter(expired, "state"), browser=expired.browser)
