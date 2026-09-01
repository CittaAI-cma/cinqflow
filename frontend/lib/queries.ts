import { attempt, isRefused } from "@/lib/api";
import type { Batch, Feed } from "@/lib/types";

/**
 * Every run across every feed, fetched CONCURRENTLY.
 *
 * Home and Control Operations each ran this as a serial `for` loop — one
 * round-trip per feed, awaited in order, behind a blank screen. At 20 feeds
 * that is 21 sequential trips before anything paints, which is the difference
 * between a usable morning screen and one an operator learns to avoid.
 *
 * A refusal on ONE feed is skipped rather than thrown: an out-of-scope feed
 * must make the rest of the list smaller, never make the page fail.
 */
export async function batchesForFeeds(feeds: Feed[], limit: number): Promise<Batch[]> {
  const results = await Promise.all(
    feeds.map((feed) =>
      attempt<Batch[]>(`/api/batches?feed_id=${encodeURIComponent(feed.feed_id)}&limit=${limit}`),
    ),
  );
  return results.flatMap((found) => (isRefused(found) ? [] : found));
}

/** Ranked by downstream harm, not by time. The most expensive thing to ignore
 *  is first — a Completed run at the top of an operator's screen wastes the
 *  first ten seconds of a morning. */
export const HARM: Record<string, number> = {
  "Needs Attention": 0,
  Missing: 1,
  "Needs Review": 2,
  Processing: 3,
  Received: 4,
  Expected: 5,
  Completed: 6,
};

export function byHarm(a: Batch, b: Batch): number {
  return (HARM[a.status] ?? 9) - (HARM[b.status] ?? 9);
}

export function byRecency(a: Batch, b: Batch): number {
  return (b.started_ts ?? "").localeCompare(a.started_ts ?? "");
}
