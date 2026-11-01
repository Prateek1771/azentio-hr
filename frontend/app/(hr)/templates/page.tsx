"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Mail, Save } from "lucide-react";
import { api, type Template } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { can, useMe } from "@/components/auth";

const SAMPLE: Record<string, string> = {
  name: "Aarav Sharma", first_name: "Aarav", role: "Software Engineer", location: "Bengaluru", joining_date: "2026-11-16",
  portal_link: "http://localhost:3000/portal/demo", item_name: "Government photo ID", owner_name: "IT Service Desk", due: "2026-11-14",
  blocked: "Developer tools access", grade: "G3", department: "Engineering",
  doc_list: "<ul><li><b>Government photo ID</b> — by 2026-11-06</li><li><b>PAN card</b> — by 2026-11-06</li></ul>",
  issues: "<li>Name on document does not match your profile.</li>",
};
const VARS = ["name", "first_name", "role", "location", "joining_date", "doc_list", "portal_link"];
const render = (s: string) => s.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k) => SAMPLE[k] ?? `{{${k}}}`);

export default function TemplatesPage() {
  const { me } = useMe();
  const canWrite = can(me, "onboarding");
  const [list, setList] = useState<Template[]>([]);
  const [cur, setCur] = useState<Template | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<Template[]>("/api/templates").then((t) => { setList(t); setCur(t.find((x) => x.id === "standard") ?? t[0] ?? null); });
  }, []);

  const preview = useMemo(() => cur && { subject: render(cur.subject), body: render(cur.body) }, [cur]);

  const save = async () => {
    if (!cur) return;
    setSaving(true);
    try {
      await api(`/api/templates/${cur.id}`, { method: "PUT", json: cur });
      setList((l) => l.map((t) => (t.id === cur.id ? cur : t)));
      toast.success("Template saved");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const newTemplate = () => {
    const t = { id: `custom-${Date.now().toString(36)}`, name: "New template", subject: "Welcome, {{first_name}}!", body: "<p>Hi {{first_name}},</p>{{doc_list}}" };
    setList((l) => [...l, t]);
    setCur(t);
  };

  return (
    <>
      <PageHeader title="Email templates" subtitle="Customise the document-request emails HR sends. Variables are filled per employee."
        actions={canWrite && <Button variant="outline" onClick={newTemplate}>New template</Button>} />
      <div className="grid gap-4 lg:grid-cols-[220px_1fr_1fr]">
        <Card className="gap-1 p-2">
          {list.map((t) => (
            <button key={t.id} onClick={() => setCur(t)}
              className={cn("flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm", cur?.id === t.id ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted")}>
              <Mail className="size-4 shrink-0" /> <span className="truncate">{t.name}</span>
            </button>
          ))}
        </Card>
        {cur && (
          <Card className="gap-3 p-4">
            <div className="space-y-1.5"><Label>Name</Label><Input value={cur.name} onChange={(e) => setCur({ ...cur, name: e.target.value })} /></div>
            <div className="space-y-1.5"><Label>Subject</Label><Input value={cur.subject} onChange={(e) => setCur({ ...cur, subject: e.target.value })} /></div>
            <div className="space-y-1.5"><Label>Body (HTML)</Label>
              <Textarea value={cur.body} onChange={(e) => setCur({ ...cur, body: e.target.value })} rows={14} className="font-mono text-xs" /></div>
            <div className="flex flex-wrap gap-1">
              {VARS.map((v) => (
                <button key={v} onClick={() => setCur({ ...cur, body: cur.body + `{{${v}}}` })}
                  className="rounded-md border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] hover:bg-muted">{`{{${v}}}`}</button>
              ))}
            </div>
            <Button onClick={save} disabled={saving || !canWrite} title={canWrite ? undefined : "Only HR Ops can edit templates"} className="self-start"><Save className="size-4" /> {saving ? "Saving…" : "Save template"}</Button>
          </Card>
        )}
        {preview && (
          <Card className="gap-0 overflow-hidden p-0">
            <div className="border-b bg-muted/40 px-4 py-2.5 text-xs"><span className="text-muted-foreground">Preview · Subject:</span> <b>{preview.subject}</b></div>
            <div className="flex-1 bg-white p-5 text-sm text-zinc-800 [&_a]:text-indigo-600 [&_a]:underline [&_li]:ml-5 [&_li]:list-disc [&_p]:my-2"
              dangerouslySetInnerHTML={{ __html: preview.body }} />
          </Card>
        )}
      </div>
    </>
  );
}
