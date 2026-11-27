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

To test Gluons in a supported ChatGPT workspace, open **Plugins**, select **+ → Add
custom MCP server**, and enter the HTTPS `/mcp` URL with OAuth authentication.
Complete the Gluons consent screen, then select the plugin in a ChatGPT conversation.
Workspace permissions determine who can add it and what actions need confirmation.
See [ChatGPT's custom MCP setup guide](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

Start the workflow in the assistant conversation after connecting. For example,
ask Gluons to help start an online business and save the answers as a draft. The
assistant can ask the setup questions, save answers and continue the draft in chat.
Gluons opens targeted owner pages for authorization and device verification when
the flow requires them. The web workspace is available as an optional fallback.

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

The implementation has local PostgreSQL and official SDK protocol tests. On
November 27, 2026, the owner connected the hosted staging server to ChatGPT and
confirmed that it created a business setup draft, saved an answer and presented
the next question in chat. In a new ChatGPT conversation, Gluons retrieved the
saved draft and resumed at that question. See the [deployment status](DEPLOYMENT_STATUS.md)
for remaining hosted and provider checks.

An owner can start a guided workflow from chat. The first two entry points are
starting an online business and handing off an existing process. See the
[chat-first setup guide](execution/EX05_CHAT_FIRST_SETUP.md) for the question,
provider handoff, draft, and shadow trial flow.
