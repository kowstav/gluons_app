# Gluons research: MCP, Claude, ChatGPT, Google OAuth, Gmail, GCP
Checked 2026-11-02 against official sources. Example hosts (`mcp.gluons.example`) and scope names (`gluons.read`) are placeholders.
Method: SDK code, the quota numbers, the OpenAI redirect wording and the OpenAI help-center FAQ were read from raw page text. Other Google and Claude values came through a fetch tool, so spot-check any value you hard-code.
The blueprint itself was not supplied. Only claims quoted in the research brief, and assumptions implied by it, were compared.

## 0. Blueprint conflicts
| # | Blueprint claim or implied assumption | Verdict | Current fact |
|---|---|---|---|
| 1 | Latest MCP revision is 2026-07-28 | CONFIRMED | `2026-07-28` is "Current"; the draft changelog is empty |
| 2 | "Custom MCP connections in ChatGPT are web-only" | CONFIRMED, narrow it | FAQ wording: "Can I connect custom MCP servers on mobile? No - web only." That covers connecting. Reviewed plugins are a separate path and must work on mobile (section 4) |
| 3 | `Mcp-Session-Id`, `initialize`, GET stream are current | OUTDATED | All removed in 2026-07-28. They survive only for legacy clients, which the SDK serves automatically |
| 4 | DCR is the registration method | OUTDATED | DCR is Deprecated. CIMD is the preferred path; both hosts still accept DCR as a fallback |
| 5 | `FastMCP`, `FastMCP(stateless_http=True)` | OUTDATED | `mcp` 2.x renamed it `MCPServer`; the old import raises `ModuleNotFoundError`; transport options moved to `streamable_http_app()` |
| 6 | `stateless_http` enables stateless mode | MISLEADING | It affects the legacy path only. The 2026-07-28 path is sessionless by construction |
| 7 | Tasks are part of the core protocol | OUTDATED | Moved to extension `io.modelcontextprotocol/tasks`; Python SDK does not implement it |
| 8 | URL elicitation uses `elicitationId`, completion notification, error `-32042` | OUTDATED | All three removed; the client retries the original request instead |
| 9 | ChatGPT Developer Mode is required | OUTDATED | "You don't need to turn on Developer Mode first." Add the server from Plugins |
| 10 | Sign in with ChatGPT is usable by third parties | NOT GENERALLY | Limited trial for "selected commercial partners"; waitlist via an OpenAI representative |

## 1. MCP specification 2026-07-28
Sources, under https://modelcontextprotocol.io/specification/2026-07-28 : /changelog | /basic/authorization | /basic/authorization/authorization-server-discovery | /basic/authorization/client-registration | /basic/authorization/security-considerations | /basic/transports/streamable-http | /basic/versioning | /client/elicitation | /server/tools | /schema
Also: https://modelcontextprotocol.io/specification/versioning | https://modelcontextprotocol.io/extensions/tasks/overview | https://modelcontextprotocol.io/extensions/client-matrix | https://github.com/modelcontextprotocol/ext-tasks

### 1.1 Authorization
- PRM (RFC 9728): MCP servers MUST implement it, with `authorization_servers` holding at least one issuer. Advertise via `resource_metadata` in `WWW-Authenticate` on a 401, or at a well-known URI.
- Client PRM lookup: the header, then `/.well-known/oauth-protected-resource/<mcp-path>`, then `/.well-known/oauth-protected-resource`.
- 401 shape: `WWW-Authenticate: Bearer resource_metadata="https://mcp.gluons.example/.well-known/oauth-protected-resource/mcp", scope="gluons.read"`.
- Servers SHOULD send `scope`. Client order is the challenge `scope`, then PRM `scopes_supported`. Do NOT put `offline_access` in either; it belongs in AS metadata `scopes_supported`.
- Insufficient scope: `403` with `WWW-Authenticate: Bearer error="insufficient_scope", scope="...", resource_metadata="...", error_description="..."`. Emit every needed scope at once.
- AS discovery, issuer with no path: `/.well-known/oauth-authorization-server`, then `/.well-known/openid-configuration`.
- AS discovery, issuer with path `/tenant1`: `/.well-known/oauth-authorization-server/tenant1`, `/.well-known/openid-configuration/tenant1`, `/tenant1/.well-known/openid-configuration`.
- The document's `issuer` MUST be identical to the identifier used to build the URL, or clients reject it.
- Registration priority (client side): pre-registered, then CIMD if `client_id_metadata_document_supported: true`, then DCR if `registration_endpoint` exists, then prompt the user.
- CIMD: `client_id` is an HTTPS URL with a path; the document MUST contain `client_id`, `client_name`, `redirect_uris`. The AS MUST verify its `client_id` equals the URL exactly, validate redirect URIs against it, and SHOULD mitigate SSRF and cache per HTTP headers.
- DCR is Deprecated and retained for compatibility. Clients MUST send `application_type` (`native` or `web`).
- PKCE: clients MUST use `S256` when capable and MUST refuse if `code_challenge_methods_supported` is absent. An AS MUST therefore advertise it, including on OIDC discovery.
- New: AS SHOULD return `iss` (RFC 9207) on authorization responses and set `authorization_response_iss_parameter_supported: true`. Clients MUST compare a present `iss` by exact string, with no slash or case normalization.
- Public-client refresh tokens MUST rotate. Redirect URIs MUST be localhost or HTTPS and match exactly.
- `resource` (RFC 8707): clients MUST send it on authorization and token requests, using the canonical server URI (`https://mcp.gluons.example/mcp`; no fragment; prefer no trailing slash).
- Audience: MCP servers MUST validate that the token was issued for them and reject others. Invalid or expired tokens get 401.
- Token passthrough is forbidden: the server "MUST NOT pass through the token it received from the MCP client" to upstream APIs. The Gmail token is a separate token held by Gluons.
- Tokens never go in a query string, and `Authorization` is required on every HTTP request.

### 1.2 Streamable HTTP
- One endpoint accepting POST; each JSON-RPC message is its own POST; `Accept` lists `application/json` and `text/event-stream`.
- `Mcp-Session-Id` is gone. A modern-only server ignores it, ignores `Last-Event-ID`, and answers GET and DELETE with `405`.
- Server-to-client requests are gone; the server returns `resultType: "input_required"` with `inputRequests` and optional `requestState`, and the client retries with `inputResponses`.
- Required POST headers: `MCP-Protocol-Version: 2026-07-28`, `Mcp-Method`, and `Mcp-Name` for `tools/call`, `resources/read`, `prompts/get`. Non-ASCII values use `=?base64?...?=`.
- Errors: header mismatch or missing header `400` with `-32020`; unsupported version `400` with `-32022` and `data.supported`; unknown method `404` with `-32601`.
- `_meta` on every request: `io.modelcontextprotocol/protocolVersion`, `.../clientCapabilities`, and SHOULD `.../clientInfo`. `server/discover` is mandatory on servers.
- `tools/list` MUST NOT vary per connection but MAY vary by authorization. List results require `ttlMs` and `cacheScope`; use `"private"` for per-tenant data.
- Origin: servers MUST validate `Origin` and return `403` when present and invalid. Send `X-Accel-Buffering: no` on SSE. Closing an SSE stream cancels the request; streams are not resumable.
- Dual-era: a request with modern `_meta` is served statelessly; `initialize` selects legacy semantics. Legacy clients cannot fall forward, so a modern-only server fails them.

### 1.3 Tasks, URL elicitation, annotations
- Tasks: extension `io.modelcontextprotocol/tasks`, schema `2026-07-28` marked Stable. Methods `tasks/get`, `tasks/update`, `tasks/cancel`; `resultType: "task"`; `tasks/list` and blocking `tasks/result` removed. Client matrix (community-maintained): Claude web and Claude Code yes; Claude Desktop and ChatGPT no.
- URL elicitation: capability `elicitation: {"form": {}, "url": {}}`; request fields `mode: "url"`, `message`, `url`. Empty `elicitation: {}` means form only. `accept` means consent to open, not completion.
- For third-party OAuth the page requires: the URL targets Gluons' own connect page, not Google's; the URL is not pre-authenticated; it carries no PII; the opener is verified to be the same user (compare `sub`) to prevent account takeover; tokens never reach the client.
- It is not for authorizing the client to the MCP server. Form mode MUST NOT collect secrets.
- `ToolAnnotations` (all hints, untrusted unless the server is trusted): `title`; `readOnlyHint` default `false`; `destructiveHint` default `true`; `idempotentHint` default `false`; `openWorldHint` default `true`. Destructive and idempotent hints apply only when not read-only.
- Tool names SHOULD be 1 to 128 characters of `A-Za-z0-9_-.`. Return serialized JSON in a text block alongside `structuredContent`. Business errors use `isError: true`.

## 2. Official MCP Python SDK
Sources: https://pypi.org/pypi/mcp/json, and under https://py.sdk.modelcontextprotocol.io : /run/asgi/ | /run/authorization/ | /run/deploy/ | /run/legacy-clients/ | /run/ | /migration/ | /whats-new/ | /get-started/installation/ | /servers/tools/ | /handlers/elicitation/ | /handlers/multi-round-trip/

- Version: `mcp` 2.3.0, uploaded 2026-10-02. v2.0.0 shipped 2026-07-28. 1.30.0 (2026-09-07) is the last 1.x seen. Pin `mcp>=2.3,<3`.
- Python: docs say 3.11+, while PyPI metadata says `>=3.10`. Use 3.11+.
- One app serves both eras, routed on `MCP-Protocol-Version`; missing header or a version up to `2025-11-25` goes to legacy. There is no option to disable either era.
- Host check: with no `transport_security=` only localhost is accepted. A real hostname gets `421 Misdirected Request` on every request, and a bad Origin gets `403`.
- `allowed_hosts` entries are exact; list both `host` and `host:*`. Include the `run.app` host if it is used. `TransportSecuritySettings(enable_dns_rebinding_protection=False)` is available behind a proxy that controls Host.
- Mounting: a mounted sub-app's lifespan never runs; the host app must enter `mcp.session_manager.run()` or the first request fails with `RuntimeError: Task group is not initialized`. `Mount("/")` must come last.
- `stateless_http=True`: legacy path only; no sessions, so no sticky routing, but `ctx.elicit()` raises `NoBackChannelError` and notifications drop (`json_response=True` has the same cost). Without it, sessions live in one worker's memory and other workers return `404 Session not found`. Gluons on Cloud Run should set it.
- `requestState`: each process generates its own key, so a retry reaching another instance fails `-32602`. Set `request_state_security=RequestStateSecurity(keys=[key])` (key at least 32 bytes, from `mcp.server.mcpserver`) with the same server name on all instances.
- `AuthSettings`: `issuer_url`, `resource_server_url`, `required_scopes`, `validate_token_resource` (unset warns and acts as `False`; 3.0 defaults to `True`). `token_verifier=` and `auth=` must be passed together.
- The SDK serves PRM at `/.well-known/oauth-protected-resource/mcp` and returns `WWW-Authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="..."`, with no `scope`. Auth covers the whole endpoint, so lazy auth needs your own gate in front.
- The SDK provides no login page, consent screen or token issuance; `auth_server_provider=` is discouraged. `@mcp.custom_route()` endpoints are never authenticated.
- Read the caller with `get_access_token()`: fields `client_id`, `scopes`, `subject`, `expires_at`, `claims`. Returns `None` outside an authenticated HTTP request. Supply `subject` consistently; `requestState` binding uses it.
- URL elicitation: `ctx.elicit_url(message, url, elicitation_id)` works only on a legacy connection with a back channel. On 2026-07-28 use `Elicit` dependencies or `InputRequiredResult`.
- Tasks: the SDK "does not implement yet" the extension. Do not design around it from Python.

Sketch. Docs show `Starlette(routes=[Mount("/", app=...)], lifespan=...)`; the FastAPI form below is an adaptation.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import AnyHttpUrl
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

RESOURCE = "https://mcp.gluons.example/mcp"

class GluonsVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        ...  # check signature, iss, aud == RESOURCE, exp; return None on failure

mcp = MCPServer("gluons", token_verifier=GluonsVerifier(), auth=AuthSettings(
    issuer_url=AnyHttpUrl("https://auth.gluons.example/oauth"),
    resource_server_url=AnyHttpUrl(RESOURCE),
    required_scopes=["gluons.read"], validate_token_resource=True))

@mcp.tool(title="List threads", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def list_threads() -> list[str]:
    caller = get_access_token()  # caller.subject maps to the tenant user
    return []

security = TransportSecuritySettings(allowed_hosts=["mcp.gluons.example", "mcp.gluons.example:*"])
mcp_app = mcp.streamable_http_app(transport_security=security, stateless_http=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan)
app.mount("/", mcp_app)  # register Gluons routes first; "/" matches everything
```

Trailing-slash risk (derived, test it): the docs show `issuer_url=AnyHttpUrl("https://auth.example.com")` rendering as `"https://auth.example.com/"` in PRM. Clients compare issuers exactly, and ChatGPT requires PRM `authorization_servers` and AS `issuer` to match. Use a path issuer as above, or make both strings identical.

## 3. Claude custom connectors
Sources, under https://claude.com/docs/connectors : /building | /building/authentication | /building/lazy-authentication | /building/troubleshooting | /building/review-criteria | /building/submission | /custom/add-unlisted
Also: https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp | https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities | https://code.claude.com/docs/en/mcp

- Plans: Free, Pro, Max, Ultra, Team, Enterprise. Free is limited to one custom connector. Team: Owners and Primary Owners add. Enterprise: also custom roles with library management. Each member then connects individually.
- Callback for claude.ai, Desktop, mobile, Cowork: exactly `https://claude.ai/api/mcp/auth_callback`. Claude Code: loopback on an ephemeral port; accept `http://localhost/callback` and `http://127.0.0.1/callback` ignoring port. Its CIMD is `https://claude.ai/oauth/claude-code-client-metadata`.
- Registration: `oauth_dcr` and `oauth_cimd` by default. Users can also pick "Use your own OAuth client"; a directory listing can use `oauth_anthropic_creds`. No machine-to-machine `client_credentials`.
- CIMD selected only when AS metadata has BOTH `client_id_metadata_document_supported: true` and `"none"` in `token_endpoint_auth_methods_supported`; otherwise falls back to DCR. The UI calls this "Use Claude's published identity" and recommends it. DCR registers a new client on every fresh connection.
- PKCE `S256` always. `resource` sent in canonical form on both requests. `offline_access` appended when AS metadata lists it.
- Sign-in requires a real HTTP `401` with `WWW-Authenticate`. A `200` with `isError: true` shows no Connect prompt. `403` triggers re-auth only with `error="insufficient_scope"`. Only the first `authorization_servers` entry is used. PRM `resource` must equal the URL as entered, including path.
- Timeouts: 10s for discovery, registration and token; 30s for refresh. Refresh on 401 and proactively up to 5 minutes before expiry. Return `invalid_grant` for dead refresh tokens. `/token` must accept `application/x-www-form-urlencoded`. DCR and CIMD connections are public clients, so rotate refresh tokens.
- Network: traffic originates from Anthropic, including for mobile, from `160.79.104.0/21`. Hostnames need a public `A` record (connectors are IPv4-only). A cross-host redirect drops `Authorization`. Discovery metadata is cached about 5 minutes.
- Limits on claude.ai and Desktop: tool result about 150,000 characters; 240 seconds per tool call. No fixed tool count; the support article suggests "On demand" tool access at 10+ connectors. Directory tool names 64 characters or fewer.
- Mobile: web connectors work on iOS and Android once set up. "Installing connectors on mobile is currently in beta"; Desktop or web is recommended for custom connectors.
- Annotations: directory requires `title` plus `readOnlyHint` or `destructiveHint`. Read-only tools can run without per-call confirmation; destructive tools always prompt. Research mode can call tools without approval, so write tools need care. A tool mixing read and write methods is rejected.
- Not yet supported: resource subscriptions, sampling, "advanced or draft capabilities". Spec versions listed are authorization 2025-03-26, 2025-06-18, 2025-11-25.

UNVERIFIED: whether hosted Claude apps speak 2026-07-28. Docs list authorization revisions only through 2025-11-25; Claude Code "v2 runtime" negotiates 2026-07-28 with servers that support it.
UNVERIFIED: hosted-app URL-mode elicitation. Claude Code declares `{form:{}, url:{}}` on 2026-07-28 connections and bare `{}` on earlier-revision connections such as claude.ai connectors. Community issue anthropics/claude-ai-mcp#153 reports no URL mode. Design a connect-link fallback.
UNVERIFIED: `https://claude.com/api/mcp/auth_callback`. An older support article said to allowlist it too; that page now returns 404 and current docs name only claude.ai.
UNVERIFIED: the URL of the CIMD document Anthropic hosts for the hosted apps. Validate by CIMD rules rather than hard-coding it.

## 4. ChatGPT
Sources: https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt | https://help.openai.com/en/articles/20001256-plugins-in-chatgpt | https://help.openai.com/en/articles/11487775-connected-apps-in-chatgpt
Under https://developers.openai.com : /plugins/build/auth.md | /plugins/build/mcp-server.md | /plugins/deploy/connect-chatgpt.md | /plugins/deploy/submission.md | /plugins/plugin-guidelines.md | /siwc/website | /siwc/quickstart

- Availability wording: "Custom MCP server connections and full MCP support are available for ChatGPT Business and Enterprise/Edu customers on ChatGPT web."
- Write tools: allowed with full MCP, including "write/modify actions"; ChatGPT may ask for confirmation and some risky actions may be blocked. Pro: "Pro users can connect custom MCP servers with read/fetch permissions." Full MCP is "only available to Business and Enterprise/Edu users, currently."
- Business: only admins and owners create and publish. Enterprise/Edu: RBAC can grant creation; only admins and owners publish. Permission name: `Create plugins with MCPs`.
- Mobile: "Can I connect custom MCP servers on mobile? No - web only." The plugin directory is on web, desktop and mobile, and guidelines say reviewed plugins must work on desktop and mobile.
- Adding: Plugins, plus button, Add custom MCP server, HTTPS URL including `/mcp` or Secure MCP Tunnel, authentication, acknowledge risk, Create as a plugin. Developer Mode is not needed.
- Frozen tools: after approval ChatGPT uses a "frozen" snapshot of tools. Changes need an admin refresh; incompatible changes make calls error with no notice to admins; new actions start disabled. Deep research is read/fetch only.
- Redirect: stable `https://chatgpt.com/connector_platform_oauth_redirect` when the AS meets RFC 9207 requirements; otherwise `https://chatgpt.com/connector/oauth/{callback_id}`. Copy the exact value from the server's management page.
- RFC 9207 requirements: `authorization_response_iss_parameter_supported: true`; the same exact issuer in AS `issuer` and PRM `authorization_servers`; `iss` on every success and error response. Advertising it and omitting `iss` makes ChatGPT reject the response.
- Registration: CIMD preferred. `client_id` is `https://chatgpt.com/oauth/client.json` (compliant AS) or `https://chatgpt.com/oauth/{callback_id}/client.json`. Token auth methods `none` or `private_key_jwt` (JWKS at `/oauth/jwks.json`). DCR once per connection; predefined clients also supported. The builder chooses between CIMD and DCR when both exist.
- Flow: authorization code with PKCE `S256`, `resource` on both requests, `code_challenge_methods_supported` required. Publish `offline_access` in AS `scopes_supported`, or "ChatGPT may lose access after the original authorization expires".
- Per-tool `securitySchemes` (`noauth`, `oauth2` with `scopes`). The tool-level linking UI needs both `securitySchemes` and a result with `isError: true` plus `_meta["mcp/www_authenticate"]` holding a Bearer challenge with `error` and `error_description`.
- Annotations: set `readOnlyHint`, `destructiveHint`, `openWorldHint` explicitly. Send and delete actions are `readOnlyHint: false`, `destructiveHint: true`. Use MCP elicitation for structured input, never secrets. Publishing needs individual or business verification, and API-key connections are not accepted.
- Sign in with ChatGPT: "currently available to selected commercial partners through a limited trial. To join the waitlist, contact your OpenAI representative." Issuer `https://auth.openai.com`; client IDs usually `oaiapp_`. Plan usage is open to open-source partners and selected private clients. Do not plan on it as a login option.

UNVERIFIED: Plus, Go and Free for custom MCP. The article does not mention them; third-party guides conflict.
UNVERIFIED: whether a workspace-published custom connection can be used (not added) from mobile. Current FAQ wording is about connecting; a search snippet of an older version read "Are MCP apps available on mobile? No - web only."
UNVERIFIED: whether a publicly listed plugin's write tools reach Plus users. Docs say use "depends on your plan, workspace, role, region".
UNVERIFIED: the MCP protocol revision ChatGPT speaks, and whether it supports URL-mode elicitation or 2026-07-28 transport. Docs cite 2025-11-25 for authorization and 2026-07-28 for `iss` validation only.

## 5. Gluons AS checklist (derived from sections 1, 3, 4)
Satisfies both hosts' CIMD path and the ChatGPT stable redirect:

```json
{"issuer": "https://auth.gluons.example/oauth",
 "authorization_endpoint": "https://auth.gluons.example/oauth/authorize", "token_endpoint": "https://auth.gluons.example/oauth/token",
 "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
 "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"],
 "client_id_metadata_document_supported": true, "authorization_response_iss_parameter_supported": true,
 "scopes_supported": ["gluons.read", "gluons.write", "offline_access"]}
```

- Serve it at all three path-issuer locations in section 1.1 and confirm with both hosts; Claude's docs describe only the root well-known forms. Emit `iss` on every authorization response. Rotate refresh tokens. Bind `aud` to the `resource` value.
- On the consent screen name the `client_id` URL host, not the self-asserted `client_name`, and show the redirect hostname.
- Pre-allow for non-CIMD paths: `https://claude.ai/api/mcp/auth_callback`, loopback as above, `https://chatgpt.com/connector_platform_oauth_redirect`.

## 6. Google OAuth 2.0 for web server apps
Sources, under https://developers.google.com : /identity/protocols/oauth2/web-server | /identity/protocols/oauth2 | /identity/protocols/oauth2/native-app | /identity/openid-connect/openid-connect | /identity/protocols/oauth2/production-readiness/sensitive-scope-verification | /identity/protocols/oauth2/production-readiness/restricted-scope-verification | /workspace/workspace-api-user-data-developer-policy
Also: https://accounts.google.com/.well-known/openid-configuration | https://support.google.com/cloud/answer/7454865 | https://support.google.com/cloud/answer/15549945 | https://support.google.com/cloud/answer/13463816

Endpoints, from the discovery document:
- Authorization `https://accounts.google.com/o/oauth2/v2/auth`; token `https://oauth2.googleapis.com/token`; revocation `https://oauth2.googleapis.com/revoke`.
- Userinfo `https://openidconnect.googleapis.com/v1/userinfo`; JWKS `https://www.googleapis.com/oauth2/v3/certs`; signing `RS256`; `code_challenge_methods_supported: ["plain", "S256"]`.
- Hard-code only the discovery URL `https://accounts.google.com/.well-known/openid-configuration`.
- Authorization parameters: `client_id`, `redirect_uri` (exact match), `response_type=code`, `scope` (space-delimited), `state` (recommended, CSRF), `access_type` (`online` default, `offline` for a refresh token), `include_granted_scopes=true`, `login_hint`, `prompt` (`none`, `consent`, `select_account`; space-delimited; `none` stands alone).

- Refresh tokens are returned on first authorization only; get another with `prompt=consent` or by revoking.
- Token response: `access_token`, `expires_in`, `refresh_token`, `scope`, `token_type`, optional `refresh_token_expires_in` (time-based access only). Users can grant a subset of scopes, so always read `scope`.
- Refresh: POST token endpoint with `client_id`, `client_secret`, `grant_type=refresh_token`, `refresh_token`.
- Revoke: POST `token` as `application/x-www-form-urlencoded`; `200` success, `400` error. Works on access or refresh tokens and removes all scopes for the project, across its clients.
- ID token: verify signature via JWKS; `iss` is `https://accounts.google.com` or `accounts.google.com`; `aud` equals client ID; check `exp`; check `hd` if restricting domains. Key users on `sub`, never `email`.
- Max sizes: auth code 256 bytes, access token 2048, refresh token 512. An optional `DPoP` header on token requests is documented and recommended.
- Refresh token invalidation: user revoked; unused 6 months; password change when the token has Gmail scopes; over 100 live tokens per account per client (oldest silently invalidated); time-based access expired; `admin_policy_enforced`; session control (`invalid_grant`, `error_subtype: invalid_rapt`, sessions 1 to 24 hours).
- Testing status: with External user type, refresh tokens expire in 7 days unless only name, email, profile scopes are requested. Up to 100 test users. Internal user type is not capped but works only inside one Workspace org.

Verification:
- Sensitive: brand verification, Search Console domain ownership, privacy policy on the same domain, unlisted YouTube demo showing consent screen, client ID and each scope. Takes up to 10 days.
- Restricted: all of that plus permitted-use justification. "Several weeks." Brand verification takes 2 to 3 business days and must be published within 7 days.
- CASA security assessment is required when restricted data is stored on or transmitted through servers, so it applies to Gluons. Renew at least every 12 months. New restricted scopes may need reassessment.
- Unverified apps: "100 new users in total". Exceptions: personal use, Testing with listed users, internal.
- Permitted Gmail uses include productivity tools such as CRM, mail merge and generative AI summaries. Prohibited: ads, selling data, and training models beyond the user's personalized model.

UNVERIFIED: PKCE for confidential web clients. The web-server page has zero mentions of `code_challenge`; it appears only in the native-app page and the discovery document. Test before relying on it; `state` stays mandatory regardless.
UNVERIFIED: CASA tier and cost for Gluons. Fees in search results came from a Google Health API page, not Gmail.

## 7. Gmail API
Sources, under https://developers.google.com/workspace/gmail/api : /auth/scopes | /guides/push | /guides/sync | /guides/threads | /guides/sending | /guides/uploads | /guides/handle-errors | /reference/quota
Under .../gmail/api/reference/rest/v1 : /users/watch | /users/stop | /users.history/list | /users.messages/get | /users.messages/send | /users.messages.attachments | /Format

Scopes (prefix `https://www.googleapis.com/auth/`):

| Scope | Class | Notes |
|---|---|---|
| `gmail.send` | Sensitive | Send only |
| `gmail.labels` | Non-sensitive | Labels only |
| `gmail.readonly` | Restricted | Read messages and settings |
| `gmail.metadata` | Restricted | No bodies; `format=full` and `raw` unavailable |
| `gmail.compose` | Restricted | Drafts and send |
| `gmail.modify` | Restricted | Read, compose, send; no permanent delete |
| `https://mail.google.com/` | Restricted | Full access; only if immediate permanent delete is needed |

All paths below are under `https://gmail.googleapis.com/gmail/v1`.
- Scope note: `watch` and `history.list` accept only `mail.google.com`, `gmail.modify`, `gmail.readonly`, `gmail.metadata`. Push ingestion is therefore always a restricted-scope feature.
- `POST /users/{userId}/watch` body: `topicName` (`projects/{project}/topics/{topic}`), `labelIds[]`, `labelFilterBehavior`. `labelFilterAction` is deprecated and ignored when the new field is set.
- Enum case conflict: the reference lists `include`/`exclude`, the guide sample uses `INCLUDE`. Test which is accepted.
- Watch response: `{"historyId": "...", "expiration": "<epoch ms string>"}`. A successful watch immediately sends one notification.
- The topic's project must match the requesting project. Grant `publish` on it to `gmail-api-push@system.gserviceaccount.com`; domain-restricted sharing may block this.
- Renew at least every 7 days; Google recommends daily. Multi-tenant: schedule daily per mailbox.
- `POST /users/{userId}/stop`: empty body, empty JSON response. Notifications stop within minutes.
- Notification limits: at most 1 event per second per user, excess dropped. Delivery can be delayed or dropped, so run periodic `history.list` as a fallback.
- `GET /users/{userId}/history` params: `startHistoryId` (required), `maxResults` (default 100, max 500), `pageToken`, `labelId`, `historyTypes[]` (`messageAdded`, `messageDeleted`, `labelAdded`, `labelRemoved`).
- History response: `history[]`, `nextPageToken`, `historyId`. Messages typically carry only `id` and `threadId`.
- `404` on a stale or invalid ID: do a full sync (`messages.list` plus batched `messages.get`). IDs are "typically valid for at least a week" but can be "only a few hours".
- `GET /users/{userId}/messages/{id}`: `format` is `minimal`, `full`, `raw`, `metadata`; `metadataHeaders[]` with `metadata`.
- `GET /users/{userId}/messages/{messageId}/attachments/{id}`: needs `mail.google.com`, `gmail.modify` or `gmail.readonly`. Returns `{attachmentId, size, data}`; `data` is base64url. A part with `body.attachmentId` needs this call; otherwise content is in `body.data`.
- `POST /users/{userId}/messages/send` with `{"raw": "<base64url RFC 2822>", "threadId": "..."}`. Upload URI `https://gmail.googleapis.com/upload/gmail/v1/users/{userId}/messages/send`.
- Threading needs all three: `threadId`, RFC 2822 `References` and `In-Reply-To`, matching `Subject`.
- Quota: 1,200,000 units per minute per project; 6,000 units per minute per user per project.
- Costs: `messages.get` 20 (60 with full, raw, html), `messages.send` 100, `messages.list` 5, `history.list` 2, `watch` 100, `stop` 50, `attachments.get` 20, `threads.get` 40 (120 with full), `drafts.send` 100.
- Billing: 80,000,000 units per day per project is a non-increasable billing threshold. Exceeding quota limits "is planned to incur charges" later in 2026, with at least 90 days' notice.
- Errors: `401` refresh; `403` `dailyLimitExceeded`, `userRateLimitExceeded`, `rateLimitExceeded`, `domainPolicy`; `429` "Too many concurrent requests for user"; `500`, `502`, `503`, `504` back off (start 1s, 2^n plus jitter, cap 32 to 64s). Keep batches at 50 or fewer.

UNVERIFIED: maximum `messages.send` size. The reference and upload guide did not state a number.
UNVERIFIED: notification `historyId` JSON type. The guide sample shows a string; accept string or number.

## 8. Cloud Pub/Sub push
Sources, under https://docs.cloud.google.com : /pubsub/docs/push | /pubsub/docs/authenticate-push-subscriptions | /pubsub/docs/subscription-properties | /run/docs/triggering/pubsub-push

```json
{"message": {"data": "<base64>", "messageId": "...", "message_id": "...", "publishTime": "...", "publish_time": "...", "attributes": {}},
 "subscription": "projects/p/subscriptions/s", "deliveryAttempt": 1}
```

- Gmail `data` decodes to `{"emailAddress": "...", "historyId": ...}`. Gmail says base64url, Pub/Sub says base64; decode tolerantly.
- Ack: `102`, `200`, `201`, `202`, `204`. Anything else or a deadline expiry redelivers. Ack deadline default 10s, range 10 to 600s. Push backoff 100ms to 60s, not tunable. Delivery is at-least-once in effect, so dedupe on `messageId`.
- Retention default 7 days. Dead-letter attempts 5 to 100. Subscription expiry default 31 days; set never expire.
- JWT: `Authorization: Bearer <JWT>` from the push auth service account. Claims `aud`, `azp`, `email`, `email_verified`, `exp`, `iat`, `iss`, `sub`. Tokens may be up to an hour old.
- Validate: signature, `aud` equals the configured audience, `iss` is `https://accounts.google.com` or `accounts.google.com`, `email` equals the expected service account, `email_verified` is true.
- IAM: Pub/Sub service agent `service-{PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com` needs `roles/iam.serviceAccountTokenCreator` on the push SA (projects created on or before 2021-04-08 must grant it; newer ones hold `roles/pubsub.serviceAgent`). The subscription creator needs `iam.serviceAccounts.actAs`. For Cloud Run the push SA needs `roles/run.invoker`.

```python
from google.auth.transport import requests
from google.oauth2 import id_token

token = request.headers["Authorization"].split(" ")[1]
claim = id_token.verify_oauth2_token(token, requests.Request(), audience=PUSH_AUDIENCE)  # checks signature, aud, exp
if claim["email"] != PUSH_SA or not claim["email_verified"]:
    raise PermissionError
```

## 9. Cloud Tasks and Cloud Run
Sources, under https://docs.cloud.google.com : /tasks/docs/creating-http-target-tasks | /tasks/docs/reference/rest/v2/projects.locations.queues.tasks | /tasks/docs/reference/rpc/google.cloud.tasks.v2 | /tasks/docs/quotas | /run/docs/configuring/request-timeout | /run/docs/authenticating/service-to-service | /run/docs/securing/managing-access

Cloud Tasks:
- Set `httpRequest.oidcToken` with `serviceAccountEmail` and `audience`. If omitted, audience defaults to the target URI. Queue-level `httpTarget` auth overrides task-level.
- `dispatchDeadline` 15s to 30 minutes; HTTP default 10 minutes. Set it a few seconds above the handler timeout.
- IAM: enqueuing caller needs `cloudtasks.enqueuer` role and `iam.serviceAccounts.actAs` on the SA; the SA must be in the queue's project and needs `roles/run.invoker`; service agent `service-PROJECT_NUMBER@gcp-sa-cloudtasks.iam.gserviceaccount.com` needs Service Account User on it.
- Headers `X-CloudTasks-QueueName`, `-TaskName`, `-TaskRetryCount`, `-TaskExecutionCount`, `-TaskETA` are informational, never identity.
- Limits: task 1 MiB; retention 31 days; schedule up to 30 days ahead; 500 dispatches per second per queue.

Cloud Run:
- Request timeout default 300s, maximum 3600s (`--timeout`). On timeout the client gets `504`, but the instance is not terminated and may keep processing. Use idempotent handlers.
- Token `aud` must be the service URL or a configured custom audience. Custom domains are not supported as `aud`.
- `Authorization` or `X-Serverless-Authorization` can carry the ID token. If both are sent, only the latter is checked, and Cloud Run strips its signature before the container sees it.
- Public service: `--no-invoker-iam-check` is the recommended method (annotation `run.googleapis.com/invoker-iam-disabled: 'true'`, Terraform `invoker_iam_disabled = true`). `--allow-unauthenticated` grants `allUsers`, which domain-restricted sharing can block.

Layout implication (derived): ChatGPT and Claude send OAuth bearer tokens in `Authorization`, which are not Google ID tokens, so the MCP service must have the invoker check disabled. Preferred: two services, a public one for MCP and OAuth with the check off, and a private worker with IAM on for Pub/Sub and Tasks. Alternative: one public service that verifies Pub/Sub and Tasks JWTs in the app per section 8, with a chosen audience.

Timeout budget: Claude 240s per tool call, Pub/Sub ack 600s max, Tasks 30 minutes, Cloud Run 60 minutes. Ack Pub/Sub fast by enqueuing a task, and use explicit job handles for long tools.

UNVERIFIED: whether Cloud Run also strips the signature from `Authorization` when its IAM check runs. Docs state it only for `X-Serverless-Authorization`; do not plan in-app JWT verification behind the IAM check without testing.
