"""Offline consistency audit of GitHub integration plan.

IMPORTANT: Does NOT scan or approve upstream source code, verify license files,
or certify integration/runtime security. Run repository-by-repository diligence.
"""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
ALLOWED={'embed','selective_embed','sidecar','optional_sidecar','optional','sandbox_or_customer_system','reference_only'}

def audit():
    repos=json.loads((ROOT/'repos/repository_registry.json').read_text())
    mapping=json.loads((ROOT/'repos/skill_to_repo_map.json').read_text())
    catalog=json.loads((ROOT/'skills/catalog.json').read_text())
    assert len(repos)>=20
    byid={r['id']:r for r in repos}
    assert len(byid)==len(repos)
    assert len(mapping)==len(catalog)==31
    assert {m['skill_id'] for m in mapping}=={s['id'] for s in catalog}
    for r in repos:
        assert r['url'].startswith('https://github.com/')
        assert r['license_evidence'].startswith('https://')
        assert r['proposed_use'] in ALLOWED
        assert r['upstream_code_included'] is False
        if r['commercial_reuse_class']=='restricted':
            assert r['proposed_use']=='reference_only',r['name']
    for m in mapping:
        assert m['repo_ids'] and set(m['repo_ids'])<=set(byid)
        assert m['integration_status']=='PLANNED_NOT_BUILT'
        assert m['is_complete_open_source_equivalent'] is False
        p=ROOT/'skills'/m['skill_id']/'INTEGRATION.md'
        assert p.exists(),p
    print(f"Offline mapping valid: {len(repos)} candidate repos, {len(mapping)} skill mappings. NO upstream code audited or deployed.")

if __name__=='__main__': audit()
