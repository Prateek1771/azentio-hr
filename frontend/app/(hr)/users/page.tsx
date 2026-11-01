"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, KeyRound, RefreshCw, Search, UserPlus, UserRoundCheck, UserRoundX, Users } from "lucide-react";
import { toast } from "sonner";
import { api, type Role, type User } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { ROLE_HINT, ROLE_LABEL, RoleBadge, RoleGate, initials, useMe } from "@/components/auth";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

const ROLES: Role[] = ["admin", "hr_ops", "approver", "manager", "auditor"];
const ROLE_OPTIONS = ROLES.map((r) => ({ value: r, label: ROLE_LABEL[r] }));

function tempPassword() {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint32Array(14));
  return Array.from(bytes, (b) => abc[b % abc.length]).join("");
}

function when(iso?: string | null) {
  if (!iso) return "Never";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export default function UsersPage() {
  return <RoleGate roles={["admin"]}><UsersAdmin /></RoleGate>;
}

type Confirm = { user: User; kind: "deactivate" | "reactivate" | "reset" } | null;

function UsersAdmin() {
  const { me } = useMe();
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null);

  const load = useCallback(() => {
    api<User[]>("/api/users").then((u) => { setUsers(u); setError(""); }).catch((e) => { setError((e as Error).message); setUsers([]); });
  }, []);
  useEffect(load, [load]);

  const activeAdmins = useMemo(() => (users ?? []).filter((u) => u.role === "admin" && u.active).length, [users]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (users ?? []).filter((u) => !q || `${u.name} ${u.email} ${ROLE_LABEL[u.role]}`.toLowerCase().includes(q));
  }, [users, query]);

  const patch = async (u: User, body: Partial<User> & { password?: string }, done: string) => {
    try {
      await api(`/api/users/${u.id}`, { method: "PATCH", json: body });
      toast.success(done);
      load();
      return true;
    } catch (e) {
      toast.error((e as Error).message);
      return false;
    }
  };

  const isLastAdmin = (u: User) => u.role === "admin" && u.active && activeAdmins <= 1;

  return (
    <>
      <PageHeader title="Users" subtitle="Who can sign in, and what each person can do. Changes take effect on their next request."
        actions={<Button onClick={() => setAdding(true)}><UserPlus className="size-4" /> Add user</Button>} />

      <div className="mb-4 grid gap-3 sm:grid-cols-5">
        {ROLES.map((r) => (
          <Card key={r} className="gap-1 p-3.5">
            <div className="flex items-center justify-between"><RoleBadge role={r} />
              <span className="text-lg font-semibold tabular-nums">{users ? users.filter((u) => u.role === r && u.active).length : "–"}</span></div>
            <p className="line-clamp-2 text-xs text-muted-foreground">{ROLE_HINT[r]}</p>
          </Card>
        ))}
      </div>

      <Card className="gap-0 p-0">
        <div className="flex items-center gap-2 border-b p-3">
          <div className="relative max-w-sm flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search name, email or role" className="pl-8" aria-label="Search users" />
          </div>
          <Button variant="ghost" size="icon" onClick={load} aria-label="Refresh"><RefreshCw className="size-4" /></Button>
        </div>
        {users === null ? (
          <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : error ? (
          <div className="p-10 text-center text-sm text-destructive">{error}</div>
        ) : shown.length === 0 ? (
          <div className="flex flex-col items-center gap-2 p-14 text-center">
            <Users className="size-8 text-muted-foreground" />
            <div className="font-semibold">{query ? "No one matches that search" : "No users yet"}</div>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Person</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Last sign-in</TableHead>
                <TableHead>Added</TableHead>
                <TableHead className="pr-4 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((u) => {
                const self = me?.id === u.id;
                const locked = isLastAdmin(u);
                return (
                  <TableRow key={u.id} className={cn(!u.active && "opacity-60")}>
                    <TableCell className="pl-4">
                      <div className="flex items-center gap-3">
                        <span className="grid size-8 place-items-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{initials(u.name)}</span>
                        <div className="leading-tight">
                          <div className="font-medium">{u.name}{self && <span className="ml-1.5 text-xs font-normal text-muted-foreground">(you)</span>}</div>
                          <div className="text-xs text-muted-foreground">{u.email}</div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div title={locked ? "Last active admin: make someone else an admin first" : undefined} className="w-36">
                        {locked || !u.active ? <RoleBadge role={u.role} /> : (
                          <SimpleSelect value={u.role} options={ROLE_OPTIONS} className="h-8 w-36"
                            onChange={(r) => r && r !== u.role && patch(u, { role: r as Role }, `${u.name} is now ${ROLE_LABEL[r as Role]}`)} />
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className={cn("inline-flex items-center gap-1.5 text-xs", u.active ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground")}>
                        <span className={cn("size-1.5 rounded-full", u.active ? "bg-emerald-500" : "bg-zinc-400")} />{u.active ? "Active" : "Deactivated"}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{when(u.last_login)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{when(u.created_at)}</TableCell>
                    <TableCell className="pr-4 text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="ghost" onClick={() => setConfirm({ user: u, kind: "reset" })} disabled={!u.active}>
                          <KeyRound className="size-3.5" /> Reset
                        </Button>
                        {u.active ? (
                          <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" disabled={locked || self}
                            title={locked ? "Last active admin" : self ? "You can't deactivate yourself" : undefined}
                            onClick={() => setConfirm({ user: u, kind: "deactivate" })}><UserRoundX className="size-3.5" /> Deactivate</Button>
                        ) : (
                          <Button size="sm" variant="ghost" onClick={() => setConfirm({ user: u, kind: "reactivate" })}><UserRoundCheck className="size-3.5" /> Reactivate</Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>

      <AddUser open={adding} onOpenChange={setAdding} onCreated={(email, password) => { load(); setIssued({ email, password }); }} />
      <ConfirmDialog confirm={confirm} onClose={() => setConfirm(null)} onConfirm={async (c) => {
        if (c.kind === "reset") {
          const pw = tempPassword();
          if (await patch(c.user, { password: pw }, `Password reset for ${c.user.name}`)) setIssued({ email: c.user.email, password: pw });
        } else {
          await patch(c.user, { active: c.kind === "reactivate" }, `${c.user.name} ${c.kind === "reactivate" ? "reactivated" : "deactivated"}`);
        }
        setConfirm(null);
      }} />
      <IssuedPassword issued={issued} onClose={() => setIssued(null)} />
    </>
  );
}

function AddUser({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (email: string, pw: string) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("hr_ops");
  const [password, setPassword] = useState(tempPassword);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const valid = name.trim() && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) && password.length >= 10;

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/api/users", { method: "POST", json: { name, email, role, password } });
      onOpenChange(false);
      onCreated(email.toLowerCase(), password);
      setName(""); setEmail(""); setRole("hr_ops"); setPassword(tempPassword());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add user</DialogTitle>
          <DialogDescription>They sign in with this email and the temporary password, then change it from the account menu.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5"><Label htmlFor="nu-name">Full name</Label>
            <Input id="nu-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoFocus /></div>
          <div className="space-y-1.5"><Label htmlFor="nu-email">Work email</Label>
            <Input id="nu-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" /></div>
          <div className="space-y-1.5"><Label>Role</Label>
            <SimpleSelect value={role} onChange={(r) => r && setRole(r as Role)} options={ROLE_OPTIONS} className="w-full" />
            <p className="text-xs text-muted-foreground">{ROLE_HINT[role]}</p></div>
          <div className="space-y-1.5"><Label htmlFor="nu-pw">Temporary password</Label>
            <div className="flex gap-2">
              <Input id="nu-pw" value={password} onChange={(e) => setPassword(e.target.value)} className="font-mono" />
              <Button variant="outline" size="icon" onClick={() => setPassword(tempPassword())} aria-label="Generate password"><RefreshCw className="size-4" /></Button>
            </div>
            {password.length < 10 && <p className="text-xs text-destructive">At least 10 characters.</p>}</div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={save} disabled={busy || !valid}>Add user</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ConfirmDialog({ confirm, onClose, onConfirm }: { confirm: Confirm; onClose: () => void; onConfirm: (c: NonNullable<Confirm>) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  if (!confirm) return null;
  const { user, kind } = confirm;
  const copy = {
    deactivate: { title: `Deactivate ${user.name}?`, body: "They're signed out everywhere immediately and can't sign in until reactivated. Their history stays in the audit log.", cta: "Deactivate" },
    reactivate: { title: `Reactivate ${user.name}?`, body: "They can sign in again with their existing password.", cta: "Reactivate" },
    reset: { title: `Reset ${user.name}'s password?`, body: "A new temporary password is generated and their current sessions end. Share it with them securely.", cta: "Reset password" },
  }[kind];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{copy.title}</DialogTitle><DialogDescription>{copy.body}</DialogDescription></DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button variant={kind === "deactivate" ? "destructive" : "default"} disabled={busy}
            onClick={async () => { setBusy(true); await onConfirm(confirm); setBusy(false); }}>{copy.cta}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function IssuedPassword({ issued, onClose }: { issued: { email: string; password: string } | null; onClose: () => void }) {
  if (!issued) return null;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Temporary password</DialogTitle>
          <DialogDescription>Share this with {issued.email} securely. It won&apos;t be shown again.</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 rounded-lg border bg-muted/40 p-3 font-mono text-sm">
          <span className="flex-1 break-all">{issued.password}</span>
          <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(issued.password).then(() => toast.success("Copied"))}>
            <Copy className="size-3.5" /> Copy</Button>
        </div>
        <DialogFooter><Button onClick={onClose}>Done</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
