"""Native Gluons chat for people whose assistant cannot use tools (doc 13 Path B).

Rules route a message onto the same service functions REST and MCP call, so this works with
no model configured. A model, when one is evaluated and permitted, may only pick from
`INTENTS` for a message the rules could not place. It never sees a permission and its choice
gets no extra rights: the service function still checks the principal, and every write waits
for an explicit confirmation turn carrying a signed, short-lived token.

Nothing is stored. The token holds the pending action, so a lost conversation loses nothing.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, get_args

import httpx
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from gluons import approvals, billing, model_gateway, tasks
from gluons.authz import Principal
from gluons.config import get_settings
from gluons.core import GluonsError, canonical_json, constant_time_equals, hmac_sha256_hex, utcnow, validation_failed
from gluons.db.models import Approval, Organization
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from gluons.skills import catalog

MODEL_PURPOSE = "native_chat_intent"
CONFIRMATION_TTL = timedelta(minutes=5)
CUSTOMER_MESSAGE_CATEGORY = "send"  # ActionSpec.permission of every customer-message action.
MAX_MESSAGE = 2000
TERMINAL = ("cancelled", "completed", "cancel_requested")

Intent = Literal["list_tasks", "task_status", "pause_task", "find_automations", "list_approvals", "spending",
                 "stop_customer_messages", "unknown"]
INTENTS: tuple[str, ...] = get_args(Intent)
WRITE_INTENTS = frozenset({"pause_task", "stop_customer_messages"})

_NEGATED = re.compile(r"\b(don'?t|do not|never|without|not)\b", re.I)
_YES = re.compile(r"^\s*(yes|yep|confirm|confirmed|do it|go ahead|proceed)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|cancel|stop|never ?mind|don'?t)\b", re.I)
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("stop_customer_messages", re.compile(
        r"\b(stop|halt|pause|block|turn off)\b[^.?!]*\b(customer|outgoing|outbound|all)\b[^.?!]*"
        r"\b(messages?|emails?|texts?|communications?|outreach)\b|\bstop sending\b", re.I)),
    ("pause_task", re.compile(r"\b(?:pause|hold|suspend|stop)\s+(?:the\s+|my\s+|task\s+|automation\s+)*(?P<ref>[^.?!]*)", re.I)),
    ("list_approvals", re.compile(r"\bapprovals?\b|\bapprove\b|\bwaiting (?:on|for) me\b|\bneeds? my (?:decision|sign[- ]?off)\b", re.I)),
    ("spending", re.compile(r"\b(spend|spent|spending|costs?|bill|billing|budget|how much)\b", re.I)),
    ("find_automations", re.compile(r"\bautomate\b|\bfind\b[^.?!]*\bautomations?\b|\bopportunit(y|ies)\b|\bfind ways\b", re.I)),
    ("task_status", re.compile(r"\b(?:status|progress|how(?:'s| is| are))(?:\s+(?:of|on|for|is|are|the|my))*\s+(?P<ref>[^.?!]+)", re.I)),
    ("list_tasks", re.compile(r"\b(list|show|what|which)\b[^.?!]*\b(tasks?|automations?|running)\b|\bwhat'?s running\b", re.I)),
)
_TRAILING = re.compile(r"\s+(?:please|now|task|automation|doing)\s*$", re.I)


class IntentChoice(BaseModel):
    """All a model may return. Anything else fails validation and falls back to the rules."""

    model_config = ConfigDict(extra="forbid")
    intent: Intent
    task: str | None = None


@dataclass(frozen=True)
class Route:
    intent: str
    reference: str | None
    routed_by: str  # rules | model | none


def _clean_reference(value: str | None) -> str | None:
    text = (value or "").strip().strip("\"'")
    while True:
        shorter = _TRAILING.sub("", text)
        if shorter == text:
            break
        text = shorter
    return text or None


def route_by_rules(message: str) -> Route:
    for intent, pattern in _RULES:
        match = pattern.search(message)
        if match is None:
            continue
        if intent in WRITE_INTENTS and _NEGATED.search(message):
            return Route("unknown", None, "rules")
        return Route(intent, _clean_reference(match.groupdict().get("ref")), "rules")
    return Route("unknown", None, "none")


def _route_by_model(principal: Principal, message: str, transport: httpx.BaseTransport | None) -> Route | None:
    try:
        if not model_gateway.is_configured(principal.tenant_id, MODEL_PURPOSE):
            return None
        result = model_gateway.complete_structured(
            tenant_id=principal.tenant_id, purpose=MODEL_PURPOSE, schema=IntentChoice, user=message, transport=transport,
            system="Pick the one intent that matches the owner's request, or unknown. You decide no permissions and take no action.",
        )
    except GluonsError:
        return None  # No configured model, an exhausted budget or bad output: the rules still answer.
    choice = result.value
    if choice.intent == "unknown":
        return None
    return Route(choice.intent, _clean_reference(choice.task), "model")


def _key() -> bytes:
    settings = get_settings()
    return settings.key_bytes(settings.signing_key_b64)


def _binding(principal: Principal) -> dict[str, Any]:
    return {"tenant": principal.tenant_id, "actor": principal.actor_id, "session": principal.session_id}


def _seal(principal: Principal, intent: str, arguments: dict[str, Any]) -> dict[str, str]:
    expires = utcnow() + CONFIRMATION_TTL
    body = canonical_json({**_binding(principal), "intent": intent, "arguments": arguments, "expires": expires})
    signature = hmac_sha256_hex(_key(), f"chat-confirmation:{body}")
    token = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {"token": f"{token}.{signature}", "expires_at": expires.isoformat()}


def _open(principal: Principal, token: str) -> dict[str, Any]:
    import json
    from datetime import datetime

    refused = GluonsError("CONFIRMATION_INVALID", "This confirmation does not apply. Ask again.", status=403)
    encoded, _, signature = token.partition(".")
    try:
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        if not constant_time_equals(signature, hmac_sha256_hex(_key(), f"chat-confirmation:{body}")):
            raise refused
        pending = json.loads(body)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise refused from exc
    if any(pending.get(field) != value for field, value in _binding(principal).items()):
        raise refused
    if datetime.fromisoformat(pending["expires"]) <= utcnow():
        raise GluonsError("CONFIRMATION_EXPIRED", "This confirmation expired. Ask again.", status=409)
    return pending


def _reply(route: Route, status: str, reply: str, *, data: Any = None, confirmation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"intent": route.intent, "routed_by": route.routed_by, "status": status,
            "reply": f"Intent {route.intent}: {reply}", "data": data, "confirmation": confirmation}


def _resolve_task(principal: Principal, route: Route, *, live_only: bool) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Deterministic lookup: an exact id or an unambiguous name, never a guess."""
    rows = lifecycle.list_all(principal)
    if live_only:
        rows = [row for row in rows if row["status"] not in TERMINAL]
    reference = route.reference
    if reference:
        lowered = reference.lower()
        matches = [row for row in rows if row["id"] == reference] or [row for row in rows if lowered in row["name"].lower()]
    else:
        matches = rows if len(rows) == 1 else []
    if len(matches) == 1:
        return matches[0], None
    options = [{"id": row["id"], "name": row["name"], "status": row["status"]} for row in (matches or rows)[:10]]
    wanted = f"'{reference}'" if reference else "that"
    return None, _reply(route, "needs_clarification",
                        f"I could not tell which task you mean by {wanted}. Name one of these." if options
                        else "You have no tasks that match.", data={"options": options})


def _list_tasks(principal: Principal, route: Route) -> dict[str, Any]:
    rows = lifecycle.list_all(principal)
    summary = "; ".join(f"{row['name']} ({row['status']})" for row in rows[:10]) or "none yet"
    return _reply(route, "completed", f"{len(rows)} task(s): {summary}.",
                  data={"automations": [{"id": r["id"], "name": r["name"], "status": r["status"], "mode": r["mode"]} for r in rows]})


def _task_status(principal: Principal, route: Route) -> dict[str, Any]:
    if route.reference is None and len(lifecycle.list_all(principal)) != 1:
        return _list_tasks(principal, Route("list_tasks", None, route.routed_by))
    task, clarify = _resolve_task(principal, route, live_only=False)
    if clarify:
        return clarify
    status = tasks.task_status(principal, task["id"])
    waiting = len(status["pending_approvals"])
    return _reply(route, "completed", f"{status['name']} is {status['status']} in {status['mode']} mode, with {waiting} approval(s) waiting.",
                  data=status)


def _find_automations(principal: Principal, route: Route, message: str) -> dict[str, Any]:
    result = catalog.discover(principal, message[:2000])
    titles = ", ".join(item["title"] for item in result["opportunities"][:3])
    return _reply(route, "completed",
                  f"Closest matches: {titles}. {result['basis']}" if titles else "Nothing in the catalog matches that yet.", data=result)


def _list_approvals(principal: Principal, route: Route) -> dict[str, Any]:
    principal.require("approvals:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        rows = [approvals.serialize(a) for a in session.execute(
            select(Approval).where(Approval.status == "pending").order_by(Approval.created_at.desc()).limit(100)).scalars()]
    summary = "; ".join(f"{a['title']} ({a['risk_tier']})" for a in rows[:10])
    text = f"{len(rows)} waiting: {summary}." if rows else "Nothing is waiting for a decision."
    return _reply(route, "completed", f"{text} Decide them in the Approval Inbox; chat does not approve anything.",
                  data={"approvals": rows})


def _spending(principal: Principal, route: Route) -> dict[str, Any]:
    principal.require("billing:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        summary = billing.spending_summary(session, principal.tenant_id)
    budget = summary["monthly_budget_usd"]
    limit = f" of a {budget} USD monthly budget" if budget else " with no monthly budget set"
    return _reply(route, "completed", f"{summary['total_usd']} USD recorded this month{limit}. Recorded usage may be an estimate, not a provider invoice.",
                  data=summary)


def _ask_pause(principal: Principal, route: Route) -> dict[str, Any]:
    principal.require("tasks:control")
    task, clarify = _resolve_task(principal, route, live_only=True)
    if clarify:
        return clarify
    seal = _seal(principal, "pause_task", {"automation_id": task["id"], "name": task["name"], "expected_version": task["version"]})
    return _reply(route, "confirmation_required", f"This will pause {task['name']}. New actions stop; one already in progress may finish. Reply yes to confirm.",
                  confirmation={**seal, "summary": f"Pause {task['name']}"})


def _ask_stop_messages(principal: Principal, route: Route) -> dict[str, Any]:
    principal.require("tasks:control")
    seal = _seal(principal, "stop_customer_messages", {})
    return _reply(route, "confirmation_required",
                  "This will stop every new customer message across the business. Messages already sent cannot be recalled. Reply yes to confirm.",
                  confirmation={**seal, "summary": "Stop all new customer messages"})


def _run_write(principal: Principal, route: Route, arguments: dict[str, Any]) -> dict[str, Any]:
    if route.intent == "pause_task":
        result = lifecycle.pause(principal, arguments["automation_id"], arguments["expected_version"])
        return _reply(route, "completed", f"{arguments['name']} is paused. {result['disclosure']}", data=result)
    principal.require("tasks:control")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        current = list(session.get(Organization, principal.tenant_id).paused_categories)
    paused = lifecycle.set_paused_categories(principal, [*current, CUSTOMER_MESSAGE_CATEGORY])
    return _reply(route, "completed",
                  "New customer messages are stopped. Anything already sent cannot be recalled. Turn this off in Gluons Web when ready.",
                  data={"paused_categories": paused})


def _confirm(principal: Principal, message: str, token: str) -> dict[str, Any]:
    pending = _open(principal, token)
    route = Route(pending["intent"], None, "confirmation")
    if _YES.search(message):
        return _run_write(principal, route, pending["arguments"])
    if _NO.search(message):
        return _reply(route, "cancelled", "Nothing was changed.")
    return _reply(route, "needs_clarification", "Reply yes to go ahead or no to leave things as they are.",
                  confirmation={"token": token, "expires_at": pending["expires"], "summary": pending["intent"]})


def respond(principal: Principal, message: str, *, confirmation_token: str | None = None,
            transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    if not message.strip() or len(message) > MAX_MESSAGE:
        raise validation_failed(f"Send a message of up to {MAX_MESSAGE} characters.")
    if confirmation_token:
        return _confirm(principal, message, confirmation_token)
    route = route_by_rules(message)
    if route.intent == "unknown" and route.routed_by == "none":
        route = _route_by_model(principal, message, transport) or route
    if route.intent == "pause_task":
        return _ask_pause(principal, route)
    if route.intent == "stop_customer_messages":
        return _ask_stop_messages(principal, route)
    if route.intent == "list_tasks":
        return _list_tasks(principal, route)
    if route.intent == "task_status":
        return _task_status(principal, route)
    if route.intent == "find_automations":
        return _find_automations(principal, route, message)
    if route.intent == "list_approvals":
        return _list_approvals(principal, route)
    if route.intent == "spending":
        return _spending(principal, route)
    return _reply(route, "needs_clarification",
                  "I can list tasks, show a task's status, pause a task, find automations, list approvals, "
                  "show spending or stop all customer messages. No action was taken.")
