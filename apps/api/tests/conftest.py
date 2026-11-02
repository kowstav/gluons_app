"""Tests run against real PostgreSQL, as the restricted application role, so row level
security is genuinely in force. The sandbox ERP runs as a real HTTP server on a real port.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DATA = Path(tempfile.mkdtemp(prefix="gluons-test-"))


@pytest.fixture(scope="session", autouse=True)
def database():
    from gluons import devdb

    db = devdb.start(REPO_ROOT / ".pgdata" / "test", database=f"gluons_test_{os.getpid()}")
    os.environ.update(db.environment())
    os.environ["GLUONS_ENV"] = "test"
    os.environ["GLUONS_MAIL_DIR"] = str(TEST_DATA / "mail")
    devdb.migrate(db)

    from gluons.config import get_settings
    from gluons.db.session import reset_engines
    from gluons.skills.registry import get_registry

    get_settings.cache_clear()
    reset_engines()
    get_registry.cache_clear()
    yield db
    reset_engines()
    shutil.rmtree(TEST_DATA, ignore_errors=True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def sandbox_erp():
    os.environ["SANDBOX_ERP_DB"] = str(TEST_DATA / "sandbox_erp.sqlite3")
    sys.path.insert(0, str(REPO_ROOT / "apps" / "sandbox_erp"))
    import uvicorn
    from sandbox_erp import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.05)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


class Erp:
    def __init__(self, base: str) -> None:
        self.base = base

    def fault(self, **faults) -> None:
        httpx.post(f"{self.base}/__sim/fault", json=faults, timeout=5).raise_for_status()

    def orders(self) -> list[dict]:
        return httpx.get(f"{self.base}/__sim/orders", timeout=5).json()["orders"]

    def reset(self) -> None:
        httpx.post(f"{self.base}/__sim/reset", timeout=5).raise_for_status()


@pytest.fixture
def erp(sandbox_erp) -> Erp:
    client = Erp(sandbox_erp)
    client.reset()
    return client


@pytest.fixture
def world(database):
    from helpers import World

    return World.create()
