import type { UploadStatus } from "@/lib/api";

/** The seven-screen run flow (docs/blueprints/analyst-forward-flow.md).
 *  "processing", "review", "bronze" and "mapping" have a page in this build
 *  — see `builtInThisPhase` and docs/blueprints/forward-flow-adoption.md §8
 *  for the phase order. "landing", "promoting" and "silver" (S3, S6, S7)
 *  aren't built yet: landing's own progress still surfaces inline on the G1
 *  decision record (`review/page.tsx`), and promotion/Silver Raw have no run
 *  route of their own — see `/batches/[batchId]` for that detail today. */
export type RunStepKey =
  | "processing"
  | "review"
  | "landing"
  | "bronze"
  | "mapping"
  | "promoting"
  | "silver";

export interface RunStepDef {
  key: RunStepKey;
  label: string;
  band: "landing" | "bronze" | "silver";
  gate?: boolean;
  builtInThisPhase: boolean;
}

export const RUN_STEPS: RunStepDef[] = [
  { key: "processing", label: "Profile", band: "landing", builtInThisPhase: true },
  { key: "review", label: "G1", band: "landing", gate: true, builtInThisPhase: true },
  { key: "landing", label: "Land", band: "landing", builtInThisPhase: false },
  { key: "bronze", label: "Bronze", band: "bronze", builtInThisPhase: true },
  { key: "mapping", label: "G2", band: "bronze", gate: true, builtInThisPhase: true },
  { key: "promoting", label: "Promote", band: "silver", builtInThisPhase: false },
  { key: "silver", label: "Silver", band: "silver", builtInThisPhase: false },
];

const STEP_ORDER = RUN_STEPS.map((s) => s.key);

/** A step at or behind the canonical step is safe to view, read-only once
 *  it's in the past - only a step *ahead* of it must redirect back. This is
 *  the "Navigation law" from analyst-forward-flow.md §2.1, made precise now
 *  that more than two steps are real routes: "not equal to canonical" was
 *  fine while canonical only ever resolved to "processing" or "review", but
 *  it would wrongly bounce a landed upload's `/review` (a completed, still-
 *  viewable step) once canonical started resolving to "bronze". */
export function isStepViewable(step: RunStepKey, canonical: RunStepKey): boolean {
  return STEP_ORDER.indexOf(step) <= STEP_ORDER.indexOf(canonical);
}

const PRE_INTERPRET: UploadStatus[] = [
  "received",
  "profiling",
  "profiled",
  "interpreting",
  "profile_failed",
  "interpret_failed",
];

/** The step the control plane's own state proves the run is at. A URL for a
 *  step ahead of this redirects here — see `isStepViewable` and the guard at
 *  the top of each `app/runs/[uploadId]/*` page.
 *
 *  `approved`/`landing`/`land_failed` still resolve to "review": S3 (a
 *  dedicated landing screen) isn't built, so that in-flight and failed state
 *  keeps surfacing inline on the G1 decision record, same as before. Once
 *  `landed`, the run has real Bronze content, so canonical moves to "bronze".
 *  "mapping" (S5) is deliberately never returned here - the control plane has
 *  no status field for "a mapping version exists" (§2.1), so it's reached by
 *  an explicit CTA once landed, not by this function. */
export function canonicalStep(status: UploadStatus): RunStepKey {
  if (PRE_INTERPRET.includes(status)) return "processing";
  if (status === "landed") return "bronze";
  return "review";
}

export function runHref(uploadId: string, step: RunStepKey): string {
  return `/runs/${encodeURIComponent(uploadId)}/${step}`;
}
