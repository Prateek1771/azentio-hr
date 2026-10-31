"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, EyeOff } from "lucide-react";
import { api, fmtTs, type LogRecord, type LogRun } from "@/lib/api";
import { RunStatus, StageBadge } from "@/components/integration-bits";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const PAGE = 15;
const OUTCOME = {
  advanced: { label: "Moved on", cls: "bg-emerald-50 text-emerald-700" },
  waiting: { label: "Waiting", cls: "bg-muted text-muted-foreground" },
  failed: { label: "Failed", cls: "bg-rose-50 text-rose-700" },
} as const;

/** Every run: what it fetched and did, and — expanded — each candidate's steps and attribute mapping. */
export function IntegrationLog({ integrationId, showName = false, refreshKey = 0 }: { integrationId?: string; showName?: boolean; refreshKey?: number }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ items: LogRun[]; total: number } | null>(null);
  const load = useCallback(() => {
    api<{ items: LogRun[]; total: number }>(`/api/logs/integration?integration_id=${integrationId ?? ""}&page=${page}&page_size=${PAGE}`).then(setData);
  }, [integrationId, page]);
  useEffect(load, [load, refreshKey]);

  if (!data) return <Skeleton className="h-48 rounded-xl" />;
  if (!data.items.length) return (
    <Card className="items-center p-12 text-center">
      <div className="font-medium">No runs logged yet</div>
      <p className="text-sm text-muted-foreground">Each run appears here with what it fetched and did, once an active integration starts.</p>
    </Card>
  );
  const pages = Math.ceil(data.total / PAGE);
  return (
    <div className="space-y-3">
      {data.items.map((r) => <RunCard key={r.id} run={r} showName={showName} />)}
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

function Count({ label, value, danger }: { label: string; value: number | null; danger?: boolean }) {
  return (
    <div className="min-w-24">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      {value === null ? (
        <span title="Excluded from this log" className="inline-flex items-center gap-1 text-sm text-muted-foreground"><EyeOff className="size-3.5" /> —</span>
      ) : <div className={cn("text-sm font-semibold tabular-nums", danger && value > 0 && "text-rose-600")}>{value}</div>}
    </div>
  );
}

function RunCard({ run, showName }: { run: LogRun; showName: boolean }) {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<LogRecord[] | null>(null);
  const toggle = () => {
    setOpen((o) => !o);
    if (!records) api<LogRecord[]>(`/api/logs/integration/${run.id}/records`).then(setRecords);
  };
  const excluded = [...run.excluded_sections, ...run.excluded_fields];
  return (
    <Card className="gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        {showName && <Link href={`/integrations/${run.integration_id}`} className="font-medium hover:underline">{run.integration_name}</Link>}
        <RunStatus status={run.status} />
        <span className="text-xs text-muted-foreground">{fmtTs(run.started)}, {run.trigger === "scheduled" ? "on schedule" : "Run now"}</span>
        <button onClick={toggle} className="ml-auto flex items-center gap-1 text-xs font-medium text-primary hover:underline">
          {open ? "Hide candidates" : `Show ${run.advanced_count + run.waiting_count + run.failed_count} candidates`}
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
        </button>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        <Count label="Fetched" value={run.fetched_count} />
        <Count label="New" value={run.enrolled_count} />
        <Count label="Documents requested" value={run.documents_requested_count} />
        <Count label="Documents verified" value={run.documents_verified_count} />
        <Count label="Awaiting approval" value={run.approvals_pending_count} />
        <Count label="Approvals applied" value={run.approvals_used_count} />
        <Count label="Offers sent" value={run.offers_sent_count} />
        <Count label="Employees created" value={run.employees_created_count} />
        <Count label="Employees updated" value={run.employees_updated_count} />
        <Count label="Failed" value={run.failed_count} danger />
      </div>
      {open && (
        <div className="space-y-2 border-t pt-3">
          {excluded.length > 0 && (
            <div className="flex items-center gap-1.5 rounded-md bg-muted/60 px-3 py-1.5 text-xs text-muted-foreground">
              <EyeOff className="size-3.5" /> Left out of this log: {excluded.join(", ")}
            </div>
          )}
          {records === null ? <Skeleton className="h-16" /> : records.length === 0 ? (
            <p className="text-sm text-muted-foreground">No candidates were processed in this run.</p>
          ) : records.map((rec) => <CandidateRow key={rec.id} rec={rec} />)}
        </div>
      )}
    </Card>
  );
}

function CandidateRow({ rec }: { rec: LogRecord }) {
  const [open, setOpen] = useState(false);
  const o = OUTCOME[rec.outcome];
  const steps = rec.error ? <span className="text-rose-600">{rec.error}</span>
    : rec.actions.length ? rec.actions.map((a) => a.message).join(". ") : <span className="text-muted-foreground">No visible steps</span>;
  return (
    <div className="rounded-lg border">
      <button onClick={() => setOpen((x) => !x)} className="flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-muted/40">
        <ChevronRight className={cn("mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} />
        <span className="font-medium">{rec.candidate_name}</span>
        <span className="min-w-0 flex-1 text-xs text-muted-foreground">{steps}</span>
        <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", o.cls)}>{o.label}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t px-3 py-2 text-xs">
          <div className="flex items-center gap-2">
            {rec.stage_before === rec.stage_after ? <>Stayed at <StageBadge stage={rec.stage_after} /></>
              : <><StageBadge stage={rec.stage_before} /> → <StageBadge stage={rec.stage_after} /></>}
          </div>
          {rec.mapping_hidden ? <p className="text-muted-foreground">Attribute mapping is excluded from this integration&apos;s log.</p>
            : rec.mapping.length === 0 ? <p className="text-muted-foreground">No mapping was recorded for this candidate.</p> : (
              <table className="w-full">
                <thead className="text-left text-muted-foreground"><tr><th className="py-1 font-normal">From</th><th className="font-normal">To</th><th className="font-normal">Value</th></tr></thead>
                <tbody>
                  {rec.mapping.map((m) => (
                    <tr key={m.target_field} className="border-t">
                      <td className="py-1 font-mono">{m.source_field ?? <span className="font-sans text-muted-foreground">Default</span>}</td>
                      <td className="font-mono">{m.target_field}</td>
                      <td>{m.excluded ? <span className="inline-flex items-center gap-1 text-muted-foreground"><EyeOff className="size-3" /> Excluded</span>
                        : m.value === null ? <span className="text-muted-foreground">Empty</span>
                          : <>{m.value}{m.from_default && <span className="ml-1 text-muted-foreground">(default)</span>}</>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      )}
    </div>
  );
}
