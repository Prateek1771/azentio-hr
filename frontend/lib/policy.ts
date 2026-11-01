import { toast } from "sonner";
import { api, STATE_LABEL, type VersionsResp } from "@/lib/api";

/** Open (or create) the single rule-set draft, then go to the editor. Returns false if another version is mid-flight. */
export async function openDraft(push: (href: string) => void, edit?: string): Promise<boolean> {
  const vs = await api<VersionsResp>("/api/policy/versions");
  if (!vs.open_id) await api("/api/policy/drafts", { method: "POST" });
  else {
    const open = vs.versions.find((v) => v.id === vs.open_id);
    if (open && open.state !== "draft") {
      toast.info(`v${open.number} is ${STATE_LABEL[open.state].toLowerCase()}. Wait for it to finish before starting new changes.`);
      return false;
    }
  }
  push(`/rules/draft${edit ? `?edit=${encodeURIComponent(edit)}` : ""}`);
  return true;
}
