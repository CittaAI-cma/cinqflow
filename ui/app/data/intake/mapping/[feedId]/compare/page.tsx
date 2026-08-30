import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { MappingDiff } from "@/lib/types";

/**
 * Two mapping versions, compared. CF-V1-E6-04.
 *
 * THE LOSSES ARE THE PAGE. Everything else is context. A mapping change that
 * drops a line does not break anything — the batch runs, the counts reconcile,
 * the ledger balances, and one canonical column arrives empty on every record
 * from that day. Somebody notices in March that a report has been wrong since
 * November.
 *
 * So a field that stops being populated is stated in full sentences at the top,
 * before the ordinary changes, and the page says out loud that nothing will
 * fail. An approver who skims must still come away with the one fact that
 * matters.
 */
export default async function MappingComparePage({
  params,
  searchParams,
}: {
  params: Promise<{ feedId: string }>;
  searchParams: Promise<{ from?: string; to?: string }>;
}) {
  const { feedId } = await params;
  const { from, to } = await searchParams;
  const query = new URLSearchParams();
  if (from) query.set("from_version", from);
  if (to) query.set("to_version", to);
  const suffix = query.toString() ? `?${query}` : "";

  const diff = await attempt<MappingDiff>(
    `/api/feeds/${encodeURIComponent(feedId)}/mapping/diff${suffix}`,
  );

  const crumbs = (
    <p className="note">
      <Link href="/data/intake">Data Intake</Link> /{" "}
      <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> /{" "}
      <Link href={`/data/intake/mapping/${feedId}`}>mapping</Link> / compare
    </p>
  );

  if (isRefused(diff)) {
    return (
      <>
        {crumbs}
        <h1>Compare mapping versions</h1>
        <RefusalNotice refusal={diff} />
      </>
    );
  }

  const losses = diff.lines.filter((line) => line.loses_its_source);
  const others = diff.lines.filter((line) => !line.loses_its_source);

  return (
    <>
      {crumbs}
      <h1>
        {diff.feed_id} mapping · v{diff.from_version} → v{diff.to_version}
      </h1>
      <p className="lede">{diff.summary}</p>

      {losses.length > 0 ? (
        <div className="card">
          <strong>
            {losses.length} field{losses.length === 1 ? "" : "s"} stop being populated
          </strong>
          <p className="note">
            Nothing will fail. The batch will run, the row counts will reconcile and the ledger
            will balance — these columns will simply be empty from the next delivery onward.
            {diff.from_published
              ? " Approving this change requires naming each of them."
              : " The earlier version was never published, so nothing live is affected."}
          </p>
          <ul>
            {losses.map((line) => (
              <li key={line.address}>{line.explanation}</li>
            ))}
          </ul>
        </div>
      ) : diff.lines.length > 0 ? (
        <div className="card note">
          <strong>No field loses its source</strong>
          <p>Every target field that was populated before is still populated.</p>
        </div>
      ) : null}

      {others.length > 0 ? (
        <>
          <h2>Other changes</h2>
          <div className="card scroll">
            <table>
              <thead>
                <tr>
                  <th>Target field</th>
                  <th>Change</th>
                  <th>Before</th>
                  <th>After</th>
                </tr>
              </thead>
              <tbody>
                {others.map((line) => (
                  <tr className="row" key={line.address}>
                    <td className="mono">{line.address}</td>
                    <td>{line.change}</td>
                    <td className="note">{line.before || "—"}</td>
                    <td>{line.after || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {diff.lines.length === 0 ? (
        <div className="card note">
          These two versions are identical. Reordering the lines is not a change — a mapping is
          read by target field, never by position.
        </div>
      ) : null}
    </>
  );
}
