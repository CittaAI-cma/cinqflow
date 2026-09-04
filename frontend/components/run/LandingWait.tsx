"use client";

import { useRouter } from "next/navigation";
import WaitNotice from "@/components/ui/WaitNotice";
import { getUploadProgress, type UploadProgress as ProgressPayload } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const POLL_MS = 1500;

/** A worker that is up claims this within a tick or two of the approval, so
 *  anything past this is not "slow", it is "nothing is consuming the queue". */
const STALL_AFTER_MS = 45_000;

/** Fills the gap between "approved" and a `land_bronze` run existing.
 *  `UploadProgress.batch_id` is set the instant landing starts, not once it
 *  finishes (same fact `isUploadInFlight` was extended for), so this settles
 *  within one worker tick of the approval, not once the whole landing run
 *  completes - it just needs the run to *exist* to hand off to
 *  `/runs/{id}/bronze`'s own guard.
 *
 *  A worker is running in every real deployment (docker compose's `worker`
 *  service, Railway's combined process) - but "should be" is not "is", and
 *  when it isn't, the approval sits in the queue untouched. `WaitNotice` is
 *  what makes that visible instead of leaving this line on screen forever. */
export default function LandingWait({ uploadId }: { uploadId: string }) {
  const router = useRouter();

  const poll = usePoll<ProgressPayload>(
    () => getUploadProgress(uploadId),
    {
      enabled: true,
      intervalMs: POLL_MS,
      isSettled: (next) => next.batch_id !== null,
      onSettle: () => router.refresh(),
      stallAfterMs: STALL_AFTER_MS,
    },
    [uploadId],
  );

  return (
    <div style={{ marginTop: 12 }}>
      <WaitNotice
        poll={poll}
        what="landing to Bronze"
        waiting="Approved. Landing to Bronze is queued — this updates automatically once it starts."
        stalled={
          <>
            The approval is recorded and the file is safe, but no worker has claimed the{" "}
            <span className="mono">bronze.land</span> job. Nothing lands until one does — this
            page will pick it up on its own the moment that happens.
          </>
        }
      />
    </div>
  );
}
