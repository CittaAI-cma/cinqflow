"use client";

import { useEffect, useState } from "react";

export type ReadingModeKey = "verdict" | "evidence" | "forensic";

const STORAGE_KEY = "reading-mode";
const MODES: { key: ReadingModeKey; label: string }[] = [
  { key: "verdict", label: "Verdict" },
  { key: "evidence", label: "Evidence" },
  { key: "forensic", label: "Forensic" },
];

/** Filters the evidence column by trust level, not an accordion: three fixed
 *  postures (a 15-second pass, the normal two-minute review, a detailed
 *  investigation), not a pile of things to individually expand. Persisted
 *  per-browser so switching to Forensic once doesn't have to be redone on
 *  every run. */
export default function ReadingMode({
  mode,
  onChange,
}: {
  mode: ReadingModeKey;
  onChange: (mode: ReadingModeKey) => void;
}) {
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY) as ReadingModeKey | null;
      if (saved && MODES.some((m) => m.key === saved)) onChange(saved);
    } catch {
      // No storage access (private mode, etc.) — the default stands.
    }
    // Runs once, on mount, to restore the saved preference.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function select(next: ReadingModeKey) {
    onChange(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Best-effort — the in-memory selection still works this session.
    }
  }

  return (
    <div className="reading-mode" role="radiogroup" aria-label="Reading mode">
      {MODES.map((m) => (
        <button
          key={m.key}
          type="button"
          role="radio"
          aria-checked={mode === m.key}
          className={`reading-mode-option${mode === m.key ? " active" : ""}`}
          onClick={() => select(m.key)}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
