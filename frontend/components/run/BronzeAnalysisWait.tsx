"use client";

import { useRouter } from "next/navigation";
import { getProposal } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const POLL_MS = 1500;

/** S4's guard only opens once the upload is `landed`, so by the time this
 *  renders, landing itself is done - the one thing still in flight is Bronze
 *  analysis (the AI proposal), queued the instant landing completes (see
 *  `workers/land_bronze.py`). Same gap `LandingWait`/`PreviewPanel` already
 *  closed, and the "analysis" leg of `BatchProcessing`'s three-way poll on
 *  `/batches/{id}` - this is that same poll, scoped to the one thing S4 can
 *  still be waiting on. */
export default function BronzeAnalysisWait({ batchId }: { batchId: string }) {
  const router = useRouter();

  usePoll(
    () => getProposal(batchId),
    {
      enabled: true,
      intervalMs: POLL_MS,
      isSettled: (proposal) => proposal !== null,
      onSettle: () => router.refresh(),
    },
    [batchId],
  );

  return (
    <p className="empty" aria-live="polite">
      No mapping proposal yet. Bronze analysis is queued — this updates automatically once it's
      ready.
    </p>
  );
}
