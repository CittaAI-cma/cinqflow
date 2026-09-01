import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import type { Feed, Mapping } from "@/lib/types";

/**
 * "Mapping & Rules" — how source columns become the canonical model, across
 * every feed. CF-V1-E6-01 already browses the CANONICAL side (`/data/
 * canonical`); this is the other half nothing browsed before: which feeds
 * have a mapping at all, how complete each one is, and which have a field
 * blocking approval right now.
 *
 * BUILT ENTIRELY FROM THE PER-FEED ROUTE, N+1, IN PARALLEL — the same shape
 * `/operations/incidents` already uses for evidence per row. No new backend
 * aggregation exists for this, and none was invented: `Mapping.mapped_count/
 * unmapped_count/blocking_count` were already computed, per feed, and simply
 * had nowhere across-feed to be read together until now.
 */
export default async function MappingBrowserPage() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) {
    return (
      <>
        <h1>Mapping & Rules</h1>
        <RefusalNotice refusal={feeds} />
      </>
    );
  }
  if (feeds.length === 0) {
    return (
      <>
        <h1>Mapping & Rules</h1>
        <EmptyState kind="recorded" what="feeds" />
      </>
    );
  }

  const mappings = await Promise.all(
    feeds.map((feed) => attempt<Mapping>(`/api/feeds/${encodeURIComponent(feed.feed_id)}/mapping`)),
  );

  return (
    <>
      <h1>Mapping & Rules</h1>
      <p className="lede">How source columns become the canonical model, across every feed.</p>

      <div className="card flush scroll">
        <table>
          <caption className="sr-only">Every feed&apos;s mapping, mapped and blocking counts</caption>
          <thead>
            <tr>
              <th scope="col">Feed</th>
              <th scope="col">Mapping state</th>
              <th scope="col">Mapped</th>
              <th scope="col">Unmapped</th>
              <th scope="col">Blocking</th>
            </tr>
          </thead>
          <tbody>
            {feeds.map((feed, index) => {
              const mapping = mappings[index];
              const has = !isRefused(mapping);
              return (
                <tr className="row" key={feed.feed_id}>
                  <td>
                    <Link className="cited" href={`/data/intake/mapping/${feed.feed_id}`}>
                      {feed.feed_id}
                    </Link>
                  </td>
                  <td>
                    {has ? <Status word={mapping.status} /> : <span className="note">no mapping yet</span>}
                  </td>
                  <td className="num mono">{has ? `${mapping.mapped_count} / ${mapping.total_count}` : "—"}</td>
                  <td className="num mono">{has ? mapping.unmapped_count : "—"}</td>
                  <td className="num mono">
                    {has && mapping.blocking_count > 0 ? (
                      <span style={{ color: "var(--st-needs-attention)" }}>{mapping.blocking_count}</span>
                    ) : has ? (
                      "0"
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
