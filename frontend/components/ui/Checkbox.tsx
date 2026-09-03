"use client";

import { CheckIcon } from "@/components/icons";

/** Filled square checkbox. The native input stays in the tree for keyboard and
 *  assistive-tech behaviour; the visible box is drawn beside it. */
export default function Checkbox({
  checked,
  onChange,
  label,
  id,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Accessible name — visually hidden, since the card renders its own title. */
  label: string;
  id?: string;
  disabled?: boolean;
}) {
  return (
    <span className={`checkbox${disabled ? " disabled" : ""}`}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="checkbox-box" aria-hidden="true">
        {checked ? <CheckIcon size={12} /> : null}
      </span>
    </span>
  );
}
