"""Domain steps for order taking. Skills on any channel reuse these."""
from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert

from gluons import orders
from gluons.core import utcnow
from gluons.db.models import Customer, Order
from gluons.db.session import tenant_tx
from gluons.policy import NON_LIVE_MODES
from gluons.runtime.step import StepContext, StepOutcome
from gluons.skills.contract import Node
from gluons.skills.primitives import domain


@domain("orders.resolve_customer")
def resolve_customer(ctx: StepContext, node: Node) -> StepOutcome:
    sender = ctx.input.get("sender") or ""
    phone = ctx.input.get("phone")
    with tenant_tx(ctx.tenant_id) as session:
        customer = orders.find_customer(session, email=sender or None, phone=phone)
        if customer is None:
            return StepOutcome(
                output={"customer": None},
                branch="unknown",
                state={"review_reasons": [f"No authorized customer matches {sender or phone or 'this sender'}."]},
            )
        return StepOutcome(
            output={"customer_id": customer.id},
            state={
                "customer": {"id": customer.id, "name": customer.name, "emails": customer.emails, "phones": customer.phones}
            },
        )


@domain("orders.extract")
def extract(ctx: StepContext, node: Node) -> StepOutcome:
    text = str(ctx.input.get("text", ""))
    lines, unparsed = orders.parse_lines(text)
    delivery = orders.parse_delivery_date(text)
    reasons = [f"Could not read this line: '{u}'." for u in unparsed]
    if not lines:
        reasons.append("No order lines were found in this message.")
    return StepOutcome(
        output={"lines_found": len(lines), "unparsed": unparsed},
        state={
            "requested_lines": [line.__dict__ for line in lines],
            "delivery_date": delivery,
            "review_reasons": reasons,
        },
        model_identifier="deterministic:order-parser-v1",
    )


def _requested(ctx: StepContext) -> list[orders.RequestedLine]:
    return [orders.RequestedLine(**line) for line in ctx.state.get("requested_lines", [])]


def _load_customer(session: Any, ctx: StepContext) -> Customer | None:
    customer_id = (ctx.state.get("customer") or {}).get("id")
    return session.get(Customer, customer_id) if customer_id else None


@domain("orders.match_catalog")
def match_catalog(ctx: StepContext, node: Node) -> StepOutcome:
    with tenant_tx(ctx.tenant_id) as session:
        customer = _load_customer(session, ctx)
        resolved, reasons = orders.resolve_lines(session, _requested(ctx), customer)
    all_reasons = [*ctx.state.get("review_reasons", []), *reasons]
    return StepOutcome(
        output={"resolved": sum(1 for r in resolved if r.sku and not r.problems), "problems": len(reasons)},
        state={"lines": orders.as_dicts(resolved), "review_reasons": all_reasons},
    )


@domain("orders.build_draft")
def build_draft(ctx: StepContext, node: Node) -> StepOutcome:
    lines = [orders.ResolvedLine(**line) for line in ctx.state.get("lines", [])]
    reasons = list(ctx.state.get("review_reasons", []))
    customer = ctx.state.get("customer") or {}
    delivery = ctx.state.get("delivery_date")
    key = orders.order_business_key(customer.get("id"), lines, delivery)
    total = orders.total_minor(lines)
    currency = ctx.config.get("currency", "USD")
    state: dict[str, Any] = {
        "order_business_key": key,
        "order_total_minor": total,
        "order_summary": f"{len(lines)} line(s) for {customer.get('name', 'unknown customer')}, total {total / 100:.2f} {currency}",
    }
    if lines and not reasons:
        state["erp_payload"] = orders.erp_payload(
            customer["name"],
            lines,
            reference=ctx.idempotency_key("erp"),
            delivery_date=delivery,
            currency=currency,
        )
    if ctx.mode not in NON_LIVE_MODES:
        with tenant_tx(ctx.tenant_id) as session:
            inserted = session.execute(
                insert(Order)
                .values(
                    id=f"ord_{key[:24]}",
                    tenant_id=ctx.tenant_id,
                    run_id=ctx.run_id,
                    channel=ctx.input.get("channel", "email"),
                    source_event_id=ctx.source_event_id,
                    business_key=key,
                    customer_id=customer.get("id"),
                    status="needs_review" if reasons else "draft",
                    lines=orders.as_dicts(lines),
                    total_minor=total,
                    currency=currency,
                    review_reasons=reasons,
                    is_test=ctx.config.get("is_test", True),
                )
                .on_conflict_do_nothing(constraint="uq_order_business_key")
                .returning(Order.id)
            ).first()
            if inserted is None:
                existing = session.query(Order).filter(Order.business_key == key).one()
                if existing.run_id != ctx.run_id:
                    reasons.append("This looks like a duplicate of an order already taken on another message.")
                    state["duplicate_of"] = existing.id
    state["review_reasons"] = reasons
    return StepOutcome(
        output={"total_minor": total, "reasons": reasons},
        state=state,
        branch="needs_review" if reasons else "clean",
    )


@domain("orders.apply_review")
def apply_review(ctx: StepContext, node: Node) -> StepOutcome:
    """A reviewer resolved the problem. Their corrected lines go back through the same checks."""
    resolution = ctx.state.get("review_resolution") or {}
    corrected = resolution.get("lines")
    if corrected:
        lines = [orders.RequestedLine(c.get("raw", c["item_text"]), c["quantity"], c.get("unit"), c["item_text"]) for c in corrected]
        return StepOutcome(
            output={"corrected": len(lines)},
            state={"requested_lines": [line.__dict__ for line in lines], "review_reasons": []},
            branch="rematch",
        )
    if ctx.state.get("duplicate_of"):
        return StepOutcome(output={"duplicate": True}, branch="stop")
    if not ctx.state.get("erp_payload"):
        return StepOutcome(output={"unresolved": True}, branch="stop")
    return StepOutcome(output={"accepted": True}, state={"review_reasons": []}, branch="continue")


@domain("orders.record_written")
def record_written(ctx: StepContext, node: Node) -> StepOutcome:
    written = ctx.state.get("erp_order") or {}
    if ctx.mode not in NON_LIVE_MODES and written.get("verified"):
        with tenant_tx(ctx.tenant_id) as session:
            session.query(Order).filter(Order.business_key == ctx.state["order_business_key"]).update(
                {
                    "status": "confirmed",
                    "external_order_id": written.get("provider_object_id"),
                    "receipt_id": written.get("receipt_id"),
                    "updated_at": utcnow(),
                }
            )
    return StepOutcome(output={"external_order_id": written.get("provider_object_id")})


@domain("orders.build_confirmation")
def build_confirmation(ctx: StepContext, node: Node) -> StepOutcome:
    """A confirmation exists only after the ERP readback. Recipient comes from the record."""
    written = ctx.state.get("erp_order") or {}
    customer = ctx.state.get("customer") or {}
    recipient = (customer.get("emails") or [None])[0]
    if not written.get("verified") or recipient is None or "email" not in ctx.connections:
        return StepOutcome(output={"sent": False}, branch="skip", state={"confirmation_skipped": True})
    return StepOutcome(
        output={"recipient": recipient},
        state={
            "confirmation": {
                "to": recipient,
                "subject": f"Order {written['provider_object_id']} confirmed",
                "body": f"Your order {written['provider_object_id']} is confirmed. {ctx.state.get('order_summary', '')}",
                "thread_id": ctx.input.get("thread_id"),
            },
            "confirmation_resources": {
                "recipient": recipient,
                "recipient_verified": True,
                "order_verified": True,
            },
        },
    )
