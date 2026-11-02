"""OIDC verification on the worker wake endpoint.

PROVIDER DOUBLE: Google's JWKS endpoint is replaced by a real local HTTP server holding a
key generated here. Signature checking, key lookup and claim validation are the real code,
but this proves Gluons' handling only. A real Cloud Tasks token has not been tested.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from gluons import queue
from gluons.api.app import create_app
from gluons.api.routes import internal
from gluons.config import get_settings
from gluons.core import new_id
from gluons.db.models import Job
from gluons.db.session import system_tx

AUDIENCE = "https://gluons-test-worker.gluons.internal/wake"
CALLER = "gluons-scheduler@gluons-test.iam.gserviceaccount.com"
WAKE = "/internal/worker/wake"
KID = "test-key-1"


def _uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks_url(signing_key):
    numbers = signing_key.public_key().public_numbers()
    body = json.dumps(
        {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID, "n": _uint(numbers.n), "e": _uint(numbers.e)}]}
    ).encode()

    class Keys(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Keys)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/certs"
    server.shutdown()


@pytest.fixture
def configured(monkeypatch, jwks_url):
    monkeypatch.setenv("GLUONS_WORKER_WAKE_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(get_settings(), "worker_service_account", CALLER)
    monkeypatch.setattr(internal, "google_keys", lambda: jwt.PyJWKClient(jwks_url))


@pytest.fixture
def client(database):
    return TestClient(create_app())


def claims(**changes):
    now = int(time.time())
    base = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "email": CALLER,
        "email_verified": True,
        "iat": now,
        "exp": now + 300,
        "sub": "1234567890",
    }
    base.update(changes)
    return {key: value for key, value in base.items() if value is not None}


def token(key, payload=None, *, kid=KID, algorithm="RS256"):
    return jwt.encode(payload or claims(), key, algorithm=algorithm, headers={"kid": kid})


def bearer(value):
    return {"Authorization": f"Bearer {value}"}


def queued_job() -> str:
    job_id_key = new_id("wake")
    with system_tx() as session:
        queue.enqueue(session, "approvals.expire", {}, tenant_id=None, dedupe_key=job_id_key)
    return job_id_key


def job_status(dedupe_key: str) -> str:
    with system_tx() as session:
        return session.execute(select(Job.status).where(Job.dedupe_key == dedupe_key)).scalar_one()


def test_valid_token_drains_due_work(client, configured, signing_key):
    job = queued_job()
    response = client.post(WAKE, headers=bearer(token(signing_key)))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok" and response.json()["processed"] >= 1
    assert job_status(job) == "done"


def test_bare_issuer_form_is_accepted(client, configured, signing_key):
    assert client.post(WAKE, headers=bearer(token(signing_key, claims(iss="accounts.google.com")))).status_code == 200


def test_unauthenticated_request_does_no_work(client, configured):
    job = queued_job()
    response = client.post(WAKE)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert job_status(job) == "queued"


def test_cloud_tasks_headers_prove_nothing(client, configured):
    headers = {"X-CloudTasks-QueueName": "gluons-staging-model-work", "X-CloudTasks-TaskName": "t", "User-Agent": "Google-Cloud-Tasks"}
    assert client.post(WAKE, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "authorization",
    ["Bearer", "Bearer ", "Bearer not.a.jwt", "Basic dXNlcjpwYXNz", "garbage", f"token {AUDIENCE}"],
)
def test_malformed_credentials_are_rejected(client, configured, authorization):
    assert client.post(WAKE, headers={"Authorization": authorization}).status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "https://gluons-production-worker.gluons.internal/wake"},
        {"aud": None},
        {"iss": "https://evil.example"},
        {"iss": None},
        {"email": "attacker@evil.iam.gserviceaccount.com"},
        {"email": None},
        {"email_verified": False},
        {"email_verified": None},
        {"exp": int(time.time()) - 3600},
        {"exp": None},
        {"iat": None},
    ],
    ids=lambda c: "-".join(f"{k}={'absent' if v is None else 'bad'}" for k, v in c.items()),
)
def test_wrong_or_missing_claims_are_rejected(client, configured, signing_key, changes):
    job = queued_job()
    assert client.post(WAKE, headers=bearer(token(signing_key, claims(**changes)))).status_code == 401
    assert job_status(job) == "queued"


def test_forged_signature_under_a_known_key_id_is_rejected(client, configured):
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert client.post(WAKE, headers=bearer(token(forged))).status_code == 401


def test_unknown_key_id_is_rejected(client, configured, signing_key):
    assert client.post(WAKE, headers=bearer(token(signing_key, kid="not-a-google-key"))).status_code == 401


def test_stripped_signature_is_rejected(client, configured, signing_key):
    head, payload, _ = token(signing_key).split(".")
    assert client.post(WAKE, headers=bearer(f"{head}.{payload}.SIGNATURE_REMOVED_BY_GOOGLE")).status_code == 401


def test_unsigned_token_is_rejected(client, configured):
    unsigned = jwt.encode(claims(), None, algorithm="none", headers={"kid": KID})
    assert client.post(WAKE, headers=bearer(unsigned)).status_code == 401


def test_hmac_token_is_rejected(client, configured):
    """Algorithm confusion: an attacker signs with a shared secret and hopes it is accepted."""
    forged = jwt.encode(claims(), "x" * 32, algorithm="HS256", headers={"kid": KID})
    assert client.post(WAKE, headers=bearer(forged)).status_code == 401


def test_unconfigured_service_rejects_even_a_valid_token(client, monkeypatch, jwks_url, signing_key):
    """This is the state of the public API, MCP and webhooks services, which share the route."""
    monkeypatch.delenv("GLUONS_WORKER_WAKE_AUDIENCE", raising=False)
    monkeypatch.setattr(get_settings(), "worker_wake_url", None)
    monkeypatch.setattr(get_settings(), "worker_service_account", None)
    monkeypatch.setattr(internal, "google_keys", lambda: jwt.PyJWKClient(jwks_url))
    assert client.post(WAKE, headers=bearer(token(signing_key))).status_code == 401


def test_audience_falls_back_to_the_wake_url(client, monkeypatch, jwks_url, signing_key):
    monkeypatch.delenv("GLUONS_WORKER_WAKE_AUDIENCE", raising=False)
    monkeypatch.setattr(get_settings(), "worker_wake_url", "https://worker.example/wake")
    monkeypatch.setattr(get_settings(), "worker_service_account", CALLER)
    monkeypatch.setattr(internal, "google_keys", lambda: jwt.PyJWKClient(jwks_url))
    good = token(signing_key, claims(aud="https://worker.example/wake"))
    assert client.post(WAKE, headers=bearer(good)).status_code == 200


def test_key_lookup_failure_refuses_and_can_be_retried(client, configured, monkeypatch, signing_key):
    def unavailable():
        raise jwt.PyJWKClientConnectionError("network down")

    monkeypatch.setattr(internal, "google_keys", unavailable)
    job = queued_job()
    assert client.post(WAKE, headers=bearer(token(signing_key))).status_code == 503
    assert job_status(job) == "queued"


def test_request_body_is_never_read(client, configured, signing_key):
    """A wake cannot inject work: jobs come only from Postgres."""
    job = queued_job()
    response = client.post(WAKE, headers=bearer(token(signing_key)), content=b"\xff not json {", )
    assert response.status_code == 200
    assert job_status(job) == "done"


def test_route_is_hidden_from_the_public_schema(client):
    assert WAKE not in client.get("/openapi.json").json()["paths"]
