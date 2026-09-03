"use client";

import { useMemo, useState } from "react";
import { SortArrows } from "@/components/icons";

export interface Column<T> {
  key: string;
  /** A node so a column can carry a control, e.g. a select-all checkbox. */
  header: React.ReactNode;
  /** Rendered beside the header, outside the sort button, so a column can
   *  carry its own control without swallowing the sort click. */
  headerAfter?: React.ReactNode;
  /** Sortable columns need `value` — it is what gets compared. */
  sortable?: boolean;
  align?: "left" | "right";
  width?: string;
  /** Comparable projection of the row for this column. */
  value?: (row: T) => string | number;
  render: (row: T) => React.ReactNode;
}

export interface SortState {
  key: string;
  dir: "asc" | "desc";
}

/** A presentational, generic table: it owns sorting (a table concern) and
 *  nothing else. Filtering belongs to whoever owns the query, so rows arrive
 *  already filtered. */
export default function DataTable<T>({
  rows,
  columns,
  rowKey,
  initialSort,
  emptyMessage = "Nothing to show.",
  variant = "plain",
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  initialSort?: SortState;
  emptyMessage?: string;
  /** "grid" rules the columns and tints the header. */
  variant?: "plain" | "grid";
}) {
  const [sort, setSort] = useState<SortState | null>(initialSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((candidate) => candidate.key === sort.key);
    if (!column?.value) return rows;
    const direction = sort.dir === "asc" ? 1 : -1;
    const project = column.value;
    return [...rows].sort((a, b) => {
      const left = project(a);
      const right = project(b);
      if (typeof left === "number" && typeof right === "number") {
        return (left - right) * direction;
      }
      return String(left).localeCompare(String(right)) * direction;
    });
  }, [rows, columns, sort]);

  function toggleSort(key: string) {
    setSort((current) =>
      current?.key === key
        ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" },
    );
  }

  return (
    <div className="dt-wrap">
      {/* `dt-ruled`, not `grid`: there is a `.grid { display: grid }` utility,
          and a table that computes to grid loses its shared column widths. */}
      <table className={`dt${variant === "grid" ? " dt-ruled" : ""}`}>
        <thead>
          <tr>
            {columns.map((column) => {
              const active = sort?.key === column.key;
              return (
                <th
                  key={column.key}
                  style={column.width ? { width: column.width } : undefined}
                  className={column.align === "right" ? "right" : undefined}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
                >
                  <span className="dt-head">
                    {column.sortable && column.value ? (
                      <button
                        type="button"
                        className={`dt-sort${active ? ` active ${sort!.dir}` : ""}`}
                        onClick={() => toggleSort(column.key)}
                      >
                        {column.header}
                        <SortArrows size={12} className="dt-sort-icon" />
                      </button>
                    ) : (
                      column.header
                    )}
                    {column.headerAfter}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="dt-empty">
                {emptyMessage}
              </td>
            </tr>
          ) : (
            sorted.map((row) => (
              <tr key={rowKey(row)}>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={column.align === "right" ? "right" : undefined}
                  >
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
