import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * One entity's stable contract page. CF-V3-E10-02's own words: "every
 * entity a stable contract page downstream teams can link to." Keyed by
 * entity name alone in the URL — the link a Business Analyst bookmarks
 * today still resolves once the model underneath it moves to a later
 * version, because this route always reads the CURRENT PUBLISHED model,
 * never a proposal.
 *
 * Consumers are COMPUTED here, not typed by an author: every mapping that
 * targets a column, and the business-named reports that mapping declares
 * depend on it. A pending removal shows its real, computed consumers during
 * review — "lineage lists the consumers on the proposal automatically ...
 * not after the break."
 */
type Consumer = { mapping_id: string; business_consumers: string[] };
type FieldChange = { entity: string; column: string; kind: string; was: string; now: string };
type Deprecation = { change: FieldChange; consumers: Consumer[]; is_breaking: boolean };
type Column = {
  name: string;
  type: string;
  nullable: boolean;
  is_phi: boolean;
  comment: string;
};
type ContractPage = {
  entity: string;
  model_version: number;
  columns: Column[];
  consumers: Record<string, Consumer[]>;
  pending_deprecations: Deprecation[];
};

export default async function OdsContractPage({
  params,
}: {
  params: Promise<{ entity: string }>;
}) {
  const { entity } = await params;
  const found = await attempt<ContractPage>(`/api/ods-model/${encodeURIComponent(entity)}`);

  if (isRefused(found)) {
    return (
      <>
        <p className="note">
          <Link href="/data/ods-model">Canonical ODS model</Link> / {entity}
        </p>
        <h1>{entity}</h1>
        <RefusalNotice refusal={found} />
      </>
    );
  }

  const removalByColumn = new Map(
    found.pending_deprecations.map((notice) => [notice.change.column, notice]),
  );

  return (
    <>
      <p className="note">
        <Link href="/data/ods-model">Canonical ODS model</Link> / {found.entity}
      </p>
      <h1>{found.entity}</h1>
      <p className="lede">
        Model version {found.model_version} · {found.columns.length} columns · the contract
        downstream teams may build against today.
      </p>

      {found.pending_deprecations.length > 0 ? (
        <div className="card">
          <strong>
            {found.pending_deprecations.length} proposed removal
            {found.pending_deprecations.length === 1 ? "" : "s"} in review
          </strong>
          <p className="note">
            Not yet in effect — the columns below are still part of this contract. Shown now so
            an owner is notified before the change ships, not after it breaks.
          </p>
          <ul>
            {found.pending_deprecations.map((notice) => (
              <li key={notice.change.column}>
                <span className="mono">{notice.change.column || notice.change.entity}</span>
                {notice.is_breaking ? (
                  <>
                    {" "}
                    — used by {notice.consumers.map((c) => c.mapping_id).join(", ")}
                    {notice.consumers.some((c) => c.business_consumers.length > 0) ? (
                      <>
                        {" "}
                        (reports:{" "}
                        {[...new Set(notice.consumers.flatMap((c) => c.business_consumers))].join(
                          ", ",
                        )}
                        )
                      </>
                    ) : null}
                  </>
                ) : (
                  <span className="note"> — no known consumer</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Column</th>
              <th>Type</th>
              <th>Nullable</th>
              <th>Consumers</th>
            </tr>
          </thead>
          <tbody>
            {found.columns.map((column) => {
              const consumers = found.consumers[column.name] ?? [];
              const pending = removalByColumn.get(column.name);
              return (
                <tr className="row" key={column.name}>
                  <td className="mono">
                    {column.name}
                    {column.is_phi ? <span className="note"> · PHI</span> : null}
                    {pending ? <span className="note"> · proposed removal</span> : null}
                  </td>
                  <td>{column.type}</td>
                  <td>{column.nullable ? "yes" : "no"}</td>
                  <td>
                    {consumers.length === 0 ? (
                      <span className="note">none</span>
                    ) : (
                      consumers.map((c) => c.mapping_id).join(", ")
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
