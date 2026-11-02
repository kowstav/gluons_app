"""The shared order transaction engine. Email, WhatsApp and voice all converge here.

Matching is deterministic. Anything unclear becomes a review reason; nothing is guessed,
and arithmetic stays in integer minor units. A model may help read messy text, but its
output goes through these same checks and can never mark a line as resolved by itself.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gluons.core import fingerprint
from gluons.db.models import CatalogItem, Customer

_UNIT_ALIASES = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "case": "case", "cases": "case", "cs": "case",
    "box": "box", "boxes": "box",
    "bag": "bag", "bags": "bag",
    "each": "each", "ea": "each", "unit": "each", "units": "each", "pc": "each", "pcs": "each",
}
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "twelve": 12, "a": 1, "an": 1,
}
_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?P<qty>\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s*(?:x\s*)?"
    r"(?:(?P<unit>" + "|".join(sorted(_UNIT_ALIASES, key=len, reverse=True)) + r")\b\.?\s*)?"
    r"(?:of\s+)?(?P<item>[A-Za-z0-9][\w\s\-/&']*?)\s*[.!]?\s*$",
    re.I,
)
_DELIVERY = re.compile(r"\b(?:deliver(?:y)?|ship|needed?|by)\b.{0,30}?\b(today|tomorrow|\d{4}-\d{2}-\d{2})\b", re.I)
_SEPARATORS = re.compile(r"[\n;]|,(?=\s*(?:\d|one|two|three|four|five|six|seven|eight|nine|ten)\b)", re.I)
_NOISE = re.compile(r"^(?:hi|hello|hey|dear|thanks|thank you|regards|best|cheers|please|order|po\b|purchase order|sent from|from:|to:|subject:)", re.I)


@dataclass
class RequestedLine:
    raw: str
    quantity: float
    unit: str | None
    item_text: str


@dataclass
class ResolvedLine:
    raw: str
    quantity: float
    unit: str | None
    item_text: str
    sku: str | None = None
    name: str | None = None
    unit_price_minor: int | None = None
    line_total_minor: int | None = None
    match: str = "unresolved"  # sku | customer_alias | catalog_alias | name | unresolved
    candidates: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def parse_lines(text: str) -> tuple[list[RequestedLine], list[str]]:
    """Returns parsed lines and any text that looked like an order line but did not parse."""
    lines: list[RequestedLine] = []
    unparsed: list[str] = []
    for chunk in _SEPARATORS.split(text):
        candidate = chunk.strip()
        if not candidate:
            continue
        match = _LINE.match(candidate)
        if match is None:
            if re.search(r"\d", candidate) and not _NOISE.match(candidate) and not _DELIVERY.search(candidate):
                unparsed.append(candidate)
            continue
        if _DELIVERY.search(candidate) and not match.group("unit"):
            continue
        raw_qty = match.group("qty").lower()
        quantity = float(_NUMBER_WORDS.get(raw_qty, raw_qty))
        unit = _UNIT_ALIASES.get((match.group("unit") or "").lower())
        lines.append(RequestedLine(candidate, quantity, unit, match.group("item").strip()))
    return lines, unparsed


def parse_delivery_date(text: str, today: date | None = None) -> str | None:
    match = _DELIVERY.search(text)
    if match is None:
        return None
    value = match.group(1).lower()
    base = today or date.today()  # noqa: DTZ011 - business calendar date
    if value == "today":
        return base.isoformat()
    if value == "tomorrow":
        return (base + timedelta(days=1)).isoformat()
    return value


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def find_customer(session: Session, *, email: str | None = None, phone: str | None = None) -> Customer | None:
    """Exact identity match only. A similar name is not an authorized account."""
    if not email and not phone:
        return None
    query = select(Customer)
    if email:
        query = query.where(Customer.emails.any(email.strip().lower()))
    else:
        query = query.where(Customer.phones.any(phone.strip()))
    matches = session.execute(query).scalars().all()
    return matches[0] if len(matches) == 1 else None


def resolve_lines(
    session: Session, lines: list[RequestedLine], customer: Customer | None
) -> tuple[list[ResolvedLine], list[str]]:
    catalog = session.execute(select(CatalogItem)).scalars().all()
    by_sku = {c.sku.lower(): c for c in catalog}
    by_alias: dict[str, list[CatalogItem]] = {}
    for item in catalog:
        for alias in [item.name, *item.aliases]:
            by_alias.setdefault(normalize(alias), []).append(item)
    customer_aliases = {normalize(k): v for k, v in (customer.alias_map if customer else {}).items()}

    resolved: list[ResolvedLine] = []
    reasons: list[str] = []
    for line in lines:
        result = ResolvedLine(line.raw, line.quantity, line.unit, line.item_text)
        key = normalize(line.item_text)
        item: CatalogItem | None = None
        if line.item_text.lower() in by_sku:
            item, result.match = by_sku[line.item_text.lower()], "sku"
        elif key in customer_aliases and customer_aliases[key].lower() in by_sku:
            item, result.match = by_sku[customer_aliases[key].lower()], "customer_alias"
        elif key in by_alias:
            options = by_alias[key]
            if len(options) == 1:
                item, result.match = options[0], "catalog_alias"
            else:
                result.candidates = [o.sku for o in options]
                result.problems.append("ambiguous_sku")
        if item is None:
            if not result.problems:
                result.problems.append("unknown_item")
            reasons.append(f"Could not match '{line.item_text}' to one catalog item.")
        else:
            result.sku, result.name = item.sku, item.name
            result.unit_price_minor = item.unit_price_minor
            if line.unit is not None and line.unit != item.unit:
                result.problems.append("invalid_unit")
                reasons.append(f"'{line.raw}' is in {line.unit}, but {item.sku} sells by {item.unit}.")
            else:
                result.unit = item.unit
            if line.quantity <= 0 or line.quantity != int(line.quantity):
                result.problems.append("invalid_quantity")
                reasons.append(f"'{line.raw}' does not have a whole, positive quantity.")
            if item.in_stock is not None and float(item.in_stock) < line.quantity:
                result.problems.append("unavailable")
                reasons.append(f"{item.sku} has {item.in_stock} available, and {line.quantity:g} were requested.")
            if not result.problems:
                result.line_total_minor = int(line.quantity) * item.unit_price_minor
        resolved.append(result)
    return resolved, reasons


def order_business_key(customer_id: str | None, lines: list[ResolvedLine], delivery: str | None) -> str:
    """Same customer, items and delivery day means the same order, on whichever channel."""
    items = sorted((line.sku or line.item_text, line.quantity) for line in lines)
    return fingerprint({"customer": customer_id, "items": items, "delivery": delivery})


def as_dicts(lines: list[ResolvedLine]) -> list[dict[str, Any]]:
    return [asdict(line) for line in lines]


def total_minor(lines: list[ResolvedLine]) -> int:
    return sum(line.line_total_minor or 0 for line in lines)


def erp_payload(
    customer_name: str, lines: list[ResolvedLine], *, reference: str, delivery_date: str | None, currency: str
) -> dict[str, Any]:
    return {
        "customer": customer_name,
        "reference": reference,
        "delivery_date": delivery_date,
        "currency": currency,
        "items": [
            {"sku": line.sku, "quantity": int(line.quantity), "unit_price_minor": line.unit_price_minor}
            for line in lines
        ],
    }
