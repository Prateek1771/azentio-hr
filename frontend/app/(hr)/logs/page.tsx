"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { api, fmtTs, type AuditEntry, type IntegrationSummary } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { IntegrationLog } from "@/components/integration-log";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function LogsPage() {
  const [integrations, setIntegrations] = useState<IntegrationSummary[]>([]);
  const [filter, setFilter] = useState("");
  useEffect(() => { api<IntegrationSummary[]>("/api/integrations").then(setIntegrations).catch(() => {}); }, []);
  return (
    <>
      <PageHeader title="Logs" subtitle="Every integration run and every change HR or the agent made. Values excluded in an integration's log settings are never shown." />
      <Tabs defaultValue="integration">
        <TabsList>
          <TabsTrigger value="integration">Integration log</TabsTrigger>
          <TabsTrigger value="audit">Audit log</TabsTrigger>
        </TabsList>
        <TabsContent value="integration" className="mt-4 space-y-3">
          <SimpleSelect value={filter} onChange={setFilter} placeholder="All integrations" className="w-64"
            options={integrations.map((i) => ({ value: i.id, label: i.name }))} />
          <IntegrationLog key={filter} integrationId={filter || undefined} showName={!filter} />
        </TabsContent>
        <TabsContent value="audit" className="mt-4"><AuditLog /></TabsContent>
      </Tabs>
    </>
  );
}

const PAGE = 25;

function AuditLog() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ items: AuditEntry[]; total: number } | null>(null);
  useEffect(() => {
    api<{ items: AuditEntry[]; total: number }>(`/api/logs/audit?q=${encodeURIComponent(query)}&page=${page}&page_size=${PAGE}`).then(setData);
  }, [query, page]);
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  return (
    <div className="space-y-3">
      <form onSubmit={(e) => { e.preventDefault(); setPage(1); setQuery(q); }} className="relative max-w-sm">
        <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search what happened" className="pl-8" />
      </form>
      {!data ? <Skeleton className="h-48 rounded-xl" /> : data.items.length === 0 ? (
        <Card className="items-center p-12 text-center text-sm text-muted-foreground">No audit entries{query && " match that search"}.</Card>
      ) : (
        <Card className="gap-0 overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
                <tr><th className="px-4 py-2.5 font-medium">When</th><th className="px-4 font-medium">Who</th><th className="px-4 font-medium">What happened</th><th className="px-4 font-medium">Event</th></tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id} className="border-t">
                    <td className="px-4 py-2.5 text-xs whitespace-nowrap text-muted-foreground">{fmtTs(a.ts)}</td>
                    <td className="px-4 text-xs">{a.actor}</td>
                    <td className="px-4">{a.summary}</td>
                    <td className="px-4"><code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{a.action}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground">
          Page {page} of {pages}
          <Button size="icon" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous"><ChevronLeft className="size-4" /></Button>
          <Button size="icon" variant="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next"><ChevronRight className="size-4" /></Button>
        </div>
      )}
    </div>
  );
}
