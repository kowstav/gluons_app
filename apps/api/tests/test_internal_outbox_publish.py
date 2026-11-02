"""Outbox publishing with ordering keys.

PROVIDER DOUBLE: a stand-in for google.cloud.pubsub_v1 that copies the real client's documented
rule, that an ordering key needs enable_message_ordering. It proves Gluons sets the option and
recovers from a failed publish, not that a message reached Pub/Sub.
"""
from __future__ import annotations

import sys
import types

import pytest

from gluons.db.models import OutboxEvent
from gluons.runtime import worker


class Result:
    def __init__(self, error=None):
        self.error = error

    def result(self, timeout=None):
        if self.error:
            raise self.error


class FakePublisher:
    instances: list[FakePublisher] = []

    def __init__(self, publisher_options=None):
        self.ordering = bool(publisher_options and publisher_options.enable_message_ordering)
        self.published: list[tuple[str, bytes, str]] = []
        self.resumed: list[tuple[str, str]] = []
        self.error: Exception | None = None
        FakePublisher.instances.append(self)

    def publish(self, topic, data, ordering_key=""):
        if ordering_key and not self.ordering:
            raise ValueError("Cannot publish a message with an ordering key when message ordering is not enabled.")
        self.published.append((topic, data, ordering_key))
        return Result(self.error)

    def resume_publish(self, topic, ordering_key):
        self.resumed.append((topic, ordering_key))


@pytest.fixture
def publisher(monkeypatch):
    FakePublisher.instances = []
    fake = types.ModuleType("google.cloud.pubsub_v1")
    fake.PublisherClient = FakePublisher
    fake.types = types.SimpleNamespace(PublisherOptions=lambda enable_message_ordering: types.SimpleNamespace(enable_message_ordering=enable_message_ordering))
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    cloud.pubsub_v1 = fake
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.pubsub_v1", fake)
    worker._publisher.cache_clear()
    yield worker._publisher()
    worker._publisher.cache_clear()


def event(tenant_id):
    return OutboxEvent(id="evt_1", tenant_id=tenant_id, topic="state", payload={"a": 1})


def test_tenant_events_publish_in_order(publisher):
    worker._publish("projects/p/topics/t", event("org_1"))
    assert publisher.published[0][2] == "org_1"


def test_system_events_have_no_ordering_key(publisher):
    worker._publish("projects/p/topics/t", event(None))
    assert publisher.published[0][2] == ""


def test_client_is_reused(publisher):
    worker._publish("t", event("org_1"))
    worker._publish("t", event("org_1"))
    assert len(FakePublisher.instances) == 1


def test_failed_publish_unblocks_the_tenant_and_still_raises(publisher):
    publisher.error = RuntimeError("deadline")
    with pytest.raises(RuntimeError):
        worker._publish("t", event("org_1"))
    assert publisher.resumed == [("t", "org_1")]
