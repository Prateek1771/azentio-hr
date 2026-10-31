"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, ExternalLink, FileSpreadsheet, KeyRound, Loader2, Plug, Plus, Rocket, Search, UploadCloud } from "lucide-react";
import { API, api, fmtDate, fmtTs, timeAgo, type ConnRecord, type ConnectionSettings, type Connector, type FieldSpec, type Template } from "@/lib/api";
import { ConnectionBadge, RoleBadge } from "@/components/status";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const PAGE = 20;
const COLUMNS: Record<string, string[]> = {
  candidate: ["candidate_id", "first_name", "last_name", "email", "designation", "location", "joining_date", "status"],
  employee: ["employee_number", "first_name", "last_name", "work_email", "job_title", "department_name", "start_date", "employment_status"],
  activity: ["action", "person", "email", "resource", "mode", "status", "reference", "message"],
};
const STATUS_CLS: Record<string, string> = {
  hired: "bg-emerald-50 text-emerald-700", offer_accepted: "bg-sky-50 text-sky-700", interviewing: "bg-violet-50 text-violet-700",
  applied: "bg-zinc-100 text-zinc-700", rejected: "bg-rose-50 text-rose-700", active: "bg-emerald-50 text-emerald-700",
  added: "bg-emerald-50 text-emerald-700", created: "bg-emerald-50 text-emerald-700", invited: "bg-emerald-50 text-emerald-700",
  posted: "bg-sky-50 text-sky-700", open: "bg-sky-50 text-sky-700", requested: "bg-amber-50 text-amber-700",
  on_leave: "bg-amber-50 text-amber-700", terminated: "bg-rose-50 text-rose-700",
};

const show = (v: unknown) => Array.isArray(v) ? v.join(", ") : v === null || v === undefined || v === "" ? "—" : String(v);

function Cell({ k, v }: { k: string; v: unknown }) {
  if ((k === "status" || k === "employment_status") && v) {
    return <span className={cn("rounded-md px-1.5 py-0.5 text-[11px] font-medium", STATUS_CLS[String(v)] ?? "bg-muted")}>{String(v).replace("_", " ")}</span>;
  }
  if ((k.endsWith("_date") || k === "date_of_birth") && v) return <>{fmtDate(String(v))}</>;
  return <>{show(v)}</>;
}

export default function ConnectorPage() {
  const { id } = useParams<{ id: string }>();
  const [c, setC] = useState<Connector | null>(null);
  const [schema, setSchema] = useState<FieldSpec[]>([]);
  const [sample, setSample] = useState<Record<string, unknown>>({});
  const [tab, setTab] = useState<string | null>(null);

  const loadMeta = useCallback(() => {
    api<Connector>(`/api/connectors/${id}`).then(setC).catch((e) => toast.error(e.message));
    api<FieldSpec[]>(`/api/connectors/${id}/schema`).then(setSchema);
    api<{ items: ConnRecord[] }>(`/api/connectors/${id}/records?page_size=1`).then((d) => setSample(d.items[0]?.values ?? {}));
  }, [id]);
  useEffect(loadMeta, [loadMeta]);

  if (!c) return <div className="space-y-4"><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-96 rounded-xl" /></div>;
  const isImport = c.key === "candidate_import";

  return (
    <>
      <Link href="/connectors" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Connectors
      </Link>
      <Card className={cn("mb-4 flex-row flex-wrap items-center gap-4 border-l-4 p-5", c.role === "source" ? "border-l-source" : c.role === "app" ? "border-l-violet-500" : "border-l-target")}>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight">{c.name}</h1>
            <RoleBadge role={c.role} />
            {c.demo && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground uppercase">Demo</span>}
            {!isImport && <ConnectionBadge status={c.connection_status} />}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{c.description}</p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums">{c.record_count}</div>
          <div className="text-xs text-muted-foreground">{{ candidate: "candidates", employee: "employees", activity: "actions" }[c.record_kind]} · {schema.length} fields</div>
        </div>
      </Card>

      <Tabs value={tab ?? (isImport && c.record_count === 0 ? "imports" : !isImport && c.connection_status === "not_configured" ? "connection" : "records")}
        onValueChange={(v) => setTab(String(v))}>
        <TabsList>
          <TabsTrigger value="records">{c.role === "app" ? "Activity" : "Records"}</TabsTrigger>
          {c.role !== "app" && <TabsTrigger value="fields">Fields</TabsTrigger>}
          {!isImport && <TabsTrigger value="connection">Connection</TabsTrigger>}
          {isImport && <TabsTrigger value="imports">Import history</TabsTrigger>}
        </TabsList>
        <TabsContent value="records" className="mt-4"><RecordsTab c={c} schema={schema} /></TabsContent>
        <TabsContent value="fields" className="mt-4"><FieldsTab c={c} schema={schema} sample={sample} onAdded={loadMeta} /></TabsContent>
        {!isImport && <TabsContent value="connection" className="mt-4"><ConnectionTab c={c} onSaved={loadMeta} /></TabsContent>}
        {isImport && <TabsContent value="imports" className="mt-4"><ImportsTab c={c} onImported={loadMeta} /></TabsContent>}
      </Tabs>
    </>
  );
}

function ConnectionTab({ c, onSaved }: { c: Connector; onSaved: () => void }) {
  const [cfg, setCfg] = useState<ConnectionSettings | null>(null);
  const [form, setForm] = useState({ tenant: "", auth_type: "token" as "basic" | "token", username: "", password: "", token: "" });
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  useEffect(() => {
    api<ConnectionSettings>(`/api/connectors/${c.id}/connection`).then((d) => {
      setCfg(d);
      setForm({ tenant: d.tenant, auth_type: d.auth_type, username: d.username, password: "", token: "" });
    });
  }, [c.id]);
  if (!cfg) return <Skeleton className="h-64 rounded-xl" />;
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const save = async () => {
    setBusy("save");
    try {
      const d = await api<ConnectionSettings>(`/api/connectors/${c.id}/connection`, { method: "PUT", json: form });
      setCfg(d); setForm((f) => ({ ...f, password: "", token: "" }));
      toast.success("Connection settings saved");
      onSaved();
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  };
  const test = async () => {
    setBusy("test");
    try {
      const r = await api<NonNullable<ConnectionSettings["last_test"]>>(`/api/connectors/${c.id}/connection/test`, { method: "POST" });
      setCfg((x) => x && { ...x, last_test: r });
      toast.success(r.message);
      onSaved();
    } catch (e) { toast.error((e as Error).message); } finally { setBusy(null); }
  };
  const secretKey = form.auth_type === "basic" ? "password" : "token";
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      <Card className="gap-4 p-5">
        <div className="flex items-center gap-2 font-semibold"><Plug className="size-4 text-primary" /> Connection to {c.name}</div>
        <div className="grid gap-1.5">
          <Label htmlFor="tenant">{cfg.tenant_label}</Label>
          <Input id="tenant" value={form.tenant} onChange={(e) => set("tenant", e.target.value)} placeholder={cfg.tenant_example} />
        </div>
        <div className="grid gap-1.5">
          <Label>Authentication</Label>
          <div className="inline-flex w-fit rounded-lg border p-0.5">
            {(["token", "basic"] as const).map((t) => (
              <button key={t} type="button" onClick={() => set("auth_type", t)}
                className={cn("rounded-md px-3 py-1 text-sm", form.auth_type === t ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
                {t === "token" ? "API token" : "Username & password"}</button>
            ))}
          </div>
        </div>
        {form.auth_type === "basic" && (
          <div className="grid gap-1.5">
            <Label htmlFor="username">Username</Label>
            <Input id="username" value={form.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" placeholder="integration.user@azentio.com" />
          </div>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor="secret">{form.auth_type === "basic" ? "Password" : "API token"}</Label>
          <Input id="secret" type="password" autoComplete="new-password" value={form[secretKey]} onChange={(e) => set(secretKey, e.target.value)}
            placeholder={cfg.secret_set && cfg.auth_type === form.auth_type ? `Saved (${cfg.secret_hint}). Leave blank to keep it` : "Paste it here"} />
          <p className="text-[11px] text-muted-foreground">Stored on the server and never shown again.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={save} disabled={!!busy || form.tenant.trim().length < 2}>{busy === "save" ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />} Save settings</Button>
          <Button variant="outline" onClick={test} disabled={!!busy || !cfg.secret_set}>{busy === "test" ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />} Test connection</Button>
        </div>
      </Card>
      <Card className="h-fit gap-2 p-4 text-sm">
        <div className="font-semibold">Status</div>
        {!cfg.secret_set ? <p className="text-muted-foreground">Not configured yet. Save the {cfg.tenant_label.toLowerCase()} and credentials, then test.</p> : (
          <>
            <div className="flex justify-between"><span className="text-muted-foreground">{cfg.tenant_label}</span><span className="font-medium">{cfg.tenant}</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">Auth</span><span>{cfg.auth_type === "basic" ? `${cfg.username} / ${cfg.secret_hint}` : `Token ${cfg.secret_hint}`}</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">Saved</span><span>{fmtTs(cfg.configured_at)}</span></div>
            {cfg.last_test ? (
              <div className="mt-1 rounded-md bg-emerald-50 px-2.5 py-2 text-xs text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">
                <CheckCircle2 className="mr-1 inline size-3.5" />{cfg.last_test.message} · {cfg.last_test.ms} ms · {timeAgo(cfg.last_test.at)}
              </div>
            ) : <p className="text-xs text-amber-700">Saved but not tested yet.</p>}
          </>
        )}
        {c.demo && <p className="mt-1 text-[11px] text-muted-foreground">Demo connector: the test always succeeds and no external call is made.</p>}
      </Card>
    </div>
  );
}

function RecordsTab({ c, schema }: { c: Connector; schema: FieldSpec[] }) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ items: ConnRecord[]; total: number } | null>(null);
  const [open, setOpen] = useState<ConnRecord | null>(null);
  const load = useCallback(() => {
    api<{ items: ConnRecord[]; total: number }>(`/api/connectors/${c.id}/records?page=${page}&page_size=${PAGE}&q=${encodeURIComponent(q)}`)
      .then(setData).catch((e) => toast.error(e.message));
  }, [c.id, page, q]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);

  const cols = COLUMNS[c.record_kind];
  const label = (k: string) => schema.find((f) => f.key === k)?.label ?? k;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;

  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="flex items-center gap-2 border-b p-3">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder={`Search ${c.record_kind}s`} className="pl-8" />
        </div>
        <span className="ml-auto text-xs text-muted-foreground">{data ? `${data.total} record${data.total === 1 ? "" : "s"}` : ""}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
            <tr>{cols.map((k) => <th key={k} className="px-4 py-2.5 font-medium whitespace-nowrap">{label(k)}</th>)}<th /></tr>
          </thead>
          <tbody>
            {data === null && <tr><td colSpan={cols.length + 1} className="p-4"><Skeleton className="h-40" /></td></tr>}
            {data?.items.length === 0 && (
              <tr><td colSpan={cols.length + 1} className="px-4 py-14 text-center text-muted-foreground">
                {q ? "No records match your search." : c.key === "candidate_import" ? "No candidates yet — upload a file in Import history." : "No records yet."}
              </td></tr>
            )}
            {data?.items.map((r) => (
              <tr key={r.id} onClick={() => setOpen(r)} className="cursor-pointer border-t hover:bg-muted/40">
                {cols.map((k) => <td key={k} className="px-4 py-2.5 whitespace-nowrap"><Cell k={k} v={r.values[k]} /></td>)}
                <td className="px-3 text-right">{r.employee_id && <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">Onboarding</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 border-t p-2 text-xs text-muted-foreground">
          Page {page} of {pages}
          <Button size="icon" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous"><ChevronLeft className="size-4" /></Button>
          <Button size="icon" variant="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next"><ChevronRight className="size-4" /></Button>
        </div>
      )}
      <RecordSheet c={c} schema={schema} record={open} onClose={() => setOpen(null)} onChanged={load} />
    </Card>
  );
}

function RecordSheet({ c, schema, record, onClose, onChanged }: { c: Connector; schema: FieldSpec[]; record: ConnRecord | null; onClose: () => void; onChanged: () => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [tid, setTid] = useState("standard");
  const [send, setSend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [joiner, setJoiner] = useState<string | null>(null);
  useEffect(() => {
    if (record && c.record_kind === "candidate") api<Template[]>("/api/templates").then((t) => setTemplates(t.filter((x) => !["correction", "escalation"].includes(x.id))));
  }, [record, c.record_kind]);

  const v = record?.values ?? {};
  const name = [v.first_name, v.last_name].filter(Boolean).join(" ");
  const linked = joiner ?? record?.employee_id ?? null;
  const custom = (v.custom ?? {}) as Record<string, unknown>;

  const start = async () => {
    if (!record) return;
    setBusy(true);
    try {
      const res = await fetch(`${API}/api/connectors/${c.id}/records/${record.id}/onboard`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ template_id: tid, send }),
      });
      const body = await res.json();
      if (!res.ok) {
        const errs = (body.detail?.errors ?? []) as { field: string; message: string }[];
        throw new Error(errs.length ? errs.map((e) => `${e.field}: ${e.message}`).join(" · ") : body.detail?.message ?? body.detail);
      }
      setJoiner(body.id);
      toast.success(`Onboarding started for ${name}`, { description: body.sent ? "Document request emailed." : body.email_error ?? "Checklist created — email not sent." });
      onChanged();
    } catch (e) {
      toast.error("Could not start onboarding", { description: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet open={!!record} onOpenChange={(o) => { if (!o) { onClose(); setJoiner(null); } }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{name || record?.id}</SheetTitle>
          <SheetDescription>{c.name} · {c.record_kind}</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-6">
          {c.record_kind === "candidate" && (
            <Card className="gap-3 bg-muted/30 p-4">
              {linked ? (
                <div className="flex items-center justify-between gap-2 text-sm">
                  <span>Onboarding is under way for this candidate.</span>
                  <Button size="sm" nativeButton={false} render={<Link href={`/employees/${linked}`} />}><ExternalLink className="size-4" /> Open joiner</Button>
                </div>
              ) : (
                <>
                  <div className="text-sm font-medium">Start onboarding</div>
                  <p className="-mt-2 text-xs text-muted-foreground">Creates the joiner with a {show(v.designation)} checklist for {show(v.location)}, joining {show(v.joining_date)}.</p>
                  <SimpleSelect value={tid} onChange={(x) => setTid(x || "standard")} className="w-full" placeholder="Template"
                    options={templates.map((t) => ({ value: t.id, label: t.name }))} />
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} className="size-4 accent-primary" />
                    Email the document request to {show(v.email)} now
                  </label>
                  <Button onClick={start} disabled={busy}><Rocket className="size-4" /> {busy ? "Starting…" : "Start onboarding"}</Button>
                </>
              )}
            </Card>
          )}
          {c.record_kind === "employee" && linked && (
            <Button variant="outline" size="sm" nativeButton={false} render={<Link href={`/employees/${linked}`} />}><ExternalLink className="size-4" /> Open onboarding record</Button>
          )}
          <dl className="grid gap-3 text-sm">
            {schema.map((f) => {
              const val = f.is_custom ? custom[f.key] : v[f.key];
              return (
                <div key={f.key} className="grid grid-cols-[140px_1fr] gap-2 border-b pb-2 last:border-0">
                  <dt className="text-xs text-muted-foreground">{f.label}{f.is_custom && <span className="ml-1 text-primary">· custom</span>}</dt>
                  <dd className="font-medium break-words">
                    {Array.isArray(val) ? <ul className="space-y-0.5">{val.map((x, i) => <li key={i}>• {String(x)}</li>)}</ul> : <Cell k={f.key} v={val} />}
                  </dd>
                </div>
              );
            })}
          </dl>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function FieldsTab({ c, schema, sample, onAdded }: { c: Connector; schema: FieldSpec[]; sample: Record<string, unknown>; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const custom = (sample.custom ?? {}) as Record<string, unknown>;
  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <span className="text-sm text-muted-foreground">{schema.length} fields · {schema.filter((f) => f.required).length} required</span>
        {c.key === "hr_portal" && <Button size="sm" onClick={() => setOpen(true)}><Plus className="size-4" /> Add field</Button>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
            <tr><th className="px-4 py-2.5 font-medium">Field</th><th className="px-4 font-medium">Key</th><th className="px-4 font-medium">Type</th>
              <th className="px-4 font-medium">Required</th><th className="px-4 font-medium">Sample value</th></tr>
          </thead>
          <tbody>
            {schema.map((f) => (
              <tr key={f.key} className="border-t align-top">
                <td className="px-4 py-2.5">
                  <div className="font-medium">{f.label}</div>
                  {f.description && <div className="text-xs text-muted-foreground">{f.description}</div>}
                </td>
                <td className="px-4 py-2.5 font-mono text-xs">{f.key}</td>
                <td className="px-4 py-2.5"><span className="rounded bg-muted px-1.5 py-0.5 text-xs">{f.type}</span></td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap gap-1">
                    {f.required ? <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-400">Required</span>
                      : <span className="text-xs text-muted-foreground">Optional</span>}
                    {f.generated && <span className="rounded bg-target-soft px-1.5 py-0.5 text-[11px] font-medium text-target">Generated if unmapped</span>}
                    {f.is_custom && <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary">Custom</span>}
                  </div>
                </td>
                <td className="max-w-64 truncate px-4 py-2.5 text-muted-foreground">{show(f.is_custom ? custom[f.key] : sample[f.key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <AddFieldDialog key={String(open)} open={open} onOpenChange={setOpen} connectorId={c.id} onAdded={onAdded} />
    </Card>
  );
}

function AddFieldDialog({ open, onOpenChange, connectorId, onAdded }: { open: boolean; onOpenChange: (o: boolean) => void; connectorId: string; onAdded: () => void }) {
  const [label, setLabel] = useState("");
  const [type, setType] = useState("string");
  const [required, setRequired] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const key = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`${API}/api/connectors/${connectorId}/attributes`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ label, data_type: type, is_required: required }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? res.statusText);
      toast.success(`Field “${body.label}” added`, { description: `Key ${body.key}` });
      onOpenChange(false);
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a custom field</DialogTitle>
          <DialogDescription>Custom fields are stored on each HR Portal employee and can be mapped in integrations.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Label</Label>
            <Input value={label} onChange={(e) => { setLabel(e.target.value); setError(""); }} placeholder="Badge number" aria-invalid={!!error} />
            <p className="text-xs text-muted-foreground">Key: <span className="font-mono">{key || "—"}</span></p>
          </div>
          <div className="space-y-1.5">
            <Label>Type</Label>
            <SimpleSelect value={type} onChange={(v) => setType(v || "string")} className="w-full"
              options={["string", "number", "boolean", "date"].map((t) => ({ value: t, label: t }))} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} className="size-4 accent-primary" /> Required
          </label>
          {error && <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-400">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={save} disabled={busy || !key}>{busy ? "Adding…" : "Add field"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface ImportRun { id: string; filename: string; total: number; imported: number; ts: number; errors: { row: number; name: string; field: string; message: string }[] }

function ImportsTab({ c, onImported }: { c: Connector; onImported: () => void }) {
  const [runs, setRuns] = useState<ImportRun[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const load = useCallback(() => { api<ImportRun[]>(`/api/connectors/${c.id}/imports`).then(setRuns); }, [c.id]);
  useEffect(load, [load]);

  const upload = async (file: File) => {
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`${API}/api/connectors/${c.id}/import`, { method: "POST", body: fd });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? res.statusText);
      if (body.errors.length) toast.warning(`${body.imported}/${body.total} candidates imported`, { description: `${body.errors.length} problem(s) — see the history below.` });
      else toast.success(`${body.imported} candidates imported from ${file.name}`);
      load();
      onImported();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <button onClick={() => ref.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) upload(f); }}
          className={cn("flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors",
            drag ? "border-source bg-source-soft" : "border-border hover:border-source/60 hover:bg-muted/40")}>
          {busy ? <FileSpreadsheet className="size-6 animate-pulse text-source" /> : <UploadCloud className="size-6 text-muted-foreground" />}
          <span className="text-sm font-medium">{busy ? "Importing…" : "Drop a CSV, Excel (.xlsx) or JSON file of candidates"}</span>
          <span className="text-xs text-muted-foreground">Required columns: candidate_id, first_name, last_name, email, status · Re-importing a candidate_id updates it</span>
        </button>
        <input ref={ref} type="file" accept=".csv,.xlsx,.xls,.json" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
      </Card>
      <Card className="gap-0 overflow-hidden p-0">
        <div className="border-b px-4 py-3 text-sm font-semibold">Import history</div>
        {runs === null ? <Skeleton className="m-4 h-24" /> : runs.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">No imports yet.</p>
        ) : runs.map((r) => (
          <details key={r.id} className="border-b last:border-0">
            <summary className="flex cursor-pointer items-center gap-3 px-4 py-3 text-sm hover:bg-muted/40">
              <FileSpreadsheet className="size-4 text-source" />
              <span className="font-medium">{r.filename}</span>
              <span className="text-muted-foreground">{timeAgo(r.ts)}</span>
              <span className="ml-auto text-emerald-600">{r.imported} imported</span>
              {r.errors.length > 0 && <span className="text-rose-600">{r.errors.length} problem(s)</span>}
              <span className="text-muted-foreground">of {r.total}</span>
            </summary>
            {r.errors.length > 0 && (
              <table className="mx-4 mb-3 w-[calc(100%-2rem)] text-xs">
                <thead className="text-left text-muted-foreground"><tr><th className="py-1">Row</th><th>Name</th><th>Field</th><th>Problem</th></tr></thead>
                <tbody>{r.errors.map((e, i) => (
                  <tr key={i} className="border-t"><td className="py-1 tabular-nums">{e.row}</td><td>{e.name}</td><td className="font-mono">{e.field}</td><td className="text-rose-600">{e.message}</td></tr>
                ))}</tbody>
              </table>
            )}
          </details>
        ))}
      </Card>
    </div>
  );
}
