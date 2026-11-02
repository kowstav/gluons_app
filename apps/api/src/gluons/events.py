"""Transactional outbox and owner notifications."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from gluons.core import new_id
from gluons.db.models import Notification, OutboxEvent


def emit(session: Session, tenant_id: str | None, topic: str, payload: dict[str, Any]) -> None:
    """Must share the transaction of the state change, so neither happens without the other."""
    session.add(OutboxEvent(id=new_id("evt"), tenant_id=tenant_id, topic=topic, payload=payload))


def notify(
    session: Session,
    tenant_id: str,
    *,
    kind: str,
    title: str,
    body: str = "",
    severity: str = "info",
    link: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    session.add(
        Notification(
            id=new_id("ntf"),
            tenant_id=tenant_id,
            kind=kind,
            title=title,
            body=body,
            severity=severity,
            link=link,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )
