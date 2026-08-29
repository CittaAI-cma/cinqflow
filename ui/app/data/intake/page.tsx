import Link from "next/link";
import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { attempt, isRefused } from "@/lib/api";
import type { Feed } from "@/lib/types";

/**
 * Data Intake — the feed registry, and what should arrive.
 *
 * Six fields of versioned metadata. Everything the engine does comes from
 * here, which is why the version is a CITATION rather than a label: clicking it
 * opens the exact version a run was executed against.
 */
export default async function DataIntake() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  return (
    <>
      <h1>Data Intake</h1>
      <p className="lede">
        What feeds exist, what should arrive, what arrived, and what is Missing.
      </p>

      <p className="note">
        <Link href="/data/intake/proposals">Agent proposals awaiting review</Link> — everything an
        agent has suggested and nobody has decided. Nothing there is in effect.
      </p>

      {feeds.length === 0 ? (
        <div className="card note">
          No feeds registered. A feed is six fields — domain, format, landing folder,
          file-name pattern, schedule, owner — and its pattern is validated against a real
          sample name before it can be saved.
        </div>
      ) : (
        <div className="card scroll">
          <table>
            <thead>
              <tr>
                <th>Feed</th>
                <th>Domain</th>
                <th>Source</th>
                <th>Format</th>
                <th>Pattern</th>
                <th>Schedule</th>
                <th>Version</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {feeds.map((feed) => (
                <tr className="row" key={feed.feed_id}>
                  <td>
                    <Link className="cited" href={feed.route}>
                      {feed.feed_id}
                    </Link>
                  </td>
                  <td>{feed.domain}</td>
                  <td>{feed.source_system}</td>
                  <td>{feed.file_format}</td>
                  <td className="mono">{feed.file_pattern}</td>
                  <td className="mono">{feed.schedule_cron}</td>
                  <td>
                    <Cited value={`v${feed.version}`} citationId={feed.citation_id} />
                  </td>
                  <td>
                    <Status word={feed.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
