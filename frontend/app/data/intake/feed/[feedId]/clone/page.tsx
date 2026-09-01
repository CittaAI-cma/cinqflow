import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Feed } from "@/lib/types";

/**
 * "Clone a similar feed." CF-V1-E3-03.
 *
 * The screen's job is to make the RIGHT original obvious, and the platform
 * already knows which one it is: `/similar` ranks candidates from the
 * registry's own structured fields — same payer, same domain, same file-name
 * convention — and hands back the reasons with the score.
 *
 * Showing the reasons rather than only the ranking is the point. A ranked list
 * a BA cannot check is a ranked list they scroll past; "same source, same
 * domain, same convention" makes the top result obviously right or obviously
 * wrong at a glance.
 */
type Similar = {
  feed_id: string;
  score: number;
  reasons: string[];
  lifecycle_state: string;
  domain: string;
  source_system: string;
};

export default async function CloneFeedPage({
  params,
}: {
  params: Promise<{ feedId: string }>;
}) {
  const { feedId } = await params;
  const feed = await attempt<Feed>(`/api/feeds/${encodeURIComponent(feedId)}`);
  if (isRefused(feed)) return <RefusalNotice refusal={feed} />;

  const similar = await attempt<Similar[]>(`/api/feeds/${encodeURIComponent(feedId)}/similar`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${feed.feed_id}`}>{feed.feed_id}</Link> / clone
      </p>
      <h1>Clone this feed</h1>
      <p className="lede">
        A clone inherits the configuration — the contract, the mappings and the rules — and none
        of the approval. It arrives as a first draft that has never been reviewed, whatever this
        feed had earned.
      </p>

      <div className="card">
        <strong>What a clone carries</strong>
        <ul>
          <li>the six engine fields, and the operational envelope around them</li>
          <li>the data contract, the mappings and the data-quality rules</li>
        </ul>
        <strong>What it does not</strong>
        <ul>
          <li>the version number, the lifecycle state and the approver&apos;s name</li>
          <li>the audit trail</li>
          <li>
            the profiling evidence — a clone must profile its own sample, or the stale-evidence
            gate would pass a feed that profiled nothing
          </li>
        </ul>
      </div>

      {isRefused(similar) ? (
        <RefusalNotice refusal={similar} />
      ) : similar.length === 0 ? (
        <div className="card note">
          No other feed in the registry has enough in common with this one to be worth cloning
          from.
        </div>
      ) : (
        <>
          <h2>Feeds worth cloning from</h2>
          <div className="card scroll">
            <table>
              <thead>
                <tr>
                  <th>Feed</th>
                  <th>Domain</th>
                  <th>Source</th>
                  <th>State</th>
                  <th>Why it matches</th>
                </tr>
              </thead>
              <tbody>
                {similar.map((match) => (
                  <tr className="row" key={match.feed_id}>
                    <td>
                      <Link href={`/data/intake/feed/${match.feed_id}`}>{match.feed_id}</Link>
                    </td>
                    <td>{match.domain}</td>
                    <td>{match.source_system}</td>
                    <td>{match.lifecycle_state}</td>
                    <td className="note">{match.reasons.join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
