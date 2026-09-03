"use client";

import { SearchIcon } from "@/components/icons";

/** Search on the left, whatever the page needs on the right. */
export default function TableToolbar({
  query,
  onQueryChange,
  placeholder = "Search by name...",
  children,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  placeholder?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="toolbar">
      <div className="search">
        <SearchIcon size={16} className="search-icon" />
        <input
          type="search"
          className="search-input"
          value={query}
          placeholder={placeholder}
          aria-label={placeholder}
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </div>
      <div className="toolbar-actions">{children}</div>
    </div>
  );
}
