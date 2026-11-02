from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from gluons.config import Settings
from gluons.db.models import Base

target_metadata = Base.metadata


def url() -> str:
    return os.environ.get("GLUONS_MIGRATION_DATABASE_URL") or Settings().migration_database_url


def run_offline() -> None:
    context.configure(url=url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
