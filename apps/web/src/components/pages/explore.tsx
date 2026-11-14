"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, Automation, Skill } from "@/lib/api";
import { human } from "@/lib/format";
import { useQueryState, useResource } from "@/lib/hooks";
import Icon from "../icon";
import { Badge, Empty, ErrorBox, Loading, Note, PageTitle, Panel, Field } from "../ui";

const skillIcon = (id: string) => id.includes("order") ? "box" : id.includes("voice") || id.includes("call") ? "phone" : id.includes("account") || id.includes("book") || id.includes("invoice") ? "money" : id.includes("cart") || id.includes("commerce") ? "store" : "layers";

// What each capability status lets an owner expect. Wording follows docs/SKILL_CAPABILITIES.md.
const CAPABILITY_NOTE: Record<string, string> = {
  live_verified: "Verified against a production provider.",
  sandbox_verified: "Verified against a provider sandbox, not production.",
  implemented: "Passed its evaluation suite. Not yet verified against a provider.",
  implemented_unverified: "Built, with no passing evaluation or provider evidence recorded yet.",
  blocked_by_external_dependency: "Prepares drafts for your review. Going live needs something outside Gluons first.",
};

export function Explore() {
  const { data, error, loading, load } = useResource<{ skills: Skill[] }>("/v1/skills");
  const [query, setQuery] = useQueryState("q", ""); const [industry, setIndustry] = useQueryState("industry", "all");
  const industries = useMemo(() => Array.from(new Set((data?.skills || []).map(s => s.industry).filter(Boolean))).sort(), [data]);
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = (data?.skills || []).filter(s => (industry === "all" || s.industry === industry) && words.every(word => `${s.title} ${s.industry} ${s.outcome} ${s.domain}`.toLowerCase().includes(word)));
  return <>
    <PageTitle title="Explore skills" description="Each skill is a ready-made workflow. Check what it can do, which accounts it needs and how far it has been verified before you start." />
    <div className="explore-toolbar"><div className="search-input"><Icon name="search" size={18} /><input type="search" aria-label="Search skills" placeholder="Search by problem, workflow or industry…" value={query} onChange={e => setQuery(e.target.value)} autoComplete="off" /></div>
      <select aria-label="Filter industry" value={industry} onChange={e => setIndustry(e.target.value)}><option value="all">All industries</option>{industries.map(i => <option key={i}>{i}</option>)}</select>
      <span className="subtle" aria-live="polite">{loading ? "" : `${rows.length} of ${data?.skills.length || 0} skills`}</span></div>
    <ErrorBox error={error} retry={load} />
    {loading && !data ? <Loading label="Loading skills…" /> : <div className="skill-grid">{rows.map(s => <Link href={`/explore/${s.id}`} className="skill-card" key={s.id}>
      <div className="skill-card-top"><span className="skill-icon"><Icon name={skillIcon(s.id)} size={21} /></span><span className="risk-label" title="Risk tier set by the skill contract">{human(s.risk_tier)} risk</span></div>
      <span className="skill-industry">{s.industry}</span><h2>{s.title}</h2><p>{s.outcome || `Workflow for ${String(s.industry).toLowerCase()} operations.`}</p>
      <div className="skill-card-bottom"><Badge status={s.capability.status}>{human(s.capability.status)}</Badge><Icon name="chevron" size={16} /></div></Link>)}</div>}
    {!loading && data && !rows.length && <Empty icon="search" title="No skills match" action={<button className="button secondary" onClick={() => { setQuery(""); setIndustry("all"); }}>Clear search and filter</button>}>Try a broader word, or look across all industries.</Empty>}
  </>;
}

export function SkillDetail({ id }: { id: string }) {
  const router = useRouter(); const { data: skill, error, loading, load } = useResource<Skill>(`/v1/skills/${id}`);
  const [busy, setBusy] = useState(false); const [failure, setFailure] = useState<string | null>(null);
  if (loading && !skill) return <Loading label="Loading skill…" />;
  if (!skill) return <><Link className="back-link" href="/explore">← All skills</Link><ErrorBox error={error || "This skill could not be loaded."} retry={load} /></>;
  const workflow = (skill.workflow || []).filter(step => typeof step === "string") as unknown as string[];
  return <>
    <Link className="back-link" href="/explore">← All skills</Link>
    <PageTitle eyebrow={skill.industry} title={skill.title} description={skill.outcome || "Review the workflow and what it needs."} meta={<><Badge status={skill.capability.status}>{human(skill.capability.status)}</Badge><span className="risk-label">{human(skill.risk_tier)} risk</span></>} />
    <div className="detail-grid"><div>
      <Panel title="What this skill can do"><div className="panel-body">
        <p className="body-copy">{CAPABILITY_NOTE[skill.capability.status] || "Readiness depends on verified connections and evaluation evidence."} Going live also needs verified connections and a policy you sign.</p>
        <div className="capability-list">{skill.actions?.map(a => <div key={String(a.name)}><Icon name={a.is_write ? "shield" : "check"} size={17} /><span><strong>{human(a.name)}</strong><small>{String(a.description || "The server checks permission for this action every time.")}</small></span><Badge status={a.is_write ? "pending" : ""}>{a.is_write ? "External action" : "Read only"}</Badge></div>)}</div>
        {workflow.length > 0 && <><h3 style={{ marginTop: 20 }}>How it works</h3><ol className="body-copy" style={{ paddingLeft: 20 }}>{workflow.map((step, i) => <li key={i}>{step}</li>)}</ol></>}
        {skill.limitations?.length ? <><h3 style={{ marginTop: 20 }}>Known limits</h3><ul className="plain-list">{skill.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></> : null}
      </div></Panel>
      <Panel title="Connections and prerequisites"><div className="panel-body">
        {skill.connectors.map(c => <div className="requirement" key={c.capability}><span className="row-icon"><Icon name="link" size={18} /></span><div><strong>{human(c.capability)}</strong><p>{c.purpose}</p><small>Works with {c.providers.join(", ")}</small></div><Badge status={c.required_for_live ? "pending" : ""}>{c.required_for_live ? "Required for live" : "Optional"}</Badge></div>)}
        {!skill.connectors.length && <p className="body-copy">This skill needs no connected account.</p>}
        {skill.capability.blockers.map((b, i) => <Note key={i} icon="alert">{String(b.description || b.requirement || b.name || Object.values(b).join(" · "))}</Note>)}
      </div></Panel></div>
      <Panel title="Start with a draft" className="setup-panel"><div className="panel-body">
        <p className="body-copy">A draft saves your setup. Nothing runs and no provider action is permitted until you start a trial or activate it.</p>
        <ErrorBox error={failure} />
        <form onSubmit={async e => { e.preventDefault(); setBusy(true); setFailure(null); const fields = Object.fromEntries(new FormData(e.currentTarget)); try { const result = await api<Automation>("/v1/automations", { method: "POST", idempotent: true, body: { skill_id: id, name: fields.name, monthly_budget_usd: fields.budget ? Number(fields.budget) : null, config: {} } }); router.push(`/automations/${result.id}`); } catch (err) { setFailure((err as Error).message); } finally { setBusy(false); } }}>
          <label>Automation name<input name="name" defaultValue={skill.title} maxLength={200} required autoComplete="off" /></label>
          <Field label="Monthly budget (USD)" hint={<>Optional. Chargeable work stops at this ceiling.</>}><input name="budget" type="number" inputMode="decimal" min="0" step="0.01" autoComplete="off" /></Field>
          <button className="button primary wide" disabled={busy}>{busy ? "Creating…" : "Create draft automation"}</button>
        </form>
        <p className="small-print" style={{ marginTop: 14 }}><Icon name="shield" size={14} />Shadow and replay trials perform no external writes.</p>
      </div></Panel></div>
  </>;
}
