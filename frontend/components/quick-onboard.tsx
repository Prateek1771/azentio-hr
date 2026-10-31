"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Eye, Send, UserPlus } from "lucide-react";
import { API, api, type Template } from "@/lib/api";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

interface Role { role: string; department: string; grade: string }
const EMPTY = { name: "", email: "", role: "", location: "", joining_date: "", template_id: "standard" };
type Field = keyof typeof EMPTY;

/** One step: candidate basics → role-specific checklist → document-request email. */
export function QuickOnboard() {
  const [meta, setMeta] = useState<{ roles: Role[]; locations: string[]; templates: Template[] }>({ roles: [], locations: [], templates: [] });
  const [f, setF] = useState(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"send" | "create" | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  useEffect(() => {
    Promise.all([api<{ roles: Role[]; locations: Record<string, string> }>("/api/rules"), api<Template[]>("/api/templates")])
      .then(([r, t]) => setMeta({
        roles: r.roles, locations: Object.keys(r.locations ?? {}),
        templates: t.filter((x) => !["correction", "escalation"].includes(x.id)),
      }))
      .catch(() => {});
  }, []);

  const role = meta.roles.find((r) => r.role === f.role);
  const tpl = meta.templates.find((t) => t.id === f.template_id);
  const preview = useMemo(() => {
    if (!tpl) return null;
    const name = f.name || "Candidate";
    const vars: Record<string, string> = {
      name, first_name: name.split(" ")[0], role: f.role || "—", location: f.location || "—", joining_date: f.joining_date || "—",
      portal_link: "(personal portal link)",
      doc_list: "<ul><li><i>Required documents for this role and location are listed automatically</i></li></ul>",
    };
    const fill = (x: string) => x.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k) => vars[k] ?? "");
    return { subject: fill(tpl.subject), body: fill(tpl.body) };
  }, [tpl, f]);

  const set = (k: Field) => (v: string) => {
    setF((p) => ({ ...p, [k]: v }));
    setErrors((e) => ({ ...e, [k]: "" }));
  };

  const submit = async (send: boolean) => {
    const required: Field[] = ["name", "email", "role", "location", "joining_date"];
    const missing = Object.fromEntries(required.filter((k) => !f[k]).map((k) => [k, "Required"]));
    if (Object.keys(missing).length) { setErrors(missing); return; }
    setBusy(send ? "send" : "create");
    try {
      const res = await fetch(`${API}/api/onboard`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...f, send }),
      });
      const body = await res.json();
      if (!res.ok) {
        const errs = body.detail?.errors as { field: string; message: string }[] | undefined;
        if (errs) setErrors(Object.fromEntries(errs.map((e) => [e.field, e.message])));
        throw new Error(body.detail?.message ?? body.detail ?? "Could not create the joiner");
      }
      const link = <Link href={`/employees/${body.id}`} className="font-medium underline">Open record →</Link>;
      if (body.sent) toast.success(`${f.name} added — document request sent`, { description: link });
      else if (send && body.email_error) toast.warning(`${f.name} added, but the email was not sent`, { description: <>{body.email_error}. {link}</> });
      else toast.success(`${f.name} added with a ${role?.role ?? ""} checklist`, { description: link });
      setF(EMPTY);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const err = (k: string) => errors[k] ? <p className="text-xs text-rose-600">{errors[k]}</p> : null;

  return (
    <Card className="gap-4 overflow-hidden p-0">
      <div className="flex items-center gap-3 border-b bg-gradient-to-r from-primary/10 to-transparent px-5 py-4">
        <div className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground"><UserPlus className="size-4" /></div>
        <div>
          <h3 className="text-sm font-semibold">Onboard a candidate</h3>
          <p className="text-xs text-muted-foreground">Enter the basics once. We build the role-specific checklist and email the document request in one step.</p>
        </div>
      </div>
      <div className="grid gap-4 px-5 lg:grid-cols-[1fr_380px]">
        <div className="grid content-start gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Candidate name</Label>
            <Input value={f.name} onChange={(e) => set("name")(e.target.value)} placeholder="Priya Raman" aria-invalid={!!errors.name} />
            {err("name")}
          </div>
          <div className="space-y-1.5">
            <Label>Candidate email</Label>
            <Input type="email" value={f.email} onChange={(e) => set("email")(e.target.value)} placeholder="priya@gmail.com" aria-invalid={!!errors.email} />
            {err("email")}
          </div>
          <div className="space-y-1.5">
            <Label>Role</Label>
            <SimpleSelect value={f.role} onChange={set("role")} placeholder="Select role" className="w-full"
              options={meta.roles.map((r) => ({ value: r.role, label: r.role }))} />
            {role ? <p className="text-xs text-muted-foreground">{role.department} · grade {role.grade}</p> : err("role")}
          </div>
          <div className="space-y-1.5">
            <Label>Location</Label>
            <SimpleSelect value={f.location} onChange={set("location")} placeholder="Select location" className="w-full"
              options={meta.locations.map((l) => ({ value: l, label: l }))} />
            {err("location")}
          </div>
          <div className="space-y-1.5">
            <Label>Joining date</Label>
            <Input type="date" value={f.joining_date} onChange={(e) => set("joining_date")(e.target.value)} aria-invalid={!!errors.joining_date} />
            {err("joining_date")}
          </div>
          <div className="space-y-1.5">
            <Label>Email template</Label>
            <SimpleSelect value={f.template_id} onChange={(v) => set("template_id")(v || "standard")} placeholder="Template" className="w-full"
              options={meta.templates.map((t) => ({ value: t.id, label: t.name }))} />
          </div>
          {(errors.manager_email || errors.department || errors.grade) && (
            <p className="text-xs text-rose-600 sm:col-span-2">{errors.manager_email || errors.department || errors.grade}</p>
          )}
          <div className="flex flex-wrap gap-2 pt-1 pb-5 sm:col-span-2">
            <Button onClick={() => submit(true)} disabled={busy !== null}>
              <Send className="size-4" /> {busy === "send" ? "Creating & sending…" : "Create & send request"}
            </Button>
            <Button variant="outline" onClick={() => submit(false)} disabled={busy !== null}>{busy === "create" ? "Creating…" : "Create only"}</Button>
            <Button variant="ghost" className="lg:hidden" onClick={() => setShowPreview((s) => !s)}><Eye className="size-4" /> Preview</Button>
          </div>
        </div>
        {preview && (
          <div className={cn("mb-5 overflow-hidden rounded-lg border", !showPreview && "hidden lg:block")}>
            <div className="border-b bg-muted/40 px-3 py-2 text-xs"><span className="text-muted-foreground">Subject:</span> <b>{preview.subject}</b></div>
            <div className="max-h-72 overflow-y-auto bg-white p-4 text-xs text-zinc-800 [&_a]:text-indigo-600 [&_li]:ml-4 [&_li]:list-disc [&_p]:my-1.5"
              dangerouslySetInnerHTML={{ __html: preview.body }} />
          </div>
        )}
      </div>
    </Card>
  );
}
