"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { MessageCircle, RotateCcw, X } from "lucide-react";
import { api } from "@/lib/api";
import { ChatPanel, type Msg } from "@/components/chat-panel";
import { cn } from "@/lib/utils";

const GENERAL = ["Who is at risk of not being ready on day one?", "What's blocking Kabir?", "Which documents need attention?", "Summarise joiners starting this month"];
const FOCUSED = (n: string) => [`What is ${n} still missing?`, `Why is ${n}'s checklist blocked?`, `Draft a reminder email to ${n}`, `Is ${n} ready for day one?`];

/** Floating HR co-pilot: mounted once in the HR layout so the conversation survives page navigation. */
export function HrAssistant() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const focusId = path.match(/^\/employees\/([^/]+)/)?.[1] ?? null;
  const [focus, setFocus] = useState<{ id: string; name: string } | null>(null);

  useEffect(() => {
    if (!focusId) return;
    let live = true;
    api<{ employee: { name: string } }>(`/api/employees/${focusId}`)
      .then((d) => live && setFocus({ id: focusId, name: d.employee.name }))
      .catch(() => {});
    return () => { live = false; };
  }, [focusId]);
  const focusName = focus && focus.id === focusId ? focus.name : null;
  const first = focusName?.split(" ")[0];

  return (
    <>
      <div className={cn("fixed right-4 bottom-20 z-50 w-[calc(100vw-2rem)] origin-bottom-right transition-all duration-200 sm:right-6 sm:bottom-24 sm:w-[420px]",
        open ? "pointer-events-auto scale-100 opacity-100" : "pointer-events-none scale-95 opacity-0")}>
        <ChatPanel
          endpoint="/api/hr/chat"
          extraBody={focusId ? { employee_id: focusId } : undefined}
          msgs={msgs} setMsgs={setMsgs}
          title="HR co-pilot"
          subtitle={focusName ? `Viewing ${focusName} · knows every joiner` : "Live data on every joiner, task, owner & policy"}
          suggestions={first ? FOCUSED(first) : GENERAL}
          greeting={first
            ? <>Ask me anything about <b>{focusName}</b> — documents, blockers, owners, deadlines — or about any other joiner.</>
            : <>Hi! I can see every new joiner&apos;s checklist, documents, flags, owners and recent activity. Ask me anything.</>}
          className="h-[min(620px,calc(100vh-8rem))] rounded-2xl shadow-2xl ring-1 ring-foreground/10"
          headerExtra={<>
            {msgs.length > 0 && (
              <button onClick={() => setMsgs([])} title="New conversation" className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
                <RotateCcw className="size-4" />
              </button>
            )}
            <button onClick={() => setOpen(false)} title="Close" className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
              <X className="size-4" />
            </button>
          </>}
        />
      </div>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close HR co-pilot" : "Open HR co-pilot"}
        className="fixed right-4 bottom-4 z-50 grid size-14 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 text-white shadow-lg shadow-indigo-600/30 transition-transform hover:scale-105 active:scale-95 sm:right-6 sm:bottom-6">
        {open ? <X className="size-6" /> : <MessageCircle className="size-6" />}
        {!open && (
          <span className="absolute top-0.5 right-0.5 flex size-3.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex size-3.5 rounded-full border-2 border-white bg-emerald-400" />
          </span>
        )}
      </button>
    </>
  );
}
