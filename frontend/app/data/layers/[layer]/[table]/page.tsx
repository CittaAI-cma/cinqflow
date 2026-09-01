import Link from "next/link";
import { EmptyState } from "@/components/ui/EmptyState";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { LayerRows } from "@/lib/types";

/** The page size. Small on purpose — this is a window onto a layer, not an
 *  export tool. Bulk extraction is a DELIVERY: it goes through
 *  `core/delivery`, and it leaves a row saying who took what. */
const PAGE = 25;

/**
 * A page of rows from one layer, masked before it left the server.
 *
 * WHAT MASKING IS AND IS NOT HERE. Every column the schema contract flags
 * `is_phi` renders as bullets, and the bullets carry no shape — not the
 * length, not an initial, not the year of a date. The first version kept an
 * initial and a length hint because it read better ("J••• D•••••"), and that
 * version let a reader tell members apart and re-identify a known one from a
 * roster they already had. Legibility of a value nobody is entitled to see is
 * not a feature.
 *
 * The unmasked value is never in the response. There is no `original` field
 * and no client-side toggle, so no amount of browser code can reveal it — the
 * masking happens in the adapter as the row is read, and what reaches this
 * component is the only version that exists above the database.
 *
 * BRONZE IS THE INTERESTING CASE. Its whole source record lives in one flagged
 * JSON column, so masking it whole would hide the most useful thing on the
 * screen. The KEYS survive and every value is replaced: the keys are the
 * source system's own column names — which the mapping screens already
 * publish, and which are what an engineer opening Bronze is actually looking
 * for — while the values are the member.
 */
export default async function LayerRowsPage({
  params,
  searchParams,
}: {
  params: Promise<{ layer: string; table: string }>;
  searchParams: Promise<{ batch?: string; page?: string }>;
}) {
  const { layer, table } = await params;
  const { batch = "", page = "1" } = await searchParams;
  const current = Math.max(1, Number.parseInt(page, 10) || 1);
  const offset = (current - 1) * PAGE;

  const query = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (batch) query.set("batch_id", batch);
  const rows = await attempt<LayerRows>(
    `/api/layers/${encodeURIComponent(layer)}/tables/${encodeURIComponent(table)}/rows?${query}`,
  );
  // A 409 here is not a bug and not a refusal to apologise for: the layer is
  // real, the request was well-formed, and the SCHEMA is what is absent. The
  // detail carries the reason, so rendering it as a refusal shows the reason.
  if (isRefused(rows)) return <RefusalNotice refusal={rows} />;

  const lastPage = Math.max(1, Math.ceil(rows.total_rows / PAGE));
  const shown = rows.rows.length;
  const first = rows.total_rows === 0 ? 0 : offset + 1;

  return (
    <>
      <p className="note">
        <Link className="cited" href="/data/layers">
          Medallion Layers
        </Link>{" "}
        /{" "}
        <Link className="cited" href={`/data/layers/${layer}`}>
          {layer}
        </Link>{" "}
        / {rows.table}
      </p>
      <h1 className="mono">
        {rows.schema_name}.{rows.table}
      </h1>
      <p className="lede">
        {rows.total_rows === 0
          ? "On the plane, and empty."
          : `Rows ${first}–${first + shown - 1} of ${rows.total_rows.toLocaleString()}.`}
        {rows.batch_id && (
          <>
            {" "}
            Filtered to batch <span className="mono">{rows.batch_id}</span>.
          </>
        )}
      </p>

      {rows.masked_columns.length > 0 && (
        <div className="card">
          <p className="note">
            <strong>
              {rows.masked_columns.length} of {rows.columns.length} columns are masked
            </strong>{" "}
            — {rows.masked_columns.map((c) => (
              <span className="mono" key={c}>
                {c}{" "}
              </span>
            ))}
            — because the schema contract flags them <span className="mono">is_phi</span>. Every
            viewer sees the same bullets, including a steward: masking is not a permission tier,
            and the unmasked value never leaves the server. An unmask ceremony with its own
            approval and audit row is CF-V4-E14-04.
          </p>
        </div>
      )}

      {rows.total_rows === 0 ? (
        <EmptyState kind="recorded" what={`${rows.schema_name}.${rows.table}`} />
      ) : (
        <div className="card flush scroll">
          <table>
            <caption className="note">
              Column order is the CONTRACT&rsquo;s, not the engine&rsquo;s — identifiers first,
              audit columns last, the same on every plane. Ordered by primary key, ascending:
              a non-unique sort would let page 2 overlap page 1 silently, which is how a reader
              concludes a batch has duplicates it does not have.
            </caption>
            <thead>
              <tr>
                {rows.columns.map((column) => (
                  <th scope="col" key={column}>
                    {column}
                    {rows.masked_columns.includes(column) && (
                      <span className="tag">masked</span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.rows.map((row, index) => (
                <tr className="row" key={index}>
                  {rows.columns.map((column) => {
                    const cell = row[column];
                    if (!cell) return <td key={column}>—</td>;
                    if (cell.value === null)
                      // NULL is not masked even in a flagged column: "absent"
                      // is not protected information, and hiding it would make
                      // a completeness screen unable to show that a required
                      // identifier was missing.
                      return (
                        <td className="note" key={column}>
                          null
                        </td>
                      );
                    return (
                      <td className="mono" key={column} data-masked={cell.masked || undefined}>
                        {cell.masked ? (
                          <span title={cell.reason} aria-label={`masked — ${cell.reason}`}>
                            {cell.value}
                          </span>
                        ) : (
                          cell.value
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {lastPage > 1 && (
        <nav className="tabs" aria-label="Pages">
          {current > 1 && (
            <Link
              href={`/data/layers/${layer}/${table}?page=${current - 1}${batch ? `&batch=${batch}` : ""}`}
            >
              Previous
            </Link>
          )}
          <span className="note">
            Page {current} of {lastPage}
          </span>
          {current < lastPage && (
            <Link
              href={`/data/layers/${layer}/${table}?page=${current + 1}${batch ? `&batch=${batch}` : ""}`}
            >
              Next
            </Link>
          )}
        </nav>
      )}
    </>
  );
}
