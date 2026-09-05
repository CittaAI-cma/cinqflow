import { cache } from "react";
import { getUploadProgress, type StepProgress, type UploadStatus } from "@/lib/api";
import { canonicalStep, canonicalStepFromSteps, type RunStepKey } from "@/lib/runStep";

/** Server-side companions to `lib/runStep.ts` for the `app/runs/**` pages.
 *
 *  `loadRunSteps` is `cache()`-wrapped for the same reason `getUpload` is: the
 *  run layout (rail) and the step page it wraps both need the ledger for one
 *  render, and should cost one request, not two. An unreachable API yields an
 *  empty list rather than an error - the pages then fall back to the upload's
 *  status (`canonicalStep`), which is what they did before the ledger existed.
 *  Server Components only: client pollers call `getUploadProgress` directly. */
export const loadRunSteps = cache(async function loadRunSteps(
  uploadId: string,
): Promise<StepProgress[]> {
  try {
    return (await getUploadProgress(uploadId)).steps ?? [];
  } catch {
    return [];
  }
});

/** The ledger's answer when it has one, the status's otherwise. */
export function resolveCanonical(steps: StepProgress[], status: UploadStatus): RunStepKey {
  return canonicalStepFromSteps(steps) ?? canonicalStep(status);
}
