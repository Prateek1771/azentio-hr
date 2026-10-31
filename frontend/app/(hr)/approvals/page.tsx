"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { FileText, Inbox } from "lucide-react";
import { api, timeAgo } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { DecisionButtons } from "@/components/integration-bits";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type Approval =
  | { kind: "candidate_documents"; integration_id: string; integration_name: string; item_id: string; candidate_name: string; joiner_id: string | null;
      document_results: { name: string; status: string; reason?: string | null }[]; waiting_since: number | null }
  | { kind: "offer_format"; integration_id: string; integration_name: string; offer_template: string; waiting_since: number | null };

export default function ApprovalsPage() {
  const [rows, setRows] = useState<Approval[] | null>(null);
  const load = useCallback(() => { api<Approval[]>("/api/approvals").then(setRows).catch(() => setRows([])); }, []);
  useEffect(load, [load]);

  return (
    <>
      <PageHeader title="Approval Center" subtitle="Everything waiting on an HR decision: candidates whose documents passed AI checks, and offer letter formats." />
      {rows === null ? <Skeleton className="h-48 rounded-xl" /> : rows.length === 0 ? (
        <Card className="items-center gap-2 p-14 text-center">
          <Inbox className="size-8 text-muted-foreground" />
          <div className="font-semibold">Nothing waiting on you</div>
          <p className="text-sm text-muted-foreground">Candidates appear here once their documents pass the AI checks.</p>
        </Card>
      ) : (
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
              <DecisionButtons integrationId={a.integration_id} itemId={a.item_id} name={a.candidate_name} onDone={load} />
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
      )}
    </>
  );
}
