import Link from "next/link";
import type { StepProgress } from "@/lib/api";
import { RUN_STEPS, railStates, runHref, type RunStepKey } from "@/lib/runStep";

const BANDS: { key: "landing" | "bronze" | "silver"; label: string }[] = [
  { key: "landing", label: "Landing" },
  { key: "bronze", label: "Bronze" },
  { key: "silver", label: "Silver Raw" },
];

/** The run's own stepper — seven dots across three medallion bands. Distinct
 *  from `GroupStageTabs` on purpose: that stepper describes the feed's
 *  configuration surfaces (durable, revisited constantly); this one describes
 *  one file's journey (linear, closed once). They must not look alike, or an
 *  analyst who has learned one will misread the other.
 *
 *  Each dot's state comes from the step ledger (`steps`, via `railStates`):
 *  done when every ledger step behind that screen is done, adverse when one
 *  failed, current for the canonical screen. Without ledger rows (a run from
 *  before the migration) the rail falls back to position relative to the
 *  canonical step, as it always did. Only built screens are links. */
export default function RunRail({
  uploadId,
  step,
  adverse,
  steps = [],
}: {
  uploadId: string;
  step: RunStepKey;
  /** Fallback only, for a run without ledger rows: the current dot reads as
   *  an alert (rejection or failure) rather than progress. */
  adverse?: boolean;
  /** `UploadProgress.steps` - the ledger's view of this run. */
  steps?: StepProgress[];
}) {
  const order = RUN_STEPS.map((s) => s.key);
  const currentIndex = order.indexOf(step);
  const states = railStates(steps, step, adverse);

  return (
    <nav className="run-rail" aria-label="Run progress">
      {BANDS.map((band) => (
        <div key={band.key} className="run-rail-band">
          <span className="run-rail-band-label">{band.label}</span>
          <div className="run-rail-steps">
            {RUN_STEPS.filter((def) => def.band === band.key).map((def) => {
              const index = order.indexOf(def.key);
              const state = states[def.key];
              // A screen is reachable once the run has been there: at or behind
              // the canonical step, or with a finished (or failed) step behind it.
              const reachable =
                def.builtInThisPhase &&
                (index <= currentIndex || state === "done" || state === "adverse");
              const content = (
                <>
                  <span
                    className={`run-step-dot ${state}${def.gate ? " gate" : ""}`}
                    aria-hidden="true"
                  />
                  <span className="run-step-label">{def.label}</span>
                </>
              );
              return reachable ? (
                <Link
                  key={def.key}
                  href={runHref(uploadId, def.key)}
                  className={`run-step ${state}`}
                  aria-current={index === currentIndex ? "step" : undefined}
                >
                  {content}
                </Link>
              ) : (
                <span
                  key={def.key}
                  className={`run-step ${state} disabled`}
                  aria-disabled="true"
                  title={
                    def.builtInThisPhase
                      ? "This run hasn't reached this step yet"
                      : "Not built in this phase — see docs/blueprints/forward-flow-adoption.md §8"
                  }
                >
                  {content}
                </span>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}
