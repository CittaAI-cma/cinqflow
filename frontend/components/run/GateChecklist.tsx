"use client";

import { useEffect, useState } from "react";
import Checkbox from "@/components/ui/Checkbox";

export interface ChecklistItem {
  id: string;
  text: string;
}

/** Composes a gate's freeform `note` field from a short checklist, so the
 *  record left behind reads as "what she checked" rather than a blank
 *  textbox. This is not a control: the API has no field for "checklist
 *  complete", so nothing here disables Approve — an unticked box is the
 *  analyst's own risk to take, not this build's to block.
 *
 *  Shared by both gates - `items` is built by the caller (`GateActions` for
 *  G1's PHI/unknowns items, `ApproveMapping` for G2's preview/edit items), so
 *  the composing logic lives in exactly one place. */
export default function GateChecklist({
  items,
  onChange,
  onProgress,
}: {
  items: ChecklistItem[];
  onChange: (note: string) => void;
  /** Ticked count and total, so the gate can show progress and name the
   *  unticked items in its confirmation. Still not a control: nothing here
   *  blocks Approve. */
  onProgress?: (checked: number, total: number) => void;
}) {
  const [checked, setChecked] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const note = items
      .map((item) => `${checked[item.id] ? "☑" : "☐"} ${item.text}`)
      .join("\n");
    onChange(note);
    onProgress?.(items.filter((item) => checked[item.id]).length, items.length);
    // Re-compose only when a box actually changes, not on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checked, items]);

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
