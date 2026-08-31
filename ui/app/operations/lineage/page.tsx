import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import type { Feed } from "@/lib/types";

/**
 * "Data Lineage" — where a column came from and what depends on it.
 *
 * The computation already exists (`core.impact`, the SAME reference-graph
 * traversal the approval packet and CF-V1-E3-02's "referenced everywhere"
 * view already use) — this is the picker in front of it. Feeds are the
 * entry point rather than columns, because a feed is what the reference
 * graph is actually rooted at; `/operations/lineage/[feedId]` is the same
 * `ImpactPacketCard` the proposal review console shows an approver, made
 * reachable on demand rather than only at the moment of a decision.
 */
export default async function LineageIndexPage() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) {
    return (
      <>
        <h1>Data Lineage</h1>
        <RefusalNotice refusal={feeds} />
      </>
    );
  }

  return (
    <>
      <h1>Data Lineage</h1>
      <p className="lede">Where a column came from, and what depends on it.</p>

      {feeds.length === 0 ? (
        <EmptyState kind="recorded" what="feeds" />
      ) : (
        <ul>
          {feeds.map((feed) => (
            <li key={feed.feed_id}>
              <Link className="cited" href={`/operations/lineage/${feed.feed_id}`}>
                {feed.feed_id}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
