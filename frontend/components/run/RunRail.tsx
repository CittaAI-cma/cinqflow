import Link from "next/link";
import { RUN_STEPS, runHref, type RunStepKey } from "@/lib/runStep";

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
 *  Only "processing" and "review" are real routes in this phase — every other
 *  dot renders inert with a stated reason, same pattern as the rest of this
 *  console. */
export default function RunRail({
  uploadId,
  step,
  adverse,
}: {
  uploadId: string;
  step: RunStepKey;
  /** The run stopped at this step because of a rejection or a failure, not
   *  because it's still working — the current dot reads as an alert, not progress. */
  adverse?: boolean;
}) {
  const order = RUN_STEPS.map((s) => s.key);
  const currentIndex = order.indexOf(step);

  return (
    <nav className="run-rail" aria-label="Run progress">
      {BANDS.map((band) => (
        <div key={band.key} className="run-rail-band">
          <span className="run-rail-band-label">{band.label}</span>
          <div className="run-rail-steps">
            {RUN_STEPS.filter((def) => def.band === band.key).map((def) => {
              const index = order.indexOf(def.key);
              const state =
                index < currentIndex
                  ? "done"
                  : index === currentIndex
                    ? adverse
                      ? "adverse"
                      : "current"
                    : "pending";
              const reachable = def.builtInThisPhase && index <= currentIndex;
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
