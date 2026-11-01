"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BotOff, CheckCircle2, ClipboardCheck, FileSearch, Inbox, ScanSearch } from "lucide-react";
import { api, fmtWhen, type ReviewItem, type ReviewQueueResp } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { ReasonChip, SlaCountdown, TierBadge, docTypeLabel } from "@/components/review-bits";
import { SimpleSelect } from "@/components/simple-select";
import { StatusPill } from "@/components/status";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const TABS = { review: { kind: "doc.review", status: "open" }, spot: { kind: "doc.spot_check", status: "open" },
               done: { kind: "doc.review", status: "resolved" } } as const;
type Tab = keyof typeof TABS;
const REASONS = [["high_risk", "High risk"], ["low_confidence", "Low confidence"], ["ai_unavailable", "AI unavailable"],
                 ["unexpected_type", "Unexpected type"], ["spot_check_failed", "Spot check failed"]] as const;

export default function ReviewQueuePage() {
  const [tab, setTab] = useState<Tab>("review");
  const [reason, setReason] = useState("");
  const [assignee, setAssignee] = useState("all");
  const [data, setData] = useState<Record<Tab, ReviewQueueResp | null>>({ review: null, spot: null, done: null });

  const load = useCallback((t: Tab) => {
    const p = new URLSearchParams({ kind: TABS[t].kind, status: TABS[t].status, assignee, reason: t === "review" ? reason : "" });
    api<ReviewQueueResp>(`/api/review-queue?${p}`).then((r) => setData((d) => ({ ...d, [t]: r }))).catch(() => {});
  }, [assignee, reason]);
  useEffect(() => { (Object.keys(TABS) as Tab[]).forEach(load); }, [load]);
  useEffect(() => {  // a queue is live work: refresh quietly
    const t = setInterval(() => load(tab), 15000);
    return () => clearInterval(t);
  }, [tab, load]);

  const r = data.review;
  const badge = (n?: number) => n ? <span className="ml-1.5 rounded-full bg-primary/15 px-1.5 text-[10px] font-semibold text-primary">{n}</span> : null;
  return (
    <>
      <PageHeader title="Document review"
        subtitle="Documents the policy routes to a person: high-risk types always, low-risk ones below their threshold, and a random sample of automatic acceptances." />
      {r?.llm_unavailable && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          <BotOff className="mt-0.5 size-4 shrink-0" />
          <div><b>AI extraction unavailable:</b> new documents are coming to review instead of being checked automatically.
            <div className="text-xs opacity-80">{r.llm_unavailable}</div></div>
        </div>
      )}
      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <div className="flex flex-wrap items-center gap-2">
          <TabsList>
            <TabsTrigger value="review"><FileSearch className="size-3.5" /> Needs review{badge(data.review?.items.length)}</TabsTrigger>
            <TabsTrigger value="spot"><ScanSearch className="size-3.5" /> Spot checks{badge(data.spot?.items.length)}</TabsTrigger>
            <TabsTrigger value="done"><CheckCircle2 className="size-3.5" /> Done (7 days)</TabsTrigger>
          </TabsList>
          <div className="flex-1" />
          {tab === "review" && <SimpleSelect value={reason} onChange={setReason} placeholder="Any reason" options={REASONS.map(([v, l]) => ({ value: v, label: l }))} />}
          {tab !== "done" && <SimpleSelect value={assignee === "all" ? "" : assignee} onChange={(v) => setAssignee(v || "all")} placeholder="Anyone"
            options={[{ value: "me", label: "Assigned to me" }, { value: "unassigned", label: "Unassigned" }]} />}
        </div>
        {(Object.keys(TABS) as Tab[]).map((t) => (
          <TabsContent key={t} value={t} className="mt-4"><Queue tab={t} data={data[t]} /></TabsContent>
        ))}
      </Tabs>
    </>
  );
}

function Queue({ tab, data }: { tab: Tab; data: ReviewQueueResp | null }) {
  const router = useRouter();
  if (!data) return <Card className="gap-0 p-0">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="m-3 h-12" />)}</Card>;
  if (!data.items.length) return (
    <Card className="items-center gap-2 p-14 text-center">
      {tab === "done" ? <ClipboardCheck className="size-8 text-muted-foreground" /> : <Inbox className="size-8 text-muted-foreground" />}
      <div className="font-semibold">{tab === "review" ? "Nothing to review" : tab === "spot" ? "No spot checks waiting" : "Nothing decided this week"}</div>
      <p className="max-w-md text-sm text-muted-foreground">
        {tab === "review" ? `Low-risk documents are being accepted automatically (${data.auto_accepted_this_week} this week). High-risk ones will appear here.`
          : tab === "spot" ? "A random 10% of automatic acceptances land here so the AI's precision is measured, not assumed."
            : "Decided reviews from the last 7 days show here."}
      </p>
    </Card>
  );
  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
            <tr><th className="px-4 py-2.5 font-medium">Joiner · document</th><th className="px-3 font-medium">Risk</th>
              <th className="px-3 font-medium">Why it&apos;s here</th><th className="px-3 font-medium">Received</th>
              <th className="px-3 font-medium">{tab === "done" ? "Outcome" : "SLA"}</th><th className="px-3 font-medium">Reviewer</th></tr>
          </thead>
          <tbody>
            {data.items.map((it) => <Row key={it.id} it={it} tab={tab} onOpen={() => it.document && router.push(`/review/${it.document.id}`)} />)}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Row({ it, tab, onOpen }: { it: ReviewItem; tab: Tab; onOpen: () => void }) {
  const d = it.document;
  const code = String(it.detail.reason_code ?? (it.kind === "doc.spot_check" ? "spot_check" : ""));
  return (
    <tr onClick={onOpen} className="cursor-pointer border-t transition-colors hover:bg-muted/30">
      <td className="px-4 py-3">
        <Link href={d ? `/review/${d.id}` : "#"} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>{d?.employee_name ?? it.entity_id}</Link>
        <div className="text-xs text-muted-foreground">{docTypeLabel(d?.doc_type ?? "")} · {d?.filename}</div>
      </td>
      <td className="px-3"><TierBadge tier={d?.tier} /></td>
      <td className="px-3"><ReasonChip code={code} label={it.reason} /></td>
      <td className="px-3 text-xs whitespace-nowrap text-muted-foreground">{fmtWhen(d?.received_at ?? it.created_at)}</td>
      <td className="px-3">{tab === "done" ? (d && <StatusPill status={d.status} />) : <SlaCountdown due={it.sla_due} />}</td>
      <td className="px-3 text-xs">
        {it.assignee ? <span className={cn("inline-flex items-center gap-1.5", it.status === "in_progress" && "font-medium")}>
          <span className="grid size-5 place-items-center rounded-full bg-primary/15 text-[10px] font-semibold text-primary uppercase">{it.assignee[0]}</span>
          {it.assignee.split("@")[0]}</span> : <span className="text-muted-foreground">Unassigned</span>}
        <div className="text-[10px] text-muted-foreground" title={it.steps}>escalates to {it.escalate_to}</div>
      </td>
    </tr>
  );
}
