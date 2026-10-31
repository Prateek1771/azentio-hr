"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export interface Option { value: string; label: string }

/** Thin wrapper so call sites stay one line. `""` means "all / none selected". */
export function SimpleSelect({ value, onChange, options, placeholder, className }: {
  value: string; onChange: (v: string) => void; options: Option[]; placeholder?: string; className?: string;
}) {
  const items = [{ value: "", label: placeholder ?? "Select…" }, ...options];
  return (
    <Select items={items} value={value} onValueChange={(v) => onChange((v as string) ?? "")}>
      <SelectTrigger className={className}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {items.map((o) => (
          <SelectItem key={o.value || "__all"} value={o.value}>{o.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
