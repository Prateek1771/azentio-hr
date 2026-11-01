"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertOctagon, AlertTriangle, ArrowLeft, Check, ClipboardCopy, ExternalLink, FileText, Pencil, Send, UserX } from "lucide-react";
import { API, api, daysUntil, decisionLine, fmtDate, timeAgo, type Doc, type EmployeeDetail, type Item, type Template } from "@/lib/api";
import { DocViewer } from "@/components/doc-viewer";
import { TierBadge } from "@/components/review-bits";
import { Initials, ProgressRing, STATE_STYLE, StateBadge, StatusPill } from "@/components/status";
import { GraphView } from "@/components/graph-view";
import { Timeline } from "@/components/timeline";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { can, useMe } from "@/components/auth";

const KIND_LABEL = { document: "Documents", ack: "Policy acknowledgements", task: "Tasks & provisioning" } as const;


export default function EmployeePage() {
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<EmployeeDetail | null>(null);
  const [graph, setGraph] = useState<{ nodes: never[]; edges: never[] } | null>(null);
  const [sendOpen, setSendOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [preview, setPreview] = useState<Doc | null>(null);
  const [links, setLinks] = useState<{ type: string; connector_id: string; connector: string; employee_number?: string }[]>([]);
  const [loadError, setLoadError] = useState("");
  const { me } = useMe();
  const canWrite = can(me, "onboarding");

  const load = useCallback(() => {
    api<EmployeeDetail>(`/api/employees/${id}`).then(setD).catch((e) => setLoadError((e as Error).message || "Couldn't load this joiner"));
    api<{ nodes: never[]; edges: never[] }>(`/api/employees/${id}/graph`).then(setGraph).catch(() => {});
    api<typeof links>(`/api/employees/${id}/links`).then(setLinks).catch(() => {});
  }, [id]);
  useEffect(load, [load]);

  const setStatus = async (item: Item, status: string, note = "") => {
    try {
      setD(await api<EmployeeDetail>(`/api/items/${item.id}/status`, { method: "POST", json: { status, note } }));
      toast.success(`${item.name}: ${status}`);
      api<{ nodes: never[]; edges: never[] }>(`/api/employees/${id}/graph`).then(setGraph);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const copyPortal = async () => {
    const { token } = await api<{ token: string }>(`/api/employees/${id}/portal-link`);
    const url = `${window.location.origin}/portal/${token}`;
    await navigator.clipboard.writeText(url).catch(() => {});
    toast.success("Portal link copied", { description: url });
  };
  const openPortal = async () => {
    const { token } = await api<{ token: string }>(`/api/employees/${id}/portal-link`);
    window.open(`/portal/${token}`, "_blank");
  };

  if (loadError) return (
    <Card className="items-center gap-2 p-14 text-center">
      <UserX className="size-8 text-muted-foreground" />
      <div className="font-semibold">{/not found/i.test(loadError) ? "Joiner not found" : "Couldn't load this joiner"}</div>
      <p className="text-sm text-muted-foreground">{/not found/i.test(loadError) ? "They may not exist, or they aren't one of your reports." : loadError}</p>
      <Button variant="outline" size="sm" className="mt-2" nativeButton={false} render={<Link href="/dashboard" />}><ArrowLeft className="size-4" /> All joiners</Button>
    </Card>
  );
  if (!d) return <div className="space-y-4"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-96 rounded-xl" /></div>;
  const { employee: e, summary: s } = d;
  const daysToJoin = daysUntil(e.joining_date);
  const allDocs = [...d.items.flatMap((i) => i.documents.map((doc) => ({ doc, item: i.name }))), ...d.unmatched_documents.map((doc) => ({ doc, item: "Unmatched" }))];

  return (
    <>
      <Link href="/dashboard" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> All joiners
      </Link>

      <Card className="mb-4 flex-row flex-wrap items-center gap-5 p-5">
        <Initials name={e.name} className="size-14 text-base" />
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">{e.name}</h1>
          <p className="text-sm text-muted-foreground">{e.role} · {e.grade} · {e.department} · {e.location}</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Joins <b className="text-foreground">{fmtDate(e.joining_date)}</b> ({daysToJoin >= 0 ? `in ${daysToJoin} days` : `${-daysToJoin}d ago`})</span>
            <span>{e.email}</span>
            {d.manager && <span>Manager: {d.manager.name}</span>}
            <span>ID {e.id}</span>
            {links.map((l) => (
              <Link key={l.type + l.connector_id} href={`/connectors/${l.connector_id}`}
                className={cn("rounded-full px-2 py-0.5 font-medium", l.type === "SYNCED_TO" ? "bg-target-soft text-target" : "bg-source-soft text-source")}>
                {l.type === "SYNCED_TO" ? `Synced to ${l.connector}${l.employee_number ? ` · ${l.employee_number}` : ""}` : `From ${l.connector}`}
              </Link>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="hidden gap-4 text-center text-xs sm:flex">
            {(["completed", "pending", "overdue", "blocked"] as const).map((k) => (
              <div key={k}><div className="text-lg font-semibold tabular-nums">{s[k]}</div><div className="text-muted-foreground">{STATE_STYLE[k].label}</div></div>
            ))}
          </div>
          <ProgressRing value={s.progress} size={64} />
        </div>
        {canWrite && (
          <div className="flex w-full flex-wrap gap-2 border-t pt-4">
            <Button onClick={() => setSendOpen(true)}><Send className="size-4" /> Send document request</Button>
            <Button variant="outline" onClick={() => setEditOpen(true)}><Pencil className="size-4" /> Change date / role</Button>
            <Button variant="outline" onClick={copyPortal}><ClipboardCopy className="size-4" /> Copy portal link</Button>
            <Button variant="ghost" onClick={openPortal}><ExternalLink className="size-4" /> Open employee portal</Button>
          </div>
        )}
      </Card>

      {d.flags.filter((f) => f.level !== "info").length > 0 && (
        <div className="mb-4 space-y-1.5 rounded-xl border border-rose-200 bg-rose-50/60 p-3 dark:border-rose-500/30 dark:bg-rose-500/5">
          {d.flags.filter((f) => f.level !== "info").map((f, i) => (
            <div key={i} className={cn("flex items-start gap-2 text-sm", f.level === "error" ? "text-rose-700 dark:text-rose-400" : "text-amber-700 dark:text-amber-400")}>
              {f.level === "error" ? <AlertOctagon className="mt-0.5 size-4 shrink-0" /> : <AlertTriangle className="mt-0.5 size-4 shrink-0" />} {f.message}
            </div>
          ))}
        </div>
      )}

      <Tabs defaultValue="checklist">
        <TabsList>
          <TabsTrigger value="checklist">Checklist</TabsTrigger>
          <TabsTrigger value="documents">Documents ({allDocs.length})</TabsTrigger>
          <TabsTrigger value="record">HR record</TabsTrigger>
          <TabsTrigger value="graph">Knowledge graph</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="checklist" className="mt-4 space-y-4">
          {(["document", "ack", "task"] as const).map((kind) => {
            const items = d.items.filter((i) => i.kind === kind);
            if (!items.length) return null;
            return (
              <Card key={kind} className="gap-0 p-0">
                <div className="flex items-center justify-between border-b px-4 py-3">
                  <h3 className="text-sm font-semibold">{KIND_LABEL[kind]}</h3>
                  <span className="text-xs text-muted-foreground">{items.filter((i) => i.state === "completed").length}/{items.length} done</span>
                </div>
                {items.map((it) => (
                  <ChecklistRow key={it.id} it={it} canWrite={canWrite} onStatus={setStatus} onPreview={setPreview} />
                ))}
              </Card>
            );
          })}
        </TabsContent>

        <TabsContent value="documents" className="mt-4">
          {allDocs.length === 0 ? (
            <Card className="items-center p-12 text-center text-sm text-muted-foreground">
              <FileText className="size-8" /> No documents yet. Send a document request — replies with attachments are verified automatically.
            </Card>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {allDocs.map(({ doc, item }) => <DocCard key={doc.id} doc={doc} item={item} employee={e} onPreview={() => setPreview(doc)} />)}
            </div>
          )}
        </TabsContent>

        <TabsContent value="record" className="mt-4">
          <Card className="p-5">
            <h3 className="text-sm font-semibold">Structured HR record</h3>
            <p className="-mt-2 text-xs text-muted-foreground">Profile data from ingestion, enriched with fields extracted from accepted documents.</p>
            <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {([["Employee ID", e.id], ["Full name", e.name], ["Email", e.email], ["Role", e.role], ["Grade", e.grade],
                ["Department", e.department], ["Location", e.location], ["Manager", d.manager?.name ?? e.manager_email],
                ["Joining date", fmtDate(e.joining_date)], ["Date of birth", e.date_of_birth ? fmtDate(e.date_of_birth) : "—"]] as const).map(([k, v]) => (
                <div key={k}><dt className="text-xs text-muted-foreground">{k}</dt><dd className="font-medium">{v}</dd></div>
              ))}
              {Object.entries(d.hr_record).map(([k, v]) => (
                <div key={k}><dt className="text-xs text-muted-foreground">{k.replace(/_/g, " ")} <span className="text-primary">· from {v.source.split("(")[0]}</span></dt>
                  <dd className="font-medium">{String(v.value)}</dd></div>
              ))}
            </dl>
          </Card>
        </TabsContent>

        <TabsContent value="graph" className="mt-4">
          <Card className="p-4">
            <div className="flex items-baseline justify-between">
              <h3 className="text-sm font-semibold">Onboarding entity graph</h3>
              <span className="text-xs text-muted-foreground">Live from FalkorDB · hover a node to trace dependencies</span>
            </div>
            {graph ? <GraphView nodes={graph.nodes} edges={graph.edges} /> : <Skeleton className="h-96" />}
          </Card>
        </TabsContent>

        <TabsContent value="activity" className="mt-4">
          <Card className="p-5"><Timeline events={d.events} /></Card>
        </TabsContent>
      </Tabs>

      <SendDialog open={sendOpen} onOpenChange={setSendOpen} employeeId={id} onSent={load} />
      <EditDialog key={`edit-${editOpen}`} open={editOpen} onOpenChange={setEditOpen} e={e} onSaved={(nd) => { setD(nd); load(); }} />
      <Dialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader><DialogTitle>{preview?.filename}</DialogTitle></DialogHeader>
          {preview && <DocViewer id={preview.id} mime={preview.mime} filename={preview.filename} />}
          {preview && <div className="flex justify-end"><Button size="sm" variant="outline" nativeButton={false} render={<Link href={`/review/${preview.id}`} />}>Open review page</Button></div>}
        </DialogContent>
      </Dialog>
    </>
  );
}

function ChecklistRow({ it, canWrite, onStatus, onPreview }: {
  it: Item; canWrite: boolean; onStatus: (i: Item, s: string) => void; onPreview: (d: Doc) => void;
}) {
  const st = STATE_STYLE[it.state];
  const latest = it.documents[0];
  return (
    <div className="flex flex-wrap items-start gap-3 border-b px-4 py-3 last:border-0">
      <st.Icon className={cn("mt-0.5 size-4 shrink-0", { completed: "text-emerald-500", pending: "text-sky-500", blocked: "text-zinc-400", overdue: "text-rose-500" }[it.state])} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{it.name}</span>
          <StatusPill status={it.status} />
          {it.state !== "completed" && it.state !== "pending" && <StateBadge state={it.state} />}
        </div>
        <p className="mt-1 text-xs text-muted-foreground"><span className="font-medium text-foreground/70">Why: </span>{it.reason}</p>
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>Due {fmtDate(it.due)}</span>
          <span>Owner: {it.owner_name || it.category}</span>
          {it.policy_ref && <span>Policy: {it.policy_ref}</span>}
          {it.blocked_by.length > 0 && <span className="text-zinc-500">Waiting on: {it.blocked_by.join(", ")}</span>}
        </div>
        {latest && (
          <button onClick={() => onPreview(latest)} className="mt-2 inline-flex items-center gap-1.5 rounded-md border bg-muted/40 px-2 py-1 text-xs hover:bg-muted">
            <FileText className="size-3.5" /> {latest.filename} · via {latest.source}
          </button>
        )}
        {latest?.decision && <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <TierBadge tier={latest.tier} />{decisionLine(latest)}</div>}
        {latest?.issues?.length > 0 && it.status === "Correction Requested" && (
          <ul className="mt-1.5 space-y-0.5 text-xs text-amber-700 dark:text-amber-400">{latest.issues.map((x, i) => <li key={i}>• {x}</li>)}</ul>
        )}
      </div>
      <div className="flex gap-1.5">
        {/* Document decisions are made, with a reason, on the review page; never set directly here. */}
        {it.kind === "document" && latest && (
          <Button size="sm" variant={latest.status === "Under Review" ? "outline" : "ghost"} nativeButton={false} render={<Link href={`/review/${latest.id}`} />}>
            {latest.status === "Under Review" ? "Open review" : "Details"}</Button>
        )}
        {canWrite && it.kind === "task" && it.status !== "Completed" && it.state !== "blocked" && (
          <Button size="sm" variant="outline" onClick={() => onStatus(it, "Completed")}><Check className="size-3.5" /> Mark done</Button>
        )}
      </div>
    </div>
  );
}

function DocCard({ doc, item, employee, onPreview }: { doc: Doc; item: string; employee: EmployeeDetail["employee"]; onPreview: () => void }) {
  const x = doc.extracted as Record<string, string | number | boolean | null>;
  const compare: [string, unknown, unknown][] = [
    ["Name", x.full_name, employee.name], ["Date of birth", x.date_of_birth, employee.date_of_birth],
  ];
  return (
    <Card className="gap-3 p-4">
      <div className="flex items-start gap-3">
        <button onClick={onPreview} className="grid size-12 shrink-0 place-items-center overflow-hidden rounded-lg border bg-muted">
          {doc.mime.startsWith("image/")
            // eslint-disable-next-line @next/next/no-img-element
            ? <img src={`${API}/api/documents/${doc.id}/file`} alt="" className="size-full object-cover" />
            : <FileText className="size-5 text-muted-foreground" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{doc.filename}</div>
          <div className="text-xs text-muted-foreground">{item} · via {doc.source} · {timeAgo(doc.ts)}</div>
        </div>
        <StatusPill status={doc.status} />
      </div>
      {doc.confidence > 0 && (
        <div className="text-xs">
          <div className="mb-1 flex justify-between text-muted-foreground"><span>AI extraction confidence</span><span>{Math.round(doc.confidence * 100)}%</span></div>
          <div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${doc.confidence * 100}%` }} /></div>
        </div>
      )}
      <table className="w-full text-xs">
        <thead><tr className="text-muted-foreground"><th className="py-1 text-left font-normal">Field</th><th className="text-left font-normal">On document</th><th className="text-left font-normal">Profile</th></tr></thead>
        <tbody>
          {compare.map(([k, a, b]) => {
            const doc = String(a ?? "").toLowerCase(), prof = String(b ?? "").toLowerCase();
            const bad = Boolean(doc && prof) && (k === "Name"
              ? !prof.split(" ").every((t) => doc.includes(t))
              : doc !== prof);
            return (
              <tr key={k} className="border-t">
                <td className="py-1.5 text-muted-foreground">{k}</td>
                <td className={cn("font-medium", bad && "text-rose-600")}>{(a as string) ?? "—"}</td>
                <td>{(b as string) ?? "—"}</td>
              </tr>
            );
          })}
          {(["document_number", "issue_date", "expiry_date", "issuer"] as const).filter((k) => x[k]).map((k) => (
            <tr key={k} className="border-t"><td className="py-1.5 text-muted-foreground">{k.replace(/_/g, " ")}</td><td className="font-medium" colSpan={2}>{String(x[k])}</td></tr>
          ))}
        </tbody>
      </table>
      {doc.issues.length > 0 && <ul className="space-y-0.5 text-xs text-amber-700 dark:text-amber-400">{doc.issues.map((i, n) => <li key={n}>• {i}</li>)}</ul>}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        <TierBadge tier={doc.tier} />{decisionLine(doc)}{typeof x._model === "string" && <span>· extracted by {x._model}</span>}
        <Link href={`/review/${doc.id}`} className="ml-auto font-medium text-primary hover:underline">Review page</Link>
      </div>
    </Card>
  );
}

function SendDialog({ open, onOpenChange, employeeId, onSent }: { open: boolean; onOpenChange: (o: boolean) => void; employeeId: string; onSent: () => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [tid, setTid] = useState("standard");
  const [pv, setPv] = useState<{ to: string; subject: string; html: string } | null>(null);
  const [sending, setSending] = useState(false);
  useEffect(() => {
    if (open) api<Template[]>("/api/templates").then((t) => setTemplates(t.filter((x) => !["correction", "escalation"].includes(x.id))));
  }, [open]);
  useEffect(() => {
    if (open) api<typeof pv>(`/api/employees/${employeeId}/preview-email?template_id=${tid}`).then(setPv).catch((e) => toast.error(e.message));
  }, [open, tid, employeeId]);
  const send = async () => {
    setSending(true);
    try {
      await api(`/api/employees/${employeeId}/send-request`, { method: "POST", json: { template_id: tid } });
      toast.success("Document request sent", { description: `Replies to this thread are picked up and verified automatically.` });
      onOpenChange(false);
      onSent();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSending(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Send document request</DialogTitle>
          <DialogDescription>Personalised from the template with this joiner&apos;s outstanding documents and portal link.</DialogDescription>
        </DialogHeader>
        <SimpleSelect value={tid} onChange={(v) => setTid(v || "standard")} options={templates.map((t) => ({ value: t.id, label: t.name }))} placeholder="Template" className="w-full" />
        {pv ? (
          <div className="overflow-hidden rounded-lg border">
            <div className="space-y-0.5 border-b bg-muted/40 px-4 py-2 text-xs">
              <div><span className="text-muted-foreground">To:</span> {pv.to}</div>
              <div><span className="text-muted-foreground">Subject:</span> <b>{pv.subject}</b></div>
            </div>
            <div className="prose-sm max-h-80 overflow-y-auto bg-white p-4 text-sm text-zinc-800 [&_li]:ml-5 [&_li]:list-disc [&_p]:my-2" dangerouslySetInnerHTML={{ __html: pv.html }} />
          </div>
        ) : <Skeleton className="h-64" />}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={send} disabled={sending || !pv}><Send className="size-4" /> {sending ? "Sending…" : "Send via Gmail"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditDialog({ open, onOpenChange, e, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; e: EmployeeDetail["employee"]; onSaved: (d: EmployeeDetail) => void }) {
  const [form, setForm] = useState(e);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try {
      const nd = await api<EmployeeDetail>(`/api/employees/${e.id}`, { method: "PATCH", json: {
        joining_date: form.joining_date, role: form.role, grade: form.grade, department: form.department, location: form.location, email: form.email,
      } });
      const changes = nd.events.filter((ev) => ev.kind === "checklist").slice(0, 6);
      toast.success("Onboarding plan recalculated", { description: changes.length ? changes.map((c) => c.message).join("\n") : "No checklist changes needed." });
      onOpenChange(false);
      onSaved(nd);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const field = (k: keyof typeof form, label: string, type = "text") => (
    <div className="space-y-1.5"><Label>{label}</Label>
      <Input type={type} value={(form[k] as string) ?? ""} onChange={(ev) => setForm({ ...form, [k]: ev.target.value })} /></div>
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Change joining date or role</DialogTitle>
          <DialogDescription>Dependent tasks, deadlines, owners and required documents are recalculated automatically.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          {field("joining_date", "Joining date", "date")}
          {field("grade", "Grade")}
          {field("role", "Role")}
          {field("department", "Department")}
          {field("location", "Location")}
          {field("email", "Email")}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={save} disabled={saving}>{saving ? "Recalculating…" : "Save & recalculate"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

