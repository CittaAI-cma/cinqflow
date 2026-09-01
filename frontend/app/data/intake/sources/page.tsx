import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { attempt, isRefused } from "@/lib/api";
import type { Source } from "@/lib/types";

/**
 * The source registry — who sends us data, and who to ring. CF-V1-E3-02.
 *
 * A source is the ORGANISATION; a feed is one thing it sends. The distinction
 * earns its own screen because the questions differ: "which of Fidelis's feeds
 * are late this morning" cannot be answered by a registry where the payer's
 * name is a string repeated on nine rows with three spellings, which is
 * exactly what the incumbent had.
 *
 * The feed count is COMPUTED from the feeds that name each source, never a
 * maintained field. A registry whose "used by" column is hand-kept is a
 * registry whose "used by" column is wrong.
 */
export default async function SourcesPage() {
  const sources = await attempt<Source[]>("/api/sources");
  if (isRefused(sources)) {
    return (
      <>
        <p className="note">
          <Link href="/data/intake">Data Intake</Link> / sources
        </p>
        <h1>Sources</h1>
        <RefusalNotice refusal={sources} />
      </>
    );
  }

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / sources
      </p>
      <h1>Sources</h1>
      <p className="lede">
        Every organisation that sends or receives data, what they send, and the person here who
        owns the relationship.
      </p>

      {sources.length === 0 ? (
        <div className="card note">
          No sources registered. A source is the payer, provider or vendor — the thing a feed
          belongs to, and the thing somebody rings when a delivery is wrong.
        </div>
      ) : (
        <div className="card scroll">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Kind</th>
                <th>Lines of business</th>
                <th>States</th>
                <th>Relationship owner</th>
                <th>Feeds</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => {
                const owner = source.owners.find((o) => o.role === "business");
                return (
                  <tr className="row" key={source.source_id}>
                    <td>
                      {source.name}
                      <br />
                      <span className="note mono">{source.source_id}</span>
                    </td>
                    <td>{source.kind}</td>
                    <td>{source.line_of_business.join(", ") || "—"}</td>
                    <td>{source.states.join(", ") || "—"}</td>
                    <td>
                      {owner ? (
                        owner.display_name
                      ) : (
                        <span className="note">nobody named yet</span>
                      )}
                    </td>
                    <td>
                      {source.feed_ids.length === 0 ? (
                        <span className="note">none</span>
                      ) : (
                        source.feed_ids.map((feedId, index) => (
                          <span key={feedId}>
                            {index > 0 ? ", " : ""}
                            <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link>
                          </span>
                        ))
                      )}
                    </td>
                    <td>
                      <Status word={source.status} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
