"""Database access. Two engines keep tenant isolation enforced by Postgres itself.

`tenant_tx` connects as `gluons_app`, a role with row level security forced on: a
query that forgets a tenant filter still returns only the current tenant's rows.
`system_tx` connects as `gluons_system` (explicit worker RLS policies) for work with no
tenant context yet: claiming queue jobs and routing inbound webhooks to a tenant.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from gluons.config import get_settings


@lru_cache
def app_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)


@lru_cache
def system_engine() -> Engine:
    return create_engine(get_settings().system_database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)


@lru_cache
def lock_engine() -> Engine:
    # Locks span provider calls, so they must not occupy the application pool:
    # otherwise each lock holder could starve its own short DB transactions.
    return create_engine(get_settings().database_url, poolclass=NullPool)


def reset_engines() -> None:
    """Tests swap databases between sessions."""
    for factory in (app_engine, system_engine, lock_engine):
        try:
            factory().dispose()
        except Exception:  # noqa: BLE001, S110 - engine may never have been created
            pass
        factory.cache_clear()


def _bind_context(session: Session, tenant_id: str | None, user_id: str | None) -> None:
    session.execute(
        text("select set_config('app.tenant_id', :t, true), set_config('app.user_id', :u, true)"),
        {"t": tenant_id or "", "u": user_id or ""},
    )


@contextmanager
def tenant_tx(tenant_id: str, user_id: str | None = None) -> Iterator[Session]:
    """One transaction scoped to a tenant. `set_config(..., true)` is transaction local."""
    if not tenant_id:
        raise ValueError("tenant_tx requires a tenant id")
    factory = sessionmaker(bind=app_engine(), expire_on_commit=False)
    with factory() as session, session.begin():
        _bind_context(session, tenant_id, user_id)
        yield session


@contextmanager
def user_tx(user_id: str) -> Iterator[Session]:
    """A user with no selected tenant: sees global tables and their own memberships."""
    factory = sessionmaker(bind=app_engine(), expire_on_commit=False)
    with factory() as session, session.begin():
        _bind_context(session, None, user_id)
        yield session


@contextmanager
def public_tx() -> Iterator[Session]:
    """No tenant, no user: only global tables such as the skill catalog and sign-in."""
    factory = sessionmaker(bind=app_engine(), expire_on_commit=False)
    with factory() as session, session.begin():
        _bind_context(session, None, None)
        yield session


@contextmanager
def system_tx() -> Iterator[Session]:
    factory = sessionmaker(bind=system_engine(), expire_on_commit=False)
    with factory() as session, session.begin():
        yield session


@contextmanager
def operation_lock(key: str) -> Iterator[bool]:
    """Cross-process exclusion across short DB transactions and a provider call.

    A dedicated PostgreSQL session owns this lock. It is released on process death;
    the durable write-ahead receipt still requires provider lookup on recovery.
    Contenders return to the durable queue instead of waiting on a pool connection.
    """
    with lock_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = connection.execute(text("select pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": key}).scalar_one()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("select pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": key})
