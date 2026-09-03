"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronUpDown } from "@/components/icons";

export interface ComboboxOption {
  value: string;
  label: string;
  /** Optional leading glyph, e.g. a connection kind. */
  icon?: React.ReactNode;
}

/** Searchable picker: a button that opens a filter box over its options. Posts
 *  through a hidden input so it works inside a plain form action. When
 *  `allowCustom` is set, a query that matches nothing can be used as-is —
 *  the backing field is free text, so inventing a fixed list would be a lie. */
export default function Combobox({
  name,
  value,
  onChange,
  options,
  placeholder = "Select...",
  searchPlaceholder = "Search...",
  allowCustom = false,
  id,
}: {
  name: string;
  value: string;
  onChange: (value: string) => void;
  options: ComboboxOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  allowCustom?: boolean;
  id?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [options, query]);

  const trimmed = query.trim();
  const customAvailable =
    allowCustom &&
    trimmed.length > 0 &&
    !options.some((option) => option.label.toLowerCase() === trimmed.toLowerCase());

  // Rows the keyboard walks: the filtered options, then the custom row if shown.
  const rowCount = filtered.length + (customAvailable ? 1 : 0);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    setActiveIndex(0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function commit(next: string) {
    onChange(next);
    setOpen(false);
    setQuery("");
  }

  function onSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (rowCount ? (index + 1) % rowCount : 0));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (rowCount ? (index - 1 + rowCount) % rowCount : 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (activeIndex < filtered.length) {
        commit(filtered[activeIndex].value);
      } else if (customAvailable) {
        commit(trimmed);
      }
    } else if (event.key === "Escape") {
      // Close the picker without letting the dialog handle the key.
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    }
  }

  const selected = options.find((option) => option.value === value);
  const shownLabel = selected?.label ?? (value || placeholder);

  return (
    <div className="combobox" ref={rootRef}>
      <input type="hidden" name={name} value={value} />
      <button
        type="button"
        id={id}
        className="combobox-button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {selected?.icon ? <span className="combobox-icon">{selected.icon}</span> : null}
        <span className={`combobox-value${value ? "" : " placeholder"}`}>{shownLabel}</span>
        <ChevronUpDown size={15} className="combobox-caret" />
      </button>

      {open ? (
        <div className="combobox-panel">
          <input
            ref={searchRef}
            type="text"
            className="combobox-search"
            value={query}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onSearchKeyDown}
          />
          <ul className="combobox-options" role="listbox">
            {filtered.map((option, index) => (
              <li key={option.value}>
                <button
                  type="button"
                  role="option"
                  aria-selected={option.value === value}
                  className={`combobox-option${index === activeIndex ? " active" : ""}${
                    option.value === value ? " selected" : ""
                  }`}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => commit(option.value)}
                >
                  {option.icon ? <span className="combobox-icon">{option.icon}</span> : null}
                  {option.label}
                </button>
              </li>
            ))}

            {customAvailable ? (
              <li>
                <button
                  type="button"
                  className={`combobox-option custom${
                    activeIndex === filtered.length ? " active" : ""
                  }`}
                  onMouseEnter={() => setActiveIndex(filtered.length)}
                  onClick={() => commit(trimmed)}
                >
                  Use “{trimmed}”
                </button>
              </li>
            ) : null}

            {rowCount === 0 ? <li className="combobox-empty">No match.</li> : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
