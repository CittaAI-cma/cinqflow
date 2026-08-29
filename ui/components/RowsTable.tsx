import { EmptyState } from "@/components/ui/EmptyState";
import type { Rows } from "@/lib/types";

/**
 * A certified tool's rows, rendered generically — the SAME projection the
 * batch drawer uses. Columns come from the first row's own keys, so every
 * caller must keep its rows homogeneous (see intelligence/tools.py:
 * _get_reconciliation for why that is a real constraint, not a convenience).
 */
export function RowsTable({ result }: { result: Rows }) {
  if (result.out_of_scope) return <EmptyState kind="scope" what={result.tool} />;
  if (result.row_count === 0) return <EmptyState kind="recorded" what={result.tool} />;
  return (
    <div className="card flush scroll">
      <table>
        <caption className="sr-only">{result.tool}</caption>
        <thead>
          <tr>
            {Object.keys(result.rows[0])
              .filter((column) => column !== "citation_id")
              .map((column) => (
                <th key={column} scope="col">
                  {column.replace(/_/g, " ")}
                </th>
              ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, index) => (
            <tr className="row" key={index}>
              {Object.entries(row)
                .filter(([column]) => column !== "citation_id")
                .map(([column, value]) => (
                  <td key={column} className={typeof value === "number" ? "num mono" : undefined}>
                    {value === null || value === undefined
                      ? "—"
                      : Array.isArray(value)
                        ? value.join(", ")
                        : typeof value === "object"
                          ? JSON.stringify(value)
                          : String(value)}
                  </td>
                ))}
            </tr>
          ))}
        </tbody>
      </table>
      {result.note && (
        <p className="note" style={{ padding: "var(--s-3)" }}>
          {result.note}
        </p>
      )}
    </div>
  );
}
