"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Database, RefreshCw, TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { RoleGate } from "@/components/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type LabelRow = {
  label: string; batch: string; table: string; source: "graph" | "postgres"; switched_at: string | null;
  graph_count: number | null; pg_count: number | null; parity_ok: boolean | null; unmapped_keys: string[] | null;
  edges: { edge: string; graph: number; pg: number; ok: boolean }[] | null;
  before: { graph_count: number; pg_count: number } | null; cutover_at: string | null; drift_since_cutover: boolean | null;
};
type Report = { labels: LabelRow[]; batches: string[]; projection_types: Record<string, string> };

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

export default function SystemPage() {
  return <RoleGate roles={["admin", "auditor"]}><System /></RoleGate>;
}

function System() {
  const [rep, setRep] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<Report>("/api/admin/migration").then((r) => { setRep(r); setError(""); }).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);

  const drift = rep?.labels.filter((l) => l.drift_since_cutover === false) ?? [];
  return (
    <>
      <PageHeader title="System" subtitle="Where each kind of record is stored, and proof that the move to Postgres lost nothing."
        actions={<Button variant="outline" size="sm" onClick={load}><RefreshCw className="size-4" /> Re-check</Button>} />
      <Card className="p-5">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Database className="size-5 text-muted-foreground" />
          <h2 className="text-base font-semibold">Storage migration</h2>
          {rep && (drift.length
            ? <Badge variant="destructive"><TriangleAlert className="size-3.5" /> Graph changed after cutover: {drift.map((d) => d.label).join(", ")}</Badge>
            : <Badge variant="secondary"><CheckCircle2 className="size-3.5" /> No drift since cutover</Badge>)}
        </div>
        {rep && (
          <p className="mb-4 text-sm text-muted-foreground">
            Postgres is the system of record. Already moved: {Object.entries(rep.projection_types).map(([t, s]) => `${t} (${s})`).join(", ")}.
          </p>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!rep && !error && <Skeleton className="h-32 w-full" />}
        {rep && rep.labels.length === 0 && (
          <p className="text-sm text-muted-foreground">No graph record batches are registered yet. Each batch appears here when its cutover is deployed.</p>
        )}
        {rep && rep.labels.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Record</TableHead><TableHead>Batch</TableHead><TableHead>Read from</TableHead>
                <TableHead className="text-right">Graph</TableHead><TableHead className="text-right">Postgres</TableHead>
                <TableHead>Parity</TableHead><TableHead>Cutover</TableHead><TableHead>Since cutover</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rep.labels.map((l) => (
                <TableRow key={l.label}>
                  <TableCell className="font-medium">{l.label}<div className="text-xs text-muted-foreground">{l.table}</div></TableCell>
                  <TableCell>{l.batch}</TableCell>
                  <TableCell><Badge variant={l.source === "postgres" ? "default" : "outline"}>{l.source}</Badge></TableCell>
                  <TableCell className="text-right tabular-nums">{l.graph_count ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{l.pg_count ?? "—"}</TableCell>
                  <TableCell>
                    {l.parity_ok == null ? "—" : l.parity_ok ? "Match" : "Mismatch"}
                    {!!l.edges?.length && <div className="text-xs text-muted-foreground">{l.edges.map((e) => `${e.edge} ${e.graph}/${e.pg}`).join(", ")}</div>}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{when(l.cutover_at)}</TableCell>
                  <TableCell>{l.drift_since_cutover == null ? "—" : l.drift_since_cutover ? "Unchanged" : <span className="text-destructive">Changed</span>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
