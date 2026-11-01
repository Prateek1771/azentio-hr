"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, CheckCircle2, CircleDashed, Clock, GitCommitVertical, History, RotateCcw, XCircle } from "lucide-react";
import { api, fmtWhen, STATE_LABEL, type RuleSetVersion, type VersionDetail, type VersionsResp, type VersionState } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { can, useMe } from "@/components/auth";
import { diffCount, PolicyDiffView } from "@/components/policy-diff";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const TONE: Record<VersionState, string> = {
  active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
  pending_approval: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  approved: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300",
  draft: "bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300",
  superseded: "bg-zinc-200 text-zinc-700 dark:bg-zinc-500/20 dark:text-zinc-300",
  rejected: "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300",
};
const ICON: Record<VersionState, typeof Clock> = {
  active: CheckCircle2, pending_approval: Clock, approved: Clock, draft: CircleDashed, superseded: GitCommitVertical, rejected: XCircle,
};

export default function HistoryPage() {
  const { me } = useMe();
  const canRollback = can(me, "onboarding");
  const [data, setData] = useState<VersionsResp | null>(null);
  const [open, setOpen] = useState<VersionDetail | null>(null);
  const [rollback, setRollback] = useState<RuleSetVersion | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => { api<VersionsResp>("/api/policy/versions").then(setData).catch((e) => toast.error((e as Error).message)); }, []);
  useEffect(load, [load]);

  const show = async (v: RuleSetVersion) => {
    try { setOpen(await api<VersionDetail>(`/api/policy/versions/${v.id}`)); } catch (e) { toast.error((e as Error).message); }
  };
  const byId = Object.fromEntries((data?.versions ?? []).map((v) => [v.id, v]));
  const doRollback = async () => {
    if (!rollback) return;
    setBusy(true);
    try {
      const v = await api<VersionDetail>(`/api/policy/versions/${rollback.id}/rollback`, { method: "POST", json: { reason } });
      toast.success(`v${v.number} created as a copy of v${rollback.number} and sent for approval`);
      setRollback(null);
      setReason("");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Link href="/rules" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Requirement rules</Link>
      <PageHeader title="Version history" subtitle="Every rule-set version is kept, with who wrote it, who approved it and why. Rolling back creates a new version that needs approval too." />
      {!data ? <div className="space-y-3">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}</div> : (
        <ol className="relative space-y-3 before:absolute before:top-2 before:bottom-2 before:left-[1.15rem] before:w-px before:bg-border">
          {data.versions.map((v) => {
            const Icon = ICON[v.state];
            const approvals = v.approvals.filter((a) => a.decision === "approve");
            const rejected = v.approvals.find((a) => a.decision === "reject");
            return (
              <li key={v.id} className="relative pl-11">
                <span className={cn("absolute top-4 left-2 grid size-6 place-items-center rounded-full ring-4 ring-background", TONE[v.state])}><Icon className="size-3.5" /></span>
                <Card className="gap-2 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-base font-semibold">v{v.number}</span>
                    <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", TONE[v.state])}>{STATE_LABEL[v.state]}</span>
                    <span className="min-w-0 flex-1 truncate text-sm">{v.title}</span>
                    <Button size="sm" variant="ghost" onClick={() => show(v)}>View changes</Button>
                    {canRollback && v.state === "superseded" && !data.open_id &&
                      <Button size="sm" variant="outline" onClick={() => setRollback(v)}><RotateCcw className="size-3.5" /> Roll back to this</Button>}
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>By {v.author_email ?? "system"}</span>
                    {v.submitted_at && <span>Submitted {fmtWhen(v.submitted_at)}</span>}
                    {v.published_at && <span>Published {fmtWhen(v.published_at)}</span>}
                    {v.state === "pending_approval" && <span className="text-amber-700 dark:text-amber-400">{approvals.length} of {v.required_approvals} approval{v.required_approvals > 1 ? "s" : ""}</span>}
                    {approvals.length > 0 && <span>Approved by {approvals.map((a) => a.email).join(", ")}</span>}
                    {rejected && <span className="text-rose-700 dark:text-rose-400">Rejected by {rejected.email}: {rejected.reason}</span>}
                    {v.rollback_of && <span className="text-violet-700 dark:text-violet-400">Rolls back to v{byId[v.rollback_of]?.number ?? "?"}</span>}
                  </div>
                  {v.reason && <p className="text-sm text-muted-foreground">{v.reason}</p>}
                </Card>
              </li>
            );
          })}
        </ol>
      )}

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto data-[side=right]:sm:max-w-xl">
          {open && <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2"><History className="size-4" /> v{open.number}: {open.title}</SheetTitle>
              <SheetDescription>{diffCount(open.diff)} change{diffCount(open.diff) === 1 ? "" : "s"} compared with {open.diff_against ? `v${byId[open.diff_against]?.number ?? "?"}` : "nothing (first version)"}</SheetDescription>
            </SheetHeader>
            <div className="px-4 pb-6"><PolicyDiffView diff={open.diff} /></div>
          </>}
        </SheetContent>
      </Sheet>

      <Dialog open={!!rollback} onOpenChange={(o) => !o && setRollback(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Roll back to v{rollback?.number}?</DialogTitle>
            <DialogDescription>This creates v{(data?.versions[0]?.number ?? 0) + 1} as a copy of v{rollback?.number} and sends it for approval.
              Joiners keep completed work and documents either way.</DialogDescription>
          </DialogHeader>
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder="Why roll back? Approvers see this." maxLength={2000} />
          <DialogFooter><Button variant="outline" onClick={() => setRollback(null)}>Cancel</Button>
            <Button onClick={doRollback} disabled={busy || reason.trim().length < 3}><RotateCcw className="size-4" /> Create rollback</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
