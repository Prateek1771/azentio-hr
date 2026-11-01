"use client";

import { useEffect, useState } from "react";
import { FileCheck2 } from "lucide-react";
import { api, type AiAccuracy } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const dur = (s: number | null) => s == null ? "—" : s < 3600 ? `${Math.round(s / 60)} min` : s < 172800 ? `${(s / 3600).toFixed(1)} h` : `${Math.round(s / 86400)} d`;

/** Document decisions: how much the policy lets the AI accept, and how often a human finds it was wrong. */
export function AiDecisionsCard() {
  const [a, setA] = useState<AiAccuracy | null>(null);
  useEffect(() => { api<AiAccuracy>("/api/quality/ai-accuracy").then(setA).catch(() => {}); }, []);
  if (!a) return null;
  const maxReason = Math.max(1, ...a.review_reasons.map((r) => r.n));
  return (
    <Card className="gap-4 p-5">
      <div className="flex items-center gap-2">
        <FileCheck2 className="size-4 text-primary" />
        <div className="text-sm font-semibold">AI document decisions</div>
        <span className="text-xs text-muted-foreground">last {a.days} days · {a.documents} documents{a.models.length ? ` · ${a.models.join(", ")}` : ""}</span>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Auto-accepted" value={pct(a.auto_accept_rate)} hint="Low-risk documents that passed every check above their threshold" />
        <Stat label="Spot-check precision" value={a.too_small && a.spot_checks === 0 ? "—" : pct(a.spot_check_precision)}
          hint={a.precision_ci95 ? `95% CI ${pct(a.precision_ci95[0])}–${pct(a.precision_ci95[1])} · n=${a.spot_checks}${a.too_small ? " · n too small to judge" : ""}` : "No spot checks yet"}
          warn={a.too_small} />
        <Stat label="Median time to decision" value={dur(a.median_seconds_to_decision)} hint="From routing to a person's decision" />
      </div>
      {a.review_reasons.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-medium text-muted-foreground">Why documents went to a person</div>
          {a.review_reasons.map((r) => (
            <div key={r.reason_code} className="grid grid-cols-[9rem_1fr_2rem] items-center gap-2 text-xs">
              <span>{r.label}</span>
              <div className="h-2 rounded-full bg-muted"><div className="h-full rounded-full bg-primary/70" style={{ width: `${(r.n / maxReason) * 100}%` }} /></div>
              <span className="text-right tabular-nums">{r.n}</span>
            </div>
          ))}
        </div>
      )}
      {Object.keys(a.by_type).length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-left text-muted-foreground"><tr><th className="py-1 font-normal">Type</th><th className="font-normal">Docs</th>
            <th className="font-normal">Auto</th><th className="font-normal">Review</th><th className="font-normal">Correction</th><th className="font-normal">Spot ✓/✕</th></tr></thead>
          <tbody>{Object.entries(a.by_type).map(([t, v]) => (
            <tr key={t} className="border-t"><td className="py-1.5">{t.replace(/_/g, " ")}</td><td className="tabular-nums">{v.total}</td>
              <td className="tabular-nums">{v.auto_accept}</td><td className="tabular-nums">{v.review}</td><td className="tabular-nums">{v.correction}</td>
              <td className={cn("tabular-nums", v.spot_fail > 0 && "text-rose-600 dark:text-rose-400")}>{v.spot_pass}/{v.spot_fail}</td></tr>))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Stat({ label, value, hint, warn }: { label: string; value: string; hint: string; warn?: boolean }) {
  return (
    <div>
      <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      <div className={cn("text-[11px] text-muted-foreground", warn && "text-amber-700 dark:text-amber-400")}>{hint}</div>
    </div>
  );
}
