"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, ArrowRight, GitBranch, Pencil, Plus, RotateCcw, ShieldAlert, Trash2, Undo2 } from "lucide-react";
import { api, STATE_LABEL, type Rule, type VersionDetail, type VersionsResp } from "@/lib/api";
import { openDraft } from "@/lib/policy";
import { PageHeader } from "@/components/app-shell";
import { RoleGate } from "@/components/auth";
import { blankRule, dueText, matchText, RuleDialog } from "@/components/rule-form";
import { diffCount, PolicyDiffView } from "@/components/policy-diff";
import { DocPolicyCard } from "@/components/doc-policy-form";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

async function fetchOpen(): Promise<VersionDetail | null> {
  const vs = await api<VersionsResp>("/api/policy/versions");
  return vs.open_id ? api<VersionDetail>(`/api/policy/versions/${vs.open_id}`) : null;
}

export default function DraftPage() {
  return <RoleGate roles={["hr_ops"]}><DraftEditor /></RoleGate>;
}

function DraftEditor() {
  const router = useRouter();
  const [draft, setDraft] = useState<VersionDetail | null>(null);
  const [none, setNone] = useState(false);
  const [docTypes, setDocTypes] = useState<Record<string, { name: string }>>({});
  const [edit, setEdit] = useState<{ rule: Rule; isNew: boolean } | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const show = useCallback((d: VersionDetail | null) => { setDraft(d); setNone(!d); return d; }, []);
  const load = useCallback(() => fetchOpen().then(show), [show]);

  useEffect(() => {
    api<{ doc_types: Record<string, { name: string }> }>("/api/rules").then((r) => setDocTypes(r.doc_types ?? {})).catch(() => {});
    fetchOpen().then((d) => {
      show(d);
      const want = new URLSearchParams(window.location.search).get("edit");
      const r = want && d?.rules.find((x) => x.id === want);
      if (r && d?.state === "draft") setEdit({ rule: r, isNew: false });
    }).catch((e) => toast.error((e as Error).message));
  }, [show]);

  const marks = useMemo(() => {
    const m: Record<string, "added" | "changed"> = {};
    draft?.diff.added.forEach((r) => { m[r.id] = "added"; });
    draft?.diff.changed.forEach((c) => { m[c.id] = "changed"; });
    return m;
  }, [draft]);

  if (none) return (
    <Card className="items-center gap-3 p-14 text-center">
      <GitBranch className="size-8 text-muted-foreground" />
      <div className="font-semibold">No open draft</div>
      <p className="max-w-md text-sm text-muted-foreground">Start a draft to change requirement rules. It copies the active version; nothing reaches joiners until it&apos;s approved.</p>
      <Button onClick={() => openDraft(router.push).then(() => load())}><GitBranch className="size-4" /> Start a draft</Button>
    </Card>
  );
  if (!draft) return <div className="space-y-3"><Skeleton className="h-16 rounded-xl" /><Skeleton className="h-96 rounded-xl" /></div>;

  const editable = draft.state === "draft";
  const n = diffCount(draft.diff);

  const save = async (r: Rule) => {
    try {
      setDraft(await api<VersionDetail>(`/api/policy/drafts/${draft.id}/rules/${encodeURIComponent(r.id)}`, { method: "PUT", json: r }));
      toast.success(`Rule ${r.id} saved to draft v${draft.number}`);
      setEdit(null);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const remove = async (r: Rule) => {
    try {
      setDraft(await api<VersionDetail>(`/api/policy/drafts/${draft.id}/rules/${encodeURIComponent(r.id)}`, { method: "DELETE" }));
      toast.success(`Rule ${r.id} removed from the draft`);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const discard = async () => {
    try {
      await api(`/api/policy/drafts/${draft.id}`, { method: "DELETE" });
      toast.success(`Draft v${draft.number} discarded`);
      router.push("/rules");
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <>
      <Link href="/rules" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Requirement rules</Link>
      <PageHeader title={`Draft v${draft.number}`}
        subtitle={editable ? "Edit rules here. Nothing changes for joiners until this draft is approved and published."
          : `${STATE_LABEL[draft.state]}. The draft is frozen while it's reviewed.`}
        actions={editable && <Button variant="outline" onClick={() => setEdit({ rule: blankRule(), isNew: true })}><Plus className="size-4" /> Add rule</Button>} />

      <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
        <div className="min-w-0 space-y-4">
        <Card className="gap-0 overflow-hidden p-0">
          <ul className="divide-y">
            {draft.rules.map((r) => (
              <li key={r.id} className="flex items-start gap-3 px-4 py-3">
                <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", marks[r.id] === "added" ? "bg-emerald-500" : marks[r.id] === "changed" ? "bg-amber-500" : "bg-transparent")}
                  title={marks[r.id] ? `${marks[r.id]} in this draft` : undefined} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm font-medium">{r.name || docTypes[r.item]?.name || r.item}
                    <span className="font-mono text-[11px] font-normal text-muted-foreground">{r.id}</span>
                    {r.compliance && <ShieldAlert className="size-3.5 text-amber-600" aria-label="compliance rule" />}
                    {marks[r.id] && <span className={cn("rounded px-1.5 text-[10px] font-medium", marks[r.id] === "added" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300" : "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300")}>{marks[r.id]}</span>}
                  </div>
                  <p className="text-xs text-muted-foreground">{matchText(r.match)} · {dueText(r.offset_days)} · {r.category}</p>
                </div>
                {editable && <div className="flex shrink-0">
                  <Button size="icon" variant="ghost" onClick={() => setEdit({ rule: r, isNew: false })} aria-label={`Edit ${r.id}`}><Pencil className="size-4" /></Button>
                  <Button size="icon" variant="ghost" onClick={() => remove(r)} aria-label={`Remove ${r.id}`}><Trash2 className="size-4" /></Button>
                </div>}
              </li>
            ))}
            {draft.diff.removed.map((r) => (
              <li key={`rm-${r.id}`} className="flex items-start gap-3 bg-rose-50/50 px-4 py-3 dark:bg-rose-500/5">
                <span className="mt-1.5 size-2 shrink-0 rounded-full bg-rose-500" />
                <div className="min-w-0 flex-1 text-sm">
                  <span className="font-medium line-through decoration-rose-400">{r.name || docTypes[r.item]?.name || r.item}</span>
                  <span className="ml-2 font-mono text-[11px] text-muted-foreground">{r.id}</span>
                  <p className="text-xs text-muted-foreground">Removed in this draft</p>
                </div>
                {editable && <Button size="sm" variant="ghost" onClick={() => save(r)}><Undo2 className="size-3.5" /> Restore</Button>}
              </li>
            ))}
          </ul>
        </Card>
        <DocPolicyCard key={draft.id + JSON.stringify(draft.doc_policy)} draft={draft} docTypes={docTypes} editable={editable} onSaved={setDraft} />
        </div>

        <div className="space-y-4">
          <Card className="gap-3 p-4">
            <div className="text-sm font-semibold">Changes vs the active version</div>
            <PolicyDiffView diff={draft.diff} empty="No changes yet. Edit, add or remove a rule." />
          </Card>
          <Card className="sticky top-4 gap-3 p-4">
            <div className="text-sm"><b>{n}</b> change{n === 1 ? "" : "s"} in this draft</div>
            {editable ? <>
              <Button disabled={!n} nativeButton={false} render={<Link href="/rules/draft/preview" />}>Preview impact <ArrowRight className="size-4" /></Button>
              <Button variant="ghost" className="text-destructive hover:text-destructive" onClick={() => setConfirmDiscard(true)}><RotateCcw className="size-4" /> Discard draft</Button>
            </> : <Button variant="outline" nativeButton={false} render={<Link href="/approvals?tab=policy" />}>View approval</Button>}
          </Card>
        </div>
      </div>

      {edit && <RuleDialog key={edit.rule.id} rule={edit.rule} isNew={edit.isNew} docTypes={docTypes} onClose={() => setEdit(null)} onSave={save} />}
      <Dialog open={confirmDiscard} onOpenChange={setConfirmDiscard}>
        <DialogContent>
          <DialogHeader><DialogTitle>Discard draft v{draft.number}?</DialogTitle>
            <DialogDescription>All {n} change{n === 1 ? "" : "s"} in it are thrown away. The active rules aren&apos;t affected.</DialogDescription></DialogHeader>
          <DialogFooter><Button variant="outline" onClick={() => setConfirmDiscard(false)}>Keep editing</Button>
            <Button variant="destructive" onClick={discard}>Discard draft</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
