"""Static blueprint contract tests; do not imply real provider authorization is working."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / 'docs' / '13_permissions_connections_and_mobile_onboarding.md').read_text()


def test_permission_spec_has_all_four_layers():
    for term in ["Assistant → Gluons", "Gluons → provider", "Owner → automation", "Network → AI phone agent"]:
        assert term in DOC


def test_mobile_fallback_and_provider_flows_are_present():
    for term in ["web-only", "responsive web", "Google OAuth", "Embedded Signup", "Twilio", "ElevenLabs", "carrier", "single-use", "PKCE"]:
        assert term.lower() in DOC.lower()


def test_all_p0_cases_listed():
    for prefix, start, stop in [("AUTH", 1, 10), ("WA", 1, 2), ("PHONE", 1, 4), ("POL", 1, 3)]:
        for i in range(start, stop + 1):
            assert f"{prefix}-{i:02d}" in DOC


def test_json_permission_schemas_valid():
    for name in ["connection_session", "automation_permission", "phone_route"]:
        schema = json.loads((ROOT / 'schemas' / f'{name}.schema.json').read_text())
        assert schema['type'] == 'object'
        assert schema['additionalProperties'] is False
        assert schema['required']
        assert 'description' in schema
