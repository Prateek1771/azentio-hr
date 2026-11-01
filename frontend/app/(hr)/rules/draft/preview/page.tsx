"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Send, ShieldAlert } from "lucide-react";
import { api, type ImpactSummary, type PolicyPreview, type VersionDetail, type VersionsResp } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { RoleGate } from "@/components/auth";
import { ImpactPreview } from "@/components/impact-preview";
import { BacktestSummary, diffCount, loosens, PolicyDiffView } from "@/components/policy-diff";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

export default function PreviewPage() {
  return <RoleGate roles={["hr_ops"]}><Preview /></RoleGate>;
}

function Preview() {
  const router = useRouter();
  const [draft, setDraft] = useState<VersionDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [summary, setSummary] = useState<ImpactSummary | null>(null);
  const [title, setTitle] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [backtest, setBacktest] = useState<PolicyPreview["backtest"]>(null);

  useEffect(() => {
    api<VersionsResp>("/api/policy/versions").then((vs) => {
      if (!vs.open_id) return setMissing(true);
      api<PolicyPreview>(`/api/policy/versions/${vs.open_id}/preview`).then((p) => setBacktest(p.backtest ?? null)).catch(() => {});
      return api<VersionDetail>(`/api/policy/versions/${vs.open_id}`).then((d) => (d.state === "draft" ? setDraft(d) : setMissing(true)));
    }).catch((e) => toast.error((e as Error).message));
  }, []);

  if (missing) return (
    <Card className="items-center gap-2 p-14 text-center">
      <div className="font-semibold">No draft to preview</div>
      <Button variant="outline" nativeButton={false} render={<Link href="/rules" />}>Back to rules</Button>
    </Card>
  );
  if (!draft) return <div className="space-y-3"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-96 rounded-xl" /></div>;

  const compliance = [...draft.diff.added, ...draft.diff.removed].some((r) => r.compliance)
    || draft.diff.changed.some((c) => draft.rules.find((r) => r.id === c.id)?.compliance || "compliance" in c.fields);
  const loosening = (draft.diff.doc_policy ?? []).some(loosens);

  const submit = async () => {
    setBusy(true);
    try {
      const v = await api<VersionDetail>(`/api/policy/drafts/${draft.id}/submit`, { method: "POST", json: { title, reason } });
      toast.success(`v${v.number} sent for approval`, { description: `Needs ${v.required_approvals} approval${v.required_approvals > 1 ? "s" : ""} from someone other than you.` });
      router.push("/rules/history");
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <>
      <Link href="/rules/draft" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Back to draft v{draft.number}</Link>
      <PageHeader title={`Impact of draft v${draft.number}`}
        subtitle="What publishing this draft would do to every joiner still onboarding. Completed work and documents are never removed." />

      <ImpactPreview versionId={draft.id} onSummary={setSummary} />

      <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_24rem]">
        <Card className="gap-3 p-4">
          <div className="text-sm font-semibold">{diffCount(draft.diff)} rule change{diffCount(draft.diff) === 1 ? "" : "s"}</div>
          <PolicyDiffView diff={draft.diff} />
          {backtest && <BacktestSummary bt={backtest} />}
        </Card>
        <Card className="h-fit gap-3 p-4">
          <div className="text-sm font-semibold">Submit for approval</div>
          {loosening && !compliance && <p className="flex gap-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
            <ShieldAlert className="size-4 shrink-0" /> This loosens a document control (lower threshold, tier change or fewer spot checks), so two different approvers must sign off.</p>}
          {compliance && <p className="flex gap-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
            <ShieldAlert className="size-4 shrink-0" /> This touches a compliance rule, so two different approvers must sign off.</p>}
          <div className="space-y-1.5"><Label htmlFor="sub-title">Title</Label>
            <Input id="sub-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="e.g. PAN deadline moves to 7 days before joining" /></div>
          <div className="space-y-1.5"><Label htmlFor="sub-reason">Why this change</Label>
            <Textarea id="sub-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={4} maxLength={2000}
              placeholder="The approver sees this with the impact above. It's kept in the audit log." /></div>
          {summary && summary.joiners > 0 && <p className="text-xs text-muted-foreground">Affects {summary.joiners} joiner{summary.joiners === 1 ? "" : "s"}
            {summary.near_start > 0 && <>; {summary.near_start} starting soon will go to HR review instead of changing automatically</>}.</p>}
          <Button onClick={submit} disabled={busy || title.trim().length < 3 || reason.trim().length < 3}><Send className="size-4" /> Submit for approval</Button>
        </Card>
      </div>
    </>
  );
}
