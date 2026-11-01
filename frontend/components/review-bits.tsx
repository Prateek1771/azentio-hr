"use client";

import { useEffect, useState } from "react";
import { Clock, ShieldAlert, ShieldCheck } from "lucide-react";
import type { Tier } from "@/lib/api";
import { cn } from "@/lib/utils";

export function TierBadge({ tier, className }: { tier: Tier | null | undefined; className?: string }) {
  if (!tier) return null;
  const high = tier === "high";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap",
      high ? "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300" : "bg-slate-100 text-slate-700 dark:bg-slate-500/15 dark:text-slate-300", className)}>
      {high ? <ShieldAlert className="size-3" /> : <ShieldCheck className="size-3" />}{high ? "High risk" : "Low risk"}
    </span>
  );
}

const REASON_TONE: Record<string, string> = {
  high_risk: "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/30",
  low_confidence: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/30",
  ai_unavailable: "bg-zinc-100 text-zinc-700 ring-zinc-200 dark:bg-zinc-500/15 dark:text-zinc-300 dark:ring-zinc-500/30",
  unexpected_type: "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/30",
  spot_check_failed: "bg-violet-50 text-violet-800 ring-violet-200 dark:bg-violet-500/10 dark:text-violet-300 dark:ring-violet-500/30",
};

export function ReasonChip({ code, label }: { code: string; label: string }) {
  return <span className={cn("inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap",
    REASON_TONE[code] ?? "bg-muted text-muted-foreground ring-border")}>{label}</span>;
}

/** The current time, refreshed every minute (render stays pure; countdowns stay live). */
export function useNow(every = 60_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(t);
  }, [every]);
  return now;
}

/** Time left before the SLA: amber under 24h, red once overdue. */
export function SlaCountdown({ due }: { due: string | null }) {
  const now = useNow();
  if (!due) return <span className="text-xs text-muted-foreground">—</span>;
  const ms = new Date(due).getTime() - now;
  const h = Math.round(Math.abs(ms) / 3_600_000);
  const text = h < 1 ? "< 1h" : h < 48 ? `${h}h` : `${Math.round(h / 24)}d`;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs tabular-nums whitespace-nowrap",
      ms < 0 ? "font-medium text-rose-600 dark:text-rose-400" : ms < 86_400_000 ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground")}>
      <Clock className="size-3" />{ms < 0 ? `${text} overdue` : `${text} left`}
    </span>
  );
}

export const docTypeLabel = (k: string) => k ? k.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "Unclassified";
