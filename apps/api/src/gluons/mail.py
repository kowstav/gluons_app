"""Transactional email for account flows (verification, reset, invitations).

Local development writes messages to files instead of sending them. Hosted environments
must configure SMTP. This is separate from customer email, which goes through the Gmail
connector and the policy gateway.
"""
from __future__ import annotations

import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import unquote, urlsplit

from gluons.config import get_settings
from gluons.core import GluonsError, new_id, utcnow


def allowed_recipients() -> frozenset[str] | None:
    configured = get_settings().mail_allowed_recipients
    return frozenset(item.strip().lower() for item in configured.split(",") if item.strip()) if configured else None


def recipient_allowed(address: str) -> bool:
    recipients = allowed_recipients()
    return recipients is None or address.strip().lower() in recipients


def require_recipient(address: str) -> None:
    if not recipient_allowed(address):
        raise GluonsError("MAIL_RECIPIENT_NOT_ALLOWED", "This Gluons environment sends mail only to approved recipients.",
                         status=403)


def send(to: str, subject: str, body: str) -> None:
    require_recipient(to)
    settings = get_settings()
    if settings.mail_transport == "file":
        folder = Path(settings.mail_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{new_id('mail')}.json").write_text(
            json.dumps({"to": to, "subject": subject, "body": body, "at": utcnow().isoformat()}), encoding="utf-8"
        )
        return
    if not settings.smtp_url:
        raise RuntimeError("GLUONS_SMTP_URL is required when GLUONS_MAIL_TRANSPORT=smtp")
    url = urlsplit(settings.smtp_url)
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(url.hostname or "localhost", url.port or 587, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if url.username:
            smtp.login(unquote(url.username), unquote(url.password or ""))
        smtp.send_message(message)
