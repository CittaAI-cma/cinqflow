import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { attempt, isRefused } from "@/lib/api";
import type { Batch, Feed } from "@/lib/types";

/**
 * Control Operations — every run, and the one drawer.
 *
 * Depth is a drawer, never an IA branch: a row opens
 * /operations/control/batch/<id>?panel=<tab>, which IS the row's citation_id.
 * There is exactly one depth level in Wave 0.
 */
export default async function ControlOperations() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  const batches: Batch[] = [];
  for (const feed of feeds) {
    const found = await attempt<Batch[]>(
      `/api/batches?feed_id=${encodeURIComponent(feed.feed_id)}&limit=50`,
    );
    if (!isRefused(found)) batches.push(...found);
  }

  return (
    <>
      <h1>Control Operations</h1>
      <p className="lede">
        Stages, inputs, errors, quarantine and reconciliation — one drawer, one level deep.
      </p>

      {batches.length === 0 ? (
        <div className="card note">No runs recorded yet.</div>
      ) : (
        <div className="card scroll">
          <table>
            <thead>
              <tr>
                <th>Batch</th>
                <th>Feed</th>
                <th>Business date</th>
                <th>Started</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => (
                <tr className="row" key={batch.batch_id}>
                  <td>
                    <Cited value={batch.batch_id} citationId={batch.citation_id} />
                  </td>
                  <td>{batch.feed_id}</td>
                  <td>{batch.business_date}</td>
                  <td className="mono">{batch.started_ts?.slice(0, 19).replace("T", " ")}</td>
                  <td>
                    <Status word={batch.status} />
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
