"""Loads skill contracts and rejects anything that does not match its blueprint.

Registration fails on: malformed schema, a blueprint step with no graph node, an
unhandled failure branch, a missing metric, an action outside the allowlist, an unknown
primitive, unreachable nodes, or a write action in a regulated skill with no human gate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from gluons.actions import get_action
from gluons.config import get_settings
from gluons.core import fingerprint, not_found
from gluons.skills.contract import SkillContract


class SkillValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedSkill:
    contract: SkillContract
    contract_hash: str
    blueprint_hash: str
    blueprint: dict[str, Any]
    catalog: dict[str, Any]
    skill_md: str
    fixtures: list[dict[str, Any]]


def _reachable(contract: SkillContract) -> set[str]:
    seen: set[str] = set()
    pending = [contract.graph.entry]
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = contract.graph.nodes[node_id]
        pending.extend(t for t in [node.next, *node.branches.values()] if t)
    return seen


def validate(contract: SkillContract, blueprint: dict[str, Any], primitives: frozenset[str]) -> None:
    problems: list[str] = []
    if contract.id != blueprint["id"]:
        problems.append(f"id {contract.id} does not match blueprint {blueprint['id']}")
    if contract.version != blueprint["version"]:
        problems.append(f"version {contract.version} does not match blueprint {blueprint['version']}")

    covered = {step for node in contract.graph.nodes.values() for step in node.blueprint_steps}
    for step in blueprint["workflow"]:
        if step["step"] not in covered:
            problems.append(f"blueprint step {step['step']} has no graph node")
    for branch in blueprint["failure_branches"]:
        if branch not in contract.failure_handling:
            problems.append(f"failure branch '{branch}' has no handler")
    for metric in blueprint["metrics"]:
        if metric not in contract.metrics:
            problems.append(f"metric '{metric}' is not tracked")

    unreachable = set(contract.graph.nodes) - _reachable(contract)
    if unreachable:
        problems.append(f"unreachable nodes: {sorted(unreachable)}")
    for node in contract.graph.nodes.values():
        if node.primitive not in primitives:
            problems.append(f"{node.id} uses unknown primitive {node.primitive}")
        if node.primitive == "action":
            name = node.params.get("action")
            if name is not None and name not in contract.actions:
                problems.append(f"{node.id} uses undeclared action {name}")

    has_human_gate = any(n.primitive == "human_review" for n in contract.graph.nodes.values())
    for name in contract.actions:
        spec = get_action(name)
        if spec is None:
            problems.append(f"action {name} is not in the allowlist")
            continue
        if contract.regulated and spec.is_write and spec.risk_tier != "R0" and not has_human_gate:
            problems.append(f"regulated skill writes via {name} with no human_review node")
    if problems:
        raise SkillValidationError(f"{contract.slug}: " + "; ".join(problems))


class SkillRegistry:
    def __init__(self, skills: dict[str, LoadedSkill]) -> None:
        self._skills = skills

    def __len__(self) -> int:
        return len(self._skills)

    def ids(self) -> list[str]:
        return sorted(self._skills)

    def all(self) -> list[LoadedSkill]:
        return [self._skills[k] for k in sorted(self._skills)]

    def loaded(self, skill_id: str) -> LoadedSkill:
        key = skill_id.removeprefix("gluons.")
        skill = self._skills.get(key)
        if skill is None:
            raise not_found("Skill")
        return skill

    def get(self, skill_id: str, version: str | None = None) -> SkillContract:
        skill = self.loaded(skill_id)
        if version is not None and skill.contract.version != version:
            raise not_found("Skill version")
        return skill.contract


def load(skills_dir: Path, blueprint_dir: Path) -> SkillRegistry:
    from gluons.connectors import load_all as load_connectors
    from gluons.skills.primitives import PRIMITIVES

    load_connectors()
    catalog = {
        entry["id"]: entry
        for entry in json.loads((blueprint_dir / "skills" / "catalog.json").read_text(encoding="utf-8"))
    }
    primitives = frozenset(PRIMITIVES)
    skills: dict[str, LoadedSkill] = {}
    for path in sorted(skills_dir.glob("*/contract.yaml")):
        slug = path.parent.name
        contract = SkillContract.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        folder = blueprint_dir / "playbooks" / slug if slug not in catalog else blueprint_dir / "skills" / slug
        blueprint = yaml.safe_load((folder / "workflow.yaml").read_text(encoding="utf-8"))
        validate(contract, blueprint, primitives)
        fixtures = json.loads((folder / "evals.json").read_text(encoding="utf-8"))
        skills[slug] = LoadedSkill(
            contract=contract,
            contract_hash=fingerprint(contract.model_dump(mode="json")),
            blueprint_hash=fingerprint(blueprint),
            blueprint=blueprint,
            catalog=catalog.get(slug, {}),
            skill_md=(folder / "SKILL.md").read_text(encoding="utf-8"),
            fixtures=fixtures.get("fixtures", fixtures.get("cases", [])),
        )
    return SkillRegistry(skills)


@lru_cache
def get_registry() -> SkillRegistry:
    settings = get_settings()
    return load(Path(settings.skills_dir), Path(settings.blueprint_dir))
