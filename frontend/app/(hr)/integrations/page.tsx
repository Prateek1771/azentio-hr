"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, Plus } from "lucide-react";
import { api, describeSchedule, fmtTs, type IntegrationSummary } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { IntegrationStatus, StepBar } from "@/components/integration-bits";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function IntegrationsPage() {
  const router = useRouter();
  const [list, setList] = useState<IntegrationSummary[] | null>(null);
  useEffect(() => { api<IntegrationSummary[]>("/api/integrations").then(setList).catch(() => setList([])); }, []);

  return (
    <>
      <PageHeader title="Integrations" subtitle="Each integration moves candidates from one source to one target: documents, HR approval, offer letter, then the employee record."
        actions={<Button nativeButton={false} render={<Link href="/integrations/new" />}><Plus className="size-4" /> New integration</Button>} />
      {list === null ? <Skeleton className="h-64 rounded-xl" /> : list.length === 0 ? (
        <Card className="items-center gap-2 p-14 text-center">
          <div className="font-semibold">No integrations yet</div>
          <p className="text-sm text-muted-foreground">Describe what you need and the agent sets it up with you.</p>
          <Button className="mt-2" nativeButton={false} render={<Link href="/integrations/new" />}><Plus className="size-4" /> New integration</Button>
        </Card>
      ) : (
        <Card className="gap-0 overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
                <tr><th className="px-4 py-2.5 font-medium">Integration</th><th className="px-4 font-medium">Data flow</th><th className="px-4 font-medium">Schedule</th>
                  <th className="px-4 font-medium">Next run</th><th className="px-4 font-medium">Last run</th><th className="px-4 font-medium">Status</th></tr>
              </thead>
              <tbody>
                {list.map((it) => (
                  <tr key={it.id} onClick={() => router.push(`/integrations/${it.id}`)} className="cursor-pointer border-t hover:bg-muted/40">
                    <td className="px-4 py-3">
                      <div className="font-medium">{it.name}</div>
                      {it.description && <div className="max-w-sm truncate text-xs text-muted-foreground">{it.description}</div>}
                    </td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap">
                      <span className="font-medium text-source">{it.source_name}</span> <ArrowRight className="inline size-3 text-muted-foreground" /> <span className="font-medium text-target">{it.target_name}</span>
                    </td>
                    <td className="px-4 py-3 text-xs">{describeSchedule(it.schedule)}</td>
                    <td className="px-4 py-3 text-xs">{it.status === "active" ? fmtTs(it.next_run_at) : "—"}</td>
                    <td className="px-4 py-3 text-xs">{fmtTs(it.last_run_at)}</td>
                    <td className="px-4 py-3">
                      <IntegrationStatus status={it.status} />
                      {it.status === "draft" && <StepBar step={it.setup_step} className="mt-1.5 w-24" />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
