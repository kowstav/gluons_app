"""Hard domain constraints from doc 08, checked deterministically on every draft.

A model is not involved and owner input cannot switch these off. Each check either passes,
records a hard stop that a person must clear, or escalates at once. Patterns are English and
deliberately broad: a false alarm costs a phone call, a missed gas leak does not. They are
review triggers, never a legal, safety or licensing determination.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

# Facts that carry a request or a draft. Reference data such as an emergency contact list or a
# maintenance policy names hazards in every business, so it must not raise an alarm by itself.
REQUEST_FIELDS = ("request", "issue", "issue_description", "ticket", "work_order", "message", "description", "notes", "technician_notes",
                  "draft_text", "content", "marketing_brief", "brief", "post", "caption", "transcript")

HAZARDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("gas", re.compile(r"\bgas (leak|smell|odou?r)\b|\b(smell|smelling|odou?r)\w*\s+(of|like)\s+(natural\s+)?gas\b|\bleaking gas\b"
                       r"|\bcarbon monoxide\b|\bco (alarm|detector)\b", re.I)),
    ("flood", re.compile(r"\bflood(ed|ing|s)?\b|\b(burst|ruptured|broken)\s+(water\s+)?(pipe|main|line)\b"
                         r"|\bwater\s+(is\s+)?(pouring|gushing|everywhere)\b|\bsewage\s+(backup|back-up|overflow)\b", re.I)),
    ("electrical", re.compile(r"\b(sparks?|sparking|arcing|electrocut\w*)\b|\bexposed\s+(live\s+)?wir(es?|ing)\b|\blive\s+wires?\b"
                              r"|\belectric(al)?\s+(fire|shock|burning|smoke)\b"
                              r"|\b(outlet|panel|breaker|fuse\s*box|wiring)\b[^.]{0,40}\b(smok\w*|burn\w*|melt\w*|scorch\w*)\b", re.I)),
    ("fire_or_life_safety", re.compile(r"\bon fire\b|\b(house|building|kitchen|apartment)\s+fire\b|\ba fire\b(?!\s+(alarm|extinguisher|drill|door|inspection))"
                                       r"|\bsmoke\s+(is\s+)?(coming|filling|pouring)\b|\bmedical emergency\b|\bunconscious\b|\bnot breathing\b"
                                       r"|\b(ceiling|wall|roof|floor|building)\b[^.]{0,30}\bcollaps\w*\b|\bcollaps(ed|ing)\b|\btrapped\b", re.I)),
)
_ALLERGEN = re.compile(r"\b(allerg\w*|anaphyla\w*|intoleran\w*|celiac|coeliac|epi-?pen|(gluten|dairy|nut|egg|soy|sesame)[- ]free"
                       r"|no\s+(nuts?|peanuts?|tree nuts?|dairy|shellfish|gluten|eggs?|soy|sesame))\b", re.I)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_FAKE_REVIEW = re.compile(r"\bfake\s+(\w+\s+)?(reviews?|testimonials?)\b|\b(write|post|generate|invent|fabricate)\b[^.]{0,40}\b(reviews?|testimonials?)\b"
                          r"|\b(buy|bought|buying|purchase|purchased|pay|paying|paid)\b[^.]{0,20}\b(reviews?|ratings?|followers)\b"
                          r"|\breview\s+(swap|exchange|farm)\b|\bastroturf\w*\b|\bsock\s?puppets?\b|\b5[- ]star\b[^.]{0,20}\bfor\b[^.]{0,20}\b(free|discount|gift)\b", re.I)
_UNDISCLOSED = re.compile(r"\bwithout\s+(any\s+)?(disclos\w*|#?ad\b|#?sponsored)|\bdo(n'?t| not)\s+(disclose|mention|say)\b[^.]{0,30}\b(paid|sponsor\w*|gift\w*|ad)\b"
                          r"|\bhide\b[^.]{0,20}\b(sponsor\w*|payment|ad)\b", re.I)
_PROTECTED = re.compile(r"\b(age|aged|young|younger|elderly|date of birth|dob|birth ?year|race|racial|ethnic\w*|skin colou?r|religio\w*|caste|gender|sex|male|female"
                        r"|pregnan\w*|maternity|disabilit\w*|disabled|marital|married|family status|national origin|nationality|citizenship|immigration status"
                        r"|sexual orientation|gay|lesbian|transgender|veteran|genetic|mother tongue|native speaker|maiden name)\b", re.I)
PROTECTED_RECORD_FIELDS = frozenset({"age", "date_of_birth", "dob", "gender", "sex", "race", "ethnicity", "religion", "caste", "marital_status", "pregnant",
                                     "pregnancy", "disability", "nationality", "national_origin", "sexual_orientation", "photo", "photograph", "veteran_status"})
_RESTRICTED_GOODS = re.compile(r"\blithium(-ion| ion)?\b|\bexplosives?\b|\bammunition\b|\bfirearms?\b|\bgunpowder\b|\bfireworks\b|\bradioactive\b|\bhazmat\b"
                               r"|\bhazardous\b|\bflammable\b|\bcorrosive\b|\bcompressed gas\b|\bdry ice\b|\bbiohazard\w*\b|\bdual[- ]use\b|\bitar\b|\beccn\b", re.I)
# A conservative review list, not a statement of current law. Owners extend it with
# facts.restricted_destinations, and a qualified person always makes the determination.
REVIEW_DESTINATIONS = {"CU", "IR", "KP", "SY", "RU", "BY"}
_SANCTIONED_PLACE = re.compile(r"\b(cuba|iran|north korea|dprk|syria|russia|belarus|crimea|donetsk|luhansk)\b", re.I)
_WIRE = re.compile(r"\b(approve|release|send|authori[sz]e|confirm)\b[^.]{0,30}\b(wire|wires|funds|disbursement)\b|\bwire\s+(approval|transfer|instructions?)\b"
                   r"|\btransfer\s+(money|funds)\b|\bdisburse\w*\b|\brouting number\b|\bbank (details|account|instructions)\b", re.I)
_WIRE_CHANGE = re.compile(r"\b(new|changed|updated|different|revised)\b[^.]{0,30}\b(wire|bank|routing|account)\b", re.I)
_CLOSING = re.compile(r"\b(authori[sz]e|approve|complete|finali[sz]e)\b[^.]{0,20}\bclosing\b|\bclose the (deal|transaction|sale)\b", re.I)
_CLINICAL = re.compile(r"\bdiagnos\w*\b|\bprescri\w*\b|\bdosage\b|\bhow much\b[^.]{0,20}\b(ibuprofen|antibiotic|medicine|medication)\b"
                       r"|\btreatment plan\b|\bshould i (take|stop taking)\b", re.I)
_DENTAL_URGENT = re.compile(r"\b(uncontrolled|won'?t stop)\s+bleeding\b|\b(facial|face|jaw)\s+swelling\b|\b(can'?t|cannot|trouble)\s+breath\w*\b", re.I)
_UNLAWFUL_TARGETING = re.compile(r"\b(exclude|only|avoid|target)\b[^.]{0,40}" + _PROTECTED.pattern, re.I)

_SIGNOFF_ANY = r"\bsigned off\b|\bcertified by\b|\blicensed (professional|reviewer)\s+(has\s+)?(reviewed|approved)\b|\bprofessionally certified\b"
SIGNOFF: dict[str, tuple[str, re.Pattern[str]]] = {
    "legal_matter_intake": ("a licensed attorney", re.compile(
        _SIGNOFF_ANY + r"|\b(this|that) (is|constitutes) legal advice\b|\bas your (attorney|lawyer|counsel)\b|\battorney[- ]client relationship\b[^.]{0,30}\b(formed|established|created|exists)\b"
        r"|\breviewed by (an|our) (attorney|lawyer)\b|\byou have a (strong|valid|winning|good) case\b", re.I)),
    "commercial_insurance_submission": ("a licensed insurance broker", re.compile(
        _SIGNOFF_ANY + r"|\bcoverage is bound\b|\bwe (hereby )?bind\b|\bpolicy is (now )?in force\b|\byou are (now )?covered\b|\bcertificate of insurance (is )?issued\b"
        r"|\b(licensed )?broker (has )?(approved|reviewed)\b", re.I)),
    "property_valuation_reports": ("a licensed appraiser", re.compile(
        _SIGNOFF_ANY + r"|\bcertified appraisal\b|\bi certify\b|\bappraised value (is|of)\b|\bmarket value is\b|\buspap[- ]compliant\b|\bsigned by (the |an? )?appraiser\b", re.I)),
    "tax_return_preparation": ("a licensed tax professional", re.compile(
        _SIGNOFF_ANY + r"|\bready to file\b|\bhas been filed\b|\bthis return is (accurate|correct|complete)\b|\breviewed by (a |an |our )?(cpa|enrolled agent|tax professional)\b"
        r"|\bsigned by (a |an |our |the )?(cpa|preparer|enrolled agent)\b|\bguaranteed refund\b", re.I)),
}
REGULATED_REVIEWER = {
    "accounting_practice_operations": "a qualified accountant", "bookkeeping_reconciliation": "a qualified accountant",
    "dental_reception_and_claims": "a licensed dental professional", "title_closing_support": "a licensed title or escrow officer",
}


@dataclass
class Inputs:
    slug: str
    text: str
    facts: dict[str, Any]
    corpus: str


@dataclass
class Findings:
    checks: list[str] = field(default_factory=list)
    stops: list[dict[str, Any]] = field(default_factory=list)
    escalation: dict[str, Any] | None = None
    preserved: dict[str, Any] = field(default_factory=dict)

    def stop(self, code: str, reason: str, **detail: Any) -> None:
        self.stops.append({"code": code, "reason": reason, **detail})


def _as_text(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(_as_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_as_text(v) for v in value)
    return str(value) if isinstance(value, str) else ""


def _records(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _sentences(text: str) -> Iterable[str]:
    return (part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip())


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _strip_cards(text: str) -> tuple[str, int]:
    found = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found += 1
            return "[card number removed]"
        return match.group()

    return _CARD.sub(replace, text), found


def check_emergency(item: Inputs, out: Findings) -> None:
    out.checks.append("safety_emergency")
    hazards = [name for name, pattern in HAZARDS if pattern.search(item.corpus)]
    if item.facts.get("emergency_reported") is True:
        hazards.append("reported_emergency")
    jobs = (item.facts.get("dispatch_problem") or {}).get("jobs") if isinstance(item.facts.get("dispatch_problem"), dict) else None
    if any(job.get("emergency") is True for job in _records(jobs)) or any(o.get("safety_issue") is True for o in _records(item.facts.get("opportunities"))):
        hazards.append("flagged_job")
    if hazards:
        out.escalation = {"required": True, "level": "immediate", "hazards": sorted(set(hazards)), "routine_work_blocked": True,
                          "emergency_contacts": item.facts.get("emergency_contacts"),
                          "instruction": "A person must contact the configured emergency channel now. Do not schedule routine work. Gluons contacted no one."}
        out.stop("SAFETY_EMERGENCY", "Safety emergency: contact the configured human emergency channel immediately; do not schedule routine work.",
                 hazards=out.escalation["hazards"])


def check_licensing(item: Inputs, out: Findings) -> None:
    """The optimizer's eligibility rule decides, so a value figure can never buy an assignment."""
    from gluons.skills.domain.dispatch import DispatchInput, eligible

    out.checks.append("technician_licensing")
    out.preserved["ranking_basis"] = ["emergency", "technician_licensing", "dispatch_feasibility"]
    out.preserved["revenue_used_for_priority"] = False
    try:
        data = DispatchInput.model_validate(item.facts["dispatch_problem"])
    except (KeyError, ValueError):
        return
    unlicensed = [job.id for job in data.jobs if job.required_certifications
                  and not any(eligible(t, job, data.service_date) for t in data.technicians)]
    if unlicensed:
        out.stop("NO_LICENSED_TECHNICIAN", "No technician holds a current licence for this work. It stays unassigned.", job_ids=unlicensed)


def check_opportunity_licensing(item: Inputs, out: Findings) -> None:
    out.checks.append("technician_licensing")
    out.preserved["revenue_used_for_priority"] = False
    unlicensed = [o.get("id") for o in _records(item.facts.get("opportunities"))
                  if o.get("required_license") and o["required_license"] not in (o.get("technician_licenses") or [])]
    if unlicensed:
        out.stop("NO_LICENSED_TECHNICIAN", "These opportunities need a licence the assigned technician does not hold.", opportunity_ids=unlicensed)


def check_allergens(item: Inputs, out: Findings) -> None:
    out.checks.append("allergen_statements")
    statements, seen = [], set()
    supplied = item.facts.get("allergen_statements")
    for sentence in [*(_sentences(item.corpus)), *([str(s) for s in supplied] if isinstance(supplied, list) else [])]:
        clean, _ = _strip_cards(sentence)
        if clean not in seen and (_ALLERGEN.search(clean) or (isinstance(supplied, list) and sentence in supplied)):
            seen.add(clean)
            statements.append(clean)
    out.preserved["allergen_statements"] = statements
    out.preserved["allergen_safety_claimed"] = False
    if statements:
        out.stop("ALLERGEN_CONFIRMATION_REQUIRED", "Allergen statements are carried verbatim and need staff confirmation. Gluons makes no safety claim.")
    for row in _records(item.facts.get("substitutions")):
        before, after = row.get("original_allergens"), row.get("substitute_allergens")
        if not isinstance(after, list) or not isinstance(before, list) or set(after) - set(before):
            out.stop("ALLERGEN_SUBSTITUTION", "This substitution adds an allergen or does not declare its allergens.",
                     substitute=row.get("substitute"), original=row.get("original"))


def check_payment_capture(item: Inputs, out: Findings) -> None:
    out.checks.append("payment_card_capture")
    _, cards = _strip_cards(item.corpus)
    out.preserved["payment_card_captured"] = False
    if cards:
        out.stop("PAYMENT_CARD_IN_CONTENT", "A card number appeared in this content. It is not kept in the draft; use a compliant payment link.", occurrences=cards)


def check_contact_eligibility(item: Inputs, out: Findings) -> None:
    """Default deny. Suppression beats opt-in, and absent consent is not consent."""
    out.checks.append("opt_in_and_suppression")
    facts = item.facts
    suppressed_ids = {str(v).lower() for v in facts.get("suppression_list") or []} if isinstance(facts.get("suppression_list"), list) else set()
    contacts = _records(facts.get("contacts"))
    eligible, excluded = [], []
    for contact in contacts:
        identifiers = {str(contact.get(key)).lower() for key in ("id", "email", "phone") if contact.get(key)}
        name = contact.get("id") or "unnamed contact"
        if contact.get("suppressed") is True or identifiers & suppressed_ids:
            excluded.append({"id": name, "reason": "suppressed"})
        elif contact.get("opted_in") is not True:
            excluded.append({"id": name, "reason": "no_opt_in"})
        else:
            eligible.append(name)
    consent = any(facts.get(key) is True for key in ("contact_consent", "opt_in_status", "creator_consent"))
    out.preserved["contact_eligibility"] = {"eligible": eligible, "excluded": excluded, "basis": "owner_supplied_unverified", "messages_sent": 0}
    if facts.get("suppressed") is True or (not contacts and not consent):
        out.stop("CONTACT_ELIGIBILITY_NOT_ESTABLISHED", "Contact eligibility has not been established; prepare drafts only and send nothing.")
    elif excluded:
        out.stop("CONTACTS_EXCLUDED", "Some contacts lack opt-in or are suppressed. They were removed and must stay out of any send.", excluded=len(excluded))


def check_fake_reviews(item: Inputs, out: Findings) -> None:
    out.checks.append("no_fake_reviews")
    hits = sorted({match.group().lower() for match in _FAKE_REVIEW.finditer(item.corpus)})
    if hits:
        out.stop("FAKE_REVIEW_PROHIBITED", "Fabricated, bought or swapped reviews are never produced.", matched=hits[:5])
    unverified = [r.get("id") or r.get("source_ref") or "unnamed review" for r in _records(item.facts.get("reviews"))
                  if r.get("verified_customer") is not True or not r.get("source_ref")]
    if unverified:
        out.stop("REVIEW_NOT_VERIFIED", "Only reviews from a verified customer with a source may be used.", reviews=unverified[:20])
    if _UNDISCLOSED.search(item.corpus):
        out.stop("UNDISCLOSED_PROMOTION", "Paid or gifted promotion must be disclosed.")


def check_job_related(item: Inputs, out: Findings) -> None:
    out.checks.append("job_related_criteria")
    facts = item.facts
    raw = facts.get("job_related_requirements") if facts.get("job_related_requirements") not in (None, True) else facts.get("assessment_criteria")
    criteria = [line.strip() for line in re.split(r"[\n;]+", _as_text(raw)) if line.strip()]
    protected = sorted({m.group().lower() for line in criteria for m in _PROTECTED.finditer(line)})
    if protected:
        out.stop("PROTECTED_ATTRIBUTE_CRITERION", "Assessment criteria contain protected-attribute signals; recruiter review required.", matched=protected)
    if not criteria and facts.get("job_related_requirements") is not True:
        out.stop("JOB_RELATED_CRITERIA_REQUIRED", "Written job-related criteria are required before any screening.")
    seen = sorted({key.lower() for row in _records(facts.get("candidates")) for key in row if key.lower() in PROTECTED_RECORD_FIELDS})
    if seen:
        out.stop("PROTECTED_DATA_IN_RECORDS", "Candidate records hold protected fields. They must not inform screening.", fields=seen)
    out.preserved.update(candidates_ranked=False, final_decision="human_only", adverse_impact_review_required=True)


def check_unlawful_targeting(item: Inputs, out: Findings) -> None:
    out.checks.append("no_unlawful_targeting")
    text = "\n".join((item.corpus, _as_text(item.facts.get("targeting"))))
    if _UNLAWFUL_TARGETING.search(text):
        out.stop("UNLAWFUL_TARGETING", "Audience rules appear to select on a protected attribute. Marketing review required.")


def check_restricted_goods(item: Inputs, out: Findings) -> None:
    out.checks.append("restricted_goods")
    facts = item.facts
    goods_text = "\n".join((item.corpus, *(_as_text(facts.get(key)) for key in ("bom", "line_items", "shipments", "items", "customs_data"))))
    terms = sorted({m.group().lower() for m in _RESTRICTED_GOODS.finditer(goods_text)})
    flagged = [row.get("id") or row.get("sku") or "unnamed item" for key in ("bom", "line_items", "shipments", "items") for row in _records(facts.get(key))
               if row.get("restricted") is True or row.get("hazmat_class")]
    if terms or flagged:
        out.stop("RESTRICTED_GOODS_REVIEW", "Restricted or hazardous goods need a qualified person before anything moves.", matched=terms[:10], items=flagged[:20])


def check_sanctions(item: Inputs, out: Findings) -> None:
    out.checks.append("sanctions_flags")
    facts = item.facts
    review = REVIEW_DESTINATIONS | {str(code).upper() for code in facts.get("restricted_destinations") or []}
    countries = {str(facts.get(key)).upper() for key in ("destination_country", "origin_country", "supplier_country", "consignee_country") if facts.get(key)}
    for key in ("shipments", "items", "line_items"):
        countries |= {str(row.get("destination_country")).upper() for row in _records(facts.get(key)) if row.get("destination_country")}
    if countries & review or _SANCTIONED_PLACE.search(item.corpus):
        out.stop("SANCTIONS_DESTINATION_FLAG", "A destination or party needs sanctions review. This is a flag, not a legal determination.", countries=sorted(countries & review))
    counterparties = any(facts.get(key) for key in ("quotes", "counterparty", "consignee", "supplier", "customer_contacts", "shipments"))
    screening = facts.get("sanctions_screening")
    status = screening.get("status") if isinstance(screening, dict) else None
    if status == "hit":
        out.stop("SANCTIONS_SCREENING_HIT", "A screening hit stops this work until a qualified person clears it.")
    elif counterparties and not (status == "clear" and screening.get("source") and screening.get("screened_at")):
        out.stop("SANCTIONS_SCREENING_REQUIRED", "Record a dated sanctions screening from a named source for these parties.")
    out.preserved["screening_basis"] = "review_flags_only_not_a_legal_determination"


def check_wire_rules(item: Inputs, out: Findings) -> None:
    out.checks.append("no_wire_approvals")
    facts = item.facts
    out.preserved.update(wire_approval_performed=False, closing_authorized=False)
    if _WIRE.search(item.corpus) or any(facts.get(key) for key in ("wire_instructions", "wire_approved", "wire_approval", "disbursement")):
        out.stop("WIRE_APPROVAL_PROHIBITED", "Gluons never approves wires or disbursements. A licensed closer decides, outside this draft.")
    if _WIRE_CHANGE.search(item.corpus):
        out.stop("WIRE_CHANGE_FRAUD_RISK", "Changed payment details are a common fraud pattern. Verify by a known phone number, not by this message.")
    if _CLOSING.search(item.corpus) or facts.get("closing_approved"):
        out.stop("INDEPENDENT_CLOSING_PROHIBITED", "Gluons cannot close or authorize a closing.")


def check_patient_identity(item: Inputs, out: Findings) -> None:
    out.checks.append("patient_identity_and_consent")
    facts = item.facts

    def confirmed(value: Any, key: str) -> bool:
        return value is True or (isinstance(value, dict) and value.get(key) is True)

    if not confirmed(facts.get("patient_identity"), "verified"):
        out.stop("PATIENT_IDENTITY_NOT_VERIFIED", "Verify the patient's identity before using any record.")
    if not confirmed(facts.get("consent"), "granted"):
        out.stop("PATIENT_CONSENT_MISSING", "Patient consent is required before contact or a claim.")
    if _CLINICAL.search(item.corpus):
        out.stop("ADMINISTRATIVE_ONLY", "Clinical questions go to a licensed clinician. This skill is administrative only.")
    if _DENTAL_URGENT.search(item.corpus):
        out.escalation = {"required": True, "level": "immediate", "hazards": ["urgent_clinical"], "routine_work_blocked": True, "emergency_contacts": None,
                          "instruction": "A person must direct this patient to urgent care now. Gluons contacted no one."}
        out.stop("URGENT_CLINICAL_SIGNAL", "This message may describe an urgent condition. Staff must respond now.")


def check_entity_boundaries(item: Inputs, out: Findings) -> None:
    out.checks.append("entity_boundaries")
    facts = item.facts
    records = [*_records(facts.get("transactions")), *_records(facts.get("invoices")), *_records(facts.get("return_lines"))]
    entities = {row["entity"] for row in records if row.get("entity")}
    declared = facts.get("legal_entity") or facts.get("taxpayer_entity")
    outside = sorted(str(entity) for entity in entities if entity != declared) if declared else []
    if outside:
        out.stop("ENTITY_BOUNDARY", "Records from another legal entity cannot be combined in this work.", declared=declared, entities=outside)
    elif not declared and len(entities) > 1:
        out.stop("ENTITY_BOUNDARY", "Records span several legal entities and none was declared.", entities=sorted(str(e) for e in entities))
    if records and not declared and not entities:
        out.stop("ENTITY_NOT_DECLARED", "State which legal entity these records belong to.")
    out.preserved["legal_entity"] = declared


def check_source_citations(item: Inputs, out: Findings) -> None:
    out.checks.append("source_citations")
    facts = item.facts
    records = [*_records(facts.get("transactions")), *_records(facts.get("invoices")), *_records(facts.get("return_lines"))]
    uncited = [row.get("id") or row.get("line") or "unnamed record" for row in records if not (row.get("source_ref") or row.get("source_document"))]
    if uncited:
        out.stop("SOURCE_CITATION_REQUIRED", "Every figure needs a source document reference.", records=[str(r) for r in uncited[:50]], missing=len(uncited))
    out.preserved["citations_required"] = True


def check_signoff(item: Inputs, out: Findings) -> None:
    """Applies to every regulated skill. A sign-off cannot be asserted by data or by a draft."""
    out.checks.append("professional_signoff_never_claimed")
    reviewer, pattern = SIGNOFF.get(item.slug, (REGULATED_REVIEWER.get(item.slug, "a licensed professional"), re.compile(_SIGNOFF_ANY, re.I)))
    hits = sorted({m.group().lower() for m in pattern.finditer(item.corpus)})
    if hits:
        out.stop("PROFESSIONAL_SIGNOFF_CLAIM", f"This content claims a professional sign-off that has not happened. Only {reviewer} can give it.", matched=hits[:5])
    if item.facts.get("professional_signoff") or item.facts.get("signed_off"):
        out.stop("SIGNOFF_NOT_ACCEPTED_FROM_DATA", "A sign-off supplied as input is not treated as a sign-off.")
    out.preserved["professional_signoff"] = {"claimed": False, "status": "not_obtained", "required_from": reviewer}


MARKETING = (check_contact_eligibility,)
TRADE = (check_restricted_goods, check_sanctions)
LEDGER = (check_entity_boundaries, check_source_citations)
CHECKS: dict[str, tuple[Callable[[Inputs, Findings], None], ...]] = {
    "property_maintenance": (check_emergency,),
    "home_service_dispatch": (check_emergency, check_licensing),
    "field_service_opportunity_detection": (check_emergency, check_opportunity_licensing),
    "restaurant_voice_ordering": (check_allergens, check_payment_capture),
    "restaurant_inventory_planning": (check_allergens,),
    "abandoned_cart_retention": MARKETING,
    "lead_research_and_outreach": MARKETING,
    "crm_reactivation_and_hygiene": MARKETING,
    "local_creator_campaigns": (*MARKETING, check_fake_reviews),
    "paid_ad_campaign_operations": (check_unlawful_targeting, check_fake_reviews),
    "social_media_operations": (check_fake_reviews,),
    "website_organic_growth": (check_fake_reviews,),
    "candidate_screening": (check_job_related,),
    "hardware_procurement": TRADE,
    "freight_order_to_cash": TRADE,
    "outbound_shipping_management": TRADE,
    "title_closing_support": (check_wire_rules,),
    "dental_reception_and_claims": (check_patient_identity,),
    "bookkeeping_reconciliation": LEDGER,
    "tax_return_preparation": LEDGER,
    "accounting_practice_operations": LEDGER,
}


def evaluate(slug: str, *, regulated: bool, text: str, facts: dict[str, Any]) -> Findings:
    corpus = "\n".join((text, *(_as_text(facts.get(key)) for key in REQUEST_FIELDS)))
    item = Inputs(slug, text, facts, corpus)
    found = Findings()
    for check in (*CHECKS.get(slug, ()), *((check_signoff,) if regulated else ())):
        check(item, found)
    return found


def field_opportunities(facts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Safety first, then licensing, and only then revenue. Permission gates any recommendation."""
    if "opportunities" not in facts:
        return {}, []
    ranked = []
    for row in _records(facts["opportunities"]):
        licensed = not row.get("required_license") or row["required_license"] in (row.get("technician_licenses") or [])
        safety = row.get("safety_issue") is True
        permitted = row.get("customer_permission") is True
        blocked = [reason for reason, active in (("safety_issue_first", safety), ("no_licensed_technician", not licensed), ("no_customer_permission", not permitted)) if active]
        ranked.append({"id": row.get("id"), "safety_issue": safety, "licensed": licensed, "customer_permission": permitted,
                       "estimated_revenue_minor": row.get("estimated_revenue_minor"), "recommendable": not blocked, "blocked_reasons": blocked})
    ranked.sort(key=lambda r: (not r["safety_issue"], not r["licensed"], -(r["estimated_revenue_minor"] or 0)))
    return {"ranked_opportunities": ranked, "ranking_basis": ["safety", "licensing", "revenue"], "revenue_basis": "owner_supplied_estimate"}, []
