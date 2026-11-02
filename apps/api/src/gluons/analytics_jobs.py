"""Periodic trial expiry and anomaly scans. Importing this module registers both with the worker."""
from __future__ import annotations

from sqlalchemy import select

from gluons import reports, trials
from gluons.db.models import Automation, Job
from gluons.db.session import system_tx
from gluons.runtime import worker

EXPIRE_TRIALS = "trials.expire"
SCAN_ANOMALIES = "reports.anomalies"


def _tenants() -> list[str]:
    with system_tx() as session:
        return list(session.execute(select(Automation.tenant_id).distinct()).scalars())


@worker.handler(EXPIRE_TRIALS)
def _expire_trials(job: Job, worker_id: str) -> None:
    for tenant_id in _tenants():
        trials.expire_due(tenant_id)


@worker.handler(SCAN_ANOMALIES)
def _scan_anomalies(job: Job, worker_id: str) -> None:
    for tenant_id in _tenants():
        reports.detect_anomalies(tenant_id)


worker.PERIODIC[EXPIRE_TRIALS] = 3600
worker.PERIODIC[SCAN_ANOMALIES] = 900
