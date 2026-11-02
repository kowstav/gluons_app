"""Local Postgres without Docker, for development and tests on any machine.

Uses the embedded server from `pixeltable-pgserver` (real PostgreSQL, real row level
security, pgvector included). `docker-compose.yml` is the equivalent path when Docker
is available; hosted environments use Cloud SQL. Not imported by application code.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pixeltable_pgserver as pgserver
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from gluons.config import REPO_ROOT

APP_PASSWORD = "gluons_app_dev"  # noqa: S105 - local throwaway role
SYSTEM_PASSWORD = "gluons_system_dev"  # noqa: S105 - local throwaway role


@dataclass(frozen=True)
class LocalDatabase:
    migration_url: str
    app_url: str
    system_url: str
    server: object

    def environment(self) -> dict[str, str]:
        return {
            "GLUONS_MIGRATION_DATABASE_URL": self.migration_url,
            "GLUONS_DATABASE_URL": self.app_url,
            "GLUONS_SYSTEM_DATABASE_URL": self.system_url,
        }


def start(data_dir: Path, database: str = "gluons") -> LocalDatabase:
    data_dir.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data_dir, cleanup_mode=None)
    admin = make_url(server.get_uri()).set(drivername="postgresql+psycopg")
    with create_engine(admin, isolation_level="AUTOCOMMIT").connect() as conn:
        exists = conn.execute(text("select 1 from pg_database where datname = :d"), {"d": database}).scalar()
        if not exists:
            conn.execute(text(f'create database "{database}"'))
    target = admin.set(database=database)
    render = lambda u: u.render_as_string(hide_password=False)  # noqa: E731
    app = target.set(username="gluons_app", password=APP_PASSWORD)
    system = target.set(username="gluons_system", password=SYSTEM_PASSWORD)
    return LocalDatabase(render(target), render(app), render(system), server)


def migrate(db: LocalDatabase) -> None:
    env = {**os.environ, **db.environment(), "GLUONS_DEV_ROLE_PASSWORDS": "1"}
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT / "apps" / "api",
        env=env,
        check=True,
    )


def main() -> None:
    db = start(REPO_ROOT / ".pgdata" / "dev")
    migrate(db)
    for key, value in db.environment().items():
        print(f"{key}={value}")  # noqa: T201 - this is the tool's output


if __name__ == "__main__":
    main()
