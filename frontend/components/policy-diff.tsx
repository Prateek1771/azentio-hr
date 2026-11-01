"use client";

import { Minus, PencilLine, Plus, ShieldAlert } from "lucide-react";
import type { PolicyDiff, Rule } from "@/lib/api";
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

export const diffCount = (d?: PolicyDiff | null) => d ? d.added.length + d.removed.length + d.changed.length : 0;

/** Rule-level changes between two rule-set versions, side by side by rule id. */
export function PolicyDiffView({ diff, empty = "No rule changes" }: { diff: PolicyDiff; empty?: string }) {
  if (!diffCount(diff)) return <p className="text-sm text-muted-foreground">{empty}</p>;
  const ruleLine = (r: Rule) => (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="font-medium">{r.name || r.item}</span>
      <span className="font-mono text-[11px] text-muted-foreground">{r.id}</span>
      {r.compliance && <ShieldAlert className="size-3.5 text-amber-600" aria-label="compliance rule" />}
    </span>
  );
  return (
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
  );
}
