import Link from "next/link";
import { EmptyState } from "@/components/ui/EmptyState";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { LayerDetail } from "@/lib/types";

/**
 * One layer: its tables, their shapes, and the evidence behind its gate.
 *
 * THE COLUMN TABLE SHOWS TWO TYPES, side by side and never merged. The
 * contract declares `timestamp_utc`; Postgres reports `timestamptz`. That is a
 * match. A Databricks plane would report `TIMESTAMP` against the same
 * `timestamp_utc`, and that is also a match — which is the entire reason the
 * portable type exists. A screen showing only the engine's answer could not
 * show a drift; one showing only the contract's could not show that the plane
 * never got the migration. The conformance kit compares exactly these two
 * columns for its verdict, so a reader here is looking at the same evidence
 * the gate looks at.
 *
 * AN UNBUILT LAYER RENDERS, and answers 200 rather than 404. It is a real
 * position on the spine with a real reason for being empty, and 404 would make
 * the screen unable to tell "this layer is not in this architecture" from
 * "this layer is not built yet" — the second is true and the first is not.
 */
export default async function LayerPage({ params }: { params: Promise<{ layer: string }> }) {
  const { layer } = await params;
  const detail = await attempt<LayerDetail>(`/api/layers/${encodeURIComponent(layer)}`);
  if (isRefused(detail)) return <RefusalNotice refusal={detail} />;
  const { layer: spec, tables, quarantine, reconciliation } = detail;

  return (
    <>
      <p className="note">
        <Link className="cited" href="/data/layers">
          Medallion Layers
        </Link>{" "}
        / {spec.label}
      </p>
      <h1>{spec.label}</h1>
      <p className="lede">{spec.purpose}</p>

      <div className="card">
        <dl className="kv">
          <dt>Position</dt>
          <dd>
            {spec.entry_gate
              ? `Entered through gate ${spec.entry_gate}`
              : "The arrival zone — nothing is promoted into it"}
          </dd>
          <dt>Schema on the plane</dt>
          <dd className="mono">{spec.schema_name || "none"}</dd>
          <dt>Status</dt>
          <dd>
            {spec.status === "built"
              ? `Built — ${spec.row_count === null ? "no plane fitted" : `${spec.row_count.toLocaleString()} rows`}`
              : spec.status === "provisioned_empty"
                ? "Provisioned and deliberately empty"
                : `Not built — Wave ${spec.wave}`}
          </dd>
        </dl>
      </div>

      {spec.status !== "built" && (
        <>
          <h2>Why this layer is empty</h2>
          <div className="card">
            <p>{spec.absence_reason}</p>
            <p className="note">
              This is not a stub and nothing is hidden behind it. The layer appears on the spine
              so the map of the platform stays honest — a screen that omitted it would read as a
              finished architecture.
            </p>
          </div>
        </>
      )}

      <h2>Tables</h2>
      {tables.length === 0 ? (
        <EmptyState kind="wave" what={`${spec.label}'s tables`} />
      ) : (
        tables.map((table) => (
          <section key={table.name} className="card flush">
            <table>
              <caption>
                <strong className="mono">
                  {table.schema_name}.{table.name}
                </strong>
                {" — "}
                {table.row_count === null ? (
                  <span className="uncited">
                    declared by the contract, absent from the plane
                  </span>
                ) : (
                  <>
                    {table.row_count.toLocaleString()} {table.row_count === 1 ? "row" : "rows"}
                  </>
                )}
                {table.append_only && <span className="tag">append-only</span>}
                {table.phi_column_count > 0 && (
                  <span className="tag">
                    {table.phi_column_count} masked {table.phi_column_count === 1 ? "column" : "columns"}
                  </span>
                )}
                {table.rows_route && (
                  <>
                    {" · "}
                    <Link className="cited" href={table.rows_route}>
                      Browse rows
                    </Link>
                  </>
                )}
                <span className="note">{table.comment}</span>
              </caption>
              <thead>
                <tr>
                  <th scope="col">Column</th>
                  <th scope="col">Declared</th>
                  <th scope="col">On the plane</th>
                  <th scope="col">Null</th>
                  <th scope="col">PHI</th>
                </tr>
              </thead>
              <tbody>
                {table.columns.map((column) => {
                  const key = table.primary_key.includes(column.name);
                  return (
                    <tr className="row" key={column.name}>
                      <th scope="row" className="mono">
                        {column.name}
                        {key && <span className="tag">pk</span>}
                      </th>
                      <td className="mono">{column.declared_type || "—"}</td>
                      <td className="mono">
                        {column.present_on_plane ? (
                          column.engine_type
                        ) : (
                          // A column the contract declares and the plane
                          // lacks. Marked with the defect glyph rather than
                          // left blank: blank reads as "no value", and this
                          // is a missing migration.
                          <span className="uncited">absent</span>
                        )}
                      </td>
                      <td>{column.nullable ? "yes" : "no"}</td>
                      <td>{column.is_phi ? <span className="tag">masked</span> : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        ))
      )}

      {quarantine.length > 0 && (
        <>
          <h2>Why rows did not cross {spec.entry_gate}</h2>
          <div className="card flush scroll">
            <table>
              <caption className="note">
                Grouped by the rule that excluded them. Quarantine means stored, visible, counted
                and re-processable — never deleted. The rows themselves are not in this response,
                not even masked: this answers what is wrong and how much of it.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col">Reason</th>
                  <th scope="col">Rows</th>
                </tr>
              </thead>
              <tbody>
                {quarantine.map((reason) => (
                  <tr className="row" key={`${reason.rule_id}-${reason.reason}`}>
                    <th scope="row" className="mono">
                      {reason.rule_id}
                    </th>
                    <td>{reason.reason}</td>
                    <td className="num">{reason.row_count.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {reconciliation.length > 0 && (
        <>
          <h2>Reconciliation</h2>
          <div className="card flush scroll">
            <table>
              <caption className="note">
                <strong>Balanced</strong> is the ledger&rsquo;s recorded verdict, not recomputed
                here — a screen that re-derives it can disagree with the row an auditor reads.
                <strong> Unattributed</strong> is derived, and shown beside it: every drop must be
                attributed, so a green tick with unexplained rows behind it has to be visible
                rather than trusted.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Batch</th>
                  <th scope="col">Feed</th>
                  <th scope="col">In</th>
                  <th scope="col">Out</th>
                  <th scope="col">Quarantined</th>
                  <th scope="col">Attributed</th>
                  <th scope="col">Unattributed</th>
                  <th scope="col">Balanced</th>
                </tr>
              </thead>
              <tbody>
                {reconciliation.map((line) => (
                  <tr className="row" key={`${line.batch_id}-${line.recorded_ts}`}>
                    <th scope="row" className="mono">
                      <Link className="cited" href={line.route}>
                        {line.batch_id}
                      </Link>
                    </th>
                    <td className="mono">{line.feed_id}</td>
                    <td className="num">{line.records_in.toLocaleString()}</td>
                    <td className="num">{line.records_out.toLocaleString()}</td>
                    <td className="num">{line.quarantined.toLocaleString()}</td>
                    <td className="num">{line.attributed_drops.toLocaleString()}</td>
                    <td className="num">
                      {line.unattributed === 0 ? (
                        "0"
                      ) : (
                        <span className="uncited">{line.unattributed.toLocaleString()}</span>
                      )}
                    </td>
                    <td>{line.balanced ? "yes" : <span className="uncited">no</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
