"use client";

import { useEffect, useState } from "react";
import { Eye, EyeOff, FileCheck2, Loader2, LockKeyhole, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import { api, type Role, type User } from "@/lib/api";
import { ROLE_LABEL, RoleBadge } from "@/components/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const DEV_ACCOUNTS: { email: string; password: string; role: Role }[] = [
  { email: "admin@azentio.local", password: "admin-dev-pass", role: "admin" },
  { email: "hrops@azentio.local", password: "hrops-dev-pass", role: "hr_ops" },
  { email: "approver@azentio.local", password: "approver-dev-pass", role: "approver" },
  { email: "manager@azentio.local", password: "manager-dev-pass", role: "manager" },
  { email: "auditor@azentio.local", password: "auditor-dev-pass", role: "auditor" },
];

const POINTS = [
  { Icon: Workflow, title: "Every joiner, planned", text: "Documents and tasks worked out per role, department and location, with the reason for each." },
  { Icon: FileCheck2, title: "AI checks, human sign-off", text: "Routine documents clear automatically; identity and work authorisation always go to a person." },
  { Icon: ShieldCheck, title: "Every action on record", text: "A tamper-evident audit trail shows who did what, when, and why." },
];

/** Only same-origin paths, so a crafted ?next= can't bounce users to another site. */
function safeNext(): string {
  const n = new URLSearchParams(window.location.search).get("next") ?? "";
  return n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/login") ? n : "/dashboard";
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Already signed in? Skip the form.
  useEffect(() => {
    api<User>("/api/auth/me").then(() => window.location.replace(safeNext())).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/login", { method: "POST", json: { email, password } });
      window.location.replace(safeNext());
    } catch (err) {
      setError((err as Error).message || "Sign-in failed");
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen bg-background md:grid-cols-[1.05fr_1fr]">
      <aside className="relative hidden overflow-hidden bg-sidebar p-12 text-white md:flex md:flex-col">
        <div aria-hidden className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_40rem_at_-10%_-10%,rgba(139,92,246,0.35),transparent_60%),radial-gradient(40rem_30rem_at_110%_110%,rgba(79,70,229,0.35),transparent_60%)]" />
        <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:linear-gradient(to_right,white_1px,transparent_1px),linear-gradient(to_bottom,white_1px,transparent_1px)] [background-size:44px_44px]" />
        <div className="relative flex items-center gap-2.5">
          <div className="grid size-9 place-items-center rounded-xl bg-gradient-to-br from-violet-400 to-indigo-600 shadow-lg shadow-indigo-900/40">
            <Sparkles className="size-4.5 text-white" />
          </div>
          <div className="leading-tight">
            <div className="font-semibold">Azentio Onboard</div>
            <div className="text-xs text-white/60">AI onboarding agent</div>
          </div>
        </div>
        <div className="relative mt-auto max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight">From offer to day one, without the chasing.</h2>
          <ul className="mt-8 space-y-5">
            {POINTS.map(({ Icon, title, text }) => (
              <li key={title} className="flex gap-3.5">
                <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-white/10 ring-1 ring-white/15">
                  <Icon className="size-4 text-violet-200" />
                </div>
                <div>
                  <div className="text-sm font-medium">{title}</div>
                  <p className="mt-0.5 text-sm text-white/65">{text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative mt-12 text-xs text-white/45">Access is limited to invited staff. Joiners use the secure link in their email.</p>
      </aside>

      <main className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 md:hidden">
            <div className="grid size-9 place-items-center rounded-xl bg-gradient-to-br from-violet-400 to-indigo-600">
              <Sparkles className="size-4.5 text-white" />
            </div>
            <span className="font-semibold">Azentio Onboard</span>
          </div>
          <div className="mb-7">
            <div className="mb-3 grid size-10 place-items-center rounded-xl bg-primary/10 text-primary">
              <LockKeyhole className="size-5" />
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
            <p className="mt-1 text-sm text-muted-foreground">Use the work email your admin invited.</p>
          </div>

          <form onSubmit={submit} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="email">Work email</Label>
              <Input id="email" type="email" autoComplete="username" autoFocus required value={email}
                onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" className="h-10" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <div className="relative">
                <Input id="password" type={show ? "text" : "password"} autoComplete="current-password" required value={password}
                  onChange={(e) => setPassword(e.target.value)} className="h-10 pr-10" />
                <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}
                  className="absolute inset-y-0 right-0 grid w-10 place-items-center text-muted-foreground hover:text-foreground">
                  {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>
            {error && (
              <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</p>
            )}
            <Button type="submit" className="h-10 w-full" disabled={busy || !email || !password}>
              {busy ? <><Loader2 className="size-4 animate-spin" /> Signing in…</> : "Sign in"}
            </Button>
          </form>

          {process.env.NODE_ENV !== "production" && (
            <div className="mt-8 rounded-xl border border-dashed p-4">
              <div className="mb-2.5 text-xs font-medium text-muted-foreground">Development accounts: click to fill</div>
              <div className="grid gap-1.5">
                {DEV_ACCOUNTS.map((a) => (
                  <button key={a.email} type="button" onClick={() => { setEmail(a.email); setPassword(a.password); setError(""); }}
                    className="flex items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm hover:bg-muted"
                    aria-label={`Fill ${ROLE_LABEL[a.role]} account`}>
                    <span className="truncate">{a.email}</span>
                    <RoleBadge role={a.role} />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
