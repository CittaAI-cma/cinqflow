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
export default async function DataIntake({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; domain?: string; state?: string; not_ready?: string }>;
}) {
  // CF-V1-E3-03. The list screen and the search screen are the same screen —
  // a search that requires a term cannot be the default view.
  const { q = "", domain = "", state = "", not_ready = "" } = await searchParams;
  const query = new URLSearchParams();
  if (q) query.set("q", q);
  if (domain) query.set("domain", domain);
  if (state) query.set("state", state);
  if (not_ready) query.set("not_ready", "true");
  const suffix = query.toString() ? `?${query.toString()}` : "";

  const feeds = await attempt<Feed[]>(`/api/feeds${suffix}`);
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  return (
    <>
      <h1>Data Intake</h1>
      <p className="lede">
        What feeds exist, what should arrive, what arrived, and what is Missing.
      </p>

      {/* CF-V1-E3-05. The wizard's first step is "Upload sample", and for as
          long as the only way to reach it was through a feed's own page, the
          screen named after intake offered no way to take anything IN. */}
      <p className="inline action-row">
        <Link className="action" href="/data/intake/deliver">
          Deliver a file
        </Link>
        <span className="note">
          Upload what a payer sent. It lands through the same controls as an SFTP poller&rsquo;s
          fetch, and the platform tells you what it decided.
        </span>
      </p>

      <p className="inline action-row">
        <Link className="action" href="/data/intake/new">
          Register a new feed
        </Link>
        <span className="note">
          A feed is six fields — domain, format, landing folder, file-name pattern, schedule — plus
          a real sample name the pattern is checked against before it can be saved.
        </span>
      </p>

      <p className="note">
        <Link href="/data/canonical">The canonical model</Link> — what feeds map to ·{" "}
        <Link href="/data/intake/sources">Sources</Link> — who sends us data, and who to ring ·{" "}
        <Link href="/data/intake/proposals">Agent proposals awaiting review</Link> — everything an
        agent has suggested and nobody has decided. Nothing there is in effect.
      </p>

      <form className="card" method="get">
        <label htmlFor="q">Search the registry</label>{" "}
        <input
          id="q"
          name="q"
          type="search"
          defaultValue={q}
          placeholder="feed, payer, domain, owner"
        />{" "}
        <button type="submit">Search</button>
        <p className="note">
          Free text reaches the feed&apos;s id, its domain, its payer, its file-name pattern and
          its owners — so &ldquo;which feeds is Sam on the hook for&rdquo; is one query.{" "}
          <Link href="/data/intake?not_ready=true">Show only feeds that cannot be activated</Link>.
        </p>
      </form>

      {feeds.length === 0 ? (
        <div className="card note">
          {q || domain || state || not_ready ? (
            <>
              Nothing matches that. <Link href="/data/intake">Clear the filters</Link>.
            </>
          ) : (
            <>
              No feeds registered. <Link href="/data/intake/new">Register the first one</Link> —
              six fields, and a real sample name its pattern is validated against before it can be
              saved.
            </>
          )}
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
                <th>Deliver</th>
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
                  <td>
                    {/* The feed's own delivery step, one click from the list.
                        Named per row rather than as a bare "Upload", because a
                        row action with no subject is unreadable to a screen
                        reader hearing it out of context. */}
                    <Link className="cited" href={`/data/intake/feed/${feed.feed_id}/deliver`}>
                      Upload<span className="sr-only"> a file to {feed.feed_id}</span>
                    </Link>
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
