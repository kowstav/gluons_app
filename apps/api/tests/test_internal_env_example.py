"""The configuration template stays complete, and holds placeholders only."""
from __future__ import annotations

import re
from pathlib import Path

from dotenv import dotenv_values

from gluons.config import Settings

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".env.example"


def test_every_setting_is_documented():
    text = TEMPLATE.read_text(encoding="utf-8")
    missing = [f"GLUONS_{name.upper()}" for name in Settings.model_fields if not re.search(rf"\bGLUONS_{name.upper()}\b", text)]
    assert missing == []


def test_active_lines_hold_no_secret_values():
    sensitive = re.compile(r"SECRET|TOKEN|PASSWORD|API_KEY|_KEY_B64|KEK|DATABASE_URL|SMTP_URL")
    values = dotenv_values(TEMPLATE)
    assert values, "the template should have active lines"
    filled = {name for name, value in values.items() if sensitive.search(name) and value}
    assert filled == set()


def test_a_copy_of_the_template_boots(monkeypatch):
    """Copying the file to .env is the documented first step, so it must parse."""
    monkeypatch.delenv("GLUONS_ENV", raising=False)
    settings = Settings(_env_file=TEMPLATE)
    assert settings.env == "local"
    assert settings.mail_transport == "file"
