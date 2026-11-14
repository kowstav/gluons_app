"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, Can, Organization, Row, Session, Spending } from "@/lib/api";
import { date, fromNow, human, money } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import Icon from "../icon";
import { ConnectionNotice, ProviderConnect } from "../operations";
import { Badge, CopyButton, DataView, Empty, ErrorBox, Meter, Note, PageTitle, Panel, SkeletonRows, Success, useConfirm, Field } from "../ui";
import { Stat } from "./home";

const PROVIDERS = [
  { id: "gmail", title: "Gmail", icon: "mail", text: "Read the order messages you authorize and send approved replies, through Google’s own sign-in." },
  { id: "whatsapp_business", title: "WhatsApp Business", icon: "message", text: "Sign in through Meta’s own window and pick your business number. Personal WhatsApp is not supported." },
  { id: "elevenlabs", title: "Telephone and ElevenLabs", icon: "phone", text: "Needs an authorized inbound number or verified carrier forwarding, and one real test call." },
  { id: "shopify", title: "Shopify", icon: "store", text: "Install Gluons from Shopify, then choose this business. Identity and payment verification stay with you." },
];
const providerIcon = (provider: unknown) => PROVIDERS.find(p => p.id === provider)?.icon || "link";

export function Connections({ path, can }: { path: string; can: Can }) {
  const { data, error, loading, load } = useResource<{ connections: Row[] }>("/v1/connections");
  const [failure, setFailure] = useState<string | null>(null); const [notice, setNotice] = useState(""); const [session, setSession] = useState<Row | null>(null); const [busy, setBusy] = useState(false); const [gmailSend, setGmailSend] = useState(false);
  const [confirm, confirmDialog] = useConfirm();
  const manage = can("connections:manage");
  useEffect(() => { if (path.startsWith("/setup/")) api<Row>(`/v1/connections/sessions/${path.split("/")[2]}`).then(setSession).catch(e => setFailure(e.message)); }, [path]);
  const sessionId = session ? String(session.session_id || session.id) : "";
  async function run(fn: () => Promise<unknown>, done = "") { setBusy(true); setFailure(null); setNotice(""); try { await fn(); setNotice(done); } catch (e) { setFailure((e as Error).message); } finally { setBusy(false); } }
  const begin = (provider: string) => run(async () => setSession(await api<Row>("/v1/connections/sessions", { method: "POST", body: { provider, capabilities: provider === "gmail" ? ["read_order_messages", ...(gmailSend ? ["send_order_confirmations"] : [])] : [] } })));

  return <>
    {can("org:manage") && <Link className="back-link" href="/business-setup">← Return to business setup</Link>}
    <PageTitle title="Connections" description="Sign in to each provider on its own page. A connection lets Gluons reach the account; every automation still needs its own signed policy before it can act." />
    <ConnectionNotice /><ErrorBox error={failure} /><Success>{notice}</Success>

    {session && <Panel title="Connection setup" meta={<Badge status={String(session.status)}>{human(session.status)}</Badge>}><div className="panel-body">
      <h3>{human(session.provider)}</h3>
      <p className="body-copy">{String(session.next_step || session.message || "Review the requested access, then continue to the provider’s own authorization page.")}</p>
      {Array.isArray(session.requested_scopes) && session.requested_scopes.length > 0 && <details><summary>Access Gluons will request ({session.requested_scopes.length})</summary><ul className="plain-list mono">{(session.requested_scopes as string[]).map(scope => <li key={scope}>{scope}</li>)}</ul></details>}
      <p className="small-print">This setup expires {fromNow(session.expires_at)} ({date(session.expires_at)}). A setup link alone never grants access.</p>
      {["whatsapp_business", "shopify", "elevenlabs"].includes(String(session.provider)) && <ProviderConnect provider={String(session.provider)} can={can} />}
      <div className="button-row" style={{ marginTop: 12 }}>
        <button className="button primary" disabled={!manage || busy || session.status !== "requires_external_authorization"} onClick={() => run(async () => { const r = await api<Row>(`/v1/connections/sessions/${sessionId}/authorize`, { method: "POST" }); const url = new URL(String(r.authorization_url)); if (url.protocol !== "https:") throw new Error("The provider’s authorization page must use HTTPS. Nothing was opened."); window.location.assign(url.toString()); })}>Continue to provider</button>
        <button className="button secondary" disabled={busy} onClick={() => run(async () => { setSession(await api<Row>(`/v1/connections/sessions/${sessionId}`)); await load(); })}>Refresh status</button>
        <button className="button danger-outline" disabled={busy} onClick={() => run(async () => { setSession(await api<Row>(`/v1/connections/sessions/${sessionId}/cancel`, { method: "POST" })); await load(); }, "Setup cancelled. Nothing was connected.")}>Cancel setup</button>
        <button className="text-button" onClick={() => setSession(null)}>Close</button>
      </div></div></Panel>}

    <Panel title="Connected accounts"><ErrorBox error={error} retry={load} />
      {loading && !data ? <SkeletonRows /> : data?.connections.length ? <div className="panel-body">{data.connections.map(c => <div className="connection-row" key={String(c.id)}><span className="row-icon"><Icon name={providerIcon(c.provider)} size={18} /></span>
        <div className="grow"><strong>{String(c.account_display || human(c.provider))}</strong><small>{human(c.provider)}, {human(c.environment)} environment{c.last_checked_at ? `, checked ${fromNow(c.last_checked_at)}` : ""}{c.account_confirmed ? "" : ", account not confirmed"}</small>
          <details><summary className="subtle">Granted access and ID</summary><small className="mono">{Array.isArray(c.scopes) && c.scopes.length ? c.scopes.join(" ") : "The provider reported no scopes."}</small><small className="mono">ID {String(c.id)}</small></details></div>
        <Badge status={String(c.status)}>{human(c.status)}</Badge>
        <div className="button-row">
          <button className="button secondary compact" disabled={!manage || busy} onClick={() => run(async () => { await api(`/v1/connections/${c.id}/verify`, { method: "POST" }); await load(); }, "Connection checked with the provider.")}>Check now</button>
          {!c.account_confirmed && <button className="button primary compact" disabled={!manage || busy} onClick={() => run(async () => { await api(`/v1/connections/${c.id}/confirm`, { method: "POST" }); await load(); }, "Account confirmed as yours.")}>Confirm account</button>}
          <button className="button danger-outline compact" disabled={!manage || busy} onClick={async () => { if (await confirm({ title: `Disconnect ${String(c.account_display || human(c.provider))}?`, body: "Automations that use this account stop being able to act through it. You can connect it again later.", confirmLabel: "Disconnect", danger: true })) await run(async () => { await api(`/v1/connections/${c.id}`, { method: "DELETE" }); await load(); }, "Account disconnected."); }}>Disconnect</button>
        </div></div>)}</div>
        : <Empty icon="link" title="No accounts connected">Connect only the accounts your workflows need. Credentials are encrypted on the server and never shown to a model.</Empty>}</Panel>

    <h2 style={{ margin: "28px 0 4px" }}>Add an account</h2>
    <div className="provider-grid">{PROVIDERS.map(p => <Panel key={p.id}><div className="panel-body"><span className="skill-icon"><Icon name={p.icon} size={21} /></span><h2>{p.title}</h2><p className="body-copy">{p.text}</p>
      {p.id === "gmail" ? <><label className="checkbox-label"><input type="checkbox" checked={gmailSend} onChange={e => setGmailSend(e.target.checked)} /><span>Also request sending. Sending still needs a separately signed policy.</span></label><button className="button secondary" disabled={busy || !manage} onClick={() => begin(p.id)}>Review setup requirements</button></> : <ProviderConnect provider={p.id} can={can} />}
    </div></Panel>)}</div>
    {confirmDialog}
  </>;
}

export function Knowledge({ can }: { can: Can }) {
  const { data, error, loading, load } = useResource<{ documents: Row[] }>("/v1/knowledge/documents");
  const [failure, setFailure] = useState<string | null>(null); const [notice, setNotice] = useState(""); const [query, setQuery] = useState("");
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = (data?.documents || []).filter(d => words.every(word => `${d.title} ${d.body || d.excerpt || ""}`.toLowerCase().includes(word)));
  return <>
    <PageTitle title="Business knowledge" description="Facts your automations may use, such as opening hours and procedures. Knowledge gives context only; it can never change a permission." />
    <div className="detail-grid">
      <Panel title="Saved knowledge" meta={(data?.documents.length || 0) > 4 ? <div className="search-input" style={{ flex: "0 1 240px" }}><Icon name="search" size={16} /><input type="search" aria-label="Search knowledge" placeholder="Search…" value={query} onChange={e => setQuery(e.target.value)} style={{ height: 36 }} autoComplete="off" /></div> : undefined}>
        <ErrorBox error={error} retry={load} />
        {loading && !data ? <SkeletonRows /> : rows.length ? <div className="panel-body">{rows.map(d => <details className="knowledge-item" key={String(d.id)}><summary><Icon name="box" size={17} /><strong>{String(d.title)}</strong><Badge>{human(d.kind)}</Badge></summary><p>{String(d.body || d.excerpt || "")}</p><small>Added {date(d.created_at)}{d.source ? `, source: ${d.source}` : ""}</small></details>)}</div>
          : <Empty icon="box" title={data?.documents.length ? "Nothing matches" : "No knowledge saved yet"}>{data?.documents.length ? "Try a different word." : "Add your opening hours, product notes and approved procedures so automations can use them."}</Empty>}
      </Panel>
      <Panel title="Add a note"><div className="panel-body"><ErrorBox error={failure} /><Success>{notice}</Success>
        <form onSubmit={async e => { e.preventDefault(); const form = e.currentTarget; const fields = Object.fromEntries(new FormData(form)); setFailure(null); setNotice(""); try { await api("/v1/knowledge/documents", { method: "POST", body: { ...fields, kind: "note", source: fields.source || null } }); form.reset(); await load(); setNotice("Knowledge saved."); } catch (err) { setFailure((err as Error).message); } }}>
          <label>Title<input name="title" required maxLength={200} autoComplete="off" /></label>
          <Field label="Approved content" hint={<>Facts only. Do not paste passwords or account numbers.</>}><textarea name="body" rows={8} required /></Field>
          <Field label="Source reference" hint={<>Optional link or document name.</>}><input name="source" autoComplete="off" /></Field>
          <button className="button primary wide" disabled={!can("knowledge:write")}>Save knowledge</button>
        </form></div></Panel>
    </div>
  </>;
}

type Roi = { measured?: Row; estimated?: Row; unverified?: Row; recorded_estimates?: unknown; missing_evidence?: string[] };

export function Costs({ org, can, onUpdate }: { org: Organization | null; can: Can; onUpdate: () => Promise<void> }) {
  const { data, error, load } = useResource<Spending>("/v1/billing/usage"); const report = useResource<Roi>("/v1/reports/roi");
  const [failure, setFailure] = useState<string | null>(null); const [saved, setSaved] = useState(false);
  const used = Number(data?.total_usd || 0), budget = Number(org?.monthly_budget_usd || 0);
  return <>
    <PageTitle title="Costs & insights" description="Recorded usage against the ceilings you set. Measured figures and estimates are always shown separately." />
    <ErrorBox error={error || failure} retry={error ? load : undefined} />{saved && <Success>Monthly budget saved.</Success>}
    <div className="stat-grid three">
      <Stat label="Recorded usage this month" value={data ? money(used) : "—"} icon="money" note={data ? `Since ${date(data.period_start)}` : "Loading recorded usage…"}>{budget > 0 && data && <Meter value={used} max={budget} label="Share of monthly budget used" />}</Stat>
      <Stat label="Monthly budget" value={budget ? money(budget) : "Not set"} icon="shield" note="Checked on the server before any spend" />
      <Stat label="Left this month" value={budget && data ? money(Math.max(0, budget - used)) : "—"} icon="chart" note={budget ? "Chargeable work stops at zero" : "Set a budget to cap spending"} />
    </div>
    <div className="detail-grid">
      <Panel title="Usage by provider">{data?.breakdown.length ? <div className="table-wrap"><table><thead><tr><th>Provider</th><th>Category</th><th>Basis</th><th className="num">Events</th><th className="num">Cost</th></tr></thead><tbody>{data.breakdown.map((r, i) => <tr key={i}><td>{r.provider}</td><td>{human(r.category)}</td><td><Badge status={r.cost_basis === "measured" ? "verified" : "draft"}>{r.cost_basis}</Badge></td><td className="num">{r.events}</td><td className="num">{money(r.cost_usd)}</td></tr>)}</tbody></table></div>
        : <Empty icon="money" title="No usage recorded yet">Usage is added when a task incurs a tracked cost. A provider’s own invoice may still need reconciling.</Empty>}</Panel>
      <Panel title="Monthly budget"><div className="panel-body">
        <form onSubmit={async e => { e.preventDefault(); setFailure(null); setSaved(false); const f = new FormData(e.currentTarget); try { await api("/v1/organizations/current", { method: "PATCH", body: { monthly_budget_usd: f.get("budget") === "" ? null : Number(f.get("budget")) } }); await onUpdate(); await load(); setSaved(true); } catch (err) { setFailure((err as Error).message); } }}>
          <Field label="Monthly business budget (USD)" hint={<>Leave empty for no ceiling.</>}><input key={org?.monthly_budget_usd} name="budget" type="number" inputMode="decimal" min="0" step="0.01" defaultValue={org?.monthly_budget_usd || ""} autoComplete="off" /></Field>
          <p className="body-copy">New chargeable work stops once recorded spend plus reserved costs reach this ceiling.</p>
          <button className="button primary" disabled={!can("billing:manage")}>Save monthly budget</button>
        </form></div></Panel>
    </div>
    <Panel title="Return on investment"><div className="panel-body"><ErrorBox error={report.error} retry={report.load} />
      {report.data ? <>
        {(report.data.missing_evidence || []).map((item, i) => <Note key={i} icon="alert">{item}</Note>)}
        <div className="evidence-grid">
          <div><h3><Badge status="verified">measured</Badge></h3><DataView value={report.data.measured} raw={false} /></div>
          <div><h3><Badge status="draft">estimated</Badge></h3><DataView value={report.data.estimated} raw={false} /></div>
          <div className="span-two"><h3><Badge status="draft">unverified</Badge></h3><DataView value={report.data.unverified} raw={false} /></div>
        </div>
        <details className="data-raw"><summary>Exact report</summary><DataView value={report.data} /></details>
      </> : !report.error && <p className="body-copy">Return on investment needs a baseline, recorded time studies and verified costs. Until then nothing is assumed.</p>}
    </div></Panel>
  </>;
}

const ROLES = ["viewer", "operator", "approver", "admin", "field_rep", "support"];

export function Security({ session, org, can, onUpdate }: { session: Session; org: Organization | null; can: Can; onUpdate: () => Promise<void> }) {
  const members = useResource<{ members: Row[] }>("/v1/organizations/current/members");
  const sessions = useResource<{ sessions: Row[]; current: string }>("/v1/auth/sessions");
  const assistants = useResource<{ assistants: Row[] }>("/v1/assistants");
  const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState(""); const [mfa, setMfa] = useState<Row | null>(null); const [codes, setCodes] = useState<string[] | null>(null);
  const [confirm, confirmDialog] = useConfirm();
  const stopped = Boolean(org?.emergency_stop_at);
  async function run(fn: () => Promise<unknown>, done = "") { setError(null); setNotice(""); try { await fn(); setNotice(done); } catch (e) { setError((e as Error).message); } }

  return <>
    <PageTitle title="Team & security" description={`Who can act for ${org?.name}, which assistants and browsers are signed in, and the switch that stops external actions.`} />
    <ErrorBox error={error} /><Success>{notice}</Success>

    {can("emergency:stop") && <Panel title="Emergency stop" meta={<Badge status={stopped ? "blocked" : "active"}>{stopped ? "stop is on" : "actions allowed"}</Badge>}><div className="panel-body inline-between">
      <p className="body-copy" style={{ margin: 0, flex: "1 1 320px" }}>{stopped ? "New external actions are blocked for this business. Actions already committed stay in the activity history." : "Blocks every new external action for this business at once. It does not undo actions already committed."}</p>
      {stopped ? <button className="button secondary" onClick={async () => { if (await confirm({ title: "Allow external actions again?", body: "Automations will be able to act within their signed policies again.", confirmLabel: "Release emergency stop" })) await run(async () => { await api("/v1/organizations/current/emergency-stop", { method: "POST", body: { active: false } }); await onUpdate(); }, "Emergency stop released."); }}>Release emergency stop</button>
        : <button className="button danger" onClick={() => run(async () => { await api("/v1/organizations/current/emergency-stop", { method: "POST", body: { active: true } }); await onUpdate(); }, "Emergency stop is on. New external actions are blocked.")}><Icon name="stop" size={16} />Stop external actions</button>}
    </div></Panel>}

    <Panel title="Team"><ErrorBox error={members.error} retry={members.load} /><div className="panel-body">
      {members.loading && !members.data ? <SkeletonRows rows={2} /> : members.data?.members.map(m => <div className="member-row" key={String(m.id)}><span className="user-avatar">{String(m.display_name || m.email || "?")[0]}</span><div className="grow"><strong>{String(m.display_name || m.email)}{m.email === session.user.email ? " (you)" : ""}</strong><small>{String(m.email)}</small></div>
        {can("members:manage") ? <><select aria-label={`Role for ${m.email}`} className="member-role" value={String(m.role)} onChange={e => run(async () => { await api(`/v1/organizations/current/members/${m.id}`, { method: "PATCH", body: { role: e.target.value } }); await members.load(); }, `Role updated for ${m.email}.`)}>{["owner", ...ROLES].map(role => <option key={role} value={role}>{human(role)}</option>)}</select>
          <button className="button danger-outline compact" onClick={async () => { if (await confirm({ title: `Remove ${String(m.display_name || m.email)}?`, body: `They lose access to ${org?.name} immediately. You can invite them again later.`, confirmLabel: "Remove member", danger: true })) await run(async () => { await api(`/v1/organizations/current/members/${m.id}`, { method: "DELETE" }); await members.load(); }, "Member removed."); }}>Remove member</button></> : <Badge>{human(m.role)}</Badge>}</div>)}
      {can("members:manage") && <form className="inline-form" style={{ marginTop: 16 }} onSubmit={e => { e.preventDefault(); const form = e.currentTarget; const f = Object.fromEntries(new FormData(form)); void run(async () => { await api("/v1/organizations/current/invitations", { method: "POST", body: f }); form.reset(); }, `Invitation sent to ${f.email}.`); }}>
        <label className="grow">Invite by email<input type="email" name="email" required autoComplete="off" spellCheck={false} placeholder="teammate@business.com" /></label>
        <label>Role<select name="role">{[...ROLES, ...(org?.role === "owner" ? ["owner"] : [])].map(r => <option key={r} value={r}>{human(r)}</option>)}</select></label>
        <button className="button secondary">Send invitation</button></form>}
    </div></Panel>

    <div className="home-bottom">
      <Panel title="Connected assistants"><ErrorBox error={assistants.error} retry={assistants.load} /><div className="panel-body">
        {assistants.data?.assistants.length ? assistants.data.assistants.map(a => <div className="member-row" key={String(a.id)}><span className="row-icon"><Icon name="spark" size={18} /></span><div className="grow"><strong>{String(a.client_name || a.provider || a.client_id)}</strong><small>{Array.isArray(a.scopes) ? a.scopes.join(", ") : ""}</small></div><Badge status={String(a.status)}>{human(a.status)}</Badge>
          <button className="button danger-outline compact" disabled={!can("assistants:manage")} onClick={() => run(async () => { await api(`/v1/assistants/${a.id}/revoke`, { method: "POST" }); await assistants.load(); }, "Assistant access revoked.")}>Revoke</button></div>)
          : assistants.loading ? <SkeletonRows rows={2} /> : <Empty icon="spark" title="No assistants connected">Authorize Gluons from ChatGPT, Claude or another remote MCP client. Each authorization can be revoked on its own.</Empty>}
      </div></Panel>
      <Panel title="Two-step verification"><div className="panel-body">
        <p className="body-copy">Signed in as <strong>{session.user.email}</strong>. An authenticator app adds a second check at sign-in.</p>
        {!mfa && !codes && <button className="button secondary" onClick={() => run(async () => setMfa(await api<Row>("/v1/auth/mfa/enroll", { method: "POST" })))}>Set up authenticator</button>}
        {mfa && !codes && <div className="mfa-setup"><p><strong>1.</strong> Add this key to your authenticator app. Keep it private.</p>
          <div className="secret-box"><span translate="no">{String(mfa.secret)}</span><CopyButton text={String(mfa.secret)} label="Copy key" /></div>
          {typeof mfa.otpauth_uri === "string" && <p className="small-print"><a href={mfa.otpauth_uri}>Open in an authenticator app on this device</a></p>}
          <form onSubmit={e => { e.preventDefault(); const body = Object.fromEntries(new FormData(e.currentTarget)); void run(async () => { const result = await api<{ recovery_codes: string[] }>("/v1/auth/mfa/confirm", { method: "POST", body }); setCodes(result.recovery_codes); setMfa(null); }, "Two-step verification is on."); }}>
            <label><span><strong>2.</strong> Enter the 6-digit code it shows</span><input name="code" required autoComplete="one-time-code" inputMode="numeric" spellCheck={false} /></label>
            <button className="button primary">Turn on two-step verification</button></form></div>}
        {codes && <div className="mfa-setup"><p><strong>Save these recovery codes now.</strong> Each works once if you lose your authenticator. They are not shown again.</p>
          <ul className="code-grid" translate="no">{codes.map(code => <li key={code}>{code}</li>)}</ul>
          <div className="button-row"><CopyButton text={codes.join("\n")} label="Copy all codes" /><button className="button secondary compact" onClick={() => setCodes(null)}>I have saved them</button></div></div>}
      </div></Panel>
    </div>

    <Panel title="Signed-in browsers"><ErrorBox error={sessions.error} retry={sessions.load} /><div className="panel-body">
      {sessions.loading && !sessions.data ? <SkeletonRows rows={2} /> : sessions.data?.sessions.map(s => { const mine = s.id === sessions.data?.current; return <div className="member-row" key={String(s.id)}><span className="row-icon"><Icon name="monitor" size={18} /></span><div className="grow"><strong>{mine ? "This browser" : "Another browser"}</strong><small>{String(s.user_agent || "Unknown browser")}</small><small>Signed in {date(s.created_at)}</small></div>
        <button className="button secondary compact" onClick={() => run(async () => { await api(`/v1/auth/sessions/${s.id}`, { method: "DELETE" }); if (mine) await onUpdate(); else await sessions.load(); }, mine ? "" : "That browser was signed out.")}>{mine ? "Sign out here" : "Sign out that browser"}</button></div>; })}
    </div></Panel>
    {confirmDialog}
  </>;
}

export function AssistantConsent() {
  const [consent, setConsent] = useState<Row | null>(null); const [error, setError] = useState<string | null>(null); const [requestId, setRequestId] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { const id = new URLSearchParams(window.location.search).get("request_id") || ""; setRequestId(id); if (id) api<Row>(`/v1/assistants/consent/${encodeURIComponent(id)}`).then(setConsent).catch(e => setError(e.message)); else setError("This authorization link is incomplete. Start again from your assistant."); }, []);
  async function decide(approve: boolean) {
    setBusy(true); setError(null);
    try { const r = await api<{ redirect_url: string }>(`/v1/assistants/consent/${encodeURIComponent(requestId)}`, { method: "POST", body: { approve } }); const target = new URL(r.redirect_url); if (!["https:", "http:"].includes(target.protocol)) throw new Error("The assistant’s return address is not a web address. Nothing was authorized from this page."); window.location.assign(target.toString()); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <>
    <PageTitle title="Authorize an assistant" description="This lets an assistant use Gluons for one business. It does not connect provider accounts and it does not sign any automation policy." />
    <ErrorBox error={error} />
    {consent && <Panel title={String(consent.client_name || "Assistant access request")}><div className="panel-body narrow">
      <dl className="facts"><div><dt>Business</dt><dd>{String(consent.organization_name)}</dd></div><div><dt>Request expires</dt><dd>{date(consent.expires_at)}</dd></div><div className="span-two"><dt>Client</dt><dd><span className="mono">{String(consent.client_id)}</span> <span className="subtle">(its name is self-reported and not verified)</span></dd></div><div className="span-two"><dt>Permissions requested</dt><dd>{Array.isArray(consent.scopes) ? consent.scopes.join(", ") : String(consent.scopes)}</dd></div><div className="span-two"><dt>Returns you to</dt><dd className="mono">{String(consent.redirect_uri)}</dd></div></dl>
      <Note>{String(consent.notice || "The assistant receives only the permissions granted for this business. Financial and legal authority stays on Gluons Web.")}</Note>
      <div className="button-row"><button className="button secondary" disabled={busy} onClick={() => decide(false)}>Deny access</button><button className="button primary" disabled={busy} onClick={() => decide(true)}>Authorize assistant</button></div>
    </div></Panel>}
  </>;
}

export function AcceptInvite({ onDone }: { onDone: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState(""); const [busy, setBusy] = useState(false);
  return <>
    <PageTitle title="Join a business" description="Accept the invitation that was sent to your verified email address." />
    <ErrorBox error={error} /><Success>{notice}</Success>
    {!notice && <button className="button primary" disabled={busy} onClick={async () => { const p = new URLSearchParams(window.location.search); setBusy(true); setError(null); try { await api(`/v1/organizations/${encodeURIComponent(p.get("org") || "")}/invitations/accept`, { method: "POST", body: { token: p.get("token") } }); setNotice("Invitation accepted. Switch to this business from the selector at the top of the navigation."); await onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>Accept invitation</button>}
    {notice && <Link className="button secondary" href="/org/select">Choose a business</Link>}
  </>;
}
