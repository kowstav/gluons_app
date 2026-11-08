# Agent G — independent quality, skill evidence and economics

Own EX-10/14 and verification of all gates; support EX-15. Read verification/measurement guides, `skills/evaluations.py`, `skills/registry.py`, `skills/catalog.py`, domain code/contracts/evals, `model_gateway.py`, `trials.py`, `reports.py`, `billing.py` and current test evidence.

## Deliverables

1. Independent requirement-to-test matrix for the next gate, with representative failures and adversarial/untrusted-input cases.
2. Meaningful domain reference cases: at least ten task-specific cases plus the blueprint's six universal failure categories for each promoted skill. Include ambiguous, missing, conflicting, unsafe and multilingual/noisy input where relevant to the pilot.
3. Versioned expert review record: domain/jurisdiction, reviewer role, reviewed procedure/cases, changes, limits, date and re-review trigger. No fictional expert endorsements.
4. Model/skill evaluation with held-out cases, structured-output correctness, action correctness, error recovery, human correction time, latency and measured full attempt costs. Reject failing/unevaluated models.
5. Real pilot outcome/cost report with denominators, failure categories, interventions, repeated usage and actual payment status.
6. Release review and rollback decision based on evidence; update status through A after integration.

## Acceptance

- MODEL-01 and SKILL-01 pass; verify relevant AUTH/SETUP/FLOW/COM/OPS cases independently.
- A preparation packet does not activate an external workflow; `implemented`, sandbox and live evidence levels remain distinct.
- Do not let an evaluator certify itself by reusing its own training/examples as held-out proof. Golden answers and provider facts have provenance.
- Model changes preserve approval and risk rules, run regression, pin the resulting version, and support rollback/canary. No automatic production promotion simply because a newer model exists.
- Count costs from failed attempts and recovery; distinguish measured, estimated and unknown usage. Reconcile ledger totals with provider evidence when available.
- Trial success measures autonomous success and correct human escalation separately. No invented time saved, ARR or recurring revenue.
- New performance/security tests target actual risks, not copies of implementation details or superficial coverage counts.

## Scope and coordination

Do not independently rewrite provider/runtime modules while reviewing them. File actionable findings against exact paths/scenarios and coordinate fixes with the owner. A owns central status/traceability. Founder chooses customer/system and authorizes real data/model spend; G can complete fixtures and evaluation harness without those prerequisites. Regulated-domain skills remain administrative review aids until their explicit domain gates pass.

For EX-15 choose one evidence-backed adjacent workflow from the expansion map. Do not promote all 30 preparation skills at once. Report expert review, provider action graph and real-provider evidence as separate requirements.
