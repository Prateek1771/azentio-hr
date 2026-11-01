"use client";

import { useState } from "react";
import { Minus, Plus, RotateCw } from "lucide-react";
import { API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** The original file, as the reviewer must judge it. PDFs use the browser's viewer (pages, zoom); images get zoom + rotate. */
export function DocViewer({ id, mime, filename, className }: { id: string; mime: string; filename: string; className?: string }) {
  const [zoom, setZoom] = useState(1);
  const [turn, setTurn] = useState(0);
  const src = `${API}/api/documents/${id}/file`;
  if (mime === "application/pdf") return <iframe src={src} title={filename} className={cn("h-[70vh] w-full rounded-lg border", className)} />;
  return (
    <div className={cn("relative min-h-[50vh] overflow-auto rounded-lg border bg-[repeating-conic-gradient(var(--muted)_0_25%,transparent_0_50%)] bg-[length:16px_16px]", className)}>
      <div className="sticky top-2 z-10 ml-auto mr-2 flex w-fit gap-1 rounded-lg border bg-background/90 p-1 shadow-sm backdrop-blur">
        <Button size="icon" variant="ghost" className="size-7" onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} aria-label="Zoom out"><Minus className="size-3.5" /></Button>
        <span className="grid w-11 place-items-center text-xs tabular-nums">{Math.round(zoom * 100)}%</span>
        <Button size="icon" variant="ghost" className="size-7" onClick={() => setZoom((z) => Math.min(4, z + 0.25))} aria-label="Zoom in"><Plus className="size-3.5" /></Button>
        <Button size="icon" variant="ghost" className="size-7" onClick={() => setTurn((t) => (t + 90) % 360)} aria-label="Rotate"><RotateCw className="size-3.5" /></Button>
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={filename} style={{ transform: `rotate(${turn}deg) scale(${zoom})` }}
        className="mx-auto -mt-8 max-h-[70vh] min-w-24 origin-top object-contain pt-10 transition-transform [image-rendering:auto]" />
    </div>
  );
}
