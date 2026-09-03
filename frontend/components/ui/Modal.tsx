"use client";

import { useEffect, useRef } from "react";
import { CloseIcon } from "@/components/icons";

/** Native <dialog> in modal mode, so focus trapping, background inertness and
 *  Escape come from the platform rather than from hand-rolled listeners.
 *  Escape and backdrop clicks are routed through `onClose` so a route-driven
 *  modal can navigate back instead of just hiding. */
export default function Modal({
  title,
  badge,
  onClose,
  children,
}: {
  title: string;
  badge?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      className="modal"
      aria-labelledby="modal-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      // A click on ::backdrop targets the dialog element itself.
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="modal-header">
        {badge ? <span className="modal-badge">{badge}</span> : null}
        <h2 id="modal-title" className="modal-title">
          {title}
        </h2>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          <CloseIcon size={18} />
        </button>
      </div>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}
