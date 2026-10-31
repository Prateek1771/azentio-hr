"use client";

import { AlertOctagon, AlertTriangle, Bot, Check, FileText, Info, Mail, Pencil, RotateCcw, ShieldCheck, Sparkles, UserRound } from "lucide-react";
import { timeAgo, type Event } from "@/lib/api";
import { cn } from "@/lib/utils";

const EVENT_ICON: Record<string, typeof Info> = {
  email: Mail, verification: ShieldCheck, auto: Sparkles, ai: Bot, document: FileText, error: AlertOctagon,
  change: Pencil, checklist: RotateCcw, escalation: AlertTriangle, review: Check, acknowledgement: Check, profile: UserRound,
};

export function Timeline({ events }: { events: Event[] }) {
  if (!events.length) return <p className="text-sm text-muted-foreground">No activity yet.</p>;
  return (
    <ol className="relative space-y-4 border-l pl-5">
      {events.map((ev) => {
        const Icon = EVENT_ICON[ev.kind] ?? Info;
        return (
          <li key={ev.id} className="relative">
            <span className={cn("absolute -left-[31px] grid size-5 place-items-center rounded-full border bg-card",
              ev.kind === "error" ? "text-rose-500" : ev.kind === "auto" || ev.kind === "ai" ? "text-primary" : "text-muted-foreground")}>
              <Icon className="size-3" />
            </span>
            <p className="text-sm">{ev.message}</p>
            <p className="text-xs text-muted-foreground">{ev.employee_name ? `${ev.employee_name} · ` : ""}{ev.kind} · {timeAgo(ev.ts)}</p>
          </li>
        );
      })}
    </ol>
  );
}

