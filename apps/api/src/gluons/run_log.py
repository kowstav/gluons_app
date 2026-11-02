"""Immutable ordered event stream for a run (blueprint doc 02, envelope from doc 12)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from gluons.core import new_id
from gluons.db.models import RunEvent

DEFAULT_ENGINE = "python-actions"
ENGINE_VERSION = "1"


def append(
    session: Session,
    tenant_id: str,
    run_id: str,
    event_type: str,
    data: dict[str, Any] | None = None,
    *,
    engine: str = DEFAULT_ENGINE,
    engine_version: str = ENGINE_VERSION,
) -> None:
    session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"run_events:{run_id}"})
    seq = session.execute(
        select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id)
    ).scalar_one()
    session.add(
        RunEvent(
            id=new_id("rev"),
            tenant_id=tenant_id,
            run_id=run_id,
            seq=seq + 1,
            event_type=event_type,
            engine=engine,
            engine_version=engine_version,
            data=data or {},
        )
    )
    session.flush()
