"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Check, X } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const STAGE: Record<string, { label: string; cls: string }> = {
  pending: { label: "Not started", cls: "bg-muted text-muted-foreground" },
  documents_requested: { label: "Waiting on documents", cls: "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-400" },
  documents_rejected: { label: "Documents rejected, next run asks again", cls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400" },
  awaiting_hr_approval: { label: "Needs HR approval", cls: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400" },
  hr_rejected: { label: "Rejected by HR", cls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400" },
  approved: { label: "Approved, next run continues", cls: "bg-source-soft text-source" },
  offer_sent: { label: "Offer sent", cls: "bg-source-soft text-source" },
  completed: { label: "Employee created", cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400" },
  skipped: { label: "Skipped", cls: "bg-muted text-muted-foreground" },
};

export function StageBadge({ stage }: { stage: string }) {
  const s = STAGE[stage] ?? { label: stage, cls: "bg-muted" };
  return <span className={cn("inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap", s.cls)}>{s.label}</span>;
}

const STATUS: Record<string, string> = {
  draft: "bg-muted text-muted-foreground", active: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
  paused: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400",
};
export function IntegrationStatus({ status }: { status: string }) {
  return <span className={cn("inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize", STATUS[status])}>{status}</span>;
}

const STEPS = ["records", "mapping", "notifications", "documents", "offer", "schedule", "review"];
export function StepBar({ step, className }: { step: string; className?: string }) {
  const at = STEPS.indexOf(step);
  return (
    <div className={cn("flex gap-0.5", className)} title={`Setup: ${step}`}>
      {STEPS.map((s, i) => <span key={s} className={cn("h-1 flex-1 rounded-full", i <= at ? "bg-primary" : "bg-muted")} />)}
    </div>
  );
}

const RUN_STATUS: Record<string, { label: string; cls: string }> = {
  running: { label: "Running", cls: "bg-amber-50 text-amber-700" }, succeeded: { label: "Succeeded", cls: "bg-emerald-50 text-emerald-700" },
  partial: { label: "Partly failed", cls: "bg-amber-50 text-amber-700" }, failed: { label: "Failed", cls: "bg-rose-50 text-rose-700" },
};
export function RunStatus({ status }: { status: string }) {
  const s = RUN_STATUS[status] ?? { label: status, cls: "bg-muted" };
  return <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", s.cls)}>{s.label}</span>;
}

/** Approve / Reject (with a required reason) for a candidate waiting on HR. */
export function DecisionButtons({ integrationId, itemId, name, onDone }: { integrationId: string; itemId: string; name: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      await api(`/api/integrations/${integrationId}/candidates/${itemId}/decision`, { method: "POST", json: { approve, reason: approve ? null : reason } });
      toast.success(approve ? `Approved ${name} — the next run sends the offer and creates the employee` : `Rejected documents for ${name}`);
      setOpen(false);
      onDone();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex gap-1.5">
      <Button size="sm" onClick={() => decide(true)} disabled={busy}><Check className="size-3.5" /> Approve</Button>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)} disabled={busy}><X className="size-3.5" /> Reject</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject documents for {name}</DialogTitle>
            <DialogDescription>The reason is saved with this decision and in the audit log.</DialogDescription>
          </DialogHeader>
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} rows={4} placeholder="Reason" />
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={() => decide(false)} disabled={busy || !reason.trim()}>Reject documents</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
