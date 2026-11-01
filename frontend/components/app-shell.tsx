"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Activity, ArrowLeftRight, CheckCheck, ChevronsUpDown, FileStack, Gauge, KeyRound, LayoutDashboard, ListChecks, LogOut, Mail, Moon, Plug, ScrollText, Sparkles, Sun, Upload, Users } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { api, type Role, type Stats } from "@/lib/api";
import { HrAssistant } from "@/components/hr-assistant";
import { MeProvider, RoleBadge, RoleGate, initials, useMe } from "@/components/auth";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

const STAFF: Role[] = ["admin", "hr_ops", "approver", "auditor"];
const ALL: Role[] = [...STAFF, "manager"];

// Who sees each page. Mirrors the read rules in backend/identity/perms.py; the backend enforces them regardless.
export const NAV: { href: string; label: string; Icon: typeof Activity; roles: Role[] }[] = [
  { href: "/dashboard", label: "Dashboard", Icon: LayoutDashboard, roles: ALL },
  { href: "/import", label: "Add joiners", Icon: Upload, roles: ["hr_ops"] },
  { href: "/templates", label: "Email templates", Icon: Mail, roles: STAFF },
  { href: "/rules", label: "Requirement rules", Icon: ListChecks, roles: STAFF },
  { href: "/connectors", label: "Connectors", Icon: Plug, roles: STAFF },
  { href: "/integrations", label: "Integrations", Icon: ArrowLeftRight, roles: STAFF },
  { href: "/approvals", label: "Approvals", Icon: CheckCheck, roles: STAFF },
  { href: "/logs", label: "Logs", Icon: ScrollText, roles: STAFF },
  { href: "/quality", label: "AI quality", Icon: Gauge, roles: STAFF },
  { href: "/activity", label: "Activity", Icon: Activity, roles: ALL },
  { href: "/users", label: "Users", Icon: Users, roles: ["admin"] },
];

function ShellSkeleton() {
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col gap-2 bg-sidebar p-5 md:flex">
        <Skeleton className="mb-4 h-8 w-36 bg-white/10" />
        {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-7 bg-white/10" />)}
      </aside>
      <main className="flex-1 space-y-4 p-8"><Skeleton className="h-8 w-64" /><Skeleton className="h-40" /><Skeleton className="h-64" /></main>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return <MeProvider fallback={<ShellSkeleton />}><Shell>{children}</Shell></MeProvider>;
}

function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { me } = useMe();
  const { resolvedTheme, setTheme } = useTheme();
  const [gmail, setGmail] = useState<boolean | null>(null);
  useEffect(() => {
    api<Stats>("/api/stats").then((s) => setGmail(s.gmail)).catch(() => setGmail(false));
  }, []);
  const nav = NAV.filter((n) => me && n.roles.includes(me.role));
  const page = NAV.find((n) => path.startsWith(n.href)) ?? (path.startsWith("/employees") ? NAV[0] : undefined);

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
          {nav.map(({ href, label, Icon }) => {
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
          {me && <UserMenu />}
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
            {nav.slice(0, 4).map((n) => <Link key={n.href} href={n.href}>{n.label.split(" ")[0]}</Link>)}
          </nav>
        </div>
        <main className="mx-auto w-full max-w-7xl px-4 py-6 md:px-8 md:py-8">
          {page && me && !page.roles.includes(me.role) ? <RoleGate roles={page.roles}>{null}</RoleGate> : children}
        </main>
      </div>
      {me && me.role !== "manager" && <HrAssistant />}
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

function UserMenu() {
  const { me } = useMe();
  const [pwOpen, setPwOpen] = useState(false);
  if (!me) return null;
  const signOut = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch {}
    // Full reload on purpose: drops every client-side cache of the previous user's data.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger className="-mx-1 mb-2 flex w-[calc(100%+0.5rem)] items-center gap-2.5 rounded-lg px-1 py-1.5 text-left hover:bg-sidebar-accent/60" aria-label="Account menu">
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-400 to-indigo-600 text-[11px] font-semibold text-white">{initials(me.name)}</span>
          <span className="min-w-0 flex-1 leading-tight">
            <span className="block truncate text-[13px] font-medium text-white">{me.name}</span>
            <RoleBadge role={me.role} className="mt-0.5 h-4 px-1.5 text-[10px]" />
          </span>
          <ChevronsUpDown className="size-3.5 text-sidebar-foreground/60" />
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="w-56">
          <DropdownMenuGroup>
            <DropdownMenuLabel className="truncate text-xs font-normal text-muted-foreground">{me.email}</DropdownMenuLabel>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setPwOpen(true)}><KeyRound /> Change password</DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onClick={signOut}><LogOut /> Sign out</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChangePassword open={pwOpen} onOpenChange={setPwOpen} />
    </>
  );
}

function ChangePassword({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api("/api/auth/password", { method: "POST", json: { current, new: next } });
      toast.success("Password changed. Your other sessions were signed out.");
      onOpenChange(false);
      setCurrent("");
      setNext("");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Change password</DialogTitle>
          <DialogDescription>At least 10 characters. Other devices will be signed out.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="pw-cur">Current password</Label>
            <Input id="pw-cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pw-new">New password</Label>
            <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={save} disabled={busy || !current || next.length < 10}>Change password</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
