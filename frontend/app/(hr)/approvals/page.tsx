"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Check, FileText, GitPullRequest, Inbox, UserCheck, X } from "lucide-react";
import { api, fmtDate, fmtWhen, timeAgo, type ImpactSummary, type RuleSetVersion, type VersionDetail, type VersionsResp, type WorkItem } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { DecisionButtons } from "@/components/integration-bits";
import { ImpactPreview } from "@/components/impact-preview";
import { BacktestSummary, loosens, PolicyDiffView } from "@/components/policy-diff";
import { can, useMe } from "@/components/auth";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type Approval =
  | { kind: "candidate_documents"; integration_id: string; integration_name: string; item_id: string; candidate_name: string; joiner_id: string | null;
      document_results: { name: string; status: string; reason?: string | null }[]; waiting_since: number | null }
  | { kind: "offer_format"; integration_id: string; integration_name: string; offer_template: string; waiting_since: number | null };

const TABS = ["policy", "near_start", "integrations"] as const;
type Tab = (typeof TABS)[number];

export default function ApprovalsPage() {
  const { me } = useMe();
  const [tab, setTab] = useState<Tab>(() => {
    if (typeof window === "undefined") return "policy";
    const t = new URLSearchParams(window.location.search).get("tab");
    return (TABS as readonly string[]).includes(t ?? "") ? (t as Tab) : me?.role === "hr_ops" ? "near_start" : "policy";
  });
  const [counts, setCounts] = useState<Record<Tab, number | null>>({ policy: null, near_start: null, integrations: null });
  const setCount = useCallback((t: Tab) => (n: number) => setCounts((c) => (c[t] === n ? c : { ...c, [t]: n })), []);

  const badge = (n: number | null) => n ? <span className="ml-1.5 rounded-full bg-primary/15 px-1.5 text-[10px] font-semibold text-primary">{n}</span> : null;
  return (
    <>
      <PageHeader title="Approval Center"
        subtitle="Everything waiting on a human decision: rule changes, joiners a rule change would affect right before they start, and candidates from integrations." />
      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <TabsList>
          <TabsTrigger value="policy"><GitPullRequest className="size-3.5" /> Policy changes{badge(counts.policy)}</TabsTrigger>
          <TabsTrigger value="near_start"><UserCheck className="size-3.5" /> Joiners needing review{badge(counts.near_start)}</TabsTrigger>
          <TabsTrigger value="integrations"><Inbox className="size-3.5" /> Integrations{badge(counts.integrations)}</TabsTrigger>
        </TabsList>
        <TabsContent value="policy" className="mt-4"><PolicyApprovals onCount={setCount("policy")} /></TabsContent>
        <TabsContent value="near_start" className="mt-4"><NearStartQueue onCount={setCount("near_start")} /></TabsContent>
        <TabsContent value="integrations" className="mt-4"><IntegrationApprovals onCount={setCount("integrations")} /></TabsContent>
      </Tabs>
    </>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return (
    <Card className="items-center gap-2 p-14 text-center">
      <Inbox className="size-8 text-muted-foreground" />
      <div className="font-semibold">{title}</div>
      <p className="max-w-md text-sm text-muted-foreground">{text}</p>
    </Card>
  );
}

// ---------- policy changes ----------

function PolicyApprovals({ onCount }: { onCount: (n: number) => void }) {
  const [items, setItems] = useState<VersionDetail[] | null>(null);
  const load = useCallback(() => {
    api<VersionsResp>("/api/policy/versions")
      .then((vs) => Promise.all(vs.versions.filter((v) => v.state === "pending_approval" || v.state === "approved")
        .map((v) => api<VersionDetail>(`/api/policy/versions/${v.id}`))))
      .then((d) => { setItems(d); onCount(d.filter((v) => v.state === "pending_approval").length); })
      .catch(() => setItems([]));
  }, [onCount]);
  useEffect(load, [load]);
  if (!items) return <Skeleton className="h-48 rounded-xl" />;
  if (!items.length) return <Empty title="No rule changes waiting" text="When HR Ops submits a draft rule set, it appears here with its impact on every joiner." />;
  return <div className="space-y-4">{items.map((v) => <PolicyCard key={v.id} v={v} onDone={load} />)}</div>;
}

function PolicyCard({ v, onDone }: { v: VersionDetail; onDone: () => void }) {
  const { me } = useMe();
  const [showImpact, setShowImpact] = useState(false);
  const [live, setLive] = useState<ImpactSummary | null>(null);
  const [decide, setDecide] = useState<"approve" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const approvals = v.approvals.filter((a) => a.decision === "approve");
  const mine = v.approvals.find((a) => a.email === me?.email);
  const isAuthor = !!me && me.email === v.author_email;
  const canDecide = can(me, "approve") && !isAuthor && !mine && v.state === "pending_approval";
  const IMPACT: (keyof ImpactSummary)[] = ["joiners", "added", "cancelled", "kept_as_evidence", "due_moved", "newly_blocked", "near_start"];
  const drifted = live && v.preview && IMPACT.some((k) => v.preview![k] !== live[k]);
  const loosening = (v.diff.doc_policy ?? []).some(loosens);

  const submit = async () => {
    if (!decide) return;
    setBusy(true);
    try {
      const out = await api<RuleSetVersion>(`/api/policy/versions/${v.id}/${decide}`, { method: "POST", json: { reason } });
      toast.success(decide === "reject" ? `v${v.number} rejected` : out.state === "approved" ? `v${v.number} approved and publishing now` : `Approval recorded (${approvals.length + 1} of ${v.required_approvals})`);
      setDecide(null);
      setReason("");
      onDone();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="gap-3 p-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><GitPullRequest className="size-4" /></div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="font-semibold">Rule set v{v.number}</span>
            <span className="text-sm">{v.title}</span>
            {v.rollback_of && <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-800 dark:bg-violet-500/15 dark:text-violet-300">Rollback</span>}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">By {v.author_email ?? "system"} · submitted {fmtWhen(v.submitted_at)}</div>
          {v.reason && <p className="mt-2 text-sm">{v.reason}</p>}
        </div>
        <div className="text-right">
          <div className={cn("text-sm font-medium", v.state === "approved" ? "text-sky-700 dark:text-sky-400" : "text-amber-700 dark:text-amber-400")}>
            {v.state === "approved" ? "Approved · publishing" : `${approvals.length} of ${v.required_approvals} approval${v.required_approvals > 1 ? "s" : ""}`}</div>
          {approvals.length > 0 && <div className="text-xs text-muted-foreground">{approvals.map((a) => a.email).join(", ")}</div>}
          {v.required_approvals > 1 && <div className="text-[11px] text-muted-foreground">{loosening ? "Loosens a document control" : "Touches a compliance rule"}</div>}
        </div>
      </div>

      {v.preview && <div className="rounded-lg bg-muted/40 p-3 text-xs">
        Impact when submitted: <b>{v.preview.joiners}</b> joiners · {v.preview.added} added · {v.preview.cancelled} cancelled ·{" "}
        {v.preview.kept_as_evidence} kept as evidence · {v.preview.due_moved} due dates moved · {v.preview.near_start} held for HR review
      </div>}
      {drifted && <p className="flex items-center gap-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
        <AlertTriangle className="size-4" /> The impact has changed since this was submitted (joiners moved). Check the live preview below.</p>}

      <PolicyDiffView diff={v.diff} />
      {v.preview?.backtest && <BacktestSummary bt={v.preview.backtest} />}

      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        <Button size="sm" variant="ghost" onClick={() => setShowImpact((s) => !s)}>{showImpact ? "Hide" : "Show"} live impact</Button>
        <div className="flex-1" />
        {can(me, "approve") && v.state === "pending_approval" && <>
          <span title={isAuthor ? "You authored this" : mine ? "You've already decided" : undefined}>
            <Button size="sm" variant="outline" disabled={!canDecide} onClick={() => setDecide("reject")}><X className="size-3.5" /> Reject</Button></span>
          <span title={isAuthor ? "You authored this" : mine ? "You've already decided" : undefined}>
            <Button size="sm" disabled={!canDecide} onClick={() => setDecide("approve")}><Check className="size-3.5" /> Approve</Button></span>
        </>}
        {!can(me, "approve") && v.state === "pending_approval" && <span className="text-xs text-muted-foreground">Waiting on an approver</span>}
      </div>
      {showImpact && <ImpactPreview versionId={v.id} onSummary={setLive} />}

      <Dialog open={!!decide} onOpenChange={(o) => !o && setDecide(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{decide === "approve" ? `Approve rule set v${v.number}?` : `Reject rule set v${v.number}?`}</DialogTitle>
            <DialogDescription>{decide === "approve"
              ? (approvals.length + 1 >= v.required_approvals ? "This is the last approval needed. Publishing starts straight away; joiners starting soon go to HR review instead of changing." : `Your approval is ${approvals.length + 1} of ${v.required_approvals}.`)
              : "The draft is closed. HR Ops can start a new one."} Your reason is kept in the audit log.</DialogDescription>
          </DialogHeader>
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={2000} placeholder={decide === "reject" ? "Reason (required)" : "Reason (optional)"} />
          <DialogFooter><Button variant="outline" onClick={() => setDecide(null)}>Cancel</Button>
            <Button variant={decide === "reject" ? "destructive" : "default"} onClick={submit} disabled={busy || (decide === "reject" && reason.trim().length < 3)}>
              {decide === "approve" ? "Approve" : "Reject"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// ---------- near-start joiners ----------

function NearStartQueue({ onCount }: { onCount: (n: number) => void }) {
  const { me } = useMe();
  const [items, setItems] = useState<WorkItem[] | null>(null);
  const [act, setAct] = useState<{ item: WorkItem; action: "apply" | "skip" } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api<WorkItem[]>("/api/work-queue?kind=policy.near_start").then((d) => { setItems(d); onCount(d.length); }).catch(() => setItems([]));
  }, [onCount]);
  useEffect(load, [load]);

  const resolve = async () => {
    if (!act) return;
    setBusy(true);
    try {
      await api(`/api/work-queue/${act.item.id}/resolve`, { method: "POST", json: { resolution: reason, action: act.action } });
      toast.success(act.action === "apply" ? "Changes applied to the joiner's checklist" : "Skipped: their checklist stays as it is");
      setAct(null);
      setReason("");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!items) return <Skeleton className="h-48 rounded-xl" />;
  if (!items.length) return <Empty title="No joiners waiting on review"
    text="When a published rule change would alter the checklist of someone starting within a few days, it waits here instead of changing automatically." />;
  return (
    <div className="space-y-3">
      {items.map((w) => {
        const d = w.detail as { employee_name?: string; version?: number; changes?: string[] };
        return (
          <Card key={w.id} className="gap-2 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <UserCheck className="size-4 text-amber-600" />
              <Link href={`/employees/${w.entity_id}`} className="font-medium hover:underline">{d.employee_name ?? w.entity_id}</Link>
              <span className="text-xs text-muted-foreground">rule set v{d.version} · decide by {w.sla_due ? fmtDate(w.sla_due.slice(0, 10)) : "—"}</span>
              <div className="flex-1" />
              {can(me, "onboarding") ? <>
                <Button size="sm" variant="outline" onClick={() => setAct({ item: w, action: "skip" })}>Skip</Button>
                <Button size="sm" onClick={() => setAct({ item: w, action: "apply" })}>Apply changes</Button>
              </> : <span className="text-xs text-muted-foreground">HR Ops decides</span>}
            </div>
            <p className="text-sm text-muted-foreground">{w.reason}</p>
            {d.changes && d.changes.length > 0 && <ul className="grid gap-0.5 text-xs sm:grid-cols-2">{d.changes.map((c) => <li key={c}>• {c}</li>)}</ul>}
          </Card>
        );
      })}
      <Dialog open={!!act} onOpenChange={(o) => !o && setAct(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{act?.action === "apply" ? "Apply the rule change now?" : "Skip the rule change for this joiner?"}</DialogTitle>
            <DialogDescription>{act?.action === "apply"
              ? "Their checklist is updated to the active rule set. Completed items and documents are kept."
              : "Their checklist stays as it is. They'll start under the rules they had."} The reason goes in the audit log.</DialogDescription>
          </DialogHeader>
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={2000} placeholder="Reason (required)" />
          <DialogFooter><Button variant="outline" onClick={() => setAct(null)}>Cancel</Button>
            <Button onClick={resolve} disabled={busy || reason.trim().length < 3}>{act?.action === "apply" ? "Apply changes" : "Skip"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------- integration candidates (unchanged behaviour) ----------

function IntegrationApprovals({ onCount }: { onCount: (n: number) => void }) {
  const [rows, setRows] = useState<Approval[] | null>(null);
  const { me } = useMe();
  const canDecide = can(me, "approve");
  const load = useCallback(() => {
    api<Approval[]>("/api/approvals").then((r) => { setRows(r); onCount(r.length); }).catch(() => setRows([]));
  }, [onCount]);
  useEffect(load, [load]);

  if (rows === null) return <Skeleton className="h-48 rounded-xl" />;
  if (rows.length === 0) return <Empty title="Nothing waiting from integrations" text="Candidates appear here once their documents pass the AI checks." />;
  return (
    <div className="space-y-3">
      {rows.map((a) => a.kind === "candidate_documents" ? (
        <Card key={a.item_id} className="flex-row flex-wrap items-center gap-4 p-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              {a.joiner_id ? <Link href={`/employees/${a.joiner_id}`} className="font-medium hover:underline">{a.candidate_name}</Link> : <span className="font-medium">{a.candidate_name}</span>}
              <span className="text-xs text-muted-foreground">via <Link href={`/integrations/${a.integration_id}`} className="hover:underline">{a.integration_name}</Link>
                {a.waiting_since ? ` · waiting ${timeAgo(a.waiting_since)}` : ""}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {a.document_results.map((d) => (
                <span key={d.name} title={d.reason ?? undefined} className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium",
                  d.status === "verified" ? "bg-emerald-50 text-emerald-700" : d.status === "rejected" ? "bg-rose-50 text-rose-700" : "bg-muted text-muted-foreground")}>
                  {d.name}: {d.status}</span>
              ))}
            </div>
          </div>
          {canDecide ? <DecisionButtons integrationId={a.integration_id} itemId={a.item_id} name={a.candidate_name} onDone={load} />
            : <span className="text-xs text-muted-foreground">Waiting on an approver</span>}
        </Card>
      ) : (
        <Card key={`offer-${a.integration_id}`} className="flex-row flex-wrap items-center gap-4 p-4">
          <FileText className="size-5 text-primary" />
          <div className="min-w-0 flex-1">
            <div className="font-medium">Offer letter format for {a.integration_name}</div>
            <p className="truncate text-xs text-muted-foreground">{a.offer_template.slice(0, 140)}</p>
          </div>
          <Button size="sm" variant="outline" nativeButton={false} render={<Link href={`/integrations/${a.integration_id}`} />}>Review format</Button>
        </Card>
      ))}
    </div>
  );
}
