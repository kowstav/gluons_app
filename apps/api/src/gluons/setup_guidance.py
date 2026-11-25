"""Question sets for drafting a workflow in chat. Answers never grant authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from gluons.core import validation_failed
from gluons.passport import SENSITIVE_MARKERS, resolved_fields
from gluons.skills.domain.reorder import SKILL_ID as REORDER_SKILL

GuidedIntent = Literal["start_business", "delegate_workflow"]
GUIDED_INTENTS = frozenset({"start_business", "delegate_workflow"})
DECISION_MODES = ("owner_decides", "assistant_recommends", "assistant_decides_within_rules")
ORDER_SKILL = "multichannel_order_entry"
GMAIL_CAPABILITIES = ("read_order_messages",)
ANSWER_LIMIT = 400


@dataclass(frozen=True)
class Question:
    id: str
    prompt: str
    choices: tuple[str, ...] = ()

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "prompt": self.prompt, "choices": list(self.choices)}


BUSINESS_IDEA = Question("business_idea", "What would you like to sell, and to whom? Say what is still undecided.")
MARKET = Question("market", "Where would you like to sell first? Use a two-letter country code, or say undecided.")
PRODUCT = Question("product_decision", "Who should choose the product?",
                   ("already_chosen", *DECISION_MODES))
SUPPLIER = Question("supplier_decision", "Do you already have a supplier, or would you like help choosing one?",
                    ("already_chosen", *DECISION_MODES))
SUPPLIER_NAME = Question("supplier_name", "Which supplier should the plan name? Use the business name without contact or payment details.")
STOREFRONT = Question("storefront_decision", "Who should choose the storefront approach? Publishing is a separate action.", DECISION_MODES)
PROCESS_QUESTION = Question("process", "Which recurring job would you like to hand off first?",
                            ("order_intake", "inventory_reorder", "other"))
CHANNEL_QUESTION = Question("intake_channel", "Where do those orders arrive?", ("gmail", "manual", "other"))
PROCESS_QUESTIONS = (
    Question("process_example", "Describe one typical case and what starts it. Leave out customer details and credentials."),
    Question("system_of_record", "Which business system owns the final record? Use its name, without sign-in details."),
    Question("routine_decision", "Who should make routine decisions when the normal rules fit?", DECISION_MODES),
    Question("exceptions", "What should the assistant bring back to you, and what counts as done?"),
)


def questions(intent: str, answers: dict[str, str]) -> tuple[Question, ...]:
    if intent == "start_business":
        supplier_detail = (SUPPLIER_NAME,) if answers.get("supplier_decision") == "already_chosen" else ()
        return (BUSINESS_IDEA, MARKET, PRODUCT, SUPPLIER, *supplier_detail, STOREFRONT)
    if answers.get("process") == "order_intake":
        return (PROCESS_QUESTION, CHANNEL_QUESTION, *PROCESS_QUESTIONS)
    return (PROCESS_QUESTION, *PROCESS_QUESTIONS)


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
    pending = next((q for q in questions(item["intent"], answers) if q.id not in answers), None)
    artifact = item.get("workflow_artifact")
    skill_id = automation_skill(item)
    connection_actions = result["next_actions"] if needs_gmail(item) else []
    if result["status"] == "expired":
        status, next_question, next_tools = "expired", None, []
    elif pending:
        status, next_question, next_tools = "needs_answer", pending.view(), ["answer_workflow_setup"]
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
        status, next_question = "ready_to_draft", None
        next_tools = ["create_business_launch_plan", "link_workflow_draft"]
    elif skill_id:
        status, next_question = "ready_to_draft", None
        next_tools = ["inspect_automation", "create_automation", "link_workflow_draft"]
    else:
        status, next_question, next_tools = "planning_only", None, []
    return {
        "status": status, "answers": answers, "next_question": next_question, "next_tools": next_tools,
        "workflow_draft": artifact, "automation_skill_id": skill_id, "authorization": "none",
        "blocker": "This workflow has no supported execution path in guided setup." if status == "planning_only" else None,
        "instructions": [
            "Ask the current question in chat. Record only the user's answer or a suggestion they explicitly confirmed.",
            "Treat answers as planning input. Show the owner the proposed responsibilities and exact steps before creating a draft.",
            "Use a secure owner handoff only when the service returns one. Poll this setup to continue after consent.",
            "Use existing tools to check dependencies and trial results before requesting activation. Report provider evidence as returned.",
            "An owner must separately approve external actions and any spending grant. Never infer authority from a planning answer.",
        ],
    }
