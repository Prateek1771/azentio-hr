"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { CalendarDays, CheckCircle2, Clock, FileCheck2, Laptop, Loader2, Lock, MapPin, ShieldCheck, Sparkles, UploadCloud, UserRound } from "lucide-react";
import { API, api, daysUntil, fmtDate, type Item, type Summary } from "@/lib/api";
import { Markdown } from "@/components/markdown";
import { ChatPanel, type Msg } from "@/components/chat-panel";
import { ProgressRing, StatusPill } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface Portal {
  employee: { id: string; name: string; role: string; grade: string; department: string; location: string; joining_date: string; email: string; manager: string };
  items: Item[]; summary: Summary;
}

const SUGGESTIONS = ["Which documents do I need before my joining date?", "What happens on my first day?", "When will I get my laptop and email?", "Why was my document rejected?"];

export default function PortalPage() {
  const { token } = useParams<{ token: string }>();
  const [p, setP] = useState<Portal | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ack, setAck] = useState<Item | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);

  const load = useCallback(() => api<Portal>(`/api/portal/${token}`).then(setP).catch((e) => setErr(e.message)), [token]);
  useEffect(() => { load(); }, [load]);

  if (err) return <div className="grid min-h-screen place-items-center p-6 text-center text-muted-foreground">This onboarding link is invalid or has expired. Please contact HR.</div>;
  if (!p) return <div className="mx-auto max-w-6xl space-y-4 p-6"><Skeleton className="h-48 rounded-2xl" /><Skeleton className="h-96 rounded-2xl" /></div>;

  const e = p.employee;
  const days = daysUntil(e.joining_date);
  const groups = [
    { title: "Before you join", items: p.items.filter((i) => i.due < e.joining_date) },
    { title: "On your first day", items: p.items.filter((i) => i.due === e.joining_date) },
    { title: "After you join", items: p.items.filter((i) => i.due > e.joining_date) },
  ];
  const yourActions = p.items.filter((i) => (i.kind === "document" || i.kind === "ack") && i.state !== "completed").length;

  return (
    <div className="min-h-screen">
      <header className="brand-gradient text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-6 px-6 pt-6 pb-20">
          <div className="flex w-full items-center gap-2 text-sm text-white/80"><Sparkles className="size-4" /> Azentio · Onboarding portal</div>
          <div className="min-w-0 flex-1">
            <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">Welcome aboard, {e.name.split(" ")[0]} 👋</h1>
            <p className="mt-2 text-white/80">{e.role} · {e.department}</p>
            <div className="mt-4 flex flex-wrap gap-2 text-sm">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 ring-1 ring-white/20"><CalendarDays className="size-4" /> {fmtDate(e.joining_date)}</span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 ring-1 ring-white/20"><MapPin className="size-4" /> {e.location}</span>
              {e.manager && <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 ring-1 ring-white/20"><UserRound className="size-4" /> Manager: {e.manager}</span>}
            </div>
          </div>
          <div className="flex items-center gap-6 rounded-2xl bg-white/10 px-6 py-4 ring-1 ring-white/20 backdrop-blur">
            <div className="text-center">
              <div className="text-4xl font-semibold tabular-nums">{Math.max(days, 0)}</div>
              <div className="text-xs text-white/70">{days > 0 ? "days to go" : days === 0 ? "today!" : "days since joining"}</div>
            </div>
            <div className="h-12 w-px bg-white/20" />
            <div className="text-center [&_span]:text-white">
              <ProgressRing value={p.summary.progress} size={64} className="[&_circle:first-child]:stroke-white/20" />
              <div className="mt-1 text-xs text-white/70">ready</div>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto -mt-12 grid max-w-6xl gap-6 px-4 pb-16 md:px-6 lg:grid-cols-[1fr_400px]">
        <div className="space-y-4">
          <Card className="flex-row items-center gap-3 p-4">
            <div className={cn("grid size-10 place-items-center rounded-full", yourActions ? "bg-amber-100 text-amber-700 dark:bg-amber-500/15" : "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15")}>
              {yourActions ? <Clock className="size-5" /> : <CheckCircle2 className="size-5" />}
            </div>
            <div className="text-sm">
              <div className="font-medium">{yourActions ? `${yourActions} thing${yourActions > 1 ? "s" : ""} need${yourActions > 1 ? "" : "s"} your attention` : "You're all set on your side!"}</div>
              <div className="text-muted-foreground">Upload documents here or reply to your welcome email with attachments. Everything is checked within seconds.</div>
            </div>
          </Card>

          {groups.map((g) => g.items.length > 0 && (
            <Card key={g.title} className="gap-0 p-0">
              <h2 className="border-b px-5 py-3 text-sm font-semibold">{g.title}</h2>
              {g.items.map((it) => <PortalItem key={it.id} it={it} token={token} onChange={load} onAck={() => setAck(it)} />)}
            </Card>
          ))}
        </div>

        <ChatPanel endpoint={`/api/portal/${token}/chat`} msgs={msgs} setMsgs={setMsgs}
          title="Onboarding assistant" subtitle="Answers from approved policies & your checklist" suggestions={SUGGESTIONS}
          greeting={<>Hi {e.name.split(" ")[0]}! I can answer questions about your documents, first day, IT setup and company policies. I only use approved sources and will say so if I don&apos;t know.</>}
          className="sticky top-6 h-[calc(100vh-3rem)] max-h-[760px] rounded-xl ring-1 ring-foreground/10" />
      </main>

      <Dialog open={!!ack} onOpenChange={(o) => !o && setAck(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{ack?.name}</DialogTitle>
            <DialogDescription>Please read the policy below. Your acknowledgement is recorded with a timestamp.</DialogDescription>
          </DialogHeader>
          <div className="max-h-[55vh] overflow-y-auto rounded-lg border bg-muted/30 p-4"><Markdown text={ack?.policy_text || "Policy text unavailable."} /></div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAck(null)}>Later</Button>
            <Button onClick={async () => {
              if (!ack) return;
              setP(await api<Portal>(`/api/portal/${token}/ack/${ack.id}`, { method: "POST" }));
              toast.success("Thanks! Acknowledgement recorded.");
              setAck(null);
            }}><ShieldCheck className="size-4" /> I have read and acknowledge</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function PortalItem({ it, token, onChange, onAck }: { it: Item; token: string; onChange: () => void; onAck: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [result, setResult] = useState<{ status: string; issues: string[] } | null>(null);
  const done = it.state === "completed";
  const latest = it.documents[0];

  const upload = async (file: File) => {
    setBusy(true);
    setResult(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("item_key", it.key);
    try {
      const res = await fetch(`${API}/api/portal/${token}/upload`, { method: "POST", body: fd });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? res.statusText);
      setResult(body);
      if (body.status === "Accepted") toast.success(`${it.name} accepted ✓`);
      else if (body.status === "Under Review") toast.info(`${it.name} is with the People Team for review`);
      else toast.warning(`${it.name} needs a correction`, { description: body.issues?.[0] });
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const Icon = done ? CheckCircle2 : it.state === "blocked" ? Lock : it.kind === "task" ? Laptop : it.kind === "ack" ? ShieldCheck : FileCheck2;
  const issues = result?.issues ?? (it.status === "Correction Requested" ? latest?.issues : []) ?? [];

  return (
    <div
      onDragOver={(e) => { if (it.kind === "document" && !done) { e.preventDefault(); setDrag(true); } }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f && it.kind === "document" && !done) upload(f); }}
      className={cn("flex flex-wrap items-start gap-3 border-b px-5 py-4 transition-colors last:border-0", drag && "bg-primary/5 ring-2 ring-primary ring-inset")}>
      <Icon className={cn("mt-0.5 size-5 shrink-0", done ? "text-emerald-500" : it.state === "overdue" ? "text-rose-500" : it.state === "blocked" ? "text-zinc-400" : "text-primary")} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{it.name}</span>
          <StatusPill status={it.status} />
          {it.state === "overdue" && <span className="text-xs font-medium text-rose-600">Overdue</span>}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">{it.reason.split(" — rule")[0]}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Due {fmtDate(it.due)}
          {it.kind === "task" && ` · handled by ${it.owner_name || it.category}`}
          {it.state === "blocked" && it.blocked_by.length > 0 && ` · starts after: ${it.blocked_by.join(", ")}`}
        </p>
        {issues.length > 0 && (
          <div className="mt-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
            <div className="font-medium">We couldn&apos;t accept your last upload:</div>
            <ul className="mt-1 space-y-0.5">{issues.map((x, i) => <li key={i}>• {x}</li>)}</ul>
          </div>
        )}
      </div>
      {it.kind === "document" && !done && (
        <>
          <Button size="sm" variant={it.status === "Correction Requested" ? "default" : "outline"} onClick={() => ref.current?.click()} disabled={busy}>
            {busy ? <><Loader2 className="size-4 animate-spin" /> Checking…</> : <><UploadCloud className="size-4" /> {latest ? "Re-upload" : "Upload"}</>}
          </Button>
          <input ref={ref} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        </>
      )}
      {it.kind === "ack" && !done && <Button size="sm" onClick={onAck}><ShieldCheck className="size-4" /> Read & acknowledge</Button>}
    </div>
  );
}
