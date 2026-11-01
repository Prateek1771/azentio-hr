"use client";

import { useState } from "react";
import { toast } from "sonner";
import { ShieldAlert } from "lucide-react";
import type { Rule } from "@/lib/api";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export const blankRule = (): Rule => ({
  id: `R-${Date.now().toString(36).toUpperCase()}`, item: "", name: "", kind: "document", category: "HR", match: {},
  offset_days: -7, depends_on: [], description: "", policy_ref: "", auto: "", compliance: false,
});

export const matchText = (m: Record<string, unknown>) =>
  Object.keys(m).length ? Object.entries(m).map(([k, v]) => `${k.replace("_", " ")}: ${Array.isArray(v) ? v.join("/") : v}`).join(" · ") : "All employees";

export const dueText = (d: number) => d === 0 ? "On joining day" : d < 0 ? `${-d}d before joining` : `${d}d after joining`;

/** Edit one rule inside a draft. Nothing reaches joiners until the draft is approved and published. */
export function RuleDialog({ rule, isNew, docTypes, onClose, onSave }: {
  rule: Rule | null; isNew: boolean; docTypes: Record<string, { name: string }>; onClose: () => void; onSave: (r: Rule) => Promise<void>;
}) {
  const [r, setR] = useState<Rule | null>(rule);
  const [matchJson, setMatchJson] = useState(JSON.stringify(rule?.match ?? {}));
  const [busy, setBusy] = useState(false);
  if (!r) return null;
  const submit = async () => {
    let match: Record<string, unknown>;
    try {
      match = JSON.parse(matchJson || "{}");
    } catch {
      toast.error("“Applies to” must be valid JSON, e.g. {\"department\": \"Engineering\", \"grade_min\": 3}");
      return;
    }
    setBusy(true);
    try { await onSave({ ...r, match }); } finally { setBusy(false); }
  };
  return (
    <Dialog open={!!rule} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isNew ? "New rule" : `Edit rule ${r.id}`}</DialogTitle>
          <DialogDescription>Saved to the draft only. Joiners see it after the draft is approved and published.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          {isNew && <div className="col-span-2 space-y-1.5"><Label htmlFor="rf-id">Rule id</Label>
            <Input id="rf-id" value={r.id} onChange={(e) => setR({ ...r, id: e.target.value.toUpperCase().replace(/[^A-Z0-9_.-]/g, "") })} className="font-mono" /></div>}
          <div className="space-y-1.5"><Label>Kind</Label>
            <SimpleSelect value={r.kind} onChange={(v) => setR({ ...r, kind: v || "document" })} className="w-full"
              options={[{ value: "document", label: "Document" }, { value: "task", label: "Task" }, { value: "ack", label: "Policy acknowledgement" }]} /></div>
          <div className="space-y-1.5"><Label>Owner category</Label>
            <SimpleSelect value={r.category} onChange={(v) => setR({ ...r, category: v || "HR" })} className="w-full"
              options={["HR", "IT", "Facilities", "Compliance"].map((c) => ({ value: c, label: c }))} /></div>
          <div className="space-y-1.5"><Label htmlFor="rf-item">Item key</Label>
            <Input id="rf-item" value={r.item} onChange={(e) => setR({ ...r, item: e.target.value })} placeholder={r.kind === "document" ? Object.keys(docTypes).slice(0, 3).join(", ") : "e.g. vpn_access"} /></div>
          <div className="space-y-1.5"><Label htmlFor="rf-name">Display name</Label>
            <Input id="rf-name" value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} placeholder="Optional for documents" /></div>
          <div className="col-span-2 space-y-1.5"><Label htmlFor="rf-match">Applies to (JSON)</Label>
            <Input id="rf-match" value={matchJson} onChange={(e) => setMatchJson(e.target.value)} className="font-mono text-xs" />
            <p className="text-[11px] text-muted-foreground">Keys: department, role, location, country, grade_min, grade_max. Empty = all employees.</p></div>
          <div className="space-y-1.5"><Label htmlFor="rf-due">Due (days relative to joining)</Label>
            <Input id="rf-due" type="number" value={r.offset_days} onChange={(e) => setR({ ...r, offset_days: Number(e.target.value) })} /></div>
          <div className="space-y-1.5"><Label htmlFor="rf-dep">Depends on (item keys)</Label>
            <Input id="rf-dep" value={r.depends_on.join(", ")} onChange={(e) => setR({ ...r, depends_on: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></div>
          <div className="col-span-2 space-y-1.5"><Label htmlFor="rf-desc">Reason shown to HR and employees</Label>
            <Textarea id="rf-desc" value={r.description} onChange={(e) => setR({ ...r, description: e.target.value })} rows={2} /></div>
          <div className="space-y-1.5"><Label htmlFor="rf-pol">Source policy</Label>
            <Input id="rf-pol" value={r.policy_ref} onChange={(e) => setR({ ...r, policy_ref: e.target.value })} placeholder="document-submission-policy.md" /></div>
          <div className="space-y-1.5"><Label>Automation</Label>
            <SimpleSelect value={r.auto} onChange={(v) => setR({ ...r, auto: v })} className="w-full" placeholder="Manual"
              options={[{ value: "complete", label: "Auto-complete when unblocked" }, { value: "request", label: "Raise request when unblocked" }]} /></div>
          <label className="col-span-2 flex cursor-pointer items-start gap-3 rounded-lg border p-3 has-checked:border-amber-400 has-checked:bg-amber-50/60 dark:has-checked:bg-amber-500/5">
            <input type="checkbox" checked={!!r.compliance} onChange={(e) => setR({ ...r, compliance: e.target.checked })} className="mt-0.5 size-4 accent-amber-600" />
            <span className="text-sm">
              <span className="flex items-center gap-1.5 font-medium"><ShieldAlert className="size-4 text-amber-600" /> Compliance rule</span>
              <span className="text-xs text-muted-foreground">Identity, work authorisation, background checks and similar. Any change to it needs two approvers.</span>
            </span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !r.id || !r.item || !r.description}>Save to draft</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
