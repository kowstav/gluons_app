"""Transactional outbox and owner notifications."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gluons import mail
from gluons.config import get_settings
from gluons.core import new_id, utcnow
from gluons.db.models import Membership, Notification, OutboxEvent, User
from gluons.db.session import system_tx

URGENT_WINDOW = timedelta(days=1)


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


def deliver_urgent(*, limit: int = 50) -> int:
    """Emails owners about work that cannot wait for them to open Gluons.

    One row at a time: a failure leaves only that row undelivered, and a row already sent is
    never repeated. Delivery is the platform's own account mail, not a customer message, so it
    stays outside the action gateway. The mail says what needs attention and holds no business
    data beyond the notification text the owner would see in the app.
    """
    settings = get_settings()
    with system_tx() as session:
        pending = session.execute(select(Notification.id).where(
            Notification.severity == "action", Notification.delivered_at.is_(None),
            Notification.created_at > utcnow() - URGENT_WINDOW).order_by(Notification.created_at).limit(limit)).scalars().all()
    sent = 0
    for notification_id in pending:
        with system_tx() as session:
            row = session.execute(select(Notification).where(Notification.id == notification_id, Notification.delivered_at.is_(None))
                                  .with_for_update(skip_locked=True)).scalar_one_or_none()
            if row is None:
                continue
            owners = session.execute(select(User.email).join(Membership, Membership.user_id == User.id).where(
                Membership.tenant_id == row.tenant_id, Membership.role == "owner", Membership.removed_at.is_(None))).scalars().all()
            link = f"{settings.public_web_url.rstrip('/')}{row.link or '/'}"
            for address in owners:
                mail.send(address, f"Gluons: {row.title}", f"{row.title}\n\n{row.body}\n\nOpen Gluons: {link}\n")
            row.delivered_at = utcnow()
            sent += 1
    return sent
