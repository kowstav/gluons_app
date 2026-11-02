"""The single path to any external side effect.

Sequence for one action:

  1. Validate the payload against the action's typed schema.
  2. Decide (policy engine) and write a pending receipt in one transaction. The receipt
     is the write-ahead record: if the process dies after the provider call, the next
     attempt finds it and looks the write up instead of repeating it.
  3. Call the provider through a backend, with a write permit open for that call only.
  4. Read the source of truth back. Only a matching readback sets `verified`.

Shadow and replay stop at step 2 with a preview. No write permit is ever opened there,
so even a faulty connector cannot reach a provider with a write.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gluons import approvals, audit, billing, events, run_log, vault
from gluons.actions import (
    ActionConnector,
    ActionSpec,
    ConnectorContext,
    ProviderError,
    ProviderResult,
    Readback,
    get_action,
    get_connector,
)
from gluons.backends import get_backend
from gluons.core import GluonsError, fingerprint, new_id, utcnow
from gluons.db.models import (
    ActionAuthorization,
    ActionReceipt,
    Approval,
    Automation,
    Connection,
    Incident,
)
from gluons.db.session import operation_lock, tenant_tx
from gluons.net import write_permit
from gluons.policy import Decision, Outcome, PolicyInput, deny, evaluate


@dataclass(frozen=True)
class ActionRequest:
    tenant_id: str
    action: str
    input: dict[str, Any]
    idempotency_key: str
    mode: str
    actor: str = "runtime"
    automation_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    node_id: str | None = None
    skill_id: str | None = None
    connection_id: str | None = None
    pinned_policy_id: str | None = None
    skill_actions: tuple[str, ...] | None = None
    title: str | None = None
    summary: str = ""
    source_evidence: tuple[Any, ...] = ()
    resources: dict[str, Any] = field(default_factory=dict)
    # Lets tests point a connector at a local provider double.
    transport: Any = None


@dataclass(frozen=True)
class ActionResult:
    # preview | waiting_approval | denied | success | retry | waiting_external | failed | mismatch | uncertain
    status: str
    verified: bool = False
    provider_object_id: str | None = None
    receipt: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] = field(default_factory=dict)
    receipt_id: str | None = None
    approval_id: str | None = None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    retry_after: float | None = None
    duplicate: bool = False


@dataclass
class _Plan:
    """What the first transaction decided. Provider calls happen after it commits."""

    result: ActionResult | None = None
    preview: bool = False
    connector: ActionConnector | None = None
    backend: str = "python-actions"
    policy_id: str | None = None
    receipt_id: str | None = None
    must_lookup: bool = False
    lookup_only: bool = False
    verify_only: ProviderResult | None = None


def _payload_hash(action: str, connection_id: str | None, payload: dict[str, Any]) -> str:
    return fingerprint({"action": action, "connection_id": connection_id, "input": payload})


def _load_receipt(session: Session, request: ActionRequest, *, lock: bool = False) -> ActionReceipt | None:
    query = select(ActionReceipt).where(
        ActionReceipt.tenant_id == request.tenant_id,
        ActionReceipt.action == request.action,
        ActionReceipt.idempotency_key == request.idempotency_key,
    )
    if lock:
        query = query.with_for_update()
    return session.execute(query).scalar_one_or_none()


def _result_from_receipt(receipt: ActionReceipt, *, duplicate: bool = False) -> ActionResult:
    return ActionResult(
        status="success" if receipt.status == "succeeded" else receipt.status,
        verified=receipt.verified,
        provider_object_id=receipt.provider_object_id,
        receipt=receipt.receipt,
        receipt_id=receipt.id,
        warnings=tuple(receipt.warnings),
        duplicate=duplicate,
    )


def _log_authorization(
    session: Session,
    request: ActionRequest,
    decision: Decision,
    payload_hash: str,
    amount: int | None,
    approval_id: str | None = None,
) -> None:
    session.add(
        ActionAuthorization(
            id=new_id("auz"),
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            automation_id=request.automation_id,
            action=request.action,
            mode=request.mode,
            outcome=decision.outcome.value,
            reasons=list(decision.reasons),
            policy_id=decision.policy_id,
            policy_version=decision.policy_version,
            approval_id=approval_id,
            idempotency_key=request.idempotency_key,
            payload_hash=payload_hash,
            connection_id=request.connection_id,
            actor=request.actor,
            amount_minor=amount,
        )
    )
    audit.record(
        session,
        tenant_id=request.tenant_id,
        category="action",
        event_type="action.authorization",
        actor=request.actor,
        resource_type="action",
        resource_id=request.idempotency_key,
        decision=decision.outcome.value,
        data={
            "action": request.action,
            "reasons": list(decision.reasons),
            "policy_id": decision.policy_id,
            "policy_version": decision.policy_version,
            "payload_hash": payload_hash,
            "amount_minor": amount,
            "run_id": request.run_id,
            "mode": request.mode,
        },
    )


def _envelope(request: ActionRequest, payload_hash: str, state: str, policy_id: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "tenant_id": request.tenant_id,
        "run_id": request.run_id,
        "skill_id": request.skill_id,
        "action": request.action,
        "mode": request.mode,
        "policy_id": policy_id,
        "idempotency_key": request.idempotency_key,
        "input_hash": payload_hash,
        "state": state,
        "provider_receipt": extra.pop("provider_receipt", None),
        "verified": extra.pop("verified", False),
        **extra,
    }


def _find_decided_approval(session: Session, request: ActionRequest, payload_hash: str) -> Approval | None:
    return session.execute(
        select(Approval)
        .where(
            Approval.tenant_id == request.tenant_id,
            Approval.payload_hash == payload_hash,
            Approval.run_id == request.run_id,
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()


def _resolve_approval(
    session: Session,
    request: ActionRequest,
    spec: ActionSpec,
    decision: Decision,
    payload_dump: dict[str, Any],
    payload_hash: str,
    amount: int | None,
) -> tuple[ActionResult | None, str | None]:
    """Returns (stop result, approval id). A stop result means this attempt ends here."""
    found = _find_decided_approval(session, request, payload_hash)
    # A failed decision rolls its transaction back. Check the clock here as well,
    # instead of relying on the asynchronous expiry sweep to change the status.
    if found is not None and found.expires_at <= utcnow() and found.consumed_at is None:
        found.status = "expired"
        found.version += 1
    if found is not None and found.status in ("denied", "expired", "cancelled"):
        outcome = deny(f"APPROVAL_{found.status.upper()}")
        _log_authorization(session, request, outcome, payload_hash, amount, found.id)
        return ActionResult(status="denied", reasons=outcome.reasons, approval_id=found.id), None
    if found is not None and found.status == "approved":
        # An approval covers one write. Only a retry of that same write may reuse it.
        if found.idempotency_key != request.idempotency_key:
            outcome = deny("APPROVAL_ALREADY_USED")
            _log_authorization(session, request, outcome, payload_hash, amount, found.id)
            return ActionResult(status="denied", reasons=outcome.reasons, approval_id=found.id), None
        if found.consumed_at is None:
            approvals.consume(session, found, payload_hash)
        return None, found.id
    connection = session.get(Connection, request.connection_id) if request.connection_id else None
    pending = found or approvals.request_approval(
        session,
        tenant_id=request.tenant_id,
        action=request.action,
        risk_tier=spec.risk_tier,
        title=request.title or spec.description,
        summary=request.summary,
        payload_hash=payload_hash,
        run_id=request.run_id,
        automation_id=request.automation_id,
        node_id=request.node_id,
        external_system=connection.account_display if connection else None,
        connection_id=request.connection_id,
        idempotency_key=request.idempotency_key,
        records_after_preview=payload_dump,
        source_evidence=list(request.source_evidence),
        amount_minor=amount,
        policy_reference=f"{decision.policy_id}:v{decision.policy_version}",
        required_role=decision.required_role,
        required_approvals=decision.required_approvals,
    )
    _log_authorization(session, request, decision, payload_hash, amount, pending.id)
    if request.run_id:
        run_log.append(
            session,
            request.tenant_id,
            request.run_id,
            "action.waiting_approval",
            _envelope(request, payload_hash, "waiting_for_approval", decision.policy_id, approval_id=pending.id),
        )
    return ActionResult(status="waiting_approval", approval_id=pending.id, reasons=decision.reasons), None


def _write_pending(
    session: Session,
    request: ActionRequest,
    spec: ActionSpec,
    payload_hash: str,
    decision: Decision,
    connection: Connection,
    amount: int | None,
    approval_id: str | None,
    expected: dict[str, Any],
) -> str | None:
    """Returns the receipt id, or None when another attempt holds this idempotency key."""
    receipt_id = new_id("rcp")
    inserted = session.execute(
        insert(ActionReceipt)
        .values(
            id=receipt_id,
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            step_id=request.step_id,
            automation_id=request.automation_id,
            skill_id=request.skill_id,
            action=request.action,
            mode=request.mode,
            environment=connection.environment,
            idempotency_key=request.idempotency_key,
            payload_hash=payload_hash,
            connection_id=request.connection_id,
            provider=connection.provider,
            status="pending",
            expected=expected,
            approval_id=approval_id,
            policy_id=decision.policy_id,
            amount_minor=amount,
            irreversible=spec.irreversible,
        )
        .on_conflict_do_nothing(constraint="uq_action_idempotency")
        .returning(ActionReceipt.id)
    ).first()
    return receipt_id if inserted is not None else None


def _decide(
    session: Session,
    request: ActionRequest,
    spec: ActionSpec,
    payload_dump: dict[str, Any],
    payload_hash: str,
    amount: int | None,
    resources: dict[str, Any],
) -> _Plan:
    existing = _load_receipt(session, request, lock=True)
    if existing is not None and existing.payload_hash != payload_hash:
        return _Plan(result=ActionResult(status="denied", reasons=("IDEMPOTENCY_PAYLOAD_MISMATCH",)))
    if existing is not None and (existing.status == "mismatch" or (existing.status == "succeeded" and existing.verified)):
        return _Plan(result=_result_from_receipt(existing, duplicate=True))

    connection = session.get(Connection, request.connection_id) if request.connection_id else None
    connector = get_connector(connection.provider, request.action) if connection else None

    # A write that already landed is always confirmed, whatever has changed since. Reading
    # the source of truth is how Gluons reports honestly; it is not a new side effect.
    if existing is not None and existing.status == "succeeded":
        return _Plan(
            connector=connector,
            policy_id=existing.policy_id,
            receipt_id=existing.id,
            verify_only=ProviderResult(
                existing.provider_object_id, existing.receipt or {}, existing.provider_request_id
            ),
        )

    decision = evaluate(
        session,
        PolicyInput(
            tenant_id=request.tenant_id,
            automation_id=request.automation_id,
            mode=request.mode,
            action=spec,
            connection_id=request.connection_id,
            amount_minor=amount,
            resources=resources,
            pinned_policy_id=request.pinned_policy_id,
            skill_actions=request.skill_actions,
        ),
    )
    in_doubt = existing is not None and existing.status in ("pending", "uncertain")

    if decision.outcome == Outcome.DENY:
        _log_authorization(session, request, decision, payload_hash, amount)
        if request.run_id:
            run_log.append(
                session,
                request.tenant_id,
                request.run_id,
                "action.denied",
                _envelope(request, payload_hash, "denied", decision.policy_id, reasons=list(decision.reasons)),
            )
        if in_doubt and connector is not None:
            # New writes are refused, but an earlier attempt may have landed: find out.
            return _Plan(
                result=ActionResult(status="denied", reasons=decision.reasons),
                connector=connector,
                policy_id=existing.policy_id,
                receipt_id=existing.id,
                must_lookup=True,
                lookup_only=True,
            )
        return _Plan(result=ActionResult(status="denied", reasons=decision.reasons))

    if decision.outcome == Outcome.PREVIEW:
        _log_authorization(session, request, decision, payload_hash, amount)
        return _Plan(preview=True)

    approval_id: str | None = None
    if decision.outcome == Outcome.REQUIRE_APPROVAL and not in_doubt:
        stop, approval_id = _resolve_approval(session, request, spec, decision, payload_dump, payload_hash, amount)
        if stop is not None:
            return _Plan(result=stop)

    if connector is None:
        outcome = deny("NO_CONNECTOR_FOR_PROVIDER")
        _log_authorization(session, request, outcome, payload_hash, amount)
        return _Plan(result=ActionResult(status="denied", reasons=outcome.reasons))

    automation = session.get(Automation, request.automation_id) if request.automation_id else None
    granted = Decision(Outcome.ALLOW, decision.reasons, decision.policy_id, decision.policy_version)
    _log_authorization(session, request, granted, payload_hash, amount, approval_id)

    if existing is None:
        receipt_id = _write_pending(
            session, request, spec, payload_hash, decision, connection, amount, approval_id, payload_dump
        )
        if receipt_id is None:
            return _Plan(result=ActionResult(status="retry", reasons=("CONCURRENT_ATTEMPT",), retry_after=2))
    else:
        receipt_id = existing.id
        existing.status = "pending"
        existing.attempts += 1
        existing.approval_id = approval_id or existing.approval_id
        existing.policy_id = decision.policy_id
        existing.amount_minor = amount
        existing.mode = request.mode
        existing.environment = connection.environment
        existing.provider = connection.provider
        existing.updated_at = utcnow()
    return _Plan(
        connector=connector,
        backend=automation.execution_backend if automation else "python-actions",
        policy_id=decision.policy_id,
        receipt_id=receipt_id,
        must_lookup=in_doubt,
    )


def _connector_context(request: ActionRequest) -> ConnectorContext:
    connection: Connection | None = None
    if request.connection_id:
        with tenant_tx(request.tenant_id) as session:
            connection = session.get(Connection, request.connection_id)

    def credentials() -> dict[str, Any]:
        if connection is None or connection.credential_ref is None:
            raise ProviderError("auth", "no stored credential for this connection")
        with tenant_tx(request.tenant_id) as session:
            current = session.get(Connection, connection.id, with_for_update=True)
            if current is None or current.disconnected_at or current.status != "connected":
                raise ProviderError("auth", "this connection has been disconnected or needs reauthorization")
            if current.provider == "gmail":
                from gluons.connections.service import get_credential
                try:
                    return get_credential(session, request.tenant_id, current)
                except GluonsError as exc:
                    kind = "retryable" if exc.code == "EXTERNAL_UNAVAILABLE" else "auth"
                    raise ProviderError(kind, exc.message) from exc
            return vault.read(session, request.tenant_id, current.credential_ref)

    return ConnectorContext(
        tenant_id=request.tenant_id,
        connection_id=connection.id if connection else None,
        provider=connection.provider if connection else None,
        environment=connection.environment if connection else "none",
        external_account_id=connection.external_account_id if connection else None,
        metadata=dict(connection.metadata_json) if connection else {},
        credentials=credentials,
        transport=request.transport,
    )


def _preview(
    request: ActionRequest, spec: ActionSpec, payload: BaseModel, payload_dump: dict[str, Any], payload_hash: str
) -> ActionResult:
    """Never opens a write permit. A connector's own preview may read, and nothing else."""
    context = _connector_context(request)
    connector = get_connector(context.provider, request.action) if context.provider else None
    preview: dict[str, Any] = {"would_perform": request.action, "input": payload_dump}
    warnings: list[str] = []
    if connector is not None:
        try:
            preview = connector.prepare(context, payload)
        except ProviderError as exc:
            warnings.append(f"Preview could not reach the provider: {exc}")
    with tenant_tx(request.tenant_id) as session:
        if _load_receipt(session, request) is None:
            session.add(
                ActionReceipt(
                    id=new_id("rcp"),
                    tenant_id=request.tenant_id,
                    run_id=request.run_id,
                    step_id=request.step_id,
                    automation_id=request.automation_id,
                    skill_id=request.skill_id,
                    action=request.action,
                    mode=request.mode,
                    environment="none",
                    idempotency_key=request.idempotency_key,
                    payload_hash=payload_hash,
                    connection_id=request.connection_id,
                    provider=context.provider,
                    status="preview",
                    receipt={"preview": preview},
                    irreversible=spec.irreversible,
                )
            )
        if request.run_id:
            run_log.append(
                session,
                request.tenant_id,
                request.run_id,
                "action.previewed",
                _envelope(request, payload_hash, "preview", None),
            )
    return ActionResult(status="preview", preview=preview, warnings=tuple(warnings))


def _update_receipt(request: ActionRequest, receipt_id: str, **values: Any) -> None:
    with tenant_tx(request.tenant_id) as session:
        receipt = session.get(ActionReceipt, receipt_id, with_for_update=True)
        for key, value in values.items():
            setattr(receipt, key, value)
        receipt.updated_at = utcnow()


def _mark_connection_needs_reauth(request: ActionRequest, detail: str) -> None:
    with tenant_tx(request.tenant_id) as session:
        connection = session.get(Connection, request.connection_id, with_for_update=True)
        if connection is not None and connection.status == "connected":
            connection.status = "needs_reauth"
            connection.status_detail = detail
            events.notify(
                session,
                request.tenant_id,
                kind="connection_needs_reauth",
                title=f"Reconnect {connection.account_display}",
                body="Gluons paused work that depends on this account until you reconnect it.",
                severity="warning",
                link="/connections",
                resource_type="connection",
                resource_id=connection.id,
            )


def _finish_success(
    request: ActionRequest,
    payload_hash: str,
    policy_id: str | None,
    receipt_id: str,
    result: ProviderResult,
    readback: Readback,
    latency_ms: int,
) -> ActionResult:
    verified = readback.found and readback.matches
    status = "succeeded" if verified else "mismatch"
    with tenant_tx(request.tenant_id) as session:
        receipt = session.get(ActionReceipt, receipt_id, with_for_update=True)
        receipt.status = status
        receipt.verified = verified
        receipt.verified_at = utcnow() if verified else None
        receipt.provider_object_id = result.provider_object_id
        receipt.provider_request_id = result.request_id
        receipt.receipt = result.receipt
        receipt.readback = readback.record
        receipt.warnings = list(result.warnings) + list(readback.mismatches)
        receipt.latency_ms = latency_ms
        receipt.cost_usd = result.cost_usd
        receipt.updated_at = utcnow()
        if result.cost_usd is not None:
            billing.record_usage(
                session,
                tenant_id=request.tenant_id,
                dedupe_key=f"action:{receipt_id}",
                category="external_action",
                provider=receipt.provider or "unknown",
                meter=request.action,
                quantity=1,
                cost_usd=result.cost_usd,
                cost_basis="provider_reported",
                automation_id=request.automation_id,
                run_id=request.run_id,
                step_id=request.step_id,
                skill_id=request.skill_id,
                latency_ms=latency_ms,
            )
        audit.record(
            session,
            tenant_id=request.tenant_id,
            category="action",
            event_type="action.executed",
            actor=request.actor,
            resource_type="action_receipt",
            resource_id=receipt_id,
            decision=status,
            data={
                "action": request.action,
                "provider": receipt.provider,
                "environment": receipt.environment,
                "provider_object_id": result.provider_object_id,
                "verified": verified,
                "mismatches": list(readback.mismatches),
            },
        )
        if not verified:
            session.add(
                Incident(
                    id=new_id("inc"),
                    tenant_id=request.tenant_id,
                    kind="readback_mismatch",
                    severity="critical",
                    run_id=request.run_id,
                    automation_id=request.automation_id,
                    summary=f"{request.action} did not match the source of truth after it was written.",
                    diagnosis={"mismatches": list(readback.mismatches), "receipt_id": receipt_id},
                )
            )
        if request.run_id:
            run_log.append(
                session,
                request.tenant_id,
                request.run_id,
                "action.executed",
                _envelope(
                    request,
                    payload_hash,
                    status,
                    policy_id,
                    provider_receipt=result.request_id or result.provider_object_id,
                    verified=verified,
                ),
            )
        events.emit(
            session,
            request.tenant_id,
            "action.completed",
            {"receipt_id": receipt_id, "action": request.action, "verified": verified, "run_id": request.run_id},
        )
    if verified:
        return ActionResult(
            status="success",
            verified=True,
            provider_object_id=result.provider_object_id,
            receipt=result.receipt,
            receipt_id=receipt_id,
            warnings=result.warnings,
        )
    return ActionResult(
        status="mismatch",
        provider_object_id=result.provider_object_id,
        receipt=result.receipt,
        receipt_id=receipt_id,
        reasons=("READBACK_MISMATCH",),
        warnings=readback.mismatches,
    )


def _handle_provider_error(request: ActionRequest, receipt_id: str, error: ProviderError) -> ActionResult:
    if error.kind == "ambiguous":
        _update_receipt(request, receipt_id, status="uncertain", warnings=[str(error)])
        return ActionResult(
            status="retry", receipt_id=receipt_id, reasons=("OUTCOME_UNCERTAIN",), retry_after=error.retry_after
        )
    if error.kind == "retryable":
        # Rate limited or unavailable before processing: the write did not land.
        _update_receipt(request, receipt_id, status="not_applied", warnings=[str(error)])
        return ActionResult(
            status="retry", receipt_id=receipt_id, reasons=("PROVIDER_UNAVAILABLE",), retry_after=error.retry_after
        )
    if error.kind == "auth":
        _update_receipt(request, receipt_id, status="not_applied", warnings=[str(error)])
        _mark_connection_needs_reauth(request, str(error))
        return ActionResult(status="waiting_external", receipt_id=receipt_id, reasons=("CONNECTION_REAUTH_REQUIRED",))
    _update_receipt(request, receipt_id, status="failed", warnings=[str(error)])
    return ActionResult(status="failed", receipt_id=receipt_id, reasons=("PROVIDER_REJECTED",), warnings=(str(error),))


def _perform(
    request: ActionRequest,
    connector: ActionConnector,
    action_backend: str,
    payload: BaseModel,
    payload_hash: str,
    policy_id: str | None,
    receipt_id: str,
    *,
    must_lookup: bool,
    known: ProviderResult | None = None,
) -> ActionResult:
    """`known` skips the write: the provider object already exists and only needs confirming."""
    context = _connector_context(request)
    started = time.monotonic()
    result = known
    try:
        if result is None and must_lookup:
            result = connector.lookup(context, payload, request.idempotency_key)
        if result is None:
            backend = get_backend(action_backend)
            with write_permit(request.idempotency_key):
                result = backend.submit(connector, context, request.action, payload, request.idempotency_key)
    except ProviderError as error:
        return _handle_provider_error(request, receipt_id, error)
    latency_ms = int((time.monotonic() - started) * 1000)
    _update_receipt(
        request,
        receipt_id,
        status="succeeded",
        provider_object_id=result.provider_object_id,
        provider_request_id=result.request_id,
        receipt=result.receipt,
        latency_ms=latency_ms,
    )
    try:
        readback = connector.verify(context, payload, result)
    except ProviderError as error:
        # The write landed but cannot be confirmed yet. Retry the read, never the write.
        return ActionResult(
            status="retry",
            provider_object_id=result.provider_object_id,
            receipt_id=receipt_id,
            reasons=("VERIFICATION_PENDING",),
            warnings=(str(error),),
        )
    return _finish_success(request, payload_hash, policy_id, receipt_id, result, readback, latency_ms)


def _lookup_after_denial(request: ActionRequest, plan: _Plan, payload: BaseModel, payload_hash: str) -> ActionResult:
    """Policy refused a retry. Report what the earlier attempt actually did, write nothing."""
    context = _connector_context(request)
    try:
        found = plan.connector.lookup(context, payload, request.idempotency_key)
    except ProviderError:
        return ActionResult(status="uncertain", receipt_id=plan.receipt_id, reasons=("OUTCOME_UNCERTAIN",))
    if found is None:
        _update_receipt(request, plan.receipt_id, status="not_applied")
        return plan.result
    return _perform(
        request, plan.connector, plan.backend, payload, payload_hash, plan.policy_id, plan.receipt_id,
        must_lookup=False, known=found,
    )


def execute(request: ActionRequest) -> ActionResult:
    # A receipt alone does not stop two workers from both looking up an operation
    # before either provider call has finished. Hold an action lock across that gap.
    key = f"action:{request.tenant_id}:{request.action}:{request.idempotency_key}"
    with operation_lock(key) as acquired:
        if not acquired:
            return ActionResult(status="retry", reasons=("CONCURRENT_ATTEMPT",), retry_after=2)
        return _execute(request)


def _execute(request: ActionRequest) -> ActionResult:
    spec = get_action(request.action)
    if spec is None:
        return ActionResult(status="denied", reasons=("ACTION_NOT_ALLOWLISTED",))
    try:
        payload = spec.input_model.model_validate(request.input)
    except ValidationError as exc:
        return ActionResult(status="failed", reasons=("INVALID_ACTION_INPUT",), warnings=(str(exc)[:500],))
    payload_dump = payload.model_dump(mode="json")
    payload_hash = _payload_hash(request.action, request.connection_id, payload_dump)
    amount = spec.amount_minor(payload) if spec.amount_minor else None
    resources = {**(spec.resources(payload) if spec.resources else {}), **request.resources}

    with tenant_tx(request.tenant_id) as session:
        plan = _decide(session, request, spec, payload_dump, payload_hash, amount, resources)

    if plan.lookup_only:
        return _lookup_after_denial(request, plan, payload, payload_hash)
    if plan.result is not None:
        return plan.result
    if plan.preview:
        return _preview(request, spec, payload, payload_dump, payload_hash)
    return _perform(
        request,
        plan.connector,
        plan.backend,
        payload,
        payload_hash,
        plan.policy_id,
        plan.receipt_id,
        must_lookup=plan.must_lookup,
        known=plan.verify_only,
    )


def reconcile_stale(tenant_id: str, older_than: timedelta = timedelta(minutes=10)) -> list[dict[str, Any]]:
    """Lists writes left unresolved, for the operator queue and the reconciliation sweep."""
    cutoff = utcnow() - older_than
    with tenant_tx(tenant_id) as session:
        rows = (
            session.execute(
                select(ActionReceipt).where(
                    ActionReceipt.status.in_(("pending", "uncertain", "mismatch")),
                    ActionReceipt.updated_at < cutoff,
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "receipt_id": r.id,
                "action": r.action,
                "status": r.status,
                "run_id": r.run_id,
                "idempotency_key": r.idempotency_key,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]
