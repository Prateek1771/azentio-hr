"use client";

import { useEffect, useState } from "react";
import { api, type Event } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { Timeline } from "@/components/timeline";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function ActivityPage() {
  const [events, setEvents] = useState<Event[] | null>(null);
  useEffect(() => {
    const load = () => api<Event[]>("/api/events?limit=150").then(setEvents).catch(() => {});
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  return (
    <>
      <PageHeader title="Activity" subtitle="Everything the agent and HR team did: emails, verifications, automatic provisioning and escalations. Refreshes every 10s." />
      <Card className="p-6">{events ? <Timeline events={events} /> : <Skeleton className="h-96" />}</Card>
    </>
  );
}
