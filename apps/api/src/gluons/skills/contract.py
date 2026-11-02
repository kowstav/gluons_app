"""Typed skill contract. Blueprint skill files are untrusted specifications until a
contract has been validated against them (doc 03 workstream C, step 4).

The blueprint's SKILL.md is the intelligence layer: what the task means. A contract is
the deterministic authority layer: what the runtime will actually do (doc 08).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CapabilityStatus = Literal["implemented", "sandbox_verified", "live_verified", "blocked_by_external_dependency"]


class ConnectorRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    providers: list[str]
    purpose: str
    required_for_live: bool = True
    scopes: dict[str, list[str]] = Field(default_factory=dict)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    primitive: str
    params: dict[str, Any] = Field(default_factory=dict)
    next: str | None = None
    branches: dict[str, str] = Field(default_factory=dict)
    blueprint_steps: list[int] = Field(default_factory=list)
    checkpoint: bool = False


class Graph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: str
    nodes: dict[str, Node]

    @model_validator(mode="after")
    def _link(self) -> Graph:
        for node_id, node in self.nodes.items():
            node.id = node_id
        if self.entry not in self.nodes:
            raise ValueError(f"entry node {self.entry} is not defined")
        for node in self.nodes.values():
            for target in [node.next, *node.branches.values()]:
                if target is not None and target not in self.nodes:
                    raise ValueError(f"{node.id} points at unknown node {target}")
        return self


class ExternalBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dependency: str
    needed: str
    kind: Literal["credentials", "partner_access", "provider_approval", "domain_review", "regulatory", "customer_system"]


class SkillContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    version: str
    title: str
    domain: str
    risk_tier: str = Field(pattern=r"^R[0-4]$")
    regulated: bool = False
    activation_modes: list[str]
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    connectors: list[ConnectorRequirement] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    default_policy: dict[str, Any] = Field(default_factory=dict)
    graph: Graph
    # Blueprint failure branch -> the graph node or rule that handles it.
    failure_handling: dict[str, str]
    metrics: list[str]
    declared_status: CapabilityStatus = "implemented"
    blockers: list[ExternalBlocker] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    # Hours of manual effort per unit, used only as a labelled estimate in ROI reports.
    baseline_minutes_per_task: float | None = None
