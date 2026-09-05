"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { submitRerun } from "@/app/actions";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import WaitNotice from "@/components/ui/WaitNotice";
import { getFeedVersionProgress, getUploadProgress, type StepProgress } from "@/lib/api";
import { RERUN_LOCKED_REASON } from "@/lib/persona";
import {
  RERUN_CONSEQUENCE,
  RERUN_FALLBACK_CONSEQUENCE,
  RERUNNABLE_STATES,
  rerunSourceFor as rerunSourceForScope,
  type RerunSource,
} from "@/lib/rerun";
import { usePoll } from "@/lib/usePoll";
import { useToast } from "@/lib/useToast";

const POLL_MS = 1500;

/** What to poll. Serializable on purpose: a Server Component renders this and
 *  can only hand over data, never a fetcher (the `PreviewPanel.limitHref`
 *  precedent, plan §18.4). */
export type StepsSource =
  | { kind: "upload"; uploadId: string }
  | { kind: "feed_version"; feed: string; version: number };

/** A worker step that is queued or running - the only thing worth waiting on.
 *  A gate that is "running" is a person deciding; no poll settles that. */
export function stepsInFlight(steps: StepProgress[], only?: readonly string[]): boolean {
  return steps.some(
    (s) =>
      (!only || only.includes(s.key)) &&
      !s.gate &&
      (s.state === "pending" || s.state === "running"),
  );
}

function stateWord(s: StepProgress): string {
  switch (s.state) {
    case "pending":
      return "Queued";
    case "running":
      return s.gate ? "Awaiting decision" : "Running";
    case "done":
      return s.gate ? "Decided" : "Done";
    case "failed":
      return s.gate ? "Rejected" : "Failed";
    case "skipped":
      return "Skipped";
    default:
      return "Not reached";
  }
}

function dotClass(s: StepProgress): string {
  switch (s.state) {
    case "done":
      return "done";
    case "running":
      return "current";
    case "failed":
      return "adverse";
    case "pending":
      return "pending queued";
    default:
      return "pending";
  }
}

function duration(from: string, to: string | null): string {
  const ms = (to ? new Date(to).getTime() : Date.now()) - new Date(from).getTime();
  const total = Math.max(0, Math.round(ms / 1000));
  return total >= 60 ? `${Math.floor(total / 60)}m ${total % 60}s` : `${total}s`;
}

/** The re-run route comes from the ledger row's own scope (`lib/rerun.ts`). */
function rerunSourceFor(step: StepProgress): RerunSource | null {
  return step.run ? rerunSourceForScope(step.run.scope_kind, step.run.scope_id) : null;
}

/** The step ledger, live. One component, one poll over `/progress`, in place
 *  of the three bespoke pollers this replaced (`LandingWait`,
 *  `BronzeAnalysisWait`, `PreviewPanel`'s preview poll) - each of which
 *  re-derived "is step X done yet" from a different endpoint.
 *
 *  Renders every step's state, attempts, duration and error, straight from
 *  the backend's declaration (`workflow/dag.py`): there is no second list of
 *  steps here to drift. Polls only while a worker step is queued or running,
 *  and refreshes the server page once nothing is - so the page that mounted
 *  this re-renders with whatever the finished step produced. `initial` comes
 *  from the server render; a refresh that brings a newly queued step (the
 *  analyst pressed "Run preview", or an engineer pressed Re-run) restarts the
 *  poll on its own.
 *
 *  Re-run (PR-3): offered per `failed`/`done`/`skipped` worker step when the
 *  caller holds `can_rerun_steps`, with the consequence stated first. The API
 *  enforces the capability and the legality table; a 409's reason is shown
 *  verbatim. Without the capability the reason for the absent control is on
 *  screen (§18.2: never a disabled control without its reason).
 *
 *  Persona: `expanded` (Data Platform) shows unreached steps too; the analyst
 *  default shows only what has happened, with a toggle. `WaitNotice` keeps
 *  the wait honest - offline and stalled are distinct from "still going". */
export default function WorkflowSteps({
  source,
  initial,
  only,
  expanded = false,
  canRerun = false,
  stallAfterMs = 45_000,
  what = "this step",
  stalledCopy,
}: {
  source: StepsSource;
  /** `steps[]` from the server render of the page that mounts this. */
  initial: StepProgress[];
  /** Restrict to these ledger step keys (e.g. `["land"]` on the decision record). */
  only?: readonly string[];
  /** Persona default (`lib/persona.ts`): show unreached steps from the start. */
  expanded?: boolean;
  /** `capabilities.can_rerun_steps` from the server. Decides whether a
   *  finished or failed step offers Re-run, or states why it does not. */
  canRerun?: boolean;
  /** How long a queued/running step may sit before it reads as stalled. */
  stallAfterMs?: number;
  /** Named in the offline/stalled copy, e.g. "landing to Bronze". */
  what?: string;
  /** What a stall most likely means on this screen. */
  stalledCopy?: string;
}) {
  const router = useRouter();
  const { push } = useToast();
  const [showAll, setShowAll] = useState(expanded);
  const [asking, setAsking] = useState<StepProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [rerunError, setRerunError] = useState<string | null>(null);

  const sourceKey =
    source.kind === "upload"
      ? `upload:${source.uploadId}`
      : `version:${source.feed}:${source.version}`;
  const initialKey = initial.map((s) => `${s.key}=${s.state}`).join(",");
  const enabled = stepsInFlight(initial, only);

  const poll = usePoll<StepProgress[]>(
    async () =>
      source.kind === "upload"
        ? (await getUploadProgress(source.uploadId)).steps
        : (await getFeedVersionProgress(source.feed, source.version)).steps,
    {
      enabled,
      intervalMs: POLL_MS,
      isSettled: (next) => !stepsInFlight(next, only),
      onSettle: () => router.refresh(),
      stallAfterMs,
    },
    [sourceKey, initialKey],
  );

  const steps = poll.value ?? initial;
  const scoped = only ? steps.filter((s) => only.includes(s.key)) : steps;
  const collapsible = scoped.some((s) => s.state === "not_reached");
  const visible = showAll ? scoped : scoped.filter((s) => s.state !== "not_reached");
  const inFlight = scoped.find(
    (s) => !s.gate && (s.state === "pending" || s.state === "running"),
  );
  const waiting =
    inFlight && !poll.settled
      ? `${inFlight.label} ${inFlight.state === "pending" ? "is queued" : "is running"} — this updates automatically.`
      : "";
  const rerunnable = (s: StepProgress) =>
    !s.gate && s.run !== null && RERUNNABLE_STATES.has(s.state) && rerunSourceFor(s) !== null;
  const anyRerunnable = scoped.some(rerunnable);

  async function confirmRerun() {
    if (!asking) return;
    const target = rerunSourceFor(asking);
    if (!target) return;
    setBusy(true);
    setRerunError(null);
    const result = await submitRerun(target, asking.key);
    setBusy(false);
    setAsking(null);
    if (result.error) {
      setRerunError(result.error);
      push(result.error, "error");
      return;
    }
    push(`Re-run queued — ${asking.label}, run ${result.generation ?? ""}`.trim(), "success");
    // The server render now carries a `pending` row; the poll restarts from it.
    router.refresh();
  }

  return (
    <div className="workflow-steps-panel">
      {visible.length ? (
        <ol className="workflow-steps" aria-label="Workflow steps">
          {visible.map((s) => (
            <li key={s.key} className={`workflow-step ${s.state}`}>
              <span
                className={`run-step-dot ${dotClass(s)}${s.gate ? " gate" : ""}`}
                aria-hidden="true"
              />
              <span className="workflow-step-label">{s.label}</span>
              <span className="workflow-step-state">{stateWord(s)}</span>
              {s.run && s.run.attempts > 1 ? (
                <span className="meta">attempt {s.run.attempts}</span>
              ) : null}
              {s.run && s.run.generation > 1 ? (
                <span className="meta">run {s.run.generation}</span>
              ) : null}
              {s.run?.started_ts && !s.gate && (s.state === "running" || s.state === "done") ? (
                <span className="meta mono">{duration(s.run.started_ts, s.run.finished_ts)}</span>
              ) : null}
              {canRerun && rerunnable(s) ? (
                <button
                  type="button"
                  className="btn-outline workflow-step-rerun"
                  onClick={() => setAsking(s)}
                  disabled={busy}
                  title={RERUN_CONSEQUENCE[s.key]}
                >
                  Re-run
                </button>
              ) : null}
              {s.run?.error && (s.state === "failed" || s.state === "skipped") ? (
                <span className="workflow-step-error mono small">{s.run.error}</span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
      {!canRerun && anyRerunnable ? (
        <p className="meta" style={{ margin: 0 }}>
          {RERUN_LOCKED_REASON}
        </p>
      ) : null}
      {rerunError ? <p className="alert error">{rerunError}</p> : null}
      {collapsible ? (
        <button
          type="button"
          className="btn-outline workflow-steps-toggle"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? "Hide unreached steps" : `Show all ${scoped.length} steps`}
        </button>
      ) : null}
      {enabled ? (
        <WaitNotice poll={poll} what={what} waiting={waiting} stalled={stalledCopy} />
      ) : null}

      <ConfirmDialog
        open={asking !== null}
        title={asking ? `Re-run ${asking.label.toLowerCase()}?` : "Re-run?"}
        confirmLabel="Re-run"
        busy={busy}
        onCancel={() => setAsking(null)}
        onConfirm={confirmRerun}
        consequence={
          asking
            ? (RERUN_CONSEQUENCE[asking.key] ?? RERUN_FALLBACK_CONSEQUENCE)
            : ""
        }
        audit={
          asking?.run ? (
            <span className="mono small">
              {asking.run.scope_kind} {asking.run.scope_id} · generation {asking.run.generation}{" "}
              → {asking.run.generation + 1}
              {asking.run.error ? ` · last error: ${asking.run.error}` : ""}
            </span>
          ) : undefined
        }
      />
    </div>
  );
}

function RERUN_STATES_HAS(state: string): boolean {
  return RERUNNABLE_STATES.has(state);
}
