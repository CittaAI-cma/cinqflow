"use client";

import type { ReactNode } from "react";
import type { PollState } from "@/lib/usePoll";

function formatElapsed(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** The one surface every waiting screen shares, so "we are waiting" always
 *  reads the same and never lies.
 *
 *  A poll has three honest states and this renders all three. Before it, each
 *  screen rendered only the first — which meant an unreachable API and a dead
 *  worker both presented as a cheerful "this updates automatically", forever.
 *
 *  - **offline** — the control plane stopped answering. Nothing is coming;
 *    say so and offer a retry rather than spinning.
 *  - **stalled** — the API is fine and the work simply has not happened.
 *    Almost always a worker that is down or wedged, which the analyst cannot
 *    see from here and must not have to guess at.
 *  - **waiting** — the ordinary case, with elapsed time once a caller has
 *    opted into `stallAfterMs`, because a number that climbs reads as working
 *    where a static sentence reads as stuck. */
export default function WaitNotice({
  poll,
  waiting,
  stalled,
  what = "this step",
}: {
  poll: PollState<unknown>;
  /** The ordinary "still going" line. */
  waiting: ReactNode;
  /** What a stall most likely means here. Falls back to generic worker advice. */
  stalled?: ReactNode;
  /** Named in the offline/stalled copy, e.g. "landing to Bronze". */
  what?: string;
}) {
  if (poll.offline) {
    return (
      <div className="wait-notice" aria-live="assertive">
        <p className="alert error">
          <b>Can&apos;t reach the control plane.</b> {poll.failures} attempts in a row have
          failed, so the status of {what} is unknown — it may well have finished. Nothing was
          lost: this screen reads state, it doesn&apos;t hold it.
        </p>
        <button type="button" className="btn-outline" onClick={poll.retry}>
          Try again
        </button>
      </div>
    );
  }

  if (poll.stalled) {
    return (
      <div className="wait-notice" aria-live="polite">
        <p className="alert warn">
          <b>Still waiting after {formatElapsed(poll.elapsedMs)}.</b>{" "}
          {stalled ?? (
            <>
              The API is answering, so {what} was queued but nothing has picked it up. That
              usually means the worker process is not running or is wedged on an earlier job.
            </>
          )}
        </p>
        <button type="button" className="btn-outline" onClick={poll.retry}>
          Check again now
        </button>
      </div>
    );
  }

  // Callers that only mount this for the unhealthy states pass `waiting=""`.
  // Rendering an empty paragraph with a stray elapsed counter would be worse
  // than rendering nothing, so say nothing.
  if (!waiting) return null;

  return (
    <p className="empty wait-notice-waiting" aria-live="polite">
      {waiting}
      {poll.elapsedMs >= 5000 ? (
        <span className="meta mono wait-notice-elapsed">{formatElapsed(poll.elapsedMs)}</span>
      ) : null}
    </p>
  );
}
