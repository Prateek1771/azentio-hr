"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Braces, Check, FileText, FlaskConical, KeyRound, Mail, Plus, Trash2, Wand2, X } from "lucide-react";
import { API, api, type AccessRule, type DocOption, type IntegrationDetail, type MappingRule } from "@/lib/api";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

async function call(path: string, method: string, json?: unknown): Promise<IntegrationDetail> {
  const res = await fetch(`${API}${path}`, { method, headers: { "content-type": "application/json" }, body: json === undefined ? undefined : JSON.stringify(json) });
  const body = await res.json();
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "That couldn't be saved");
  return body;
}

type Tested = { candidate: string; value: unknown; error: string | null }[];

/** Expression / unique-email editor for one mapping row: field + function chips, live test on real records. */
export function ExpressionEditor({ it, rule, target, onSave, onCancel }: {
  it: IntegrationDetail; rule?: MappingRule; target: string;
  onSave: (patch: Partial<MappingRule>) => void; onCancel: () => void;
}) {
  const [kind, setKind] = useState<"expression" | "unique_email">(rule?.transform === "unique_email" ? "unique_email" : "expression");
  const [expr, setExpr] = useState(rule?.expression ?? (kind === "unique_email" ? it.email_presets[0]?.expression ?? "" : ""));
  const [domain, setDomain] = useState(rule?.email_domain ?? "azentio.com");
  const [result, setResult] = useState<Tested | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Live test, debounced: HR sees the output on real candidates while typing.
  useEffect(() => {
    if (!expr.trim()) return;
    const t = setTimeout(async () => {
      try {
        const r = await api<Tested>(`/api/integrations/${it.id}/mapping/test-expression`, {
          method: "POST", json: { expression: expr, transform: kind, email_domain: domain } });
        setResult(r); setErr(null);
      } catch (e) { setErr((e as Error).message); setResult(null); }
    }, 400);
    return () => clearTimeout(t);
  }, [expr, kind, domain, it.id]);

  const insert = (s: string) => setExpr((x) => (x && !x.endsWith(" ") && !x.endsWith("(") ? `${x} + ${s}` : `${x}${s}`));
  const preset = it.email_presets.find((p) => p.expression === expr)?.key ?? "";
  return (
    <div className="space-y-2 rounded-lg border bg-background p-3" onClick={(e) => e.stopPropagation()}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-md border p-0.5 text-xs">
          {([["expression", Braces, "Expression"], ["unique_email", Mail, "Unique email"]] as const).map(([k, Icon, label]) => (
            <button key={k} type="button" onClick={() => { setKind(k); if (k === "unique_email" && !expr) setExpr(it.email_presets[0]?.expression ?? ""); }}
              className={cn("flex items-center gap-1 rounded px-2 py-1", kind === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
              <Icon className="size-3" /> {label}</button>
          ))}
        </div>
        <span className="text-xs text-muted-foreground">for <b>{target}</b></span>
      </div>
      {kind === "unique_email" && (
        <div className="flex flex-wrap items-center gap-2">
          <SimpleSelect value={preset} onChange={(v) => v && setExpr(it.email_presets.find((p) => p.key === v)!.expression)} placeholder="Custom rule"
            className="w-40" options={it.email_presets.map((p) => ({ value: p.key, label: p.key }))} />
          <span className="text-sm text-muted-foreground">@</span>
          <Input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="azentio.com" className="w-44" />
          <span className="text-[11px] text-muted-foreground">Taken? adds 2, 3… (john.doe2)</span>
        </div>
      )}
      <Textarea value={expr} onChange={(e) => setExpr(e.target.value)} rows={2} spellCheck={false}
        placeholder={kind === "unique_email" ? 'slug(first_name) + "." + slug(last_name)' : 'lower(first_name) + "." + lower(last_name)'}
        className="font-mono text-xs" />
      <div className="space-y-1">
        <div className="flex flex-wrap gap-1">
          <span className="text-[11px] text-muted-foreground">Fields:</span>
          {it.source_fields.map((f) => (
            <button key={f.key} type="button" onClick={() => insert(f.key)} title={f.label}
              className="rounded bg-source/10 px-1.5 py-0.5 font-mono text-[11px] text-source hover:bg-source/20">{f.key}</button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          <span className="text-[11px] text-muted-foreground">Functions:</span>
          {it.expression_functions.map((f) => (
            <button key={f.name} type="button" onClick={() => setExpr((x) => `${x}${f.name}(`)} title={f.example}
              className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] hover:bg-muted/70">{f.name}()</button>
          ))}
        </div>
      </div>
      <div className="rounded-md bg-muted/40 p-2 text-xs">
        <div className="mb-1 flex items-center gap-1 font-medium"><FlaskConical className="size-3" /> Live result on real records</div>
        {err ? <p className="text-rose-600">{err}</p> : !result ? <p className="text-muted-foreground">Type an expression to see what it produces.</p> : (
          <ul className="space-y-0.5">
            {result.map((r, i) => (
              <li key={i} className="flex gap-2"><span className="w-32 shrink-0 truncate text-muted-foreground">{r.candidate}</span>
                {r.error ? <span className="text-rose-600">{r.error}</span> : <code className="font-mono">{String(r.value)}</code>}</li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={!!err || !expr.trim()} onClick={() => onSave(kind === "unique_email"
          ? { transform: "unique_email", expression: expr, email_domain: domain, source_field: null, default_value: null }
          : { transform: "expression", expression: expr, email_domain: null, source_field: null })}><Check className="size-3.5" /> Use this</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/** Documents per role: pick a role, see what the Requirement rules ask it for, trim, save. */
export function DocumentsCard({ it, onChange }: { it: IntegrationDetail; onChange: (d: IntegrationDetail) => void }) {
  const [role, setRole] = useState(it.roles[0] ?? "*");
  const [opts, setOpts] = useState<DocOption[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  useEffect(() => {
    api<DocOption[]>(`/api/integrations/${it.id}/documents/options?role=${encodeURIComponent(role)}`).then((o) => {
      setOpts(o);
      const saved = it.role_documents[role];
      setPicked(new Set(saved ?? o.filter((x) => x.suggested).map((x) => x.key)));
    });
  }, [it.id, role, it.role_documents]);
  const toggle = (k: string) => setPicked((p) => { const n = new Set(p); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const save = async () => {
    try { onChange(await call(`/api/integrations/${it.id}/documents/roles`, "PUT", { role, documents: [...picked] })); toast.success(`Document set saved for ${role === "*" ? "every role" : role}`); }
    catch (e) { toast.error((e as Error).message); }
  };
  const remove = async (r: string) => {
    try { onChange(await call(`/api/integrations/${it.id}/documents/roles?role=${encodeURIComponent(r)}`, "DELETE")); } catch (e) { toast.error((e as Error).message); }
  };
  const name = (k: string) => it.doc_types.find((d) => d.key === k)?.name ?? k;
  return (
    <Card className="gap-2 p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1 font-semibold"><FileText className="size-3.5" /> Documents by role</span>
        <span className="text-muted-foreground">From your Requirement rules</span>
      </div>
      {Object.keys(it.role_documents).length > 0 && (
        <div className="space-y-1">
          {Object.entries(it.role_documents).map(([r, docs]) => (
            <div key={r} className="flex items-start gap-2 rounded-md bg-emerald-50 px-2 py-1.5 text-xs dark:bg-emerald-500/10">
              <Check className="mt-0.5 size-3 text-emerald-600" />
              <div className="min-w-0 flex-1"><b>{r === "*" ? "Every other role" : r}:</b> {docs.map(name).join(", ")}</div>
              <button onClick={() => remove(r)} aria-label={`Remove ${r}`} className="text-muted-foreground hover:text-rose-600"><X className="size-3.5" /></button>
            </div>
          ))}
        </div>
      )}
      <SimpleSelect value={role} onChange={(v) => setRole(v || "*")} placeholder="Every role" className="w-full"
        options={it.roles.map((r) => ({ value: r, label: r }))} />
      <div className="divide-y rounded-lg border">
        {opts === null ? <div className="p-3 text-xs text-muted-foreground">Loading…</div> : opts.map((o) => (
          <label key={o.key} className="flex cursor-pointer items-start gap-2 px-3 py-2 text-sm hover:bg-muted/40">
            <input type="checkbox" checked={picked.has(o.key)} onChange={() => toggle(o.key)} className="mt-1 accent-[var(--primary)]" />
            <span className="min-w-0 flex-1">
              <span className={cn(!o.suggested && "text-muted-foreground")}>{o.name}</span>
              <span className="block text-[11px] text-muted-foreground">{o.reason}</span>
            </span>
          </label>
        ))}
      </div>
      <Button size="sm" onClick={save} disabled={picked.size === 0} className="w-fit">
        <Check className="size-3.5" /> Ask {role === "*" ? "every role" : role} for {picked.size} document{picked.size === 1 ? "" : "s"}
      </Button>
    </Card>
  );
}

const ACTION_LABEL = (it: IntegrationDetail, r: Pick<AccessRule, "app_id" | "action">) =>
  it.apps.find((a) => a.id === r.app_id)?.actions.find((x) => x.key === r.action)?.label ?? r.action;

/** App access rules (Slack, Jira, ServiceNow, Google Workspace), matched like Requirement rules. */
export function AccessCard({ it, onChange }: { it: IntegrationDetail; onChange: (d: IntegrationDetail) => void }) {
  const [rules, setRules] = useState<AccessRule[]>(it.access_saved ? it.access_rules : it.suggested_access);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ app_id: it.apps[0]?.id ?? "", action: it.apps[0]?.actions[0]?.key ?? "", resource: "", mode: "direct", role: "", department: "" });
  const app = it.apps.find((a) => a.id === draft.app_id);
  const save = async (list: AccessRule[]) => {
    try { onChange(await call(`/api/integrations/${it.id}/access`, "PUT", { rules: list })); toast.success(list.length ? "App access saved" : "No app access"); }
    catch (e) { toast.error((e as Error).message); }
  };
  const add = () => {
    const a = it.apps.find((x) => x.id === draft.app_id)!;
    setRules((r) => [...r, { id: `new-${r.length}-${draft.action}`, app_id: a.id, app: a.name, action: draft.action, resource: draft.resource,
      mode: draft.mode as "direct" | "request", match: { ...(draft.role ? { role: [draft.role] } : {}), ...(draft.department ? { department: [draft.department] } : {}) }, from_rule: "" }]);
    setAdding(false); setDraft((d) => ({ ...d, resource: "", role: "", department: "" }));
  };
  const who = (r: AccessRule) => [...(r.match.role ?? []), ...(r.match.department ?? []), ...(r.match.country ?? []), ...(r.match.location ?? [])].join(", ") || "Everyone";
  return (
    <Card className="gap-2 p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1 font-semibold"><KeyRound className="size-3.5" /> App access for new joiners</span>
        <span className={cn("rounded-full px-2 py-0.5 font-medium", it.access_saved ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700")}>
          {it.access_saved ? "Saved" : "Suggested from your rules"}</span>
      </div>
      <div className="divide-y rounded-lg border text-sm">
        {rules.length === 0 && <div className="p-3 text-xs text-muted-foreground">No app access.</div>}
        {rules.map((r, i) => (
          <div key={r.id} className="flex items-center gap-2 px-3 py-2">
            <span className="w-28 shrink-0 font-medium">{r.app}</span>
            <span className="min-w-0 flex-1">
              {ACTION_LABEL(it, r)}{r.resource && <> <code className="rounded bg-muted px-1 text-xs">{r.resource}</code></>}
              {r.mode === "request" && <span className="ml-1 rounded bg-amber-50 px-1.5 text-[11px] text-amber-700">asks admin</span>}
              <span className="block text-[11px] text-muted-foreground">For {who(r)}{r.from_rule && ` · from rule ${r.from_rule}`}</span>
            </span>
            {(it.apps.find((a) => a.id === r.app_id)?.key === "slack" || it.apps.find((a) => a.id === r.app_id)?.key === "jira") && r.resource && (
              <button onClick={() => setRules((l) => l.map((x, j) => j === i ? { ...x, mode: x.mode === "request" ? "direct" : "request" } : x))}
                className="text-[11px] text-primary hover:underline">{r.mode === "request" ? "Add directly" : "Ask admin instead"}</button>
            )}
            <button onClick={() => setRules((l) => l.filter((_, j) => j !== i))} aria-label="Remove" className="text-muted-foreground hover:text-rose-600"><Trash2 className="size-3.5" /></button>
          </div>
        ))}
      </div>
      {adding ? (
        <div className="grid gap-2 rounded-lg border bg-muted/30 p-2 sm:grid-cols-2">
          <SimpleSelect value={draft.app_id} onChange={(v) => { const a = it.apps.find((x) => x.id === v); setDraft((d) => ({ ...d, app_id: v, action: a?.actions[0]?.key ?? "" })); }}
            className="w-full" options={it.apps.map((a) => ({ value: a.id, label: a.name }))} />
          <SimpleSelect value={draft.action} onChange={(v) => setDraft((d) => ({ ...d, action: v }))} className="w-full"
            options={(app?.actions ?? []).map((a) => ({ value: a.key, label: a.label }))} />
          {app?.actions.find((a) => a.key === draft.action)?.needs_resource && (
            <Input value={draft.resource} onChange={(e) => setDraft((d) => ({ ...d, resource: e.target.value }))}
              placeholder={app.key === "slack" ? "#channel" : app.key === "jira" ? "Project key" : app.key === "servicenow" ? "What to request (e.g. Laptop)" : "Group"} />
          )}
          {(app?.key === "slack" || app?.key === "jira") && (
            <SimpleSelect value={draft.mode} onChange={(v) => setDraft((d) => ({ ...d, mode: v || "direct" }))} className="w-full"
              options={[{ value: "direct", label: "Add them directly" }, { value: "request", label: "Ask the channel/project admin" }]} />
          )}
          <SimpleSelect value={draft.role} onChange={(v) => setDraft((d) => ({ ...d, role: v }))} placeholder="Any role" className="w-full"
            options={it.roles.map((r) => ({ value: r, label: r }))} />
          <SimpleSelect value={draft.department} onChange={(v) => setDraft((d) => ({ ...d, department: v }))} placeholder="Any department" className="w-full"
            options={it.departments.map((r) => ({ value: r, label: r }))} />
          <div className="flex gap-2 sm:col-span-2">
            <Button size="sm" onClick={add}><Plus className="size-3.5" /> Add</Button>
            <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
          </div>
        </div>
      ) : <Button size="sm" variant="outline" className="w-fit" onClick={() => setAdding(true)}><Plus className="size-3.5" /> Add app access</Button>}
      <div className="flex flex-wrap gap-2 border-t pt-2">
        <Button size="sm" onClick={() => save(rules)}><Check className="size-3.5" /> Save {rules.length} access item{rules.length === 1 ? "" : "s"}</Button>
        {!it.access_saved && <Button size="sm" variant="ghost" onClick={() => save([])}>No app access</Button>}
        {it.access_saved && <Button size="sm" variant="ghost" onClick={() => setRules(it.suggested_access)}><Wand2 className="size-3.5" /> Use suggestion</Button>}
      </div>
    </Card>
  );
}
