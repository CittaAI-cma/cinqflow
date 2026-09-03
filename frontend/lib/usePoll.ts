"use client";

import { useEffect, useState } from "react";

/** Generalised from the polling loop `UploadProgress.tsx` first wrote for S3's
 *  upload progress. Polls `fetcher` every `intervalMs` while `enabled`, stops
 *  the moment `isSettled` says the latest value is terminal, and keeps the
 *  last good value across a transient fetch failure (a 502, the API
 *  restarting) instead of clearing the screen. */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  opts: {
    enabled: boolean;
    intervalMs: number;
    isSettled: (value: T) => boolean;
    onSettle?: () => void;
    /** Fires on every successful tick, settled or not — for a caller that
     *  needs to react partway through (S1 revalidates the server page once
     *  profiling finishes, well before interpretation settles the poll). */
    onTick?: (value: T) => void;
  },
  deps: React.DependencyList,
): T | null {
  const [value, setValue] = useState<T | null>(null);
  const { enabled, intervalMs, isSettled, onSettle, onTick } = opts;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let settled = false;

    async function tick() {
      if (cancelled || settled) return;
      try {
        const next = await fetcher();
        if (cancelled) return;
        setValue(next);
        onTick?.(next);
        if (isSettled(next)) {
          settled = true;
          onSettle?.();
          return;
        }
      } catch {
        // Transient failure: the next tick retries, and the last good value stays on screen.
      }
      if (!cancelled && !settled) {
        setTimeout(tick, intervalMs);
      }
    }

    tick();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return value;
}
