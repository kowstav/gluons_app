"""Structural guardrails for the commerce launch blueprint (not integration tests)."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_business_launch_blueprint_explicit_owner_gates():
    text = (ROOT / 'docs/14_business_launch_owner_portal_and_identity.md').read_text()
    for phrase in ('client transfer', 'Shopify Payments', 'Stripe', 'supplier', 'owner', 'MCP', 'mobile', 'provider', 'KYC', 'fulfillmentCreate'):
        assert phrase.lower() in text.lower(), phrase


def test_business_launch_evals_include_security_and_payment():
    data = json.loads((ROOT / 'playbooks/commerce_business_launch/evals.json').read_text())
    ids = {entry['id'] for entry in data['cases']}
    for case in ('wrong_org', 'merchant_kyc_pending', 'supplier_outreach_not_approved', 'test_checkout_not_live', 'mobile_fallback'):
        assert case in ids
    assert len(ids) >= 10
