"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { api, Approval, AuditEvent, Can, Row } from "@/lib/api";
import { date, day, fromNow, human, money, time } from "@/lib/format";
import { useQueryState, useResource } from "@/lib/hooks";
import Icon from "../icon";
import { Badge, DataView, Diff, Empty, ErrorBox, PageTitle, Panel, SkeletonRows, Success, Tabs, Field } from "../ui";

type InboxApproval = Approval & { automation_id?: string | null; run_id?: string | null; required_role?: string | null; decided_at?: string | null };
const STATUSES = [{ id: "pending", label: "Waiting" }, { id: "approved", label: "Approved" }, { id: "denied", label: "Denied" }, { id: "expired", label: "Expired" }];

export function Approvals({ can }: { can: Can }) {
  const [status, setStatus] = useQueryState("status", "pending");
  const current = STATUSES.some(s => s.id === status) ? status : "pending";
  const { data, error, loading, load } = useResource<{ approvals: InboxApproval[] }>(`/v1/approvals?status=${current}`);
  const [failure, setFailure] = useState<string | null>(null); const [notice, setNotice] = useState(""); const [busy, setBusy] = useState<string | null>(null);
  const allowed = can("approvals:decide");

  async function decide(a: InboxApproval, decision: string, reason: FormDataEntryValue | null) {
    setBusy(a.id); setFailure(null); setNotice("");
    try { await api(`/v1/approvals/${a.id}/decide`, { method: "POST", body: { decision, reason: reason || null, expected_version: a.version } }); await load(); setNotice(decision === "approve" ? `Approved: ${a.title}` : `Denied: ${a.title}`); }
    catch (err) { setFailure((err as Error).message); } finally { setBusy(null); }
  }

  return <>
    <PageTitle title="Approval inbox" description="Each item shows the exact action, its cost and its evidence. Nothing moves until you decide." />
    <Tabs label="Approval status" value={current} onChange={setStatus} tabs={STATUSES} />
    <ErrorBox error={error || failure} retry={error ? load : undefined} /><Success>{notice}</Success>
    {!allowed && current === "pending" && <p className="small-print" style={{ marginBottom: 16 }}>Your role can view approvals but cannot decide them.</p>}
    {loading && !data ? <Panel><SkeletonRows rows={3} /></Panel> : data?.approvals.length ? data.approvals.map(a => <Panel key={a.id} className="approval-card"><div className="panel-body">
      <div className="inline-between"><div className="approval-title"><span className={`row-icon ${a.status === "pending" ? "amber" : ""}`}><Icon name="inbox" size={18} /></span><div><h2>{a.title}</h2><p>{a.external_system || "External action"}, {human(a.risk_tier)} risk</p></div></div><Badge status={a.status}>{a.status === "pending" ? "needs your decision" : human(a.status)}</Badge></div>
      <p className="body-copy">{a.summary}</p>
      <div className="approval-meta">
        {a.status === "pending" ? <span>Expires <strong>{fromNow(a.expires_at)}</strong> ({date(a.expires_at)})</span> : <span>{a.decided_at ? `Decided ${date(a.decided_at)}` : `Expired ${date(a.expires_at)}`}</span>}
        <span><strong>{a.required_approvals}</strong> approval{a.required_approvals === 1 ? "" : "s"} required{a.required_role ? ` from ${human(a.required_role)}` : ""}</span>
        <span>Estimated execution cost: <strong>{a.estimated_cost_usd ? money(a.estimated_cost_usd) : "not provided"}</strong></span>
        {a.automation_id && <Link className="text-button" href={`/automations/${a.automation_id}`}>Open automation</Link>}
        {a.run_id && <Link className="text-button" href={`/runs/${a.run_id}`}>Open run</Link>}
      </div>
      {/* The exact change sits open above the decision so approval is always of something the owner can see. */}
      <details open={a.status === "pending"}><summary>What changes</summary><Diff before={a.records_before} after={a.records_after_preview} /></details>
      <details><summary>Source evidence ({Array.isArray(a.source_evidence) ? a.source_evidence.length : 0})</summary>{Array.isArray(a.source_evidence) && a.source_evidence.length ? <DataView value={a.source_evidence} /> : <p className="subtle">No source evidence was attached.</p>}</details>
      {a.status === "pending" && <form className="approval-form" onSubmit={e => { e.preventDefault(); const submitter = (e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null; void decide(a, submitter?.value || "deny", new FormData(e.currentTarget).get("reason")); }}>
        <Field label="Decision note" hint={<>Optional. Saved in the activity history.</>}><input name="reason" maxLength={2000} autoComplete="off" /></Field>
        <div className="button-row"><button name="decision" value="deny" className="button secondary" disabled={!allowed || busy === a.id}>Deny</button><button name="decision" value="approve" className="button primary" disabled={!allowed || busy === a.id}><Icon name="check" size={16} />Approve exact action</button></div>
      </form>}
    </div></Panel>) : <Panel><Empty icon="inbox" title={current === "pending" ? "Nothing waiting on you." : `No ${current} approvals`}>{current === "pending" ? "When a workflow needs your judgment, the proposed record and its evidence appear here." : "Past decisions also stay in the activity history."}</Empty></Panel>}
  </>;
}

export function Activity() {
  const { data, error, loading, load } = useResource<{ events: AuditEvent[] }>("/v1/audit/events?limit=200");
  const [check, setCheck] = useState<Row | null>(null); const [failure, setFailure] = useState<string | null>(null); const [checking, setChecking] = useState(false);
  const [category, setCategory] = useQueryState("category", "all"); const [query, setQuery] = useState("");
  const events = useMemo(() => data?.events || [], [data]);
  const categories = useMemo(() => Array.from(new Set(events.map(e => e.category))).sort(), [events]);
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = events.filter(e => (category === "all" || e.category === category) && words.every(word => `${e.event_type} ${e.actor_kind} ${e.actor_id} ${e.resource_id} ${e.category}`.toLowerCase().includes(word)));
  const days = useMemo(() => { const groups = new Map<string, AuditEvent[]>(); for (const e of rows) { const key = day(e.created_at); groups.set(key, [...(groups.get(key) || []), e]); } return Array.from(groups); }, [rows]);

  return <>
    <PageTitle title="Activity" description="An append-only record of actions, decisions and permission changes. Entries are chained, so tampering is detectable." action={<button className="button secondary" disabled={checking} onClick={async () => { setChecking(true); setFailure(null); setCheck(null); try { setCheck(await api<Row>("/v1/audit/verify")); } catch (e) { setFailure((e as Error).message); } finally { setChecking(false); } }}><Icon name="shield" size={16} />{checking ? "Verifying…" : "Verify audit chain"}</button>} />
    <ErrorBox error={error || failure} retry={error ? load : undefined} />
    {check && (check.valid ? <Success>Audit verification passed: {String(check.checked ?? events.length)} entries recomputed and the chain is intact.</Success>
      : <ErrorBox error={`Audit verification failed at entry #${String(check.broken_at_seq)} after ${String(check.checked)} valid entries. Export your data and contact your Gluons operator.`} />)}
    <div className="explore-toolbar"><div className="search-input"><Icon name="search" size={18} /><input type="search" aria-label="Search activity" placeholder="Search by event, actor or record…" value={query} onChange={e => setQuery(e.target.value)} autoComplete="off" /></div>
      <div className="chip-row" role="group" aria-label="Filter by category"><button className="chip" aria-pressed={category === "all"} onClick={() => setCategory("all")}>All</button>{categories.map(c => <button className="chip" key={c} aria-pressed={category === c} onClick={() => setCategory(c)}>{human(c)}</button>)}</div></div>
    <Panel>{loading && !data ? <SkeletonRows rows={5} /> : rows.length ? days.map(([label, items]) => <div key={label}><div className="day-heading">{label}</div><div className="activity-list">{items.map(e => <div className="activity-item" key={e.seq}><span className="activity-symbol"><Icon name={e.category === "permission" ? "shield" : e.actor_kind === "user" ? "team" : "clock"} size={15} /></span><div>
      <strong>{human(e.event_type)}</strong>
      <span className="activity-meta"><span>{time(e.created_at)}</span><span>by {human(e.actor_kind)}</span><Badge>{e.category}</Badge>{e.decision && <Badge status={e.decision === "allow" ? "approved" : e.decision === "deny" ? "denied" : ""}>{human(e.decision)}</Badge>}<span className="mono">#{e.seq}</span></span>
      <details><summary>Entry details</summary><DataView value={{ actor: e.actor_id, record: e.resource_id, recorded: e.created_at, ...(e.data && Object.keys(e.data).length ? { data: e.data } : {}) }} raw={false} /></details>
    </div></div>)}</div></div>)
      : <Empty icon="clock" title={events.length ? "No entries match" : "No activity yet"} action={events.length ? <button className="button secondary" onClick={() => { setQuery(""); setCategory("all"); }}>Clear search and filter</button> : undefined}>{events.length ? "Try another word or category." : "Entries appear as you set up and run your business."}</Empty>}</Panel>
    {events.length >= 200 && <p className="small-print">Showing the latest 200 entries. Download the full history from Your data.</p>}
  </>;
}
