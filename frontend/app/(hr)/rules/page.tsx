"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { FileText, GitBranch, History, ListTodo, Pencil, ShieldAlert, ShieldCheck } from "lucide-react";
import { api, fmtWhen, STATE_LABEL, type Rule, type RuleSetVersion, type VersionsResp } from "@/lib/api";
import { openDraft } from "@/lib/policy";
import { PageHeader } from "@/components/app-shell";
import { can, useMe } from "@/components/auth";
import { dueText, matchText } from "@/components/rule-form";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface RulesResp {
  rules: Rule[]; doc_types: Record<string, { name: string }>;
  version: { id: number; number: number; published_at: string; author_email: string | null } | null;
}
const KIND_ICON = { document: FileText, task: ListTodo, ack: ShieldCheck } as Record<string, typeof FileText>;

export default function RulesPage() {
  const router = useRouter();
  const { me } = useMe();
  const canWrite = can(me, "onboarding");
  const [data, setData] = useState<RulesResp | null>(null);
  const [open, setOpen] = useState<RuleSetVersion | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<RulesResp>("/api/rules").then((d) => { setData(d); setError(""); }).catch((e) => setError((e as Error).message));
    api<VersionsResp>("/api/policy/versions").then((v) => setOpen(v.versions.find((x) => x.id === v.open_id) ?? null)).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const label = (r: Rule) => r.name || data?.doc_types?.[r.item]?.name || r.item;
  const v = data?.version;
  const go = (edit?: string) => openDraft(router.push, edit).catch((e) => toast.error((e as Error).message));

  return (
    <>
      <PageHeader title="Requirement rules"
        subtitle={v ? `Version ${v.number} active since ${fmtWhen(v.published_at)}${v.author_email ? ` · authored by ${v.author_email}` : " · migrated from the original rules"}. Changes go through a draft, an impact preview and approval.`
          : "Which documents, tasks and acknowledgements apply by role, department, grade and location, and why."}
        actions={<>
          <Button variant="outline" nativeButton={false} render={<Link href="/rules/history" />}><History className="size-4" /> Version history</Button>
          {canWrite && <Button onClick={() => go()}><GitBranch className="size-4" /> {open?.state === "draft" ? "Continue draft" : "Propose changes"}</Button>}
        </>} />

      {open && (
        <Card className="sticky top-2 z-10 mb-4 flex-row flex-wrap items-center gap-3 border-amber-300 bg-amber-50/80 p-3.5 backdrop-blur dark:border-amber-500/40 dark:bg-amber-500/10">
          <GitBranch className="size-5 text-amber-600" />
          <div className="min-w-0 flex-1 text-sm">
            <span className="font-medium">Draft v{open.number}</span> by {open.author_email ?? "system"} · {STATE_LABEL[open.state]}
            {open.state === "pending_approval" && <> · {open.approvals.filter((a) => a.decision === "approve").length} of {open.required_approvals} approval{open.required_approvals > 1 ? "s" : ""}</>}
            {open.title && open.state !== "draft" && <span className="text-muted-foreground"> · {open.title}</span>}
          </div>
          {open.state === "draft" && canWrite && <Button size="sm" onClick={() => go()}>Open draft</Button>}
          {open.state === "pending_approval" && <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/approvals?tab=policy" />}>View approval</Button>}
        </Card>
      )}

      {error ? (
        <Card className="items-center gap-2 p-10 text-center"><p className="text-sm text-destructive">{error}</p>
          <Button size="sm" variant="outline" onClick={load}>Retry</Button></Card>
      ) : (
        <Card className="gap-0 overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
                <tr><th className="px-4 py-2.5 font-medium">Requirement</th><th className="px-4 font-medium">Applies to</th><th className="px-4 font-medium">Due</th>
                  <th className="px-4 font-medium">Owner</th><th className="px-4 font-medium">Depends on</th><th /></tr>
              </thead>
              <tbody>
                {!data ? Array.from({ length: 6 }).map((_, i) => <tr key={i} className="border-t"><td colSpan={6} className="p-3"><Skeleton className="h-9" /></td></tr>)
                  : data.rules.map((r) => {
                    const Icon = KIND_ICON[r.kind] ?? FileText;
                    return (
                      <tr key={r.id} className="border-t align-top">
                        <td className="max-w-md px-4 py-3">
                          <div className="flex flex-wrap items-center gap-2 font-medium"><Icon className="size-4 text-primary" />{label(r)}
                            <span className="font-mono text-[11px] font-normal text-muted-foreground">{r.id}</span>
                            {r.compliance && <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
                              <ShieldAlert className="size-3" /> Compliance</span>}</div>
                          <p className="mt-0.5 text-xs text-muted-foreground">{r.description}</p>
                        </td>
                        <td className="px-4 py-3 text-xs">{matchText(r.match)}</td>
                        <td className="px-4 py-3 text-xs whitespace-nowrap">{dueText(r.offset_days)}</td>
                        <td className="px-4 py-3 text-xs">{r.category}{r.auto && <div className="text-primary">auto-{r.auto}</div>}</td>
                        <td className="px-4 py-3 text-xs text-muted-foreground">{r.depends_on.join(", ") || "—"}</td>
                        <td className="px-2 py-2 whitespace-nowrap">
                          {canWrite && <Button size="icon" variant="ghost" onClick={() => go(r.id)} aria-label={`Change ${r.id} in a draft`}
                            title="Change in a draft"><Pencil className="size-4" /></Button>}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
