import asyncio, json
from pathlib import Path
from adapters.reference_contract import ActionRequest, ReferenceNoWriteAdapter
from scripts.audit_repo_registry import audit

def test_catalog_and_legal_blocks():
    audit()

def test_shadow_never_writes():
    a=ReferenceNoWriteAdapter()
    req=ActionRequest(tenant_id='t1',task_id='t1:one',skill_id='multichannel_order_entry', action='erp.order.create', mode='shadow',idempotency_key='t1:event:1',policy_version=1,connection_id='sandbox',payload={'sku':'1'})
    res=asyncio.run(a.execute(req))
    assert res.status=='preview' and res.verified is False and res.provider_object_id is None

def test_live_reference_cannot_claim_success():
    a=ReferenceNoWriteAdapter()
    req=ActionRequest(tenant_id='t1',task_id='t1:one',skill_id='multichannel_order_entry',action='erp.order.create',mode='live',idempotency_key='t1:event:1',policy_version=1,connection_id='sandbox',payload={},approved_action_id='approved')
    res=asyncio.run(a.execute(req))
    assert res.status=='failed' and res.verified is False
