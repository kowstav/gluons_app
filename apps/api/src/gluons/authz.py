"""Who is acting and what they may do.

A `Principal` always comes from verified authentication. `tenant_id` lives here and
nowhere else: request bodies, tool arguments and model output can never set it.
An assistant's effective rights are its member's role intersected with its OAuth
scopes, so a token can only ever narrow what the person could do themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from gluons.core import forbidden


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    APPROVER = "approver"
    OPERATOR = "operator"
    VIEWER = "viewer"
    FIELD_REP = "field_rep"
    SUPPORT = "support"


READ_PERMISSIONS = frozenset(
    {
        "org:read",
        "skills:read",
        "connections:read",
        "plans:read",
        "automations:read",
        "tasks:read",
        "policies:read",
        "approvals:read",
        "trials:read",
        "launch:read",
        "knowledge:read",
        "reports:read",
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.OWNER: READ_PERMISSIONS
    | {
        "org:manage",
        "org:delete",
        "members:manage",
        "assistants:manage",
        "connections:manage",
        "phone:manage",
        "plans:write",
        "automations:write",
        "automations:activate",
        "policies:write",
        "policies:sign",
        "tasks:control",
        "approvals:decide",
        "billing:read",
        "billing:manage",
        "models:manage",
        "audit:read",
        "audit:export",
        "data:export",
        "knowledge:write",
        "trials:write",
        "launch:write",
        "emergency:stop",
        "assist:consent",
    },
    # Technical administration inside delegated limits: no legal or spend authority.
    Role.ADMIN: READ_PERMISSIONS
    | {
        "members:manage",
        "assistants:manage",
        "connections:manage",
        "phone:manage",
        "plans:write",
        "automations:write",
        "policies:write",
        "tasks:control",
        "billing:read",
        "models:manage",
        "audit:read",
        "knowledge:write",
        "trials:write",
        "launch:write",
        "emergency:stop",
    },
    Role.APPROVER: READ_PERMISSIONS | {"approvals:decide", "audit:read", "billing:read"},
    Role.OPERATOR: READ_PERMISSIONS | {"plans:write", "automations:write", "tasks:control", "knowledge:write", "trials:write"},
    Role.VIEWER: READ_PERMISSIONS | {"audit:read", "billing:read"},
    Role.FIELD_REP: frozenset(
        {"org:read", "skills:read", "connections:read", "plans:read", "plans:write", "automations:write", "trials:read", "trials:write", "assist:use"}
    ),
    Role.SUPPORT: frozenset({"org:read", "skills:read", "tasks:read", "reports:read"}),
}

# Requested by assistants during OAuth consent. Each maps to the permissions it unlocks.
ASSISTANT_SCOPES: dict[str, frozenset[str]] = {
    "gluons.read": READ_PERMISSIONS | {"billing:read"},
    "gluons.plan": frozenset({"plans:write", "automations:write", "trials:write", "launch:write", "knowledge:write"}),
    "gluons.tasks": frozenset({"tasks:control"}),
    "gluons.connections": frozenset({"connections:manage", "phone:manage"}),
    "gluons.approvals": frozenset({"approvals:decide"}),
    "gluons.activate": frozenset({"automations:activate"}),
    "gluons.models": frozenset({"models:manage"}),
}
DEFAULT_ASSISTANT_SCOPES = ("gluons.read", "gluons.plan", "gluons.tasks")

# Authority that stays on the Gluons-owned web surface regardless of granted scopes.
WEB_ONLY_PERMISSIONS = frozenset(
    {
        "policies:sign",
        "billing:manage",
        "members:manage",
        "assistants:manage",
        "org:delete",
        "data:export",
        "audit:export",
    }
)

RISK_ORDER = ("R0", "R1", "R2", "R3", "R4")


def risk_at_most(tier: str, limit: str) -> bool:
    return RISK_ORDER.index(tier) <= RISK_ORDER.index(limit)


@dataclass(frozen=True)
class Principal:
    kind: str  # user | assistant | system | webhook
    tenant_id: str
    user_id: str | None = None
    role: Role | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    binding_id: str | None = None
    session_id: str | None = None
    approval_tier_limit: str | None = None

    @property
    def actor_id(self) -> str:
        return self.user_id or self.kind

    @property
    def via(self) -> str:
        return "assistant" if self.kind == "assistant" else "web" if self.kind == "user" else self.kind

    def permissions(self) -> frozenset[str]:
        if self.kind == "system":
            return frozenset()
        base = ROLE_PERMISSIONS.get(self.role, frozenset()) if self.role else frozenset()
        if self.kind != "assistant":
            return base
        allowed: set[str] = set()
        for scope in self.scopes:
            allowed |= ASSISTANT_SCOPES.get(scope, frozenset())
        return (base & allowed) - WEB_ONLY_PERMISSIONS

    def can(self, permission: str) -> bool:
        return permission in self.permissions()

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise forbidden(f"This requires the '{permission}' permission.")


def system_principal(tenant_id: str) -> Principal:
    return Principal(kind="system", tenant_id=tenant_id)
