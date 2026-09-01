"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { runSampleTest, submitOnboarding } from "./actions";

/**
 * CF-V1-E4-03 — step 5's one button, and the refusal it renders.
 *
 * DISABLED IS A COURTESY, NEVER THE GATE. The server refuses an unready or
 * stale submission on its own; this only avoids sending a request whose
 * answer is already known. The distinction matters because the interesting
 * case is the one where the two DISAGREE — she edited a mapping in another
 * tab — and there the request must go, so she reads the server's sentence
 * about which evidence went stale rather than a button that quietly greyed
 * itself out.
 */
export function SubmitOnboarding({
  feedId,
  publishable,
  blocking,
}: {
  feedId: string;
  publishable: boolean;
  blocking: number;
}) {
  const [result, action] = useActionState(submitOnboarding, null);

  return (
    <form action={action} className="card">
      <input type="hidden" name="feed_id" value={feedId} />
      <strong>Step 5 — submit for approval</strong>
      <p className="note">
        Both a business and a technical approver must accept, each seeing the evidence pack.
        Scheduling activates at publication and not before — nothing runs on a schedule until
        somebody signs.
      </p>
      {publishable ? null : (
        <p className="note">
          {blocking > 0
            ? `${blocking} blocking item${blocking === 1 ? "" : "s"} above. Submitting is blocked until they clear — the checklist reflects real lifecycle states, not optimism.`
            : "Not publishable yet. The checklist above says what is still outstanding."}
        </p>
      )}
      <Button enabled={publishable} />
      {result ? (
        <p className="verdict" data-verdict={result.refused ? "mismatch" : "match"}>
          {result.message}
        </p>
      ) : null}
    </form>
  );
}

/**
 * CF-V1-E4-02 — step 4's own button, beside step 5's.
 *
 * A separate form rather than a second submit on the same one: running the
 * test and asking two people to approve are different acts with different
 * consequences, and one form with two submits is one misclick away from
 * submitting when you meant to test.
 */
export function RunSampleTest({ feedId, hasPack }: { feedId: string; hasPack: boolean }) {
  const [result, action] = useActionState(runSampleTest, null);
  return (
    <form action={action} className="card">
      <input type="hidden" name="feed_id" value={feedId} />
      <strong>Step 4 — run the end-to-end test</strong>
      <p className="note">
        Runs your draft schema, mapping and rules over the profiled sample through the real engine,
        in a sandbox that writes no control row and touches no layer. It produces the evidence pack
        both approvers read.
      </p>
      <TestButton again={hasPack} />
      {result ? (
        <p className="verdict" data-verdict={result.refused ? "mismatch" : "match"}>
          {result.message}
        </p>
      ) : null}
    </form>
  );
}

function TestButton({ again }: { again: boolean }) {
  const { pending } = useFormStatus();
  return (
    <button className="action" type="submit" disabled={pending}>
      {pending ? "Running the engine…" : again ? "Run the test again" : "Run the end-to-end test"}
    </button>
  );
}

function Button({ enabled }: { enabled: boolean }) {
  const { pending } = useFormStatus();
  return (
    <button className="action" type="submit" disabled={!enabled || pending}>
      {pending ? "Submitting…" : "Submit for approval →"}
    </button>
  );
}
