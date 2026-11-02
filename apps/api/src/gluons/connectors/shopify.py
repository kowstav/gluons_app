"""Draft-only Shopify product actions plus approved supplier email contracts."""
from __future__ import annotations

import html
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gluons.actions import (
    ActionSpec,
    ConnectorContext,
    ProviderError,
    ProviderResult,
    Readback,
    register_action,
    register_connector,
)
from gluons.commerce import shopify
from gluons.connectors.gmail import GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE, GmailSendConnector, SendEmail
from gluons.core import sha256_hex
from gluons.net import GuardedClient


class ProductDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    description_text: str = Field(default="", max_length=20_000)
    vendor: str = Field(min_length=1, max_length=255)
    source_evidence: list[str] = Field(min_length=1, max_length=20)

    @field_validator("source_evidence")
    @classmethod
    def check_sources(cls, values: list[str]) -> list[str]:
        if any(not value.startswith("https://") or len(value) > 2000 for value in values):
            raise ValueError("Reference dated, authorized HTTPS product sources.")
        return values


class ProductDraftConnector:
    def client(self, ctx: ConnectorContext) -> GuardedClient:
        return GuardedClient((shopify.shop_domain(ctx.metadata["shop"]),), transport=ctx.transport)

    def _query(self, ctx: ConnectorContext, query: str, variables: dict, *, mutation: bool = False) -> dict:
        return shopify.graphql(ctx.metadata["shop"], ctx.credentials()["access_token"], query, variables,
                               mutation=mutation, transport=ctx.transport)

    def prepare(self, ctx: ConnectorContext, payload: ProductDraft) -> dict:
        return {"would_create": "Shopify unpublished draft product", "status": "DRAFT", "input": payload.model_dump(),
                "notice": "No checkout, publication, supplier agreement or shipment is created."}

    def execute(self, ctx: ConnectorContext, payload: ProductDraft, idempotency_key: str) -> ProviderResult:
        handle = "gluons-" + sha256_hex(idempotency_key)[:40]
        variables = {"product": {"title": payload.title, "vendor": payload.vendor, "handle": handle,
                                  "descriptionHtml": html.escape(payload.description_text).replace("\n", "<br>"), "status": "DRAFT"}}
        data = self._query(ctx, "mutation GluonsStageProduct($product: ProductCreateInput!) { productCreate(product: $product) { product { id handle status } userErrors { field message } } }", variables, mutation=True)
        result = data.get("productCreate") or {}
        if result.get("userErrors"):
            raise ProviderError("permanent", "Shopify rejected this product draft; review its fields and permissions.")
        product = result.get("product") or {}
        if not re.fullmatch(r"gid://shopify/Product/[0-9]+", product.get("id", "")):
            raise ProviderError("ambiguous", "Shopify did not confirm a product identifier.")
        return ProviderResult(product["id"], {"handle": handle, "status": product.get("status"), "source_evidence": payload.source_evidence})

    def lookup(self, ctx: ConnectorContext, payload: ProductDraft, idempotency_key: str) -> ProviderResult | None:
        handle = "gluons-" + sha256_hex(idempotency_key)[:40]
        data = self._query(ctx, "query GluonsFindProduct($query: String!) { products(first: 2, query: $query) { nodes { id handle } } }", {"query": f"handle:{handle}"})
        products = (data.get("products") or {}).get("nodes", [])
        if len(products) != 1 or products[0].get("handle") != handle:
            raise ProviderError("ambiguous", "Product lookup is inconclusive. Reconcile the earlier attempt before retrying creation.")
        return ProviderResult(products[0]["id"], {"handle": handle, "recovered_by_lookup": True})

    def verify(self, ctx: ConnectorContext, payload: ProductDraft, result: ProviderResult) -> Readback:
        if not re.fullmatch(r"gid://shopify/Product/[0-9]+", result.provider_object_id or ""):
            raise ProviderError("permanent", "Shopify product identity is invalid.")
        data = self._query(ctx, "query GluonsVerifyProduct($id: ID!) { product(id: $id) { id title vendor handle status description } }", {"id": result.provider_object_id})
        product = data.get("product")
        if not product:
            return Readback(found=False, matches=False, mismatches=("product not found",))
        expected = {"id": result.provider_object_id, "title": payload.title, "vendor": payload.vendor, "status": "DRAFT", "handle": result.receipt.get("handle")}
        mismatches = [field for field, value in expected.items() if product.get(field) != value]
        if " ".join(str(product.get("description", "")).split()) != " ".join(payload.description_text.split()):
            mismatches.append("description")
        return Readback(found=True, matches=not mismatches, record={"id": product.get("id"), "handle": product.get("handle"),
            "status": product.get("status"), "verified_scope": "draft_product_only", "live_publication_verified": False}, mismatches=tuple(mismatches))


class SupplierInquiry(SendEmail):
    represented_entity: str = Field(min_length=1, max_length=300, pattern=r"^[^\r\n]+$")


class SupplierInquiryConnector(GmailSendConnector):
    def _mail(self, payload: SupplierInquiry) -> SendEmail:
        return SendEmail(to=payload.to, subject=payload.subject, body=f"{payload.body}\n\nSent on behalf of {payload.represented_entity}.", thread_id=payload.thread_id)

    def prepare(self, ctx: ConnectorContext, payload: SupplierInquiry) -> dict:
        return {"would_send": self._mail(payload).model_dump(), "represented_entity": payload.represented_entity,
                "notice": "First contact requires an exact owner approval and an independent automation grant."}

    def execute(self, ctx: ConnectorContext, payload: SupplierInquiry, idempotency_key: str) -> ProviderResult:
        return super().execute(ctx, self._mail(payload), idempotency_key)

    def lookup(self, ctx: ConnectorContext, payload: SupplierInquiry, idempotency_key: str) -> ProviderResult | None:
        return super().lookup(ctx, self._mail(payload), idempotency_key)

    def verify(self, ctx: ConnectorContext, payload: SupplierInquiry, result: ProviderResult) -> Readback:
        return super().verify(ctx, self._mail(payload), result)


register_action(ActionSpec(name="shopify.product.create_draft", permission="draft", risk_tier="R1", capability="storefront",
    input_model=ProductDraft, description="Stage an unpublished Shopify product draft", required_scopes={"shopify": ("read_products", "write_products")}, irreversible=False))
register_connector("shopify", "shopify.product.create_draft", ProductDraftConnector())
register_action(ActionSpec(name="commerce.supplier.inquiry", permission="send", risk_tier="R2", capability="email", input_model=SupplierInquiry,
    description="Send a precise owner-approved supplier inquiry", required_scopes={"gmail": (GMAIL_SEND_SCOPE, GMAIL_READ_SCOPE)},
    irreversible=True, resources=lambda payload: {"recipient": str(payload.to)}))
register_connector("gmail", "commerce.supplier.inquiry", SupplierInquiryConnector())


class PublishProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(pattern=r"^gid://shopify/Product/[0-9]+$")
    publication_id: str = Field(pattern=r"^gid://shopify/Publication/[0-9]+$")
    expected_title: str = Field(min_length=1, max_length=255)


class PublishProductConnector(ProductDraftConnector):
    def _product(self, ctx: ConnectorContext, payload: PublishProduct) -> dict:
        data = self._query(ctx, "query GluonsPublicationState($id: ID!, $publication: ID!) { product(id: $id) { id title status publishedOnPublication(publicationId: $publication) } publication(id: $publication) { id name } }",
                           {"id": payload.product_id, "publication": payload.publication_id})
        product = data.get("product") or {}
        if product.get("id") != payload.product_id or product.get("title") != payload.expected_title or (data.get("publication") or {}).get("id") != payload.publication_id:
            raise ProviderError("permanent", "Product or publication does not match the approved target.")
        return product

    def prepare(self, ctx: ConnectorContext, payload: PublishProduct) -> dict:
        return {"would_publish": payload.model_dump(), "requires": "Owner R3 approval and an already ACTIVE product", "external_writes": 0}

    def execute(self, ctx: ConnectorContext, payload: PublishProduct, idempotency_key: str) -> ProviderResult:
        if self._product(ctx, payload).get("status") != "ACTIVE":
            raise ProviderError("permanent", "The approved product must already be ACTIVE. No product status is silently changed.")
        data = self._query(ctx, "mutation GluonsPublishProduct($id: ID!, $input: [PublicationInput!]!) { publishablePublish(id: $id, input: $input) { userErrors { field message } } }",
                           {"id": payload.product_id, "input": [{"publicationId": payload.publication_id}]}, mutation=True)
        if (data.get("publishablePublish") or {}).get("userErrors"):
            raise ProviderError("permanent", "Shopify refused this publication. Review the channel and permissions.")
        return ProviderResult(payload.product_id, {"publication_id": payload.publication_id})

    def lookup(self, ctx: ConnectorContext, payload: PublishProduct, idempotency_key: str) -> ProviderResult | None:
        product = self._product(ctx, payload)
        if product.get("publishedOnPublication") is True:
            return ProviderResult(payload.product_id, {"publication_id": payload.publication_id, "recovered_by_lookup": True})
        # This operation sets existing membership, unlike creating a product.
        # Repeating a verified-unpublished target cannot create duplicate products.
        return None

    def verify(self, ctx: ConnectorContext, payload: PublishProduct, result: ProviderResult) -> Readback:
        product = self._product(ctx, payload)
        matches = result.provider_object_id == payload.product_id and product.get("status") == "ACTIVE" and product.get("publishedOnPublication") is True
        return Readback(found=True, matches=matches, record={"id": product.get("id"), "publication_id": payload.publication_id,
            "status": product.get("status"), "published": product.get("publishedOnPublication"), "whole_store_launch_verified": False},
            mismatches=() if matches else ("publication does not match the approved active product",))


class FulfillmentLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^gid://shopify/FulfillmentOrderLineItem/[0-9]+$")
    quantity: int = Field(gt=0, le=100_000)


class RecordFulfillment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^gid://shopify/Order/[0-9]+$")
    fulfillment_order_id: str = Field(pattern=r"^gid://shopify/FulfillmentOrder/[0-9]+$")
    line_items: list[FulfillmentLine] = Field(min_length=1, max_length=100)
    tracking_number: str = Field(pattern=r"^[A-Za-z0-9-]{3,100}$")
    carrier: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    shipment_receipt_id: str = Field(min_length=5, max_length=40)

    @field_validator("line_items")
    @classmethod
    def unique_lines(cls, lines: list[FulfillmentLine]) -> list[FulfillmentLine]:
        if len({line.id for line in lines}) != len(lines):
            raise ValueError("Fulfillment line identifiers must be unique.")
        return lines


class FulfillmentConnector(ProductDraftConnector):
    def prepare(self, ctx: ConnectorContext, payload: RecordFulfillment) -> dict:
        return {"would_record_fulfillment": payload.model_dump(), "requires": ["paid ACCEPT-risk order", "verified shipment receipt", "independent carrier transit evidence", "owner R3 approval"], "notify_customer": False, "delivered": False}

    def _shipment(self, ctx: ConnectorContext, payload: RecordFulfillment) -> dict:
        from gluons.config import get_settings
        from gluons.db.models import ActionReceipt
        from gluons.db.session import tenant_tx
        with tenant_tx(ctx.tenant_id) as session:
            receipt = session.get(ActionReceipt, payload.shipment_receipt_id)
            expected = {"order_id": payload.order_id, "tracking_number": payload.tracking_number, "carrier": payload.carrier}
            if (receipt is None or not receipt.verified or receipt.status != "succeeded" or receipt.action != "shipping.shipment.dispatch"
                    or any((receipt.readback or {}).get(key) != value for key, value in expected.items())
                    or (receipt.readback or {}).get("physical_handoff_verified") is not True
                    or receipt.environment != ctx.environment):
                raise ProviderError("permanent", "A verified shipment dispatch receipt bound to this order and tracking reference is required.")
        token = getattr(get_settings(), "shippo_api_token", None)
        if not token:
            raise ProviderError("auth", "Configure the authorized Shippo tracking credential before fulfillment verification.")
        client = GuardedClient(("api.goshippo.com",), transport=ctx.transport)
        try:
            try:
                response = client.get(f"https://api.goshippo.com/tracks/{payload.carrier}/{payload.tracking_number}", headers={"Authorization": f"ShippoToken {token}"})
            except httpx.HTTPError as exc:
                raise ProviderError("retryable", "The carrier tracking provider is unavailable.") from exc
            if response.status_code >= 400:
                from gluons.actions import classify_http
                raise classify_http(response.status_code)
            try:
                tracking = response.json()
            except ValueError as exc:
                raise ProviderError("permanent", "The carrier tracking response could not be verified.") from exc
        finally:
            client.close()
        if not isinstance(tracking, dict):
            raise ProviderError("permanent", "The carrier tracking response was not a record.")
        status = (tracking.get("tracking_status") or {}).get("status")
        if (tracking.get("carrier") != payload.carrier or tracking.get("tracking_number") != payload.tracking_number
                or status not in ("TRANSIT", "DELIVERED") or payload.carrier == "shippo"):
            raise ProviderError("permanent", "The carrier has not independently confirmed physical transit for this shipment.")
        return {"carrier": payload.carrier, "tracking_number": payload.tracking_number, "carrier_status": status, "provider": "shippo", "shipment_receipt_id": payload.shipment_receipt_id}

    def _order(self, ctx: ConnectorContext, payload: RecordFulfillment, *, writable: bool) -> tuple[dict, list[dict]]:
        data = self._query(ctx, "query GluonsFulfillmentGate($id: ID!) { fulfillmentOrder(id: $id) { id status order { id cancelledAt displayFinancialStatus risk { recommendation } } lineItems(first: 100) { nodes { id remainingQuantity lineItem { id } } pageInfo { hasNextPage } } } }", {"id": payload.fulfillment_order_id})
        fulfillment = data.get("fulfillmentOrder") or {}
        order = fulfillment.get("order") or {}
        lines = fulfillment.get("lineItems") or {}
        if fulfillment.get("id") != payload.fulfillment_order_id or order.get("id") != payload.order_id or (lines.get("pageInfo") or {}).get("hasNextPage"):
            raise ProviderError("permanent", "The fulfillment order does not match the approved order or exceeds the bounded line set.")
        if writable and (order.get("cancelledAt") or order.get("displayFinancialStatus") != "PAID" or (order.get("risk") or {}).get("recommendation") != "ACCEPT"
                         or fulfillment.get("status") not in ("OPEN", "IN_PROGRESS")):
            raise ProviderError("permanent", "The order must be paid, uncancelled, ACCEPT-risk and open for fulfillment.")
        by_id = {line["id"]: line for line in lines.get("nodes", [])}
        expected = []
        for line in payload.line_items:
            current = by_id.get(line.id)
            if not current or (writable and current.get("remainingQuantity", 0) < line.quantity):
                raise ProviderError("permanent", "Fulfillment quantities exceed the provider-confirmed remaining order lines.")
            expected.append({"id": current["lineItem"]["id"], "quantity": line.quantity})
        return fulfillment, expected

    def execute(self, ctx: ConnectorContext, payload: RecordFulfillment, idempotency_key: str) -> ProviderResult:
        carrier = self._shipment(ctx, payload)
        _, lines = self._order(ctx, payload, writable=True)
        variables = {"fulfillment": {"notifyCustomer": False, "trackingInfo": {"number": payload.tracking_number, "company": payload.carrier},
             "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": payload.fulfillment_order_id, "fulfillmentOrderLineItems": [line.model_dump() for line in payload.line_items]}]}}
        data = self._query(ctx, "mutation GluonsRecordFulfillment($fulfillment: FulfillmentInput!) { fulfillmentCreate(fulfillment: $fulfillment) { fulfillment { id } userErrors { field message } } }", variables, mutation=True)
        result = data.get("fulfillmentCreate") or {}
        if result.get("userErrors"):
            raise ProviderError("permanent", "Shopify refused this fulfillment. Review stock, permissions and approved quantities.")
        identifier = (result.get("fulfillment") or {}).get("id", "")
        if not re.fullmatch(r"gid://shopify/Fulfillment/[0-9]+", identifier):
            raise ProviderError("ambiguous", "Shopify returned no confirmed fulfillment identifier.")
        return ProviderResult(identifier, {"carrier_evidence": carrier, "expected_lines": lines, "order_id": payload.order_id, "delivered": False})

    def lookup(self, ctx: ConnectorContext, payload: RecordFulfillment, idempotency_key: str) -> ProviderResult | None:
        data = self._query(ctx, "query GluonsFindFulfillment($id: ID!) { order(id: $id) { id fulfillments(first: 100) { id status trackingInfo { number } } } }", {"id": payload.order_id})
        order = data.get("order") or {}
        matches = [item for item in order.get("fulfillments", []) if item.get("status") != "CANCELLED" and any(t.get("number") == payload.tracking_number for t in item.get("trackingInfo", []))]
        if order.get("id") != payload.order_id or len(matches) != 1:
            raise ProviderError("ambiguous", "Fulfillment lookup is inconclusive; reconcile before any repeat write.")
        _, lines = self._order(ctx, payload, writable=False)
        return ProviderResult(matches[0]["id"], {"expected_lines": lines, "recovered_by_lookup": True, "order_id": payload.order_id, "delivered": False})

    def verify(self, ctx: ConnectorContext, payload: RecordFulfillment, result: ProviderResult) -> Readback:
        if not re.fullmatch(r"gid://shopify/Fulfillment/[0-9]+", result.provider_object_id or ""):
            raise ProviderError("permanent", "The fulfillment identifier is invalid.")
        data = self._query(ctx, "query GluonsVerifyFulfillment($id: ID!) { fulfillment(id: $id) { id status order { id } trackingInfo { number } fulfillmentLineItems(first: 100) { nodes { quantity lineItem { id } } pageInfo { hasNextPage } } } }", {"id": result.provider_object_id})
        fulfillment = data.get("fulfillment") or {}
        line_data = fulfillment.get("fulfillmentLineItems") or {}
        actual = sorted((item.get("lineItem", {}).get("id"), item.get("quantity")) for item in line_data.get("nodes", []))
        expected = sorted((item["id"], item["quantity"]) for item in result.receipt.get("expected_lines", []))
        matches = (fulfillment.get("id") == result.provider_object_id and fulfillment.get("status") == "SUCCESS" and (fulfillment.get("order") or {}).get("id") == payload.order_id
                   and any(item.get("number") == payload.tracking_number for item in fulfillment.get("trackingInfo", []))
                   and bool(expected) and expected == actual and not (line_data.get("pageInfo") or {}).get("hasNextPage"))
        return Readback(found=bool(fulfillment), matches=matches, record={"id": fulfillment.get("id"), "order_id": payload.order_id,
            "tracking_number": payload.tracking_number, "status": fulfillment.get("status"), "delivered": False,
            "notice": "Provider fulfillment record verified; this is not a claim of parcel delivery."}, mismatches=() if matches else ("fulfillment differs from approved order, lines or tracking",))


register_action(ActionSpec(name="shopify.product.publish", permission="publish", risk_tier="R3", capability="storefront", input_model=PublishProduct,
    description="Publish one exact active Shopify product to one approved publication", required_scopes={"shopify": ("read_products", "read_publications", "write_publications")}, irreversible=True))
register_connector("shopify", "shopify.product.publish", PublishProductConnector())
register_action(ActionSpec(name="shopify.fulfillment.create", permission="submit_order", risk_tier="R3", capability="storefront", input_model=RecordFulfillment,
    description="Record an independently verified physical shipment against approved paid order lines", required_scopes={"shopify": ("read_orders", "read_merchant_managed_fulfillment_orders", "write_merchant_managed_fulfillment_orders")}, irreversible=True))
register_connector("shopify", "shopify.fulfillment.create", FulfillmentConnector())
