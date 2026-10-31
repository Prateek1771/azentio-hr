"use client";

import { useEffect, useRef, useState } from "react";
import { Bot, Loader2, Send } from "lucide-react";
import { API } from "@/lib/api";
import { Markdown } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface Msg { role: "user" | "assistant"; content: string; sources?: { source: string; section: string; title: string }[] }

/** Streaming chat used by the employee portal and the HR co-pilot. Backend streams `__SOURCES__[json]\n` then text. */
export function ChatPanel({ endpoint, title, subtitle, greeting, suggestions, extraBody, msgs, setMsgs, className, headerExtra }: {
  endpoint: string; title: string; subtitle: string; greeting: React.ReactNode; suggestions: string[];
  extraBody?: Record<string, unknown>; msgs: Msg[]; setMsgs: React.Dispatch<React.SetStateAction<Msg[]>>;
  className?: string; headerExtra?: React.ReactNode;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [msgs]);

  const ask = async (q: string) => {
    if (!q.trim() || busy) return;
    const history = msgs.map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);
    try {
      const res = await fetch(`${API}${endpoint}`, { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ message: q, history, ...extraBody }) });
      if (!res.body) throw new Error("No response");
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "", sources: Msg["sources"];
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let text = buf;
        if (buf.startsWith("__SOURCES__")) {
          const nl = buf.indexOf("\n");
          if (nl < 0) continue;
          sources = JSON.parse(buf.slice(11, nl));
          text = buf.slice(nl + 1);
        }
        setMsgs((m) => [...m.slice(0, -1), { role: "assistant", content: text, sources }]);
      }
    } catch {
      setMsgs((m) => [...m.slice(0, -1), { role: "assistant", content: "Sorry, I couldn't reach the assistant. Please try again." }]);
    } finally {
      setBusy(false);
    }
  };

  const cite = (c: string) => (
    <span className="mx-0.5 inline-flex items-center rounded bg-primary/10 px-1 py-px align-baseline text-[10px] font-medium text-primary">
      {c === "checklist" ? "your checklist" : c === "activity" ? "activity log" : c.replace(/^joiner:\s*/, "👤 ").replace(".md", "").replace(/-/g, " ")}
    </span>
  );

  return (
    <div className={cn("flex flex-col overflow-hidden bg-card", className)}>
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 text-white"><Bot className="size-4" /></div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{title}</div>
          <div className="truncate text-xs text-muted-foreground">{subtitle}</div>
        </div>
        {headerExtra}
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {msgs.length === 0 && (
          <div className="space-y-3">
            <div className="rounded-2xl rounded-tl-sm bg-muted px-3.5 py-2.5 text-sm">{greeting}</div>
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((s) => (
                <button key={s} onClick={() => ask(s)} className="rounded-full border px-3 py-1.5 text-left text-xs transition-colors hover:border-primary hover:bg-primary/5">{s}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div className={cn("max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm",
              m.role === "user" ? "rounded-tr-sm bg-primary text-primary-foreground" : "rounded-tl-sm bg-muted")}>
              {m.role === "assistant"
                ? (m.content ? <Markdown text={m.content} cite={cite} /> : <span className="flex items-center gap-2 text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Thinking…</span>)
                : m.content}
              {m.role === "assistant" && m.sources && m.sources.length > 0 && m.content && (
                <div className="mt-2 flex flex-wrap gap-1 border-t border-foreground/10 pt-2">
                  {m.sources.map((s, j) => (
                    <span key={j} className="rounded-md bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground ring-1 ring-border">📄 {s.title} § {s.section}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="flex gap-2 border-t p-3">
        <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask anything…" disabled={busy} />
        <Button type="submit" size="icon" disabled={busy || !input.trim()} aria-label="Send"><Send className="size-4" /></Button>
      </form>
    </div>
  );
}
