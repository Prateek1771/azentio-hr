"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import { BookOpen, Building2, CheckCircle2, FileSpreadsheet, ListChecks, UploadCloud, Users, XCircle } from "lucide-react";
import { API } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { QuickOnboard } from "@/components/quick-onboard";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface Result { kind: string; total: number; imported: number; errors: { row: number; field: string; message: string; name: string }[] }

const KINDS = [
  { kind: "employees", title: "New-joiner profiles", Icon: Users, accept: ".csv,.xlsx,.xls,.json",
    hint: "name, email, role, grade, department, location, manager_email, joining_date (+ optional date_of_birth)" },
  { kind: "owners", title: "Department owners & managers", Icon: Building2, accept: ".csv,.xlsx,.xls,.json",
    hint: "name, email, department, type (manager | owner:HR | owner:IT | owner:Facilities | owner:Compliance)" },
  { kind: "requirements", title: "Onboarding requirements", Icon: ListChecks, accept: ".json,.csv,.xlsx",
    hint: "Rules by role, department, location and grade, plus document types (JSON like seed/requirements.json)" },
  { kind: "policies", title: "Policies & process guides", Icon: BookOpen, accept: ".md,.txt",
    hint: "Markdown or text. Indexed for the employee assistant, which cites them in answers." },
] as const;

export default function ImportPage() {
  const [results, setResults] = useState<Record<string, Result>>({});
  return (
    <>
      <PageHeader title="Add joiners" subtitle="Onboard one candidate in a single step, or bulk-import CSV, Excel or JSON with row-by-row validation." />
      <QuickOnboard />
      <h2 className="mt-8 mb-3 text-sm font-semibold text-muted-foreground">Bulk import</h2>
      <div className="grid gap-4 md:grid-cols-2">
        {KINDS.map((k) => <Dropzone key={k.kind} {...k} result={results[k.kind]} onResult={(r) => setResults((p) => ({ ...p, [k.kind]: r }))} />)}
      </div>
    </>
  );
}

function Dropzone({ kind, title, Icon, accept, hint, result, onResult }: (typeof KINDS)[number] & { result?: Result; onResult: (r: Result) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);

  const upload = async (file: File) => {
    setBusy(true);
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file);
    try {
      const res = await fetch(`${API}/api/ingest`, { method: "POST", body: fd });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? res.statusText);
      onResult(body);
      if (body.errors.length) toast.warning(`${body.imported}/${body.total} imported from ${file.name}`, { description: `${body.errors.length} validation issue(s) — see the table.` });
      else toast.success(`${body.imported} record(s) imported from ${file.name}`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="gap-3 p-5">
      <div className="flex items-center gap-3">
        <div className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary"><Icon className="size-4" /></div>
        <div><h3 className="text-sm font-semibold">{title}</h3><p className="text-xs text-muted-foreground">{accept.replaceAll(",", " ")}</p></div>
      </div>
      <button
        onClick={() => ref.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) upload(f); }}
        className={cn("flex flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors",
          drag ? "border-primary bg-primary/5" : "border-border hover:border-primary/50 hover:bg-muted/40")}>
        {busy ? <FileSpreadsheet className="size-6 animate-pulse text-primary" /> : <UploadCloud className="size-6 text-muted-foreground" />}
        <span className="text-sm font-medium">{busy ? "Validating…" : "Drop a file or click to browse"}</span>
        <span className="text-xs text-muted-foreground">{hint}</span>
      </button>
      <input ref={ref} type="file" accept={accept} hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
      {result && (
        <div className="space-y-2">
          <div className="flex items-center gap-3 text-sm">
            <span className="flex items-center gap-1 text-emerald-600"><CheckCircle2 className="size-4" /> {result.imported} imported</span>
            {result.errors.length > 0 && <span className="flex items-center gap-1 text-rose-600"><XCircle className="size-4" /> {result.errors.length} issue(s)</span>}
            <span className="text-muted-foreground">of {result.total} rows</span>
          </div>
          {result.errors.length > 0 && (
            <div className="max-h-56 overflow-auto rounded-lg border">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-muted text-left text-muted-foreground"><tr><th className="px-2 py-1.5">Row</th><th>Name</th><th>Field</th><th>Problem</th></tr></thead>
                <tbody>{result.errors.map((e, i) => (
                  <tr key={i} className="border-t"><td className="px-2 py-1.5 tabular-nums">{e.row}</td><td>{e.name}</td><td className="font-mono">{e.field}</td><td className="text-rose-600">{e.message}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
