"""Durable merchant consent and commerce evidence. No click completes KYC or shipping."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import replace
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from gluons import approvals, audit, events, gateway, vault
from gluons.actions import get_action
from gluons.authz import Principal
from gluons.commerce import readiness as readiness_projection
from gluons.commerce import shopify
from gluons.config import get_settings
from gluons.core import (
    GluonsError,
    fingerprint,
    new_id,
    new_secret_token,
    not_found,
    sha256_hex,
    token_hash,
    utcnow,
    validation_failed,
)
from gluons.db.models import (
    ActionReceipt,
    Approval,
    ApprovalDecision,
    Automation,
    Connection,
    ConnectionSession,
    LaunchEvidence,
    LaunchPlan,
    Supplier,
    WebhookInbox,
)
from gluons.db.session import operation_lock, tenant_tx

CAPABILITIES = {
    "read_catalog": ("read_products",), "stage_catalog": ("read_products", "write_products"),
    "monitor_orders": ("read_orders",), "payment_status": ("read_shopify_payments_accounts",),
    "publish_catalog": ("read_products", "read_publications", "write_publications"),
    "record_fulfillment": ("read_orders", "read_merchant_managed_fulfillment_orders", "write_merchant_managed_fulfillment_orders"),
}


def _browser(principal: Principal) -> None:
    principal.require("connections:manage")
    if principal.kind != "user" or not principal.session_id:
        raise GluonsError("OWNER_WEB_REQUIRED", "Authorize merchant accounts in Gluons Web.", status=403)


def _view(row: ConnectionSession) -> dict:
    return {"session_id": row.id, "provider": "shopify", "status": row.status,
            "shop": row.extra.get("shop"), "connection_id": row.connection_id,
            "requested_scopes": row.requested_scopes, "expires_at": row.expires_at.isoformat(),
            "next_step": row.failure_reason or "Owner creates a store through Shopify signup or official Partner transfer, then authorizes this app.",
            "signup_url": "https://www.shopify.com/start", "setup_url": f"{get_settings().public_web_url}/setup/{row.id}"}


def begin_session(principal: Principal, *, shop: str, capabilities: list[str], plan_id: str | None = None) -> dict:
    _browser(principal)
    shopify.shop_domain(shop)
    if not capabilities or any(capability not in CAPABILITIES for capability in capabilities):
        raise validation_failed("Choose supported Shopify capabilities.")
    pilot_shops = {s.strip() for s in get_settings().shopify_pilot_shops.split(",") if s.strip()}
    if shop not in pilot_shops:
        raise GluonsError("PILOT_STORE_APPROVAL_REQUIRED", "This manual store connection is limited to operator-approved pilot shops. Public app distribution requires Shopify review.", status=409)
    scopes = sorted({scope for capability in capabilities for scope in CAPABILITIES[capability]})
    state = new_secret_token()
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        if plan_id and session.get(LaunchPlan, plan_id) is None:
            raise not_found("Business launch plan")
        row = ConnectionSession(id=new_id("cns"), tenant_id=principal.tenant_id, initiated_by=principal.actor_id,
                                provider="shopify", requested_capabilities=capabilities, requested_scopes=scopes,
                                automation_plan_id=plan_id, setup_handle_hash=token_hash(new_secret_token()), state_hash=token_hash(state),
                                expires_at=utcnow() + timedelta(seconds=get_settings().connection_session_ttl_seconds),
                                status="pending" if shopify.configured() else "needs_configuration",
                                extra={"shop": shop, "browser_session_id": principal.session_id})
        session.add(row)
        session.flush()
        audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="shopify.authorization_started",
                     actor=principal, resource_type="connection_session", resource_id=row.id, data={"shop": shop, "scopes": scopes})
        result = _view(row)
        if row.status == "pending":
            result["authorization_url"] = shopify.authorize_url(shop, scopes, state)
        return result


def session_status(principal: Principal, session_id: str) -> dict:
    _browser(principal)
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.get(ConnectionSession, session_id)
        if row is None or row.provider != "shopify" or row.initiated_by != principal.actor_id:
            raise not_found("Shopify connection session")
        result = _view(row)
        if row.expires_at <= utcnow() and not row.consumed_at:
            result["status"] = "expired"
        return result


def callback(principal: Principal, parameters: list[tuple[str, str]]) -> dict:
    _browser(principal)
    params = shopify.verify_query(parameters, required=("state", "code"))
    shop = params["shop"]
    failure = None
    result = {}
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        row = session.scalar(select(ConnectionSession).where(ConnectionSession.state_hash == token_hash(params["state"])).with_for_update())
        if (row is None or row.provider != "shopify" or row.initiated_by != principal.actor_id or row.status != "pending"
                or row.extra.get("shop") != shop or row.extra.get("browser_session_id") != principal.session_id
                or row.expires_at <= utcnow() or row.consumed_at is not None):
            raise GluonsError("OAUTH_STATE_INVALID", "This Shopify authorization is expired, already used or belongs to another session.", status=400)
        row.consumed_at = utcnow()
        try:
            credential = shopify.exchange(shop, params["code"])
            identity = shopify.identity(shop, credential["access_token"])
            if not set(row.requested_scopes) <= set(identity["scopes"]):
                raise GluonsError("SCOPE_INSUFFICIENT", "Shopify did not grant the requested capabilities.", status=409)
            connection = store_connection(session, principal, shop=shop, identity=identity, credential=credential, distribution="approved_pilot_only")
            row.connection_id, row.status = connection.id, "authorized"
            result = _view(row)
        except (GluonsError, shopify.ProviderError) as exc:
            row.status = "failed"
            row.failure_reason = "Shopify authorization could not be verified. Restart the connection."
            failure = exc if isinstance(exc, GluonsError) else GluonsError("EXTERNAL_UNAVAILABLE", row.failure_reason, status=502)
    if failure:
        raise failure
    return result


def assert_shop_free(tenant_id: str, shop: str) -> None:
    """One store feeds one business: two would make every webhook and order ambiguous."""
    from gluons.db.session import system_tx
    with system_tx() as session:
        holders = session.scalars(select(Connection.tenant_id).where(
            Connection.provider == "shopify", Connection.disconnected_at.is_(None),
            Connection.metadata_json["shop"].as_string() == shop)).all()
    if any(holder != tenant_id for holder in holders):
        raise GluonsError("ACCOUNT_ALREADY_LINKED", "This store is connected to another Gluons business. Disconnect it there first.", status=409)


def store_connection(session: Any, principal: Principal, *, shop: str, identity: dict, credential: dict, distribution: str) -> Connection:
    """Shared by the pilot flow and the Shopify-initiated install. A reconnect replaces the
    token and asks the owner to confirm the store again."""
    assert_shop_free(principal.tenant_id, shop)
    connection = session.scalar(select(Connection).where(Connection.provider == "shopify", Connection.external_account_id == identity["id"],
                                                         Connection.disconnected_at.is_(None)).with_for_update())
    if connection is None:
        connection = Connection(id=new_id("con"), tenant_id=principal.tenant_id, provider="shopify", external_account_id=identity["id"],
                                account_display=identity["name"], created_by=principal.actor_id)
        session.add(connection)
    elif connection.credential_ref:
        vault.destroy(session, principal.tenant_id, connection.credential_ref)
    connection.credential_ref = vault.store(session, principal.tenant_id, "shopify", credential)
    connection.account_display = f"{identity['name']} ({shop})"
    connection.account_confirmed_at = None
    connection.account_confirmed_by = None
    connection.environment = "sandbox" if (identity.get("plan") or {}).get("partnerDevelopment") is True else "production"
    connection.granted_scopes = sorted(identity["scopes"])
    connection.status = "connected"
    connection.status_detail = "Confirm the displayed store before any automation uses it."
    connection.last_checked_at = utcnow()
    connection.token_expires_at = utcnow() + timedelta(seconds=int(credential["expires_in"]))
    connection.metadata_json = {"shop": shop, "currency": identity.get("currencyCode"), "app_distribution": distribution,
                                **shopify.token_metadata(credential)}
    session.flush()
    audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="shopify.authorized", actor=principal,
                 resource_type="connection", resource_id=connection.id,
                 data={"shop": shop, "scopes": sorted(identity["scopes"]), "environment": connection.environment, "distribution": distribution})
    return connection


def verify_connection(principal: Principal, connection_id: str) -> dict:
    principal.require("connections:manage")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        connection = _connection(session, connection_id)
        credential = shopify.get_credential(session, principal.tenant_id, connection)
        identity = shopify.identity(connection.metadata_json["shop"], credential["access_token"])
        if identity["id"] != connection.external_account_id:
            raise GluonsError("PROVIDER_IDENTITY_MISMATCH", "The Shopify account changed. Reconnect.", status=409)
        connection.granted_scopes = identity["scopes"]
        connection.last_checked_at = utcnow()
        return {"id": connection.id, "provider": "shopify", "status": connection.status, "account_display": connection.account_display,
                "scopes": connection.granted_scopes, "environment": connection.environment, "account_confirmed": connection.account_confirmed_at is not None}


def _connection(session, connection_id: str) -> Connection:  # type: ignore[no-untyped-def]
    connection = session.get(Connection, connection_id, with_for_update=True)
    if connection is None or connection.provider != "shopify":
        raise not_found("Shopify connection")
    return connection


def readiness(principal: Principal, plan_id: str, *, order_id: str | None = None) -> dict:
    principal.require("launch:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = session.get(LaunchPlan, plan_id)
        if plan is None:
            raise not_found("Business launch plan")
        connection_id = plan.state.get("store_connection_id")
        if not connection_id:
            return {"plan_id": plan.id, "store_connected": False, "payments": {"status": "unverified"}, "store_live": False, "missing": ["owner_store_connection"],
                    "readiness": readiness_projection.project(session, plan)}
        connection = _connection(session, connection_id)
        if not connection.account_confirmed_at:
            raise GluonsError("ACCOUNT_CONFIRMATION_REQUIRED", "Confirm the Shopify identity first.", status=409)
        credential = shopify.get_credential(session, principal.tenant_id, connection)
        token, shop = credential["access_token"], connection.metadata_json["shop"]
        snapshot = (plan.version, connection.id, connection.version, connection.external_account_id, connection.environment,
                    connection.status, connection.disconnected_at, connection.account_confirmed_at, connection.credential_ref,
                    tuple(sorted(connection.granted_scopes)), shop)

    # Provider reads run outside the tenant transaction. Token rotation above retains
    # its existing per-connection lock because refresh credentials are single-use.
    account = shopify.identity(shop, token)
    if account["id"] != snapshot[3]:
        raise GluonsError("PROVIDER_IDENTITY_MISMATCH", "The store identity changed.", status=409)
    granted = set(snapshot[9]) & set(account["scopes"])
    payments = shopify.payment_status(shop, token) if "read_shopify_payments_accounts" in granted else {"status": "permission_required", "kyc_verified": False, "live_payments_verified": False}
    order = shopify.order_status(shop, token, order_id) if order_id else None
    detail = {"store_id": account["id"], "environment": snapshot[4], "payments": payments, "order": order,
              "test_order_payment_observed": bool(order and order["is_test"] and order["payment_confirmed"]),
              "test_checkout_verified": False, "store_live": False,
              "notice": "An observed test order is not yet linked to the current plan and its checkout requirements.",
              "missing": ["owner_go_live_approval", "verified_shipping_and_tax_configuration", "publication_and_fulfillment_contracts"]}
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = session.get(LaunchPlan, plan_id, with_for_update=True)
        if plan is None:
            raise not_found("Business launch plan")
        connection = _connection(session, snapshot[1])
        current = (plan.version, connection.id, connection.version, connection.external_account_id, connection.environment,
                   connection.status, connection.disconnected_at, connection.account_confirmed_at, connection.credential_ref,
                   tuple(sorted(connection.granted_scopes)), connection.metadata_json.get("shop"))
        if current != snapshot or plan.state.get("store_connection_id") != connection.id:
            raise GluonsError("READINESS_CHANGED", "The plan or store changed during the check. Refresh and try again.", status=409)
        detail.update({"connection_id": connection.id, "plan_version": plan.version,
                       "plan_store_context": readiness_projection.plan_action_context(plan, connection),
                       "observed_scopes": sorted(granted)})
        session.add(LaunchEvidence(id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan.id, stage=plan.stage,
                                   kind="provider_readiness", is_test=connection.environment != "production", source="shopify", detail=detail))
        return {"plan_id": plan.id, "store_connected": True, **detail, "readiness": readiness_projection.project(session, plan)}


def _reserve_plan_action(principal: Principal, *, plan_id: str, action: str, automation_id: str, connection_id: str,
                         payload: dict, idempotency_key: str) -> gateway.ActionResult | None:
    """Persist one plan-bound intent before the gateway can execute a write."""
    spec = get_action(action)
    if spec is None:
        return gateway.ActionResult(status="denied", reasons=("ACTION_NOT_ALLOWLISTED",))
    try:
        normalized = spec.input_model.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        return gateway.ActionResult(status="failed", reasons=("INVALID_ACTION_INPUT",), warnings=(str(exc)[:500],))
    payload_hash = fingerprint({"action": action, "connection_id": connection_id, "input": normalized})
    key_hash = fingerprint({"action": action, "idempotency_key": idempotency_key})
    with operation_lock(f"launch-intent:{principal.tenant_id}:{key_hash}") as acquired:
        if not acquired:
            return gateway.ActionResult(status="retry", reasons=("CONCURRENT_ATTEMPT",), retry_after=2)
        with tenant_tx(principal.tenant_id, principal.user_id) as session:
            plan = session.get(LaunchPlan, plan_id, with_for_update=True)
            connection = _connection(session, connection_id)
            if plan is None:
                raise not_found("Business launch plan")
            if plan.branch_status in ("PAUSED", "CANCELLED") or plan.state.get("store_connection_id") != connection_id:
                raise GluonsError("PLAN_NOT_RUNNABLE", "The plan must be running and bound to this store.", status=409)
            context = readiness_projection.plan_action_context(plan, connection)
            intent = session.scalar(select(LaunchEvidence).where(LaunchEvidence.kind == "provider_action_intent",
                LaunchEvidence.detail["key_hash"].as_string() == key_hash))
            expected = {"action": action, "key_hash": key_hash, "payload_hash": payload_hash,
                        "connection_id": connection_id, "automation_id": automation_id, "context": context}
            if intent is not None:
                if intent.plan_id != plan_id or intent.detail != expected:
                    raise GluonsError("PLAN_ACTION_MISMATCH", "This action key is already bound to different plan details.", status=409)
                return None
            existing = session.scalar(select(ActionReceipt.id).where(ActionReceipt.action == action,
                                                                      ActionReceipt.idempotency_key == idempotency_key))
            if existing is not None:
                raise GluonsError("PLAN_ACTION_MISMATCH", "An existing unbound receipt cannot be attached to a plan.", status=409)
            session.add(LaunchEvidence(id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan.id, stage=plan.stage,
                kind="provider_action_intent", source="gluons", is_test=connection.environment != "production", detail=expected))
    return None


def _record_plan_result(principal: Principal, plan_id: str, result: gateway.ActionResult) -> gateway.ActionResult:
    if not result.verified or not result.receipt_id:
        return result
    try:
        record_provider_fact(principal, plan_id, result.receipt_id)
    except GluonsError:
        # The verified provider action cannot be undone by a later projection error.
        # The receipt is durable; surface the reconciliation requirement explicitly.
        return replace(result, warnings=(*result.warnings, "The provider action was verified, but plan evidence needs reconciliation."))
    return result


def stage_product(principal: Principal, *, automation_id: str, connection_id: str, payload: dict, idempotency_key: str, plan_id: str | None = None) -> gateway.ActionResult:
    principal.require("automations:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = session.get(Automation, automation_id)
        if automation is None:
            raise not_found("Automation")
        _connection(session, connection_id)
        if plan_id:
            principal.require("launch:write")
            plan = session.get(LaunchPlan, plan_id)
            if plan is None:
                raise not_found("Business launch plan")
            if plan.branch_status in ("PAUSED", "CANCELLED") or plan.state.get("store_connection_id") != connection_id:
                raise GluonsError("PLAN_NOT_RUNNABLE", "The plan must be running and bound to this store.", status=409)
        mode = automation.mode
    if plan_id:
        stop = _reserve_plan_action(principal, plan_id=plan_id, action="shopify.product.create_draft", automation_id=automation_id,
                                    connection_id=connection_id, payload=payload, idempotency_key=idempotency_key)
        if stop is not None:
            return stop
    result = gateway.execute(gateway.ActionRequest(tenant_id=principal.tenant_id, automation_id=automation_id, action="shopify.product.create_draft",
                            input=payload, idempotency_key=idempotency_key, mode=mode, actor=principal.actor_id, connection_id=connection_id,
                            skill_id="commerce_business_launch", skill_actions=("shopify.product.create_draft",)))
    return _record_plan_result(principal, plan_id, result) if plan_id else result


def execute_commerce_action(principal: Principal, *, action: str, automation_id: str, connection_id: str, payload: dict,
                            idempotency_key: str, plan_id: str | None = None) -> gateway.ActionResult:
    principal.require("launch:write")
    principal.require("automations:write")
    if action not in ("shopify.product.publish", "shopify.fulfillment.create"):
        raise validation_failed("This commerce action is not supported.")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        automation = session.get(Automation, automation_id)
        if automation is None:
            raise not_found("Automation")
        _connection(session, connection_id)
        if plan_id:
            plan = session.get(LaunchPlan, plan_id)
            if plan is None:
                raise not_found("Business launch plan")
            if plan.branch_status in ("PAUSED", "CANCELLED") or plan.state.get("store_connection_id") != connection_id:
                raise GluonsError("PLAN_NOT_RUNNABLE", "The plan must be running and bound to this store.", status=409)
        mode = automation.mode
    if plan_id:
        stop = _reserve_plan_action(principal, plan_id=plan_id, action=action, automation_id=automation_id,
                                    connection_id=connection_id, payload=payload, idempotency_key=idempotency_key)
        if stop is not None:
            return stop
    result = gateway.execute(gateway.ActionRequest(tenant_id=principal.tenant_id, automation_id=automation_id, action=action,
        input=payload, idempotency_key=idempotency_key, mode=mode, actor=principal.actor_id, connection_id=connection_id,
        skill_id="commerce_business_launch", skill_actions=(action,), title="Approve exact catalog publication" if action.endswith("publish") else "Approve verified-shipment fulfillment record"))
    return _record_plan_result(principal, plan_id, result) if plan_id else result


def record_provider_fact(principal: Principal, plan_id: str, receipt_id: str) -> dict:
    """Only authoritative gateway receipts can advance provider-evidence projections."""
    principal.require("launch:write")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = session.get(LaunchPlan, plan_id, with_for_update=True)
        receipt = session.get(ActionReceipt, receipt_id)
        if plan is None or receipt is None:
            raise not_found("Plan or receipt")
        allowed = {"shopify.product.create_draft": "staged_product_receipts", "shopify.product.publish": "published_product_receipts", "shopify.fulfillment.create": "fulfillment_receipts"}
        if (receipt.action not in allowed or not receipt.verified or receipt.status != "succeeded"
                or receipt.verified_at is None or not receipt.provider_object_id or receipt.readback is None
                or receipt.connection_id != plan.state.get("store_connection_id") or receipt.mode not in ("live", "supervised")):
            raise GluonsError("INSUFFICIENT_EVIDENCE", "A verified provider receipt for this plan's connected store is required.", status=409)
        connection = session.get(Connection, receipt.connection_id)
        key_hash = fingerprint({"action": receipt.action, "idempotency_key": receipt.idempotency_key})
        intent = session.scalar(select(LaunchEvidence).where(LaunchEvidence.plan_id == plan.id,
            LaunchEvidence.kind == "provider_action_intent", LaunchEvidence.detail["key_hash"].as_string() == key_hash))
        if (connection is None or intent is None or intent.detail.get("action") != receipt.action
                or intent.detail.get("payload_hash") != receipt.payload_hash or intent.detail.get("connection_id") != receipt.connection_id
                or intent.detail.get("automation_id") != receipt.automation_id or intent.detail.get("context") != readiness_projection.plan_action_context(plan, connection)
                or receipt.provider != "shopify" or receipt.environment != connection.environment):
            raise GluonsError("INSUFFICIENT_EVIDENCE", "The receipt is not bound to this plan and current store details.", status=409)
        if plan.branch_status in ("PAUSED", "CANCELLED"):
            raise GluonsError("PLAN_NOT_RUNNABLE", "Resume an uncancelled plan before recording new progress.", status=409)
        key = allowed[receipt.action]
        identifiers = list(plan.state.get(key, []))
        if receipt.id not in identifiers:
            identifiers.append(receipt.id)
            plan.state = {**plan.state, key: identifiers}
            plan.version += 1
            plan.updated_at = utcnow()
            session.add(LaunchEvidence(id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan.id, stage=plan.stage,
                kind="provider_action", source="shopify", receipt_id=receipt.id, is_test=receipt.environment != "production",
                detail={"action": receipt.action, "provider_object_id": receipt.provider_object_id, "readback": receipt.readback,
                        "whole_store_launch_verified": False, "kyc_verified": False, "physical_delivery_claimed": False}))
            audit.record(session, tenant_id=principal.tenant_id, category="business_launch", event_type="launch.provider_evidence_recorded",
                         actor=principal, resource_type="launch_plan", resource_id=plan.id, data={"receipt_id": receipt.id, "action": receipt.action})
        return {"plan_id": plan.id, "recorded_receipt_id": receipt.id, "state": plan.state, "version": plan.version,
                "whole_store_launch_verified": False, "kyc_verified": False}


def catalog_targets(principal: Principal, connection_id: str) -> dict:
    principal.require("launch:read")
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        connection = _connection(session, connection_id)
        credential = shopify.get_credential(session, principal.tenant_id, connection)
        if not {"read_products", "read_publications"} <= set(connection.granted_scopes):
            raise GluonsError("SCOPE_INSUFFICIENT", "Authorize catalog publication read capabilities first.", status=409)
        return shopify.graphql(connection.metadata_json["shop"], credential["access_token"],
            "query GluonsCatalogTargets { products(first: 50) { nodes { id title handle status } pageInfo { hasNextPage } } publications(first: 50) { nodes { id name } pageInfo { hasNextPage } } }")


def add_supplier(principal: Principal, *, plan_id: str, name: str, email: str, source_url: str) -> dict:
    principal.require("launch:write")
    if not name.strip() or len(name) > 300 or not source_url.startswith("https://") or len(source_url) > 2000:
        raise validation_failed("Provide a supplier name and dated HTTPS source reference.")
    from pydantic import EmailStr, TypeAdapter
    email = str(TypeAdapter(EmailStr).validate_python(email))
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        plan = session.get(LaunchPlan, plan_id)
        if plan is None:
            raise not_found("Business launch plan")
        if plan.branch_status in ("PAUSED", "CANCELLED"):
            raise GluonsError("PLAN_NOT_RUNNABLE", "Resume an uncancelled plan before supplier work.", status=409)
        supplier = Supplier(id=new_id("sup"), tenant_id=principal.tenant_id, plan_id=plan_id, name=name.strip(), contact_email=email,
                            source_url=source_url, evidence_date=utcnow(), reliability_note="Owner-supplied source; supplier reliability not independently verified.")
        session.add(supplier)
        session.flush()
        audit.record(session, tenant_id=principal.tenant_id, category="business_launch", event_type="supplier.added", actor=principal,
                     resource_type="supplier", resource_id=supplier.id, data={"plan_id": plan_id})
        return {"id": supplier.id, "name": supplier.name, "contact_email": supplier.contact_email, "status": supplier.status, "reliability": "unverified"}


def supplier_inquiry(principal: Principal, *, supplier_id: str, automation_id: str, connection_id: str,
                     subject: str, body: str, represented_entity: str, idempotency_key: str) -> gateway.ActionResult:
    principal.require("launch:write")
    from gluons.connectors.shopify import SupplierInquiry
    with tenant_tx(principal.tenant_id, principal.user_id) as session:
        supplier = session.get(Supplier, supplier_id)
        automation = session.get(Automation, automation_id)
        connection = session.get(Connection, connection_id)
        if supplier is None or automation is None or connection is None:
            raise not_found("Supplier, automation or connection")
        plan = session.get(LaunchPlan, supplier.plan_id)
        if not plan or plan.branch_status in ("PAUSED", "CANCELLED"):
            raise GluonsError("PLAN_NOT_RUNNABLE", "Resume an uncancelled plan before supplier outreach.", status=409)
        if connection.provider != "gmail":
            raise validation_failed("Supplier inquiries use an owner-confirmed Gmail connection.")
        payload = SupplierInquiry(to=supplier.contact_email, subject=subject, body=body, represented_entity=represented_entity).model_dump(mode="json")
        action = "commerce.supplier.inquiry"
        payload_hash = fingerprint({"action": action, "connection_id": connection_id, "input": payload})
        # First/novel contact always needs a precise owner decision, regardless of
        # an existing transactional Gmail grant. The gateway still independently
        # checks the signed automation policy and provider scopes before sending.
        found = session.scalar(select(Approval).where(Approval.action == action, Approval.payload_hash == payload_hash,
                               Approval.idempotency_key == idempotency_key).order_by(Approval.created_at.desc()).limit(1))
        if found is None:
            approval = approvals.request_approval(session, tenant_id=principal.tenant_id, kind="supplier_outreach", action=action, risk_tier="R2",
                title="Approve this supplier inquiry", payload_hash=payload_hash, idempotency_key=idempotency_key, automation_id=automation.id,
                connection_id=connection.id, records_after_preview=payload, required_role="owner", requested_by=principal.actor_id)
            return gateway.ActionResult(status="waiting_approval", approval_id=approval.id)
        if found.status != "approved" or found.expires_at <= utcnow():
            return gateway.ActionResult(status="denied" if found.status in ("denied", "expired", "cancelled") or found.expires_at <= utcnow() else "waiting_approval", approval_id=found.id)
        owner_decision = session.scalar(select(ApprovalDecision).where(ApprovalDecision.approval_id == found.id,
            ApprovalDecision.role == "owner", ApprovalDecision.via == "web", ApprovalDecision.decision == "approve"))
        if owner_decision is None:
            raise GluonsError("OWNER_WEB_REQUIRED", "The business owner must approve first supplier contact in Gluons Web.", status=403)
        mode = automation.mode
        approved_at, plan_id, is_test = owner_decision.decided_at, plan.id, connection.environment != "production"
    result = gateway.execute(gateway.ActionRequest(tenant_id=principal.tenant_id, automation_id=automation_id, action=action, input=payload,
                            idempotency_key=idempotency_key, mode=mode, actor=principal.actor_id, connection_id=connection_id,
                            skill_id="commerce_business_launch", skill_actions=(action,)))
    if result.verified and result.status == "success":
        with tenant_tx(principal.tenant_id, principal.user_id) as session:
            supplier = session.get(Supplier, supplier_id, with_for_update=True)
            supplier.first_contact_approved_at = supplier.first_contact_approved_at or approved_at
            supplier.status = "inquiry_sent"
            recorded = session.scalar(select(LaunchEvidence.id).where(LaunchEvidence.plan_id == plan_id, LaunchEvidence.receipt_id == result.receipt_id))
            if recorded is None:
                session.add(LaunchEvidence(id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan_id,
                    stage="SUPPLIER_OUTREACH_APPROVED", kind="supplier_inquiry", is_test=is_test, source="gmail", receipt_id=result.receipt_id,
                    detail={"supplier_id": supplier_id, "provider_message_id": result.provider_object_id, "verification": "Sent-mail readback only; no supplier acceptance or delivery claim"}))
    return result


def webhook(connection_id: str, raw: bytes, signature: str, event_id: str, topic: str, shop: str) -> dict:
    if len(raw) > 512 * 1024 or not re.fullmatch(r"[A-Za-z0-9-]{8,100}", event_id):
        raise validation_failed("Webhook size or event identifier is invalid.")
    secret = get_settings().shopify_client_secret
    if not secret:
        raise GluonsError("CONFIGURATION_REQUIRED", "Shopify webhook authentication is not configured.", status=503)
    expected = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(signature, expected):
        raise GluonsError("WEBHOOK_SIGNATURE_INVALID", "Shopify webhook signature is invalid.", status=401)
    from gluons.db.session import system_tx
    # The URL is a routing hint only. Provider proof below binds the payload to
    # the connection; unsigned topic/shop headers never authorize business state.
    with system_tx() as routing:
        tenant_id = routing.scalar(select(Connection.tenant_id).where(Connection.id == connection_id, Connection.provider == "shopify"))
    if not tenant_id:
        raise not_found("Webhook connection")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise validation_failed("Webhook is not JSON.") from exc
    if not isinstance(payload, dict):
        raise validation_failed("Webhook must contain an object.")
    with tenant_tx(tenant_id) as session:
        connection = _connection(session, connection_id)
        if shop != connection.metadata_json.get("shop") or connection.disconnected_at or connection.status != "connected":
            raise GluonsError("WEBHOOK_ACCOUNT_MISMATCH", "Webhook account is not connected.", status=409)
        digest = sha256_hex(raw)
        existing = session.scalar(select(WebhookInbox).where(WebhookInbox.provider == "shopify", WebhookInbox.event_id == f"{connection_id}:{digest}"))
        if existing:
            return {"status": "duplicate"}
        reused_delivery = session.scalar(select(WebhookInbox.id).where(WebhookInbox.provider == "shopify", WebhookInbox.connection_id == connection_id,
                                         WebhookInbox.payload["delivery_id"].as_string() == event_id))
        if reused_delivery:
            raise GluonsError("WEBHOOK_REPLAY_MISMATCH", "A delivery identifier was reused with different content.", status=409)
        credential = shopify.get_credential(session, tenant_id, connection)
        evidence = None
        if topic in ("orders/paid", "orders/updated", "orders/create"):
            gid = payload.get("admin_graphql_api_id", "")
            evidence = shopify.order_status(shop, credential["access_token"], gid)
        else:
            # Other events are intentionally quarantined. Signature verification
            # is not permission to publish, move funds, fulfill or uninstall.
            evidence = {"status": "requires_handler", "external_action_performed": False}
        inserted = session.execute(insert(WebhookInbox).values(id=new_id("whi"), tenant_id=tenant_id, provider="shopify",
            event_id=f"{connection_id}:{digest}", connection_id=connection_id, event_type=topic, payload_hash=digest,
            payload={"delivery_id": event_id, "provider_evidence": evidence}, status="verified_evidence", processed_at=utcnow()).on_conflict_do_nothing().returning(WebhookInbox.id)).first()
        connection.last_event_at = utcnow()
        return {"status": "recorded" if inserted else "duplicate", "evidence": evidence,
                "notice": "Evidence recorded; no supplier purchase, publication, refund or fulfillment was executed."}


APP_TOPICS = ("app/uninstalled", "shop/redact", "customers/redact", "customers/data_request")
ORDER_TOPICS = ("orders/paid", "orders/updated", "orders/create")


def _shop_connections(session: Any, shop: str) -> list[Connection]:
    return list(session.scalars(select(Connection).where(Connection.provider == "shopify",
                                                         Connection.metadata_json["shop"].as_string() == shop)))


def app_webhook(raw: bytes, signature: str, webhook_id: str, topic: str, shop_header: str) -> dict:
    """The app-level endpoint for subscriptions declared in the app configuration.

    Only the body is signed. Headers are routing hints, so compliance work is routed by the
    signed body, and each topic must have exactly its documented shape. That stops a genuine
    body from one topic being replayed under another, such as a data request as a shop erasure.
    """
    if len(raw) > 512 * 1024 or not re.fullmatch(r"[A-Za-z0-9-]{8,100}", webhook_id):
        raise validation_failed("Webhook size or delivery identifier is invalid.")
    secret = get_settings().shopify_client_secret
    if not secret:
        raise GluonsError("CONFIGURATION_REQUIRED", "Shopify webhook authentication is not configured.", status=503)
    expected = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(signature, expected):
        raise GluonsError("WEBHOOK_SIGNATURE_INVALID", "Shopify webhook signature is invalid.", status=401)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise validation_failed("Webhook is not JSON.") from exc
    if not isinstance(payload, dict):
        raise validation_failed("Webhook must contain an object.")
    from gluons.db.session import system_tx
    if topic in ORDER_TOPICS:
        shop = shopify.shop_domain(shop_header)
        with system_tx() as session:
            active = [c.id for c in _shop_connections(session, shop) if c.disconnected_at is None and c.status == "connected"]
        if len(active) != 1:
            return {"status": "ignored_unknown_store"}
        # Order evidence is only ever recorded after reading the order back from that store.
        return webhook(active[0], raw, signature, webhook_id, topic, shop)
    if topic not in APP_TOPICS:
        return {"status": "ignored_topic"}
    shop = _signed_shop(topic, payload)
    with system_tx() as session:
        inserted = session.execute(insert(WebhookInbox).values(
            id=new_id("whi"), provider="shopify_app", event_id=webhook_id, event_type=topic, payload_hash=sha256_hex(raw),
            payload={"shop": shop}, status="received").on_conflict_do_nothing(constraint="uq_webhook_provider_event").returning(WebhookInbox.id)).first()
        if inserted is None:
            return {"status": "duplicate"}
        connections = _shop_connections(session, shop)
        if topic == "app/uninstalled":
            outcome = _uninstalled(session, connections)
        elif topic == "shop/redact":
            outcome = _redact_shop(session, connections)
        elif topic == "customers/redact":
            outcome = _redact_orders(session, connections, payload.get("orders_to_redact") or [])
        else:
            outcome = _data_request(session, connections, payload)
        session.execute(WebhookInbox.__table__.update().where(WebhookInbox.id == inserted[0]).values(status="processed", processed_at=utcnow(),
                                                                                                    payload={"shop": shop, "outcome": outcome}))
    return {"status": "processed", "outcome": outcome}


# Each compliance body must carry its own fields and none of another topic's. A store erasure
# has exactly two fields, so no customer body can ever be replayed as one.
_SHAPES = {
    "shop/redact": ({"shop_id", "shop_domain"}, None),
    "customers/redact": ({"shop_id", "shop_domain", "customer", "orders_to_redact"}, {"orders_requested", "data_request"}),
    "customers/data_request": ({"shop_id", "shop_domain", "customer", "orders_requested", "data_request"}, {"orders_to_redact"}),
}


def _signed_shop(topic: str, payload: dict) -> str:
    if topic == "app/uninstalled":
        # The uninstall body is the shop resource itself.
        domain = payload.get("myshopify_domain")
        if not isinstance(payload.get("id"), int) or "shop_domain" in payload:
            raise validation_failed("This is not an uninstall payload.")
    else:
        required, foreign = _SHAPES[topic]
        keys = set(payload)
        if not required <= keys or (keys != required if foreign is None else bool(keys & foreign)):
            raise validation_failed("The webhook body does not match its topic.")
        domain = payload.get("shop_domain")
    return shopify.shop_domain(domain if isinstance(domain, str) else "")


def _uninstalled(session: Any, connections: list[Connection]) -> dict:
    """Shopify already revoked the token, so this only closes Gluons' side and tells the owner."""
    from gluons.db.models import Automation, ProviderEventSubscription
    closed = []
    for connection in connections:
        if connection.disconnected_at:
            continue
        if connection.credential_ref:
            vault.destroy(session, connection.tenant_id, connection.credential_ref)
            connection.credential_ref = None
        connection.status, connection.disconnected_at, connection.disconnected_by = "disconnected", utcnow(), "shopify_uninstall"
        connection.status_detail = "The Gluons app was removed in Shopify."
        connection.version += 1
        for automation in session.scalars(select(Automation).where(Automation.tenant_id == connection.tenant_id).with_for_update()):
            bound = set((automation.config.get("connections") or {}).values()) | set(automation.source_connections or [])
            if connection.id in bound and automation.status not in ("cancelled", "cancel_requested", "completed", "failed_terminal"):
                automation.status, automation.version = "waiting_external", automation.version + 1
        for subscription in session.scalars(select(ProviderEventSubscription).where(ProviderEventSubscription.connection_id == connection.id)):
            subscription.status = "disabled"
        audit.record(session, tenant_id=connection.tenant_id, category="permission", event_type="shopify.uninstalled", actor="shopify_webhook",
                     resource_type="connection", resource_id=connection.id)
        events.notify(session, connection.tenant_id, kind="connection_removed", severity="action", link="/connections",
                      title="Your Shopify store was disconnected", body="The Gluons app was removed in Shopify. Automations that used the store are paused.",
                      resource_type="connection", resource_id=connection.id)
        closed.append(connection.id)
    return {"connections_closed": len(closed)}


def _redact_shop(session: Any, connections: list[Connection]) -> dict:
    """Arrives 48 hours after uninstall. A store that was reinstalled since keeps its data."""
    redacted = 0
    for connection in connections:
        if connection.disconnected_at is None:
            continue
        removed = session.execute(delete(WebhookInbox).where(WebhookInbox.connection_id == connection.id)).rowcount
        connection.metadata_json = {"shop": connection.metadata_json.get("shop"), "redacted_at": utcnow().isoformat()}
        audit.record(session, tenant_id=connection.tenant_id, category="data", event_type="shopify.shop_redacted", actor="shopify_webhook",
                     resource_type="connection", resource_id=connection.id, data={"webhook_records_deleted": removed})
        redacted += 1
    return {"connections_redacted": redacted}


def _order_gids(values: list) -> set[str]:
    return {f"gid://shopify/Order/{value}" for value in values if isinstance(value, int) and value > 0}


def _redact_orders(session: Any, connections: list[Connection], order_ids: list) -> dict:
    """Gluons imports no Shopify customer profiles. It keeps order status evidence, which goes."""
    gids, removed = _order_gids(order_ids), 0
    for connection in connections:
        for row in session.scalars(select(WebhookInbox).where(WebhookInbox.connection_id == connection.id, WebhookInbox.provider == "shopify")):
            record = ((row.payload or {}).get("provider_evidence") or {}).get("provider_record") or {}
            if record.get("id") in gids:
                session.delete(row)
                removed += 1
        audit.record(session, tenant_id=connection.tenant_id, category="data", event_type="shopify.customer_redacted", actor="shopify_webhook",
                     resource_type="connection", resource_id=connection.id, data={"order_records_deleted": removed})
    return {"order_records_deleted": removed, "customer_profiles_held": 0}


def _data_request(session: Any, connections: list[Connection], payload: dict) -> dict:
    """The store owner must answer the customer. Gluons tells them what it holds for these orders."""
    gids, held = _order_gids(payload.get("orders_requested") or []), 0
    for connection in connections:
        for row in session.scalars(select(WebhookInbox).where(WebhookInbox.connection_id == connection.id, WebhookInbox.provider == "shopify")):
            record = ((row.payload or {}).get("provider_evidence") or {}).get("provider_record") or {}
            held += record.get("id") in gids
        audit.record(session, tenant_id=connection.tenant_id, category="data", event_type="shopify.customer_data_requested", actor="shopify_webhook",
                     resource_type="connection", resource_id=connection.id, data={"order_records_held": held})
        events.notify(session, connection.tenant_id, kind="shopify_data_request", severity="action", link="/data",
                      title="A Shopify customer asked for their data",
                      body=f"Gluons holds order status records for {held} of the requested orders and no customer profile. Export them from Your data to answer.",
                      resource_type="connection", resource_id=connection.id)
    return {"order_records_held": held, "customer_profiles_held": 0}
