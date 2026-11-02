"""Embedded Signup against an explicitly labelled Graph provider double; no real Meta account."""
from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select

from gluons.config import get_settings
from gluons.core import GluonsError, hmac_sha256_hex, new_id
from gluons.db.models import AuditEvent, Connection, ConnectionSession, VaultSecret, WebhookInbox
from gluons.db.session import tenant_tx
from gluons.net import SideEffectBlocked
from gluons.whatsapp import service

PHONE = ""
ACCOUNT = "2109876543"
TOKEN = "double-business-token"


@pytest.fixture(autouse=True)
def fresh_number():
    """A number links globally across businesses, so each test needs its own."""
    global PHONE
    PHONE = str(int(new_id("id").split("_")[1], 16))[:18]


@pytest.fixture
def meta(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "4242424242")
    monkeypatch.setattr(settings, "meta_app_secret", "double-app-secret")
    monkeypatch.setattr(settings, "meta_config_id", "5151515151")
    monkeypatch.setattr(settings, "meta_webhook_verify_token", "double-verify")


@pytest.fixture
def browser(world):
    return replace(world.owner, session_id=world.owner.session_id or "ses_browser")


class Graph:
    """Provider double. It proves Gluons' handling only, never that Meta behaves this way."""

    def __init__(self, *, fail_on: str | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/", 2)[2]
        self.calls.append((request.method, path))
        if self.fail_on and path.endswith(self.fail_on):
            return httpx.Response(400, json={"error": {"code": 100}})
        if path == "oauth/access_token":
            assert request.url.params["client_secret"] == "double-app-secret"
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "bearer"})
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if path.endswith("/subscribed_apps") or path.endswith("/register"):
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"id": PHONE, "display_phone_number": "+91 98765 43210", "verified_name": "Acme Foods"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def finished(**overrides):
    return {"event": "FINISH", "code": "short-lived-code", "messaging_account_ids": [ACCOUNT], "phone_number_id": PHONE, "business_id": "77", **overrides}


def begin(principal):
    return service.begin_session(principal, capabilities=["read_messages", "send_replies"])


def test_without_configuration_the_owner_is_told_the_operator_path(browser):
    result = begin(browser)
    assert result["status"] == "needs_operator_verification"
    assert "whatsapp_setup" not in result and "not configured" in result["next_step"]


def test_setup_details_hold_no_secret_and_use_signup_v5(meta, browser):
    result = begin(browser)
    assert result["status"] == "pending"
    assert result["whatsapp_setup"]["login_options"]["extras"] == {"version": "v5"}
    assert result["whatsapp_setup"]["init"]["version"] == get_settings().meta_graph_version
    assert "double-app-secret" not in json.dumps(result) and "double-verify" not in json.dumps(result)


def test_signup_exchanges_subscribes_registers_then_awaits_owner_confirmation(meta, browser, world):
    graph = Graph()
    session_id = begin(browser)["session_id"]
    result = service.complete_signup(browser, session_id, finished(), transport=graph.transport())
    assert result["status"] == "authorized" and result["account_confirmed"] is False
    assert result["account_display"] == "+91 98765 43210"
    assert graph.calls[:3] == [("GET", "oauth/access_token"), ("POST", f"{ACCOUNT}/subscribed_apps"), ("POST", f"{PHONE}/register")]
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, result["connection_id"])
        assert connection.account_confirmed_at is None
        assert connection.metadata_json["account_ids"] == [ACCOUNT]
        assert connection.metadata_json["client_payment_method"] == "unknown"
        assert TOKEN not in "".join(str(row) for row in session.execute(select(VaultSecret.ciphertext)).scalars())
        assert "double" not in json.dumps(session.execute(select(AuditEvent.data)).scalars().all())


def test_coexistence_number_is_not_registered(meta, browser):
    graph = Graph()
    service.complete_signup(browser, begin(browser)["session_id"], finished(event="FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"), transport=graph.transport())
    assert not any(path.endswith("/register") for _, path in graph.calls)


def test_a_session_works_once_and_only_in_the_browser_that_opened_it(meta, browser):
    session_id = begin(browser)["session_id"]
    with pytest.raises(GluonsError, match="another session"):
        service.complete_signup(replace(browser, session_id="ses_other"), session_id, finished(), transport=Graph().transport())
    service.complete_signup(browser, session_id, finished(), transport=Graph().transport())
    with pytest.raises(GluonsError, match="already used"):
        service.complete_signup(browser, session_id, finished(), transport=Graph().transport())


def test_an_assistant_token_cannot_finish_signup(meta, browser):
    session_id = begin(browser)["session_id"]
    with pytest.raises(GluonsError):
        service.complete_signup(replace(browser, kind="assistant"), session_id, finished(), transport=Graph().transport())


def test_a_refused_step_leaves_no_connection_and_spends_the_session(meta, browser, world):
    session_id = begin(browser)["session_id"]
    with pytest.raises(GluonsError, match="No connection"):
        service.complete_signup(browser, session_id, finished(), transport=Graph(fail_on="/register").transport())
    with tenant_tx(world.tenant_id) as session:
        assert session.execute(select(Connection)).first() is None
        assert session.get(ConnectionSession, session_id).status == "failed"


def test_cancelled_signup_is_recorded(meta, browser, world):
    session_id = begin(browser)["session_id"]
    with pytest.raises(GluonsError, match="not finished"):
        service.complete_signup(browser, session_id, {"event": "CANCEL"})
    with tenant_tx(world.tenant_id) as session:
        assert session.get(ConnectionSession, session_id).status == "failed"


@pytest.mark.parametrize("change", [{"phone_number_id": "../me"}, {"messaging_account_ids": ["1/2"]}, {"messaging_account_ids": []}, {"code": ""}, {"pin": "12"}])
def test_malformed_signup_output_is_refused_before_any_call(meta, browser, change):
    graph = Graph()
    with pytest.raises(GluonsError):
        service.complete_signup(browser, begin(browser)["session_id"], finished(**change), transport=graph.transport())
    assert graph.calls == []


def test_one_number_cannot_serve_two_businesses(meta, browser):
    from helpers import World

    service.complete_signup(browser, begin(browser)["session_id"], finished(), transport=Graph().transport())
    other = World.create("Other Foods")
    rival = replace(other.owner, session_id="ses_rival")
    with pytest.raises(GluonsError, match="Disconnect"):
        service.complete_signup(rival, begin(rival)["session_id"], finished(), transport=Graph().transport())


def test_webhook_accepts_any_account_in_the_signup_and_no_other(meta, browser, world):
    from gluons.core import utcnow

    connection_id = service.complete_signup(browser, begin(browser)["session_id"],
                                            finished(messaging_account_ids=[ACCOUNT, "3210987654"]), transport=Graph().transport())["connection_id"]
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, connection_id).account_confirmed_at = utcnow()

    def deliver(account: str, event_id: str) -> dict:
        raw = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": account, "changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": PHONE}, "messages": [{"id": event_id, "from": "15550100", "timestamp": str(int(utcnow().timestamp())),
                                                                  "type": "text", "text": {"body": "hello"}}]}}]}]}).encode()
        return service.receive_webhook(raw, "sha256=" + hmac_sha256_hex(b"double-app-secret", raw))

    assert deliver("3210987654", "wamid.one")["accepted"] == 1
    assert deliver("9999999999", "wamid.two")["accepted"] == 0
    with tenant_tx(world.tenant_id) as session:
        assert len(session.execute(select(WebhookInbox)).all()) == 1


def test_signup_writes_are_impossible_without_the_onboarding_permit():
    from gluons.net import GuardedClient

    client = GuardedClient(("graph.facebook.com",), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(SideEffectBlocked):
        client.post("https://graph.facebook.com/v26.0/1/subscribed_apps")
    client.close()
