"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { api, toLogin, type Role, type User } from "@/lib/api";
import { Card } from "@/components/ui/card";

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Admin", hr_ops: "HR Ops", approver: "Approver", manager: "Manager", auditor: "Auditor",
};

export const ROLE_HINT: Record<Role, string> = {
  admin: "Users, connectors, integrations and system settings",
  hr_ops: "Runs onboarding day to day: joiners, templates, rules",
  approver: "Signs off documents and policy changes; can't author them",
  manager: "Read-only view of their own reports",
  auditor: "Read-only on everything, including the audit log",
};

export const ROLE_TONE: Record<Role, string> = {
  admin: "bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300",
  hr_ops: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300",
  approver: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  manager: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
  auditor: "bg-zinc-200 text-zinc-800 dark:bg-zinc-500/20 dark:text-zinc-300",
};

/** UI-only mirror of backend/identity/perms.py: hides buttons a role can't use. The backend still enforces everything. */
const WRITE: Record<string, Role[]> = {
  onboarding: ["hr_ops"],          // joiners, items, templates, rules, ingest, emails
  approve: ["approver"],           // document / offer decisions
  system: ["admin"],               // connectors, integrations, users, evals
  import: ["hr_ops", "admin"],     // add joiners from a connector
};

export function can(user: User | null, action: keyof typeof WRITE): boolean {
  return !!user && WRITE[action].includes(user.role);
}

const MeContext = createContext<{ me: User | null; reload: () => void }>({ me: null, reload: () => {} });

export function MeProvider({ children, fallback }: { children: React.ReactNode; fallback?: React.ReactNode }) {
  const [me, setMe] = useState<User | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    api<User>("/api/auth/me").then(setMe).catch(() => toLogin());
  }, [tick]);
  if (!me) return <>{fallback}</>;
  return <MeContext.Provider value={{ me, reload: () => setTick((t) => t + 1) }}>{children}</MeContext.Provider>;
}

export function useMe() {
  return useContext(MeContext);
}

export function RoleGate({ roles, children }: { roles: Role[]; children: React.ReactNode }) {
  const { me } = useMe();
  if (me && roles.includes(me.role)) return <>{children}</>;
  return (
    <Card className="items-center gap-2 p-14 text-center">
      <ShieldAlert className="size-8 text-muted-foreground" />
      <div className="font-semibold">You don&apos;t have access to this page</div>
      <p className="text-sm text-muted-foreground">
        It&apos;s available to {roles.map((r) => ROLE_LABEL[r]).join(", ")}. Ask an admin if you need it.
      </p>
    </Card>
  );
}

export function RoleBadge({ role, className = "" }: { role: Role; className?: string }) {
  return <span className={`inline-flex h-5 items-center rounded-full px-2 text-[11px] font-medium ${ROLE_TONE[role]} ${className}`}>{ROLE_LABEL[role]}</span>;
}

export function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("") || "?";
}
