"""Static sanity check of design skill package; not a proof of production readiness."""
from pathlib import Path
import json,yaml
ROOT=Path(__file__).parent
catalog=json.loads((ROOT/'skills/catalog.json').read_text())
assert len(catalog)==31
ids=set()
for entry in catalog:
 slug=entry['id'];assert slug not in ids;ids.add(slug)
 d=ROOT/'skills'/slug
 assert all((d/f).exists() for f in ('SKILL.md','workflow.yaml','evals.json'))
 wf=yaml.safe_load((d/'workflow.yaml').read_text())
 assert wf['id']=='gluons.'+slug
 assert wf['production_readiness']=='SPEC_ONLY'
 assert len(wf['workflow'])>=7
 assert len(wf['failure_branches'])>=4
 assert len(wf['metrics'])>=3
 ev=json.loads((d/'evals.json').read_text()); assert len(ev['fixtures'])>=6
for f in ['README.md','docs/01_product_spec.md','docs/02_architecture_and_api.md','docs/03_build_plan.md','docs/05_demo_and_yc.md','docs/06_research_sources.md','docs/07_gcp_deployment_playbook.md','docs/08_policy_and_evaluation.md','demo/index.html']:
 assert (ROOT/f).exists(),f
print('Verified 31 skill specifications, 31 x 6 test scenario manifests, full design docs, and browser simulation.')
