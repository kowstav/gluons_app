import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from reference_api.app import app
client=TestClient(app)

def test_catalog_covers_full_plan():
    d=client.get('/v1/skills').json()
    assert len(d['skills'])==31
    assert all(x['readiness']=='SPEC_ONLY' for x in d['skills'])

def test_shadow_never_writes_and_dedups():
    headers={'Idempotency-Key':'test-idempotent-001'}
    a=client.post('/v1/automations',json={'skill_id':'multichannel_order_entry','mode':'shadow'},headers=headers).json()
    b=client.post('/v1/automations',json={'skill_id':'multichannel_order_entry','mode':'shadow'},headers=headers).json()
    assert a['id']==b['id']
    eid=a['id']
    one=client.post(f'/v1/automations/{eid}/simulate-event',json={'source_event_id':'mail-1','payload':{'order':'rice'}}).json()
    two=client.post(f'/v1/automations/{eid}/simulate-event',json={'source_event_id':'mail-1','payload':{'order':'rice'}}).json()
    assert one['external_writes']==0 and two['duplicate'] is True
    task=client.get('/v1/automations/'+eid).json()
    assert task['events_processed']==1 and task['external_writes']==0
    paused=client.post(f'/v1/automations/{eid}/pause',headers={'Expected-Version':str(task['version'])}).json()
    assert paused['status']=='paused'
    denied=client.post(f'/v1/automations/{eid}/simulate-event',json={'source_event_id':'mail-2','payload':{}})
    assert denied.status_code==409
    cancelled=client.post(f'/v1/automations/{eid}/cancel',headers={'Expected-Version':str(paused['version'])}).json()
    assert cancelled['status']=='cancelled'
