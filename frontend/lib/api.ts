// "" = same origin: requests go through the Next.js rewrite in next.config.ts.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "";

export type Role = "admin" | "hr_ops" | "approver" | "manager" | "auditor";
export interface User { id: number; email: string; name: string; role: Role; active?: boolean; created_at?: string; last_login?: string | null }

/** Send the browser to sign-in, remembering where it was. */
export function toLogin() {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  const next = window.location.pathname + window.location.search;
  window.location.href = `/login?next=${encodeURIComponent(next)}`;
}

export async function api<T = unknown>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`${API}${path}`, {
    ...rest,
    headers: json !== undefined ? { "content-type": "application/json", ...rest.headers } : rest.headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  if (res.status === 401 && !path.startsWith("/api/auth/") && !path.startsWith("/api/portal/")) toLogin();
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export type State = "completed" | "pending" | "blocked" | "overdue";

export interface Summary {
  completed: number; pending: number; blocked: number; overdue: number;
  total: number; progress: number; needs_review: number; corrections: number;
}

export interface Employee {
  id: string; name: string; email: string; role: string; grade: string; department: string;
  location: string; manager_email: string; joining_date: string; date_of_birth?: string; thread_id?: string;
}

export interface EmployeeRow extends Employee {
  summary: Summary; overall: "ready" | "overdue" | "blocked" | "in_progress"; days_to_join: number; flags: number;
}

export interface Doc {
  id: string; filename: string; mime: string; size: number; source: string; status: string; ts: number;
  confidence: number; doc_type: string; extracted: Record<string, unknown>; issues: string[];
}

export interface Item {
  id: string; key: string; name: string; kind: "document" | "task" | "ack"; category: string; rule_id: string;
  reason: string; policy_ref: string; due: string; status: string; state: State; owner_name: string;
  owner_email?: string; depends: string[]; blocked_by: string[]; documents: Doc[]; policy_text?: string; auto?: string;
}

export interface Flag { level: "error" | "warning" | "info"; type: string; message: string }
export interface Event { id: string; employee_id: string; kind: string; message: string; ts: number; employee_name?: string }

export interface EmployeeDetail {
  employee: Employee; items: Item[]; summary: Summary; flags: Flag[]; unmatched_documents: Doc[];
  events: Event[]; manager: { name: string; email: string } | null;
  hr_record: Record<string, { value: string; source: string }>;
}

export interface Stats {
  joiners: number; ready: number; joining_7d: number; overdue: number; blocked: number; needs_review: number;
  avg_progress: number; departments: string[]; locations: string[]; gmail: boolean;
}

export interface Template { id: string; name: string; subject: string; body: string }

export interface Rule {
  id: string; item: string; name: string; kind: string; category: string; match: Record<string, unknown>;
  offset_days: number; depends_on: string[]; description: string; policy_ref: string; auto: string;
}

export const fmtDate = (iso: string) =>
  iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—";

export const timeAgo = (ts: number) => {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export const daysUntil = (iso: string) => Math.round((new Date(iso + "T00:00:00").getTime() - Date.now()) / 86400000);

export interface Connector {
  id: string; name: string; key: string; role: "source" | "target" | "app"; record_kind: "candidate" | "employee" | "activity";
  demo: boolean; description: string; type_name: string; record_count: number; connection_status?: "not_configured" | "untested" | "connected";
}
export interface FieldSpec { key: string; label: string; type: string; required: boolean; description: string; is_custom: boolean; generated: boolean }
export interface ConnRecord { id: string; instance_id: string; kind: string; values: Record<string, unknown>; created: number; updated: number; employee_id?: string | null }
export interface IntegrationRun {
  id: string; source_id: string; target_id: string; source_name: string; target_name: string; policy: string; ts: number;
  mapping: Record<string, string>; counts: { created: number; updated: number; skipped: number; rejected: number };
  results: { record: string; outcome: string; reason?: string; target_id?: string }[];
}

// ---- Integration workflow ----
export interface MappingRule {
  source_field: string | null; target_field: string; transform: string; default_value: string | null;
  confidence?: number | null; rationale?: string | null; origin?: "agent" | "hr";
  expression?: string | null; email_domain?: string | null;
}
export interface AgentMessage { id: string; role: "agent" | "user"; content: string; ts: number }
export interface Schedule { kind: "manual" | "interval" | "daily"; interval_minutes: number | null; time: string | null; timezone: string }
export interface IntegrationSummary {
  id: string; name: string; description: string; status: "draft" | "active" | "paused"; setup_step: string;
  schedule: Schedule; next_run_at: number | null; last_run_at: number | null; created: number;
  source_id: string; target_id: string; source_name: string; target_name: string; candidates: number;
}
export interface IntegrationDetail extends IntegrationSummary {
  source_filter: { statuses: string[]; departments: string[] }; duplicate_policy: "update" | "skip";
  mapping: MappingRule[]; mapping_approved_at: number | null; notify_members: string[]; notify_emails: string[];
  notify_on_created: boolean; notify_on_success: boolean; notify_on_failure: boolean;
  doc_verification_enabled: boolean | null; offer_enabled: boolean | null; offer_template: string;
  offer_template_status: "none" | "pending_approval" | "approved" | "rejected"; offer_placeholders: string[];
  log_exclusions: { fields: string[]; sections: string[] }; source_fields: FieldSpec[]; target_fields: FieldSpec[];
  recipients: { email: string; name: string }[]; quick_replies: string[]; connectors_locked: boolean; messages: AgentMessage[];
  problems: string[]; agent_name: string; agent_is_simulated: boolean; delivery_is_simulated: boolean;
  owners: { name: string; email: string }[]; departments: string[];
  role_documents: Record<string, string[]>; access_rules: AccessRule[]; access_saved: boolean; roles: string[];
  doc_types: { key: string; name: string }[]; apps: AppConnector[]; suggested_access: AccessRule[];
  expression_functions: { name: string; example: string }[]; email_presets: { key: string; expression: string }[];
}
export interface AppConnector { id: string; name: string; key: string; actions: { key: string; label: string; needs_resource: boolean }[] }
export interface AccessRule {
  id: string; app_id: string; app: string; action: string; resource: string; mode: "direct" | "request";
  match: { role?: string[]; department?: string[]; location?: string[]; country?: string[]; grade_min?: number; grade_max?: number }; from_rule: string;
}
export interface AccessResult { id: string; app: string; action: string; resource: string; mode: string; status: string; reference: string; message: string; email: string }
export interface DocOption { key: string; name: string; suggested: boolean; reason: string }
export interface ConnectionSettings {
  tenant: string; auth_type: "basic" | "token"; username: string; secret_set: boolean; secret_hint: string;
  configured_at: number | null; last_test: { ok: boolean; at: number; ms: number; message: string } | null;
  supported: boolean; tenant_label: string; tenant_example: string;
}
export interface WfCandidate {
  id: string; record_id: string; name: string; email: string; stage: string; candidate_ref: string;
  document_results: { name: string; status: "verified" | "rejected" | "missing"; reason: string | null }[];
  rejection_reason?: string; last_error?: string; joiner_id?: string; employee_record_id?: string; updated: number;
  access: AccessResult[]; generated_values?: string;
}
export interface OutboundMessage {
  id: string; kind: "document_request" | "offer_letter" | "notification"; to: string; subject: string; content: string;
  attempt: number; delivered_by: "gmail" | "simulated"; candidate: string; ts: number;
}
export interface LogRun {
  id: string; integration_id: string; integration_name: string; trigger: string; status: string; started: number; finished: number;
  fetched_count: number; enrolled_count: number; advanced_count: number; waiting_count: number; failed_count: number;
  employees_created_count: number; employees_updated_count: number; documents_requested_count: number | null;
  documents_verified_count: number | null; approvals_pending_count: number | null; approvals_used_count: number | null;
  offers_sent_count: number | null; access_granted_count: number | null; access_requested_count: number | null; excluded_fields: string[]; excluded_sections: string[];
  failures: { candidate: string; message: string }[];
}
export interface LogRecord {
  id: string; candidate_name: string; outcome: "advanced" | "waiting" | "failed"; stage_before: string; stage_after: string;
  actions: { section: string; kind: string; message: string }[]; mapping_hidden: boolean; error: string;
  mapping: { source_field: string | null; target_field: string; transform: string; from_default: boolean; excluded: boolean; value: string | null }[];
}
export interface AuditEntry { id: string; action: string; entity_type: string; entity_id: string; summary: string; actor: string; ts: number; metadata: Record<string, unknown> }

export const describeSchedule = (s?: Schedule) => {
  if (!s || s.kind === "manual") return "Run now only";
  if (s.kind === "interval") {
    const m = s.interval_minutes ?? 0;
    return m === 60 ? "Every hour" : m % 60 === 0 ? `Every ${m / 60} hours` : `Every ${m} minutes`;
  }
  return `Daily at ${s.time} (${s.timezone})`;
};
export const fmtTs = (ts?: number | null) => ts ? new Date(ts * 1000).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
