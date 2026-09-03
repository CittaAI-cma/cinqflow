"use client";

import { useActionState, useState } from "react";
import { useFormStatus } from "react-dom";
import { submitDecision, type DecisionState } from "@/app/actions";
import GateChecklist from "@/components/run/GateChecklist";
import { announceOnSubmit } from "@/components/ui/AnnounceOnMount";

function Buttons() {
  const { pending } = useFormStatus();
  return (
    <div className="gate-actions">
      <button type="submit" name="decision" value="rejected" disabled={pending} className="secondary">
        Reject
      </button>
      <button type="submit" name="decision" value="approved" disabled={pending}>
        {pending ? "Working…" : "Approve — G1"}
      </button>
    </div>
  );
}

/** G1. `phiCount`/`unknownCount` size the checklist that composes the
 *  decision's `note` — the record it leaves behind reads as what she
 *  actually checked, not a blank textbox. */
export default function GateActions({
  uploadId,
  phiCount = 0,
  unknownCount = 0,
}: {
  uploadId: string;
  phiCount?: number;
  unknownCount?: number;
}) {
  const [state, action] = useActionState<DecisionState, FormData>(submitDecision, {});
  const [note, setNote] = useState("");

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const message =
      submitter?.value === "rejected" ? "Rejected." : "Approved — Bronze write queued.";
    announceOnSubmit(uploadId, message);
  }

  return (
    <form action={action} onSubmit={handleSubmit} className="gate-box">
      <input type="hidden" name="upload_id" value={uploadId} />
      <input type="hidden" name="note" value={note} />
      <p className="gate-note">
        <b>Approving mints a batch and writes Bronze</b> — append-only, enforced by a
        reject-mutation trigger. Rejecting moves the file to <span className="mono">rejected/</span>{" "}
        and writes nothing to the data plane.
      </p>
      <GateChecklist phiCount={phiCount} unknownCount={unknownCount} onChange={setNote} />
      <Buttons />
      {state.error ? <p className="alert error">{state.error}</p> : null}
    </form>
  );
}
