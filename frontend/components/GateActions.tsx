"use client";

import { useActionState, useRef, useState } from "react";
import { useFormStatus } from "react-dom";
import { submitDecision, type DecisionState } from "@/app/actions";
import GateChecklist, { type ChecklistItem } from "@/components/run/GateChecklist";
import { announceOnSubmit } from "@/components/ui/AnnounceOnMount";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { DEFAULT_UPLOADER } from "@/lib/appConfig";

type Decision = "approved" | "rejected";

/** The real submit buttons, hidden from view. The visible controls open a
 *  confirmation first; this is what the dialog actually presses, so the
 *  server action still receives a normal form submission with its own
 *  `pending` state rather than a hand-rolled fetch. */
function HiddenSubmits({
  approveRef,
  rejectRef,
}: {
  approveRef: React.RefObject<HTMLButtonElement | null>;
  rejectRef: React.RefObject<HTMLButtonElement | null>;
}) {
  return (
    <div className="sr-only" aria-hidden="true">
      <button type="submit" name="decision" value="approved" ref={approveRef} tabIndex={-1} />
      <button type="submit" name="decision" value="rejected" ref={rejectRef} tabIndex={-1} />
    </div>
  );
}

function VisibleButtons({
  onAsk,
  checkedCount,
  totalCount,
}: {
  onAsk: (decision: Decision) => void;
  checkedCount: number;
  totalCount: number;
}) {
  const { pending } = useFormStatus();
  const unchecked = totalCount - checkedCount;
  return (
    <div className="gate-actions">
      <span className="gate-progress meta">
        {checkedCount} of {totalCount} checked
      </span>
      <button
        type="button"
        className="secondary"
        onClick={() => onAsk("rejected")}
        disabled={pending}
        data-busy={pending ? "true" : undefined}
        title={pending ? "Recording the decision…" : "Reject this file — this cannot be undone"}
      >
        {pending ? "Working…" : "Reject"}
      </button>
      <button
        type="button"
        onClick={() => onAsk("approved")}
        disabled={pending}
        data-busy={pending ? "true" : undefined}
        title={
          pending
            ? "Recording the decision…"
            : unchecked > 0
              ? `${unchecked} checklist item(s) unticked — you can still approve; the record will show what you checked`
              : "Approve and queue the Bronze write"
        }
      >
        {pending ? "Working…" : "Approve — G1"}
      </button>
    </div>
  );
}

/** G1. `phiCount`/`unknownCount` size the checklist that composes the
 *  decision's `note` — the record it leaves behind reads as what she
 *  actually checked, not a blank textbox.
 *
 *  Both decisions go through a confirmation, for different reasons: approve
 *  writes to the data plane, and reject is *terminal* — the state machine has
 *  no transition out of `rejected`, so the only way forward is re-uploading
 *  the file. A one-click button for an action with no undo is the wrong
 *  affordance, so reject additionally has to be typed out.
 */
export default function GateActions({
  uploadId,
  filename,
  phiCount = 0,
  unknownCount = 0,
}: {
  uploadId: string;
  filename?: string;
  phiCount?: number;
  unknownCount?: number;
}) {
  const [state, action] = useActionState<DecisionState, FormData>(submitDecision, {});
  const [note, setNote] = useState("");
  const [checkedCount, setCheckedCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [asking, setAsking] = useState<Decision | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const approveRef = useRef<HTMLButtonElement>(null);
  const rejectRef = useRef<HTMLButtonElement>(null);

  function handleSubmit() {
    const message =
      asking === "rejected" ? "Rejected." : "Approved — Bronze write queued.";
    announceOnSubmit(uploadId, message);
  }

  function confirm() {
    setSubmitting(true);
    // Presses the real submit button, so the form action runs exactly as it
    // would have without the dialog in the way.
    (asking === "rejected" ? rejectRef : approveRef).current?.click();
    setAsking(null);
    setSubmitting(false);
  }

  const unticked = totalCount - checkedCount;
  const items: ChecklistItem[] = [
    { id: "columns", text: "Column names and types look right" },
    { id: "rowcount", text: "Row count is plausible for this delivery" },
    ...(phiCount > 0 ? [{ id: "phi", text: "PHI flags look right" }] : []),
    ...(unknownCount > 0
      ? [{ id: "unknowns", text: "The unknowns are acceptable for Bronze" }]
      : []),
  ];

  return (
    <form action={action} onSubmit={handleSubmit} className="gate-box">
      <input type="hidden" name="upload_id" value={uploadId} />
      <input type="hidden" name="note" value={note} />
      <p className="gate-note">
        <b>Approving mints a batch and writes Bronze</b> — append-only, enforced by a
        reject-mutation trigger. Rejecting moves the file to <span className="mono">rejected/</span>{" "}
        and writes nothing to the data plane.
      </p>
      <GateChecklist
        items={items}
        onChange={setNote}
        onProgress={(checked, total) => {
          setCheckedCount(checked);
          setTotalCount(total);
        }}
      />
      <VisibleButtons onAsk={setAsking} checkedCount={checkedCount} totalCount={totalCount} />
      <HiddenSubmits approveRef={approveRef} rejectRef={rejectRef} />
      {state.error ? <p className="alert error">{state.error}</p> : null}

      <ConfirmDialog
        open={asking !== null}
        tone={asking === "rejected" ? "danger" : "neutral"}
        title={asking === "rejected" ? "Reject this file?" : "Approve and write to Bronze?"}
        confirmLabel={asking === "rejected" ? "Reject permanently" : "Approve — G1"}
        requireTyped={asking === "rejected" ? "REJECT" : undefined}
        busy={submitting}
        onCancel={() => setAsking(null)}
        onConfirm={confirm}
        consequence={
          asking === "rejected" ? (
            <>
              <b>This cannot be undone.</b> Rejection is terminal — there is no
              transition out of it. The file moves to{" "}
              <span className="mono">rejected/</span>, nothing is written to the
              data plane, and the only way forward for this data is uploading a
              corrected file as a new ingestion.
            </>
          ) : (
            <>
              This mints a batch and writes every row to Bronze, which is{" "}
              <b>append-only</b> — a database trigger refuses updates and
              deletes, so rows written here cannot later be edited or removed.
              Mapping to Silver stays behind its own gate (G2).
            </>
          )
        }
        audit={
          <>
            <span>
              Decision recorded as <span className="mono">{DEFAULT_UPLOADER}</span>
            </span>
            {filename ? (
              <span>
                File <span className="mono">{filename}</span>
              </span>
            ) : null}
            <span>
              Gate <span className="mono">G1</span> · upload{" "}
              <span className="mono">{uploadId.slice(0, 8)}</span>
            </span>
            <span>
              Checklist: {checkedCount} of {totalCount} ticked
              {unticked > 0 ? " — the unticked items are recorded as unticked" : ""}
            </span>
          </>
        }
      />
    </form>
  );
}
