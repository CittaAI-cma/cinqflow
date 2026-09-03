"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, SortArrows } from "@/components/icons";

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
 *  already filtered.
 *
 *  Sorting is controlled-optional. Uncontrolled (`initialSort`) is right when
 *  the table receives every row it represents. A **paginating** caller must
 *  control it (`sort` + `onSortChange`) and sort before slicing — otherwise
 *  the table only ever orders the current page, and "sort by newest" silently
 *  means "order these ten arbitrary rows", which is worse than no sort at all
 *  because it looks like it worked.
 */
export default function DataTable<T>({
  rows,
  columns,
  rowKey,
  initialSort,
  sort: controlledSort,
  onSortChange,
  emptyMessage = "Nothing to show.",
  variant = "plain",
  expandable,
  onRowActivate,
  rowLabel,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  initialSort?: SortState;
  /** Controlled sort. Pass with `onSortChange` when the caller paginates. */
  sort?: SortState | null;
  onSortChange?: (next: SortState) => void;
  emptyMessage?: string;
  /** "grid" rules the columns and tints the header. */
  variant?: "plain" | "grid";
  /** When given, each row gets a disclosure control and this content beneath.
   *  Return `null` for a row that has nothing further to show. */
  expandable?: (row: T) => React.ReactNode;
  /** Enter/Space on a focused row. Rows only become focusable when set, so a
   *  table with no row-level action adds no empty tab stops. */
  onRowActivate?: (row: T) => void;
  /** Accessible name for a focusable row; required reading for `onRowActivate`. */
  rowLabel?: (row: T) => string;
}) {
  const [uncontrolledSort, setUncontrolledSort] = useState<SortState | null>(initialSort ?? null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const isControlled = onSortChange !== undefined;
  const sort = isControlled ? (controlledSort ?? null) : uncontrolledSort;

  const sorted = useMemo(() => {
    // A controlled caller has already sorted the full set; re-sorting the page
    // it handed us would be a no-op at best and wrong at worst.
    if (isControlled || !sort) return rows;
    const column = columns.find((candidate) => candidate.key === sort.key);
    if (!column?.value) return rows;
    return sortRows(rows, column.value, sort.dir);
  }, [rows, columns, sort, isControlled]);

  function toggleSort(key: string) {
    const next: SortState =
      sort?.key === key
        ? { key, dir: sort.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" };
    if (isControlled) onSortChange!(next);
    else setUncontrolledSort(next);
  }

  function toggleExpanded(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const columnCount = columns.length + (expandable ? 1 : 0);

  return (
    <div className="dt-wrap">
      {/* `dt-ruled`, not `grid`: there is a `.grid { display: grid }` utility,
          and a table that computes to grid loses its shared column widths. */}
      <table className={`dt${variant === "grid" ? " dt-ruled" : ""}`}>
        <thead>
          <tr>
            {expandable ? <th className="dt-expand-col" /> : null}
            {columns.map((column) => {
              const active = sort?.key === column.key;
              const nextDir = active && sort!.dir === "asc" ? "descending" : "ascending";
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
                        // The visible label is the column name; without this a
                        // screen-reader user hears "Last updated, button" with
                        // no clue what pressing it does.
                        aria-label={`Sort by ${textOf(column.header)}, ${nextDir}`}
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
              <td colSpan={columnCount} className="dt-empty">
                {emptyMessage}
              </td>
            </tr>
          ) : (
            sorted.map((row) => {
              const id = rowKey(row);
              const detail = expandable ? expandable(row) : null;
              const isOpen = expanded.has(id);
              return (
                <FragmentRow
                  key={id}
                  id={id}
                  row={row}
                  columns={columns}
                  detail={detail}
                  isOpen={isOpen}
                  onToggle={() => toggleExpanded(id)}
                  onRowActivate={onRowActivate}
                  rowLabel={rowLabel}
                  columnCount={columnCount}
                  hasExpandColumn={Boolean(expandable)}
                />
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

function FragmentRow<T>({
  id,
  row,
  columns,
  detail,
  isOpen,
  onToggle,
  onRowActivate,
  rowLabel,
  columnCount,
  hasExpandColumn,
}: {
  id: string;
  row: T;
  columns: Column<T>[];
  detail: React.ReactNode;
  isOpen: boolean;
  onToggle: () => void;
  onRowActivate?: (row: T) => void;
  rowLabel?: (row: T) => string;
  columnCount: number;
  hasExpandColumn: boolean;
}) {
  const activatable = Boolean(onRowActivate);
  return (
    <>
      <tr
        className={`${activatable ? "dt-row-activatable" : ""}${isOpen ? " dt-row-open" : ""}`}
        tabIndex={activatable ? 0 : undefined}
        aria-label={activatable && rowLabel ? rowLabel(row) : undefined}
        aria-expanded={detail ? isOpen : undefined}
        onKeyDown={
          activatable
            ? (event) => {
                // Only when the row itself has focus: a key pressed inside a
                // cell's own link or checkbox belongs to that control.
                if (event.target !== event.currentTarget) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onRowActivate!(row);
                }
              }
            : undefined
        }
      >
        {hasExpandColumn ? (
          <td className="dt-expand-col">
            {detail ? (
              <button
                type="button"
                className={`dt-expand${isOpen ? " open" : ""}`}
                onClick={onToggle}
                aria-expanded={isOpen}
                aria-controls={`dt-detail-${id}`}
                aria-label={isOpen ? "Collapse row detail" : "Expand row detail"}
              >
                <ChevronLeft size={13} />
              </button>
            ) : null}
          </td>
        ) : null}
        {columns.map((column) => (
          <td key={column.key} className={column.align === "right" ? "right" : undefined}>
            {column.render(row)}
          </td>
        ))}
      </tr>
      {detail && isOpen ? (
        <tr className="dt-detail-row" id={`dt-detail-${id}`}>
          <td colSpan={columnCount}>
            <div className="dt-detail u-fade-in">{detail}</div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** Exported so a paginating caller can sort the full set with exactly the
 *  comparison the table would have used — same ordering, before slicing. */
export function sortRows<T>(
  rows: T[],
  project: (row: T) => string | number,
  dir: "asc" | "desc",
): T[] {
  const direction = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = project(a);
    const right = project(b);
    if (typeof left === "number" && typeof right === "number") {
      return (left - right) * direction;
    }
    return String(left).localeCompare(String(right)) * direction;
  });
}

/** Best-effort readable text from a header node, for the sort button's label. */
function textOf(header: React.ReactNode): string {
  return typeof header === "string" || typeof header === "number" ? String(header) : "this column";
}
