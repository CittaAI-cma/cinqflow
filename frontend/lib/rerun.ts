import type { LedgerScope } from "@/lib/api";

/** Which object a re-run is about - the ledger row's own scope, so a caller
 *  never has to know which of the three routes a step belongs to. */
export type RerunSource =
  | { kind: "upload"; uploadId: string }
  | { kind: "batch"; batchId: string }
  | { kind: "feed_version"; feed: string; version: number };

/** The states a step may be re-run from - mirrors `RERUNNABLE` in
 *  `workflow/dag.py`. The API is the boundary; this only decides whether to
 *  offer the button. */
export const RERUNNABLE_STATES = new Set(["failed", "done", "skipped"]);

/** The consequence of running each step again, stated before the click - the
 *  `ConfirmDialog` rule: a real, outward-facing effect deserves a beat between
 *  intent and effect. Composed here, never generated. */
export const RERUN_CONSEQUENCE: Record<string, string> = {
  profile:
    "Re-profiling re-reads the preserved original and replaces the profile; the AI interpretation then runs again on the new profile.",
  interpret:
    "The model interprets the same profile again. The new interpretation replaces the current one; an undecided G1 restarts from it.",
  land: "Re-landing writes a new Bronze batch from the preserved original. Bronze is append-only: the earlier batch stays, and Bronze analysis runs again for the new one.",
  analyze:
    "Bronze analysis runs again: a new mapping proposal is produced, and the earlier one stays on record for lineage.",
  preview:
    "The preview is recomputed over the same sample of the same batch. G2 stays closed until the result is current for the spec.",
  promote:
    "Re-running promotion rebuilds this batch's Silver rows and quarantine; Bronze is untouched.",
};

export const RERUN_FALLBACK_CONSEQUENCE =
  "This step runs again as a new generation; the earlier run stays on record.";

/** The ledger row carries its own scope, so the re-run route is derived from
 *  it - a batch-scoped step shown under an upload still re-runs against its
 *  batch. */
export function rerunSourceFor(scopeKind: LedgerScope, scopeId: string): RerunSource | null {
  if (scopeKind === "upload") return { kind: "upload", uploadId: scopeId };
  if (scopeKind === "batch") return { kind: "batch", batchId: scopeId };
  const at = scopeId.lastIndexOf(":v");
  if (at < 0) return null;
  return {
    kind: "feed_version",
    feed: scopeId.slice(0, at),
    version: Number(scopeId.slice(at + 2)),
  };
}
