"""A deterministic tool-backed owner assistant, available without an external LLM."""
import re

from gluons import reports, tasks
from gluons.authz import Principal
from gluons.core import validation_failed
from gluons.runtime import lifecycle
from gluons.skills import catalog


def message(principal: Principal, text: str) -> dict:
    if not 1 <= len(text.strip()) <= 2000:
        raise validation_failed("Enter a business goal or command of at most 2000 characters.")
    text = text.strip()
    matched = re.fullmatch(r"(pause|resume|cancel|inspect)\s+(aut_[a-f0-9]{24})", text.lower())
    if matched:
        action, identifier = matched.groups()
        current = tasks.task_status(principal, identifier)
        if action == "inspect":
            return {"reply": f"{current['name']} is {current['status']} in {current['mode']} mode.", "data": current, "source": "persisted_task"}
        principal.require("tasks:control")
        return {"reply": f"Review and confirm {action} for {current['name']}. Completed external actions will not be undone.",
                "confirmation": {"action": action, "automation_id": identifier, "expected_version": current["version"]}, "source": "persisted_task"}
    if re.search(r"\b(tasks|automations|running)\b", text, re.I) and re.search(r"\b(list|show|running|status)\b", text, re.I):
        records = lifecycle.list_all(principal)
        return {"reply": f"Your business has {len(records)} automations. Their persisted states are shown below.", "data": records, "source": "persisted_tasks"}
    if re.search(r"\b(cost|spend|report|saving|roi)\b", text, re.I):
        return {"reply": "These reports use recorded operations and costs. Missing savings evidence remains unknown.",
                "data": {"operations": reports.operational(principal), "roi": reports.roi(principal)}, "source": "persisted_reports"}
    if re.search(r"\b(activate|approve|payment|kyc|publish|send)\b", text, re.I):
        return {"reply": "Use the owner approval and connection screens to review exact actions. This message grants no operational authority.",
                "links": {"approvals": "/approvals", "connections": "/connections"}, "source": "authorization_requirements"}
    found = catalog.discover(principal, text)
    return {"reply": "These registered skills match your goal. Review their prerequisites and start with a bounded trial.", "data": found,
            "source": "registered_skills", "assistant_mode": "deterministic_commands"}


def act(principal: Principal, action: str, automation_id: str, expected_version: int) -> dict:
    operations = {"pause": lifecycle.pause, "resume": lifecycle.resume, "cancel": lifecycle.cancel}
    if action not in operations:
        raise validation_failed("This command assistant supports explicit pause, resume or cancel actions.")
    return operations[action](principal, automation_id, expected_version)
