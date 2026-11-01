"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Bot, Check, CheckCircle2, Lock, RotateCcw, ScanSearch, User, X, XCircle } from "lucide-react";
import { api, DECISION_LABEL, fmtWhen, REASON_CODE_LABEL, type DocumentDetail, type WorkItem } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { useMe } from "@/components/auth";
import { DocViewer } from "@/components/doc-viewer";
import { ReasonChip, TierBadge, docTypeLabel, useNow } from "@/components/review-bits";
import { SimpleSelect } from "@/components/simple-select";
import { StatusPill } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const FIELDS: [string, string, keyof DocumentDetail["employee"] | null][] = [
  ["full_name", "Name", "name"], ["date_of_birth", "Date of birth", "date_of_birth"], ["document_number", "Document number", null],
  ["issue_date", "Issue date", null], ["expiry_date", "Expiry date", null], ["address", "Address", null], ["issuer", "Issuer", null],
];
type Decision = "accept" | "request_correction" | "reject";
const TAKEOVER_MS = 30 * 60 * 1000;

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useMe();
  const [d, setD] = useState<DocumentDetail | null>(null);
  const [err, setErr] = useState("");
  const claimed = useRef(false);
  const now = useNow();

  const load = useCallback(() => api<DocumentDetail>(`/api/documents/${id}`).then((x) => { setD(x); return x; })
    .catch((e) => { setErr((e as Error).message); return null; }), [id]);

  const claim = useCallback(async (item: WorkItem, takeover = false) => {
    try {
      await api(`/api/review-queue/${item.id}/claim`, { method: "POST", json: { takeover } });
      if (takeover) toast.success("You're reviewing this now");
    } catch (e) {
      if (takeover) toast.error((e as Error).message);
    }
    await load();
  }, [load]);

  useEffect(() => {
    load().then((x) => {  // claim on open, so two reviewers never decide the same document
      const open = x?.reviews.find((r) => r.status === "open" && !r.assignee_id);
      if (x && open && !claimed.current && (x.can_decide || open.kind === "doc.spot_check")) {
        claimed.current = true;
        claim(open);
      }
    });
  }, [load, claim]);

  if (err) return <Card className="items-center gap-2 p-14 text-center"><div className="font-semibold">Couldn&apos;t open this document</div>
    <p className="text-sm text-muted-foreground">{err}</p></Card>;
  if (!d) return <div className="grid gap-4 lg:grid-cols-[3fr_2fr]"><Skeleton className="h-[70vh] rounded-xl" /><Skeleton className="h-[70vh] rounded-xl" /></div>;

  const doc = d.document;
  const review = d.reviews.find((r) => r.kind === "doc.review");
  const spot = d.reviews.find((r) => r.kind === "doc.spot_check");
  const holder = d.reviews.find((r) => r.assignee_id && r.assignee_id !== me?.id);
  const stale = holder?.claimed_at && now - new Date(holder.claimed_at).getTime() > TAKEOVER_MS;
  const ex = doc.extracted as Record<string, unknown>;
  const mism = new Set(d.mismatches.map((m) => m.field === "name" ? "full_name" : m.field));
  const conf = doc.confidence;
  const thr = d.policy.threshold;
  const auto = d.policy.decided_by === "system";

  return (
    <div className="pb-40">
      <Link href="/review" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Review queue</Link>
      <PageHeader title={`${docTypeLabel(doc.doc_type)} · ${d.employee.name}`}
        subtitle={`${doc.filename} · received ${fmtWhen(doc.received_at)} via ${doc.source}`}
        actions={<StatusPill status={doc.status} />} />

      {/* Policy facts first: they decide what the reviewer is accountable for. */}
      <Card className="mb-4 flex-row flex-wrap items-center gap-x-6 gap-y-2 p-4 text-sm">
        <Fact label="Risk tier"><TierBadge tier={d.policy.tier} />{d.policy.always_high && <span className="ml-1.5 text-[11px] text-muted-foreground">legal floor</span>}</Fact>
        <Fact label="Auto-accept threshold">{d.policy.tier === "high" ? <span className="text-muted-foreground">never (human only)</span> : thr != null ? <b className="tabular-nums">{thr.toFixed(2)}</b> : "—"}</Fact>
        <Fact label="Policy in effect">{d.policy.version ? <Link className="font-medium hover:underline" href="/rules/history">Rule set v{d.policy.version.number}</Link> : "—"}</Fact>
        <Fact label="Decided">{auto
          ? <span className="inline-flex items-center gap-1"><Bot className="size-3.5" /> automatically</span>
          : d.policy.decided_by ? <span className="inline-flex items-center gap-1"><User className="size-3.5" /> {d.policy.decided_by}</span> : "—"}</Fact>
        <Fact label="Why">{d.policy.reason_code ? <ReasonChip code={d.policy.reason_code} label={d.policy.reason_label ?? d.policy.reason_code} /> : "—"}</Fact>
      </Card>

      {holder && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-sky-300 bg-sky-50 p-3 text-sm text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200">
          <Lock className="size-4" /> Being reviewed by <b>{holder.assignee}</b> (claimed {fmtWhen(holder.claimed_at)}). You can read it, not decide it.
          {stale && <Button size="sm" variant="outline" className="ml-auto" onClick={() => claim(holder, true)}>Take over</Button>}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <DocViewer id={doc.id} mime={doc.mime} filename={doc.filename} className="lg:sticky lg:top-4 lg:self-start" />
        <div className="space-y-4">
          <Card className="gap-0 overflow-hidden p-0">
            <div className="border-b px-4 py-2.5 text-sm font-semibold">Extracted vs profile</div>
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground"><tr><th className="px-4 py-2 font-normal">Field</th><th className="font-normal">On document</th><th className="font-normal">On profile</th><th className="w-8" /></tr></thead>
              <tbody>
                {FIELDS.filter(([k, , p]) => ex[k] || p).map(([k, label, p]) => {
                  const bad = mism.has(k);
                  return (
                    <tr key={k} className={cn("border-t", bad && "bg-rose-50 dark:bg-rose-500/10")}>
                      <td className="px-4 py-2 text-xs text-muted-foreground">{label}</td>
                      <td className={cn("py-2 font-medium", bad && "text-rose-700 dark:text-rose-300")}>{ex[k] ? String(ex[k]) : <span className="font-normal text-muted-foreground">not found</span>}</td>
                      <td className="py-2">{p ? d.employee[p] || "—" : ""}</td>
                      <td>{bad ? <XCircle className="size-4 text-rose-600" /> : ex[k] && p ? <CheckCircle2 className="size-4 text-emerald-600" /> : null}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>

          <Card className="gap-2 p-4">
            <div className="text-sm font-semibold">Checks</div>
            {(doc.checks ?? []).length === 0 && <p className="text-xs text-muted-foreground">No content checks ran (file problem or AI unavailable).</p>}
            <ul className="space-y-1 text-sm">{(doc.checks ?? []).map((c) => (
              <li key={c.name} className="flex items-start gap-2">{c.ok ? <Check className="mt-0.5 size-4 text-emerald-600" /> : <X className="mt-0.5 size-4 text-rose-600" />}
                <span>{c.name.replace(/_/g, " ")}{c.detail && <span className="text-xs text-muted-foreground"> · {c.detail}</span>}</span></li>))}
            </ul>
            {doc.issues.length > 0 && <ul className="mt-1 space-y-0.5 text-xs text-amber-700 dark:text-amber-400">{doc.issues.map((x, i) => <li key={i}>• {x}</li>)}</ul>}
          </Card>

          <Card className="gap-2 p-4">
            <div className="text-sm font-semibold">AI facts <span className="font-normal text-muted-foreground">(evidence, not the decision)</span></div>
            <div className="text-xs">
              <div className="mb-1 flex justify-between text-muted-foreground"><span>Confidence</span><span className="tabular-nums">{Math.round(conf * 100)}%</span></div>
              <div className="relative h-2 rounded-full bg-muted">
                <div className={cn("h-full rounded-full", thr != null && conf < thr ? "bg-amber-500" : "bg-primary")} style={{ width: `${conf * 100}%` }} />
                {thr != null && d.policy.tier !== "high" && <div className="absolute -top-1 h-4 w-0.5 bg-foreground" style={{ left: `${thr * 100}%` }} title={`threshold ${thr}`} />}
              </div>
            </div>
            <dl className="grid grid-cols-2 gap-1 text-xs">
              <dt className="text-muted-foreground">Model</dt><dd className="truncate font-mono">{doc.model ?? "—"}</dd>
              <dt className="text-muted-foreground">Prompt version</dt><dd className="font-mono">{doc.prompt_version ?? "—"}</dd>
              <dt className="text-muted-foreground">File sha256</dt><dd className="truncate font-mono" title={doc.sha256 ?? ""}>{doc.sha256?.slice(0, 12) ?? "missing"}</dd>
            </dl>
          </Card>

          <Card className="gap-2 p-4">
            <div className="text-sm font-semibold">Decision history</div>
            <ol className="space-y-2.5 border-l pl-4">{d.decisions.map((x) => (
              <li key={x.id} className="relative text-sm">
                <span className={cn("absolute top-1.5 -left-[21px] size-2.5 rounded-full ring-4 ring-background", x.decided_by ? "bg-primary" : "bg-zinc-400")} />
                <div className="font-medium">{DECISION_LABEL[x.kind] ?? x.kind} <span className="font-normal text-muted-foreground">→ {x.status_after}</span></div>
                <div className="text-xs text-muted-foreground">{x.decided_by_email ?? "system"} · {fmtWhen(x.at)}
                  {x.decided_by ? ` · ${REASON_CODE_LABEL[x.reason_code] ?? x.reason_code}` : x.threshold != null ? ` · ${x.tier} tier, threshold ${x.threshold.toFixed(2)}` : ""}</div>
                {x.reason_text && <p className="text-xs">{x.reason_text}</p>}
              </li>))}
            </ol>
          </Card>
        </div>
      </div>

      {!holder && spot && <SpotFooter item={spot} onDone={load} />}
      {!holder && !spot && review && d.can_decide && <DecisionFooter d={d} onDone={load} />}
      {!holder && !review && !spot && d.can_override && <OverrideFooter d={d} onDone={load} />}
      {!holder && review && !d.can_decide && <ReadOnlyFooter text="High-risk documents are decided by an approver. You can read the evidence and history." />}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-[11px] tracking-wide text-muted-foreground uppercase">{label}</div><div className="mt-0.5 flex items-center">{children}</div></div>;
}

function Footer({ children }: { children: React.ReactNode }) {
  return <div className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/95 shadow-[0_-8px_24px_-12px_rgba(0,0,0,0.15)] backdrop-blur md:left-60">
    <div className="mx-auto max-w-7xl p-4 pr-24">{children}</div></div>;  // pr-24 keeps the buttons clear of the assistant bubble
}

function ReadOnlyFooter({ text }: { text: string }) {
  return <Footer><p className="flex items-center gap-2 text-sm text-muted-foreground"><Lock className="size-4" /> {text}</p></Footer>;
}

function ReasonInputs({ codes, code, setCode, text, setText }: { codes: string[]; code: string; setCode: (v: string) => void; text: string; setText: (v: string) => void }) {
  return (
    <div className="flex flex-1 flex-wrap gap-2">
      <SimpleSelect value={code} onChange={setCode} placeholder="Reason…" className="w-56" options={codes.map((c) => ({ value: c, label: REASON_CODE_LABEL[c] ?? c }))} />
      <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Why (required, kept in the audit log)" className="min-w-64 flex-1" maxLength={2000} />
    </div>
  );
}

function useSubmit(onDone: () => void) {
  const key = useRef("");
  const [busy, setBusy] = useState(false);
  const run = async (path: string, body: object, ok: string) => {
    key.current ||= crypto.randomUUID();  // a double click or retry re-sends the same key: one decision row
    setBusy(true);
    try {
      await api(path, { method: "POST", json: { ...body, decision_key: key.current } });
      toast.success(ok);
      key.current = "";
      onDone();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

function DecisionFooter({ d, onDone }: { d: DocumentDetail; onDone: () => void }) {
  const [code, setCode] = useState("verified_against_original");
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"" | "correction" | "fields">("");
  const [issues, setIssues] = useState(d.document.issues.join("\n"));
  const [fields, setFields] = useState<Record<string, string>>({});
  const { busy, run } = useSubmit(onDone);
  const ok = !!code && text.trim().length >= 3 && !busy;
  const send = (decision: Decision) => run(`/api/documents/${d.document.id}/decision`, {
    decision, reason_code: code, reason_text: text,
    corrected_fields: Object.keys(fields).length ? fields : undefined,
    issues: decision === "request_correction" ? issues.split("\n").filter((x) => x.trim()) : undefined,
  }, { accept: "Accepted", request_correction: "Correction requested; the joiner is emailed", reject: "Rejected" }[decision]);
  return (
    <Footer>
      {mode === "correction" && <Textarea value={issues} onChange={(e) => setIssues(e.target.value)} rows={3} className="mb-3"
        placeholder="One problem per line. The joiner sees exactly this in the correction email." />}
      {mode === "fields" && <div className="mb-3 grid gap-2 sm:grid-cols-4">{FIELDS.slice(0, 4).map(([k, label]) => (
        <Input key={k} placeholder={`${label}: ${String((d.document.extracted as Record<string, unknown>)[k] ?? "")}`} value={fields[k] ?? ""}
          onChange={(e) => setFields((f) => { const n = { ...f, [k]: e.target.value }; if (!e.target.value) delete n[k]; return n; })} />))}</div>}
      <div className="flex flex-wrap items-center gap-2">
        <ReasonInputs codes={d.reason_codes} code={code} setCode={setCode} text={text} setText={setText} />
        <Button variant="ghost" size="sm" onClick={() => setMode(mode === "fields" ? "" : "fields")}>Correct fields</Button>
        <Button variant="outline" disabled={!ok} onClick={() => send("reject")}><X className="size-4" /> Reject</Button>
        <Button variant="outline" disabled={!ok} onClick={() => mode === "correction" ? send("request_correction") : setMode("correction")}>
          <RotateCcw className="size-4" /> {mode === "correction" ? "Send correction request" : "Request correction"}</Button>
        <Button disabled={!ok} onClick={() => send("accept")}><Check className="size-4" /> Accept</Button>
      </div>
    </Footer>
  );
}

function OverrideFooter({ d, onDone }: { d: DocumentDetail; onDone: () => void }) {
  const [code, setCode] = useState("other");
  const [text, setText] = useState("");
  const { busy, run } = useSubmit(onDone);
  const to: Decision = d.document.status === "Accepted" ? "request_correction" : "accept";
  return (
    <Footer>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Decided ({d.document.status}). Reverse it?</span>
        <ReasonInputs codes={d.reason_codes} code={code} setCode={setCode} text={text} setText={setText} />
        <Button variant="outline" disabled={busy || text.trim().length < 3}
          onClick={() => run(`/api/documents/${d.document.id}/override`, { decision: to, reason_code: code, reason_text: text }, "Decision overridden")}>
          <RotateCcw className="size-4" /> Override to {to === "accept" ? "Accepted" : "Correction requested"}</Button>
      </div>
    </Footer>
  );
}

function SpotFooter({ item, onDone }: { item: WorkItem; onDone: () => void }) {
  const { me } = useMe();
  const [text, setText] = useState("");
  const { busy, run } = useSubmit(onDone);
  if (me?.role !== "approver") return <ReadOnlyFooter text="Spot checks are for approvers." />;
  const verdict = (v: "pass" | "fail") => run(`/api/review-queue/${item.id}/spot-check`, { verdict: v, reason_text: text },
    v === "pass" ? "Marked correct" : "Marked wrong; the document is back in review");
  return (
    <Footer>
      <div className="flex flex-wrap items-center gap-2">
        <ScanSearch className="size-4 text-violet-600" />
        <span className="text-sm">Spot check: was the automatic acceptance right?</span>
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Why (required)" className="min-w-64 flex-1" maxLength={2000} />
        <Button variant="outline" disabled={busy || text.trim().length < 3} onClick={() => verdict("fail")}><X className="size-4" /> Wrong</Button>
        <Button disabled={busy || text.trim().length < 3} onClick={() => verdict("pass")}><Check className="size-4" /> Correct</Button>
      </div>
    </Footer>
  );
}
