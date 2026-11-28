"""Question sets for drafting a workflow in chat. Answers never grant authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from gluons.core import validation_failed
from gluons.passport import SENSITIVE_MARKERS, resolved_fields
from gluons.skills.domain.reorder import SKILL_ID as REORDER_SKILL

GuidedIntent = Literal["start_business", "delegate_workflow"]
GUIDED_INTENTS = frozenset({"start_business", "delegate_workflow"})
WITHIN_RULES = "assistant_decides_within_rules"
DECISION_MODES = ("owner_decides", "assistant_recommends", WITHIN_RULES)
ORDER_SKILL = "multichannel_order_entry"
GMAIL_CAPABILITIES = ("read_order_messages",)
ANSWER_LIMIT = 400
QUESTIONNAIRE_VERSION = 2

# These are setup prerequisites. An area can be reused, but never implies a grant.
SETUP_AREAS = {
    "email_and_messages": ("Email and messages", "Choose an authorized inbox, number or messaging account, and the permitted channels or recipients."),
    "phone_calls": ("Phone calls", "Choose a supported number or routing provider and confirm call handling and escalation."),
    "receive_payments": ("Receive payments", "Choose checkout and settlement accounts. The merchant completes provider verification."),
    "send_payments": ("Send payments", "Choose an authorized payout method and separately approve the payee, amount or a bounded spending policy."),
    "web_and_storefront": ("Website and storefront", "Choose the site or store and the permitted read, edit or publication access."),
    "alerts_and_escalations": ("Alerts and escalations", "Choose what triggers each alert, who receives it and through which authorized channel."),
    "business_records": ("Business records", "Identify the source of truth and the authorized records and read or write access."),
    "documents_and_data": ("Documents and data", "Choose approved files or storage, access scope and permitted sharing."),
    "shipping_and_fulfillment": ("Shipping and fulfillment", "Choose the fulfillment system and the evidence that confirms dispatch or delivery."),
    "team_access": ("Team access", "Identify who may act, review exceptions and grant or revoke access."),
}


@dataclass(frozen=True)
class Question:
    id: str
    prompt: str
    choices: tuple[str, ...] = ()

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "prompt": self.prompt, "choices": list(self.choices)}


BUSINESS_IDEA = Question("business_idea", "What would you like to sell, and to whom? Say what is still undecided.")
MARKET = Question("market", "Where would you like to sell first? Use a two-letter country code, or say undecided.")
OFFER = Question("offer_type", "What kind of offer should we plan for?",
                 ("physical_goods", "digital_products", "services", "undecided"))
EXPERIENCE = Question("launch_experience", "How much have you already worked out for this business?",
                       ("starting_from_scratch", "some_decisions_made", "ready_to_execute_a_plan"))
PRODUCT = Question("product_decision", "Who should choose the product?",
                   ("already_chosen", *DECISION_MODES))
DIGITAL_OFFER = Question("product_decision", "Who should choose the offer or service?",
                         ("already_chosen", *DECISION_MODES))
CHOSEN_OFFER = Question("chosen_offer", "What offer have you already chosen? Use the name or a short description.")
SUPPLIER = Question("supplier_decision", "Do you already have a supplier, or would you like help choosing one?",
                    ("already_chosen", *DECISION_MODES))
SUPPLIER_NAME = Question("supplier_name", "Which supplier should the plan name? Use the business name without contact or payment details.")
STOREFRONT = Question("storefront_decision", "Who should choose the storefront approach? Publishing is a separate action.", DECISION_MODES)
PROCESS_QUESTION = Question("process", "Which recurring job would you like to hand off first?",
                            ("order_intake", "inventory_reorder", "other"))
CHANNEL_QUESTION = Question("intake_channel", "Where do those orders arrive?", ("gmail", "manual", "other"))
PRIMARY_SETUP = Question("primary_setup_area", "Which setup does this job need first?", (*SETUP_AREAS, "undecided"))
HANDOFF = Question("handoff_readiness", "How much of this process can you describe today?",
                   ("still_figuring_it_out", "have_examples", "have_written_rules"))
PROCESS_QUESTIONS = (
    Question("process_example", "Describe one typical case and what starts it. Leave out customer details and credentials."),
    Question("system_of_record", "Which business system owns the final record? Use its name, without sign-in details."),
    Question("routine_decision", "Who should make routine decisions when the normal rules fit?", DECISION_MODES),
    Question("exceptions", "What should the assistant bring back to you, and what counts as done?"),
)
DECISIONS = {
    "product_decision": ("Choose the offer", "product_rules", "What criteria should the assistant use when choosing the offer?"),
    "supplier_decision": ("Choose a supplier", "supplier_rules", "What criteria should the assistant use when choosing a supplier?"),
    "storefront_decision": ("Choose the storefront approach", "storefront_rules", "What constraints should guide the storefront choice?"),
    "routine_decision": ("Handle routine cases", "routine_rules", "Which rules define a routine case the assistant may handle?"),
}


def questions(item: dict[str, Any]) -> tuple[Question, ...]:
    answers = item.get("answers", {})
    current = item.get("questionnaire_version") == QUESTIONNAIRE_VERSION
    if item["intent"] == "start_business":
        supplier_detail = (SUPPLIER_NAME,) if answers.get("supplier_decision") == "already_chosen" else ()
        if not current:
            return (BUSINESS_IDEA, MARKET, PRODUCT, SUPPLIER, *supplier_detail, STOREFRONT)
        physical = answers.get("offer_type") == "physical_goods"
        offer_detail = (CHOSEN_OFFER,) if answers.get("product_decision") == "already_chosen" else ()
        supplier = (SUPPLIER, *supplier_detail) if physical else ()
        sequence = (BUSINESS_IDEA, MARKET, OFFER, EXPERIENCE, PRODUCT if physical else DIGITAL_OFFER,
                    *offer_detail, *supplier, STOREFRONT)
    else:
        follow_up = {"order_intake": (CHANNEL_QUESTION,), "other": (PRIMARY_SETUP,) if current else ()}
        maturity = (HANDOFF,) if current else ()
        sequence = (PROCESS_QUESTION, *follow_up.get(answers.get("process"), ()), *maturity, *PROCESS_QUESTIONS)
    result = []
    for question in sequence:
        result.append(question)
        if current and question.id in DECISIONS and answers.get(question.id) == WITHIN_RULES:
            _, rule_id, prompt = DECISIONS[question.id]
            result.append(Question(rule_id, prompt + " Leave out credentials and payment instructions."))
    return tuple(result)


def planning_details(item: dict[str, Any], answers: dict[str, str], sequence: tuple[Question, ...]) -> dict[str, Any]:
    decisions = []
    for question in sequence:
        if question.id not in DECISIONS or question.id not in answers:
            continue
        label, rule_id, _ = DECISIONS[question.id]
        mode = answers[question.id]
        row: dict[str, Any] = {"decision": question.id, "label": label, "mode": mode}
        if mode == WITHIN_RULES:
            row["rules"] = answers.get(rule_id)
        if mode == "already_chosen":
            row["owner_choice"] = answers.get("chosen_offer" if question.id == "product_decision" else "supplier_name")
        decisions.append(row)
    if item["intent"] == "start_business":
        offer = answers.get("offer_type")
        area_ids = ["web_and_storefront", "receive_payments"] if offer and offer != "undecided" else []
        if offer == "physical_goods":
            area_ids.extend(("shipping_and_fulfillment", "send_payments"))
    else:
        area_ids = []
        if answers.get("process") in {"order_intake", "inventory_reorder"}:
            area_ids.append("business_records")
        if answers.get("intake_channel") == "gmail":
            area_ids.append("email_and_messages")
        if answers.get("primary_setup_area") in SETUP_AREAS:
            area_ids.append(answers["primary_setup_area"])
    return {
        "decision_plan": decisions,
        "setup_areas": [{"id": key, "label": SETUP_AREAS[key][0], "prerequisite": SETUP_AREAS[key][1]} for key in area_ids],
        "owner_only": ["approve_provider_access", "authorize_external_actions", "authorize_spending"],
    }


def needs_gmail(item: dict[str, Any]) -> bool:
    answers = item.get("answers", {})
    return item["intent"] == "delegate_workflow" and (
        answers.get("process"), answers.get("intake_channel")
    ) == ("order_intake", "gmail")


def automation_skill(item: dict[str, Any]) -> str | None:
    answers = item.get("answers", {})
    if answers.get("process") == "inventory_reorder":
        return REORDER_SKILL
    if answers.get("process") == "order_intake" and answers.get("intake_channel") in {"gmail", "manual"}:
        return ORDER_SKILL
    return None


def validate_answer(question: Question, value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= ANSWER_LIMIT or SENSITIVE_MARKERS.search(value):
        raise validation_failed("Enter a short business answer without credentials or sensitive identifiers.")
    if question.choices and value not in question.choices:
        raise validation_failed("Choose one of the available answers for this question.")
    if question.id == "market":
        if value.casefold() == "undecided":
            return "undecided"
        value = value.upper()
        if len(value) != 2 or not value.isascii() or not value.isalpha():
            raise validation_failed("Use a two-letter country code or undecided.")
    return value


def view(org, item: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    answers = item.get("answers", {})
    sequence = questions(item)
    pending = next((q for q in sequence if q.id not in answers), None)
    artifact = item.get("workflow_artifact")
    skill_id = automation_skill(item)
    connection_actions = result["next_actions"] if needs_gmail(item) else []
    blocker = None
    if result["status"] == "expired":
        status, next_question, next_tools = "expired", None, []
    elif pending:
        status, next_question, next_tools = "needs_answer", pending.view(), ["answer_workflow_setup"]
        if pending.id == "primary_setup_area":
            next_question["choice_labels"] = {key: value[0] for key, value in SETUP_AREAS.items()} | {"undecided": "Undecided"}
        if pending.id == "market":
            regions = resolved_fields(org).get("selling_regions", {}).get("value") or []
            if len(regions) == 1:
                next_question["suggested_answer"] = {"value": regions[0], "source": "business_passport"}
    elif connection_actions:
        status, next_question, next_tools = "needs_connection", None, []
        if connection_actions[0]["code"] in {"connect_provider", "restart_connection", "reconnect_account"}:
            next_tools = ["list_connections", "prepare_workflow_gmail"]
    elif artifact:
        status, next_question = "workflow_linked", None
        if artifact["kind"] == "launch_plan":
            next_tools = ["get_business_launch_plan", "start_business_launch_plan", "pause_business_launch_plan"]
        elif skill_id == REORDER_SKILL:
            next_tools = ["configure_inventory_reorder", "get_task_status", "start_trial", "submit_inventory_trial_case",
                          "configure_trial_handoff", "get_task_run", "get_trial_report", "pause_task", "resume_task"]
        else:
            next_tools = ["get_task_status", "list_missing_connections", "start_trial", "submit_order_trial_case",
                          "get_task_run", "get_trial_report", "policy_request_activation", "get_task_evidence",
                          "pause_task", "resume_task", "get_alerts"]
    elif item["intent"] == "start_business":
        if item.get("questionnaire_version") == QUESTIONNAIRE_VERSION and answers.get("offer_type") != "physical_goods":
            status, next_question, next_tools = "planning_only", None, []
            blocker = "The setup and decisions are saved. The supported commerce launch plan is for physical goods."
        else:
            status, next_question = "ready_to_draft", None
            next_tools = ["create_business_launch_plan", "link_workflow_draft"]
    elif skill_id:
        status, next_question = "ready_to_draft", None
        next_tools = ["inspect_automation", "create_automation", "link_workflow_draft"]
    else:
        status, next_question, next_tools = "planning_only", None, ["discover_automations"]
        blocker = "This workflow has no supported execution path in guided setup."
    return {
        "status": status, "answers": answers, "next_question": next_question, "next_tools": next_tools,
        "workflow_draft": artifact, "automation_skill_id": skill_id, "authorization": "none",
        "blocker": blocker,
        **planning_details(item, answers, sequence),
        "instructions": [
            "Ask the current question in chat. Record only the user's answer or a suggestion they explicitly confirmed.",
            "Show the decision plan before linking a draft. Explain who decides and use only rules the owner supplied.",
            "Reuse approved accounts where they cover the setup areas. Obtain separate approval for each new scope or action.",
            "Use a secure owner handoff only when the service returns one. Poll this setup to continue after consent.",
            "Use existing tools to check dependencies and trial results before requesting activation. Report provider evidence as returned.",
            "An owner must separately approve external actions and any spending grant. Never infer authority from a planning answer.",
        ],
    }
