"use client";

import { FlaskConical, Minus, PencilLine, Plus, ShieldAlert, SlidersHorizontal } from "lucide-react";
import type { Backtest, DocPolicyChange, PolicyDiff, Rule } from "@/lib/api";
import { cn } from "@/lib/utils";

const FIELD_LABEL: Record<string, string> = {
  item: "Item", name: "Name", kind: "Kind", category: "Owner", match: "Applies to", offset_days: "Due (days)",
  depends_on: "Depends on", description: "Reason", policy_ref: "Policy", auto: "Automation", compliance: "Compliance",
};

const show = (v: unknown) => {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (typeof v === "object") return Object.keys(v as object).length ? JSON.stringify(v) : "all employees";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
};

export const diffCount = (d?: PolicyDiff | null) => d ? d.added.length + d.removed.length + d.changed.length + (d.doc_policy?.length ?? 0) : 0;

const DOC_FIELD: Record<DocPolicyChange["field"], string> = {
  tier: "Risk tier", auto_accept_min: "Auto-accept threshold", spot_check_rate: "Spot-check sample", extraction_deadline_minutes: "AI deadline (min)",
};
const fmtDoc = (c: DocPolicyChange, v: string | number) =>
  c.field === "spot_check_rate" ? `${Math.round(Number(v) * 100)}%` : c.field === "auto_accept_min" ? Number(v).toFixed(2) : String(v);
/** Loosening a control (lower threshold, tier change, fewer spot checks) needs two approvers. */
export const loosens = (c: DocPolicyChange) => c.field === "tier" || ((c.field === "auto_accept_min" || c.field === "spot_check_rate") && Number(c.to) < Number(c.from));

export function DocPolicyDiffView({ changes }: { changes: DocPolicyChange[] }) {
  if (!changes.length) return null;
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="flex items-center gap-2 bg-muted/40 px-3 py-2 text-xs font-medium"><SlidersHorizontal className="size-3.5" /> Document review policy</div>
      <table className="w-full text-xs">
        <tbody>{changes.map((c, i) => (
          <tr key={i} className="border-t">
            <td className="px-3 py-1.5 font-medium">{c.type === "*" ? "All documents" : c.type.replace(/_/g, " ")}</td>
            <td className="text-muted-foreground">{DOC_FIELD[c.field]}</td>
            <td className="py-1.5 tabular-nums"><span className="rounded bg-rose-100/70 px-1 line-through dark:bg-rose-500/15">{fmtDoc(c, c.from)}</span>
              {" → "}<span className="rounded bg-emerald-100/70 px-1 dark:bg-emerald-500/15">{fmtDoc(c, c.to)}</span></td>
            <td className="pr-3 text-right">{loosens(c) && <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">loosens a control</span>}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  );
}

/** What the draft thresholds would have done to the last 90 days of automatic decisions. Never re-decides anything. */
export function BacktestSummary({ bt }: { bt: Backtest }) {
  return (
    <div className="rounded-lg border border-violet-200 bg-violet-50/60 p-3 text-xs dark:border-violet-500/30 dark:bg-violet-500/10">
      <div className="mb-1 flex items-center gap-1.5 font-medium"><FlaskConical className="size-3.5 text-violet-600" /> Backtest: last {bt.days} days</div>
      {bt.changed === 0 ? <p>None of the {bt.decisions} automatic decisions would have come out differently.</p> : <p>
        Of {bt.decisions} automatic decisions, <b>{bt.changed}</b> would change: <b>{bt.newly_auto_accepted}</b> more auto-accepted
        {bt.newly_auto_accepted_spot_failed > 0 && <span className="text-rose-700 dark:text-rose-300"> ({bt.newly_auto_accepted_spot_failed} of which failed a spot check)</span>},{" "}
        <b>{bt.newly_reviewed}</b> more sent to a person.</p>}
      {Object.keys(bt.by_type).length > 0 && <p className="mt-1 text-muted-foreground">{Object.entries(bt.by_type)
        .map(([t, v]) => `${t.replace(/_/g, " ")}: +${v.newly_auto_accepted} auto / +${v.newly_reviewed} review`).join(" · ")}</p>}
    </div>
  );
}

/** Rule-level changes between two rule-set versions, side by side by rule id. */
export function PolicyDiffView({ diff, empty = "No rule changes" }: { diff: PolicyDiff; empty?: string }) {
  if (!diffCount(diff)) return <p className="text-sm text-muted-foreground">{empty}</p>;
  const rulesOnly = diff.added.length + diff.removed.length + diff.changed.length === 0;
  if (rulesOnly) return <DocPolicyDiffView changes={diff.doc_policy ?? []} />;
  const ruleLine = (r: Rule) => (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="font-medium">{r.name || r.item}</span>
      <span className="font-mono text-[11px] text-muted-foreground">{r.id}</span>
      {r.compliance && <ShieldAlert className="size-3.5 text-amber-600" aria-label="compliance rule" />}
    </span>
  );
  return (
    <div className="space-y-2">
    <ul className="space-y-1.5 text-sm">
      {diff.added.map((r) => (
        <li key={`a-${r.id}`} className="flex gap-2 rounded-lg bg-emerald-50 px-3 py-2 dark:bg-emerald-500/10">
          <Plus className="mt-0.5 size-4 shrink-0 text-emerald-600" /><div><span className="mr-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">Added</span>{ruleLine(r)}
            <p className="text-xs text-muted-foreground">{r.description}</p></div>
        </li>
      ))}
      {diff.removed.map((r) => (
        <li key={`r-${r.id}`} className="flex gap-2 rounded-lg bg-rose-50 px-3 py-2 dark:bg-rose-500/10">
          <Minus className="mt-0.5 size-4 shrink-0 text-rose-600" /><div><span className="mr-1.5 text-xs font-medium text-rose-700 dark:text-rose-400">Removed</span>
            <span className="line-through decoration-rose-400">{ruleLine(r)}</span></div>
        </li>
      ))}
      {diff.changed.map((c) => (
        <li key={`c-${c.id}`} className="flex gap-2 rounded-lg bg-amber-50 px-3 py-2 dark:bg-amber-500/10">
          <PencilLine className="mt-0.5 size-4 shrink-0 text-amber-600" />
          <div className="min-w-0 flex-1">
            <span className="mr-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">Changed</span>
            <span className="font-mono text-[11px] text-muted-foreground">{c.id}</span>
            <div className="mt-1 grid gap-1">
              {Object.entries(c.fields).map(([f, [a, b]]) => (
                <div key={f} className="grid grid-cols-[7rem_1fr] gap-2 text-xs">
                  <span className="text-muted-foreground">{FIELD_LABEL[f] ?? f}</span>
                  <span className="min-w-0 break-words"><span className={cn("rounded bg-rose-100/70 px-1 line-through dark:bg-rose-500/15")}>{show(a)}</span>
                    {" → "}<span className="rounded bg-emerald-100/70 px-1 dark:bg-emerald-500/15">{show(b)}</span></span>
                </div>
              ))}
            </div>
          </div>
        </li>
      ))}
    </ul>
    <DocPolicyDiffView changes={diff.doc_policy ?? []} />
    </div>
  );
}
