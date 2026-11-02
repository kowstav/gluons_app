"""Owner consent and representative limits around trials."""
from dataclasses import replace
from datetime import timedelta

import pytest

from gluons import assist, trials
from gluons.core import GluonsError, utcnow
from gluons.db.models import AssistSession
from gluons.db.session import tenant_tx
from gluons.runtime import lifecycle
from helpers import World, add_member, make_user


def test_representative_cannot_start_trial_before_owner_web_consent(world):
    user, _ = make_user()
    rep = add_member(world.tenant_id, user, "field_rep")
    request = assist.request_session(rep)
    with pytest.raises(GluonsError, match="consent"):
        assist.setup_shadow_trial(rep, request["id"], "multichannel_order_entry")
    with pytest.raises(GluonsError, match="Gluons Web"):
        assist.consent(world.owner, request["id"], allow_shadow_trial=True)
    assist.consent(replace(world.owner, session_id="verified-browser-session"), request["id"], allow_shadow_trial=True)
    assert assist.setup_shadow_trial(rep, request["id"], "multichannel_order_entry")["mode"] == "shadow"
    assert not rep.can("policies:sign") and not rep.can("automations:activate") and not rep.can("connections:manage")
    # The consented shadow trial stays the only write: the role itself cannot create or touch work.
    with pytest.raises(GluonsError, match="automations:write"):
        lifecycle.create(rep, skill_id="multichannel_order_entry")
    with pytest.raises(GluonsError):
        lifecycle.start_trial(rep, world.order_automation()["id"], "shadow", 2)
    assist.end_session(rep, request["id"], {})
    with pytest.raises(GluonsError, match="consent"):
        trials.list_all(rep)


def test_expired_session_and_other_tenant_cannot_grant_representative_access(world):
    user, _ = make_user()
    rep = add_member(world.tenant_id, user, "field_rep")
    request = assist.request_session(rep)
    with pytest.raises(GluonsError):
        assist.consent(replace(World.create().owner, session_id="browser"), request["id"])
    with tenant_tx(world.tenant_id) as session:
        session.get(AssistSession, request["id"]).expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(GluonsError):
        assist.consent(replace(world.owner, session_id="browser"), request["id"])
    assert assist.list_sessions(rep)[0]["status"] == "expired"


def test_representative_cannot_read_or_complete_owner_trial_even_with_consent(world):
    user, _ = make_user()
    rep = add_member(world.tenant_id, user, "field_rep")
    request = assist.request_session(rep)
    assist.consent(replace(world.owner, session_id="browser"), request["id"])
    automation = world.order_automation()
    trial = lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    assert trials.list_all(rep) == []
    for function in (trials.get_report, trials.complete):
        with pytest.raises(GluonsError):
            function(rep, trial["trial_id"])
    assert lifecycle.get(world.owner, automation["id"])["status"] == "trial_shadow"
    assist.end_session(world.owner, request["id"], {})
    with pytest.raises(GluonsError, match="consent"):
        trials.list_all(rep)


@pytest.mark.parametrize("minimum", ["many", True, 0, -1, 10001])
def test_trial_minimum_cases_is_bounded(world, minimum):
    automation = world.order_automation()
    with pytest.raises(GluonsError, match="minimum_cases"):
        lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"], comparator={"minimum_cases": minimum})
