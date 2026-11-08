# Gluons agent entry point

Read `CLAUDE.md` for engineering invariants and `docs/execution/README.md` for the current execution package before making changes. Read any more specific `AGENTS.md` in the area you edit.

The November 8 execution package reconciles the newer founder product/onboarding priorities with the implemented platform. It is a plan, not proof of completion. Current code, tests, dated evidence and architecture decisions determine implementation status. `gluons_blueprint/` remains a read-only historical specification; record implementation changes in ADRs and current traceability rather than rewriting it.

- Preserve the authenticated Principal, forced tenant RLS, owner policy/approval, budget and gateway/readback boundaries.
- Keep provider doubles, simulator, real sandbox and production evidence distinct.
- Follow `docs/execution/05_AGENT_WORKING_AGREEMENT.md` for shared-file/migration ownership and handoffs.
- Work on one dependency-ready package. Do not duplicate existing modules or merge the older `kowstav/gluons` prototype runtime into this project.
- Do not publish `GLUONS_COMPLETE_FOUNDER_AND_PRODUCT_MASTER_CONTEXT_*.md`, credentials, runtime data or raw private provider evidence.
- Cloud spending, paid provider actions and real external messages require the applicable explicit owner authorization. Local tests and reversible implementation work can proceed within assigned scope.
- These documents provide guidance for assigned agents; they do not automatically authorize spawning agents, changing sharing, or executing all future work packages.
