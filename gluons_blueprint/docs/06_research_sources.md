# 06 — Research sources, provenance, and constraints (checked November 2026)

All primary references below were inspected or found in web research during preparation. Product versions and pricing can change; pin API versions and review documentation during implementation.

## YC
- https://www.ycombinator.com/apply — current batch, application timing.
- https://www.ycombinator.com/video — official distinction between founder video and demo.
- https://www.ycombinator.com/howtoapply — YC guidance that founders should understand objections and show actual work.

## Agent and plugin interoperability
- https://developers.openai.com/plugins — plugin architecture: skills, MCP servers, interfaces.
- https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt — host and plan limits for write actions. Verify current tier before promising support.
- https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp — remote Claude connectors and per-account availability.
- https://blog.modelcontextprotocol.io/posts/2026-07-28/ — July 28, 2026 MCP revision and tasks extension, auth hardening.
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/client/elicitation.mdx — URL-mode elicitation for external auth, never obtain passwords in chat.

## Google Cloud
- https://docs.cloud.google.com/run/docs/ai-agents — Cloud Run service/job/worker use cases.
- https://docs.cloud.google.com/run/docs/configuring/request-timeout — service request timeouts, not a workflow durability guarantee.
- https://docs.cloud.google.com/workflows/quotas — Cloud Workflows maximum duration and retention.
- https://docs.cloud.google.com/pubsub/docs/choosing-pubsub-or-cloud-tasks — at-least-once delivery and appropriate event vs scheduled task distinction.
- https://docs.cloud.google.com/sql/docs/postgres/ai-overview — Cloud SQL pgvector and Vertex integration.
- https://docs.cloud.google.com/architecture/multi-tenant-agentic-ai-system — Google reference architecture for multi-tenant agent applications.
- https://docs.cloud.google.com/run/docs/securing/multi-tenant — stronger isolation recommended for executing untrusted customer code.

## Voice provider
- https://elevenlabs.io/docs/eleven-agents/overview — tools, workflows, supported voice platform.
- https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/register-call — Twilio register-call method and transfer caveat.
- https://elevenlabs.io/docs/eleven-agents/customization/tools/webhook-tools — authenticated tool callbacks.
- https://elevenlabs.io/docs/eleven-agents/workflows/post-call-webhooks — authenticated HMAC webhooks, retries and events.
- https://elevenlabs.io/pricing/agents — per-minute charges plus separate LLM and telephone costs; recheck current rates before quoting to buyers.

## OAuth and security
- https://developers.google.com/workspace/gmail/api/auth/scopes — read scopes can be restricted; verification and potentially assessment required.
- https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ — agentic threat model.

## Competitive market
- https://zapier.com — broad app and MCP automations; verify exact integration count in current pricing/docs before public claims.
- https://n8n.io — workflow builder and MCP/agent integrations.
- https://composio.dev — agent auth/tool integration category.
- https://www.gumloop.com — AI workflows and MCP.
- https://elevenlabs.io/docs/eleven-agents/operate/architect — ElevenLabs agentic authoring and self-improvement feature.

## Sources for skill market analogues
The 31 Gluons domain skill specifications are original design documents based on generic publicly described operational categories, including those associated with the 30 startup examples from the earlier research. They are not copies of those startups' internal implementations and contain no unsupported claim of proprietary access. Before production, validate every industry-specific claim with primary customer workflows, vendor API rules, specialist reviewers and jurisdictional law.

## Additional market adoption and closest competitor updates
- https://www.gov.uk/government/publications/ai-adoption-survey-2026/ai-adoption-survey-report-2026-executive-summary — UK business AI adoption, trust and ROI barriers in 2026.
- https://www.uschamber.com/technology/2026-empowering-small-business-report — US 2026 SMB technology survey, a different population.
- https://help.zapier.com/hc/en-us/articles/48308034391821-What-is-Zapier-MCP — Zapier MCP can discover actions, build recurring workflows and use existing connections.
- https://zapier.com/mcp — reusable Skills and AI-host interoperability: important overlap with Gluons.

## GitHub-first reuse sources (added November 2, 2026)

See `docs/11_github_repository_inventory.md` for repo-by-repo upstream source links and exact license-evidence URLs. Critical restrictive license evidence: https://support.n8n.io/article/can-i-use-your-license-for-my-use-case ; https://github.com/windmill-labs/windmill/blob/main/LICENSE . MIT core Activepieces licensing: https://www.activepieces.com/docs/about/license . OR-Tools Apache 2.0: https://github.com/google/or-tools . Docling MIT (model caveat): https://github.com/docling-project/docling .
