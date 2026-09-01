import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { Tag, type Tone } from "@/components/Tag";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import type { Feed, ReviewQueue, RulePolicySet } from "@/lib/types";

/**
 * "Data Quality" — which rules exist, where they run, and what stands
 * between them and production, across every feed. CF-V1-E7-05's own
 * "rules running in production, visibly" is answered here at the level
 * this platform can actually measure: CONFIGURED severity and layer per
 * rule (`core.rules.policy`), and how many candidates still need a person
 * (`core.rules.review`) — not run-time firing counts, which nothing in this
 * codebase computes yet (a genuinely separate, larger piece of work: a
 * rule's pass/fail history would need to be read back from every batch's
 * `error_log`/`quarantine_records`, aggregated over time — not invented
 * here as a number nobody could trace to a query).
 */
export default async function QualityBrowserPage() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) {
    return (
      <>
        <h1>Data Quality</h1>
        <RefusalNotice refusal={feeds} />
      </>
    );
  }
  if (feeds.length === 0) {
    return (
      <>
        <h1>Data Quality</h1>
        <EmptyState kind="recorded" what="feeds" />
      </>
    );
  }

  const [policySets, reviewQueues] = await Promise.all([
    Promise.all(
      feeds.map((feed) =>
        attempt<RulePolicySet>(`/api/feeds/${encodeURIComponent(feed.feed_id)}/rule-policies`),
      ),
    ),
    Promise.all(
      feeds.map((feed) =>
        attempt<ReviewQueue>(`/api/feeds/${encodeURIComponent(feed.feed_id)}/rule-reviews`),
      ),
    ),
  ]);

  const totalRules = policySets.reduce((sum, set) => sum + (isRefused(set) ? 0 : set.policies.length), 0);
  const totalNeedsReview = reviewQueues.reduce((sum, q) => sum + (isRefused(q) ? 0 : q.open_count), 0);

  return (
    <>
      <h1>Data Quality</h1>
      <p className="lede">
        {totalRules} configured rule{totalRules === 1 ? "" : "s"} across {feeds.length} feed
        {feeds.length === 1 ? "" : "s"} · {totalNeedsReview} candidate
        {totalNeedsReview === 1 ? "" : "s"} still need{totalNeedsReview === 1 ? "s" : ""} a person.
      </p>

      <div className="card flush scroll">
        <table>
          <caption className="sr-only">Rule configuration and review status per feed</caption>
          <thead>
            <tr>
              <th scope="col">Feed</th>
              <th scope="col">Rules</th>
              <th scope="col">Consequences</th>
              <th scope="col">Approvable</th>
              <th scope="col">Needs review</th>
            </tr>
          </thead>
          <tbody>
            {feeds.map((feed, index) => {
              const set = policySets[index];
              const queue = reviewQueues[index];
              const has = !isRefused(set);
              const consequences = has
                ? Array.from(new Set(set.policies.map((p) => p.on_failure)))
                : [];
              return (
                <tr className="row" key={feed.feed_id}>
                  <td>
                    <Link className="cited" href={`/data/intake/rules/${feed.feed_id}`}>
                      {feed.feed_id}
                    </Link>
                  </td>
                  <td className="num mono">{has ? set.policies.length : "—"}</td>
                  <td>
                    {consequences.length === 0 ? (
                      <span className="note">none configured</span>
                    ) : (
                      consequences.map((c) => (
                        <Tag key={c} tone={consequenceTone(c)}>
                          {c.replace(/_/g, " ")}
                        </Tag>
                      ))
                    )}
                  </td>
                  <td>{has ? (set.is_approvable ? "Yes" : "No") : "—"}</td>
                  <td className="num mono">
                    {!isRefused(queue) && queue.open_count > 0 ? (
                      <span style={{ color: "var(--st-needs-attention)" }}>{queue.open_count}</span>
                    ) : (
                      "0"
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

function consequenceTone(consequence: string): Tone {
  switch (consequence) {
    case "reject":
    case "stop_pipeline":
      return "bad";
    case "quarantine":
    case "manual_review":
      return "pending";
    default:
      return "neutral";
  }
}
