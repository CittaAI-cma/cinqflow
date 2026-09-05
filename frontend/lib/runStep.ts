import type { StepProgress, UploadStatus } from "@/lib/api";

/** The seven-screen run flow (docs/blueprints/analyst-forward-flow.md).
 *  "processing", "review", "bronze" and "mapping" have a page in this build
 *  — see `builtInThisPhase` and docs/blueprints/forward-flow-adoption.md §8
 *  for the phase order. "landing", "promoting" and "silver" (S3, S6, S7)
 *  aren't built yet: landing's own progress surfaces inline on the G1
 *  decision record (`review/page.tsx`), and promotion/Silver Raw have no run
 *  route of their own — see `/batches/[batchId]` for that detail today.
 *
 *  Since PR-2 the *state* of each screen comes from the backend's step ledger
 *  (`workflow/dag.py`, served as `steps[]` on every `/progress` endpoint):
 *  each screen names the ledger steps it shows (`ledgerSteps`), and
 *  `canonicalStepFromSteps` / `railStates` derive everything from those.
 *  `canonicalStep(status)` stays as the fallback for a run the ledger has
 *  nothing about (uploaded before the migration, or the API is unreachable). */
export type RunStepKey =
  | "processing"
  | "review"
  | "landing"
  | "bronze"
  | "mapping"
  | "promoting"
  | "silver";

/** The backend's step keys (`workflow/dag.py: WORKFLOW`), in order. */
export type LedgerStepKey =
  | "profile"
  | "interpret"
  | "gate_g1"
  | "land"
  | "analyze"
  | "preview"
  | "gate_g2"
  | "promote";

export interface RunStepDef {
  key: RunStepKey;
  label: string;
  band: "landing" | "bronze" | "silver";
  gate?: boolean;
  builtInThisPhase: boolean;
  /** The ledger steps this screen is about; its rail dot is derived from them. */
  ledgerSteps: LedgerStepKey[];
}

export const RUN_STEPS: RunStepDef[] = [
  {
    key: "processing",
    label: "Profile",
    band: "landing",
    builtInThisPhase: true,
    ledgerSteps: ["profile", "interpret"],
  },
  { key: "review", label: "G1", band: "landing", gate: true, builtInThisPhase: true, ledgerSteps: ["gate_g1"] },
  { key: "landing", label: "Land", band: "landing", builtInThisPhase: false, ledgerSteps: ["land"] },
  { key: "bronze", label: "Bronze", band: "bronze", builtInThisPhase: true, ledgerSteps: ["analyze"] },
  {
    key: "mapping",
    label: "G2",
    band: "bronze",
    gate: true,
    builtInThisPhase: true,
    ledgerSteps: ["preview", "gate_g2"],
  },
  // Silver Raw is the outcome of promotion, so both dots read the same step.
  { key: "promoting", label: "Promote", band: "silver", builtInThisPhase: false, ledgerSteps: ["promote"] },
  { key: "silver", label: "Silver", band: "silver", builtInThisPhase: false, ledgerSteps: ["promote"] },
];

const STEP_ORDER = RUN_STEPS.map((s) => s.key);

/** A step at or behind the canonical step is safe to view, read-only once
 *  it's in the past - only a step *ahead* of it must redirect back. This is
 *  the "Navigation law" from analyst-forward-flow.md §2.1. */
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

/** Fallback only (see the module comment): the step the upload's *status*
 *  proves the run is at. `approved`/`landing`/`land_failed` resolve to
 *  "review" (S3 isn't built), `landed` to "bronze"; "mapping" is never
 *  derivable from a status, which is exactly what the ledger fixes. */
export function canonicalStep(status: UploadStatus): RunStepKey {
  if (PRE_INTERPRET.includes(status)) return "processing";
  if (status === "landed") return "bronze";
  return "review";
}

/** The step the ledger proves the run is at: the screen for the furthest step
 *  with any activity, where a finished step hands over to the screen after it
 *  only when that screen exists. Returns null when the ledger has nothing to
 *  say (no rows at all), so the caller can fall back to `canonicalStep`.
 *
 *  Feed-version steps (preview, G2) are deliberately not consulted: they
 *  belong to the feed's mapping version, which may predate this upload, and a
 *  file must not be sent past its own Bronze review because an earlier
 *  delivery's mapping was approved. They still drive the rail (`railStates`).
 *  A `pending` step is queued but not started - the analyst still belongs on
 *  the screen before it, which is why it does not count as activity. */
export function canonicalStepFromSteps(steps: StepProgress[]): RunStepKey | null {
  const own = steps.filter((s) => s.scope_kind !== "feed_version");
  const active = own.filter((s) => s.state !== "not_reached" && s.state !== "pending");
  if (active.length === 0) {
    return own.some((s) => s.state === "pending") ? "processing" : null;
  }
  const last = active[active.length - 1]; // `steps` arrive in workflow order
  switch (last.key as LedgerStepKey) {
    case "profile":
      return "processing";
    case "interpret":
      return last.state === "done" ? "review" : "processing";
    case "gate_g1":
      return "review";
    case "land":
      // S3 isn't built: landing in flight, failed or skipped surfaces on the decision record.
      return last.state === "done" ? "bronze" : "review";
    case "analyze":
      return "bronze";
    case "promote":
      // S6/S7 aren't built: promotion surfaces on the studio and `/batches/{id}`.
      return "mapping";
    default:
      return "mapping";
  }
}

export type RailState = "done" | "current" | "adverse" | "pending";

/** One dot per screen, from the ledger: adverse if any of its steps failed,
 *  done if all of them are, current if it is the canonical screen, otherwise
 *  pending. Without ledger rows the old index arithmetic applies, so a
 *  pre-ledger run still gets a sensible rail. */
export function railStates(
  steps: StepProgress[],
  canonical: RunStepKey,
  adverseFallback = false,
): Record<RunStepKey, RailState> {
  const currentIndex = STEP_ORDER.indexOf(canonical);
  const byKey = new Map(steps.map((s) => [s.key, s]));
  const hasLedger = steps.some((s) => s.state !== "not_reached");
  const out = {} as Record<RunStepKey, RailState>;
  RUN_STEPS.forEach((def, index) => {
    if (!hasLedger) {
      out[def.key] =
        index < currentIndex
          ? "done"
          : index === currentIndex
            ? adverseFallback
              ? "adverse"
              : "current"
            : "pending";
      return;
    }
    const own = def.ledgerSteps.map((k) => byKey.get(k)).filter((s): s is StepProgress => !!s);
    if (own.some((s) => s.state === "failed")) out[def.key] = "adverse";
    else if (own.length > 0 && own.every((s) => s.state === "done")) out[def.key] = "done";
    else if (def.key === canonical) out[def.key] = "current";
    else out[def.key] = "pending";
  });
  return out;
}

export function runHref(uploadId: string, step: RunStepKey): string {
  return `/runs/${encodeURIComponent(uploadId)}/${step}`;
}
