"use client";

import Link from "next/link";
import { useState } from "react";
import { ACTION_ICONS } from "@/components/icons";
import { PLATFORM_ACTIONS } from "@/lib/platformActions";

/** The four entry points and the panel they drive. Nothing is selected on first
 *  paint, so the panel opens on its empty state — the prompt to choose. */
export default function ActionLauncher() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = PLATFORM_ACTIONS.find((action) => action.id === selectedId) ?? null;

  return (
    <>
      <div className="action-chips">
        {PLATFORM_ACTIONS.map((action) => {
          const Icon = ACTION_ICONS[action.icon];
          const on = action.id === selectedId;
          return (
            <button
              key={action.id}
              type="button"
              className={`action-chip${on ? " on" : ""}`}
              aria-pressed={on}
              onClick={() => setSelectedId(on ? null : action.id)}
            >
              <Icon size={16} />
              {action.label}
            </button>
          );
        })}
      </div>

      <section className="action-panel" aria-live="polite">
        {!selected ? (
          <p className="action-panel-empty">Please select an action above to continue...</p>
        ) : (
          <>
            <h2 className="action-panel-title">{selected.label}</h2>
            <p className="action-panel-blurb">{selected.blurb}</p>

            {selected.unavailable ? (
              <p className="alert warn">{selected.unavailable}</p>
            ) : (
              <div className="action-links">
                {selected.links.map((link) => (
                  <Link key={link.href} href={link.href} className="action-link">
                    <span className="action-link-label">{link.label} →</span>
                    <span className="action-link-note">{link.note}</span>
                  </Link>
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}
