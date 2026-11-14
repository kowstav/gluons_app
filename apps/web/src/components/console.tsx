"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, Approval, Automation, Can, Organization, Row, Session, SIGNED_OUT_EVENT, Skill } from "@/lib/api";
import { human } from "@/lib/format";
import Icon, { Brand } from "./icon";
import { Empty, ErrorBox, Loading, useConfirm } from "./ui";
import { Auth, OrganizationSelect } from "./auth";
import { ThemeButton } from "./theme";
import SetupRoom from "./setup-room";
import { AnomalyPanel, AssistedSetup, BusinessLaunch, ChannelOperations, ChannelRecords, DataRights, NativeAssistant, ShopifyClaim } from "./operations";
import Home from "./pages/home";
import { Automations, RunDetail, TaskDetail } from "./pages/automations";
import { Explore, SkillDetail } from "./pages/explore";
import { Activity, Approvals } from "./pages/oversight";
import { AcceptInvite, AssistantConsent, Connections, Costs, Knowledge, Security } from "./pages/workspace";

type NavItem = readonly [path: string, label: string, icon: string];
// Grouped by the owner's job: run the business, build automations, connect accounts, govern.
const NAV: { label: string; items: NavItem[] }[] = [
  { label: "Run", items: [["/", "Business Pulse", "pulse"], ["/approvals", "Approval inbox", "inbox"], ["/automations", "Automations", "layers"], ["/activity", "Activity", "clock"]] },
  { label: "Build", items: [["/explore", "Explore skills", "grid"], ["/business-launch", "Business launch", "flag"], ["/assistant", "Gluons assistant", "spark"]] },
  { label: "Connect", items: [["/business-setup", "Business setup", "book"], ["/connections", "Connections", "link"], ["/channels", "Business channels", "message"], ["/knowledge", "Business knowledge", "box"]] },
  { label: "Govern", items: [["/costs", "Costs & insights", "chart"], ["/security", "Team & security", "team"], ["/data", "Your data", "database"], ["/assisted-setup", "Assisted setup", "help"]] },
];
const ALL_NAV = NAV.flatMap(group => group.items);
// Detail routes that belong to a navigation entry without sharing its prefix.
const ALIASES: Record<string, string> = { "/tasks": "/automations", "/runs": "/automations", "/audit": "/activity", "/setup": "/connections", "/connect": "/connections" };
const AUTH_PAGES = ["/login", "/register", "/verify-email", "/reset-password", "/forgot-password"];

function activeNav(pathname: string): NavItem | undefined {
  const first = `/${pathname.split("/")[1] || ""}`;
  const target = ALIASES[first] || first;
  return ALL_NAV.find(([path]) => path === target);
}

export default function Console() {
  const pathname = usePathname(); const router = useRouter();
  const [session, setSession] = useState<Session | null>(null); const [org, setOrg] = useState<Organization | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null);
  const [mobile, setMobile] = useState(false); const [palette, setPalette] = useState(false);
  const [notifications, setNotifications] = useState<Row[]>([]); const [showNotifications, setShowNotifications] = useState(false);
  const [pending, setPending] = useState(0);
  const [confirm, confirmDialog] = useConfirm();
  const signedIn = useRef(false);

  const loadSession = useCallback(async () => {
    setError(null);
    try {
      const value = await api<Session>("/v1/auth/session"); setSession(value); signedIn.current = true;
      if (value.active_organization_id && !value.mfa_pending) setOrg(await api<Organization>("/v1/organizations/current")); else setOrg(null);
    } catch (e) { signedIn.current = false; setSession(null); setOrg(null); if (!(e instanceof ApiError && e.status === 401)) setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void loadSession(); }, [loadSession]);

  // A session that ends mid-work returns the owner to sign-in with an explanation.
  useEffect(() => {
    const ended = () => { if (!signedIn.current) return; signedIn.current = false; setSession(null); setOrg(null); setError("You were signed out. Sign in again to continue where you left off."); };
    window.addEventListener(SIGNED_OUT_EVENT, ended);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, ended);
  }, []);

  const ready = Boolean(session && org && !session.mfa_pending);
  const orgId = org?.id;
  // Refreshed on navigation: cheap, and decisions made on one screen show up in the sidebar count.
  useEffect(() => {
    if (!ready) return;
    let current = true;
    api<{ approvals: Approval[] }>("/v1/approvals?status=pending").then(r => { if (current) setPending(r.approvals.length); }).catch(() => { if (current) setPending(0); });
    api<{ notifications: Row[] }>("/v1/notifications").then(r => { if (current) setNotifications(r.notifications); }).catch(() => {});
    return () => { current = false; };
  }, [ready, orgId, pathname]);

  useEffect(() => { setMobile(false); setShowNotifications(false); }, [pathname]);
  const active = activeNav(pathname);
  useEffect(() => { document.title = active && ready ? `${active[1]} · Gluons` : "Gluons"; }, [active, ready]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); if (ready) setPalette(open => !open); }
      if (event.key === "Escape") { setShowNotifications(false); setMobile(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready]);

  const authPage = AUTH_PAGES.includes(pathname);
  if (loading) return <main className="startup on-surface"><Brand /><Loading label="Opening your workspace…" /></main>;
  if (!session || authPage || session.mfa_pending) return <Auth path={session?.mfa_pending ? "/mfa" : pathname} initialError={error} onSignedIn={async () => { await loadSession(); if (authPage || pathname === "/mfa") router.push("/"); }} />;
  if (!session.active_organization_id || pathname === "/org/select") return <OrganizationSelect session={session} onDone={async () => { await loadSession(); if (pathname === "/org/select") router.push("/"); }} />;

  const can: Can = permission => org?.permissions?.includes(permission) || false;
  const unread = notifications.filter(n => !n.read).length;
  const stopped = Boolean(org?.emergency_stop_at);
  async function signOut() { try { await api("/v1/auth/logout", { method: "POST" }); } catch { /* The local session is cleared either way. */ } signedIn.current = false; setSession(null); router.push("/login"); }
  async function releaseStop() {
    if (!await confirm({ title: "Allow external actions again?", body: `Automations for ${org?.name} will be able to act within their signed policies again.`, confirmLabel: "Release emergency stop" })) return;
    try { await api("/v1/organizations/current/emergency-stop", { method: "POST", body: { active: false } }); await loadSession(); } catch (e) { setError((e as Error).message); }
  }

  return <div className="app-shell"><a href="#main-content" className="skip-link">Skip to main content</a>
    {mobile && <button className="sidebar-overlay" aria-label="Close navigation" onClick={() => setMobile(false)} />}
    <aside className={`sidebar ${mobile ? "is-open" : ""}`}><Link href="/" aria-label="Gluons home"><Brand /></Link>
      <button className="workspace-selector" onClick={() => router.push("/org/select")} title="Switch or add a business"><span className="workspace-avatar">{org?.name?.[0] || "G"}</span><span><strong>{org?.name || "My business"}</strong><small>{human(org?.role)} · switch business</small></span><Icon name="down" size={16} /></button>
      <nav aria-label="Main navigation">{NAV.map(group => <div className="nav-group" key={group.label}><div className="nav-label" id={`nav-${group.label}`}>{group.label}</div>
        {group.items.map(([path, label, icon]) => <Link key={path} href={path} aria-label={label} className={`nav-item ${active?.[0] === path ? "active" : ""}`} aria-current={active?.[0] === path ? "page" : undefined}><Icon name={icon} size={18} /><span>{label}</span>{path === "/approvals" && pending > 0 && <span className="nav-count" title={`${pending} waiting for your decision`}>{pending}</span>}</Link>)}</div>)}</nav>
      <div className="sidebar-bottom">
        <Link href="/security" className={`control-state ${stopped ? "stopped" : ""}`}><Icon name={stopped ? "stop" : "shield"} size={18} /><span><strong>{stopped ? "Emergency stop is on" : "External actions allowed"}</strong><span>{stopped ? "New external actions are blocked." : "Within signed policies and budgets."}</span></span></Link>
        <div className="sidebar-user"><span className="user-avatar">{session.user.display_name?.[0]}</span><span><strong>{session.user.display_name}</strong><small>{session.user.email}</small></span><button className="icon-button" aria-label="Sign out" title="Sign out" onClick={signOut}><Icon name="logout" size={17} /></button></div>
      </div>
    </aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="Open navigation" aria-expanded={mobile} onClick={() => setMobile(true)}><Icon name="menu" /></button><div className="crumb-text"><span>{org?.name}</span><span className="slash">/</span><strong>{active?.[1] || "Workspace"}</strong></div></div>
      <div className="top-actions">
        <span className={`env-label ${session.environment === "production" ? "production" : ""}`} title="The environment this Gluons server reports">{human(session.environment)} environment</span>
        <button className="jump-button" onClick={() => setPalette(true)} aria-label="Search and jump to a page" aria-keyshortcuts="Control+K Meta+K"><Icon name="search" size={16} /><span>Jump to…</span><span className="kbd">Ctrl K</span></button>
        <ThemeButton />
        <button className="icon-button" aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"} aria-expanded={showNotifications} onClick={() => setShowNotifications(open => !open)}><Icon name="bell" size={18} />{unread > 0 && <span className="unread-dot" />}</button>
      </div></header>
      {showNotifications && <Notifications rows={notifications} onChange={setNotifications} onClose={() => setShowNotifications(false)} />}
      <main id="main-content" tabIndex={-1}><ErrorBox error={error} />
        {stopped && <div className="emergency-banner" role="alert"><Icon name="stop" /><span><strong>Emergency stop is on.</strong> New external actions are blocked for this business. Actions already committed stay in the activity history.</span>{can("emergency:stop") && <button onClick={releaseStop}>Release stop</button>}</div>}
        <Screen pathname={pathname} session={session} org={org} can={can} reload={loadSession} />
      </main>
    </div>
    {palette && <CommandPalette onClose={() => setPalette(false)} />}
    {confirmDialog}
  </div>;
}

/** Maps the address to a screen. Unknown paths say so instead of rendering an empty page. */
function Screen({ pathname, session, org, can, reload }: { pathname: string; session: Session; org: Organization | null; can: Can; reload: () => Promise<void> }) {
  const [, root, id] = pathname.split("/");
  const name = String(org?.name ?? "");
  switch (root || "") {
    case "": return <Home session={session} org={org} can={can} />;
    case "explore": return id ? <SkillDetail id={id} /> : <Explore />;
    case "automations": case "tasks": return id ? <TaskDetail id={id} can={can} /> : <Automations />;
    case "runs": return id ? <RunDetail id={id} /> : <Automations />;
    case "approvals": return <Approvals can={can} />;
    case "activity": case "audit": return <Activity />;
    case "connections": case "setup": return <Connections path={pathname} can={can} />;
    case "connect": return id === "shopify" ? <ShopifyClaim id={pathname.split("/")[3]} organization={name} /> : null;
    case "knowledge": return <Knowledge can={can} />;
    case "costs": return <><Costs org={org} can={can} onUpdate={reload} /><AnomalyPanel /></>;
    case "security": return <Security session={session} org={org} can={can} onUpdate={reload} />;
    case "business-launch": return <BusinessLaunch can={can} />;
    case "business-setup": return <SetupRoom key={org?.id || "none"} organizationId={String(org?.id || "")} organizationName={name} isOwner={org?.role === "owner" && can("org:manage")} onOrganizationUpdated={reload} />;
    case "channels": return <><ChannelOperations can={can} /><ChannelRecords /></>;
    case "data": return <DataRights can={can} organization={name} />;
    case "assistant": return <NativeAssistant />;
    case "assisted-setup": return <AssistedSetup can={can} />;
    case "assistant-consent": return <AssistantConsent />;
    case "invite": return <AcceptInvite onDone={reload} />;
    default: return <Empty icon="search" title="This page does not exist" action={<Link className="button primary" href="/">Go to Business Pulse</Link>}>The address may be mistyped, or the page may have moved. Use the navigation or press Ctrl K to jump to a page.</Empty>;
  }
}

function Notifications({ rows, onChange, onClose }: { rows: Row[]; onChange: (rows: Row[]) => void; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const outside = (event: PointerEvent) => { const target = event.target as HTMLElement; if (!box.current?.contains(target) && !target.closest("[aria-expanded]")) onClose(); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [onClose]);
  return <div className="notification-popover" ref={box} role="region" aria-label="Notifications"><h3>Notifications</h3>
    {rows.length ? rows.map(n => <div className={`notification ${n.read ? "" : "unread"}`} key={String(n.id)}><strong>{String(n.title)}</strong><p>{String(n.body)}</p>
      {!n.read && <button className="text-button" onClick={async () => { try { await api(`/v1/notifications/${n.id}/read`, { method: "POST" }); onChange(rows.map(item => item.id === n.id ? { ...item, read: true } : item)); } catch { /* Stays unread; the next open retries. */ } }}>Mark as read</button>}</div>)
      : <p>Nothing new. Approvals and incidents will be announced here.</p>}</div>;
}

type Target = { href: string; label: string; icon: string; group: string; hint?: string };

/** Keyboard-first way to reach any screen, skill or automation. Only navigates; it performs no action. */
function CommandPalette({ onClose }: { onClose: () => void }) {
  const router = useRouter(); const ref = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState(""); const [index, setIndex] = useState(0); const [extra, setExtra] = useState<Target[]>([]);
  useEffect(() => { if (ref.current && !ref.current.open) ref.current.showModal(); }, []);
  useEffect(() => {
    let current = true;
    Promise.allSettled([api<{ automations: Automation[] }>("/v1/automations"), api<{ skills: Skill[] }>("/v1/skills")]).then(([automations, skills]) => {
      if (!current) return;
      setExtra([
        ...(automations.status === "fulfilled" ? automations.value.automations.map(a => ({ href: `/automations/${a.id}`, label: a.name, icon: "layers", group: "Automations", hint: human(a.status) })) : []),
        ...(skills.status === "fulfilled" ? skills.value.skills.map(s => ({ href: `/explore/${s.id}`, label: s.title, icon: "grid", group: "Skills", hint: s.industry })) : []),
      ]);
    });
    return () => { current = false; };
  }, []);
  const results = useMemo(() => {
    const pages: Target[] = [...ALL_NAV.map(([href, label, icon]) => ({ href, label, icon, group: "Pages" })), { href: "/org/select", label: "Switch or add a business", icon: "team", group: "Pages" }];
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const matches = (target: Target) => words.every(word => `${target.label} ${target.hint || ""} ${target.group}`.toLowerCase().includes(word));
    return [...pages, ...extra].filter(matches).slice(0, words.length ? 30 : pages.length);
  }, [query, extra]);
  useEffect(() => { setIndex(0); }, [query]);
  const go = (target?: Target) => { if (!target) return; onClose(); router.push(target.href); };
  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") { event.preventDefault(); setIndex(i => Math.min(results.length - 1, i + 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setIndex(i => Math.max(0, i - 1)); }
    else if (event.key === "Enter") { event.preventDefault(); go(results[index]); }
  }
  useEffect(() => { ref.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [index]);
  return <dialog ref={ref} className="palette" aria-label="Jump to a page, skill or automation" onClose={onClose} onClick={event => { if (event.target === ref.current) onClose(); }}>
    <div className="palette-input"><Icon name="search" size={18} /><input autoFocus role="combobox" aria-expanded="true" aria-controls="palette-results" aria-activedescendant={results[index] ? `palette-${index}` : undefined} aria-label="Search pages, skills and automations" placeholder="Search pages, skills and automations…" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={onKeyDown} autoComplete="off" spellCheck={false} /></div>
    <ul className="palette-list" id="palette-results" role="listbox" aria-label="Results">
      {results.map((target, i) => <li key={target.href} id={`palette-${i}`} role="option" aria-selected={i === index} className="palette-item" onPointerMove={() => setIndex(i)} onClick={() => go(target)}><Icon name={target.icon} size={17} /><span>{target.label}</span><small>{target.hint ? `${target.group} · ${target.hint}` : target.group}</small></li>)}
      {!results.length && <li className="palette-empty" role="presentation">Nothing matches “{query}”. Try a page name, a skill or an automation.</li>}
    </ul>
    <div className="palette-foot"><span><span className="kbd">↑</span> <span className="kbd">↓</span> to move</span><span><span className="kbd">Enter</span> to open</span><span><span className="kbd">Esc</span> to close</span></div>
  </dialog>;
}
