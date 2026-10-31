import { Fragment } from "react";

/** ponytail: tiny Markdown subset (headings, bullets, bold, italics, [citations]); add a real parser if policies get tables. */
function inline(text: string, cite?: (s: string) => React.ReactNode) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\.md[^\]]*\]|\[checklist\])/g);
  return parts.map((p, i) => {
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={i}>{p.slice(2, -2)}</strong>;
    if (p.startsWith("*") && p.endsWith("*") && p.length > 2) return <em key={i}>{p.slice(1, -1)}</em>;
    if (/^\[.*\]$/.test(p) && cite) return <Fragment key={i}>{cite(p.slice(1, -1))}</Fragment>;
    return <Fragment key={i}>{p}</Fragment>;
  });
}

export function Markdown({ text, cite }: { text: string; cite?: (s: string) => React.ReactNode }) {
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length} className="my-2 list-disc space-y-1 pl-5">{list.map((l, i) => <li key={i}>{inline(l, cite)}</li>)}</ul>);
    list = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
    if (bullet) { list.push(bullet[1]); continue; }
    flush();
    if (!line.trim()) continue;
    if (line.startsWith("### ")) blocks.push(<h4 key={blocks.length} className="mt-3 mb-1 font-semibold">{inline(line.slice(4), cite)}</h4>);
    else if (line.startsWith("## ")) blocks.push(<h3 key={blocks.length} className="mt-4 mb-1 text-base font-semibold">{inline(line.slice(3), cite)}</h3>);
    else if (line.startsWith("# ")) blocks.push(<h2 key={blocks.length} className="mb-2 text-lg font-semibold">{inline(line.slice(2), cite)}</h2>);
    else blocks.push(<p key={blocks.length} className="my-1.5 leading-relaxed">{inline(line, cite)}</p>);
  }
  flush();
  return <div className="text-sm">{blocks}</div>;
}
