"use client";

import Link from "next/link";
import { Approval, AuditEvent, Automation, Can, Organization, Row, Session, Spending } from "@/lib/api";
import { fromNow, human, money } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import Icon from "../icon";
import { Badge, Empty, ErrorBox, Meter, PageTitle, Panel, SkeletonRows } from "../ui";

export function Stat({ label, value, icon, note, accent, href, children }: { label: string; value: string; icon: string; note: string; accent?: boolean; href?: string; children?: React.ReactNode }) {
  const body = <><div className="stat-top"><span>{label}</span><Icon name={icon} size={17} /></div><strong>{value}</strong>{children}<small>{note}</small></>;
  return href ? <Link className={`stat ${accent ? "stat-accent" : ""}`} href={href}>{body}</Link> : <div className={`stat ${accent ? "stat-accent" : ""}`}>{body}</div>;
}

export function AutomationList({ rows }: { rows: Automation[] }) {
  return <div className="automation-list">{rows.map(a => <Link className="automation-row" href={`/automations/${a.id}`} key={a.id}><span className="row-icon"><Icon name="layers" size={18} /></span><div className="automation-name"><strong>{a.name}</strong><small>{human(a.skill_id)}</small></div><Badge status={a.status}>{human(a.status)}</Badge><span className="mode-label">{human(a.mode)}</span><Icon name="chevron" size={16} /></Link>)}</div>;
}

export default function Home({ session, org, can }: { session: Session; org: Organization | null; can: Can }) {
  const tasks = useResource<{ automations: Automation[] }>("/v1/automations");
  const approvals = useResource<{ approvals: Approval[] }>("/v1/approvals?status=pending");
  const spending = useResource<Spending>("/v1/billing/usage");
  const activity = useResource<{ events: AuditEvent[] }>("/v1/audit/events?limit=6");
  const connections = useResource<{ connections: Row[] }>("/v1/connections");
  const rows = tasks.data?.automations || [];
  const waiting = approvals.data?.approvals || [];
  const running = rows.filter(a => a.status === "active").length;
  const trials = rows.filter(a => a.status.startsWith("trial_")).length;
  const used = Number(spending.data?.total_usd || 0), budget = Number(spending.data?.monthly_budget_usd || 0);
  // Each step is read from workspace records, so the list never claims progress that did not happen.
  const steps = [
    { done: (connections.data?.connections.length || 0) > 0, title: "Connect an account", text: "Sign in with the provider. A connection alone grants no permission to act.", href: "/connections", action: "Open connections" },
    { done: rows.length > 0, title: "Choose a skill", text: "Pick a workflow and review what it needs before it can run.", href: "/explore", action: "Explore skills" },
    { done: trials > 0 || running > 0, title: "Run a shadow trial", text: "See what the automation would do with real inputs. Nothing is sent.", href: "/automations", action: "Open automations" },
    { done: running > 0, title: "Sign a policy and activate", text: "Set the exact actions, recipients and limits, then switch it on.", href: "/automations", action: "Review permissions" },
  ];
  const onboarding = !tasks.loading && !connections.loading && steps.some(step => !step.done);

  return <>
    <PageTitle title="Business Pulse" description={`Welcome back, ${session.user.display_name.split(" ")[0]}. Here is where ${org?.name} stands.`} action={can("automations:write") ? <Link className="button primary" href="/explore"><Icon name="plus" size={17} />New automation</Link> : undefined} />
    <ErrorBox error={tasks.error} retry={tasks.load} />

    <Panel title="Waiting on you" meta={waiting.length > 0 ? <Link href="/approvals" className="text-button">Open approval inbox</Link> : undefined}>
      {approvals.loading && !approvals.data ? <SkeletonRows rows={2} /> : waiting.length ? <div className="needs-you">{waiting.slice(0, 4).map(a => <Link className="needs-you-item" href="/approvals" key={a.id}><span className="row-icon amber"><Icon name="inbox" size={18} /></span><div><strong>{a.title}</strong><small>{a.external_system || "External action"} · expires {fromNow(a.expires_at)}</small></div><Badge status="pending">needs your decision</Badge></Link>)}</div>
        : <div className="calm"><Icon name="check" size={20} /><span>{approvals.error ? "Approvals could not be loaded for your role." : "No decisions are waiting. An approval appears here the moment a workflow needs you."}</span></div>}
    </Panel>

    <div className="stat-grid">
      <Stat label="Active automations" value={tasks.loading && !tasks.data ? "—" : String(running)} icon="layers" note={`${rows.length} in this business`} href="/automations" />
      <Stat label="In trial" value={tasks.loading && !tasks.data ? "—" : String(trials)} icon="spark" note="Trials send nothing externally" href="/automations?filter=trial" />
      <Stat label="Waiting for approval" value={approvals.data ? String(waiting.length) : "—"} icon="inbox" note="Decisions only you can make" accent={waiting.length > 0} href="/approvals" />
      <Stat label="Recorded usage this month" value={spending.data ? money(used) : "—"} icon="money" note={budget ? `of ${money(budget)} monthly budget` : "No monthly budget set"} href="/costs">{budget > 0 && <Meter value={used} max={budget} label="Share of monthly budget used" />}</Stat>
    </div>

    <div className="home-bottom">
      <div>
        {onboarding && <Panel title="Path to your first verified action"><ol className="steps">{steps.map(step => <li key={step.title} className={step.done ? "done" : ""}><div><strong>{step.title}</strong>{step.done && <span className="visually-hidden"> (done)</span>}<p>{step.text}</p>{!step.done && <Link className="text-button" href={step.href}>{step.action}</Link>}</div></li>)}</ol></Panel>}
        <Panel title="Your automations" meta={rows.length > 0 ? <Link href="/automations" className="text-button">View all</Link> : undefined}>
          {tasks.loading && !tasks.data ? <SkeletonRows /> : rows.length ? <AutomationList rows={rows.slice(0, 5)} /> : <Empty icon="layers" title="No automations yet" action={<Link className="button secondary" href="/explore">Explore skills</Link>}>Start from a skill. It stays a draft until you run a trial or activate it.</Empty>}
        </Panel>
      </div>
      <Panel title="Recent activity" meta={<Link className="text-button" href="/activity">View history</Link>}>
        {activity.loading && !activity.data ? <SkeletonRows /> : activity.data?.events.length ? <div className="activity-list">{activity.data.events.map(e => <div className="activity-item" key={e.seq}><span className="activity-symbol"><Icon name={e.category === "permission" ? "shield" : "clock"} size={15} /></span><div><strong>{human(e.event_type)}</strong><small>{fromNow(e.created_at)} · {human(e.actor_kind)}</small></div></div>)}</div>
          : <Empty icon="clock" title="No activity yet">Every action, decision and permission change is recorded here as it happens.</Empty>}
      </Panel>
    </div>
  </>;
}
