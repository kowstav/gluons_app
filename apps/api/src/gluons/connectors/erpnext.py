"""ERPNext / Frappe REST connector.

Frappe has no native idempotency key, so the Gluons reference is written to `po_no` and
used to look an order up after an ambiguous failure. The same connector talks to a real
ERPNext site and to `apps/sandbox_erp`, which implements the same REST subset. Which one
was used is recorded on the connection and on every receipt as `environment`.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
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


class ErpOrderConnector:
    def client(self, ctx: ConnectorContext) -> GuardedClient:
        base = ctx.metadata["base_url"]
        return GuardedClient((urlsplit(base).hostname or "",), transport=ctx.transport)

    def _headers(self, ctx: ConnectorContext) -> dict[str, str]:
        credentials = ctx.credentials()
        return {"Authorization": f"token {credentials['api_key']}:{credentials['api_secret']}"}

    def _url(self, ctx: ConnectorContext, suffix: str) -> str:
        return f"{ctx.metadata['base_url'].rstrip('/')}{suffix}"

    def _send(self, call: Any) -> httpx.Response:
        try:
            response = call()
        except httpx.TimeoutException as exc:
            raise ProviderError("ambiguous", "ERP timed out before confirming") from exc
        except httpx.TransportError as exc:
            raise ProviderError("ambiguous", f"ERP connection failed: {exc}") from exc
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
            )
        )
        document = response.json()["data"]
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
                params={"filters": json.dumps([["po_no", "=", payload.reference]]), "fields": json.dumps(["name"])},
            )
        )
        rows = response.json()["data"]
        if not rows:
            return None
        return ProviderResult(provider_object_id=rows[0]["name"], receipt={"recovered_by_lookup": True, "name": rows[0]["name"]})

    def verify(self, ctx: ConnectorContext, payload: BaseModel, result: ProviderResult) -> Readback:
        client = self.client(ctx)
        name = quote(result.provider_object_id or "")
        try:
            response = self._send(
                lambda: client.get(self._url(ctx, f"/api/resource/{SALES_ORDER}/{name}"), headers=self._headers(ctx))
            )
        except ProviderError as exc:
            if exc.status == 404:
                return Readback(found=False, matches=False, mismatches=("order not found in ERP",))
            raise
        document = response.json()["data"]
        expected = {(i.sku, float(i.quantity)) for i in payload.items}
        actual = {(i["item_code"], float(i["qty"])) for i in document.get("items", [])}
        mismatches: list[str] = []
        if document.get("customer") != payload.customer:
            mismatches.append(f"customer is {document.get('customer')}, expected {payload.customer}")
        if document.get("po_no") != payload.reference:
            mismatches.append("reference does not match")
        if actual != expected:
            mismatches.append(f"items are {sorted(actual)}, expected {sorted(expected)}")
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
