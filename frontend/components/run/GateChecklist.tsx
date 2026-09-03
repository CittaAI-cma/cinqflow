"use client";

import { useEffect, useState } from "react";
import Checkbox from "@/components/ui/Checkbox";

interface ChecklistItem {
  id: string;
  text: string;
}

/** Composes the gate's freeform `note` field from a short checklist, so the
 *  record left behind reads as "what she checked" rather than a blank
 *  textbox. This is not a control: the API has no field for "checklist
 *  complete", so nothing here disables Approve — an unticked box is the
 *  analyst's own risk to take, not this build's to block. */
export default function GateChecklist({
  phiCount,
  unknownCount,
  onChange,
  onProgress,
}: {
  phiCount: number;
  unknownCount: number;
  onChange: (note: string) => void;
  /** Ticked count and total, so the gate can show progress and name the
   *  unticked items in its confirmation. Still not a control: nothing here
   *  blocks Approve. */
  onProgress?: (checked: number, total: number) => void;
}) {
  const items: ChecklistItem[] = [
    { id: "columns", text: "Column names and types look right" },
    { id: "rowcount", text: "Row count is plausible for this delivery" },
    ...(phiCount > 0 ? [{ id: "phi", text: "PHI flags look right" }] : []),
    ...(unknownCount > 0 ? [{ id: "unknowns", text: "The unknowns are acceptable for Bronze" }] : []),
  ];

  const [checked, setChecked] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const note = items
      .map((item) => `${checked[item.id] ? "☑" : "☐"} ${item.text}`)
      .join("\n");
    onChange(note);
    onProgress?.(items.filter((item) => checked[item.id]).length, items.length);
    // Re-compose only when a box actually changes, not on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checked, phiCount, unknownCount]);

  return (
    <ul className="gate-checklist">
      {items.map((item) => (
        <li key={item.id}>
          <label className="gate-checklist-item">
            <Checkbox
              checked={Boolean(checked[item.id])}
              onChange={(next) => setChecked((current) => ({ ...current, [item.id]: next }))}
              label={item.text}
            />
            <span>{item.text}</span>
          </label>
        </li>
      ))}
    </ul>
  );
}
