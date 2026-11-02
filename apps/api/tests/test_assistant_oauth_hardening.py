"""Findings from the security review of the assistant authorization server."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from gluons.authz import Principal
from gluons.core import GluonsError
from gluons.db.models import AuditEvent
from gluons.db.session import tenant_tx
from gluons.oauth import service
from helpers import World, add_member, make_user
from test_assistant_oauth import REDIRECT, VERIFIER, authorization


def _approved_code(world, client, pending) -> str:
    decision = service.consent(world.owner, pending["request_id"], approve=True)
    return parse_qs(urlsplit(decision["redirect_url"]).query)["code"][0]


def _exchange(client, code) -> dict:
    return service.exchange_code(
        client_id=client["client_id"], code=code, redirect_uri=REDIRECT, code_verifier=VERIFIER, resource_uri=service.resource()
    )


def test_replayed_code_revokes_the_tokens_it_issued(world):
    client, pending = authorization(world)
    code = _approved_code(world, client, pending)
    pair = _exchange(client, code)
    assert service.resolve_access_token(pair["access_token"]).tenant_id == world.tenant_id

    with pytest.raises(service.OAuthError) as error:
        _exchange(client, code)
    assert error.value.code == "invalid_grant"

    # The revocation must survive the error being raised, or it protects nothing.
    with pytest.raises(GluonsError):
        service.resolve_access_token(pair["access_token"])
    with pytest.raises(service.OAuthError):
        service.refresh_tokens(client_id=client["client_id"], refresh_token=pair["refresh_token"], resource_uri=service.resource())
    with tenant_tx(world.tenant_id) as session:
        events = session.execute(select(AuditEvent.event_type)).scalars().all()
    assert "assistant.code_replay_detected" in events


def test_a_different_client_cannot_trigger_revocation_with_a_known_code(world):
    client, pending = authorization(world)
    code = _approved_code(world, client, pending)
    pair = _exchange(client, code)
    other = service.register_client({"client_name": "Other", "redirect_uris": [REDIRECT]})
    with pytest.raises(service.OAuthError):
        _exchange(other, code)
    assert service.resolve_access_token(pair["access_token"]).tenant_id == world.tenant_id


def test_consent_names_the_business_and_flags_the_client_name_as_unverified(world):
    _, pending = authorization(world)
    preview = service.consent_preview(world.owner, pending["request_id"])
    assert preview["organization_name"] == "Acme Foods"
    assert preview["client_identity_verified"] is False
    assert "not verified" in preview["notice"]
    assert preview["redirect_uri"] == REDIRECT


def _bind(world, principal: Principal):
    client, pending = authorization(world)
    decision = service.consent(principal, pending["request_id"], approve=True)
    return client, parse_qs(urlsplit(decision["redirect_url"]).query)["code"][0]


def test_any_member_can_see_and_revoke_only_their_own_assistants(world):
    user_id, _ = make_user()
    viewer = add_member(world.tenant_id, user_id, "viewer")
    other_id, _ = make_user()
    colleague = add_member(world.tenant_id, other_id, "viewer")
    client, code = _bind(world, viewer)
    pair = _exchange(client, code)
    _bind(world, colleague)

    mine = service.list_bindings(viewer)
    assert [b["user_id"] for b in mine] == [user_id]
    theirs = next(b for b in service.list_bindings(world.owner) if b["user_id"] == other_id)
    with pytest.raises(GluonsError, match="not found"):
        service.revoke_binding(viewer, theirs["id"])

    service.revoke_binding(viewer, mine[0]["id"])
    with pytest.raises(GluonsError):
        service.resolve_access_token(pair["access_token"])


def test_owner_sees_every_assistant_in_the_business(world):
    user_id, _ = make_user()
    operator = add_member(world.tenant_id, user_id, "operator")
    _bind(world, operator)
    _bind(world, world.owner)
    assert {b["user_id"] for b in service.list_bindings(world.owner)} == {user_id, world.user_id}


def test_an_assistant_token_cannot_manage_assistants(world):
    client, pending = authorization(world)
    pair = _exchange(client, _approved_code(world, client, pending))
    assistant = service.resolve_access_token(pair["access_token"])
    with pytest.raises(GluonsError, match="Gluons Web"):
        service.list_bindings(assistant)
    with pytest.raises(GluonsError, match="Gluons Web"):
        service.revoke_binding(assistant, assistant.binding_id)


def test_binding_from_another_business_cannot_be_revoked(world):
    other = World.create("Other")
    _bind(other, other.owner)
    foreign = service.list_bindings(other.owner)[0]
    with pytest.raises(GluonsError, match="not found"):
        service.revoke_binding(world.owner, foreign["id"])
