"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ArrowUp, Loader2, Sparkles } from "lucide-react";
import { api, type IntegrationDetail } from "@/lib/api";

const EXAMPLES = [
  "Move Engineering candidates with accepted offers from the Candidate Portal to the HR Portal",
  "Bring hired candidates from uploaded files into the HR Portal",
  "Set up Sales hiring from the Candidate Portal, called Sales onboarding",
];

export default function NewIntegrationPage() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const start = async () => {
    if (text.trim().length < 3 || busy) return;
    setBusy(true);
    try {
      const it = await api<IntegrationDetail>("/api/integrations/start", {
        method: "POST", json: { message: text.trim(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
      });
      router.push(`/integrations/${it.id}`);
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <div className="mx-auto flex min-h-[70vh] max-w-2xl flex-col justify-center gap-6">
      <div className="text-center">
        <div className="mx-auto mb-4 grid size-12 place-items-center rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-600 text-white"><Sparkles className="size-6" /></div>
        <h1 className="text-2xl font-semibold tracking-tight">What should this integration do?</h1>
        <p className="mt-1 text-sm text-muted-foreground">Describe it in your own words. The agent sets it up and asks only for what it still needs.</p>
      </div>
      <div className="relative rounded-2xl border bg-card p-3 shadow-sm focus-within:ring-2 focus-within:ring-ring">
        <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} rows={3} autoFocus
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); start(); } }}
          placeholder="Move accepted Engineering candidates into the HR Portal"
          className="w-full resize-none bg-transparent text-sm outline-none" />
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">{busy ? "Setting it up…" : "Press Enter to start"}</span>
          <button onClick={start} disabled={busy || text.trim().length < 3} aria-label="Start"
            className="grid size-8 place-items-center rounded-full bg-primary text-primary-foreground disabled:opacity-40">
            {busy ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        {EXAMPLES.map((ex) => (
          <button key={ex} onClick={() => setText(ex)} className="rounded-xl border px-4 py-2.5 text-left text-sm transition-colors hover:border-primary hover:bg-primary/5">{ex}</button>
        ))}
      </div>
    </div>
  );
}
