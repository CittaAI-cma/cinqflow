"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** How many consecutive fetch failures before we stop calling it transient and
 *  tell the analyst the control plane is unreachable. Two is noise (a restart,
 *  a dropped socket); three in a row at this cadence is a real outage. */
const OFFLINE_AFTER_FAILURES = 3;

/** Backoff ceiling. A failing poll must not hammer a struggling API, but it
 *  also must recover quickly once the API is back — 15s is the compromise. */
const MAX_BACKOFF_MS = 15_000;

export interface PollState<T> {
  /** Last successful value. Survives transient failures rather than blanking. */
  value: T | null;
  /** Consecutive failures. Zero while healthy. */
  failures: number;
  /** The control plane has failed `OFFLINE_AFTER_FAILURES` times running. */
  offline: boolean;
  /** Still unsettled after `stallAfterMs`. Only ever true when that option is set. */
  stalled: boolean;
  /** Milliseconds since this poll started. Only tracked when `stallAfterMs` is set. */
  elapsedMs: number;
  /** True once `isSettled` has accepted a value. */
  settled: boolean;
  /** Force an immediate attempt, clearing backoff and the failure count. */
  retry: () => void;
}

export interface PollOptions<T> {
  enabled: boolean;
  intervalMs: number;
  isSettled: (value: T) => boolean;
  onSettle?: () => void;
  /** Fires on every successful tick, settled or not — for a caller that needs
   *  to react partway through (S1 revalidates the server page once profiling
   *  finishes, well before interpretation settles the poll). */
  onTick?: (value: T) => void;
  /** Opt in to stall detection. Unset means `stalled` is never true and no
   *  per-second ticker runs, so a caller that doesn't need it pays nothing. */
  stallAfterMs?: number;
}

/** Polls `fetcher` every `intervalMs` while `enabled`, stopping the moment
 *  `isSettled` accepts a value.
 *
 *  Four things it does beyond a bare `setTimeout` loop, each because a screen
 *  that waits must stay honest about *why* it is waiting:
 *
 *  - **Failures are reported, not swallowed.** A caller can distinguish "the
 *    worker hasn't got to it yet" from "nothing has answered in 20 seconds".
 *  - **Backoff.** Consecutive failures widen the interval up to
 *    `MAX_BACKOFF_MS` instead of hammering an API that is already struggling.
 *  - **Visibility-aware.** Polling pauses on a hidden tab (where the browser
 *    would throttle it to roughly once a minute anyway, leaving a stale screen
 *    on return) and fires immediately when the tab is shown again.
 *  - **Stall detection.** With `stallAfterMs`, a poll that never settles is
 *    surfaced as stalled rather than spinning forever — the "worker is down and
 *    the UI says Processing indefinitely" failure mode.
 *
 *  Callbacks are held in refs, so they never need to appear in `deps` and a
 *  re-render cannot leave the loop running against a stale closure. */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  opts: PollOptions<T>,
  deps: React.DependencyList,
): PollState<T> {
  const { enabled, intervalMs, stallAfterMs } = opts;

  const [value, setValue] = useState<T | null>(null);
  const [failures, setFailures] = useState(0);
  const [settled, setSettled] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [nonce, setNonce] = useState(0);

  // Everything the loop calls lives in a ref: the effect below re-runs only on
  // `deps`/`enabled`/`nonce`, so reading these directly would pin the first
  // render's closures for the life of the poll.
  const latest = useRef(opts);
  latest.current = opts;
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const retry = useCallback(() => {
    setFailures(0);
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) {
      setSettled(false);
      setElapsedMs(0);
      return;
    }

    let cancelled = false;
    let done = false;
    let consecutiveFailures = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const clear = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = (delay: number) => {
      clear();
      if (cancelled || done) return;
      // A hidden tab gets no timer at all; `onVisible` restarts the loop.
      if (typeof document !== "undefined" && document.hidden) return;
      timer = setTimeout(tick, delay);
    };

    async function tick() {
      if (cancelled || done) return;
      try {
        const next = await fetcherRef.current();
        if (cancelled || done) return;

        consecutiveFailures = 0;
        setFailures(0);
        setValue(next);
        latest.current.onTick?.(next);

        if (latest.current.isSettled(next)) {
          done = true;
          clear();
          setSettled(true);
          latest.current.onSettle?.();
          return;
        }
        schedule(intervalMs);
      } catch {
        if (cancelled || done) return;
        // The last good value stays on screen; only the failure count moves.
        consecutiveFailures += 1;
        setFailures(consecutiveFailures);
        const backoff = Math.min(intervalMs * 2 ** (consecutiveFailures - 1), MAX_BACKOFF_MS);
        schedule(backoff);
      }
    }

    function onVisible() {
      if (typeof document === "undefined" || document.hidden) return;
      // Straight to a fetch, not a scheduled one: coming back to a paused tab
      // should show current state at once, not after another full interval.
      schedule(0);
    }

    setSettled(false);
    tick();
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      clear();
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, intervalMs, nonce]);

  // Elapsed time only matters for stall detection, so the per-second ticker
  // only exists when a caller asked for it — and stops the moment we settle.
  useEffect(() => {
    if (!enabled || stallAfterMs === undefined || settled) return;
    const startedAt = Date.now();
    setElapsedMs(0);
    const id = setInterval(() => setElapsedMs(Date.now() - startedAt), 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, settled, stallAfterMs, nonce]);

  return {
    value,
    failures,
    offline: failures >= OFFLINE_AFTER_FAILURES,
    stalled: stallAfterMs !== undefined && !settled && elapsedMs > stallAfterMs,
    elapsedMs,
    settled,
    retry,
  };
}
