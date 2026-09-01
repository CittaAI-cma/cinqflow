"use client";

import { useFormStatus } from "react-dom";
import { detectPhi, inferSchema, suggestMapping } from "./actions";

/**
 * CF-V1-E5-02 · E5-03 · E6-02 — the buttons that were missing.
 *
 * WHY THEY LIVE ON THE PROFILE PAGE. This is where a BA stands after step 1,
 * looking at the computed facts, and every one of these agents INTERPRETS
 * those facts — "the AI only ever interprets them, it never guesses them".
 * Putting the actions anywhere else would separate the evidence from the act
 * it grounds.
 *
 * EACH SAYS WHAT IT WILL PRODUCE. Not "Run AI" — a person about to spend a
 * model call and create something a steward must review is owed the name of
 * the artifact. Each one lands on the proposal it made.
 */
export function AgentActions({
  feedId,
  profileId,
  mayEdit,
  refused,
}: {
  feedId: string;
  profileId: string;
  mayEdit: boolean;
  refused?: string;
}) {
  if (!mayEdit) {
    return (
      <div className="card">
        <strong>Interpreting this profile</strong>
        <p className="note">
          Proposing a schema, PHI classification or mapping is{" "}
          <span className="mono">edit_feed</span>. Your role can read this profile but not ask an
          agent to interpret it.
        </p>
      </div>
    );
  }
  return (
    <div className="card">
      <strong>Interpret these facts</strong>
      <p className="note">
        Everything above is arithmetic over the sample&rsquo;s bytes. Each action below asks an
        agent to interpret it and leaves a <em>draft proposal</em> for you to review — nothing is
        applied, and nothing is published.
      </p>
      {refused ? (
        <p className="verdict" data-verdict="mismatch">
          {refused}
        </p>
      ) : null}
      <div className="action-row">
        <Action
          action={inferSchema}
          feedId={feedId}
          profileId={profileId}
          label="Propose a schema contract"
          note="CF-V1-E5-02 — types, names and nullability, each with a confidence."
        />
        <Action
          action={detectPhi}
          feedId={feedId}
          profileId={profileId}
          label="Detect PHI and code sets"
          note="CF-V1-E5-03 — uncertain fields are treated as PHI until a steward says otherwise."
        />
        <Action
          action={suggestMapping}
          feedId={feedId}
          profileId={profileId}
          label="Suggest a mapping"
          note="CF-V1-E6-02 — with precedents; unclear fields are left UNMAPPED, loudly."
        />
      </div>
    </div>
  );
}

function Action({
  action,
  feedId,
  profileId,
  label,
  note,
}: {
  action: (formData: FormData) => void | Promise<void>;
  feedId: string;
  profileId: string;
  label: string;
  note: string;
}) {
  return (
    <form action={action}>
      <input type="hidden" name="feed_id" value={feedId} />
      <input type="hidden" name="profile_id" value={profileId} />
      <Button label={label} />
      <span className="note">{note}</span>
    </form>
  );
}

function Button({ label }: { label: string }) {
  const { pending } = useFormStatus();
  return (
    <button className="action" type="submit" disabled={pending}>
      {pending ? "Asking the agent…" : label}
    </button>
  );
}
