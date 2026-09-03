"use client";

import { useActionState, useEffect } from "react";
import { useFormStatus } from "react-dom";
import { startDraft, type StudioState } from "@/app/mapping/actions";
import { useToast } from "@/lib/useToast";

function Button({ label }: { label: string }) {
  const { pending } = useFormStatus();
  return (
    <button type="submit" disabled={pending}>
      {pending ? "Creating…" : label}
    </button>
  );
}

export default function StartDraft({
  feed,
  proposalId,
  deriveFrom,
}: {
  feed: string;
  proposalId?: string;
  deriveFrom?: number;
}) {
  const [state, action] = useActionState<StudioState, FormData>(startDraft, {});
  const { push } = useToast();

  // `startDraft` redirects into the new version on success, so this component
  // unmounts before it could render any inline confirmation — a toast is the
  // only feedback that can survive the navigation, and without it creating a
  // draft was completely silent.
  useEffect(() => {
    if (state.error) push(state.error, "error");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.error]);

  return (
    <form action={action} className="card grid">
      <input type="hidden" name="feed" value={feed} />
      {proposalId ? <input type="hidden" name="from_proposal_id" value={proposalId} /> : null}
      {deriveFrom ? <input type="hidden" name="derive_from_version" value={deriveFrom} /> : null}
      {!proposalId && !deriveFrom ? (
        <div>
          <label htmlFor="from_proposal_id">Proposal id</label>
          <input
            id="from_proposal_id"
            name="from_proposal_id"
            placeholder="paste the proposal id from a batch page"
          />
        </div>
      ) : null}
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="meta">
          {proposalId ? (
            <>
              Seeds a draft from proposal <span className="mono">{proposalId}</span> — its
              defensible candidates only.
            </>
          ) : deriveFrom ? (
            `Copies v${deriveFrom} into a new editable draft and records what it came from.`
          ) : (
            "Seeds a draft from the AI proposal's defensible candidates only."
          )}
        </span>
        <Button label={deriveFrom ? `Start v${deriveFrom + 1}` : "Start draft"} />
      </div>
      {state.error ? <p className="alert error">{state.error}</p> : null}
    </form>
  );
}
