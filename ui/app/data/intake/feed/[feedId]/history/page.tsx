import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * Version history and the side-by-side comparison. CF-V1-E3-04.
 *
 * "Which version was live in March?" is one click because every version is
 * still here — an edit is a new version in Draft, never an in-place change, so
 * nothing that was ever approved has been overwritten.
 *
 * The comparison defaults to the previous version against the latest. That is
 * the question nine times in ten, and a screen that demands two version numbers
 * before it shows anything is a screen people stop opening.
 *
 * Beside the version history sits the PAUSE ledger, because the two together
 * answer the question somebody actually has: which version was live, and was it
 * running?
 */
type Version = {
  object_type: string;
  object_id: string;
  version: number;
  lifecycle_state: string;
  created_by_subject: string;
  created_by_name: string;
  created_ts: string;
  approved_by_subject: string | null;
  approved_ts: string | null;
};
type Diff = {
  from_version: number;
  to_version: number;
  from_state: string;
  to_state: string;
  from_author: string;
  to_author: string;
  differences: { field_path: string; original: unknown; clone: unknown }[];
};
type SuspensionEvent = {
  action: string;
  reason: string;
  actor_subject: string;
  actor_name: string;
  occurred_ts: string;
  resumes_after: string | null;
};

function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export default async function FeedHistoryPage({
  params,
  searchParams,
}: {
  params: Promise<{ feedId: string }>;
  searchParams: Promise<{ from?: string; to?: string }>;
}) {
  const { feedId } = await params;
  const { from, to } = await searchParams;
  const id = encodeURIComponent(feedId);

  const versions = await attempt<Version[]>(`/api/objects/feed/${id}/history`);
  if (isRefused(versions)) return <RefusalNotice refusal={versions} />;

  const diffQuery = new URLSearchParams();
  if (from) diffQuery.set("from_version", from);
  if (to) diffQuery.set("to_version", to);
  const suffix = diffQuery.toString() ? `?${diffQuery.toString()}` : "";
  const diff = await attempt<Diff>(`/api/objects/feed/${id}/diff${suffix}`);
  const pauses = await attempt<SuspensionEvent[]>(`/api/feeds/${id}/suspensions`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> / history
      </p>
      <h1>Version history</h1>
      <p className="lede">
        Every version this feed has ever had. An edit is a new version in Draft — nothing that was
        approved has been overwritten, so any past state can still be read.
      </p>

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>State</th>
              <th>Authored by</th>
              <th>Authored</th>
              <th>Approved by</th>
              <th>Compare</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((version, index) => (
              <tr className="row" key={version.version}>
                <td>v{version.version}</td>
                <td>{version.lifecycle_state}</td>
                <td>{version.created_by_name || version.created_by_subject}</td>
                <td className="note">{version.created_ts}</td>
                <td>{version.approved_by_subject ?? <span className="note">nobody</span>}</td>
                <td>
                  {index + 1 < versions.length ? (
                    <Link
                      href={`?from=${versions[index + 1].version}&to=${version.version}`}
                    >
                      against v{versions[index + 1].version}
                    </Link>
                  ) : (
                    <span className="note">first version</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>What changed</h2>
      {isRefused(diff) ? (
        <div className="card note">
          This feed has only one version, so there is nothing to compare it with yet.
        </div>
      ) : (
        <>
          <p className="note">
            v{diff.from_version} ({diff.from_state}, {diff.from_author}) → v{diff.to_version} (
            {diff.to_state}, {diff.to_author})
          </p>
          {diff.differences.length === 0 ? (
            <div className="card note">
              Nothing in the body changed between these two versions — the difference is the
              lifecycle state, not the configuration.
            </div>
          ) : (
            <div className="card scroll">
              <table>
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>v{diff.from_version}</th>
                    <th>v{diff.to_version}</th>
                  </tr>
                </thead>
                <tbody>
                  {diff.differences.map((d) => (
                    <tr className="row" key={d.field_path}>
                      <td className="mono">{d.field_path}</td>
                      <td className="mono">{show(d.original)}</td>
                      <td className="mono">{show(d.clone)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <h2>Pauses</h2>
      {isRefused(pauses) || pauses.length === 0 ? (
        <div className="card note">This feed has never been paused.</div>
      ) : (
        <div className="card scroll">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Who</th>
                <th>Why</th>
                <th>Until</th>
              </tr>
            </thead>
            <tbody>
              {pauses.map((event) => (
                <tr className="row" key={`${event.occurred_ts}-${event.action}`}>
                  <td className="note">{event.occurred_ts}</td>
                  <td>{event.action}</td>
                  <td>{event.actor_name || event.actor_subject}</td>
                  <td>{event.reason || <span className="note">—</span>}</td>
                  <td className="note">{event.resumes_after ?? "no end date"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
