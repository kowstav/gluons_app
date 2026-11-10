"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Field = { value?: string | string[]; source: string; status?: string; confirmed_at: string };
type Passport = { version: number; organization: { name: string; country: string | null; timezone: string; version: number }; fields: Record<string, Field> };
type Connection = { id: string; provider: string; account_display: string; status: string; account_confirmed: boolean; scopes: string[] };
type Action = { code: string; label: string; owner_url?: string };
type Setup = { id: string; version: number; status: string; created_at: string; expires_at: string; connection: null | { id: string; kind?: string; provider: string; account_display?: string; connection_id?: string | null; status: string; owner_url: string; requested_scopes?: string[] }; next_actions: Action[] };
type Preview = { fields: Record<string, string | string[]>; preview_token: string; provider?: string; notice: string };
type Consent = { id: string; provider: string; fields: string[]; expires_at: string; active: boolean; revoked_at?: string };

const labels: Record<string, string> = { business_name: "Business name", country: "Country", timezone: "Time zone", brand_name: "Brand name", currency: "Currency", business_type: "Business type", selling_regions: "Selling regions", support_email: "Support email", support_hours: "Support hours", operational_address: "Operating address" };
const display = (value: unknown) => Array.isArray(value) ? value.join(", ") : String(value ?? "—");
const when = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

export default function SetupRoom({ organizationId, organizationName, isOwner, onOrganizationUpdated }: { organizationId: string; organizationName: string; isOwner: boolean; onOrganizationUpdated: () => Promise<void> }) {
  const [profile, setProfile] = useState<Passport | null>(null);
  const [sessions, setSessions] = useState<Setup[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [consents, setConsents] = useState<Consent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [includeSend, setIncludeSend] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState("");
  const [selectedShop, setSelectedShop] = useState("");
  const [shareProvider, setShareProvider] = useState("shopify");
  const [shareFields, setShareFields] = useState<string[]>([]);
  const [sharePreview, setSharePreview] = useState<Preview | null>(null);
  const [importPreview, setImportPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [startPending, setStartPending] = useState(false);
  const [retryStorageAvailable, setRetryStorageAvailable] = useState(true);
  const pending = useRef<Record<string, string>>({});
  const setupApi = useCallback(<T = Record<string, unknown>,>(path: string, options?: Parameters<typeof api>[1]) =>
    api<T>(path, { ...options, expectedOrganization: organizationId }), [organizationId]);

  const load = useCallback(async () => {
    if (!isOwner) { setLoading(false); return; }
    try {
      const [passport, setups, accounts, shares] = await Promise.all([
        setupApi<Passport>("/v1/business-passport"), setupApi<{ sessions: Setup[] }>("/v1/setup-sessions"),
        setupApi<{ connections: Connection[] }>("/v1/connections"), setupApi<{ consents: Consent[] }>("/v1/business-passport/sharing/consents"),
      ]);
      setProfile(passport); setSessions(setups.sessions); setConnections(accounts.connections); setConsents(shares.consents);
      setSelectedId(previous => setups.sessions.some(s => s.id === previous) ? previous : (setups.sessions.find(s => s.status !== "expired")?.id || null));
      setError(null);
    } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, [isOwner, setupApi]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    try { setStartPending(Boolean(sessionStorage.getItem(`gluons.setup.${organizationId}.start`))); }
    catch { setStartPending(false); setRetryStorageAvailable(false); }
  }, [organizationId]);

  function keyFor(purpose: string): string {
    const name = `gluons.setup.${organizationId}.${purpose}`;
    if (pending.current[name]) return pending.current[name];
    try { pending.current[name] = sessionStorage.getItem(name) || crypto.randomUUID(); sessionStorage.setItem(name, pending.current[name]); }
    catch { pending.current[name] = crypto.randomUUID(); setRetryStorageAvailable(false); }
    if (purpose === "start") setStartPending(true);
    return pending.current[name];
  }
  function clearKey(purpose: string) {
    const name = `gluons.setup.${organizationId}.${purpose}`;
    delete pending.current[name];
    try { sessionStorage.removeItem(name); } catch { /* The server retains the setup. */ }
    if (purpose === "start") setStartPending(false);
  }
  async function run(fn: () => Promise<void>, success: string) {
    setBusy(true); setError(null); setNotice("");
    try { await fn(); await load(); setNotice(success); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const selected = sessions.find(s => s.id === selectedId) || null;
  const gmail = connections.filter(c => c.provider === "gmail");
  const shops = connections.filter(c => c.provider === "shopify");
  const capabilities = includeSend ? ["read_messages", "send_messages"] : ["read_messages"];
  const measuredSetup = selected && selected.status !== "expired" ? selected.id : null;

  useEffect(() => {
    if (!measuredSetup) return;
    let lastTick = performance.now(), lastInput = lastTick, elapsed = 0;
    let focused = document.visibilityState === "visible" && document.hasFocus();
    function tick() {
      const now = performance.now();
      if (focused) elapsed += Math.max(0, Math.min(now, lastInput + 60_000) - lastTick);
      lastTick = now;
    }
    function flush() {
      const seconds = Math.min(300, Math.floor(elapsed / 1000));
      if (seconds < 1) return;
      elapsed -= seconds * 1000;
      void setupApi(`/v1/setup-sessions/${measuredSetup}/measurements`, { method: "POST", body: { kind: "active_seconds", seconds } }).catch(() => {});
    }
    function input() { tick(); lastInput = performance.now(); }
    function focus() { tick(); focused = document.visibilityState === "visible" && document.hasFocus(); }
    const timer = window.setInterval(() => { tick(); if (elapsed >= 60_000) flush(); }, 10_000);
    window.addEventListener("focus", focus); window.addEventListener("blur", focus);
    document.addEventListener("visibilitychange", focus); document.addEventListener("pointerdown", input); document.addEventListener("keydown", input);
    return () => { tick(); flush(); window.clearInterval(timer); window.removeEventListener("focus", focus); window.removeEventListener("blur", focus); document.removeEventListener("visibilitychange", focus); document.removeEventListener("pointerdown", input); document.removeEventListener("keydown", input); };
  }, [measuredSetup, setupApi]);

  if (!isOwner) return <div className="page-title"><div><h1>Business setup</h1><p>A business owner can manage setup and Passport details here.</p></div></div>;
  if (loading) return <div className="loading" role="status">Loading business setup…</div>;
  return <div className="setup-room">
    <div className="page-title"><div><div className="eyebrow">{organizationName}</div><h1>Business setup</h1><p>Keep your business details and account handoffs together. Connecting an account does not approve an action.</p></div></div>
    {error && <div className="error-box" role="alert">{error} <button className="text-button" onClick={() => window.location.reload()}>Reload page</button></div>}
    {notice && <div className="success-box" role="status">{notice}</div>}
    <div className="detail-grid">
      <section className="panel"><div className="panel-heading"><h2>Setup progress</h2></div><div className="panel-body">
        <p className="body-copy">Your progress is saved for this business. Return here after signing in or finishing a provider handoff.</p>
        {!retryStorageAvailable && <p className="small-print">This browser cannot retain a retry key. After a reload, check saved setups before starting another.</p>}
        {sessions.length > 0 && <label>Choose saved setup<select value={selectedId || ""} onChange={e => setSelectedId(e.target.value)}><option value="">Choose a setup</option>{sessions.map(s => <option key={s.id} value={s.id}>{when(s.created_at)} · {s.status.replaceAll("_", " ")}</option>)}</select></label>}
        <button className="button secondary" disabled={busy} onClick={() => void run(async () => { const key = keyFor("start"); const result = await setupApi<Setup>("/v1/setup-sessions", { method: "POST", idempotencyKey: key, body: { intent: "business_launch" } }); setSessions(old => [result, ...old.filter(s => s.id !== result.id)]); setSelectedId(result.id); clearKey("start"); }, "Setup started. No account access or action has been approved.")}>{startPending ? "Recover previous setup" : "Start a new setup"}</button>
        {selected && <div className="setup-card"><h3>Current setup</h3><p>Started {when(selected.created_at)} · expires {when(selected.expires_at)}</p>
          {selected.connection && <p><strong>Gmail:</strong> {selected.connection.account_display || connections.find(c => c.id === selected.connection?.connection_id)?.account_display || selected.connection.status.replaceAll("_", " ")}</p>}
          {selected.connection?.requested_scopes?.length ? <details><summary>Requested access</summary><ul>{selected.connection.requested_scopes.map(scope => <li key={scope}>{scope}</li>)}</ul></details> : null}
          {selected.next_actions.length ? <ul className="plain-list">{selected.next_actions.map((a, i) => <li key={`${a.code}-${i}`}>{a.label}{a.owner_url && <> · <Link className="text-button" href={a.owner_url}>Continue</Link></>}</li>)}</ul> : <div className="notice-box">The selected account meets the stored connection checks. Business actions still need their own policy and approval.</div>}
          {selected.connection?.status === "needs_configuration" && <p className="small-print">Google OAuth is not configured on this server. Ask the server administrator to configure it, then <Link href={selected.connection.owner_url}>review or cancel this handoff</Link> before starting a new one.</p>}
          {selected.status === "expired" ? <button className="button danger-outline" disabled={busy} onClick={() => void run(async () => { await setupApi(`/v1/setup-sessions/${selected.id}/remove-expired`, { method: "POST", body: { expected_version: selected.version } }); setSelectedId(null); }, "Expired setup progress removed. Connected accounts are unchanged.")}>Remove expired setup</button> : <>
            <label className="checkbox-label"><input type="checkbox" checked={includeSend} onChange={e => setIncludeSend(e.target.checked)} /> <span>Request sending as well as reading. Sending still needs a separate action policy and approval.</span></label>
            <button className="button primary" disabled={busy} onClick={() => void run(async () => { const purpose = `connection.${selected.id}.${includeSend ? "read-send" : "read"}`; const key = keyFor(purpose); const updated = await setupApi<Setup>(`/v1/setup-sessions/${selected.id}/connection/request`, { method: "POST", idempotencyKey: key, body: { provider: "gmail", capabilities, expected_version: selected.version } }); setSessions(old => old.map(s => s.id === updated.id ? updated : s)); clearKey(purpose); }, "Gmail handoff prepared. Continue to review the requested access.")}>Prepare Gmail connection</button>
            {gmail.length > 0 && <div className="setup-divider"><label>Use an existing Gmail account<select value={selectedAccount} onChange={e => setSelectedAccount(e.target.value)}><option value="">Choose an account</option>{gmail.map(c => <option key={c.id} value={c.id}>{c.account_display} · {c.status}{c.account_confirmed ? " · confirmed" : " · not confirmed"}</option>)}</select></label><button className="button secondary" disabled={busy || !selectedAccount} onClick={() => void run(async () => { const updated = await setupApi<Setup>(`/v1/setup-sessions/${selected.id}/connection/reuse`, { method: "POST", body: { connection_id: selectedAccount, capabilities, expected_version: selected.version } }); setSessions(old => old.map(s => s.id === updated.id ? updated : s)); }, "Existing account selected. Its access and health were checked.")}>Use selected account</button></div>}
          </>}
          <details onToggle={event => { if (event.currentTarget.open && selected.status !== "expired") void setupApi(`/v1/setup-sessions/${selected.id}/measurements`, { method: "POST", body: { kind: "help_opened" } }).catch(() => {}); }}><summary>Need help with setup?</summary><p>Review the next step above. You can return to this business after signing in, or use Connections to manage provider accounts.</p></details>
          <p className="small-print">For active setups, we record help openings and estimated active time, without the values you enter. <Link href="/data">Review your data</Link>.</p>
        </div>}
        <p className="small-print">Other provider setup remains available in <Link href="/connections">Connections</Link>; unsupported provider actions remain blocked.</p>
      </div></section>
      <section className="panel"><div className="panel-heading"><h2>Business Passport</h2></div><div className="panel-body">
        <p className="body-copy">Confirm only the details you want to reuse. Do not enter passwords, account numbers or identity documents.</p>
        {profile && <><h3>Review business details</h3><ul className="plain-list">{(["business_name", "country", "timezone"] as const).map(name => { const value = name === "business_name" ? profile.organization.name : profile.organization[name]; const field = profile.fields[name]; return <li key={name}><strong>{labels[name]}:</strong> {display(value)} · {field?.status === "confirmed" ? "confirmed" : field?.status === "stale" ? "needs review" : "not confirmed"} {value && field?.status !== "confirmed" && <button className="text-button" disabled={busy} onClick={() => void run(async () => { await setupApi("/v1/business-passport/organization-fields/confirm", { method: "POST", body: { fields: [name], expected_version: profile.version, expected_organization_version: profile.organization.version } }); setSharePreview(null); }, `${labels[name]} confirmed.`)}>Confirm</button>}</li>; })}</ul>
          <details><summary>Correct business details</summary><form key={profile.organization.version} onSubmit={event => { event.preventDefault(); const data = new FormData(event.currentTarget); const name = String(data.get("name") || "").trim(); const country = String(data.get("country") || "").trim(); const timezone = String(data.get("timezone") || "").trim(); const fields: Record<string, string | null> = {}; if (name !== profile.organization.name) fields.name = name; if (country !== (profile.organization.country || "")) fields.country = country || null; if (timezone !== profile.organization.timezone) fields.timezone = timezone; if (!Object.keys(fields).length) { setError("Change a business detail before saving."); return; } void run(async () => { await setupApi("/v1/business-passport/organization-fields", { method: "PATCH", body: { expected_organization_version: profile.organization.version, fields } }); setSharePreview(null); setImportPreview(null); await onOrganizationUpdated(); }, "Business details updated. Review any stale confirmations."); }}><label>Business name<input name="name" defaultValue={profile.organization.name} required maxLength={200} /></label><div className="form-grid"><label>Country code<input name="country" defaultValue={profile.organization.country || ""} maxLength={2} pattern="[A-Z]{2}" /></label><label>Time zone<input name="timezone" defaultValue={profile.organization.timezone} required maxLength={64} /></label></div><p className="small-print">Changing these details can make earlier confirmations and sharing consent stale.</p><button className="button secondary" disabled={busy}>Save business details</button></form></details>
          <h3>Saved details</h3>{Object.keys(profile.fields).length ? <ul className="plain-list">{Object.entries(profile.fields).map(([name, field]) => <li key={name}><strong>{labels[name] || name}:</strong> {field.status === "stale" ? "Needs review" : display(field.value)} · {field.source === "shopify" ? "confirmed from Shopify" : field.source === "organization" ? "organization record" : "owner confirmed"} {field.source !== "organization" && <button className="text-button" aria-label={`Remove ${labels[name] || name}`} disabled={busy} onClick={() => void run(async () => { await setupApi("/v1/business-passport", { method: "PATCH", body: { expected_version: profile.version, fields: { [name]: null } } }); setShareFields(old => old.filter(item => item !== name)); setSharePreview(null); setImportPreview(null); }, `${labels[name] || name} removed. Existing sharing consent may need review.`)}>Remove</button>}</li>)}</ul> : <p className="body-copy">No details confirmed yet.</p>}
          <form key={profile.version} onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const fields: Record<string, string | string[]> = {}; for (const name of ["brand_name", "currency", "business_type", "selling_regions", "support_email", "support_hours", "operational_address"]) { const value = String(data.get(name) || "").trim(); if (value) fields[name] = name === "selling_regions" ? value.split(",").map(s => s.trim()).filter(Boolean) : value; } if (!Object.keys(fields).length) { setError("Enter at least one detail to save."); return; } void run(async () => { await setupApi("/v1/business-passport", { method: "PATCH", body: { expected_version: profile.version, fields } }); setSharePreview(null); setImportPreview(null); form.reset(); }, "Business details saved and confirmed."); }}>
            <label>Brand name<input name="brand_name" maxLength={200} /></label><div className="form-grid"><label>Currency code<input name="currency" maxLength={3} placeholder="INR" /></label><label>Business type<input name="business_type" maxLength={100} /></label></div>
            <label>Selling regions<input name="selling_regions" placeholder="IN, US" /></label><label>Support email<input name="support_email" type="email" /></label><label>Support hours<input name="support_hours" maxLength={300} /></label><label>Operating address<textarea name="operational_address" rows={3} maxLength={500} /></label>
            <p className="small-print">Leave a field blank to keep its existing value.</p><button className="button secondary" disabled={busy}>Save entered details</button>
          </form>
        </>}
      </div></section>
    </div>
    {profile && <div className="detail-grid setup-sharing">
      <section className="panel"><div className="panel-heading"><h2>Review a store detail</h2></div><div className="panel-body"><p className="body-copy">A confirmed, recently checked Shopify connection can suggest its currency. Nothing is imported until you confirm it.</p>
        {shops.length ? <><label>Shopify account<select value={selectedShop} onChange={e => { setSelectedShop(e.target.value); setImportPreview(null); }}><option value="">Choose an account</option>{shops.map(c => <option key={c.id} value={c.id}>{c.account_display} · {c.status}</option>)}</select></label><button className="button secondary" disabled={busy || !selectedShop} onClick={() => void run(async () => { setImportPreview(await setupApi<Preview>("/v1/business-passport/imports/preview", { method: "POST", body: { connection_id: selectedShop } })); }, "Review the suggested value below.")}>Preview currency</button></> : <p className="body-copy">No Shopify account is connected.</p>}
        {importPreview && <div className="setup-card"><p><strong>Suggested currency:</strong> {display(importPreview.fields.currency)}</p><p>{importPreview.notice}</p><button className="button primary" disabled={busy} onClick={() => void run(async () => { await setupApi("/v1/business-passport/imports/confirm", { method: "POST", body: { preview_token: importPreview.preview_token } }); setImportPreview(null); setSharePreview(null); }, "Currency imported after your confirmation.")}>Confirm currency</button><button className="text-button" onClick={() => setImportPreview(null)}>Cancel</button></div>}
      </div></section>
      <section className="panel"><div className="panel-heading"><h2>Review data sharing</h2></div><div className="panel-body"><p className="body-copy">Choose confirmed fields and a provider. Your confirmation records consent; this screen does not send data.</p>
        <label>Provider<select value={shareProvider} onChange={e => { setShareProvider(e.target.value); setSharePreview(null); }}><option value="shopify">Shopify</option><option value="gmail">Gmail</option></select></label>
        {Object.entries(profile.fields).filter(([, field]) => field.value !== undefined && field.status !== "stale").map(([name]) => <label className="checkbox-label" key={name}><input type="checkbox" checked={shareFields.includes(name)} onChange={e => { const checked = e.currentTarget.checked; setShareFields(old => checked ? [...old, name] : old.filter(item => item !== name)); setSharePreview(null); }} /> <span>{labels[name] || name}</span></label>)}
        <button className="button secondary" disabled={busy || !shareFields.length} onClick={() => void run(async () => { setSharePreview(await setupApi<Preview>("/v1/business-passport/sharing/preview", { method: "POST", body: { provider: shareProvider, purpose: "account_setup", fields: shareFields } })); }, "Review the exact fields below.")}>Preview sharing</button>
        {sharePreview && <div className="setup-card"><h3>Review for {sharePreview.provider}</h3><ul className="plain-list">{Object.entries(sharePreview.fields).map(([name, value]) => <li key={name}><strong>{labels[name] || name}:</strong> {display(value)}</li>)}</ul><p>{sharePreview.notice}</p><button className="button primary" disabled={busy} onClick={() => void run(async () => { await setupApi("/v1/business-passport/sharing/confirm", { method: "POST", body: { preview_token: sharePreview.preview_token } }); setSharePreview(null); }, "Consent recorded. No data was sent.")}>Confirm these fields</button><button className="text-button" onClick={() => setSharePreview(null)}>Cancel</button></div>}
        {consents.length > 0 && <><h3>Recorded consent</h3><ul className="plain-list">{consents.map(c => <li key={c.id}><strong>{c.provider}</strong> · {c.fields.map(f => labels[f] || f).join(", ")} · {c.active ? `active until ${when(c.expires_at)}` : "inactive"} {c.active && <button className="text-button" disabled={busy} onClick={() => void run(async () => { await setupApi(`/v1/business-passport/sharing/consents/${c.id}/revoke`, { method: "POST" }); }, "Consent revoked.")}>Revoke</button>}</li>)}</ul></>}
      </div></section>
    </div>}
  </div>;
}
