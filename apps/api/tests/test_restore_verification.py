"""Restore a real PostgreSQL archive containing simulator receipts and queued work."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, replace

from pixeltable_pgserver.pgexec import pgexec
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from gluons import devdb
from gluons.core import new_id
from gluons.db.models import ActionReceipt, AuditEvent
from gluons.db.session import tenant_tx
from gluons.restore import RestoreWitness, verify_restore
from helpers import World, drain, order_event
from test_order_vertical import approve, pending_approval


def test_postgres_restore_verifies_audit_and_receipts_without_resuming_jobs(database, world, erp, sandbox_erp, tmp_path):
    other = World.create("Other recovery fixture")
    world.connect_erp(sandbox_erp)
    automation = world.supervised_automation()
    sample = order_event(world, automation["id"], "restore-completed")
    drain()
    approve(world, pending_approval(world, sample["run_id"]).id)
    drain()
    pending = order_event(world, automation["id"], "restore-pending")
    assert len(erp.orders()) == 1

    with tenant_tx(world.tenant_id) as session:
        receipt = session.scalar(select(ActionReceipt).where(ActionReceipt.run_id == sample["run_id"], ActionReceipt.verified))
        audit_head = session.scalar(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))
    assert receipt and receipt.environment == "simulator" and audit_head

    source_name = make_url(database.migration_url).database
    target_name = new_id("recovery")
    target = devdb.start(database.server.pgdata, database=target_name)
    try:
        archive = tmp_path / "staging-fixture.dump"
        pgexec("pg_dump", ("--format=custom", "--file", str(archive), "--dbname", database.server.get_uri(database=source_name)),
               bin_path=database.server.bin_path, timeout=90)
        pgexec("pg_restore", ("--exit-on-error", "--dbname", target.server.get_uri(database=target_name), str(archive)),
               bin_path=target.server.bin_path, timeout=90)

        witness = RestoreWitness(target_name, world.tenant_id, other.tenant_id, audit_head, receipt.id, "simulator")
        assert verify_restore(database.app_url, witness)["expected_target"] is False
        assert verify_restore(target.migration_url, witness)["read_only_app_role"] is False
        assert verify_restore(target.app_url, replace(witness, audit_head="0" * 64))["audit_chain_matches"] is False
        assert verify_restore(target.app_url, replace(witness, receipt_environment="production"))["verified_receipt_retained"] is False

        witness_path = tmp_path / "witness.json"
        witness_path.write_text(json.dumps(asdict(witness)), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "gluons.cli", "verify-restore", "--witness", str(witness_path)],
            env={**os.environ, "GLUONS_RESTORE_DATABASE_URL": target.app_url},
            capture_output=True, text=True, check=True, timeout=30,
        )
        output = result.stdout
        report = json.loads(output)
        assert report["passed"] and all(report["checks"].values())
        assert all(value not in output for value in (target_name, world.tenant_id, other.tenant_id, receipt.id, audit_head))

        engine = create_engine(target.migration_url)
        try:
            with engine.connect() as conn:
                assert conn.scalar(text("SELECT status FROM runs WHERE id = :id"), {"id": pending["run_id"]}) == "queued"
        finally:
            engine.dispose()
        assert len(erp.orders()) == 1
    finally:
        engine = create_engine(database.migration_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{target_name}"'))
        finally:
            engine.dispose()
