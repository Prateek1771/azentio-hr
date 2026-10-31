"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { FlaskConical, Loader2, Play, ShieldAlert, ShieldCheck } from "lucide-react";
import { api, fmtTs, timeAgo } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type Summary = {
  faithfulness?: number | null; context_precision?: number | null; context_recall?: number | null;
  attacks_blocked?: number; attacks?: number; output_guardrail_pass?: number; avg_ms?: number | null;
};
type Run = { id: string; ts: number; finished?: number; status: "running" | "succeeded" | "failed"; judge: string; model: string;
  done: number; total: number; error?: string; summary: Summary };
type Result = { id: string; n: number; question: string; reference: string; answer: string; ms: number; guardrail_ok: boolean;
  sources: string[]; faithfulness: number | null; context_precision: number | null; context_recall: number | null };
type Guard = { id: string; ts: number; surface: string; rule: string; action: "blocked" | "flagged" };
type Quality = { runs: Run[]; latest: Run | null; results: Result[]; judge: string;
  guardrails: { days: number; by_rule: { rule: string; action: string; n: number }[]; recent: Guard[] } };

const METRICS = [
  { key: "faithfulness", label: "Faithfulness", hint: "Answers stick to the retrieved policy text" },
  { key: "context_precision", label: "Context precision", hint: "Retrieved sections are the relevant ones" },
  { key: "context_recall", label: "Context recall", hint: "Retrieval found everything the answer needs" },
] as const;

const RULES: Record<string, string> = {
  prompt_injection: "Prompt injection", too_long: "Message too long", unverified_citation: "Citation not in retrieved policy",
  unknown_email: "Email not in context", prompt_leak: "System prompt leaked",
};

const tone = (v: number | null | undefined) =>
  v == null ? "text-muted-foreground" : v >= 0.8 ? "text-emerald-600 dark:text-emerald-400" : v >= 0.6 ? "text-amber-600 dark:text-amber-400" : "text-rose-600 dark:text-rose-400";
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

export default function QualityPage() {
  const [data, setData] = useState<Quality | null>(null);
  const [starting, setStarting] = useState(false);
  const load = useCallback(() => api<Quality>("/api/quality").then(setData).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const active = data?.runs.find((r) => r.status === "running");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [active, load]);

  const start = async () => {
    setStarting(true);
    try {
      await api("/api/quality/eval", { method: "POST" });
      toast.success("Evaluation started");
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const done = data?.runs.filter((r) => r.status === "succeeded").reverse() ?? [];
  const prev = done.length > 1 ? done[done.length - 2] : null;
  const s = data?.latest?.summary;

  return (
    <>
      <PageHeader title="AI quality"
        subtitle="Ragas scores for the employee assistant on a fixed question set, and every guardrail hit across both assistants."
        actions={
          <Button onClick={start} disabled={starting || !!active}>
            {active || starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
            {active ? `Evaluating ${active.done}/${active.total || "…"}` : "Run evaluation"}
          </Button>
        } />

      {!data ? <Skeleton className="h-72 rounded-xl" /> : (
        <div className="space-y-6">
          {!data.latest && !active && (
            <Card className="items-center gap-2 p-12 text-center">
              <FlaskConical className="size-8 text-muted-foreground" />
              <div className="font-semibold">No evaluation yet</div>
              <p className="max-w-md text-sm text-muted-foreground">
                Run the evaluation to score 20 policy questions. The judge model is <code className="text-xs">{data.judge}</code>; a full run takes a few minutes.
              </p>
            </Card>
          )}

          {data.latest && s && (
            <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {METRICS.map((m) => {
                const v = s[m.key];
                const before = prev?.summary[m.key];
                const delta = v != null && before != null ? Math.round((v - before) * 100) : null;
                return (
                  <Card key={m.key} className="gap-2 p-5">
                    <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{m.label}</div>
                    <div className="flex items-end justify-between gap-3">
                      <div className={cn("text-3xl font-semibold tabular-nums", tone(v))}>{pct(v)}</div>
                      <Spark values={done.map((r) => r.summary[m.key] ?? null)} />
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {delta != null && delta !== 0 && (
                        <span className={delta > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>
                          {delta > 0 ? "+" : ""}{delta} pts vs last run ·{" "}
                        </span>
                      )}
                      {m.hint}
                    </div>
                  </Card>
                );
              })}
              <Card className="gap-2 p-5">
                <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Guardrails</div>
                <div className={cn("text-3xl font-semibold tabular-nums", tone((s.attacks_blocked ?? 0) / Math.max(s.attacks ?? 1, 1)))}>
                  {s.attacks_blocked}/{s.attacks}
                </div>
                <div className="text-xs text-muted-foreground">
                  Attack prompts blocked · {pct(s.output_guardrail_pass)} of answers passed output checks
                </div>
              </Card>
            </section>
          )}

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
            {data.latest && (
              <Card className="gap-0 overflow-hidden p-0">
                <div className="flex flex-wrap items-baseline justify-between gap-2 border-b px-4 py-3">
                  <div className="font-semibold">Latest run, by question</div>
                  <div className="text-xs text-muted-foreground">
                    {fmtTs(data.latest.finished ?? data.latest.ts)} · answers by <code>{data.latest.model}</code> · judged by <code>{data.latest.judge}</code>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
                      <tr>
                        <th className="px-4 py-2.5 font-medium">Question</th>
                        <th className="px-3 text-right font-medium">Faithful</th>
                        <th className="px-3 text-right font-medium">Precision</th>
                        <th className="px-3 text-right font-medium">Recall</th>
                        <th className="px-4 font-medium">Guardrail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.results.map((r) => (
                        <tr key={r.id} className="border-t align-top">
                          <td className="px-4 py-2.5">
                            <details>
                              <summary className="cursor-pointer marker:text-muted-foreground">{r.question}</summary>
                              <div className="mt-2 space-y-2 text-xs">
                                <p className="whitespace-pre-wrap">{r.answer}</p>
                                <p className="text-muted-foreground"><span className="font-medium">Expected:</span> {r.reference}</p>
                                <p className="text-muted-foreground"><span className="font-medium">Retrieved:</span> {r.sources.join(" · ") || "nothing"}</p>
                              </div>
                            </details>
                          </td>
                          {(["faithfulness", "context_precision", "context_recall"] as const).map((k) => (
                            <td key={k} className={cn("px-3 py-2.5 text-right tabular-nums", tone(r[k]))}>{pct(r[k])}</td>
                          ))}
                          <td className="px-4 py-2.5">
                            {r.guardrail_ok
                              ? <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-400">Pass</span>
                              : <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-400">Flagged</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            <div className="space-y-6">
              <Card className="gap-3 p-4">
                <div className="font-semibold">Guardrail hits, last {data.guardrails.days} days</div>
                {data.guardrails.by_rule.length === 0 ? (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground"><ShieldCheck className="size-4" /> No hits yet.</p>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {data.guardrails.by_rule.map((g) => (
                      <li key={g.rule + g.action} className="flex items-center justify-between gap-3">
                        <span className="flex items-center gap-2">
                          <ShieldAlert className={cn("size-4", g.action === "blocked" ? "text-rose-500" : "text-amber-500")} />
                          {RULES[g.rule] ?? g.rule}
                        </span>
                        <span className="tabular-nums text-muted-foreground">{g.n} {g.action}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {data.guardrails.recent.length > 0 && (
                  <ul className="space-y-1.5 border-t pt-3 text-xs text-muted-foreground">
                    {data.guardrails.recent.slice(0, 8).map((g) => (
                      <li key={g.id} className="flex justify-between gap-2">
                        <span>{g.surface}: {RULES[g.rule] ?? g.rule}</span><span className="shrink-0">{timeAgo(g.ts)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>

              <Card className="gap-3 p-4">
                <div className="font-semibold">Run history</div>
                {data.runs.length === 0 ? <p className="text-sm text-muted-foreground">No runs yet.</p> : (
                  <ul className="space-y-2 text-sm">
                    {data.runs.slice(0, 8).map((r) => (
                      <li key={r.id} className="flex items-center justify-between gap-3">
                        <span className="text-xs text-muted-foreground">{fmtTs(r.ts)}</span>
                        {r.status === "running" ? <span className="text-xs">Running {r.done}/{r.total}</span>
                          : r.status === "failed" ? <span className="truncate text-xs text-rose-600 dark:text-rose-400" title={r.error}>Failed</span>
                          : <span className={cn("text-xs tabular-nums", tone(r.summary.faithfulness))}>{pct(r.summary.faithfulness)} faithful</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Spark({ values }: { values: (number | null)[] }) {
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] != null);
  if (pts.length < 2) return null;
  const W = 80, H = 28, n = values.length - 1;
  const xy = pts.map(([i, v]) => [(i / n) * W, H - 2 - v * (H - 4)] as const);
  const [lx, ly] = xy[xy.length - 1];
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="text-primary" aria-hidden>
      <polyline points={xy.map((p) => p.join(",")).join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.6" />
      <circle cx={lx} cy={ly} r="2.5" fill="currentColor" />
    </svg>
  );
}
