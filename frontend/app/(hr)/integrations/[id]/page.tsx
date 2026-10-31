"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, ChevronRight, ExternalLink, KeyRound, Mail, Pause, Play, Plus, RefreshCw } from "lucide-react";
import { api, describeSchedule, fmtTs, timeAgo, type IntegrationDetail, type OutboundMessage, type WfCandidate } from "@/lib/api";
import { DecisionButtons, IntegrationStatus, StageBadge } from "@/components/integration-bits";
import { IntegrationChat } from "@/components/integration-chat";
import { IntegrationLog } from "@/components/integration-log";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/simple-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

interface RunResult { counts: Record<string, number>; status: string }

export default function IntegrationPage() {
  const { id } = useParams<{ id: string }>();
  const [it, setIt] = useState<IntegrationDetail | null>(null);
  const [tab, setTab] = useState("agent");
  const [banner, setBanner] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [refresh, setRefresh] = useState(0);

  const load = useCallback(() => { api<IntegrationDetail>(`/api/integrations/${id}`).then(setIt).catch((e) => toast.error(e.message)); }, [id]);
  useEffect(load, [load]);

  const lifecycle = async (action: "pause" | "activate") => {
    try { setIt(await api<IntegrationDetail>(`/api/integrations/${id}/${action}`, { method: "POST" })); } catch (e) { toast.error((e as Error).message); }
  };
  const runNow = async () => {
    setRunning(true);
    try {
      const r = await api<RunResult>(`/api/integrations/${id}/run`, { method: "POST" });
      const c = r.counts;
      setBanner(`Run finished: ${c.enrolled} new candidate(s), ${c.advanced} advanced, ${c.waiting} waiting, ${c.failed} failed.`);
      setTab("candidates");
      setRefresh((x) => x + 1);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  if (!it) return <div className="space-y-4"><Skeleton className="h-20 rounded-xl" /><Skeleton className="h-[500px] rounded-xl" /></div>;
  return (
    <>
      <Link href="/integrations" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Integrations
      </Link>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight">{it.name}</h1>
            <IntegrationStatus status={it.status} />
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            <span className="text-source">{it.source_name}</span> to <span className="text-target">{it.target_name}</span>
. {describeSchedule(it.schedule)}{it.status === "active" && it.next_run_at ? `, next at ${fmtTs(it.next_run_at)}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          {it.status === "active" && <Button variant="outline" onClick={() => lifecycle("pause")}><Pause className="size-4" /> Pause</Button>}
          {it.status === "paused" && <Button variant="outline" onClick={() => lifecycle("activate")}><Play className="size-4" /> Resume</Button>}
          {it.status === "active" && <Button onClick={runNow} disabled={running}><RefreshCw className={cn("size-4", running && "animate-spin")} /> {running ? "Running" : "Run now"}</Button>}
        </div>
      </div>
      {banner && <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">{banner}</div>}

      <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
        <TabsList>
          <TabsTrigger value="agent">Agent</TabsTrigger>
          <TabsTrigger value="candidates">Candidates</TabsTrigger>
          <TabsTrigger value="log">Log</TabsTrigger>
          <TabsTrigger value="messages">Sent messages</TabsTrigger>
        </TabsList>
        <TabsContent value="agent" className="mt-4"><IntegrationChat it={it} onChange={(d) => { setIt(d); setRefresh((x) => x + 1); }} /></TabsContent>
        <TabsContent value="candidates" className="mt-4"><CandidatesTab it={it} refreshKey={refresh} /></TabsContent>
        <TabsContent value="log" className="mt-4"><IntegrationLog integrationId={it.id} refreshKey={refresh} /></TabsContent>
        <TabsContent value="messages" className="mt-4"><MessagesTab id={it.id} refreshKey={refresh} /></TabsContent>
      </Tabs>
    </>
  );
}

function CandidatesTab({ it, refreshKey }: { it: IntegrationDetail; refreshKey: number }) {
  const [rows, setRows] = useState<WfCandidate[] | null>(null);
  const [granting, setGranting] = useState<WfCandidate | null>(null);
  const load = useCallback(() => { api<WfCandidate[]>(`/api/integrations/${it.id}/candidates`).then(setRows); }, [it.id]);
  useEffect(load, [load, refreshKey]);
  const check = async (c: WfCandidate) => {
    try {
      const r = await api<{ result: string }>(`/api/integrations/${it.id}/candidates/${c.id}/check-documents`, { method: "POST" });
      toast.info(r.result === "applied" ? "Document verdict applied" : r.result === "waiting" ? "Still waiting on documents" : "Nothing to update");
      load();
    } catch (e) { toast.error((e as Error).message); }
  };
  if (!rows) return <Skeleton className="h-48 rounded-xl" />;
  if (!rows.length) return (
    <Card className="items-center p-12 text-center">
      <div className="font-medium">No candidates picked up yet</div>
      <p className="text-sm text-muted-foreground">Matching candidates join this integration on its next run.</p>
    </Card>
  );
  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
            <tr><th className="px-4 py-2.5 font-medium">Candidate</th><th className="px-4 font-medium">Progress</th><th className="px-4 font-medium">Documents (AI checks)</th><th className="px-4 font-medium">App access</th><th className="px-4 font-medium">Action</th></tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="border-t align-top">
                <td className="px-4 py-3">
                  {c.joiner_id ? <Link href={`/employees/${c.joiner_id}`} className="font-medium hover:underline">{c.name}</Link> : <span className="font-medium">{c.name}</span>}
                  <div className="text-xs text-muted-foreground">{c.email}</div>
                </td>
                <td className="px-4 py-3">
                  <StageBadge stage={c.stage} />
                  {c.last_error && <div className="mt-1 max-w-xs text-xs text-rose-600">{c.last_error}</div>}
                  {c.rejection_reason && <div className="mt-1 max-w-xs text-xs text-muted-foreground">Reason: {c.rejection_reason}</div>}
                </td>
                <td className="px-4 py-3 text-xs">
                  {c.document_results.length ? c.document_results.map((d) => (
                    <div key={d.name} className="flex gap-1">
                      <span className={cn("font-medium", d.status === "verified" ? "text-emerald-600" : d.status === "rejected" ? "text-rose-600" : "text-muted-foreground")}>{d.status}</span>
                      <span className="truncate">{d.name}</span>
                    </div>
                  )) : <span className="text-muted-foreground">—</span>}
                </td>
                <td className="px-4 py-3 text-xs">
                  {c.access.map((a) => (
                    <div key={a.id} title={`${a.message} · ${a.reference}`} className="flex gap-1">
                      <span className={cn("font-medium", a.status === "requested" ? "text-amber-600" : "text-emerald-600")}>{a.app}</span>
                      <span className="truncate text-muted-foreground">{a.action.replace(/_/g, " ")}{a.resource && ` ${a.resource}`}{a.status === "requested" && " (asked admin)"}</span>
                    </div>
                  ))}
                  <button onClick={() => setGranting(c)} className="mt-1 inline-flex items-center gap-1 text-primary hover:underline"><Plus className="size-3" /> Grant access</button>
                </td>
                <td className="px-4 py-3">
                  {c.stage === "awaiting_hr_approval" && <DecisionButtons integrationId={it.id} itemId={c.id} name={c.name} onDone={load} />}
                  {c.stage === "documents_requested" && <Button size="sm" variant="outline" onClick={() => check(c)}><RefreshCw className="size-3.5" /> Check documents</Button>}
                  {c.stage === "completed" && c.employee_record_id && (
                    <Button size="sm" variant="ghost" nativeButton={false} render={<Link href={`/connectors/${it.target_id}`} />}><ExternalLink className="size-3.5" /> View employee</Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {granting && <GrantDialog it={it} cand={granting} onClose={() => setGranting(null)} onDone={() => { setGranting(null); load(); }} />}
    </Card>
  );
}

function GrantDialog({ it, cand, onClose, onDone }: { it: IntegrationDetail; cand: WfCandidate; onClose: () => void; onDone: () => void }) {
  const [appId, setAppId] = useState(it.apps[0]?.id ?? "");
  const app = it.apps.find((a) => a.id === appId);
  const [action, setAction] = useState(app?.actions[0]?.key ?? "");
  const [resource, setResource] = useState("");
  const [mode, setMode] = useState("direct");
  const [busy, setBusy] = useState(false);
  const needs = app?.actions.find((a) => a.key === action)?.needs_resource;
  const grant = async () => {
    setBusy(true);
    try {
      const r = await api<{ result: { message: string; reference: string } }>(`/api/integrations/${it.id}/candidates/${cand.id}/access`,
        { method: "POST", json: { app_id: appId, action, resource, mode } });
      toast.success(`${r.result.message} (${r.result.reference})`);
      onDone();
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><KeyRound className="size-4" /> Grant access to {cand.name}</DialogTitle>
          <DialogDescription>Runs now in the app connector (demo: recorded, no external call). Uses their generated company email once it exists.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          <SimpleSelect value={appId} onChange={(v) => { const a = it.apps.find((x) => x.id === v); setAppId(v); setAction(a?.actions[0]?.key ?? ""); }}
            className="w-full" options={it.apps.map((a) => ({ value: a.id, label: a.name }))} />
          <SimpleSelect value={action} onChange={setAction} className="w-full" options={(app?.actions ?? []).map((a) => ({ value: a.key, label: a.label }))} />
          {needs && <Input value={resource} onChange={(e) => setResource(e.target.value)}
            placeholder={app?.key === "slack" ? "#channel" : app?.key === "jira" ? "Project key, e.g. ENG" : app?.key === "servicenow" ? "What to request, e.g. Laptop" : "Group"} />}
          {(app?.key === "slack" || app?.key === "jira") && needs && (
            <SimpleSelect value={mode} onChange={(v) => setMode(v || "direct")} className="w-full"
              options={[{ value: "direct", label: "Add them directly" }, { value: "request", label: "Ask the channel/project admin" }]} />
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={grant} disabled={busy || !action || (needs && !resource.trim())}>{busy ? "Granting…" : "Grant access"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const KIND = { document_request: "Document request", offer_letter: "Offer letter", notification: "Notification email" } as const;

function MessagesTab({ id, refreshKey }: { id: string; refreshKey: number }) {
  const [rows, setRows] = useState<OutboundMessage[] | null>(null);
  useEffect(() => { api<OutboundMessage[]>(`/api/integrations/${id}/messages`).then(setRows); }, [id, refreshKey]);
  if (!rows) return <Skeleton className="h-40 rounded-xl" />;
  if (!rows.length) return (
    <Card className="items-center p-12 text-center">
      <div className="font-medium">Nothing sent yet</div>
      <p className="text-sm text-muted-foreground">Document requests, offer letters and notifications are kept here exactly as they were sent.</p>
    </Card>
  );
  return (
    <Card className="gap-0 overflow-hidden p-0">
      {rows.map((m) => (
        <details key={m.id} className="border-b last:border-0">
          <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-4 py-3 text-sm hover:bg-muted/40">
            <ChevronRight className="size-4 text-muted-foreground" />
            <Mail className="size-4 text-primary" />
            <span className="font-medium">{KIND[m.kind]}</span>
            <span className="text-muted-foreground">to {m.candidate || m.to}</span>
            {m.attempt > 1 && <span className="text-xs text-muted-foreground">attempt {m.attempt}</span>}
            <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", m.delivered_by === "gmail" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700")}>
              {m.delivered_by === "gmail" ? "Sent via Gmail" : "Simulated, not sent"}</span>
            <span className="ml-auto text-xs text-muted-foreground">{timeAgo(m.ts)}</span>
          </summary>
          <div className="space-y-1 px-4 pb-3 text-xs">
            <div><span className="text-muted-foreground">To:</span> {m.to} · <span className="text-muted-foreground">Subject:</span> {m.subject}</div>
            <div className="rounded-md border bg-white p-3 text-zinc-800 [&_p]:my-1" dangerouslySetInnerHTML={{ __html: m.content }} />
          </div>
        </details>
      ))}
    </Card>
  );
}
