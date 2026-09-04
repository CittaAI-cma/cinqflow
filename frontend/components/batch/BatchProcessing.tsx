"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import StatusWord from "@/components/StatusWord";
import WaitNotice from "@/components/ui/WaitNotice";
import { getBatchProgress, getProposal, type MappingProposal, type Run } from "@/lib/api";
import { isRunInFlight } from "@/lib/statusWords";
import { usePoll } from "@/lib/usePoll";

const POLL_MS = 1500;

/** Landing is a bulk insert a worker starts within a tick; analysis makes a
 *  model call; promotion executes a mapping in code. Different shapes, so
 *  different thresholds for "this stopped being slow and started being stuck". */
const LAND_STALL_MS = 45_000;
const ANALYSIS_STALL_MS = 120_000;
const PROMOTION_STALL_MS = 90_000;

/** Batch detail is otherwise a one-shot server render (`page.tsx`): correct
 *  once `land_bronze` and, if it ran, `promote_silver` have both settled and
 *  the AI mapping proposal exists, but until then the page just says "reload"
 *  — the one dead-time gap Stage 1's `RunProcessing` already solved for
 *  uploads. This is that same pattern for a batch: poll `/batches/{id}/progress`
 *  (already contract-compatible, previously unwired to any screen) while
 *  landing or promotion is in flight, and poll the proposal itself while
 *  Bronze analysis (queued the moment landing completes, see
 *  `workers/land_bronze.py`) hasn't produced one yet. Each leg calls
 *  `router.refresh()` the moment it settles, so the surrounding server-rendered
 *  sections (Bronze profile, proposal, Silver counts) appear without a manual
 *  reload — never a second API contract, just the one `templates.md` already
 *  defines.
 */
export default function BatchProcessing({
  batchId,
  initialLandRun,
  initialPromotionRun,
  hasProposal,
}: {
  batchId: string;
  initialLandRun: Run | null;
  initialPromotionRun: Run | null;
  hasProposal: boolean;
}) {
  const router = useRouter();
  const [landSettled, setLandSettled] = useState(!isRunInFlight(initialLandRun));

  const landPoll = usePoll<Run | null>(
    () => getBatchProgress(batchId, "land_bronze"),
    {
      enabled: isRunInFlight(initialLandRun),
      intervalMs: POLL_MS,
      isSettled: (run) => !isRunInFlight(run),
      onSettle: () => {
        setLandSettled(true);
        router.refresh();
      },
      stallAfterMs: LAND_STALL_MS,
    },
    [batchId],
  );

  // Bronze analysis (the AI proposal) is queued the instant landing completes
  // (see `land_bronze.py`), so it only makes sense to wait on it once landing
  // itself is done — polling for a proposal that cannot exist yet would just
  // burn requests.
  const proposalPoll = usePoll<MappingProposal | null>(
    () => getProposal(batchId),
    {
      enabled: landSettled && !hasProposal,
      intervalMs: POLL_MS,
      isSettled: (p) => p !== null,
      onSettle: () => router.refresh(),
      stallAfterMs: ANALYSIS_STALL_MS,
    },
    [batchId, landSettled],
  );

  const promotionPoll = usePoll<Run | null>(
    () => getBatchProgress(batchId, "promote_silver"),
    {
      enabled: isRunInFlight(initialPromotionRun),
      intervalMs: POLL_MS,
      isSettled: (run) => !isRunInFlight(run),
      onSettle: () => router.refresh(),
      stallAfterMs: PROMOTION_STALL_MS,
    },
    [batchId],
  );

  const landRun = landPoll.value;
  const proposal = proposalPoll.value;
  const promotionRun = promotionPoll.value;

  const landingInFlight = isRunInFlight(landRun ?? initialLandRun);
  const analysisInFlight = landSettled && !hasProposal && !proposal;
  const promotionInFlight = isRunInFlight(promotionRun ?? initialPromotionRun);

  // Only one notice, even if two legs go bad together: they share one cause
  // (the API, or the worker) and stacking three identical alerts on the same
  // screen tells the analyst nothing the first one didn't.
  const legs = [
    landingInFlight && {
      poll: landPoll,
      what: "landing to Bronze",
      stalled: (
        <>
          The batch exists and the original file is preserved. No worker has picked up{" "}
          <span className="mono">bronze.land</span>, so nothing has been written yet.
        </>
      ),
    },
    analysisInFlight && {
      poll: proposalPoll,
      what: "the AI mapping proposal",
      stalled: (
        <>
          Bronze landed and is safe. Only the advisory proposal is outstanding — mapping can be
          started by hand without it.
        </>
      ),
    },
    promotionInFlight && {
      poll: promotionPoll,
      what: "promotion to Silver Raw",
      stalled: (
        <>
          The approved mapping is frozen and Bronze is untouched. Promotion is re-runnable, so
          nothing is lost by this waiting.
        </>
      ),
    },
  ].filter(Boolean) as { poll: typeof landPoll; what: string; stalled: React.ReactNode }[];

  const unhealthy = legs.find((leg) => leg.poll.offline || leg.poll.stalled) ?? null;

  if (!landingInFlight && !analysisInFlight && !promotionInFlight) return null;

  return (
    <div className="card run-timeline" style={{ marginTop: 14 }} aria-live="polite">
      <span className="panel-label">Processing</span>
      {unhealthy ? (
        <WaitNotice
          poll={unhealthy.poll}
          what={unhealthy.what}
          waiting=""
          stalled={unhealthy.stalled}
        />
      ) : null}
      <ul className="run-timeline-list">
        {landingInFlight ? (
          <li>
            <StatusWord word="Processing" /> Landing to Bronze
          </li>
        ) : null}
        {analysisInFlight ? (
          <li>
            <StatusWord word="Processing" /> AI mapping proposal — reasoning over the Bronze
            profile
          </li>
        ) : null}
        {promotionInFlight ? (
          <li>
            <StatusWord word="Processing" /> Promoting to Silver Raw — executed in code, no model
            involved
          </li>
        ) : null}
      </ul>
    </div>
  );
}
