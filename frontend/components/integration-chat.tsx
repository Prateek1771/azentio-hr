"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, Bot, Check, Pencil, Send } from "lucide-react";
import { API, api, describeSchedule, type IntegrationDetail, type MappingRule } from "@/lib/api";
import { StepBar } from "@/components/integration-bits";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const tz = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

async function call(path: string, method: string, json?: unknown): Promise<IntegrationDetail> {
  const res = await fetch(`${API}${path}`, { method, headers: { "content-type": "application/json" }, body: json === undefined ? undefined : JSON.stringify(json) });
  const body = await res.json();
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "That couldn't be saved");
  return body;
}

/** The setup conversation, with inline mapping/offer cards and a live summary of every setting. */
export function IntegrationChat({ it, onChange }: { it: IntegrationDetail; onChange: (d: IntegrationDetail) => void }) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [showMapping, setShowMapping] = useState(false);
  const [showOffer, setShowOffer] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [it.messages.length, pending]);

  const send = async (message: string) => {
    if (!message.trim() || pending) return;
    setPending(message);
    setText("");
    try {
      onChange(await api<IntegrationDetail>(`/api/integrations/${it.id}/chat`, { method: "POST", json: { message, timezone: tz() } }));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPending(null);
    }
  };

  const mappingCard = (it.setup_step === "mapping" && it.mapping.length > 0 && !it.mapping_approved_at) || showMapping;
  const offerCard = (it.setup_step === "offer" && it.offer_enabled) || showOffer;
  const change = (prefix: string) => { setText(prefix); input.current?.focus(); };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
      <Card className="flex h-[calc(100vh-15rem)] min-h-[520px] flex-col gap-0 overflow-hidden p-0">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {it.messages.map((m) => (
            <div key={m.id} className={cn("flex gap-2", m.role === "user" ? "justify-end" : "justify-start")}>
              {m.role === "agent" && <div className="grid size-7 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 text-white"><Bot className="size-3.5" /></div>}
              <div className={cn("max-w-[85%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap",
                m.role === "user" ? "rounded-tr-sm bg-source text-white" : "rounded-tl-sm bg-muted")}>{m.content}</div>
            </div>
          ))}
          {pending && (
            <>
              <div className="flex justify-end"><div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-source px-3.5 py-2 text-sm text-white">{pending}</div></div>
              <div className="flex gap-1 pl-9">{[0, 1, 2].map((i) => <span key={i} className="size-1.5 animate-bounce rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 120}ms` }} />)}</div>
            </>
          )}
          {mappingCard && <MappingCard it={it} onChange={onChange} />}
          {offerCard && <OfferCard it={it} onChange={onChange} />}
          {it.setup_step === "documents" && (
            <p className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
              Documents come from your <Link href="/rules" className="font-medium text-primary underline">Requirement rules</Link>.
              Candidates get a Gmail request; each reply is checked by AI and the rules before it reaches you.
            </p>
          )}
          <div ref={end} />
        </div>
        <div className="space-y-2 border-t p-3">
          {it.quick_replies.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {it.quick_replies.map((q) => (
                <button key={q} onClick={() => send(q)} disabled={!!pending}
                  className="rounded-full border px-3 py-1 text-xs transition-colors hover:border-primary hover:bg-primary/5">{q}</button>
              ))}
            </div>
          )}
          <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="flex gap-2">
            <Input ref={input} value={text} onChange={(e) => setText(e.target.value)} placeholder="Tell the agent what you want, in your own words" disabled={!!pending} />
            <Button type="submit" size="icon" disabled={!!pending || !text.trim()} aria-label="Send"><Send className="size-4" /></Button>
          </form>
        </div>
      </Card>
      <SetupAside it={it} change={change} openMapping={() => setShowMapping(true)} openOffer={() => setShowOffer(true)} />
    </div>
  );
}

function Row({ label, value, done, onChange, onView }: { label: string; value: React.ReactNode; done: boolean; onChange: () => void; onView?: () => void }) {
  return (
    <div className="border-b py-2.5 last:border-0">
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1 font-medium">{done && <Check className="size-3.5 text-emerald-600" />}{label}</span>
        <span className="flex gap-2">
          {onView && <button onClick={onView} className="text-primary hover:underline">View</button>}
          <button onClick={onChange} className="text-muted-foreground hover:text-foreground">Change</button>
        </span>
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">{value ?? <i>Not answered yet</i>}</div>
    </div>
  );
}

function SetupAside({ it, change, openMapping, openOffer }: { it: IntegrationDetail; change: (s: string) => void; openMapping: () => void; openOffer: () => void }) {
  const steps = ["records", "mapping", "notifications", "documents", "offer", "schedule", "review"];
  const at = steps.indexOf(it.setup_step);
  const notif = [...it.recipients.map((r) => r.name), ...it.notify_emails];
  const events = [it.notify_on_created && "created", it.notify_on_success && "success", it.notify_on_failure && "failure"].filter(Boolean).join(", ");
  const excluded = [...it.log_exclusions.sections, ...it.log_exclusions.fields];
  return (
    <Card className="h-fit gap-2 p-4">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold">Setup</span>
        <span className={cn("rounded-full px-2 py-0.5", it.agent_is_simulated ? "bg-muted text-muted-foreground" : "bg-primary/10 text-primary")}>
          {it.agent_is_simulated ? "Rule-based assistant" : "AI agent connected (free model)"}
        </span>
      </div>
      <StepBar step={it.setup_step} />
      <div>
        <Row label="Candidates" done={at > 0} onChange={() => change("Pick up candidates who are ")}
          value={at > 0 || it.source_filter.statuses.length ? `${it.source_filter.statuses.map((s) => s.replace("_", " ")).join(", ")} in ${it.source_filter.departments.length ? it.source_filter.departments.join(", ") : "all departments"}; ${it.duplicate_policy} existing` : null} />
        <Row label="Field mapping" done={!!it.mapping_approved_at} onChange={() => change("Map ")} onView={it.mapping.length ? openMapping : undefined}
          value={it.mapping.length ? `${it.mapping.length} fields, ${it.mapping_approved_at ? "approved" : "not approved"}` : null} />
        <Row label="Notifications" done={at > 2} onChange={() => change("Notify ")}
          value={at > 2 ? (notif.length ? `${notif.join(", ")} (${events || "no events"})` : "Nobody") : null} />
        <Row label="Document verification" done={it.doc_verification_enabled !== null} onChange={() => change("Document verification ")}
          value={it.doc_verification_enabled === null ? null : it.doc_verification_enabled ? "Yes — Gmail request + AI checks" : "No"} />
        <Row label="Offer letter" done={it.offer_enabled === false || it.offer_template_status === "approved"} onChange={() => change("Offer letter ")}
          onView={it.offer_enabled ? openOffer : undefined}
          value={it.offer_enabled === null ? null : it.offer_enabled ? `Yes, format ${it.offer_template_status === "approved" ? "approved" : "not approved"}` : "No"} />
        <Row label="Schedule" done={at > 5} onChange={() => change("Change the schedule to ")} value={at > 5 ? describeSchedule(it.schedule) : null} />
        <Row label="Integration log" done onChange={() => change("Exclude ")} value={excluded.length ? `Leaving out: ${excluded.join(", ")}` : "Records everything"} />
      </div>
      {it.status !== "active" && at >= 5 && it.problems.length > 0 && (
        <p className="rounded-md bg-amber-50 px-2.5 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">{it.problems[0]}</p>
      )}
      {it.delivery_is_simulated && (
        <p className="text-[11px] text-muted-foreground">Gmail isn&apos;t configured, so messages are recorded but not sent.</p>
      )}
    </Card>
  );
}

function MappingCard({ it, onChange }: { it: IntegrationDetail; onChange: (d: IntegrationDetail) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ candidate: string; values: Record<string, unknown>; error: string | null }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const label = (fields: { key: string; label: string }[], k: string | null) => fields.find((f) => f.key === k)?.label ?? k;
  const byTarget = Object.fromEntries(it.mapping.map((r) => [r.target_field, r]));
  const rows = it.target_fields.filter((f) => f.required || byTarget[f.key]);
  const unmapped = it.target_fields.filter((f) => !f.required && !byTarget[f.key]);
  const missing = it.target_fields.filter((f) => f.required && !f.generated && !byTarget[f.key]);

  const save = async (target: string, patch: Partial<MappingRule>) => {
    const cur = byTarget[target] ?? { target_field: target, source_field: null, default_value: null, transform: "none" };
    const next = { ...cur, ...patch, origin: "hr" as const, confidence: null, rationale: null };
    const rules = [...it.mapping.filter((r) => r.target_field !== target), ...(next.source_field || next.default_value ? [next] : [])];
    try { onChange(await call(`/api/integrations/${it.id}/mapping`, "PUT", { rules })); } catch (e) { toast.error((e as Error).message); }
  };
  const approve = async () => {
    try { onChange(await call(`/api/integrations/${it.id}/mapping/approve`, "POST")); toast.success("Mapping approved"); } catch (e) { toast.error((e as Error).message); }
  };
  const runPreview = async () => {
    setBusy(true);
    try { setPreview(await api(`/api/integrations/${it.id}/mapping/preview`, { method: "POST" })); } finally { setBusy(false); }
  };

  return (
    <Card className="gap-2 p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold"><span className="text-source">{it.source_name}</span> <ArrowRight className="inline size-3" /> <span className="text-target">{it.target_name}</span></span>
        <span className={cn("rounded-full px-2 py-0.5 font-medium", it.mapping_approved_at ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700")}>
          {it.mapping_approved_at ? "Approved" : "Needs approval"}</span>
      </div>
      <div className="divide-y rounded-lg border text-sm">
        {rows.map((f) => {
          const r = byTarget[f.key];
          return (
            <div key={f.key}>
              <button onClick={() => setOpen(open === f.key ? null : f.key)} className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/40">
                <span className="min-w-0 flex-1 truncate">
                  {r?.source_field ? label(it.source_fields, r.source_field) : r?.default_value ? <i>Always “{r.default_value}”</i>
                    : f.generated ? <span className="text-muted-foreground">Generated automatically</span> : <span className="text-amber-600">Needs a value</span>}
                </span>
                <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate font-medium">{f.label}{f.required && !f.generated && <span className="text-rose-600">*</span>}</span>
                {r?.confidence != null ? (
                  <span title={`${Math.round(r.confidence * 100)}% match${r.rationale ? ` — ${r.rationale}` : ""}`} className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
                    <span className="block h-full bg-primary" style={{ width: `${r.confidence * 100}%` }} />
                  </span>
                ) : <Pencil className="size-3.5 text-muted-foreground" />}
              </button>
              {open === f.key && (
                <div className="grid gap-2 bg-muted/30 px-3 py-2 sm:grid-cols-2">
                  <SimpleSelect value={r?.source_field ?? ""} onChange={(v) => save(f.key, { source_field: v || null })} placeholder="No source" className="w-full"
                    options={it.source_fields.map((s) => ({ value: s.key, label: s.label }))} />
                  <Input defaultValue={r?.default_value ?? ""} placeholder="Default value" onBlur={(e) => e.target.value !== (r?.default_value ?? "") && save(f.key, { default_value: e.target.value || null })} />
                </div>
              )}
            </div>
          );
        })}
      </div>
      {unmapped.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">Not mapped:
          {unmapped.map((f) => <button key={f.key} onClick={() => setOpen(f.key)} className="rounded bg-muted px-1.5 py-0.5 hover:bg-muted/70">{f.label}</button>)}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={approve} disabled={!!it.mapping_approved_at || missing.length > 0}
          title={missing.length ? `${missing[0].label} still needs a value` : undefined}><Check className="size-3.5" /> Approve mapping</Button>
        <Button size="sm" variant="outline" onClick={runPreview} disabled={busy}>{busy ? "Building preview…" : "Preview on real records"}</Button>
      </div>
      {preview && (
        <ul className="space-y-1 rounded-lg bg-muted/30 p-2 text-xs">
          {preview.map((p, i) => (
            <li key={i}><b>{p.candidate}:</b> {p.error ? <span className="text-rose-600">{p.error}</span> : Object.entries(p.values).map(([k, v]) => `${k}=${v}`).join(" · ")}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function OfferCard({ it, onChange }: { it: IntegrationDetail; onChange: (d: IntegrationDetail) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(it.offer_template);
  const status = { approved: ["Approved", "bg-emerald-50 text-emerald-700"], rejected: ["Rejected", "bg-rose-50 text-rose-700"],
    pending_approval: ["Needs approval", "bg-amber-50 text-amber-700"], none: ["No format yet", "bg-muted"] }[it.offer_template_status];
  const act = async (path: string, method: string, json?: unknown, msg?: string) => {
    try { onChange(await call(path, method, json)); if (msg) toast.success(msg); setEditing(false); } catch (e) { toast.error((e as Error).message); }
  };
  const parts = it.offer_template.split(/(\{\{\s*[\w.]+\s*\}\})/g);
  return (
    <Card className="gap-2 p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold">Offer letter format</span>
        <span className={cn("rounded-full px-2 py-0.5 font-medium", status[1])}>{status[0]}</span>
      </div>
      {editing ? (
        <>
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={10} className="font-mono text-xs" />
          <p className="text-[11px] text-muted-foreground">Placeholders: {it.offer_placeholders.map((p) => `{{${p}}}`).join(" ")}</p>
        </>
      ) : (
        <div className="rounded-lg border bg-white p-3 text-xs whitespace-pre-wrap text-zinc-800">
          {parts.map((p, i) => /^\{\{/.test(p) ? <span key={i} className="rounded bg-primary/10 px-1 text-primary">{p.replace(/[{}\s]/g, "")}</span> : <span key={i}>{p}</span>)}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {editing ? (
          <>
            <Button size="sm" onClick={() => act(`/api/integrations/${it.id}/offer/template`, "PUT", { content: draft }, "Format saved — it needs approval")}>Save format</Button>
            <Button size="sm" variant="ghost" onClick={() => { setDraft(it.offer_template); setEditing(false); }}>Cancel</Button>
          </>
        ) : (
          <>
            {it.offer_template_status !== "approved" && <Button size="sm" onClick={() => act(`/api/integrations/${it.id}/offer/decision`, "POST", { approve: true }, "Offer letter format approved")}><Check className="size-3.5" /> Approve format</Button>}
            <Button size="sm" variant="outline" onClick={() => { setDraft(it.offer_template); setEditing(true); }}><Pencil className="size-3.5" /> Edit</Button>
            {it.offer_template_status === "pending_approval" && <Button size="sm" variant="ghost" onClick={() => act(`/api/integrations/${it.id}/offer/decision`, "POST", { approve: false }, "Format rejected")}>Reject</Button>}
          </>
        )}
      </div>
    </Card>
  );
}
