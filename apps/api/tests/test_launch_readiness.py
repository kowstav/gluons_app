"""Current launch readiness degrades while historical evidence remains intact."""
from datetime import timedelta

import pytest

from gluons import launch
from gluons.commerce import service
from gluons.core import GluonsError, utcnow
from gluons.db.models import Connection, LaunchEvidence, LaunchPlan
from gluons.db.session import tenant_tx
from helpers import World


def test_readiness_is_shared_and_never_infers_launch_from_a_plan(world):
    plan = launch.create_plan(world.owner, goal="Start a small shop")
    projection = launch.get_plan(world.owner, plan["id"])["readiness"]
    assert projection == launch.evidence(world.owner, plan["id"])["readiness"]
    assert projection == service.readiness(world.owner, plan["id"])["readiness"]
    assert projection["checks"]["internal_draft"]["status"] == "verified"
    assert projection["checks"]["store_connection"]["status"] == "blocked"
    assert projection["status"] == "blocked" and not projection["store_live"]
    with pytest.raises(GluonsError):
        launch.get_plan(World.create().owner, plan["id"])


def test_store_status_degrades_after_recheck_expiry_and_revocation(world):
    plan = launch.create_plan(world.owner, goal="Start a small shop")
    now = utcnow()
    with tenant_tx(world.tenant_id) as session:
        connection = Connection(id="con_projection", tenant_id=world.tenant_id, provider="shopify", environment="sandbox",
                                external_account_id="fixture", account_display="Fixture", created_by=world.user_id,
                                account_confirmed_at=now - timedelta(seconds=1), last_checked_at=now,
                                credential_ref="vault_projection", status="connected")
        session.add(connection)
        session.get(LaunchPlan, plan["id"]).state = {"store_connection_id": connection.id}
        session.add(LaunchEvidence(id="lev_projection", tenant_id=world.tenant_id, plan_id=plan["id"], stage="STORE_CONNECTED",
                                   kind="store_connection", source="shopify", is_test=True,
                                   detail={"connection_id": connection.id, "environment": "sandbox"}))
    current = launch.get_plan(world.owner, plan["id"])["readiness"]
    assert current["checks"]["store_connection"]["status"] == "verified"
    assert current["checks"]["store_connection"]["environment"] == "sandbox"
    assert current["status"] == "blocked" and not current["payment_verified"] and not current["store_live"]
    with tenant_tx(world.tenant_id) as session:
        session.get(Connection, "con_projection").last_checked_at = now - timedelta(hours=25)
    assert launch.get_plan(world.owner, plan["id"])["readiness"]["checks"]["store_connection"]["status"] == "needs_reauth"
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, "con_projection")
        connection.last_checked_at, connection.disconnected_at = now, now
    assert launch.get_plan(world.owner, plan["id"])["readiness"]["checks"]["store_connection"]["status"] == "needs_reauth"
    assert len(launch.evidence(world.owner, plan["id"])["items"]) == 1
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, "con_projection")
        connection.disconnected_at = None
        connection.environment = "production"
    assert launch.get_plan(world.owner, plan["id"])["readiness"]["checks"]["store_connection"]["status"] == "blocked"
    with tenant_tx(world.tenant_id) as session:
        connection = session.get(Connection, "con_projection")
        connection.environment = "sandbox"
        connection.account_confirmed_at = utcnow() + timedelta(seconds=1)
    assert launch.get_plan(world.owner, plan["id"])["readiness"]["checks"]["store_connection"]["status"] == "blocked"
