"""Background worker. Runs independently of any web request or assistant conversation."""
from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Callable
from datetime import timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from gluons import approvals, events, gateway, queue
from gluons.config import get_settings
from gluons.core import new_id, utcnow
from gluons.db.models import Automation, Incident, Job, OutboxEvent
from gluons.db.session import system_tx, tenant_tx
from gluons.observability import bind, configure_logging
from gluons.runtime import engine, lifecycle

logger = logging.getLogger("gluons.worker")

Handler = Callable[[Job, str], None]
HANDLERS: dict[str, Handler] = {}


class RetryLater(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


@handler("run.advance")
def _run_advance(job: Job, worker_id: str) -> None:
    result = engine.advance(job.tenant_id, job.payload["run_id"], worker_id)
    if result.status == "busy_or_finished":
        return


@handler("gmail.sync")
def _gmail_sync(job: Job, worker_id: str) -> None:
    from gluons.connections.gmail_sync import sync_connection
    sync_connection(job.tenant_id, job.payload["connection_id"])


@handler("gmail.ingest")
def _gmail_ingest(job: Job, worker_id: str) -> None:
    from gluons.connections.gmail_sync import ingest_message
    ingest_message(job.tenant_id, job.payload["connection_id"], job.payload["message_id"])


@handler("gmail.maintain")
def _gmail_maintain(job: Job, worker_id: str) -> None:
    from gluons.connections.gmail_sync import maintain
    maintain()


@handler("notifications.deliver")
def _deliver_notifications(job: Job, worker_id: str) -> None:
    events.deliver_urgent()


@handler("connections.health")
def _connection_health(job: Job, worker_id: str) -> None:
    from gluons.connections.health import check_all
    check_all()


@handler("housekeeping.purge")
def _housekeeping(job: Job, worker_id: str) -> None:
    """Rows anyone can create without signing in: consent requests, installs and rate counts."""
    from gluons import ratelimit
    from gluons.commerce.install import purge_stale as purge_installs
    from gluons.oauth.service import purge_stale as purge_oauth
    purge_oauth()
    purge_installs()
    ratelimit.purge()


@handler("organizations.purge")
def _purge_organizations(job: Job, worker_id: str) -> None:
    from gluons.deletion import purge_due
    purge_due()


@handler("oauth.purge")
def _purge_oauth(job: Job, worker_id: str) -> None:
    """Kept so jobs queued before the housekeeping rename still run."""
    from gluons.oauth.service import purge_stale
    purge_stale()


@handler("whatsapp.process")
def _whatsapp_process(job: Job, worker_id: str) -> None:
    from gluons.whatsapp.service import process_event
    process_event(job.tenant_id, job.payload["inbox_id"])


@handler("automation.finalize_cancel")
def _finalize_cancel(job: Job, worker_id: str) -> None:
    if not lifecycle.finalize_cancel(job.tenant_id, job.payload["automation_id"]):
        raise RetryLater(5)


@handler("approvals.expire")
def _expire_approvals(job: Job, worker_id: str) -> None:
    with system_tx() as session:
        approvals.expire_due(session)


@handler("reconcile.sweep")
def _reconcile_sweep(job: Job, worker_id: str) -> None:
    """Surfaces writes left in doubt, so a stuck action cannot stay invisible."""
    with system_tx() as session:
        tenants = session.execute(select(Automation.tenant_id).distinct()).scalars().all()
    for tenant_id in tenants:
        stale = gateway.reconcile_stale(tenant_id)
        if not stale:
            continue
        with tenant_tx(tenant_id) as session:
            for item in stale:
                seen = session.execute(
                    select(Incident.id).where(
                        Incident.kind == "stale_action",
                        Incident.diagnosis["receipt_id"].as_string() == item["receipt_id"],
                    )
                ).first()
                if seen is None:
                    session.add(
                        Incident(
                            id=new_id("inc"),
                            tenant_id=tenant_id,
                            kind="stale_action",
                            severity="critical",
                            run_id=item["run_id"],
                            summary=f"{item['action']} has been unresolved for too long.",
                            diagnosis=item,
                        )
                    )


@handler("outbox.publish")
def _publish_outbox(job: Job, worker_id: str) -> None:
    settings = get_settings()
    with system_tx() as session:
        rows = (
            session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at)
                .limit(200)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        for event in rows:
            event.attempts += 1
            if settings.pubsub_events_topic:
                _publish(settings.pubsub_events_topic, event)
            event.published_at = utcnow()


@lru_cache
def _publisher() -> Any:
    from google.cloud import pubsub_v1  # imported lazily: optional 'gcp' extra

    # The client refuses an ordering key unless ordering is switched on here.
    return pubsub_v1.PublisherClient(publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True))


def _publish(topic: str, event: OutboxEvent) -> None:
    import json

    publisher = _publisher()
    ordering_key = event.tenant_id or ""
    try:
        publisher.publish(
            topic,
            json.dumps({"id": event.id, "topic": event.topic, "payload": event.payload}).encode(),
            ordering_key=ordering_key,
        ).result(timeout=10)
    except Exception:
        # A failed publish pauses its ordering key until resumed, which would block the tenant.
        if ordering_key:
            publisher.resume_publish(topic, ordering_key)
        raise


def _dead_letter(job: Job, error: str) -> None:
    if job.tenant_id is None:
        logger.error("job dead-lettered", extra={"job_id": job.id, "kind": job.kind})
        return
    with tenant_tx(job.tenant_id) as session:
        session.add(
            Incident(
                id=new_id("inc"),
                tenant_id=job.tenant_id,
                kind="dead_letter",
                severity="critical",
                run_id=job.payload.get("run_id"),
                summary=f"Background job {job.kind} stopped after {job.attempts} attempts.",
                diagnosis={"job_id": job.id, "kind": job.kind, "error": error[:2000]},
            )
        )


def _handle(job: Job, worker_id: str) -> None:
    fn = HANDLERS.get(job.kind)
    if fn is None:
        queue.fail(job.id, f"no handler for {job.kind}")
        return
    try:
        fn(job, worker_id)
    except RetryLater as retry:
        queue.fail(job.id, "not ready", retry_after=retry.retry_after)
        return
    except Exception as exc:  # noqa: BLE001 - a failing job must never stop the worker
        logger.exception("job failed", extra={"job_id": job.id, "kind": job.kind})
        if queue.fail(job.id, f"{type(exc).__name__}: {exc}") == "dead":
            _dead_letter(job, str(exc))
        return
    queue.complete(job.id)


def run_once(worker_id: str | None = None, *, limit: int = 10) -> int:
    settings = get_settings()
    worker = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    jobs = queue.claim(worker, limit=limit, lease_seconds=settings.worker_lease_seconds)
    for job in jobs:
        with bind(job_id=job.id, job_kind=job.kind, tenant_id=job.tenant_id):
            _handle(job, worker)
    return len(jobs)


def drain(worker_id: str = "drain", *, max_rounds: int = 100) -> int:
    """Runs until nothing is due. Used by tests and one-shot Cloud Run jobs."""
    total = 0
    for _ in range(max_rounds):
        count = run_once(worker_id)
        total += count
        if count == 0:
            break
    return total


PERIODIC: dict[str, int] = {"approvals.expire": 60, "reconcile.sweep": 300, "outbox.publish": 15, "gmail.maintain": 300, "notifications.deliver": 60, "housekeeping.purge": 3600, "connections.health": 21600, "organizations.purge": 3600}


def scheduler_tick() -> None:
    now = utcnow()
    with system_tx() as session:
        for kind, seconds in PERIODIC.items():
            bucket = int(now.timestamp()) // seconds
            queue.enqueue(session, kind, {}, tenant_id=None, dedupe_key=f"periodic:{kind}:{bucket}")
        for automation in session.execute(
            select(Automation).where(
                Automation.status.in_(("active", "trial_shadow", "trial_replay")),
                Automation.trigger["kind"].as_string() == "interval",
            )
        ).scalars():
            _enqueue_interval_run(automation, now)


def _enqueue_interval_run(automation: Automation, now: Any) -> None:
    seconds = int(automation.trigger.get("seconds", 3600))
    bucket = int(now.timestamp()) // seconds
    lifecycle.submit_event(
        automation.tenant_id,
        automation.id,
        source="schedule",
        source_event_id=f"schedule:{bucket}",
        payload={"scheduled_at": now.isoformat()},
        actor="scheduler",
    )


def main() -> None:
    configure_logging()
    settings = get_settings()
    last_tick = utcnow() - timedelta(seconds=60)
    while True:
        if utcnow() - last_tick >= timedelta(seconds=15):
            scheduler_tick()
            last_tick = utcnow()
        if run_once() == 0:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
