"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { submitRerun } from "@/app/actions";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { RERUN_CONSEQUENCE, RERUN_FALLBACK_CONSEQUENCE, type RerunSource } from "@/lib/rerun";
import { useToast } from "@/lib/useToast";

/** One failed step's Re-run, from the platform home (PR-4) - the same
 *  confirmation, consequence sentence and Server Action `WorkflowSteps` uses on
 *  the run screens, so a re-run reads and behaves the same wherever it is
 *  offered. Rendered only for a caller with `can_rerun_steps`; the API
 *  enforces it regardless and its 409 reason is shown verbatim. */
export default function RerunStepButton({
  source,
  stepKey,
  label,
  audit,
}: {
  source: RerunSource;
  stepKey: string;
  label: string;
  /** The ledger row, in one line, for the dialog's "recorded with" panel. */
  audit?: string;
}) {
  const router = useRouter();
  const { push } = useToast();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    const result = await submitRerun(source, stepKey);
    setBusy(false);
    setAsking(false);
    if (result.error) {
      setError(result.error);
      push(result.error, "error");
      return;
    }
    push(`Re-run queued — ${label}`, "success");
    router.refresh();
  }

  return (
    <>
      <button
        type="button"
        className="btn-outline workflow-step-rerun"
        onClick={() => setAsking(true)}
        disabled={busy}
        title={RERUN_CONSEQUENCE[stepKey] ?? RERUN_FALLBACK_CONSEQUENCE}
      >
        Re-run
      </button>
      {error ? <div className="error small">{error}</div> : null}
      <ConfirmDialog
        open={asking}
        title={`Re-run ${label.toLowerCase()}?`}
        confirmLabel="Re-run"
        busy={busy}
        onCancel={() => setAsking(false)}
        onConfirm={confirm}
        consequence={RERUN_CONSEQUENCE[stepKey] ?? RERUN_FALLBACK_CONSEQUENCE}
        audit={audit ? <span className="mono small">{audit}</span> : undefined}
      />
    </>
  );
}
