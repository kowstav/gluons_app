"""Local harness setup does not use provider credentials or send a message."""
from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

from gluons.config import Settings

_path = Path(__file__).resolve().parents[3] / "scripts" / "gmail_proof_dev.py"
_spec = importlib.util.spec_from_file_location("gmail_proof_dev", _path)
assert _spec and _spec.loader
dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dev)


@pytest.fixture(scope="session", autouse=True)
def database():
    yield


def test_workspace_must_be_explicitly_new_or_resumed(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    data, private = dev.workspace_paths("trial-1", resume=False)
    assert data.parent == tmp_path / ".pgdata" and private.parent == tmp_path / ".gluons-data"
    data.mkdir(parents=True)
    private.mkdir(parents=True)
    with pytest.raises(ValueError):
        dev.workspace_paths("trial-1", resume=False)
    assert dev.workspace_paths("trial-1", resume=True) == (data, private)
    with pytest.raises(ValueError):
        dev.workspace_paths("../outside", resume=False)
    with pytest.raises(ValueError):
        dev.workspace_paths("missing", resume=True)


def test_harness_requires_nondefault_local_keys():
    key_a = base64.b64encode(b"a" * 32).decode()
    key_b = base64.b64encode(b"b" * 32).decode()
    dev.check_local_keys(Settings(_env_file=None, env="local", vault_kek_b64=key_a, signing_key_b64=key_b))
    with pytest.raises(ValueError):
        dev.check_local_keys(Settings(_env_file=None, env="local"))
    with pytest.raises(ValueError):
        dev.check_local_keys(Settings(_env_file=None, env="production", vault_kek_b64=key_a, signing_key_b64=key_b))
    with pytest.raises(ValueError):
        dev.check_local_keys(Settings(_env_file=None, env="local", vault_kek_b64=key_a, signing_key_b64=key_a))
