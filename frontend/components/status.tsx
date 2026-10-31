import { cn } from "@/lib/utils";
import type { State } from "@/lib/api";
import { AlertTriangle, CheckCircle2, Circle, Clock, Lock } from "lucide-react";

export const STATE_STYLE: Record<State, { label: string; cls: string; dot: string; Icon: typeof Circle }> = {
  completed: { label: "Completed", cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-400", dot: "bg-emerald-500", Icon: CheckCircle2 },
  pending: { label: "Pending", cls: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-500/10 dark:text-sky-400", dot: "bg-sky-500", Icon: Clock },
  blocked: { label: "Blocked", cls: "bg-zinc-100 text-zinc-600 ring-zinc-500/20 dark:bg-zinc-500/10 dark:text-zinc-400", dot: "bg-zinc-400", Icon: Lock },
  overdue: { label: "Overdue", cls: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-400", dot: "bg-rose-500", Icon: AlertTriangle },
};

export function StateBadge({ state, className }: { state: State; className?: string }) {
  const s = STATE_STYLE[state];
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", s.cls, className)}>
      <s.Icon className="size-3" />
      {s.label}
    </span>
  );
}

const STATUS_CLS: Record<string, string> = {
  Accepted: "text-emerald-700 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-500/10",
  Completed: "text-emerald-700 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-500/10",
  Acknowledged: "text-emerald-700 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-500/10",
  "Correction Requested": "text-amber-700 bg-amber-50 dark:text-amber-400 dark:bg-amber-500/10",
  "Under Review": "text-violet-700 bg-violet-50 dark:text-violet-400 dark:bg-violet-500/10",
  Submitted: "text-sky-700 bg-sky-50 dark:text-sky-400 dark:bg-sky-500/10",
  Requested: "text-sky-700 bg-sky-50 dark:text-sky-400 dark:bg-sky-500/10",
  "In Progress": "text-sky-700 bg-sky-50 dark:text-sky-400 dark:bg-sky-500/10",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span className={cn("inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
      STATUS_CLS[status] ?? "text-muted-foreground bg-muted")}>
      {status}
    </span>
  );
}

export function ProgressRing({ value, size = 56, stroke = 6, className }: { value: number; size?: number; stroke?: number; className?: string }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const color = value === 100 ? "stroke-emerald-500" : value >= 50 ? "stroke-primary" : "stroke-amber-500";
  return (
    <div className={cn("relative inline-grid place-items-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} className="fill-none stroke-muted" />
        <circle cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - value / 100)}
          className={cn("fill-none transition-[stroke-dashoffset] duration-700", color)} />
      </svg>
      <span className="absolute text-xs font-semibold tabular-nums">{value}%</span>
    </div>
  );
}

const AVATAR_COLORS = ["bg-violet-500", "bg-sky-500", "bg-emerald-500", "bg-amber-500", "bg-rose-500", "bg-indigo-500", "bg-teal-500"];

export function Initials({ name, className }: { name: string; className?: string }) {
  const initials = name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const color = AVATAR_COLORS[[...name].reduce((a, ch) => a + ch.charCodeAt(0), 0) % AVATAR_COLORS.length];
  return (
    <span className={cn("inline-grid size-9 shrink-0 place-items-center rounded-full text-xs font-semibold text-white", color, className)}>
      {initials}
    </span>
  );
}

export function SegmentBar({ s }: { s: { completed: number; pending: number; blocked: number; overdue: number; total: number } }) {
  if (!s.total) return null;
  const seg = (n: number, cls: string, label: string) =>
    n ? <div title={`${n} ${label}`} className={cls} style={{ width: `${(100 * n) / s.total}%` }} /> : null;
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted">
      {seg(s.completed, "bg-emerald-500", "completed")}
      {seg(s.pending, "bg-sky-400", "pending")}
      {seg(s.overdue, "bg-rose-500", "overdue")}
      {seg(s.blocked, "bg-zinc-300 dark:bg-zinc-600", "blocked")}
    </div>
  );
}

export function RoleBadge({ role, className }: { role: "source" | "target"; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
      role === "source" ? "bg-source-soft text-source" : "bg-target-soft text-target", className)}>
      <span className={cn("size-1.5 rounded-full", role === "source" ? "bg-source" : "bg-target")} />
      {role === "source" ? "Source" : "Target"}
    </span>
  );
}
