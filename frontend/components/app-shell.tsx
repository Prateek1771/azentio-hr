"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Activity, ArrowLeftRight, CheckCheck, FileStack, Gauge, LayoutDashboard, ListChecks, Mail, Moon, Plug, ScrollText, Sparkles, Sun, Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type Stats } from "@/lib/api";
import { HrAssistant } from "@/components/hr-assistant";

const NAV = [
  { href: "/dashboard", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/import", label: "Add joiners", Icon: Upload },
  { href: "/templates", label: "Email templates", Icon: Mail },
  { href: "/rules", label: "Requirement rules", Icon: ListChecks },
  { href: "/connectors", label: "Connectors", Icon: Plug },
  { href: "/integrations", label: "Integrations", Icon: ArrowLeftRight },
  { href: "/approvals", label: "Approvals", Icon: CheckCheck },
  { href: "/logs", label: "Logs", Icon: ScrollText },
  { href: "/quality", label: "AI quality", Icon: Gauge },
  { href: "/activity", label: "Activity", Icon: Activity },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { resolvedTheme, setTheme } = useTheme();
  const [gmail, setGmail] = useState<boolean | null>(null);
  useEffect(() => {
    api<Stats>("/api/stats").then((s) => setGmail(s.gmail)).catch(() => setGmail(false));
  }, []);

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-violet-400 to-indigo-600 shadow-lg shadow-indigo-900/40">
            <Sparkles className="size-4 text-white" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-white">Azentio Onboard</div>
            <div className="text-[11px] text-sidebar-foreground/60">AI onboarding agent</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map(({ href, label, Icon }) => {
            const active = path.startsWith(href) || (href === "/dashboard" && path.startsWith("/employees"));
            return (
              <Link key={href} href={href}
                className={cn("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                  active ? "bg-sidebar-accent text-white" : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-white")}>
                <Icon className="size-4" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="space-y-2 border-t border-sidebar-border p-4 text-xs">
          <div className="flex items-center gap-2">
            <span className={cn("size-2 rounded-full", gmail ? "bg-emerald-400" : gmail === false ? "bg-rose-400" : "bg-zinc-500")} />
            <span className="text-sidebar-foreground/75">{gmail ? "Gmail connected" : gmail === false ? "Gmail not configured" : "Checking Gmail…"}</span>
          </div>
          <div className="flex items-center gap-2 text-sidebar-foreground/75">
            <FileStack className="size-3.5" /> FalkorDB knowledge graph
          </div>
          <button onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
            className="flex items-center gap-2 text-sidebar-foreground/75 hover:text-white">
            {resolvedTheme === "dark" ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
            {resolvedTheme === "dark" ? "Light mode" : "Dark mode"}
          </button>
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 border-b bg-sidebar px-4 py-3 text-white md:hidden">
          <Sparkles className="size-4" /> <span className="text-sm font-semibold">Azentio Onboard</span>
          <nav className="ml-auto flex gap-3 text-xs">
            {NAV.slice(0, 4).map((n) => <Link key={n.href} href={n.href}>{n.label.split(" ")[0]}</Link>)}
          </nav>
        </div>
        <main className="mx-auto w-full max-w-7xl px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
      <HrAssistant />
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
