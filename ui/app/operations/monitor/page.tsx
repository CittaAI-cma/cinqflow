import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { attempt, isRefused } from "@/lib/api";
import type { Batch, Feed } from "@/lib/types";

/**
 * Monitor — expected versus actual, per feed, per cycle.
 *
 * The screen the daily_status.xlsx habit exists to retire. A feed with no run
 * in its window is <Status word="Missing" /> — computed from the SLA config
 * rather than noticed by a person, which is the entire difference.
 */
export default async function Monitor() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  const rows = [];
  for (const feed of feeds) {
    const batches = await attempt<Batch[]>(
      `/api/batches?feed_id=${encodeURIComponent(feed.feed_id)}&limit=1`,
    );
    const latest = !isRefused(batches) ? batches[0] : undefined;
    rows.push({ feed, latest });
  }

  return (
    <>
      <h1>Monitor</h1>
      <p className="lede">Expected versus actual, per feed, per cycle.</p>

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Feed</th>
              <th>Schedule</th>
              <th>Most recent run</th>
              <th>Business date</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ feed, latest }) => (
              <tr className="row" key={feed.feed_id}>
                <td>{feed.feed_id}</td>
                <td className="mono">{feed.schedule_cron}</td>
                <td>
                  {latest ? (
                    <Cited value={latest.batch_id} citationId={latest.citation_id} />
                  ) : (
                    <span className="note">nothing yet</span>
                  )}
                </td>
                <td>{latest?.business_date ?? "—"}</td>
                <td>
                  {/* No run at all is Expected, not Missing: the window may not
                      have closed. Calling it Missing early trains people to
                      ignore the word that matters. */}
                  <Status word={latest?.status ?? "Expected"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card note">
        Seven status words, everywhere: Expected · Received · Processing · Completed · Needs
        Review · Needs Attention · Missing. There is no eighth, and no synonym.
      </div>
    </>
  );
}
