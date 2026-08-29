import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import { batchesForFeeds, byHarm } from "@/lib/queries";
import type { Feed } from "@/lib/types";

/**
 * Control Operations — every run, and the one drawer.
 *
 * Depth is a drawer, never an IA branch: a row opens
 * /operations/control/batch/<id>?panel=<tab>, which IS the row's citation_id.
 * Clicking it overlays the drawer (app/@drawer intercepts) and keeps this list
 * behind it; pasting the same URL cold renders the full page. One depth level,
 * one address.
 */
export default async function ControlOperations() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  const batches = (await batchesForFeeds(feeds, 50)).sort(byHarm);

  return (
    <>
      <h1>Control Operations</h1>
      <p className="lede">
        Stages, inputs, errors, quarantine and reconciliation — one drawer, one level deep.
      </p>

      {batches.length === 0 ? (
        <EmptyState kind="recorded" what="runs" />
      ) : (
        <div className="card flush scroll">
          <table>
            <caption className="sr-only">Every run, ranked by downstream harm</caption>
            <thead>
              <tr>
                <th scope="col">Batch</th>
                <th scope="col">Feed</th>
                <th scope="col">Business date</th>
                <th scope="col">Started</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => (
                <tr className="row" key={batch.batch_id}>
                  <td>
                    <Cited value={batch.batch_id} citationId={batch.citation_id} />
                  </td>
                  <td>{batch.feed_id}</td>
                  <td className="num mono">{batch.business_date}</td>
                  <td className="num mono">
                    {batch.started_ts?.slice(0, 19).replace("T", " ") ?? "—"}
                  </td>
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
