# EX-05 implementation decision

Status: implementation in progress, November 9. EX-00 base: `cf5a3832af75dad81b6a53e8e4400f61d7d96f3a`. This document is a contract decision, not a claim that onboarding or a provider redirect has been verified.

## Source of truth

`Organization.business_facts` is an untyped legacy field that existing organization APIs can replace. It cannot serve as confirmed profile evidence. Store the typed Passport in a separate organization-owned JSONB column with a dedicated version, protected by the existing forced organization RLS and a row lock for optimistic concurrency. This is the single authority for Passport fields; do not automatically copy untyped facts or provider JSON into it. Organization name, country and timezone remain authoritative in the existing organization record; any presentation of these as Passport suggestions must clearly mark them unconfirmed until an explicit confirmation establishes provenance. No migration should automatically classify old data as owner-confirmed.

The v1 field allowlist is limited to brand name, currency, business type, selling regions, support email, support hours and approved operational address. Each present value records source kind, confirmation time and confirming user. New provider-sourced facts require an allowlisted preview and explicit confirmation; no provider import or sharing is assumed merely because a connection exists. Store no credentials, financial account data, legal IDs or identity documents in these fields. Audits record field names and versions, not values.

## Setup boundary

A durable setup session must reference existing launch and connection sessions, not duplicate provider credentials, authorization state or business-action approval. Authenticated `Principal` and current membership select the organization; setup links do not grant access. A link secret must be hashed, short-lived and single-use; resuming the durable session after expiry requires a new authenticated link. Include expected-version and idempotency checks; never accept a caller-supplied tenant. Use only fixed return destinations and expose a connection as ready only after identity, scopes and health are checked. Missing provider functionality stays explicitly blocked.

## Migration and data rights

Additive migration `0011` adds the Passport column and version to `organizations` rather than adding tenant tables. Existing installations start empty and old facts remain untouched. Structured exports include only the typed Passport field and provenance. The existing organization purge must clear the new column. Any future setup records must define independent export, purge and retention treatment before implementation. Downgrading the column discards Passport data, so rollback should normally leave the additive schema in place and disable the feature. No provider access or writes are authorized by this decision.

## Integration status

An initial slice implements typed owner-confirmed fields, version-checked edits, owner-only REST access, explicit export and deletion handling. It has no provider imports, sharing consent, setup session or browser flow. Real redirect verification and the full EX-05 acceptance suite remain outstanding; this slice does not make onboarding complete.
