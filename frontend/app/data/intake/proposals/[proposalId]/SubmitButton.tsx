"use client";

import { useFormStatus } from "react-dom";

/**
 * One form's own submit button, disabled while its own action is pending and
 * while the caller has already said this act is not permitted. The same
 * "advice, never a second door" rule `ActionBar`'s own `SubmitButton`
 * follows: the SERVER decides, on every one of the four routes this page
 * posts to — this only saves a reviewer the round trip to find out.
 */
export function SubmitButton({
  label,
  pendingLabel,
  disabledBecause,
  primary = false,
}: {
  label: string;
  pendingLabel: string;
  disabledBecause?: string;
  primary?: boolean;
}) {
  const { pending } = useFormStatus();
  return (
    <span className="inline">
      <button
        type="submit"
        className={primary ? "primary" : undefined}
        disabled={pending || Boolean(disabledBecause)}
      >
        {pending ? pendingLabel : label}
      </button>
      {disabledBecause ? <span className="note">{disabledBecause}</span> : null}
    </span>
  );
}
