"""Transactional email for account flows (verification, reset, invitations).

Local development writes messages to files instead of sending them. Hosted environments
must configure SMTP. This is separate from customer email, which goes through the Gmail
connector and the policy gateway.
"""
from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

from gluons.config import get_settings
from gluons.core import new_id, utcnow


def send(to: str, subject: str, body: str) -> None:
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
        smtp.starttls()
        if url.username:
            smtp.login(url.username, url.password or "")
        smtp.send_message(message)
