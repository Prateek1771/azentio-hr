"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, ArrowLeftRight, Building2, FileUp, Globe, Users } from "lucide-react";
import { api, type Connector } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { RoleBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const ICON: Record<string, typeof Globe> = { candidate_portal: Globe, candidate_import: FileUp, hr_portal: Building2 };

export default function ConnectorsPage() {
  const [list, setList] = useState<Connector[] | null>(null);
  useEffect(() => { api<Connector[]>("/api/connectors").then(setList).catch(() => setList([])); }, []);

  const section = (role: "source" | "target", title: string, hint: string) => {
    const items = list?.filter((c) => c.role === role).sort((a, b) => a.name.localeCompare(b.name));
    return (
      <section className="space-y-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold">{title}</h2>
          <span className="text-xs text-muted-foreground">{hint}</span>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items === undefined ? Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-40 rounded-xl" />)
            : items.map((c) => {
              const Icon = ICON[c.key] ?? Globe;
              return (
                <Link key={c.id} href={`/connectors/${c.id}`} className="group">
                  <Card className={cn("h-full gap-3 border-l-4 p-5 transition-shadow group-hover:shadow-md",
                    role === "source" ? "border-l-source" : "border-l-target")}>
                    <div className="flex items-start gap-3">
                      <div className={cn("grid size-10 shrink-0 place-items-center rounded-lg",
                        role === "source" ? "bg-source-soft text-source" : "bg-target-soft text-target")}>
                        <Icon className="size-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-semibold">{c.name}</span>
                          {c.demo && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground uppercase">Demo</span>}
                        </div>
                        <p className="mt-0.5 text-xs text-muted-foreground">{c.description}</p>
                      </div>
                      <ArrowRight className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                    </div>
                    <div className="mt-auto flex items-center justify-between border-t pt-3 text-sm">
                      <span className="flex items-center gap-1.5 text-muted-foreground">
                        <Users className="size-4" /> <b className="text-foreground tabular-nums">{c.record_count}</b> {c.record_kind === "candidate" ? "candidates" : "employees"}
                      </span>
                      <RoleBadge role={c.role} />
                    </div>
                  </Card>
                </Link>
              );
            })}
        </div>
      </section>
    );
  };

  return (
    <>
      <PageHeader title="Connectors" subtitle="Systems that candidate and employee records come from or go to. Demo connectors keep their data in this app."
        actions={<Button nativeButton={false} render={<Link href="/integrations" />}><ArrowLeftRight className="size-4" /> Run an integration</Button>} />
      <div className="space-y-8">
        {section("source", "Sources", "Where candidates come from")}
        {section("target", "Targets", "Where employee records are written")}
      </div>
    </>
  );
}
