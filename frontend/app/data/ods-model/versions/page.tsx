import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * Version history and the consumer-readable changelog. CF-V3-E10-02.
 *
 * "Show 'what changed and why' between any two model versions in terms a
 * consumer understands." The comparison is never a raw body diff — added,
 * removed and retyped columns, named — and "why" is the reviewer's and
 * approver's own recorded rationale for the target version, read back from
 * the audit trail rather than a field invented for this screen.
 */
type Version = {
  version: number;
  lifecycle_state: string;
  created_by_subject: string;
  created_by_name: string;
  created_ts: string;
  approved_by_subject: string | null;
  approved_ts: string | null;
  is_current: boolean;
};
type FieldChange = { entity: string; column: string; kind: string; was: string; now: string };
type Changelog = {
  from_version: number;
  to_version: number;
  rationale: string;
  added: FieldChange[];
  removed: FieldChange[];
  retyped: FieldChange[];
};

function changeRow(change: FieldChange) {
  return (
    <tr className="row" key={`${change.entity}.${change.column}-${change.kind}`}>
      <td className="mono">
        {change.entity}
        {change.column !== "*" ? `.${change.column}` : ""}
      </td>
      <td className="note">{change.kind}</td>
      <td className="mono">{change.was || <span className="note">—</span>}</td>
      <td className="mono">{change.now || <span className="note">—</span>}</td>
    </tr>
  );
}

export default async function OdsModelVersionsPage({
  searchParams,
}: {
  searchParams: Promise<{ from?: string; to?: string }>;
}) {
  const { from, to } = await searchParams;

  const versions = await attempt<Version[]>("/api/ods-model/versions");
  if (isRefused(versions)) return <RefusalNotice refusal={versions} />;

  const diffQuery = new URLSearchParams();
  if (from) diffQuery.set("from_version", from);
  if (to) diffQuery.set("to_version", to);
  const suffix = diffQuery.toString() ? `?${diffQuery.toString()}` : "";
  const changelog = await attempt<Changelog>(`/api/ods-model/versions/diff${suffix}`);

  return (
    <>
      <p className="note">
        <Link href="/data/ods-model">Canonical ODS model</Link> / versions
      </p>
      <h1>Version history</h1>
      <p className="lede">
        Every version this model has ever had — Draft, In Review, Approved, Published and
        Retired alike. Only the one marked &ldquo;current&rdquo; is what a downstream team may
        build against today.
      </p>

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>State</th>
              <th>Authored by</th>
              <th>Approved by</th>
              <th>Compare</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((version, index) => (
              <tr className="row" key={version.version}>
                <td>
                  v{version.version}
                  {version.is_current ? <span className="note"> · current</span> : null}
                </td>
                <td>{version.lifecycle_state}</td>
                <td>{version.created_by_name || version.created_by_subject}</td>
                <td>{version.approved_by_subject ?? <span className="note">nobody yet</span>}</td>
                <td>
                  {index + 1 < versions.length ? (
                    <Link href={`?from=${versions[index + 1].version}&to=${version.version}`}>
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

      <h2>What changed, and why</h2>
      {isRefused(changelog) ? (
        <div className="card note">
          This model has only one version, so there is nothing to compare it with yet.
        </div>
      ) : (
        <>
          <p className="note">
            v{changelog.from_version} → v{changelog.to_version}
          </p>
          {changelog.rationale ? (
            <div className="card">
              <strong>Why</strong>
              <p className="note">{changelog.rationale}</p>
            </div>
          ) : null}
          {changelog.added.length === 0 &&
          changelog.removed.length === 0 &&
          changelog.retyped.length === 0 ? (
            <div className="card note">No entity or column changed between these two versions.</div>
          ) : (
            <div className="card scroll">
              <table>
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Change</th>
                    <th>Was</th>
                    <th>Now</th>
                  </tr>
                </thead>
                <tbody>
                  {changelog.added.map(changeRow)}
                  {changelog.removed.map(changeRow)}
                  {changelog.retyped.map(changeRow)}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}
