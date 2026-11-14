"use client";

import { cloneElement, Fragment, ReactElement, ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import type { Row } from "@/lib/api";
import { date, human, json, looksLikeTime } from "@/lib/format";
import Icon from "./icon";

/*
  Status colour follows authority, see globals.css:
  blue is an owner decision, green is confirmed, amber is waiting,
  red is stopped, and a dashed outline means nothing external happened.
*/
const TONES: Record<string, string[]> = {
  green: ["active", "verified", "connected", "completed", "live_verified", "healthy", "passed", "live", "succeeded", "confirmed", "production"],
  blue: ["approved", "signed", "supervised", "consented"],
  amber: ["pending", "waiting_approval", "requested", "proposed", "requires_external_authorization", "needs_configuration", "awaiting_activation", "needs_review", "running", "queued", "configuring", "stale", "reauthorization_required", "pending_review", "needs_attention", "blocked_by_external_dependency"],
  red: ["failed", "cancelled", "denied", "revoked", "expired", "blocked", "error", "failed_terminal", "disconnected", "dead_letter", "rejected"],
  preview: ["shadow", "trial_shadow", "trial_replay", "historical_replay", "replay", "draft", "simulator", "sandbox", "test", "preview", "sandbox_verified", "implemented", "implemented_unverified"],
};
export function toneOf(status: string): string {
  for (const [tone, names] of Object.entries(TONES)) if (names.includes(status)) return tone;
  return "neutral";
}

export function Badge({ children, status = "" }: { children: ReactNode; status?: string }) {
  return <span className={`badge ${toneOf(status)}`}><span /><span>{children}</span></span>;
}

export function ErrorBox({ error, retry }: { error: string | null; retry?: () => void }) {
  if (!error) return null;
  return <div className="error-box" role="alert"><Icon name="alert" size={18} /><span>{error}</span>{retry && <button className="text-button" onClick={retry}>Try again</button>}</div>;
}

/** Confirms something the owner just did. One per screen keeps announcements unambiguous. */
export function Success({ children }: { children: ReactNode }) {
  if (!children) return null;
  return <div className="success-box" role="status"><Icon name="check" size={18} /><span>{children}</span></div>;
}

export function Note({ children, icon = "shield" }: { children: ReactNode; icon?: string }) {
  return <div className="notice-box"><Icon name={icon} size={18} /><span>{children}</span></div>;
}

export function Empty({ icon = "layers", title, children, action }: { icon?: string; title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name={icon} size={22} /></span><h3>{title}</h3><p>{children}</p>{action}</div>;
}

export function Panel({ title, meta, children, className = "" }: { title?: string; meta?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{title && <div className="panel-heading"><h2>{title}</h2>{meta}</div>}{children}</section>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="loading" role="status"><span className="spinner" />{label}</div>;
}

/** Placeholder rows for a list that is still loading. Decorative, so hidden from assistive tech. */
export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return <div className="skeleton-rows" aria-hidden="true">{Array.from({ length: rows }, (_, i) => <span className="skeleton" key={i} style={{ width: `${88 - i * 14}%` }} />)}</div>;
}

export function PageTitle({ eyebrow, title, description, action, meta }: { eyebrow?: string; title: string; description?: string; action?: ReactNode; meta?: ReactNode }) {
  return <div className="page-title"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}{meta && <div className="page-title-meta">{meta}</div>}</div>{action}</div>;
}

/**
 * A labelled control with an optional hint. The hint is linked with aria-describedby
 * so it is announced after the label instead of becoming part of the control's name.
 */
export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactElement<{ id?: string; "aria-describedby"?: string }> }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{cloneElement(children, { id, "aria-describedby": hint ? `${id}-hint` : undefined })}{hint && <span className="field-hint" id={`${id}-hint`}>{hint}</span>}</div>;
}

export type TabItem = { id: string; label: string; count?: number };
export function Tabs({ tabs, value, onChange, label }: { tabs: TabItem[]; value: string; onChange: (id: string) => void; label: string }) {
  const list = useRef<HTMLDivElement>(null);
  function onKeyDown(event: React.KeyboardEvent) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = tabs[(tabs.findIndex(t => t.id === value) + step + tabs.length) % tabs.length];
    onChange(next.id);
    list.current?.querySelector<HTMLButtonElement>(`[data-tab="${next.id}"]`)?.focus();
  }
  return <div className="tabs" role="tablist" aria-label={label} ref={list} onKeyDown={onKeyDown}>{tabs.map(tab =>
    <button key={tab.id} role="tab" type="button" data-tab={tab.id} aria-selected={value === tab.id} tabIndex={value === tab.id ? 0 : -1} onClick={() => onChange(tab.id)}>{tab.label}{tab.count !== undefined && <span className="tab-count">{tab.count}</span>}</button>)}</div>;
}

export function JsonView({ value }: { value: unknown }) { return <pre className="json-view">{json(value)}</pre>; }

// Field names keep their dots: an owner types "email.send" in a policy and should see the same text back.
const label = (key: string) => key.replaceAll("_", " ");

function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="empty">—</span>;
  if (typeof value === "boolean") return <>{value ? "Yes" : "No"}</>;
  if (typeof value === "number") return <span className="num">{value}</span>;
  if (typeof value === "string") return <>{looksLikeTime(value) ? date(value) : value}</>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="empty">None</span>;
    if (value.every(item => item === null || typeof item !== "object")) return <>{value.map(String).join(", ")}</>;
    return <ul>{value.map((item, i) => <li key={i}><Value value={item} /></li>)}</ul>;
  }
  const entries = Object.entries(value as Row);
  if (!entries.length) return <span className="empty">None</span>;
  return <dl>{entries.map(([key, item]) => <Fragment key={key}><dt>{label(key)}</dt><dd><Value value={item} /></dd></Fragment>)}</dl>;
}

/**
 * Shows a server record as labelled fields rather than raw JSON.
 * The exact record stays one click away, because an owner may need to quote it.
 */
export function DataView({ value, raw = true }: { value: unknown; raw?: boolean }) {
  const structured = value !== null && typeof value === "object";
  return <div className="data-view"><Value value={value} />{raw && structured && <details className="data-raw"><summary>Exact record</summary><JsonView value={value} /></details>}</div>;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const cell = (value: unknown) => value === undefined ? <span className="subtle">Not set</span> : value !== null && typeof value === "object" ? <DataView value={value} raw={false} /> : <Value value={value} />;

/** Field-by-field comparison of the current record and the proposed one. */
export function Diff({ before, after }: { before: Row | null | undefined; after: Row | null | undefined }) {
  const left = before || {}, right = after || {};
  const keys = Array.from(new Set([...Object.keys(left), ...Object.keys(right)]));
  if (!keys.length) return <p className="subtle">No record fields were provided for this action.</p>;
  return <div className="diff" role="table" aria-label="Current record compared with the proposed record">
    <div className="diff-row head" role="row"><span role="columnheader">Field</span><span role="columnheader">Before</span><span role="columnheader">Proposed</span></div>
    {keys.map(key => <div className={`diff-row ${same(left[key], right[key]) ? "" : "changed"}`} role="row" key={key}><span role="rowheader">{label(key)}</span><span role="cell">{cell(left[key])}</span><span role="cell">{cell(right[key])}</span></div>)}
  </div>;
}

export function Stamp({ tone, children }: { tone: "verified" | "waiting" | "preview" | "failed"; children: ReactNode }) {
  return <span className={`stamp ${tone}`}>{tone === "verified" && <Icon name="check" size={13} />}{children}</span>;
}

/**
 * One external-action record. Only `verified`, which the server sets after an
 * independent readback, earns the verified stamp. Everything else says what it is.
 */
export function Receipt({ record }: { record: Row }) {
  const status = String(record.status ?? "unverified"), mode = String(record.mode ?? "");
  const tone = record.verified ? "verified" : ["failed", "rejected", "error"].includes(status) ? "failed" : ["shadow", "replay", "historical_replay"].includes(mode) ? "preview" : "waiting";
  const meta = [record.provider, record.environment && `${human(record.environment)} environment`, mode && `${human(mode)} mode`, record.created_at && date(record.created_at)].filter(Boolean);
  return <article className="receipt">
    <div className="receipt-head"><strong>{human(record.action)}</strong><Stamp tone={tone}>{record.verified ? "Verified by readback" : human(status)}</Stamp></div>
    {meta.length > 0 && <div className="receipt-meta">{meta.map((item, i) => <span key={i}>{String(item)}</span>)}</div>}
    <details><summary>Receipt details</summary><DataView value={record} /></details>
  </article>;
}

export function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const ratio = max > 0 ? value / max : 0;
  return <div className={`meter ${ratio >= 1 ? "over" : ratio >= 0.8 ? "warn" : ""}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.min(value, max)}><span style={{ width: `${Math.min(100, ratio * 100)}%` }} /></div>;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return <button type="button" className="button secondary compact" onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { /* Clipboard access is optional; the value stays selectable. */ } }}>{copied ? "Copied" : label}</button>;
}

export type ConfirmOptions = { title: string; body: ReactNode; confirmLabel: string; cancelLabel?: string; danger?: boolean };

/**
 * Asks before an action that cannot be taken back. Returns the question function
 * and the dialog element to render. Native <dialog> supplies focus trapping and Escape.
 */
export function useConfirm() {
  const ref = useRef<HTMLDialogElement>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const confirm = useCallback((next: ConfirmOptions) => new Promise<boolean>(resolve => { resolver.current = resolve; setOptions(next); }), []);
  useEffect(() => { if (options && ref.current && !ref.current.open) ref.current.showModal(); }, [options]);
  const dialog = options ? <dialog ref={ref} className="confirm-dialog" aria-labelledby="confirm-title" onClose={() => { resolver.current?.(ref.current?.returnValue === "confirm"); resolver.current = null; setOptions(null); }}>
    <form method="dialog"><h2 id="confirm-title">{options.title}</h2><p>{options.body}</p>
      <div className="button-row"><button className="button secondary" value="cancel" autoFocus>{options.cancelLabel || "Keep as it is"}</button><button className={`button ${options.danger ? "danger" : "primary"}`} value="confirm">{options.confirmLabel}</button></div>
    </form></dialog> : null;
  return [confirm, dialog] as const;
}
