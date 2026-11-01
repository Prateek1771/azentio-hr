"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Lock, SlidersHorizontal } from "lucide-react";
import { api, type DocPolicy, type Tier, type VersionDetail } from "@/lib/api";
import { SimpleSelect } from "@/components/simple-select";
import { TierBadge } from "@/components/review-bits";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

// Mirror of backend documents.route.ALWAYS_HIGH (the legal floor). The backend refuses a change regardless.
const ALWAYS_HIGH = new Set(["photo_id", "i9_documents", "visa", "work_permit", "bgv_report"]);

/** Tiers and auto-accept thresholds are policy: edited in the draft, approved and published like any rule. */
export function DocPolicyCard({ draft, docTypes, editable, onSaved }: {
  draft: VersionDetail; docTypes: Record<string, { name: string }>; editable: boolean; onSaved: (d: VersionDetail) => void;
}) {
  const base = draft.doc_policy;
  const [p, setP] = useState<DocPolicy | null>(base ? structuredClone(base) : null);
  const [busy, setBusy] = useState(false);
  if (!p || !base) return null;
  const eff = (t: string) => ({ ...p.default, ...p.types[t] }) as { tier: Tier; auto_accept_min: number };
  const set = (t: string, patch: Partial<{ tier: Tier; auto_accept_min: number }>) =>
    setP({ ...p, types: { ...p.types, [t]: { ...p.types[t], ...patch } } });
  const dirty = JSON.stringify(p) !== JSON.stringify(base);
  const save = async () => {
    setBusy(true);
    try {
      onSaved(await api<VersionDetail>(`/api/policy/drafts/${draft.id}/doc-policy`, { method: "PUT", json: p }));
      toast.success("Document review policy saved to the draft");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const types = Object.keys(docTypes).length ? Object.keys(docTypes) : Object.keys(p.types);
  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <SlidersHorizontal className="size-4 text-primary" />
        <div className="text-sm font-semibold">Document review policy</div>
        <p className="w-full text-xs text-muted-foreground">High-risk documents always go to an approver. Low-risk ones are accepted automatically only when every check passes and the AI&apos;s confidence reaches the threshold.</p>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs text-muted-foreground"><tr><th className="px-4 py-2 font-medium">Document</th><th className="font-medium">Risk tier</th><th className="pr-4 font-medium">Auto-accept at or above</th></tr></thead>
        <tbody>{types.map((t) => {
          const e = eff(t), floor = ALWAYS_HIGH.has(t);
          return (
            <tr key={t} className="border-t">
              <td className="px-4 py-2">{docTypes[t]?.name ?? t}<div className="font-mono text-[10px] text-muted-foreground">{t}</div></td>
              <td>{floor || !editable
                ? <span className="inline-flex items-center gap-1"><TierBadge tier={floor ? "high" : e.tier} />{floor && <Lock className="size-3 text-muted-foreground" aria-label="legal floor" />}</span>
                : <SimpleSelect value={e.tier} onChange={(v) => set(t, { tier: (v || "low") as Tier })} className="w-28"
                    options={[{ value: "low", label: "Low risk" }, { value: "high", label: "High risk" }]} />}</td>
              <td className="pr-4">{(floor || e.tier === "high") ? <span className="text-xs text-muted-foreground">never: always reviewed</span>
                : <Input type="number" step="0.01" min={0.5} max={1} disabled={!editable} value={e.auto_accept_min} className="w-24"
                    onChange={(ev) => set(t, { auto_accept_min: Number(ev.target.value) })} />}</td>
            </tr>
          );
        })}</tbody>
      </table>
      <div className="flex flex-wrap items-end gap-4 border-t px-4 py-3 text-sm">
        <label className="space-y-1"><span className="block text-xs text-muted-foreground">Spot-check sample of auto-accepts</span>
          <span className="flex items-center gap-1"><Input type="number" min={0} max={100} disabled={!editable} className="w-20" value={Math.round(p.spot_check_rate * 100)}
            onChange={(ev) => setP({ ...p, spot_check_rate: Number(ev.target.value) / 100 })} />%</span></label>
        <label className="space-y-1"><span className="block text-xs text-muted-foreground">Send to review if AI is down for</span>
          <span className="flex items-center gap-1"><Input type="number" min={1} max={1440} disabled={!editable} className="w-20" value={p.extraction_deadline_minutes}
            onChange={(ev) => setP({ ...p, extraction_deadline_minutes: Number(ev.target.value) })} />min</span></label>
        {editable && <div className="ml-auto flex gap-2">
          {dirty && <Button variant="ghost" onClick={() => setP(structuredClone(base))}>Reset</Button>}
          <Button disabled={!dirty || busy} onClick={save}>Save to draft</Button></div>}
      </div>
    </Card>
  );
}
