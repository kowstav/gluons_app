# Owner structured data export

`GET /v1/data-export` lists the fixed datasets and exported columns.
`POST /v1/data-export/{dataset}` accepts `{ "limit": 200, "cursor": null }` and
returns a downloadable JSON page. Repeat with `next_cursor` until it is null.
Pages require a currently authenticated Gluons Web owner, the `data:export`
permission, selected organization and normal CSRF validation. Assistants, field
representatives, administrators, support and other non-owner roles cannot export.

Every query uses the restricted application role and tenant RLS. Callers cannot
supply table names, columns, filters, tenant IDs or SQL. Cursors are signed,
owner/business/dataset-bound and expire in one hour. They retain a fixed creation
timestamp cutoff with deterministic timestamp/ID pagination. This is not a
cross-request transactional snapshot; a record can be updated while pages are
downloaded. A maximum of 1000 records and 4 MiB of record content fit in one page.
An individually oversized record fails explicitly instead of being silently
omitted. Product rows have no creation timestamp and use a fixed epoch/ID order;
this catalog page sequence cannot establish an historical creation-time snapshot.

Exports include structured business/operational records: organizations and member
roles, connection/assistant status, automations/runs, approval metadata, action
authorizations and receipts, trials and measurements, usage, artifact metadata,
knowledge metadata, products/customers/orders/messages/calls, launch plans and
supplier quotes. Verification, environment, test and measurement/cost-basis labels
are preserved; exporting never certifies a provider transaction.

The implementation selects only explicitly named database columns. It omits all
vault credentials/ciphertexts, provider token references, OAuth authorization
state, browser sessions/cookies, password/MFA secrets, signing signatures,
provider request/response JSON, connection metadata, automation configuration,
runtime inputs/outputs and internal artifact storage locators. Raw document,
email and transcript bodies and order-line JSON are excluded from this safe
structured export. Retrieval of raw contents must use separate authenticated,
retention-aware business workflows; this endpoint is not a byte-for-byte account
backup. Owner-supplied business names/addresses remain owner data and are private.

Every successful page appends an audit event containing only the dataset name,
count and pagination flag. The page includes the existing immutable audit-chain
anchor at its creation cutoff. This anchor is a reference, not a recomputed
integrity claim. Full audit verification/export remains on the existing audit
endpoints.

No records, connected provider accounts or organizations are deleted or revoked.
Account erasure and offboarding require separately established retention rules,
provider revocation behavior and legal authorization; export does not silently
guess those policies.

Verification: **19 tests passed** using the isolated `export-tests` embedded
PostgreSQL cluster and real tenant RLS. Tests cover every dataset, all non-owner
roles, assistant/sessionless denial, same-owner multiple-business isolation,
secret sentinels in vault/connection metadata/configuration, stable timestamp/ID
pagination, cursor tampering/expiry/dataset/owner/tenant binding, bounds and HTTP
CSRF/download/cache headers. Ruff passed for the service, routes and tests.
