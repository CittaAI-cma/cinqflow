"use client";

import { ChevronLeft, ChevronRight } from "@/components/icons";

export const PAGE_SIZES = [10, 25, 50] as const;

/** Page controls on the left, page size on the right — the totals it reports
 *  are the caller's filtered count, not the raw dataset. */
export default function Pagination({
  page,
  pageCount,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pageCount: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}) {
  return (
    <div className="pagination">
      <div className="pagination-pages">
        <button
          type="button"
          className="page-button"
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
        >
          <ChevronLeft size={14} />
        </button>
        <span className="pagination-status">
          Page <b>{page}</b> of <b>{pageCount}</b> <span className="dot">·</span> {totalItems}{" "}
          {totalItems === 1 ? "item" : "items"}
        </span>
        <button
          type="button"
          className="page-button"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= pageCount}
          aria-label="Next page"
        >
          <ChevronRight size={14} />
        </button>
      </div>

      <select
        className="native-select page-size"
        value={pageSize}
        aria-label="Rows per page"
        onChange={(event) => onPageSizeChange(Number(event.target.value))}
      >
        {PAGE_SIZES.map((size) => (
          <option key={size} value={size}>
            {size} per page
          </option>
        ))}
      </select>
    </div>
  );
}
