"""Google OIDC protocol tests use local keys and HTTP doubles, not Google evidence."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from gluons import google_identity as google
from gluons.core import GluonsError, token_hash, utcnow

CLIENT = "synthetic-google-client"
CALLBACK = "https://api.example.invalid/v1/auth/google/callback"


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def transport_for(key, *, duplicate=False):
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update({"kid": "synthetic-key", "alg": "RS256", "use": "sig"})
    def handle(request):
        assert str(request.url) == google.JWKS_URL and request.method == "GET"
        return httpx.Response(200, json={"keys": [public, public] if duplicate else [public]})
    return httpx.MockTransport(handle)


def claims(nonce):
    now = utcnow()
    return {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": "synthetic-google-subject",
            "iat": now, "exp": now + timedelta(minutes=5), "nonce": nonce, "email": "Owner@Example.com",
            "email_verified": True, "name": "Test Owner"}


def signed(key, payload, *, kid="synthetic-key"):
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


def test_authorization_requests_only_identity_scopes_and_s256():
    flow = google.new_flow()
    assert len({flow.state, flow.browser, flow.nonce, flow.verifier}) == 4
    query = parse_qs(urlsplit(google.authorization_url(client_id=CLIENT, redirect_uri=CALLBACK, flow=flow)).query)
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"] == [flow.challenge]
    assert "access_type" not in query and "gmail" not in query["scope"][0]
    assert flow.verifier not in repr(flow) and flow.browser not in repr(flow)
    with pytest.raises(GluonsError):
        google.authorization_url(client_id=CLIENT, redirect_uri=CALLBACK, flow=replace(flow, challenge="a" * 43))


@pytest.mark.parametrize("callback", ["http://example.invalid/v1/auth/google/callback", "https://user@example.invalid/v1/auth/google/callback",
                                      "https://example.invalid/other", "https://example.invalid/v1/auth/google/callback?next=elsewhere"])
def test_unsafe_callback_is_rejected(callback):
    with pytest.raises(GluonsError):
        google.authorization_url(client_id=CLIENT, redirect_uri=callback, flow=google.new_flow())


def test_code_exchange_uses_pkce_without_exposing_other_tokens():
    flow = google.new_flow()
    def handle(request):
        assert str(request.url) == google.EXCHANGE_ENDPOINT and request.method == "POST"
        payload = parse_qs(request.content.decode())
        assert payload["code_verifier"] == [flow.verifier] and payload["redirect_uri"] == [CALLBACK]
        return httpx.Response(200, json={"id_token": "synthetic-id-token", "access_token": "synthetic-access-token"})
    assert google.exchange_code(client_id=CLIENT, client_secret="synthetic-secret", redirect_uri=CALLBACK,
                                code="synthetic-code", verifier=flow.verifier, transport=httpx.MockTransport(handle)) == "synthetic-id-token"


def test_valid_google_subject_is_canonical_and_email_is_not_the_key(signing_key):
    flow = google.new_flow()
    payload = claims(flow.nonce)
    payload["iss"] = "accounts.google.com"
    result = google.verify_id_token(signed(signing_key, payload), client_id=CLIENT, nonce_hash=token_hash(flow.nonce),
                                    transport=transport_for(signing_key))
    assert result.issuer == "https://accounts.google.com" and result.subject == "synthetic-google-subject"
    assert result.email == "owner@example.com"


@pytest.mark.parametrize("change", ["issuer", "audience", "expiry", "nonce", "unverified_email", "subject", "authorized_party"])
def test_invalid_claims_fail_closed(signing_key, change):
    flow = google.new_flow()
    payload = claims(flow.nonce)
    if change == "issuer":
        payload["iss"] = "https://attacker.example"
    elif change == "audience":
        payload["aud"] = "another-client"
    elif change == "expiry":
        payload["exp"] = utcnow() - timedelta(seconds=1)
    elif change == "nonce":
        payload["nonce"] = "different-nonce"
    elif change == "unverified_email":
        payload["email_verified"] = False
    elif change == "subject":
        payload["sub"] = ""
    else:
        payload.update({"aud": [CLIENT, "another-client"], "azp": "another-client"})
    with pytest.raises(GluonsError) as error:
        google.verify_id_token(signed(signing_key, payload), client_id=CLIENT, nonce_hash=token_hash(flow.nonce),
                               transport=transport_for(signing_key))
    assert error.value.code == "GOOGLE_IDENTITY_INVALID"


def test_wrong_signature_unknown_key_and_duplicate_key_fail(signing_key):
    flow = google.new_flow()
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for token, transport in ((signed(other, claims(flow.nonce)), transport_for(signing_key)),
                             (signed(signing_key, claims(flow.nonce), kid="wrong"), transport_for(signing_key)),
                             (signed(signing_key, claims(flow.nonce)), transport_for(signing_key, duplicate=True))):
        with pytest.raises(GluonsError):
            google.verify_id_token(token, client_id=CLIENT, nonce_hash=token_hash(flow.nonce), transport=transport)


def test_unsigned_or_wrong_algorithm_never_fetches_provider_keys():
    flow = google.new_flow()
    def fail_if_called(request):
        raise AssertionError("An unsupported algorithm must not trigger a key request")
    for token in (jwt.encode(claims(flow.nonce), None, algorithm="none", headers={"kid": "key"}),
                  jwt.encode(claims(flow.nonce), "synthetic-key" * 3, algorithm="HS256", headers={"kid": "key"})):
        with pytest.raises(GluonsError) as error:
            google.verify_id_token(token, client_id=CLIENT, nonce_hash=token_hash(flow.nonce), transport=httpx.MockTransport(fail_if_called))
        assert error.value.code == "GOOGLE_IDENTITY_INVALID"


def test_key_service_failure_does_not_validate_a_token(signing_key):
    flow = google.new_flow()
    token = signed(signing_key, claims(flow.nonce))
    with pytest.raises(GluonsError) as error:
        google.verify_id_token(token, client_id=CLIENT, nonce_hash=token_hash(flow.nonce),
                               transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    assert error.value.code == "GOOGLE_IDENTITY_UNAVAILABLE"
