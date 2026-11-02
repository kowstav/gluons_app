"""Gmail actions. The action contract is defined here; the connector registers below it."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr

from gluons.actions import ActionSpec, register_action

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class SendEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: EmailStr
    subject: str
    body: str
    thread_id: str | None = None


register_action(
    ActionSpec(
        name="email.send",
        permission="send",
        risk_tier="R2",
        capability="email",
        input_model=SendEmail,
        description="Send an email to a customer",
        required_scopes={"gmail": (GMAIL_SEND_SCOPE,)},
        irreversible=True,
        resources=lambda p: {"recipient": str(p.to)},
    )
)
