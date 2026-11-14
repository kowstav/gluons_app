"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, Automation, Can, Row, Skill } from "@/lib/api";
import { date, human, json, money } from "@/lib/format";
import { useQueryState, useResource } from "@/lib/hooks";
import Icon from "../icon";
import { TrialEvidence } from "../operations";
import { Badge, DataView, Empty, ErrorBox, Loading, Note, PageTitle, Panel, Receipt, SkeletonRows, Success, Tabs, useConfirm, Field } from "../ui";
import { AutomationList, Stat } from "./home";

const FILTERS = ["all", "active", "trial", "paused", "cancelled"];
const inFilter = (a: Automation, filter: string) => filter === "all" || a.status === filter || (filter === "trial" && a.status.startsWith("trial_"));

export function Automations() {
  const { data, error, loading, load } = useResource<{ automations: Automation[] }>("/v1/automations");
  const [filter, setFilter] = useQueryState("filter", "all");
  const all = data?.automations || []; const rows = all.filter(a => inFilter(a, filter));
  return <>
    <PageTitle title="Automations" description="Every workflow in this business, with its current state. State lives here, not in any assistant." action={<Link href="/explore" className="button primary"><Icon name="plus" size={17} />New automation</Link>} />
    <Tabs label="Filter automations" value={FILTERS.includes(filter) ? filter : "all"} onChange={setFilter} tabs={FILTERS.map(id => ({ id, label: id === "all" ? "All" : id === "trial" ? "In trial" : human(id).replace(/^./, c => c.toUpperCase()), count: data ? all.filter(a => inFilter(a, id)).length : undefined }))} />
    <ErrorBox error={error} retry={load} />
    <Panel>{loading && !data ? <SkeletonRows rows={4} /> : rows.length ? <AutomationList rows={rows} /> : <Empty title={all.length ? "No automations in this state" : "No automations yet"} action={all.length ? <button className="button secondary" onClick={() => setFilter("all")}>Show all automations</button> : <Link href="/explore" className="button primary">Explore skills</Link>}>{all.length ? "Choose another filter to see the rest." : "Pick a skill, review what it needs and start a trial that sends nothing."}</Empty>}</Panel>
  </>;
}

const TABS = [{ id: "overview", label: "Overview" }, { id: "runs", label: "Runs" }, { id: "trials", label: "Trials" }, { id: "evidence", label: "Evidence" }, { id: "configuration", label: "Configuration" }, { id: "permissions", label: "Permissions" }];
const SETTLED = ["cancelled", "completed", "failed_terminal"];
const NOTICES: Record<string, string> = {
  "start-trial": "Shadow trial started. It reads inputs and prepares previews; nothing is sent.",
  pause: "Paused. No new runs start until you resume.",
  resume: "Resumed.",
  cancel: "Cancellation recorded. Actions already committed stay in the evidence history.",
};

export function TaskDetail({ id, can }: { id: string; can: Can }) {
  const task = useResource<Automation>(`/v1/automations/${id}`);
  const runs = useResource<{ runs: Row[] }>(`/v1/automations/${id}/runs`);
  const evidence = useResource<{ receipts: Row[]; verified_count: number; previews: number }>(`/v1/automations/${id}/evidence`);
  const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState(""); const [busy, setBusy] = useState(false);
  const [tab, setTab] = useQueryState("tab", "overview");
  const [confirm, confirmDialog] = useConfirm();
  const a = task.data;
  const settled = !a || SETTLED.includes(a.status);
  const reload = useRef(() => {});
  reload.current = () => { void task.load(); void runs.load(); void evidence.load(); };
  // The worker changes state in the background. Poll only while this tab is in view.
  useEffect(() => {
    if (settled) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") reload.current(); }, 4000);
    return () => window.clearInterval(timer);
  }, [settled]);

  async function command(action: string, body: Row = {}) {
    if (!a) return;
    if (action === "cancel" && !await confirm({ title: "Cancel this automation?", body: "It stops for good and cannot be resumed. Actions already committed stay in the evidence history.", confirmLabel: "Cancel automation", cancelLabel: "Keep it", danger: true })) return;
    setBusy(true); setError(null); setNotice("");
    try { await api(`/v1/automations/${id}/${action}`, { method: "POST", body: { expected_version: a.version, ...body } }); await Promise.all([task.load(), runs.load()]); setNotice(NOTICES[action] || `${human(action)} recorded.`); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (task.loading && !a) return <Loading label="Loading automation…" />;
  if (!a) return <><Link className="back-link" href="/automations">← All automations</Link><ErrorBox error={task.error || "This automation could not be loaded."} retry={task.load} /></>;
  const active = TABS.some(t => t.id === tab) ? tab : "overview";
  return <>
    <Link className="back-link" href="/automations">← All automations</Link>
    <PageTitle title={a.name} description={`${human(a.skill_id)} · version ${a.version}`} action={<Badge status={a.status}>{human(a.status)}</Badge>} />
    <ErrorBox error={error || task.error} retry={task.load} /><Success>{notice}</Success>
    <div className="task-controls"><Badge status={a.mode}>{human(a.mode || "draft")}</Badge><span className="subtle">Mode</span>
      <div className="control-buttons">
        {can("trials:write") && ["draft", "configuring", "ready", "awaiting_activation"].includes(a.status) && <button className="button primary" disabled={busy} onClick={() => command("start-trial", { mode: "shadow" })}><Icon name="play" size={15} />Start shadow trial</button>}
        {can("tasks:control") && !["paused", "cancelled", "completed"].includes(a.status) && <button className="button secondary" disabled={busy} onClick={() => command("pause")}><Icon name="pause" size={15} />Pause</button>}
        {can("tasks:control") && a.status === "paused" && <button className="button primary" disabled={busy} onClick={() => command("resume")}><Icon name="play" size={15} />Resume</button>}
        {can("tasks:control") && a.status !== "cancelled" && <button className="button danger-outline" disabled={busy} onClick={() => command("cancel")}>Cancel automation</button>}
      </div></div>
    <Tabs label="Automation sections" value={active} onChange={setTab} tabs={TABS.map(t => t.id === "runs" ? { ...t, count: runs.data?.runs.length } : t.id === "evidence" ? { ...t, count: evidence.data?.receipts.length } : t)} />

    {active === "overview" && <div className="detail-grid">
      <Panel title="At a glance"><div className="panel-body">
        <dl className="facts"><div><dt>Skill</dt><dd><Link href={`/explore/${a.skill_id}`}>{human(a.skill_id)}</Link></dd></div><div><dt>Created</dt><dd>{date(a.created_at)}</dd></div><div><dt>Monthly budget</dt><dd>{a.monthly_budget_usd ? money(a.monthly_budget_usd) : "No ceiling set"}</dd></div><div><dt>Receipts verified by readback</dt><dd className="num">{evidence.data?.verified_count ?? "—"}</dd></div><div className="span-two"><dt>Automation ID</dt><dd className="mono">{a.id}</dd></div></dl>
        <Note>External writes need a verified provider connection and a policy you have signed. Shadow and replay only produce previews.</Note>
      </div></Panel>
      <Panel title="Try an input"><div className="panel-body">
        <p className="body-copy">Send one test event to the running trial. The worker processes it with this skill’s workflow.</p>
        <form onSubmit={async e => { e.preventDefault(); const f = new FormData(e.currentTarget); setError(null); setNotice(""); try { const payload = JSON.parse(String(f.get("payload"))); await api(`/v1/automations/${id}/events`, { method: "POST", body: { source: "manual", source_event_id: crypto.randomUUID(), payload } }); setNotice("Input queued. It appears under Runs once the worker picks it up."); await runs.load(); } catch (err) { setError(err instanceof SyntaxError ? "The input is not valid JSON. Check quotes, commas and braces." : (err as Error).message); } }}>
          <label>Skill input (JSON)<textarea className="mono" name="payload" rows={7} defaultValue={'{\n  "text": "Your test input"\n}'} required spellCheck={false} autoComplete="off" /></label>
          <button className="button secondary wide" disabled={!can("trials:write")}>Send test input</button>
        </form></div></Panel>
    </div>}

    {active === "runs" && <Panel title="Run history" meta={<button className="text-button" onClick={runs.load}><Icon name="refresh" size={15} />Refresh</button>}><ErrorBox error={runs.error} retry={runs.load} />
      {runs.data?.runs.length ? <div className="table-wrap"><table><thead><tr><th>Run</th><th>Status</th><th>Source and mode</th><th className="num">Recorded cost</th><th>Created</th></tr></thead><tbody>{runs.data.runs.map(r => <tr key={String(r.id)}><td className="mono"><Link className="text-button" href={`/runs/${r.id}`}>{String(r.id)}</Link></td><td><Badge status={String(r.status)}>{human(r.status)}</Badge></td><td>{human(r.source)}, {human(r.mode)}</td><td className="num">{money(r.cost_usd)}</td><td>{date(r.created_at)}</td></tr>)}</tbody></table></div>
        : runs.loading ? <SkeletonRows /> : <Empty icon="clock" title="No runs yet">Start a trial and send a test input. Each run is listed here with its cost.</Empty>}</Panel>}

    {active === "evidence" && <Panel title="External action evidence"><div className="panel-body"><ErrorBox error={evidence.error} retry={evidence.load} />
      {evidence.data?.receipts.length ? evidence.data.receipts.map(r => <Receipt record={r} key={String(r.id)} />) : <Empty icon="shield" title="No receipts yet">A receipt appears when this automation acts on a provider. It is marked verified only after an independent readback confirms the record.</Empty>}</div></Panel>}

    {active === "configuration" && <Panel title="Workflow configuration"><div className="panel-body narrow">
      <form key={a.version} onSubmit={async e => { e.preventDefault(); setError(null); setNotice(""); try { const config = JSON.parse(String(new FormData(e.currentTarget).get("config"))); await api(`/v1/automations/${id}`, { method: "PATCH", body: { config, expected_version: a.version } }); setNotice("Configuration saved."); await task.load(); } catch (err) { setError(err instanceof SyntaxError ? "The configuration is not valid JSON. Check quotes, commas and braces." : (err as Error).message); } }}>
        <Field label="Skill configuration (JSON)" hint={<>Settings this skill reads on each run. Permissions are set under Permissions, never here.</>}><textarea className="mono" rows={16} name="config" defaultValue={json(a.config)} spellCheck={false} autoComplete="off" /></Field>
        <button className="button primary" disabled={!can("automations:write")}>Save configuration</button>
      </form></div></Panel>}

    {active === "trials" && <TrialStudio automation={a} can={can} onChange={task.load} />}
    {active === "permissions" && <Policies automation={a} can={can} onChange={task.load} notify={setNotice} fail={setError} />}
    {confirmDialog}
  </>;
}

function Policies({ automation, can, onChange, notify, fail }: { automation: Automation; can: Can; onChange: () => Promise<void>; notify: (text: string) => void; fail: (text: string | null) => void }) {
  const { data, error, load } = useResource<{ policies: Row[] }>(`/v1/automations/${automation.id}/policies`);
  const skill = useResource<Skill>(`/v1/skills/${automation.skill_id}`);
  const connections = useResource<{ connections: Row[] }>("/v1/connections");
  const [confirm, confirmDialog] = useConfirm();
  const writes = (skill.data?.actions || []).filter(a => a.is_write).map(a => String(a.name));
  async function act(fn: () => Promise<unknown>, done: string) { fail(null); notify(""); try { await fn(); await load(); await onChange(); notify(done); } catch (e) { fail((e as Error).message); } }

  return <div className="detail-grid">
    <Panel title="Policies for this automation"><div className="panel-body"><ErrorBox error={error} retry={load} />
      {data?.policies.length ? data.policies.map(p => <div className="receipt" key={String(p.id)}>
        <div className="receipt-head"><strong>Policy version {String(p.policy_version)}</strong><Badge status={String(p.status)}>{human(p.status)}</Badge></div>
        <DataView value={{ mode: p.mode, allowed_actions: p.allowed_actions, requires_approval_for: p.requires_approval_for, connections: p.connection_ids, restrictions: p.resource_filters, per_action_limit: typeof p.per_action_limit_minor === "number" ? `${(p.per_action_limit_minor / 100).toFixed(2)} ${p.currency || "USD"}` : null, monthly_spend_limit: typeof p.spend_limit_minor_per_month === "number" ? `${(p.spend_limit_minor_per_month / 100).toFixed(2)} ${p.currency || "USD"}` : null, automatic_limits: p.auto_limits, data_disclosures: p.data_disclosures, expires_at: p.expires_at }} raw={false} />
        <details className="data-raw"><summary>Exact record</summary><pre className="json-view">{json(p)}</pre></details>
        <div className="button-row" style={{ marginTop: 12 }}>
          {can("policies:sign") && p.status === "proposed" && <button className="button primary" onClick={async () => { if (await confirm({ title: `Sign policy version ${p.policy_version}?`, body: "Your signature permits exactly the actions, recipients and limits listed in this policy. You can revoke it at any time.", confirmLabel: "Sign this exact policy", cancelLabel: "Not yet" })) await act(() => api(`/v1/automation-policies/${p.id}/sign`, { method: "POST" }), "Policy signed. It now sets what this automation may do."); }}>Sign this exact policy</button>}
          {can("policies:write") && p.status !== "revoked" && <button className="button danger-outline" onClick={() => act(() => api(`/v1/automation-policies/${p.id}/revoke`, { method: "POST" }), "Policy revoked. This automation can no longer act under it.")}>Revoke policy</button>}
        </div></div>)
        : <Empty icon="shield" title="No policy yet">Connecting an account does not let this automation act. Propose a policy, then sign it.</Empty>}
    </div></Panel>
    <div>
      <Panel title="Propose a policy"><div className="panel-body">
        <form onSubmit={async e => { e.preventDefault(); const form = e.currentTarget; const data = new FormData(form); const fields = Object.fromEntries(data);
          const actions = String(fields.actions).split(",").map(s => s.trim()).filter(Boolean); const recipient = String(fields.recipient || "").trim(); const expiry = String(fields.expires || "");
          if (recipient && actions.length !== 1) { fail("A recipient restriction needs exactly one action. Remove the other actions or the recipient."); return; }
          await act(async () => { await api(`/v1/automation-policies?automation_id=${automation.id}`, { method: "POST", body: { mode: fields.mode, allowed_actions: actions, connection_ids: data.getAll("connection").map(String), resource_filters: recipient ? { [actions[0]]: { allowed_recipients: [recipient] } } : {}, requires_approval_for: actions, per_action_limit_minor: fields.limit ? Math.round(Number(fields.limit) * 100) : null, currency: "USD", expires_at: expiry ? new Date(expiry).toISOString() : null } }); form.reset(); }, "Policy proposed. Review and sign it before activation."); }}>
          <label>Execution mode<select name="mode"><option value="supervised">Supervised: you approve every action</option><option value="live">Live: acts within the signed policy</option></select></label>
          <Field label="Allowed action names" hint={<>Separate several with commas.{writes.length > 0 && ` This skill’s external actions: ${writes.join(", ")}.`}</>}><input name="actions" list="policy-actions" placeholder="erp.order.create" required autoComplete="off" spellCheck={false} /></Field>
          <datalist id="policy-actions">{writes.map(name => <option key={name} value={name} />)}</datalist>
          <fieldset><legend>Accounts this policy may use</legend>
            {connections.data?.connections.length ? connections.data.connections.map(c => <label className="checkbox-label" key={String(c.id)}><input type="checkbox" name="connection" value={String(c.id)} /><span>{String(c.account_display || c.provider)} <span className="subtle">({human(c.provider)}, {human(c.status)})</span></span></label>)
              : <p className="small-print">No accounts are connected. <Link href="/connections">Connect one</Link> if this policy needs it.</p>}
          </fieldset>
          <label>Only this email recipient (optional)<input name="recipient" type="email" autoComplete="off" spellCheck={false} /></label>
          <label>Policy expiration (optional)<input name="expires" type="datetime-local" /></label>
          <label>Per-action amount ceiling (USD)<input name="limit" type="number" inputMode="decimal" min="0" step="0.01" autoComplete="off" /></label>
          <p className="small-print">Every listed action will still wait for a human approval. Proposing changes nothing until you sign.</p>
          <button className="button secondary wide" disabled={!can("policies:write")}>Propose policy</button>
        </form></div></Panel>
      <Panel title="Activate"><div className="panel-body">
        <p className="body-copy">After a passing trial and a signed policy, supervised mode lets this automation prepare real actions. Each one still waits in your approval inbox.</p>
        <button className="button primary" disabled={!can("automations:activate")} onClick={async () => { if (await confirm({ title: "Activate supervised mode?", body: "The automation starts working on real inputs. Every external action waits for approval, within the signed policy.", confirmLabel: "Activate supervised mode", cancelLabel: "Not yet" })) await act(() => api(`/v1/automations/${automation.id}/activate`, { method: "POST", body: { mode: "supervised", expected_version: automation.version } }), "Supervised mode is on. Actions will wait in your approval inbox."); }}>Activate supervised mode</button>
      </div></Panel>
    </div>
    {confirmDialog}
  </div>;
}

function TrialStudio({ automation, can, onChange }: { automation: Automation; can: Can; onChange: () => Promise<void> }) {
  const trials = useResource<{ trials: Row[] }>("/v1/trials");
  const [report, setReport] = useState<Row | null>(null); const [error, setError] = useState<string | null>(null);
  const rows = trials.data?.trials.filter(t => t.automation_id === automation.id) || [];
  const measured = report?.measured as Row | undefined;
  return <>
    <div className="detail-grid">
      <Panel title="Trials"><div className="panel-body">
        <p className="body-copy">A trial compares what the automation would do against outcomes you expect. Finishing a trial is not passing it: the comparison must meet the server’s quality bar.</p>
        <ErrorBox error={error || trials.error} retry={trials.load} />
        {rows.length ? rows.map(t => <div className="member-row" key={String(t.id)}><span className="row-icon"><Icon name="spark" size={18} /></span><div className="grow"><strong>{human(t.mode)}</strong><small>Started {date(t.created_at)}</small></div><Badge status={String(t.status)}>{human(t.status)}</Badge>
          <button className="button secondary compact" onClick={async () => { setError(null); try { setReport(await api<Row>(`/v1/trials/${t.id}/results`)); } catch (e) { setError((e as Error).message); } }}>View report</button>
          {t.status !== "completed" && can("trials:write") && <button className="button secondary compact" onClick={async () => { setError(null); try { setReport(await api<Row>(`/v1/trials/${t.id}/complete`, { method: "POST" })); await trials.load(); await onChange(); } catch (e) { setError((e as Error).message); } }}>Complete trial</button>}</div>)
          : trials.loading ? <SkeletonRows rows={2} /> : <Empty icon="spark" title="No trials yet">Shadow and replay trials read inputs and prepare previews. They never write to a provider.</Empty>}
      </div></Panel>
      <Panel title="Start a trial with expected outcomes"><div className="panel-body">
        <form onSubmit={async e => { e.preventDefault(); setError(null); const f = new FormData(e.currentTarget); try { await api(`/v1/automations/${automation.id}/start-trial`, { method: "POST", body: { expected_version: automation.version, mode: f.get("mode"), comparator: { expected_outcomes: JSON.parse(String(f.get("outcomes"))), minimum_cases: Number(f.get("minimum")) }, baseline: { method: f.get("baseline") }, budget_usd: f.get("budget") ? Number(f.get("budget")) : null } }); await trials.load(); await onChange(); } catch (err) { setError(err instanceof SyntaxError ? "Expected outcomes must be valid JSON: an object keyed by source event ID." : (err as Error).message); } }}>
          <label>Trial mode<select name="mode"><option value="shadow">Shadow: process new inputs, send nothing</option><option value="historical_replay">Historical replay: evaluate past inputs</option></select></label>
          <label>Minimum labelled cases<input name="minimum" type="number" inputMode="numeric" min="1" max="10000" defaultValue="10" required /></label>
          <label>Expected outcomes by source event ID (JSON)<textarea className="mono" name="outcomes" rows={6} defaultValue="{}" spellCheck={false} required autoComplete="off" /></label>
          <Field label="Baseline method" hint={<>How you measured the current, manual way of doing this.</>}><input name="baseline" autoComplete="off" /></Field>
          <label>Trial budget (USD)<input name="budget" type="number" inputMode="decimal" min="0" step="0.01" autoComplete="off" /></label>
          <button className="button primary wide" disabled={!can("trials:write")}>Start trial</button>
        </form></div></Panel>
    </div>
    {report && <Panel title="Trial report" meta={<Badge status={report.passed ? "verified" : "pending"}>{report.passed ? "Comparison passed" : "More evidence needed"}</Badge>}><div className="panel-body">
      <p className="subtle">{human(report.mode)} trial, {human(report.status)}</p>
      <div className="stat-grid three"><Stat label="Measured runs" value={String(measured?.runs ?? "—")} icon="layers" note={`${measured?.completed || 0} completed`} /><Stat label="Labelled comparisons" value={String(measured?.labelled_cases ?? "—")} icon="check" note={`${measured?.correct_cases || 0} correct`} /><Stat label="External writes" value={String(measured?.external_writes ?? "—")} icon="shield" note="Must be zero in shadow and replay" /></div>
      <p className="body-copy">{String(report.quality_basis ?? "")}</p>
      {Array.isArray(report.missing_evidence) && report.missing_evidence.map((item, i) => <Note key={i} icon="alert">{String(item)}</Note>)}
      <details><summary>Full recorded report</summary><DataView value={report} /></details>
    </div></Panel>}
    <TrialEvidence automationId={automation.id} version={automation.version} status={String(automation.status)} mode={String(automation.mode)} trials={rows} can={can} onChange={async () => { await trials.load(); await onChange(); }} />
  </>;
}

export function RunDetail({ id }: { id: string }) {
  const { data, error, loading, load } = useResource<Row>(`/v1/runs/${id}`);
  const [diagnosis, setDiagnosis] = useState<Row | null>(null); const [failure, setFailure] = useState<string | null>(null);
  if (loading && !data) return <Loading label="Loading run…" />;
  if (!data) return <><Link className="back-link" href="/automations">← All automations</Link><ErrorBox error={error || "This run could not be loaded."} retry={load} /></>;
  const events = Array.isArray(data.events) ? data.events as Row[] : []; const receipts = Array.isArray(data.receipts) ? data.receipts as Row[] : [];
  return <>
    <Link className="back-link" href={`/automations/${data.automation_id}?tab=runs`}>← Back to automation</Link>
    <PageTitle title="Run detail" description={`${human(data.source)} input, ${human(data.mode)} mode`} action={<Badge status={String(data.status)}>{human(data.status)}</Badge>} meta={<span className="mono">{id}</span>} />
    <ErrorBox error={failure} />
    <div className="detail-grid">
      <Panel title="Timeline" meta={<button className="text-button" onClick={load}><Icon name="refresh" size={15} />Refresh</button>}><div className="panel-body">
        {events.length ? events.map(e => <div className="activity-item" key={String(e.seq)}><span className="activity-symbol"><Icon name="clock" size={15} /></span><div><strong>{human(e.type)}</strong><small>{date(e.at)}</small>{e.data && Object.keys(e.data as Row).length > 0 ? <details><summary>Event details</summary><DataView value={e.data} /></details> : null}</div></div>) : <Empty icon="clock" title="No events yet">Refresh after the worker has processed this run.</Empty>}
      </div></Panel>
      <Panel title="Outcome"><div className="panel-body">
        <dl className="facts"><div><dt>Recorded cost</dt><dd className="num">{money(data.cost_usd)}</dd></div><div><dt>Started</dt><dd>{date(data.started_at)}</dd></div><div><dt>Finished</dt><dd>{date(data.finished_at)}</dd></div></dl>
        {data.outcome ? <DataView value={data.outcome} /> : <p className="subtle">No outcome recorded yet.</p>}
        <button className="button secondary" style={{ marginTop: 16 }} onClick={async () => { setFailure(null); try { setDiagnosis(await api<Row>(`/v1/runs/${id}/diagnosis`)); } catch (e) { setFailure((e as Error).message); } }}>View diagnosis</button>
        {diagnosis && <div style={{ marginTop: 16 }}><h3>Diagnosis</h3><DataView value={diagnosis} /></div>}
      </div></Panel>
    </div>
    <Panel title="Receipts"><div className="panel-body">{receipts.length ? receipts.map(r => <Receipt record={r} key={String(r.id)} />) : <Empty icon="shield" title="No external receipts">This run wrote nothing to a provider. Shadow results are previews, and a model’s answer is never proof of a transaction.</Empty>}</div></Panel>
  </>;
}
