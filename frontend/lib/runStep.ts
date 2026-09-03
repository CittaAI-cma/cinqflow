import type { UploadStatus } from "@/lib/api";

/** The seven-screen run flow (docs/blueprints/analyst-forward-flow.md). Only
 *  "processing" and "review" have a page in this build — see `builtInThisPhase`
 *  and docs/blueprints/forward-flow-adoption.md §8 for the phase order. */
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
  { key: "bronze", label: "Bronze", band: "bronze", builtInThisPhase: false },
  { key: "mapping", label: "G2", band: "bronze", gate: true, builtInThisPhase: false },
  { key: "promoting", label: "Promote", band: "silver", builtInThisPhase: false },
  { key: "silver", label: "Silver", band: "silver", builtInThisPhase: false },
];

const PRE_INTERPRET: UploadStatus[] = [
  "received",
  "profiling",
  "profiled",
  "interpreting",
  "profile_failed",
  "interpret_failed",
];

/** The step the control plane's own state proves the run is at. A URL for a
 *  step ahead of this redirects here — see the guard at the top of each
 *  `app/runs/[uploadId]/*` page.
 *
 *  Only resolves to "processing" or "review" in this phase: everything from
 *  `approved` onward is real control-plane state (S3–S7 aren't built yet, but
 *  the status itself is not ahead of anything a *user* asked for), so it
 *  resolves to "review", which renders those states read-only per the S2 spec
 *  ("approved or later: read-only, decision record, continue CTA"). Extending
 *  this to branch into landing/bronze/mapping/promoting/silver is Phase 2+. */
export function canonicalStep(status: UploadStatus): RunStepKey {
  return PRE_INTERPRET.includes(status) ? "processing" : "review";
}

export function runHref(uploadId: string, step: RunStepKey): string {
  return `/runs/${encodeURIComponent(uploadId)}/${step}`;
}
