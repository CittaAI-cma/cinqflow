import Link from "next/link";
import { redirect } from "next/navigation";
import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { attempt, isRefused, token } from "@/lib/api";
import type { Batch, Feed, Principal } from "@/lib/types";

/**
 * Home, shaped by persona.
 *
 * The merge rule: persona shapes the home and the RANKING; it never shapes the
 * vocabulary or the depth. An Engineer sees what needs them, ranked by
 * downstream harm. A Read-Only analyst sees what arrived and what completed.
 * Both see the same seven words and open the same drawer.
 */
export default async function Home() {
  if (!(await token())) redirect("/signin");

  const me = await attempt<Principal>("/api/me");
  if (isRefused(me)) return <RefusalNotice refusal={me} />;
  if (!me.has_access) redirect("/no-access");

  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  const engineer = me.roles.includes("engineer");
  const batches: Batch[] = [];
  for (const feed of feeds) {
    const found = await attempt<Batch[]>(
      `/api/batches?feed_id=${encodeURIComponent(feed.feed_id)}&limit=10`,
    );
    if (!isRefused(found)) batches.push(...found);
  }

  // Ranked by downstream harm, not by time. A completed batch at the top of an
  // engineer's screen is a screen that wastes the first ten seconds of a
  // morning.
  const harm: Record<string, number> = {
    "Needs Attention": 0,
    Missing: 1,
    "Needs Review": 2,
    Processing: 3,
    Received: 4,
    Expected: 5,
    Completed: 6,
  };
  const ranked = [...batches].sort((a, b) =>
    engineer
      ? (harm[a.status] ?? 9) - (harm[b.status] ?? 9)
      : (b.started_ts ?? "").localeCompare(a.started_ts ?? ""),
  );

  return (
    <>
      <h1>{engineer ? "What needs you" : "What arrived"}</h1>
      <p className="lede">
        {engineer
          ? "Ranked by downstream harm. The most expensive thing to ignore is first."
          : "Most recent first. Every figure opens the row it came from."}
      </p>

      <div className="grid">
        <div className="card">
          <div className="note">Feeds published</div>
          <div className="big">{feeds.filter((f) => f.lifecycle_state === "published").length}</div>
        </div>
        <div className="card">
          <div className="note">Runs in view</div>
          <div className="big">{batches.length}</div>
        </div>
        <div className="card">
          <div className="note">Needing attention</div>
          <div className="big">
            {batches.filter((b) => b.status === "Needs Attention" || b.status === "Missing").length}
          </div>
        </div>
      </div>

      <h2>Runs</h2>
      {ranked.length === 0 ? (
        <div className="card note">
          No runs recorded yet. Run <span className="mono">cinqflow simulate</span> to place a
          file — the demo places no files by hand.
        </div>
      ) : (
        <div className="card scroll">
          <table>
            <thead>
              <tr>
                <th>Batch</th>
                <th>Feed</th>
                <th>Business date</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((batch) => (
                <tr className="row" key={batch.batch_id}>
                  <td>
                    <Cited value={batch.batch_id} citationId={batch.citation_id} />
                  </td>
                  <td>{batch.feed_id}</td>
                  <td>{batch.business_date}</td>
                  <td>
                    <Status word={batch.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Feeds</h2>
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Feed</th>
              <th>Domain</th>
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
    </>
  );
}
