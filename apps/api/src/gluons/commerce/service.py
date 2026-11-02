"""Durable merchant consent and commerce evidence. No click completes KYC or shipping."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from gluons import approvals, audit, gateway, vault
from gluons.authz import Principal
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
from gluons.db.session import tenant_tx

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
            result["authorization_url"] = f"https://{shop}/admin/oauth/authorize?" + urlencode({
                "client_id": get_settings().shopify_client_id, "scope": ",".join(scopes), "state": state,
                "redirect_uri": f"{get_settings().public_api_url.rstrip('/')}/v1/commerce/shopify/callback",
            })
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
    params = dict(parameters)
    if len(parameters) != len(params) or not all(params.get(key) for key in ("shop", "state", "code", "hmac", "timestamp")):
        raise GluonsError("OAUTH_CALLBACK_INVALID", "Shopify authorization response is invalid.", status=400)
    shop = shopify.shop_domain(params["shop"])
    secret = get_settings().shopify_client_secret
    if not secret:
        raise GluonsError("CONFIGURATION_REQUIRED", "Configure the Shopify app credentials.", status=409)
    message = "&".join(f"{key}={value}" for key, value in sorted(params.items()) if key != "hmac")
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, params["hmac"]):
        raise GluonsError("OAUTH_SIGNATURE_INVALID", "Shopify authorization signature is invalid.", status=400)
    try:
        valid_time = abs(utcnow().timestamp() - int(params["timestamp"])) <= 300
    except (ValueError, OverflowError):
        valid_time = False
    if not valid_time:
        raise GluonsError("OAUTH_CALLBACK_EXPIRED", "Restart Shopify authorization.", status=400)
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
            scopes = set(identity["scopes"])
            if not set(row.requested_scopes) <= scopes:
                raise GluonsError("SCOPE_INSUFFICIENT", "Shopify did not grant the requested capabilities.", status=409)
            connection = session.scalar(select(Connection).where(Connection.provider == "shopify", Connection.external_account_id == identity["id"], Connection.disconnected_at.is_(None)).with_for_update())
            if connection is None:
                connection = Connection(id=new_id("con"), tenant_id=principal.tenant_id, provider="shopify", external_account_id=identity["id"], account_display=identity["name"], created_by=principal.actor_id)
                session.add(connection)
            elif connection.credential_ref:
                vault.destroy(session, principal.tenant_id, connection.credential_ref)
            connection.credential_ref = vault.store(session, principal.tenant_id, "shopify", credential)
            connection.account_display = f"{identity['name']} ({shop})"
            connection.account_confirmed_at = None
            connection.account_confirmed_by = None
            connection.environment = "sandbox" if (identity.get("plan") or {}).get("partnerDevelopment") is True else "production"
            connection.granted_scopes = sorted(scopes)
            connection.status = "connected"
            connection.last_checked_at = utcnow()
            connection.token_expires_at = utcnow() + timedelta(seconds=int(credential["expires_in"]))
            connection.metadata_json = {"shop": shop, "currency": identity.get("currencyCode"), "app_distribution": "approved_pilot_only"}
            session.flush()
            row.connection_id, row.status = connection.id, "authorized"
            audit.record(session, tenant_id=principal.tenant_id, category="permission", event_type="shopify.authorized", actor=principal,
                         resource_type="connection", resource_id=connection.id, data={"shop": shop, "scopes": sorted(scopes), "environment": connection.environment})
            result = _view(row)
        except (GluonsError, shopify.ProviderError) as exc:
            row.status = "failed"
            row.failure_reason = "Shopify authorization could not be verified. Restart the connection."
            failure = exc if isinstance(exc, GluonsError) else GluonsError("EXTERNAL_UNAVAILABLE", row.failure_reason, status=502)
    if failure:
        raise failure
    return result


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
            return {"plan_id": plan.id, "store_connected": False, "payments": {"status": "unverified"}, "store_live": False, "missing": ["owner_store_connection"]}
        connection = _connection(session, connection_id)
        if not connection.account_confirmed_at:
            raise GluonsError("ACCOUNT_CONFIRMATION_REQUIRED", "Confirm the Shopify identity first.", status=409)
        credential = shopify.get_credential(session, principal.tenant_id, connection)
        token, shop = credential["access_token"], connection.metadata_json["shop"]
        account = shopify.identity(shop, token)
        if account["id"] != connection.external_account_id:
            raise GluonsError("PROVIDER_IDENTITY_MISMATCH", "The store identity changed.", status=409)
        payments = shopify.payment_status(shop, token) if "read_shopify_payments_accounts" in connection.granted_scopes else {"status": "permission_required", "kyc_verified": False, "live_payments_verified": False}
        order = shopify.order_status(shop, token, order_id) if order_id else None
        detail = {"store_id": account["id"], "environment": connection.environment, "payments": payments, "order": order,
                  "test_checkout_verified": bool(order and order["is_test"] and order["payment_confirmed"]), "store_live": False,
                  "missing": ["owner_go_live_approval", "verified_shipping_and_tax_configuration", "publication_and_fulfillment_contracts"]}
        session.add(LaunchEvidence(id=new_id("lev"), tenant_id=principal.tenant_id, plan_id=plan.id, stage=plan.stage,
                                   kind="provider_readiness", is_test=connection.environment != "production", source="shopify", detail=detail))
        return {"plan_id": plan.id, "store_connected": True, **detail}


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
    result = gateway.execute(gateway.ActionRequest(tenant_id=principal.tenant_id, automation_id=automation_id, action="shopify.product.create_draft",
                            input=payload, idempotency_key=idempotency_key, mode=mode, actor=principal.actor_id, connection_id=connection_id,
                            skill_id="commerce_business_launch", skill_actions=("shopify.product.create_draft",)))
    if plan_id and result.verified:
        record_provider_fact(principal, plan_id, result.receipt_id)
    return result


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
    result = gateway.execute(gateway.ActionRequest(tenant_id=principal.tenant_id, automation_id=automation_id, action=action,
        input=payload, idempotency_key=idempotency_key, mode=mode, actor=principal.actor_id, connection_id=connection_id,
        skill_id="commerce_business_launch", skill_actions=(action,), title="Approve exact catalog publication" if action.endswith("publish") else "Approve verified-shipment fulfillment record"))
    if plan_id and result.verified:
        record_provider_fact(principal, plan_id, result.receipt_id)
    return result


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
                or receipt.connection_id != plan.state.get("store_connection_id") or receipt.mode in ("shadow", "historical_replay")):
            raise GluonsError("INSUFFICIENT_EVIDENCE", "A verified provider receipt for this plan's connected store is required.", status=409)
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
