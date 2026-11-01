"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ArrowUpRight, CalendarClock, CheckCircle2, Inbox, Lock, Megaphone, Search, ShieldAlert, Upload, Users } from "lucide-react";
import { api, fmtDate, type EmployeeRow, type Stats } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { Initials, SegmentBar, StateBadge } from "@/components/status";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { can, useMe } from "@/components/auth";

const OVERALL: Record<EmployeeRow["overall"], { label: string; cls: string }> = {
  ready: { label: "Ready for day one", cls: "text-emerald-700 bg-emerald-50 ring-emerald-600/20 dark:text-emerald-400 dark:bg-emerald-500/10" },
  in_progress: { label: "On track", cls: "text-sky-700 bg-sky-50 ring-sky-600/20 dark:text-sky-400 dark:bg-sky-500/10" },
  overdue: { label: "At risk", cls: "text-rose-700 bg-rose-50 ring-rose-600/20 dark:text-rose-400 dark:bg-rose-500/10" },
  blocked: { label: "Blocked", cls: "text-zinc-700 bg-zinc-100 ring-zinc-500/20 dark:text-zinc-300 dark:bg-zinc-500/10" },
};

export default function Dashboard() {
  const router = useRouter();
  const [stats, setStats] = useState<Stats | null>(null);
  const [rows, setRows] = useState<EmployeeRow[] | null>(null);
  const [f, setF] = useState({ department: "", location: "", status: "", join_from: "", join_to: "", search: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmEsc, setConfirmEsc] = useState(false);
  const { me } = useMe();

  const load = useCallback(() => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    api<EmployeeRow[]>(`/api/employees?${qs}`).then(setRows).catch((e) => toast.error(e.message));
    api<Stats>("/api/stats").then(setStats).catch(() => {});
  }, [f]);

  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [load]);

  const checkInbox = async () => {
    setBusy("inbox");
    try {
      const r = await api<{ processed: number }>("/api/gmail/poll", { method: "POST" });
      toast.success(r.processed ? `Processed ${r.processed} attachment(s) from replies` : "No new replies with attachments");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const escalate = async () => {
    setConfirmEsc(false);
    setBusy("esc");
    try {
      const r = await api<{ escalations: { employee: string; item: string; owner: string }[] }>("/api/escalate", { method: "POST" });
      toast.success(r.escalations.length ? `Escalated ${r.escalations.length} overdue task(s) to their owners` : "Nothing overdue to escalate");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const kpis = useMemo(() => stats && [
    { label: "New joiners", value: stats.joiners, hint: `${stats.avg_progress}% average readiness`, Icon: Users, tone: "text-primary bg-primary/10" },
    { label: "Ready for day one", value: stats.ready, hint: "All items complete", Icon: CheckCircle2, tone: "text-emerald-600 bg-emerald-500/10" },
    { label: "Joining in 7 days", value: stats.joining_7d, hint: "Needs attention first", Icon: CalendarClock, tone: "text-sky-600 bg-sky-500/10" },
    { label: "Overdue items", value: stats.overdue, hint: "Past due date", Icon: AlertTriangle, tone: "text-rose-600 bg-rose-500/10" },
    { label: "Blocked items", value: stats.blocked, hint: "Waiting on prerequisites", Icon: Lock, tone: "text-zinc-600 bg-zinc-500/10" },
    { label: "Needs review", value: stats.needs_review, hint: "Low-confidence documents", Icon: ShieldAlert, tone: "text-violet-600 bg-violet-500/10" },
  ], [stats]);

  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));

  return (
    <>
      <PageHeader
        title="Onboarding overview"
        subtitle="Every new joiner, what's pending, and who is responsible."
        actions={can(me, "onboarding") && <>
          <Button variant="outline" onClick={checkInbox} disabled={busy !== null || !stats?.gmail}>
            <Inbox className="size-4" /> {busy === "inbox" ? "Checking…" : "Check inbox now"}
          </Button>
          <Button variant="outline" onClick={() => setConfirmEsc(true)} disabled={busy !== null}>
            <Megaphone className="size-4" /> {busy === "esc" ? "Escalating…" : "Escalate overdue"}
          </Button>
          <Button nativeButton={false} render={<Link href="/import" />}><Upload className="size-4" /> Import joiners</Button>
        </>}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {kpis ? kpis.map((k) => (
          <Card key={k.label} className="gap-0 p-4">
            <div className={cn("mb-3 grid size-8 place-items-center rounded-lg", k.tone)}><k.Icon className="size-4" /></div>
            <div className="text-2xl font-semibold tabular-nums">{k.value}</div>
            <div className="text-sm font-medium">{k.label}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">{k.hint}</div>
          </Card>
        )) : Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
      </div>

      <Card className="mt-6 gap-0 overflow-hidden p-0">
        <div className="flex flex-wrap items-center gap-2 border-b p-3">
          <div className="relative min-w-52 flex-1">
            <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={f.search} onChange={(e) => set("search")(e.target.value)} placeholder="Search name, role or email" className="pl-8" />
          </div>
          <SimpleSelect value={f.department} onChange={set("department")} placeholder="All departments"
            options={(stats?.departments ?? []).map((d) => ({ value: d, label: d }))} />
          <SimpleSelect value={f.location} onChange={set("location")} placeholder="All locations"
            options={(stats?.locations ?? []).map((d) => ({ value: d, label: d }))} />
          <SimpleSelect value={f.status} onChange={set("status")} placeholder="Any status"
            options={[{ value: "ready", label: "Ready" }, { value: "in_progress", label: "On track" },
              { value: "overdue", label: "Has overdue items" }, { value: "blocked", label: "Has blocked items" }]} />
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Joining
            <Input type="date" value={f.join_from} onChange={(e) => set("join_from")(e.target.value)} className="w-36" aria-label="Joining from" />
            to
            <Input type="date" value={f.join_to} onChange={(e) => set("join_to")(e.target.value)} className="w-36" aria-label="Joining to" />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">Employee</th>
                <th className="px-4 py-2.5 font-medium">Department · Location</th>
                <th className="px-4 py-2.5 font-medium">Joining</th>
                <th className="w-56 px-4 py-2.5 font-medium">Readiness</th>
                <th className="px-4 py-2.5 font-medium">Tasks</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-2" />
              </tr>
            </thead>
            <tbody>
              {rows === null && Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}><td colSpan={7} className="px-4 py-3"><Skeleton className="h-9" /></td></tr>
              ))}
              {rows?.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-16 text-center text-muted-foreground">No joiners match these filters.</td></tr>
              )}
              {rows?.map((e) => (
                <tr key={e.id} onClick={() => router.push(`/employees/${e.id}`)}
                  className="group cursor-pointer border-t transition-colors hover:bg-muted/40">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <Initials name={e.name} />
                      <div>
                        <div className="font-medium">{e.name}</div>
                        <div className="text-xs text-muted-foreground">{e.role} · {e.grade}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{e.department}<div className="text-xs">{e.location}</div></td>
                  <td className="px-4 py-3">
                    <div>{fmtDate(e.joining_date)}</div>
                    <div className={cn("text-xs", e.days_to_join < 0 ? "text-muted-foreground" : e.days_to_join <= 7 ? "font-medium text-amber-600" : "text-muted-foreground")}>
                      {e.days_to_join < 0 ? `joined ${-e.days_to_join}d ago` : e.days_to_join === 0 ? "today" : `in ${e.days_to_join} days`}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="mb-1.5 flex justify-between text-xs"><span className="font-medium tabular-nums">{e.summary.progress}%</span>
                      <span className="text-muted-foreground">{e.summary.completed}/{e.summary.total}</span></div>
                    <SegmentBar s={e.summary} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {e.summary.overdue > 0 && <StateBadge state="overdue" className="tabular-nums" />}
                      {e.summary.blocked > 0 && <span className="text-xs text-muted-foreground">{e.summary.blocked} blocked</span>}
                      {e.summary.corrections > 0 && <span className="text-xs text-amber-600">{e.summary.corrections} correction</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", OVERALL[e.overall].cls)}>
                      {OVERALL[e.overall].label}
                    </span>
                    {e.flags > 0 && <span className="ml-1.5 inline-flex items-center gap-0.5 text-xs text-rose-600"><AlertTriangle className="size-3" />{e.flags}</span>}
                  </td>
                  <td className="px-2"><ArrowUpRight className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Dialog open={confirmEsc} onOpenChange={setConfirmEsc}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Escalate overdue tasks?</DialogTitle>
            <DialogDescription>
              The owner of every overdue task (IT, Facilities, Compliance, HR) gets an email listing the task, its due date and what it is blocking.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmEsc(false)}>Cancel</Button>
            <Button onClick={escalate}><Megaphone className="size-4" /> Send escalations</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
