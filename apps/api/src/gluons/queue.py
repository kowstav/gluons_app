"""Durable job queue on Postgres. This is the execution authority for all background work.

Delivery is at-least-once, so every handler must be idempotent. Cloud Tasks, when
configured, only wakes workers sooner; a lost wake-up delays work but never loses it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gluons.core import new_id, utcnow
from gluons.db.models import Job
from gluons.db.session import system_tx

BACKOFF_BASE_SECONDS = 5
BACKOFF_MAX_SECONDS = 900


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    tenant_id: str | None,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 6,
) -> None:
    """Joins the caller's transaction. A dedupe key collapses identical active jobs."""
    stmt = insert(Job).values(
        id=new_id("job"),
        tenant_id=tenant_id,
        kind=kind,
        payload=payload,
        run_at=run_at or utcnow(),
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    session.execute(stmt.on_conflict_do_nothing())


def claim(worker_id: str, *, limit: int = 5, lease_seconds: int = 120) -> list[Job]:
    now = utcnow()
    with system_tx() as session:
        # Expired leases mean a worker died mid-job: reclaim them, counted as an attempt.
        session.execute(
            text(
                "update jobs set status = 'queued', leased_by = null, lease_until = null "
                "where status = 'leased' and lease_until < :now"
            ),
            {"now": now},
        )
        rows = (
            session.execute(
                select(Job)
                .where(Job.status == "queued", Job.run_at <= now)
                .order_by(Job.run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        for job in rows:
            job.status = "leased"
            job.leased_by = worker_id
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.attempts += 1
        return list(rows)


def complete(job_id: str) -> None:
    with system_tx() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = "done"
            job.finished_at = utcnow()
            job.leased_by = None
            job.lease_until = None


def fail(job_id: str, error: str, *, retry_after: float | None = None) -> str:
    """Returns the job's new status: 'queued' to retry, or 'dead' for the operator queue."""
    with system_tx() as session:
        job = session.get(Job, job_id)
        if job is None:
            return "dead"
        job.last_error = error[:2000]
        job.leased_by = None
        job.lease_until = None
        if job.attempts >= job.max_attempts:
            job.status = "dead"
            job.finished_at = utcnow()
            return "dead"
        delay = retry_after or min(BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1)), BACKOFF_MAX_SECONDS)
        job.status = "queued"
        job.run_at = utcnow() + timedelta(seconds=delay)
        return "queued"
