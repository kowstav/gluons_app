# EX-05 implementation decision

Status: implementation in progress, November 9. EX-00 base: `cf5a3832af75dad81b6a53e8e4400f61d7d96f3a`. This document is a contract decision, not a claim that onboarding or a provider redirect has been verified.

## Source of truth

`Organization.business_facts` is an untyped legacy field that existing organization APIs can replace. It cannot serve as confirmed profile evidence. Store the typed Passport in a separate organization-owned JSONB column with a dedicated version, protected by the existing forced organization RLS and a row lock for optimistic concurrency. This is the single authority for Passport fields; do not automatically copy untyped facts or provider JSON into it. Organization name, country and timezone remain authoritative in the existing organization record; any presentation of these as Passport suggestions must clearly mark them unconfirmed until an explicit confirmation establishes provenance. No migration should automatically classify old data as owner-confirmed.

The v1 field allowlist is limited to brand name, currency, business type, selling regions, support email, support hours and approved operational address. Each present value records source kind, confirmation time and confirming user. New provider-sourced facts require an allowlisted preview and explicit confirmation; no provider import or sharing is assumed merely because a connection exists. Store no credentials, financial account data, legal IDs or identity documents in these fields. Audits record field names and versions, not values.

## Setup boundary

A durable setup session must reference existing launch and connection sessions, not duplicate provider credentials, authorization state or business-action approval. Authenticated `Principal` and current membership select the organization; setup links do not grant access. A link secret must be hashed, short-lived and single-use; resuming the durable session after expiry requires a new authenticated link. Include expected-version and idempotency checks; never accept a caller-supplied tenant. Use only fixed return destinations and expose a connection as ready only after identity, scopes and health are checked. Missing provider functionality stays explicitly blocked.

## Migration and data rights

Additive migration `0011` adds the Passport column and version to `organizations` rather than adding tenant tables. Migration `0012` adds bounded setup records to the same row, so the existing organization lock and forced RLS cover session creation, updates and token redemption. Existing installations start empty and old facts remain untouched. Structured exports include typed Passport values and provenance and a sanitized setup summary, without token or idempotency hashes. The existing organization purge clears both fields after its normal grace period. Downgrading these columns discards data, so rollback should normally leave the additive schema in place and disable the feature. No provider access or writes are authorized by this decision.

## Integration status

Initial slices implement typed owner-confirmed fields, version-checked edits and owner-only REST access. Setup records support idempotent creation, linking an existing owned provider session, and expiring single-use resume tokens. They do not create provider access; a connection's current status is reported from its existing session. Exports and purge are covered. Provider imports, sharing consent, setup UI, broader assistant handoff, real redirect verification and the full EX-05 acceptance suite remain outstanding; these slices do not make onboarding complete.
