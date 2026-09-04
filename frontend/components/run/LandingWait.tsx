"use client";

import { useRouter } from "next/navigation";
import { getUploadProgress, type UploadProgress as ProgressPayload } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const POLL_MS = 1500;

/** Fills the gap between "approved" and a `land_bronze` run existing.
 *  `UploadProgress.batch_id` is set the instant landing starts, not once it
 *  finishes (same fact `isUploadInFlight` was extended for), so this settles
 *  within one worker tick of the approval, not once the whole landing run
 *  completes - it just needs the run to *exist* to hand off to
 *  `/runs/{id}/bronze`'s own guard. A worker is always running in every real
 *  deployment (docker compose's `worker` service, Railway's combined
 *  process), so there is nothing for the analyst to do but wait a moment -
 *  this used to be a static "run `make worker` and reload" message, which
 *  was never true outside a bare `poetry run` dev loop with no worker
 *  process at all. */
export default function LandingWait({ uploadId }: { uploadId: string }) {
  const router = useRouter();

  usePoll<ProgressPayload>(
    () => getUploadProgress(uploadId),
    {
      enabled: true,
      intervalMs: POLL_MS,
      isSettled: (next) => next.batch_id !== null,
      onSettle: () => router.refresh(),
    },
    [uploadId],
  );

  return (
    <p className="empty" style={{ marginTop: 12 }} aria-live="polite">
      Approved. Landing to Bronze is queued — this updates automatically once it starts.
    </p>
  );
}
