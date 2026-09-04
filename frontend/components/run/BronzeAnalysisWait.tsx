"use client";

import { useRouter } from "next/navigation";
import WaitNotice from "@/components/ui/WaitNotice";
import { getProposal } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const POLL_MS = 1500;

/** Bronze analysis profiles the batch in code and then makes one LLM call, so
 *  30-60s is ordinary and two minutes is not. */
const STALL_AFTER_MS = 120_000;

/** S4's guard only opens once the upload is `landed`, so by the time this
 *  renders, landing itself is done - the one thing still in flight is Bronze
 *  analysis (the AI proposal), queued the instant landing completes (see
 *  `workers/land_bronze.py`). Same gap `LandingWait`/`PreviewPanel` already
 *  closed, and the "analysis" leg of `BatchProcessing`'s three-way poll on
 *  `/batches/{id}` - this is that same poll, scoped to the one thing S4 can
 *  still be waiting on.
 *
 *  This is the longest unattended wait in the flow (a real model call), which
 *  makes it the one most in need of an honest stalled state: an LLM timeout
 *  and a dead worker look identical from here without one. */
export default function BronzeAnalysisWait({ batchId }: { batchId: string }) {
  const router = useRouter();

  const poll = usePoll(
    () => getProposal(batchId),
    {
      enabled: true,
      intervalMs: POLL_MS,
      isSettled: (proposal) => proposal !== null,
      onSettle: () => router.refresh(),
      stallAfterMs: STALL_AFTER_MS,
    },
    [batchId],
  );

  return (
    <WaitNotice
      poll={poll}
      what="the AI mapping proposal"
      waiting="No mapping proposal yet. Bronze analysis is queued — this updates automatically once it's ready."
      stalled={
        <>
          Bronze itself landed and is safe. The <span className="mono">bronze.analyze</span> job
          either has no worker or its model call is failing — the batch detail page shows whether
          the run errored. A proposal is advisory anyway: mapping can be started by hand without it.
        </>
      }
    />
  );
}
