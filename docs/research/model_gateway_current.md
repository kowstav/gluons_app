# Model gateway — 2026-11-02

The gateway implements JSON inference, provider restrictions, quality gates,
versioned-price accounting, bounded fallback and PostgreSQL budget reservations.
No provider was called with a real credential during implementation. Contract
tests use explicitly labelled HTTP provider doubles; no model quality, price or
production readiness is inferred from those tests.

Official sources opened during implementation:

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs): Responses API JSON schema in `text.format`; response validation and incomplete/refusal handling. The adapter sends `store:false`, no tools and a bounded output limit.
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs): Messages `output_config.format` with JSON schema.
- [Claude API versions](https://platform.claude.com/docs/en/api/versioning): the adapter pins `anthropic-version:2026-10-21` and omits removed sampling controls.
- [Google structured output](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/control-generated-output): JSON MIME type plus response schema and documented REST generateContent route. The adapter uses scoped Application Default Credentials through the existing optional Google auth dependency, validates project/region, and translates to Google's supported schema subset. Recursive/external schema references are rejected; returned output is always validated locally.
- [vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/): the private adapter uses OpenAI-compatible Chat Completions with JSON schema. Compatibility of a particular private endpoint remains to be verified.

Model identifiers and rates come only from operator-reviewed `ModelDescriptor`
rows; none are automatically seeded from memory. A model needs a passing latest
`ModelEvalResult` for the skill before routing or selection. Organization data
provider restrictions are a hard ceiling; request policy may only narrow them.
`gluons.models` is an optional OAuth scope, excluded from default assistant
consent. A role must independently permit `models:manage`.

Token and cache counts are provider measurements. Costs calculated from those
counts and registered rates carry `cost_basis=token_rate_estimate`, not a claim of
invoice verification. Failed ambiguous calls reserve their estimated cost in
the usage ledger pending provider reconciliation, so fallback cannot silently
discard potential spend. Invalid and incomplete output cannot become success.
Prompts, provider keys and response bodies are not stored in usage records.

Remaining work: reviewed effective-dated model descriptors, real provider
credentials saved securely, genuinely machine-graded skill-specific model
benchmarks, drift/evaluation-suite invalidation, real sandbox smoke tests and
provider invoice reconciliation. The blueprint's existing prose scenario
manifests cannot honestly certify model extraction quality; `evaluate_candidate`
returns `MODEL_EVALUATION_REQUIRED` until those domain benchmarks exist. Current
business skills continue to use deterministic implementations or explicit human
review rather than invented model results.
