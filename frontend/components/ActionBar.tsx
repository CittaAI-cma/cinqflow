"use client";

import { useFormStatus } from "react-dom";

/**
 * One gated action, offered on the governed action surface. Plate 4.7's
 * `ActionBar`, driven by `available(state, guards)` computed by the CALLER —
 * a button that appears here is one the caller already checked the incident's
 * own state machine and the signed-in principal's permitted actions against.
 *
 * THE SERVER DECIDES ANYWAY. Nothing here re-implements
 * `core.operations.fingerprint`'s transition table — a caller that got the
 * guard wrong, or a state that changed between render and click, still meets
 * the real refusal (`IncidentTransitionError`, carried to the wire as a 409)
 * when the form posts. This bar is advice about which buttons are worth
 * showing, never the authority on which ones would work.
 */
export interface ActionSpec {
  key: string;
  label: string;
  /** The server action this action's own form posts to. */
  action: (formData: FormData) => void | Promise<void>;
  /** Extra fields this action's form carries, beyond the subject's id. */
  fields?: React.ReactNode;
  /** Set when the button should render disabled — the reason is shown next
   *  to it, the same "advice, not a second door" rule `DeliverForm` follows. */
  disabledBecause?: string;
}

export function ActionBar({
  subjectField,
  subjectId,
  actions,
}: {
  /** The hidden field name each form carries the subject's id under. */
  subjectField: string;
  subjectId: string;
  actions: ActionSpec[];
}) {
  if (actions.length === 0) {
    return <span className="note">no action available</span>;
  }
  return (
    <div className="stack">
      {actions.map((spec) => (
        <form key={spec.key} action={spec.action} className="inline">
          <input type="hidden" name={subjectField} value={subjectId} />
          {spec.fields}
          <SubmitButton label={spec.label} disabledBecause={spec.disabledBecause} />
        </form>
      ))}
    </div>
  );
}

function SubmitButton({
  label,
  disabledBecause,
}: {
  label: string;
  disabledBecause?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <span className="inline">
      <button type="submit" disabled={pending || Boolean(disabledBecause)}>
        {pending ? `${label}…` : label}
      </button>
      {disabledBecause ? <span className="note">{disabledBecause}</span> : null}
    </span>
  );
}
