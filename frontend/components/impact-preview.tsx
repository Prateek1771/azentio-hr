"use client";

import { Fragment, useEffect, useState } from "react";
import { AlertTriangle, CalendarClock, ChevronDown, ChevronRight, Lock, Minus, Plus, ShieldCheck, UserCheck, Users } from "lucide-react";
import { api, fmtDate, type ImpactSummary, type PolicyPreview, type Stats } from "@/lib/api";
import { SimpleSelect } from "@/components/simple-select";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const TILES: { key: keyof ImpactSummary; label: string; hint: string; Icon: typeof Users; tone: string }[] = [
  { key: "joiners", label: "Joiners affected", hint: "In-flight joiners whose checklist changes", Icon: Users, tone: "text-primary bg-primary/10" },
  { key: "added", label: "Items added", hint: "New or restored requirements", Icon: Plus, tone: "text-emerald-600 bg-emerald-500/10" },
  { key: "cancelled", label: "Cancelled", hint: "Not started; no longer required", Icon: Minus, tone: "text-rose-600 bg-rose-500/10" },
  { key: "kept_as_evidence", label: "Kept as evidence", hint: "Done or has documents; never deleted", Icon: ShieldCheck, tone: "text-violet-600 bg-violet-500/10" },
  { key: "due_moved", label: "Due dates moved", hint: "Same item, new deadline", Icon: CalendarClock, tone: "text-sky-600 bg-sky-500/10" },
  { key: "newly_blocked", label: "Newly blocked", hint: "Now waiting on a prerequisite", Icon: Lock, tone: "text-zinc-600 bg-zinc-500/10" },
  { key: "near_start", label: "Near start", hint: "Held for HR review, not changed", Icon: UserCheck, tone: "text-amber-600 bg-amber-500/10" },
];

export function ImpactTiles({ summary, compact = false }: { summary: ImpactSummary; compact?: boolean }) {
  return (
    <div className={cn("grid gap-3", compact ? "grid-cols-2 sm:grid-cols-4 xl:grid-cols-7" : "grid-cols-2 md:grid-cols-4 xl:grid-cols-7")}>
      {TILES.map((t) => (
        <Card key={t.key} className={cn("gap-0", compact ? "p-3" : "p-4")}>
          {!compact && <div className={cn("mb-2.5 grid size-8 place-items-center rounded-lg", t.tone)}><t.Icon className="size-4" /></div>}
          <div className={cn("font-semibold tabular-nums", compact ? "text-lg" : "text-2xl")}>{summary[t.key]}</div>
          <div className="text-xs font-medium">{t.label}</div>
          {!compact && <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{t.hint}</div>}
        </Card>
      ))}
    </div>
  );
}

const chip = (n: number, label: string, cls: string) => n > 0 &&
  <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", cls)}>{n} {label}</span>;

/** What publishing a version would do to each in-flight joiner. Same diff the publish job applies. */
export function ImpactPreview({ versionId, onSummary }: { versionId: number; onSummary?: (s: ImpactSummary) => void }) {
  const [f, setF] = useState({ department: "", location: "", status: "", join_from: "", join_to: "" });
  const [data, setData] = useState<PolicyPreview | null>(null);
  const [error, setError] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => { api<Stats>("/api/stats").then(setStats).catch(() => {}); }, []);
  useEffect(() => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    const t = setTimeout(() => {
      api<PolicyPreview>(`/api/policy/versions/${versionId}/preview?${qs}`)
        .then((p) => { setData(p); setError(""); if (!qs) onSummary?.(p.summary); })
        .catch((e) => setError((e as Error).message));
    }, 150);
    return () => clearTimeout(t);
  }, [versionId, f, onSummary]);

  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));

  return (
    <div className="space-y-4">
      {error ? <Card className="p-6 text-sm text-destructive">{error}</Card>
        : data ? <ImpactTiles summary={data.summary} />
        : <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">{TILES.map((t) => <Skeleton key={t.key} className="h-28 rounded-xl" />)}</div>}

      <Card className="gap-0 overflow-hidden p-0">
        <div className="flex flex-wrap items-center gap-2 border-b p-3">
          <SimpleSelect value={f.department} onChange={set("department")} placeholder="All departments"
            options={(stats?.departments ?? []).map((d) => ({ value: d, label: d }))} />
          <SimpleSelect value={f.location} onChange={set("location")} placeholder="All locations"
            options={(stats?.locations ?? []).map((d) => ({ value: d, label: d }))} />
          <SimpleSelect value={f.status} onChange={set("status")} placeholder="Any status"
            options={[{ value: "ready", label: "Ready" }, { value: "in_progress", label: "On track" },
              { value: "overdue", label: "Has overdue items" }, { value: "blocked", label: "Blocked" }]} />
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Joining
            <Input type="date" value={f.join_from} onChange={(e) => set("join_from")(e.target.value)} className="w-36" aria-label="Joining from" />
            to
            <Input type="date" value={f.join_to} onChange={(e) => set("join_to")(e.target.value)} className="w-36" aria-label="Joining to" />
          </div>
        </div>
        {!data ? <div className="space-y-2 p-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
          : data.rows.length === 0 ? (
            <div className="p-10 text-center text-sm text-muted-foreground">
              No in-flight joiner&apos;s checklist changes{Object.values(f).some(Boolean) ? " for these filters" : ""}. New joiners will get this version.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
                  <tr><th className="w-8" /><th className="px-3 py-2.5 font-medium">Joiner</th><th className="px-3 font-medium">Joining</th>
                    <th className="px-3 font-medium">Changes</th></tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <Fragment key={r.employee_id}>
                      <tr className="cursor-pointer border-t hover:bg-muted/30" onClick={() => setOpen(open === r.employee_id ? null : r.employee_id)}>
                        <td className="pl-3 text-muted-foreground">{open === r.employee_id ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</td>
                        <td className="px-3 py-2.5"><div className="font-medium">{r.name}</div>
                          <div className="text-xs text-muted-foreground">{r.role} · {r.department} · {r.location}</div></td>
                        <td className="px-3 text-xs whitespace-nowrap">{fmtDate(r.joining_date)}</td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap gap-1.5">
                            {r.near_start && <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
                              <AlertTriangle className="size-3" /> HR review: won&apos;t change automatically</span>}
                            {chip(r.added, "added", "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300")}
                            {chip(r.cancelled, "cancelled", "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300")}
                            {chip(r.kept_as_evidence, "kept as evidence", "bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300")}
                            {chip(r.due_moved, "due moved", "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300")}
                            {chip(r.newly_blocked, "newly blocked", "bg-zinc-200 text-zinc-800 dark:bg-zinc-500/20 dark:text-zinc-300")}
                          </div>
                        </td>
                      </tr>
                      {open === r.employee_id && (
                        <tr className="border-t bg-muted/20"><td /><td colSpan={3} className="px-3 py-3 text-xs">
                          <ul className="grid gap-1 sm:grid-cols-2">
                            {r.detail.added.map((x) => <li key={`a${x.key}`}><b className="text-emerald-700 dark:text-emerald-400">+</b> {x.name} · due {fmtDate(x.due)}</li>)}
                            {r.detail.cancelled.map((x) => <li key={`c${x.key}`}><b className="text-rose-700 dark:text-rose-400">−</b> {x.name} cancelled</li>)}
                            {r.detail.kept_as_evidence.map((x) => <li key={`k${x.key}`}><b className="text-violet-700 dark:text-violet-400">◆</b> {x.name} ({x.status}) kept as evidence</li>)}
                            {r.detail.due_moved.map((x) => <li key={`d${x.key}`}><b className="text-sky-700 dark:text-sky-400">↦</b> {x.name}: {fmtDate(x.from)} → {fmtDate(x.to)}</li>)}
                            {r.detail.newly_blocked.map((k) => <li key={`b${k}`}><b className="text-zinc-500">⏸</b> {k} now waits on a prerequisite</li>)}
                          </ul>
                        </td></tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
    </div>
  );
}
