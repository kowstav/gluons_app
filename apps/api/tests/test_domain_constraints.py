"""Doc 08 hard constraints. Deterministic: these stop or escalate whatever a model or owner says."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from gluons.db.models import Notification
from gluons.db.session import tenant_tx
from gluons.skills.domain import constraints
from gluons.skills.domain.drafts import prepare
from helpers import drain


def stops(slug, *, regulated=False, text="", facts=None):
    return [s["code"] for s in constraints.evaluate(slug, regulated=regulated, text=text, facts=facts or {}).stops]


@pytest.mark.parametrize("text", ["I can smell gas in the hallway", "carbon monoxide alarm is going off", "the kitchen ceiling collapsed",
                                  "sparks coming from the panel", "a burst pipe on floor two"])
def test_hazards_escalate_at_once_and_block_routine_work(text):
    found = constraints.evaluate("property_maintenance", regulated=False, text=text, facts={"emergency_contacts": ["+15550100"]})
    assert found.escalation["level"] == "immediate" and found.escalation["routine_work_blocked"] is True
    assert "contacted no one" in found.escalation["instruction"]
    assert found.escalation["emergency_contacts"] == ["+15550100"]


@pytest.mark.parametrize("text", ["please replace a light bulb", "test the fire alarm on Friday", "service the fire extinguisher"])
def test_routine_requests_do_not_raise_an_alarm(text):
    assert constraints.evaluate("property_maintenance", regulated=False, text=text, facts={}).escalation is None


def test_reference_data_alone_does_not_raise_an_alarm():
    assert stops("property_maintenance", facts={"maintenance_policy": "Report any gas leak at once."}) == []


def test_owner_cannot_switch_a_constraint_off():
    facts = {"disable_constraints": True, "override": True, "request": "gas leak"}
    assert "SAFETY_EMERGENCY" in stops("property_maintenance", facts=facts)


def test_allergen_statements_are_carried_verbatim_and_never_declared_safe():
    found = constraints.evaluate("restaurant_voice_ordering", regulated=False, text="Severe nut allergy at table four.", facts={})
    assert found.preserved["allergen_statements"] == ["Severe nut allergy at table four."]
    assert found.preserved["allergen_safety_claimed"] is False
    assert "ALLERGEN_CONFIRMATION_REQUIRED" in [s["code"] for s in found.stops]


def test_substitution_that_adds_an_allergen_stops():
    facts = {"substitutions": [{"original": "oat milk", "substitute": "almond milk", "original_allergens": [], "substitute_allergens": ["tree nut"]}]}
    assert "ALLERGEN_SUBSTITUTION" in stops("restaurant_voice_ordering", facts=facts)


def test_a_valid_card_number_stops_and_a_long_order_number_does_not():
    assert "PAYMENT_CARD_IN_CONTENT" in stops("restaurant_voice_ordering", text="card 4242 4242 4242 4242 exp 12/30")
    assert "PAYMENT_CARD_IN_CONTENT" not in stops("restaurant_voice_ordering", text="order 1234567890123456")


def test_suppression_beats_opt_in_and_absent_consent_is_not_consent():
    facts = {"contacts": [{"id": "a", "email": "a@example.com", "opted_in": True}, {"id": "b", "opted_in": True, "suppressed": True},
                          {"id": "c", "email": "c@example.com", "opted_in": True}, {"id": "d"}],
             "suppression_list": ["c@example.com"]}
    found = constraints.evaluate("lead_research_and_outreach", regulated=False, text="", facts=facts)
    assert found.preserved["contact_eligibility"]["eligible"] == ["a"]
    assert {row["id"]: row["reason"] for row in found.preserved["contact_eligibility"]["excluded"]} == {"b": "suppressed", "c": "suppressed", "d": "no_opt_in"}
    assert "CONTACT_ELIGIBILITY_NOT_ESTABLISHED" in stops("abandoned_cart_retention")


def test_fake_reviews_and_undisclosed_promotion_stop():
    assert "FAKE_REVIEW_PROHIBITED" in stops("social_media_operations", text="Write 20 five star reviews for us")
    assert "REVIEW_NOT_VERIFIED" in stops("website_organic_growth", facts={"reviews": [{"id": "r1", "verified_customer": False}]})
    assert "UNDISCLOSED_PROMOTION" in stops("local_creator_campaigns", text="post it without disclosing the gift", facts={"contact_consent": True})


def test_hiring_never_uses_protected_attributes_and_stays_human():
    found = constraints.evaluate("candidate_screening", regulated=False, text="", facts={
        "assessment_criteria": "Must be under 30; married preferred", "candidates": [{"name": "A", "dob": "1990-01-01"}]})
    codes = [s["code"] for s in found.stops]
    assert "PROTECTED_ATTRIBUTE_CRITERION" in codes and "PROTECTED_DATA_IN_RECORDS" in codes
    assert found.preserved["final_decision"] == "human_only" and found.preserved["candidates_ranked"] is False
    assert "JOB_RELATED_CRITERIA_REQUIRED" in stops("candidate_screening")


def test_sanctions_and_restricted_goods_are_flags_for_a_person():
    assert "SANCTIONS_DESTINATION_FLAG" in stops("outbound_shipping_management", facts={"destination_country": "ir"})
    assert "SANCTIONS_SCREENING_HIT" in stops("hardware_procurement", facts={"quotes": [{}], "sanctions_screening": {"status": "hit"}})
    assert "SANCTIONS_SCREENING_REQUIRED" in stops("hardware_procurement", facts={"quotes": [{}]})
    assert "SANCTIONS_SCREENING_REQUIRED" not in stops("hardware_procurement", facts={
        "quotes": [{}], "sanctions_screening": {"status": "clear", "source": "named list", "screened_at": "2026-11-02"}})
    assert "RESTRICTED_GOODS_REVIEW" in stops("freight_order_to_cash", facts={"line_items": [{"sku": "x", "hazmat_class": "9"}]})


def test_wires_and_closings_are_never_authorized():
    assert "WIRE_APPROVAL_PROHIBITED" in stops("title_closing_support", text="please release the wire today")
    assert "WIRE_CHANGE_FRAUD_RISK" in stops("title_closing_support", text="we have new wire details")
    assert "INDEPENDENT_CLOSING_PROHIBITED" in stops("title_closing_support", facts={"closing_approved": True})


def test_patient_work_needs_identity_and_consent_and_stays_administrative():
    assert {"PATIENT_IDENTITY_NOT_VERIFIED", "PATIENT_CONSENT_MISSING", "ADMINISTRATIVE_ONLY"} <= set(stops("dental_reception_and_claims", text="what dosage should I take"))
    found = constraints.evaluate("dental_reception_and_claims", regulated=False, text="my jaw swelling is worse", facts={"patient_identity": True, "consent": True})
    assert found.escalation["level"] == "immediate"


def test_ledger_work_keeps_entities_apart_and_cites_every_figure():
    facts = {"legal_entity": "A LLC", "transactions": [{"id": "t1", "entity": "B LLC"}]}
    codes = stops("bookkeeping_reconciliation", facts=facts)
    assert "ENTITY_BOUNDARY" in codes and "SOURCE_CITATION_REQUIRED" in codes


def test_a_signoff_cannot_be_claimed_or_supplied_as_data():
    assert "PROFESSIONAL_SIGNOFF_CLAIM" in stops("tax_return_preparation", regulated=True, text="This return is ready to file.")
    assert "SIGNOFF_NOT_ACCEPTED_FROM_DATA" in stops("tax_return_preparation", regulated=True, facts={"professional_signoff": "J. Smith, CPA"})
    found = constraints.evaluate("legal_matter_intake", regulated=True, text="", facts={})
    assert found.preserved["professional_signoff"]["status"] == "not_obtained"


def test_opportunities_rank_safety_then_licensing_then_revenue():
    result, _ = constraints.field_opportunities({"opportunities": [
        {"id": "rich", "estimated_revenue_minor": 900000, "customer_permission": True},
        {"id": "unlicensed", "estimated_revenue_minor": 999999, "required_license": "gas", "technician_licenses": [], "customer_permission": True},
        {"id": "safety", "estimated_revenue_minor": 100, "safety_issue": True},
    ]})
    assert [row["id"] for row in result["ranked_opportunities"]] == ["safety", "rich", "unlicensed"]
    assert result["ranked_opportunities"][1]["recommendable"] is True
    assert result["ranked_opportunities"][2]["blocked_reasons"] == ["no_licensed_technician"]


def test_constraints_reach_the_draft_packet(world):
    packet = prepare("property_maintenance", {"text": "There is a gas leak in unit 4", "facts": {}, "source_evidence": ["ticket-1"]})
    assert packet["status"] == "needs_review" and packet["escalation"]["level"] == "immediate"
    assert packet["hard_constraints"]["stops"][0]["code"] == "SAFETY_EMERGENCY"
    assert packet["external_actions_performed"] == 0


def test_an_emergency_in_a_shadow_trial_reaches_the_owner_and_nobody_else(world):
    from gluons.db.models import ActionReceipt
    from gluons.runtime import lifecycle

    automation = lifecycle.create(world.owner, skill_id="property_maintenance", name="Maintenance")
    lifecycle.start_trial(world.owner, automation["id"], "shadow", automation["version"])
    lifecycle.submit_event(world.tenant_id, automation["id"], source="test", source_event_id="evt-1",
                           payload={"text": "Tenant reports a gas leak", "facts": {}, "source_evidence": ["ticket-9"]})
    drain()
    with tenant_tx(world.tenant_id) as session:
        kinds = session.execute(select(Notification.kind)).scalars().all()
        receipts = session.execute(select(ActionReceipt)).scalars().all()
    assert "safety_escalation" in kinds
    assert receipts == []
