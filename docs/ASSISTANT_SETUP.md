# Connect an assistant to Gluons

The local server exposes authenticated Streamable HTTP at
`http://localhost:8000/mcp`. Remote hosted assistants cannot generally reach your
computer's loopback interface. Use an explicitly authorized HTTPS deployment or
a supported secure tunnel; configuring one is a separate operation.

Set `GLUONS_PUBLIC_API_URL` to the actual API origin and
`GLUONS_PUBLIC_WEB_URL` to the owner web origin before starting the hosted API.
Both must be HTTPS in staging/production. Keep development and production OAuth
clients separate. Discovery is at `/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource/mcp` on the API origin.

For Claude, open **Customize → Connectors**, add a custom connector and enter the
HTTPS `/mcp` URL. Complete Gluons sign-in, choose the business and explicitly
approve the requested scopes. Workspace administrators may restrict custom
connectors. Follow [Claude's current setup instructions](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities).

For supported ChatGPT configurations, use ChatGPT **on the web**, enable the
appropriate developer/custom-MCP controls permitted by your workspace, add the
HTTPS `/mcp` URL and choose OAuth authentication. Complete the Gluons consent
screen. Tool access and write confirmation behavior depend on the account and
workspace. Follow [OpenAI's current setup instructions](https://developers.openai.com/api/docs/guides/custom-mcp-server).
Use the responsive Gluons owner portal from mobile devices when a host cannot
install or invoke custom tools.

For a compatible open-source MCP client, use OAuth discovery, register a public
client at `/oauth/register` with exact HTTPS or loopback redirect URIs, and send
`response_type=code`, a random client `state`, `code_challenge_method=S256`, the
SHA-256 challenge and `resource=<API_ORIGIN>/mcp` to `/oauth/authorize`. After the
owner approves in Gluons Web, validate callback state and issuer. Exchange the
code at `/oauth/token` using the original verifier, redirect URI and resource.
Send the access token only in `Authorization: Bearer ...`, on every MCP request.
Rotate refresh credentials and replace the stored refresh token atomically.
Do not paste tokens in chat or put them in URLs.

Supported scopes are `gluons.read`, `gluons.plan`, `gluons.tasks`,
`gluons.connections`, `gluons.approvals`, `gluons.activate` and `gluons.models`. Consent may narrow
them. The default scope set is read, plan and tasks. A scope can never exceed
the current member's role. Connecting Gmail or an assistant does not authorize
customer communications or spending: owner-signed automation policies are
separate.

Use **Gluons → Assistants** to revoke a binding. This invalidates that assistant's
access and refresh tokens immediately, while other bindings remain independent.
Existing authorized background automations continue until paused or cancelled;
revoking the conversational client does not silently change business policy.

The implementation has local PostgreSQL and official SDK protocol tests. It has
not been installed into a live Claude/ChatGPT account or verified through a real
provider. No claim of host certification or production readiness is made.
