"use client";

import { useEffect, useRef, useState } from "react";

/** ConfirmDialog — the pause before something the platform cannot take back.
 *
 *  Used for a decision whose consequence is real and outward-facing: a G1
 *  reject is terminal (the file must be re-uploaded), a G1 approve mints a
 *  batch and writes append-only Bronze. Those deserve a beat between intent
 *  and effect; a save draft does not, and must not get one — a confirm on a
 *  reversible action trains people to click through the ones that matter.
 *
 *  Three things this does that a `window.confirm` cannot:
 *   - states the consequence in the app's own words, next to the audit context
 *     (who is signing, what it is attached to);
 *   - can require the user to type a word for the genuinely irreversible case,
 *     so muscle memory alone can't complete it;
 *   - keeps focus management inside a native <dialog>, restoring it to the trigger on
 *     close, so a keyboard user is never dropped at the top of the document.
 */
export default function ConfirmDialog({
  open,
  title,
  tone = "neutral",
  confirmLabel,
  cancelLabel = "Cancel",
  /** The plain-language consequence. Shown prominently, never truncated. */
  consequence,
  /** Who/what the decision is attached to — the audit trail, before the fact. */
  audit,
  /** When set, the confirm button stays disabled until this exact word is typed. */
  requireTyped,
  busy = false,
  onConfirm,
  onCancel,
  children,
}: {
  open: boolean;
  title: string;
  tone?: "neutral" | "danger";
  confirmLabel: string;
  cancelLabel?: string;
  consequence: React.ReactNode;
  audit?: React.ReactNode;
  requireTyped?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children?: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const returnFocusTo = useRef<Element | null>(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      returnFocusTo.current = document.activeElement;
      dialog.showModal();
    }
    if (!open && dialog.open) {
      dialog.close();
      // Focus goes back where it came from, not to <body>.
      const target = returnFocusTo.current;
      if (target instanceof HTMLElement) target.focus();
    }
  }, [open]);

  // A fresh confirmation never inherits the last one's typed word.
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  const satisfied = !requireTyped || typed.trim().toUpperCase() === requireTyped.toUpperCase();

  return (
    <dialog
      ref={ref}
      className="modal confirm-dialog"
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-consequence"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      onClick={(event) => {
        // Backdrop click cancels — but never while the action is in flight,
        // where a stray click would leave the user unsure whether it ran.
        if (event.target === ref.current && !busy) onCancel();
      }}
    >
      <div className="modal-header">
        <h2 id="confirm-dialog-title" className="modal-title">
          {title}
        </h2>
      </div>
      <div className="modal-body">
        <p
          id="confirm-dialog-consequence"
          className={`confirm-consequence${tone === "danger" ? " danger" : ""}`}
        >
          {consequence}
        </p>

        {audit ? (
          <div className="confirm-audit">
            <span className="panel-label">Recorded with this decision</span>
            <div className="confirm-audit-body">{audit}</div>
          </div>
        ) : null}

        {children}

        {requireTyped ? (
          <div className="confirm-typed">
            <label htmlFor="confirm-typed-input">
              Type <span className="mono">{requireTyped}</span> to confirm
            </label>
            <input
              id="confirm-typed-input"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              aria-describedby="confirm-typed-hint"
            />
            <span id="confirm-typed-hint" className="sr-only">
              This action cannot be undone, so it must be typed out rather than only clicked.
            </span>
          </div>
        ) : null}

        <div className="confirm-actions">
          <button type="button" className="btn-outline" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={tone === "danger" ? "btn-danger" : "btn-dark"}
            onClick={onConfirm}
            disabled={busy || !satisfied}
            data-busy={busy ? "true" : undefined}
            title={
              satisfied
                ? undefined
                : `Type ${requireTyped} above to enable this — the action cannot be undone`
            }
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
