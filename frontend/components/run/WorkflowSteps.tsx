"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import WaitNotice from "@/components/ui/WaitNotice";
import { getFeedVersionProgress, getUploadProgress, type StepProgress } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

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
 *  analyst pressed "Run preview") restarts the poll on its own.
 *
 *  Persona: `expanded` (Data Platform) shows unreached steps too; the analyst
 *  default shows only what has happened, with a toggle. `WaitNotice` keeps
 *  the wait honest - offline and stalled are distinct from "still going". */
export default function WorkflowSteps({
  source,
  initial,
  only,
  expanded = false,
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
  /** How long a queued/running step may sit before it reads as stalled. */
  stallAfterMs?: number;
  /** Named in the offline/stalled copy, e.g. "landing to Bronze". */
  what?: string;
  /** What a stall most likely means on this screen. */
  stalledCopy?: string;
}) {
  const router = useRouter();
  const [showAll, setShowAll] = useState(expanded);

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
              {s.run?.error && (s.state === "failed" || s.state === "skipped") ? (
                <span className="workflow-step-error mono small">{s.run.error}</span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
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
    </div>
  );
}
