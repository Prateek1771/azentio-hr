"use client";

import { useMemo, useState } from "react";

interface GNode { id: string; label: string; type: string; state?: string; status?: string; category?: string }
interface GEdge { from: string; to: string; type: string; label?: string }

const STATE_FILL: Record<string, string> = {
  completed: "#10b981", pending: "#0ea5e9", blocked: "#a1a1aa", overdue: "#f43f5e",
};
const W = 900, H = 640, CX = W / 2, CY = H / 2 + 10;

/** Radial layout: employee in the middle, checklist on a ring, evidence files outside, manager on top. */
export function GraphView({ nodes, edges }: { nodes: GNode[]; edges: GEdge[] }) {
  const [hover, setHover] = useState<string | null>(null);

  const pos = useMemo(() => {
    const p: Record<string, { x: number; y: number }> = {};
    const emp = nodes.find((n) => n.type === "employee");
    if (emp) p[emp.id] = { x: CX, y: CY };
    nodes.filter((n) => n.type === "manager").forEach((n) => (p[n.id] = { x: CX, y: 46 }));
    const order = { document: 0, ack: 1, task: 2 } as Record<string, number>;
    const items = nodes.filter((n) => n.type in order).sort((a, b) => order[a.type] - order[b.type]);
    const R = 215;
    items.forEach((n, i) => {
      const a = -Math.PI / 2 + ((i + 0.5) / items.length) * Math.PI * 2 + 0.25;
      p[n.id] = { x: CX + R * Math.cos(a) * 1.25, y: CY + R * Math.sin(a) * 0.95 };
    });
    for (const e of edges.filter((e) => e.type === "HAS_DOC")) {
      const it = p[e.from];
      if (!it) continue;
      const dx = it.x - CX, dy = it.y - CY, len = Math.hypot(dx, dy) || 1;
      p[e.to] = { x: it.x + (dx / len) * 70, y: it.y + (dy / len) * 50 };
    }
    return p;
  }, [nodes, edges]);

  const lit = (e: GEdge) => hover && (e.from === hover || e.to === hover);
  const nodeById = Object.fromEntries(nodes.map((n) => [n.id, n]));

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full select-none">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-amber-500" />
          </marker>
        </defs>
        {edges.map((e, i) => {
          const a = pos[e.from], b = pos[e.to];
          if (!a || !b) return null;
          if (e.type === "DEPENDS_ON") {
            const mx = (a.x + b.x) / 2 + (CX - (a.x + b.x) / 2) * 0.45, my = (a.y + b.y) / 2 + (CY - (a.y + b.y) / 2) * 0.45;
            return <path key={i} d={`M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}`} fill="none" markerEnd="url(#arrow)"
              className={lit(e) ? "stroke-amber-500" : "stroke-amber-400/35"} strokeWidth={lit(e) ? 2 : 1.2} strokeDasharray="4 3" />;
          }
          const cls = e.type === "MISMATCH" ? "stroke-rose-500" : lit(e) ? "stroke-primary" : "stroke-border";
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={cls}
            strokeWidth={e.type === "MISMATCH" ? 2 : lit(e) ? 2 : 1} strokeDasharray={e.type === "MISMATCH" ? "6 4" : undefined} />;
        })}
        {nodes.map((n) => {
          const p = pos[n.id];
          if (!p) return null;
          const dim = hover && hover !== n.id && !edges.some((e) => (e.from === hover && e.to === n.id) || (e.to === hover && e.from === n.id));
          const label = n.label.length > 26 ? n.label.slice(0, 24) + "…" : n.label;
          if (n.type === "employee") return (
            <g key={n.id} opacity={dim ? 0.4 : 1} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}>
              <circle cx={p.x} cy={p.y} r={44} className="fill-primary" />
              <circle cx={p.x} cy={p.y} r={52} className="fill-none stroke-primary/30" strokeWidth={6} />
              <text x={p.x} y={p.y + 4} textAnchor="middle" className="fill-primary-foreground text-[13px] font-semibold">{n.label.split(" ")[0]}</text>
            </g>
          );
          if (n.type === "manager") return (
            <g key={n.id} opacity={dim ? 0.4 : 1} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}>
              <rect x={p.x - 80} y={p.y - 16} width={160} height={32} rx={16} className="fill-card stroke-border" />
              <text x={p.x} y={p.y + 4} textAnchor="middle" className="fill-foreground text-[11px]">👤 {n.label} · manager</text>
            </g>
          );
          if (n.type === "file") return (
            <g key={n.id} opacity={dim ? 0.4 : 1} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}>
              <rect x={p.x - 9} y={p.y - 11} width={18} height={22} rx={3}
                className={n.status === "Accepted" ? "fill-emerald-100 stroke-emerald-500" : n.status === "Correction Requested" ? "fill-rose-100 stroke-rose-500" : "fill-violet-100 stroke-violet-500"} />
              <title>{n.label} — {n.status}</title>
            </g>
          );
          const fill = STATE_FILL[n.state ?? "pending"];
          const shape = n.type === "document"
            ? <rect x={p.x - 10} y={p.y - 10} width={20} height={20} rx={5} fill={fill} />
            : n.type === "ack" ? <polygon points={`${p.x},${p.y - 12} ${p.x + 12},${p.y} ${p.x},${p.y + 12} ${p.x - 12},${p.y}`} fill={fill} />
            : <circle cx={p.x} cy={p.y} r={11} fill={fill} />;
          const right = p.x >= CX;
          return (
            <g key={n.id} opacity={dim ? 0.35 : 1} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} className="cursor-default">
              {shape}
              <text x={p.x + (right ? 16 : -16)} y={p.y + 4} textAnchor={right ? "start" : "end"} className="fill-foreground text-[11px]">{label}</text>
              <title>{n.label} — {n.status} ({n.category})</title>
            </g>
          );
        })}
      </svg>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-zinc-400" /> document</span>
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-zinc-400" /> task</span>
        <span className="flex items-center gap-1.5"><span className="size-2.5 rotate-45 bg-zinc-400" /> acknowledgement</span>
        {Object.entries(STATE_FILL).map(([k, v]) => <span key={k} className="flex items-center gap-1.5"><span className="size-2.5 rounded-full" style={{ background: v }} /> {k}</span>)}
        <span className="flex items-center gap-1.5"><span className="h-px w-5 border-t border-dashed border-amber-500" /> depends on</span>
        <span className="flex items-center gap-1.5"><span className="h-px w-5 border-t-2 border-dashed border-rose-500" /> data mismatch</span>
        {hover && nodeById[hover] && <span className="ml-auto font-medium text-foreground">{nodeById[hover].label}</span>}
      </div>
    </div>
  );
}
