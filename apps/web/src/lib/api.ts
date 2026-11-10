export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

export async function api<T = Record<string, unknown>>(path: string, options: { method?: string; body?: unknown; idempotent?: boolean; idempotencyKey?: string } = {}): Promise<T> {
  const method = options.method || "GET";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (method !== "GET") {
    const csrf = document.cookie.split("; ").find(c => c.startsWith("gluons_csrf="))?.slice("gluons_csrf=".length);
    if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
    if (options.idempotent || options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey || crypto.randomUUID();
  }
  let response: Response;
  try { response = await fetch(`${API_URL}${path}`, { method, headers, credentials: "include", cache: "no-store", body: options.body === undefined ? undefined : JSON.stringify(options.body) }); }
  catch { throw new ApiError("API_UNAVAILABLE", "Cannot reach Gluons API. Check that the backend is running and try again.", 0); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const fields = data.error?.details?.fields?.map((f: { field: string; message: string }) => `${f.field}: ${f.message}`).join(" ");
    throw new ApiError(data.error?.code || "REQUEST_FAILED", fields || data.error?.message || `Request failed (${response.status}).`, response.status);
  }
  return data as T;
}

export type Row = Record<string, unknown>;
export type Organization = { id: string; name: string; role: string; permissions?: string[]; monthly_budget_usd?: string | null; emergency_stop_at?: string | null; industry?: string | null };
export type Session = { user: { id: string; email: string; display_name: string }; active_organization_id: string | null; organizations: Organization[]; mfa_pending: boolean; environment: string };
export type Skill = { id: string; title: string; industry: string; domain: string; outcome?: string; risk_tier: string; activation_modes: string[]; capability: { status: string; blockers: { requirement?: string; description?: string; name?: string; [key: string]: unknown }[] }; connectors: { capability: string; providers: string[]; purpose: string; required_for_live: boolean }[]; limitations?: string[]; actions?: Row[]; workflow?: Row[]; required_data?: string[] };
export type Automation = { id: string; name: string; skill_id: string; status: string; mode: string; version: number; created_at: string; config: Row; monthly_budget_usd: string | null; run_counts?: Record<string, number> };
export type Approval = { id: string; title: string; summary: string; action: string; risk_tier: string; status: string; expires_at: string; version: number; records_before: Row; records_after_preview: Row; source_evidence: Row[]; external_system: string; estimated_cost_usd: string | null; required_approvals: number; amount_minor: number | null };
export type AuditEvent = { seq: number; event_type: string; category: string; actor_kind: string; actor_id: string; resource_id: string; decision: string | null; created_at: string; data: Row };
export type Spending = { total_usd: string; monthly_budget_usd: string | null; period_start: string; breakdown: { category: string; provider: string; cost_basis: string; cost_usd: string; events: number }[] };
