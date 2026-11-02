# Test results

Results are recorded from actual runs, newest first. Provider-double tests are labelled as such and prove Gluons' handling only, not the provider integration.

## Baseline before any implementation (2026-11-02)
Blueprint package, Python 3.13.3 on Windows:

- `pytest` in `gluons_blueprint/`: 10 passed, 1 failed. The failure is `test_permission_spec_has_all_four_layers`, caused by the test reading a UTF-8 file with the Windows default encoding, not by the specification.
- `validate_pack.py`: passed (31 skills, 31 x 6 scenario manifests).
- `scripts/audit_repo_registry.py`: passed (24 candidates, 31 mappings; no upstream code audited).
- The reference API and demo were simulations with no real authentication, persistence or integrations.
