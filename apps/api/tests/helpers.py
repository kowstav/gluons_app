"""Builds real tenants through the same service functions production uses."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import update

from gluons import identity, policy
from gluons.authz import Principal
from gluons.core import new_id, utcnow
from gluons.db.models import CatalogItem, Connection, Customer, Membership, User
from gluons.db.session import public_tx, tenant_tx
from gluons.runtime import lifecycle, worker

PASSWORD = "correct-horse-battery"  # noqa: S105


def make_user(email: str | None = None, name: str = "Test Owner") -> tuple[str, str]:
    address = email or f"{new_id('user')}@example.com"
    identity.register(address, PASSWORD, name)
    with public_tx() as session:
        session.execute(update(User).where(User.email == address).values(email_verified_at=utcnow()))
        from sqlalchemy import select

        user_id = session.execute(select(User.id).where(User.email == address)).scalar_one()
    return user_id, address


def add_member(tenant_id: str, user_id: str, role: str) -> Principal:
    with tenant_tx(tenant_id, user_id) as session:
        session.add(Membership(id=new_id("mem"), tenant_id=tenant_id, user_id=user_id, role=role))
    return identity.principal_for_user(user_id, tenant_id)


@dataclass
class World:
    user_id: str
    email: str
    tenant_id: str
    owner: Principal
    connections: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, name: str = "Acme Foods") -> World:
        user_id, email = make_user()
        tenant_id = identity.create_organization(user_id, name)
        world = cls(user_id, email, tenant_id, identity.principal_for_user(user_id, tenant_id))
        world.seed_catalog()
        return world

    def seed_catalog(self) -> None:
        with tenant_tx(self.tenant_id) as session:
            session.add_all(
                [
                    CatalogItem(id=new_id("cat"), tenant_id=self.tenant_id, sku="FISH-1", name="Atlantic Salmon Fillet", unit="case",
                                aliases=["salmon", "salmon fillet"], unit_price_minor=8900, in_stock=100),
                    CatalogItem(id=new_id("cat"), tenant_id=self.tenant_id, sku="RICE-2", name="Jasmine Rice 20kg", unit="bag",
                                aliases=["jasmine rice", "rice"], unit_price_minor=3100, in_stock=4),
                    CatalogItem(id=new_id("cat"), tenant_id=self.tenant_id, sku="OIL-A", name="Olive Oil Tin", unit="each",
                                aliases=["oil"], unit_price_minor=1500, in_stock=50),
                    CatalogItem(id=new_id("cat"), tenant_id=self.tenant_id, sku="OIL-B", name="Sunflower Oil Tin", unit="each",
                                aliases=["oil"], unit_price_minor=900, in_stock=50),
                ]
            )
            session.add(
                Customer(
                    id=new_id("cus"), tenant_id=self.tenant_id, name="Harbor Bistro",
                    emails=["orders@harbor-bistro.com"], phones=["+15550100"], alias_map={"the usual fish": "FISH-1"},
                )
            )

    def connect_erp(self, base_url: str, *, confirmed: bool = True) -> str:
        from gluons import vault

        with tenant_tx(self.tenant_id, self.user_id) as session:
            credential = vault.store(
                session, self.tenant_id, "erpnext", {"api_key": "sandbox-key", "api_secret": "sandbox-secret"}
            )
            connection = Connection(
                id=new_id("con"), tenant_id=self.tenant_id, provider="erpnext", environment="simulator",
                external_account_id=base_url, account_display="Sandbox ERP (simulator)",
                account_confirmed_at=utcnow() if confirmed else None,
                granted_scopes=[], credential_ref=credential, metadata_json={"base_url": base_url}, created_by=self.user_id,
            )
            session.add(connection)
        self.connections["erp"] = connection.id
        return connection.id

    def order_automation(self, **kwargs) -> dict:
        return lifecycle.create(
            self.owner,
            skill_id="multichannel_order_entry",
            config={"connections": dict(self.connections), "currency": "USD"},
            **kwargs,
        )

    def grant(self, automation_id: str, mode: str = "supervised", **overrides) -> str:
        spec = {"mode": mode, "allowed_actions": ["erp.order.create"], **overrides}
        with tenant_tx(self.tenant_id, self.user_id) as session:
            proposed = policy.propose(session, self.owner, automation_id, spec)
            policy.sign(session, self.owner, proposed.id)
            return proposed.id

    def supervised_automation(self, **grant_overrides) -> dict:
        automation = self.order_automation()
        self.grant(automation["id"], "supervised", **grant_overrides)
        current = lifecycle.get(self.owner, automation["id"])
        return lifecycle.activate(self.owner, automation["id"], "supervised", current["version"])


def order_event(world: World, automation_id: str, event_id: str = "mail-1", text: str = "2 cases FISH-1\n1 bag rice"):
    return lifecycle.submit_event(
        world.tenant_id,
        automation_id,
        source="email",
        source_event_id=event_id,
        payload={"text": text, "sender": "orders@harbor-bistro.com", "channel": "email"},
    )


def drain() -> int:
    return worker.drain()
