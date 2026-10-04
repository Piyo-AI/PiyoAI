import type { CatalogEntry } from "./api";

/** "tool:gmail.send" -> a sentence the user can judge. */
export function describePermission(label: string): string {
  const i = label.indexOf(":");
  const kind = label.slice(0, i);
  const value = label.slice(i + 1);
  switch (kind) {
    case "tool":
      return `Use the ${value} tool`;
    case "integration":
      return `Use your ${value} connection`;
    case "secret":
      return `Read the stored secret ${value}`;
    case "script":
      return `Run its own script ${value}`;
    case "runtime":
      return `Run code with ${value}`;
    default:
      return label;
  }
}

export const BADGES: Record<string, { label: string; hint: string }> = {
  official: { label: "Official", hint: "Made and maintained by the Piyo AI team" },
  verified: { label: "Verified publisher", hint: "From a publisher the Piyo AI team has verified" },
  community: { label: "Community", hint: "Submitted by the community and checked by the Piyo AI team" },
};

export function categoryLabel(category: string): string {
  return category ? category.charAt(0).toUpperCase() + category.slice(1) : "Other";
}

/** Why the catalog withdrew this version of the skill, or null. */
export function withdrawn(e: Pick<CatalogEntry, "revoked">, version: string): string | null {
  return e.revoked.find((r) => r.version === version || r.version === "*")?.reason ?? null;
}

/** What the user has to do before the skill works, in words. */
export function setupNeeds(e: Pick<CatalogEntry, "integrations" | "secrets">): { level: "none" | "some" | "more"; text: string } {
  const parts: string[] = [];
  if (e.integrations.length) parts.push(`your ${e.integrations.join(", ")} connection`);
  if (e.secrets.length) parts.push(e.secrets.length === 1 ? "one key" : `${e.secrets.length} keys`);
  if (parts.length === 0) return { level: "none", text: "Ready to use" };
  return { level: e.integrations.length && e.secrets.length ? "more" : "some", text: `Needs ${parts.join(" and ")}` };
}

/** Skills matching every word of `query` (name, description, author, category) and the chosen category. */
export function filterCatalog(skills: CatalogEntry[], query: string, category: string | null): CatalogEntry[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return skills.filter((e) => {
    if (category && e.category !== category) return false;
    const text = `${e.name} ${e.description} ${e.author ?? ""} ${e.category}`.toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

/** Categories present in the listing, most skills first. */
export function categoriesOf(skills: CatalogEntry[]): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const e of skills) counts.set(e.category, (counts.get(e.category) ?? 0) + 1);
  return [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
