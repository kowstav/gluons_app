# Assistant OAuth and MCP checks — 2026-11-02

Implementation was checked against the installed official Python `mcp==2.3.0`
package and current official documentation. These checks establish protocol
behavior, not a successful connection from an external Claude/ChatGPT account.

- [MCP authorization](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/authorization.mdx): PKCE, audience/resource validation, bearer authorization on every request and separate upstream credentials.
- [MCP security requirements](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/security-considerations.mdx): never pass assistant credentials through to providers.
- [Official Python SDK ASGI integration](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md): a mounted app needs the host lifespan to enter the session manager.
- [Official SDK legacy-client support](https://py.sdk.modelcontextprotocol.io/run/legacy-clients/): one Streamable HTTP endpoint supports 2025 handshake clients and 2026 request-envelope clients. Tests exercise both. The modern request has protocol version and client capabilities in `params._meta` as required by the installed SDK.
- [OpenAI authentication](https://developers.openai.com/plugins/build/auth): OAuth discovery and authorization-code flow. Gluons advertises issuer identification, returns `iss`, supports DCR and conditionally advertises CIMD. No generic client-credentials or arbitrary API-key login is advertised.
- [ChatGPT custom MCP setup](https://developers.openai.com/api/docs/guides/custom-mcp-server): setup through ChatGPT web and workspace-dependent access. Gluons Web remains the mobile fallback; no claim that ChatGPT mobile supports installation.
- [Claude connectors](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities): custom remote MCP setup and availability vary with product surface and workspace policy. Use the current in-product controls.

Security choices: exact redirect matching, public clients with PKCE S256 only,
10-minute consent requests, 2-minute single-use codes, hashed opaque access and
refresh tokens, resource binding, refresh-family revocation on reuse, live
membership checks and independent binding revocation. Assistant high-risk
approval tiers are capped at R2; the default is R1. Policies, billing authority,
team administration and data export cannot be delegated through OAuth scopes.

CIMD is disabled unless `GLUONS_OAUTH_CIMD_HOSTS` lists operator-reviewed hostnames.
Metadata URLs must use HTTPS, return a matching `client_id`, resolve only to
public addresses and stay below 64 KiB. The connection pins the resolved address
while retaining TLS SNI/hostname validation. Redirect following is disabled.
No metadata fetch was made against a real host in this session.

Deployment still requires TLS, a publicly reachable endpoint or supported tunnel,
owner consent UI, production rate limiting, review of any CIMD origin, and an
actual authorized host installation test. All work remains local; no cloud or
account registration was performed.
