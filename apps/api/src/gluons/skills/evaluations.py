"""Executable preparation fixtures; they cannot certify a provider integration."""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from gluons.config import get_settings
from gluons.core import validation_failed
from gluons.skills.domain.drafts import prepare
from gluons.skills.registry import get_registry


def preparation_suite(skill_id: str) -> dict[str, Any]:
    skill = get_registry().loaded(skill_id)
    if skill.contract.execution_scope != "draft_only":
        raise validation_failed("Use provider-specific runtime evaluations for this skill.")
    fixtures = json.loads((Path(get_settings().skills_dir) / skill.contract.slug / "evals.json").read_text(encoding="utf-8"))
    started = perf_counter()
    cases = []
    for fixture in fixtures["cases"]:
        actual = prepare(skill.contract.slug, fixture["input"])
        failures = []
        for key, expected in fixture["expected"].items():
            if key == "injection_flags_present":
                observed = bool(actual["injection_flags"])
            elif key == "all_workflow_steps_pending":
                observed = all(step["status"] == "not_executed" for step in actual["pending_workflow"])
            else:
                observed = actual.get(key)
            if observed != expected:
                failures.append(key)
        cases.append({"id": fixture["id"], "passed": not failures, "failed_assertions": failures})
    return {
        "skill_id": skill.contract.slug, "version": skill.contract.version, "contract_hash": skill.contract_hash,
        "scope": "draft_preparation_only", "passed": bool(cases) and all(case["passed"] for case in cases),
        "cases": cases, "duration_ms": round((perf_counter() - started) * 1000, 3),
        "provider_calls": 0, "provider_certification": False,
    }
