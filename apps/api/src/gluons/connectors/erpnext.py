"""ERPNext / Frappe REST connector.

Frappe has no native idempotency key, so the Gluons reference is written to `po_no` and
used to look an order up after an ambiguous failure. The same connector talks to a real
ERPNext site and to `apps/sandbox_erp`, which implements the same REST subset. Which one
was used is recorded on the connection and on every receipt as `environment`.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from gluons.actions import (
    ActionSpec,
    ConnectorContext,
    ProviderError,
    ProviderResult,
    Readback,
    classify_http,
    register_action,
    register_connector,
)
from gluons.net import GuardedClient

SALES_ORDER = quote("Sales Order")


class ErpOrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    quantity: int = Field(gt=0)
    unit_price_minor: int = Field(ge=0)


class CreateErpOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer: str
    reference: str
    delivery_date: str | None = None
    currency: str = "USD"
    items: list[ErpOrderItem] = Field(min_length=1)


def _total(payload: CreateErpOrder) -> int:
    return sum(i.quantity * i.unit_price_minor for i in payload.items)


def _response_data(response: httpx.Response) -> Any:
    try:
        return response.json()["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError("ambiguous", "ERP did not return readable order data.") from exc


def _order_lines_match(document: dict[str, Any], payload: CreateErpOrder) -> bool:
    expected = Counter((item.sku, item.quantity, Decimal(item.unit_price_minor) / 100, payload.delivery_date or None)
                       for item in payload.items)
    try:
        actual = Counter((item["item_code"], Decimal(str(item["qty"])), Decimal(str(item["rate"])),
                          item.get("delivery_date") if payload.delivery_date else None)
                         for item in document["items"])
    except (KeyError, TypeError, AttributeError, InvalidOperation):
        return False
    return actual == expected


class ErpOrderConnector:
    def client(self, ctx: ConnectorContext) -> GuardedClient:
        base = ctx.metadata["base_url"]
        return GuardedClient((urlsplit(base).hostname or "",), transport=ctx.transport)

    def _headers(self, ctx: ConnectorContext) -> dict[str, str]:
        credentials = ctx.credentials()
        return {"Authorization": f"token {credentials['api_key']}:{credentials['api_secret']}"}

    def _url(self, ctx: ConnectorContext, suffix: str) -> str:
        return f"{ctx.metadata['base_url'].rstrip('/')}{suffix}"

    def _send(self, call: Any, *, reconciling: bool = False, ambiguous_statuses: tuple[int, ...] = ()) -> httpx.Response:
        try:
            response = call()
        except httpx.TimeoutException as exc:
            raise ProviderError("ambiguous", "ERP timed out before confirming") from exc
        except httpx.TransportError as exc:
            raise ProviderError("ambiguous", f"ERP connection failed: {exc}") from exc
        if response.status_code in ambiguous_statuses or (reconciling and response.status_code >= 400):
            raise ProviderError("ambiguous", "ERP did not confirm the order outcome.", status=response.status_code)
        if response.status_code >= 400:
            raise classify_http(response.status_code, response.text)
        return response

    def prepare(self, ctx: ConnectorContext, payload: BaseModel) -> dict[str, Any]:
        return {"would_create": "Sales Order", "document": _document(payload), "environment": ctx.environment}

    def execute(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult:
        client = self.client(ctx)
        response = self._send(
            lambda: client.post(
                self._url(ctx, f"/api/resource/{SALES_ORDER}"), headers=self._headers(ctx), json=_document(payload)
            ),
            ambiguous_statuses=() if ctx.environment == "simulator" else (408, 502, 503),
        )
        document = _response_data(response)
        if not isinstance(document, dict) or not isinstance(document.get("name"), str) or not document["name"]:
            raise ProviderError("ambiguous", "ERP did not confirm a sales order identifier.")
        return ProviderResult(
            provider_object_id=document["name"],
            receipt={"doctype": "Sales Order", "name": document["name"], "docstatus": document.get("docstatus")},
            request_id=response.headers.get("X-Frappe-Request-Id"),
        )

    def lookup(self, ctx: ConnectorContext, payload: BaseModel, idempotency_key: str) -> ProviderResult | None:
        client = self.client(ctx)
        response = self._send(
            lambda: client.get(
                self._url(ctx, f"/api/resource/{SALES_ORDER}"),
                headers=self._headers(ctx),
                params={"filters": json.dumps([["po_no", "=", payload.reference]]), "limit_page_length": 2},
            ),
            reconciling=True,
        )
        rows = _response_data(response)
        if not isinstance(rows, list):
            raise ProviderError("ambiguous", "ERP did not return a readable order lookup.")
        if not rows:
            if ctx.environment == "simulator":
                return None
            raise ProviderError("ambiguous", "ERP lookup found no order yet. Reconcile the uncertain attempt before retrying a write.")
        if len(rows) != 1:
            raise ProviderError("ambiguous", "ERP lookup found multiple orders with the same reference. Reconcile before proceeding.")
        if not isinstance(rows[0], dict) or not isinstance(rows[0].get("name"), str) or not rows[0]["name"]:
            raise ProviderError("ambiguous", "ERP did not return a readable sales order identifier.")
        return ProviderResult(provider_object_id=rows[0]["name"], receipt={"recovered_by_lookup": True, "name": rows[0]["name"]})

    def verify(self, ctx: ConnectorContext, payload: BaseModel, result: ProviderResult) -> Readback:
        client = self.client(ctx)
        name = quote(result.provider_object_id or "", safe="")
        try:
            response = self._send(
                lambda: client.get(self._url(ctx, f"/api/resource/{SALES_ORDER}/{name}"), headers=self._headers(ctx))
            )
        except ProviderError as exc:
            if exc.status == 404:
                return Readback(found=False, matches=False, mismatches=("order not found in ERP",))
            raise
        document = _response_data(response)
        if not isinstance(document, dict):
            raise ProviderError("ambiguous", "ERP did not return a readable sales order.")
        mismatches: list[str] = []
        if document.get("name") != result.provider_object_id:
            mismatches.append("order identifier differs")
        if document.get("customer") != payload.customer:
            mismatches.append("customer differs")
        if document.get("po_no") != payload.reference:
            mismatches.append("reference does not match")
        if document.get("currency") != payload.currency:
            mismatches.append("currency differs")
        if type(document.get("docstatus")) is not int or document["docstatus"] != 0:
            mismatches.append("sales order is not a draft")
        if payload.delivery_date and document.get("delivery_date") != payload.delivery_date:
            mismatches.append("delivery date differs")
        if not _order_lines_match(document, payload):
            mismatches.append("approved line items, quantities, rates or delivery dates differ")
        return Readback(
            found=True,
            matches=not mismatches,
            record={"name": document.get("name"), "docstatus": document.get("docstatus"), "grand_total": document.get("grand_total")},
            mismatches=tuple(mismatches),
        )


def _document(payload: CreateErpOrder) -> dict[str, Any]:
    delivery = payload.delivery_date or (date.today() + timedelta(days=1)).isoformat()  # noqa: DTZ011
    return {
        "doctype": "Sales Order",
        "customer": payload.customer,
        "po_no": payload.reference,
        "transaction_date": date.today().isoformat(),  # noqa: DTZ011
        "delivery_date": delivery,
        "currency": payload.currency,
        "items": [
            {"item_code": i.sku, "qty": i.quantity, "rate": i.unit_price_minor / 100, "delivery_date": delivery}
            for i in payload.items
        ],
    }


register_action(
    ActionSpec(
        name="erp.order.create",
        permission="submit_order",
        risk_tier="R3",
        capability="erp",
        input_model=CreateErpOrder,
        description="Create an order in the ERP",
        amount_minor=_total,
    )
)
register_connector("erpnext", "erp.order.create", ErpOrderConnector())
