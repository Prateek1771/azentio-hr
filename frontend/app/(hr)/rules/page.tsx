"use client";
import { can, useMe } from "@/components/auth";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { FileText, ListTodo, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { api, type Rule } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface RulesResp { rules: Rule[]; doc_types: Record<string, { name: string }>; departments: string[]; locations: Record<string, string> }
const KIND_ICON = { document: FileText, task: ListTodo, ack: ShieldCheck } as Record<string, typeof FileText>;

const matchText = (m: Record<string, unknown>) =>
  Object.keys(m).length ? Object.entries(m).map(([k, v]) => `${k.replace("_", " ")}: ${Array.isArray(v) ? v.join("/") : v}`).join(" · ") : "All employees";

export default function RulesPage() {
  const { me } = useMe();
  const canWrite = can(me, "onboarding");
  const [data, setData] = useState<RulesResp | null>(null);
  const [edit, setEdit] = useState<Rule | null>(null);
  useEffect(() => { api<RulesResp>("/api/rules").then(setData); }, []);

  const save = async (r: Rule) => {
    try {
      setData(await api<RulesResp>(`/api/rules/${encodeURIComponent(r.id)}`, { method: "PUT", json: r }));
      toast.success(`Rule ${r.id} saved`, { description: "Every joiner's checklist was recalculated." });
      setEdit(null);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const remove = async (r: Rule) => {
    if (!confirm(`Delete rule ${r.id}? Matching checklist items will be removed from every joiner.`)) return;
    setData(await api<RulesResp>(`/api/rules/${encodeURIComponent(r.id)}`, { method: "DELETE" }));
    toast.success(`Rule ${r.id} deleted`);
  };

  const label = (r: Rule) => r.name || data?.doc_types?.[r.item]?.name || r.item;

  return (
    <>
      <PageHeader title="Requirement rules" subtitle="Which documents, tasks and acknowledgements apply by role, department, grade and location — and why."
        actions={canWrite && <Button onClick={() => setEdit({ id: `R-${Date.now().toString(36).toUpperCase()}`, item: "", name: "", kind: "document", category: "HR", match: {}, offset_days: -7, depends_on: [], description: "", policy_ref: "", auto: "" })}>
          <Plus className="size-4" /> Add rule</Button>} />
      <Card className="gap-0 overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              <tr><th className="px-4 py-2.5 font-medium">Requirement</th><th className="px-4 font-medium">Applies to</th><th className="px-4 font-medium">Due</th>
                <th className="px-4 font-medium">Owner</th><th className="px-4 font-medium">Depends on</th><th /></tr>
            </thead>
            <tbody>
              {data?.rules.map((r) => {
                const Icon = KIND_ICON[r.kind] ?? FileText;
                return (
                  <tr key={r.id} className="border-t align-top">
                    <td className="max-w-md px-4 py-3">
                      <div className="flex items-center gap-2 font-medium"><Icon className="size-4 text-primary" />{label(r)}
                        <span className="font-mono text-[11px] font-normal text-muted-foreground">{r.id}</span></div>
                      <p className="mt-0.5 text-xs text-muted-foreground">{r.description}</p>
                    </td>
                    <td className="px-4 py-3 text-xs">{matchText(r.match)}</td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap">{r.offset_days === 0 ? "On joining day" : r.offset_days < 0 ? `${-r.offset_days}d before joining` : `${r.offset_days}d after joining`}</td>
                    <td className="px-4 py-3 text-xs">{r.category}{r.auto && <div className="text-primary">auto-{r.auto}</div>}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{r.depends_on.join(", ") || "—"}</td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      {canWrite && <Button size="icon" variant="ghost" onClick={() => setEdit(r)} aria-label="Edit"><Pencil className="size-4" /></Button>}
                      {canWrite && <Button size="icon" variant="ghost" onClick={() => remove(r)} aria-label="Delete"><Trash2 className="size-4" /></Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
      <RuleDialog key={edit?.id ?? "none"} rule={edit} data={data} onClose={() => setEdit(null)} onSave={save} />
    </>
  );
}

function RuleDialog({ rule, data, onClose, onSave }: { rule: Rule | null; data: RulesResp | null; onClose: () => void; onSave: (r: Rule) => void }) {
  const [r, setR] = useState<Rule | null>(rule);
  const [matchJson, setMatchJson] = useState(JSON.stringify(rule?.match ?? {}));
  if (!r) return null;
  const submit = () => {
    try {
      onSave({ ...r, match: JSON.parse(matchJson || "{}") });
    } catch {
      toast.error("“Applies to” must be valid JSON, e.g. {\"department\": \"Engineering\", \"grade_min\": 3}");
    }
  };
  return (
    <Dialog open={!!rule} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader><DialogTitle>{data?.rules.some((x) => x.id === r.id) ? `Edit rule ${r.id}` : "New rule"}</DialogTitle>
          <DialogDescription>Saving recalculates every joiner&apos;s checklist immediately.</DialogDescription></DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5"><Label>Kind</Label>
            <SimpleSelect value={r.kind} onChange={(v) => setR({ ...r, kind: v || "document" })} className="w-full"
              options={[{ value: "document", label: "Document" }, { value: "task", label: "Task" }, { value: "ack", label: "Policy acknowledgement" }]} /></div>
          <div className="space-y-1.5"><Label>Owner category</Label>
            <SimpleSelect value={r.category} onChange={(v) => setR({ ...r, category: v || "HR" })} className="w-full"
              options={["HR", "IT", "Facilities", "Compliance"].map((c) => ({ value: c, label: c }))} /></div>
          <div className="space-y-1.5"><Label>Item key</Label>
            <Input value={r.item} onChange={(e) => setR({ ...r, item: e.target.value })} placeholder={r.kind === "document" ? Object.keys(data?.doc_types ?? {}).slice(0, 3).join(", ") : "e.g. vpn_access"} /></div>
          <div className="space-y-1.5"><Label>Display name</Label><Input value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} placeholder="Optional for documents" /></div>
          <div className="col-span-2 space-y-1.5"><Label>Applies to (JSON)</Label>
            <Input value={matchJson} onChange={(e) => setMatchJson(e.target.value)} className="font-mono text-xs" />
            <p className="text-[11px] text-muted-foreground">Keys: department, role, location, country, grade_min, grade_max. Empty = all employees.</p></div>
          <div className="space-y-1.5"><Label>Due (days relative to joining)</Label>
            <Input type="number" value={r.offset_days} onChange={(e) => setR({ ...r, offset_days: Number(e.target.value) })} /></div>
          <div className="space-y-1.5"><Label>Depends on (item keys)</Label>
            <Input value={r.depends_on.join(", ")} onChange={(e) => setR({ ...r, depends_on: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></div>
          <div className="col-span-2 space-y-1.5"><Label>Reason shown to HR and employees</Label>
            <Textarea value={r.description} onChange={(e) => setR({ ...r, description: e.target.value })} rows={2} /></div>
          <div className="space-y-1.5"><Label>Source policy</Label><Input value={r.policy_ref} onChange={(e) => setR({ ...r, policy_ref: e.target.value })} placeholder="document-submission-policy.md" /></div>
          <div className="space-y-1.5"><Label>Automation</Label>
            <SimpleSelect value={r.auto} onChange={(v) => setR({ ...r, auto: v })} className="w-full" placeholder="Manual"
              options={[{ value: "complete", label: "Auto-complete when unblocked" }, { value: "request", label: "Raise request when unblocked" }]} /></div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!r.item || !r.description}>Save rule</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
