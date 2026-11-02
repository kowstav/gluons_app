"""Gluons REFERENCE API (SIMULATED, NOT PRODUCTION) to demonstrate task state.
NO real auth, persistence, connectors, phone calls, MCP transport or external writes.
Never expose this publicly or handle customer data with it.
"""
from __future__ import annotations
from uuid import uuid4
from typing import Literal
import json
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "skills/catalog.json").read_text())
SKILLS = {x['id']: x for x in CATALOG}
app = FastAPI(title="Gluons demo-only task API", version="0.0.1-SIMULATION",
    description="Simulated in-memory reference; no authentication or third-party systems.")
RUNS: dict[str, dict] = {}
EVENTS: dict[str, list[dict]] = {}
IDEMPOTENCY: dict[str, str] = {}

class Start(BaseModel):
    skill_id: str
    mode: Literal['historical_replay','shadow','supervised'] = 'shadow'
    monthly_budget_usd: float = Field(default=25, ge=0, le=100000)

class Event(BaseModel):
    source_event_id: str
    payload: dict

class Decision(BaseModel):
    decision: Literal['approve','deny']

@app.get('/health')
def health():
    return {'status':'ok', 'simulation':True, 'external_integrations':False}

@app.get('/v1/skills')
def skills():
    return {'skills':[dict(x, readiness='SPEC_ONLY') for x in CATALOG]}

@app.post('/v1/automations')
def start(body:Start, idempotency_key:str=Header(..., alias='Idempotency-Key')):
    if body.skill_id not in SKILLS:
        raise HTTPException(404,'No such skill')
    if idempotency_key in IDEMPOTENCY:
        return RUNS[IDEMPOTENCY[idempotency_key]]
    task_id = str(uuid4())
    task = {'id':task_id, 'skill_id':body.skill_id, 'mode':body.mode,
        'status':'active','version':1,'monthly_budget_usd':body.monthly_budget_usd,
        'simulation':True,'external_writes':0,'events_processed':0}
    RUNS[task_id]=task
    EVENTS[task_id]=[{'type':'created','mode':body.mode,'simulation':True}]
    IDEMPOTENCY[idempotency_key]=task_id
    return task

@app.get('/v1/automations/{task_id}')
def get(task_id:str):
    if task_id not in RUNS:
        raise HTTPException(404,'No such task')
    return dict(RUNS[task_id], history=EVENTS[task_id])

@app.post('/v1/automations/{task_id}/simulate-event')
def simulate(task_id:str, event:Event):
    task=RUNS.get(task_id)
    if not task:raise HTTPException(404,'No such task')
    if task['status']!='active':raise HTTPException(409,'Task is not active')
    if any(e.get('source_event_id')==event.source_event_id for e in EVENTS[task_id]):
        return {'duplicate':True,'status':task['status'],'external_writes':0}
    task['events_processed']+=1
    task['version']+=1
    EVENTS[task_id].append({'type':'simulated_event','source_event_id':event.source_event_id,
        'payload_summary':{'keys':list(event.payload.keys())},'external_writes':0})
    return {'duplicate':False,'result':'A simulated plan was evaluated; no provider contacted',
        'mode':task['mode'],'external_writes':0}

@app.post('/v1/automations/{task_id}/{action}')
def lifecycle(task_id:str,action:Literal['pause','resume','cancel'],expected_version:int=Header(..., alias='Expected-Version')):
    task=RUNS.get(task_id)
    if not task:raise HTTPException(404,'No such task')
    if expected_version!=task['version']:raise HTTPException(409,'State changed; refresh and retry')
    if task['status']=='cancelled':raise HTTPException(409,'Cannot restart a canceled task')
    if action=='resume' and task['status']!='paused':raise HTTPException(409,'Only paused tasks can resume')
    task['status']={'pause':'paused','resume':'active','cancel':'cancelled'}[action]
    task['version']+=1
    EVENTS[task_id].append({'type':action,'external_writes':0})
    return task
