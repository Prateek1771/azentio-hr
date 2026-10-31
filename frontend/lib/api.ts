export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function api<T = unknown>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`${API}${path}`, {
    ...rest,
    headers: json !== undefined ? { "content-type": "application/json", ...rest.headers } : rest.headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
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
  id: string; name: string; key: string; role: "source" | "target"; record_kind: "candidate" | "employee";
  demo: boolean; description: string; type_name: string; record_count: number;
}
export interface FieldSpec { key: string; label: string; type: string; required: boolean; description: string; is_custom: boolean; generated: boolean }
export interface ConnRecord { id: string; instance_id: string; kind: string; values: Record<string, unknown>; created: number; updated: number; employee_id?: string | null }
export interface IntegrationRun {
  id: string; source_id: string; target_id: string; source_name: string; target_name: string; policy: string; ts: number;
  mapping: Record<string, string>; counts: { created: number; updated: number; skipped: number; rejected: number };
  results: { record: string; outcome: string; reason?: string; target_id?: string }[];
}
