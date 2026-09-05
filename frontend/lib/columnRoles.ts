import type { ColumnFacts, ColumnRole, Interpretation, Profile } from "@/lib/api";

/** Column roles on the frontend (PR-7): vocabulary, order and the one rule for
 *  "which role does this column have" - the interpretation's judged role where
 *  the model saw the column, the profiler's hint otherwise (an interpretation
 *  written before prompt v3 has no roles at all; a v1 profile has no hints).
 *  Nothing here classifies: the backend did (`interpret_file._assemble`), and
 *  this only reads it. */

export type RoleKey = ColumnRole["role"];

/** The order screens group and sort by (plan §7.1): what identifies a row,
 *  what is measured, how it is sliced, when, then the descriptive rest, with
 *  the platform's own bookkeeping last. */
export const ROLE_ORDER: RoleKey[] = [
  "identifier",
  "measure",
  "dimension",
  "date",
  "business_attribute",
  "derived",
  "unclassified",
  "technical",
];

export const ROLE_LABEL: Record<RoleKey, string> = {
  identifier: "Identifiers",
  measure: "Measures",
  dimension: "Dimensions",
  date: "Dates",
  business_attribute: "Business attributes",
  derived: "Derived",
  unclassified: "Unclassified",
  technical: "Technical",
};

export const ROLE_WORD: Record<RoleKey, string> = {
  identifier: "identifier",
  measure: "measure",
  dimension: "dimension",
  date: "date",
  business_attribute: "attribute",
  derived: "derived",
  unclassified: "unclassified",
  technical: "technical",
};

export function roleIndex(role: RoleKey): number {
  const at = ROLE_ORDER.indexOf(role);
  return at < 0 ? ROLE_ORDER.length : at;
}

/** One `ColumnRole` per profiled column. Model-judged where the interpretation
 *  has one, otherwise the hint stands in (marked `source: "hint"`, the same
 *  shape `_assemble` writes for a column the model skipped). */
export function rolesByColumn(
  profile: Profile,
  interpretation: Interpretation | null,
): Record<string, ColumnRole> {
  const judged = new Map(
    (interpretation?.content.column_roles ?? []).map((role) => [role.name, role] as const),
  );
  const out: Record<string, ColumnRole> = {};
  for (const column of profile.facts.columns) {
    const hint = column.hint ?? "unclassified";
    out[column.name] = judged.get(column.name) ?? {
      name: column.name,
      role: hint,
      importance: hint === "technical" || hint === "unclassified" ? "low" : "medium",
      reason: "from profile hint",
      hint,
      source: "hint",
    };
  }
  return out;
}

/** Profiled columns grouped by role in `ROLE_ORDER`, empty groups dropped;
 *  within a group the file's own column order is kept. */
export function groupByRole(
  columns: ColumnFacts[],
  roles: Record<string, ColumnRole>,
): { role: RoleKey; columns: ColumnFacts[] }[] {
  return ROLE_ORDER.map((role) => ({
    role,
    columns: columns.filter((c) => (roles[c.name]?.role ?? "unclassified") === role),
  })).filter((group) => group.columns.length > 0);
}
