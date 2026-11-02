"""Provider-double contracts only: these tests never send a real email."""
import base64

import httpx
import pytest

from gluons.actions import ConnectorContext, ProviderError
from gluons.connectors.gmail import GmailSendConnector, SendEmail
from gluons.net import SideEffectBlocked, write_permit


@pytest.fixture(scope="session", autouse=True)
def database():
    """These pure HTTP-adapter contracts need no database or provider credentials."""
    yield


def test_gmail_send_requires_permit_and_verifies_independent_sent_readback():
    writes = []
    def provider(request):
        if request.method == "POST":
            import json
            writes.append(json.loads(request.content)["raw"])
            return httpx.Response(200, json={"id": "message-1", "threadId": "thread-1"})
        return httpx.Response(200, json={"id": "message-1", "raw": writes[0], "labelIds": ["SENT"], "threadId": "thread-1"})
    context = ConnectorContext("tenant", "connection", "gmail", "sandbox", "subject",
                               {"account_email": "owner@example.com"}, lambda: {"access_token": "test-double"}, httpx.MockTransport(provider))
    connector = GmailSendConnector()
    payload = SendEmail(to="customer@example.com", subject="Order confirmed", body="Verified order 123.")
    with pytest.raises(SideEffectBlocked):
        connector.execute(context, payload, "key")
    assert writes == []
    with write_permit("approved-test"):
        result = connector.execute(context, payload, "key")
    verified = connector.verify(context, payload, result)
    assert verified.matches is True and len(writes) == 1
    changed = payload.model_copy(update={"to": "wrong@example.com"})
    assert connector.verify(context, changed, result).matches is False
    assert b"Message-ID: <gluons-" in base64.urlsafe_b64decode(writes[0])


def test_gmail_empty_search_does_not_authorize_resend_after_timeout():
    context = ConnectorContext("tenant", "connection", "gmail", "sandbox", "subject", {},
                               lambda: {"access_token": "test-double"}, httpx.MockTransport(lambda r: httpx.Response(200, json={"messages": []})))
    with pytest.raises(ProviderError) as error:
        GmailSendConnector().lookup(context, SendEmail(to="customer@example.com", subject="Order", body="Body"), "key")
    assert error.value.kind == "ambiguous"
