"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, CheckCircle2, CircleSlash, Play, RefreshCw, XCircle } from "lucide-react";
import { api, timeAgo, type Connector, type FieldSpec, type IntegrationRun } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { RoleBadge } from "@/components/status";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface MappingResp { source_schema: FieldSpec[]; target_schema: FieldSpec[]; mapping: Record<string, string> }
const STATUSES = ["applied", "interviewing", "offer_accepted", "hired", "rejected"];
const OUTCOME = {
  created: { cls: "text-emerald-600", Icon: CheckCircle2 }, updated: { cls: "text-sky-600", Icon: RefreshCw },
  skipped: { cls: "text-zinc-500", Icon: CircleSlash }, rejected: { cls: "text-rose-600", Icon: XCircle },
} as const;

export default function IntegrationsPage() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [m, setM] = useState<MappingResp | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [policy, setPolicy] = useState("update");
  const [statuses, setStatuses] = useState<string[]>(["offer_accepted", "hired"]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<IntegrationRun | null>(null);
  const [runs, setRuns] = useState<IntegrationRun[]>([]);

  useEffect(() => {
    api<Connector[]>("/api/connectors").then((cs) => {
      setConnectors(cs);
      setSource(cs.find((c) => c.key === "candidate_portal")?.id ?? cs.find((c) => c.role === "source")?.id ?? "");
      setTarget(cs.find((c) => c.role === "target")?.id ?? "");
    });
    api<IntegrationRun[]>("/api/integrations/runs").then(setRuns);
  }, []);

  useEffect(() => {
    if (!source || !target) return;
    api<MappingResp>(`/api/integrations/mapping?source=${source}&target=${target}`).then((r) => { setM(r); setMapping(r.mapping); });
  }, [source, target]);

  const missing = m?.target_schema.filter((f) => f.required && !f.generated && !mapping[f.key]) ?? [];
  const src = connectors.find((c) => c.id === source);
  const tgt = connectors.find((c) => c.id === target);

  const run = async () => {
    setRunning(true);
    setResult(null);
    try {
      const r = await api<IntegrationRun>("/api/integrations/run", { method: "POST", json: {
        source_id: source, target_id: target, mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)),
        duplicate_policy: policy, statuses: statuses.length ? statuses : null,
      } });
      setResult(r);
      setRuns((p) => [r, ...p]);
      toast.success(`Sync finished: ${r.counts.created} created, ${r.counts.updated} updated, ${r.counts.skipped} skipped, ${r.counts.rejected} rejected`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <PageHeader title="Integrations" subtitle="Move records from a source connector into a target. Runs are safe to repeat — matching records are updated or skipped, never duplicated." />

      <Card className="gap-5 p-5">
        <div className="grid items-end gap-3 md:grid-cols-[1fr_auto_1fr]">
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">From <RoleBadge role="source" /></div>
            <SimpleSelect value={source} onChange={setSource} placeholder="Choose source" className="w-full"
              options={connectors.filter((c) => c.role === "source").map((c) => ({ value: c.id, label: `${c.name} (${c.record_count})` }))} />
          </div>
          <ArrowRight className="mb-2 hidden size-5 text-muted-foreground md:block" />
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">To <RoleBadge role="target" /></div>
            <SimpleSelect value={target} onChange={setTarget} placeholder="Choose target" className="w-full"
              options={connectors.filter((c) => c.role === "target").map((c) => ({ value: c.id, label: `${c.name} (${c.record_count})` }))} />
          </div>
        </div>

        {src?.record_kind === "candidate" && (
          <div className="space-y-1.5">
            <div className="text-xs font-medium text-muted-foreground">Only candidates with status</div>
            <div className="flex flex-wrap gap-1.5">
              {STATUSES.map((s) => {
                const on = statuses.includes(s);
                return (
                  <button key={s} onClick={() => setStatuses(on ? statuses.filter((x) => x !== s) : [...statuses, s])}
                    className={cn("rounded-full border px-3 py-1 text-xs transition-colors", on ? "border-source bg-source-soft font-medium text-source" : "hover:bg-muted")}>
                    {s.replace("_", " ")}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <h3 className="text-sm font-semibold">Field mapping</h3>
            <span className="text-xs text-muted-foreground">Suggested automatically — adjust any row</span>
          </div>
          {!m ? <Skeleton className="h-64" /> : (
            <div className="overflow-hidden rounded-lg border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-left text-xs">
                  <tr>
                    <th className="px-3 py-2 font-medium text-target">{tgt?.name} field</th>
                    <th className="w-8" />
                    <th className="px-3 py-2 font-medium text-source">{src?.name} field</th>
                  </tr>
                </thead>
                <tbody>
                  {m.target_schema.map((f) => (
                    <tr key={f.key} className="border-t">
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium">{f.label}</span>
                          {f.required && !f.generated && <span className="text-xs text-rose-600">required</span>}
                          {f.generated && <span className="rounded bg-target-soft px-1 text-[10px] font-medium text-target">auto if unmapped</span>}
                          {f.is_custom && <span className="rounded bg-primary/10 px-1 text-[10px] font-medium text-primary">custom</span>}
                        </div>
                        <div className="font-mono text-[11px] text-muted-foreground">{f.key}</div>
                      </td>
                      <td className="text-center text-muted-foreground">←</td>
                      <td className="px-3 py-2">
                        <SimpleSelect value={mapping[f.key] ?? ""} onChange={(v) => setMapping((p) => ({ ...p, [f.key]: v }))}
                          placeholder={f.generated ? "Generate automatically" : "Not mapped"} className="w-full max-w-xs"
                          options={m.source_schema.map((s) => ({ value: s.key, label: `${s.label} (${s.key})` }))} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t pt-4">
          <span className="text-sm text-muted-foreground">If an employee with the same work email exists:</span>
          <SimpleSelect value={policy} onChange={(v) => setPolicy(v || "update")}
            options={[{ value: "update", label: "Update them" }, { value: "skip", label: "Leave them unchanged" }]} />
          <div className="ml-auto flex items-center gap-3">
            {missing.length > 0 && <span className="text-xs text-rose-600">Map {missing.map((f) => f.label).join(", ")} first</span>}
            <Button onClick={run} disabled={running || !source || !target || missing.length > 0}>
              <Play className="size-4" /> {running ? "Running…" : "Run sync"}
            </Button>
          </div>
        </div>
      </Card>

      {result && <RunResult run={result} />}

      <Card className="mt-6 gap-0 overflow-hidden p-0">
        <div className="border-b px-4 py-3 text-sm font-semibold">Run history</div>
        {runs.length === 0 ? <p className="px-4 py-10 text-center text-sm text-muted-foreground">No runs yet.</p> : runs.map((r) => (
          <details key={r.id} className="border-b last:border-0">
            <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-sm hover:bg-muted/40">
              <span className="font-medium"><span className="text-source">{r.source_name}</span> → <span className="text-target">{r.target_name}</span></span>
              <span className="text-xs text-muted-foreground">{r.policy} · {timeAgo(r.ts)}</span>
              <span className="ml-auto flex gap-3 text-xs">
                {(Object.keys(OUTCOME) as (keyof typeof OUTCOME)[]).map((k) => (
                  <span key={k} className={OUTCOME[k].cls}>{r.counts[k]} {k}</span>
                ))}
              </span>
            </summary>
            <div className="px-4 pb-3"><ResultList run={r} /></div>
          </details>
        ))}
      </Card>
    </>
  );
}

function RunResult({ run }: { run: IntegrationRun }) {
  return (
    <Card className="mt-4 gap-3 p-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(Object.keys(OUTCOME) as (keyof typeof OUTCOME)[]).map((k) => {
          const { cls, Icon } = OUTCOME[k];
          return (
            <div key={k} className="rounded-lg border p-3">
              <div className={cn("flex items-center gap-1.5 text-xs font-medium capitalize", cls)}><Icon className="size-3.5" /> {k}</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{run.counts[k]}</div>
            </div>
          );
        })}
      </div>
      <ResultList run={run} />
    </Card>
  );
}

function ResultList({ run }: { run: IntegrationRun }) {
  return (
    <ul className="divide-y rounded-lg border text-sm">
      {run.results.map((r, i) => {
        const o = OUTCOME[r.outcome as keyof typeof OUTCOME] ?? OUTCOME.skipped;
        return (
          <li key={i} className="flex items-start gap-2 px-3 py-2">
            <o.Icon className={cn("mt-0.5 size-4 shrink-0", o.cls)} />
            <span className="font-medium">{r.record}</span>
            <span className={cn("text-xs capitalize", o.cls)}>{r.outcome}</span>
            {r.reason && <span className="text-xs text-muted-foreground">— {r.reason}</span>}
          </li>
        );
      })}
      {run.results.length === 0 && <li className="px-3 py-2 text-muted-foreground">No records matched.</li>}
    </ul>
  );
}
