"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import StatusWord from "@/components/StatusWord";
import { getBatchProgress, getProposal, type MappingProposal, type Run } from "@/lib/api";
import { isRunInFlight } from "@/lib/statusWords";
import { usePoll } from "@/lib/usePoll";

const POLL_MS = 1500;

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

  const landRun = usePoll<Run | null>(
    () => getBatchProgress(batchId, "land_bronze"),
    {
      enabled: isRunInFlight(initialLandRun),
      intervalMs: POLL_MS,
      isSettled: (run) => !isRunInFlight(run),
      onSettle: () => {
        setLandSettled(true);
        router.refresh();
      },
    },
    [batchId],
  );

  // Bronze analysis (the AI proposal) is queued the instant landing completes
  // (see `land_bronze.py`), so it only makes sense to wait on it once landing
  // itself is done — polling for a proposal that cannot exist yet would just
  // burn requests.
  const proposal = usePoll<MappingProposal | null>(
    () => getProposal(batchId),
    {
      enabled: landSettled && !hasProposal,
      intervalMs: POLL_MS,
      isSettled: (p) => p !== null,
      onSettle: () => router.refresh(),
    },
    [batchId, landSettled],
  );

  const promotionRun = usePoll<Run | null>(
    () => getBatchProgress(batchId, "promote_silver"),
    {
      enabled: isRunInFlight(initialPromotionRun),
      intervalMs: POLL_MS,
      isSettled: (run) => !isRunInFlight(run),
      onSettle: () => router.refresh(),
    },
    [batchId],
  );

  const landingInFlight = isRunInFlight(landRun ?? initialLandRun);
  const analysisInFlight = landSettled && !hasProposal && !proposal;
  const promotionInFlight = isRunInFlight(promotionRun ?? initialPromotionRun);

  if (!landingInFlight && !analysisInFlight && !promotionInFlight) return null;

  return (
    <div className="card run-timeline" style={{ marginTop: 14 }} aria-live="polite">
      <span className="panel-label">Processing</span>
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
