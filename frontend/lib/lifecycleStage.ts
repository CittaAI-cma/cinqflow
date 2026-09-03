import type { Upload, UploadDetail } from "@/lib/api";

/** How far the medallion pipeline has actually carried an object.
 *
 *  This is a different axis from the seven status words in `statusWords.ts`:
 *  a status says how an object is doing, a stage says where it is. The group
 *  header in the design reads "Stage : Dq Applied", which is a position, not a
 *  health word — hence its own vocabulary.
 *
 *  Every stage below is only ever shown because control-plane state says so.
 *  "Dq Applied" sits after promotion, not after landing: `land_bronze` appends
 *  every parsed row to Bronze unchanged — no typing, no rules, no rejection —
 *  and it is `promote_silver` that applies the approved mapping's rules and
 *  writes the quarantine (engine/runner.py). A landed object has been preserved,
 *  not screened. */
export type LifecycleStage =
  | "Received"
  | "Profiled"
  | "Interpreted"
  | "Rejected"
  | "Landing"
  | "Landed"
  | "Promoting"
  | "Dq Applied"
  | "Failed";

/** Ordered by pipeline position. A group's stage is the furthest of its
 *  objects, so one object waiting at G1 does not drag the group backwards. */
export const LIFECYCLE_ORDER: LifecycleStage[] = [
  "Failed",
  "Rejected",
  "Received",
  "Profiled",
  "Interpreted",
  "Landing",
  "Landed",
  "Promoting",
  "Dq Applied",
];

/** Stages that mean something went wrong, so the header can say so in red. */
export function isStageAdverse(stage: LifecycleStage): boolean {
  return stage === "Failed" || stage === "Rejected";
}

/** Promotion is invisible on the upload row — the upload stays `landed` forever
 *  — so it can only be read off the batch's runs. A completed promotion is the
 *  first point at which the mapping's rules have run against every row. */
function promotionStage(detail: UploadDetail | null): LifecycleStage | null {
  const runs = detail?.runs.filter((run) => run.kind === "promote_silver") ?? [];
  if (runs.some((run) => run.state === "completed")) return "Dq Applied";
  if (runs.some((run) => run.state === "in_progress" || run.state === "received")) {
    return "Promoting";
  }
  return null;
}

export function stageOf(upload: Upload, detail: UploadDetail | null = null): LifecycleStage {
  switch (upload.status) {
    case "received":
    case "profiling":
      return "Received";
    case "profiled":
    case "interpreting":
      return "Profiled";
    case "interpreted":
      return "Interpreted";
    case "rejected":
      return "Rejected";
    case "approved":
    case "landing":
      return "Landing";
    case "landed":
      return promotionStage(detail) ?? "Landed";
    case "profile_failed":
    case "interpret_failed":
    case "land_failed":
      return "Failed";
    default:
      return "Received";
  }
}

/** `details` is positional against `objects`; a missing entry just costs the
 *  promoted/dq distinction for that object. */
export function groupStage(
  objects: Upload[],
  details: (UploadDetail | null)[] = [],
): LifecycleStage | null {
  let best: LifecycleStage | null = null;
  objects.forEach((object, index) => {
    const stage = stageOf(object, details[index] ?? null);
    if (!best || LIFECYCLE_ORDER.indexOf(stage) > LIFECYCLE_ORDER.indexOf(best)) best = stage;
  });
  return best;
}
