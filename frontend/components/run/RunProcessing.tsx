"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import StatusWord from "@/components/StatusWord";
import {
  getUploadProgress,
  retryUpload,
  type StageState,
  type UploadProgress as ProgressPayload,
  type UploadStatus,
} from "@/lib/api";
import { isUploadInFlight, type StatusWord as StatusWordType } from "@/lib/statusWords";
import { usePoll } from "@/lib/usePoll";
import { useToast } from "@/lib/useToast";

const POLL_MS = 1500;

function stateWord(state: StageState): StatusWordType {
  switch (state) {
    case "pending":
      return "Expected";
    case "running":
      return "Processing";
    case "done":
      return "Completed";
    case "failed":
      return "Needs Attention";
  }
}

/** A climbing ":14" reads as working, not broken — the one thing dead time on
 *  this screen must never look like. Ticks for as long as something is
 *  running; there is no true server-side start timestamp for a stage, so this
 *  is wall-clock-since-first-seen-running, not the stage's actual duration. */
function useElapsedSeconds(running: boolean): number {
  const [seconds, setSeconds] = useState(0);
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    if (!running) {
      startRef.current = null;
      setSeconds(0);
      return;
    }
    startRef.current = Date.now();
    const id = setInterval(() => {
      setSeconds(Math.floor((Date.now() - (startRef.current ?? Date.now())) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [running]);

  return seconds;
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `:${String(s).padStart(2, "0")}`;
}

const RETRY_LABEL: Record<string, string> = {
  profile_failed: "Retry profiling",
  interpret_failed: "Retry interpreting",
};

/** S1 — the timeline while a file is parsed, profiled and interpreted.
 *
 *  The deterministic profile is rendered by the server page as soon as
 *  `detail.profile` exists (which can be well before this component's poll
 *  settles) — this component only owns the live timeline and the failure
 *  surface. It calls `router.refresh()` twice, not once: as soon as profiling
 *  finishes (so the page's own facts panel appears while the LLM is still
 *  running — "never show dead time"), and again once the whole poll settles
 *  (which hands off to the server guard in `processing/page.tsx` to redirect
 *  into S2 the moment the control plane says `interpreted`). */
export default function RunProcessing({
  uploadId,
  initialStatus,
  initialError,
}: {
  uploadId: string;
  initialStatus: UploadStatus;
  initialError: string | null;
}) {
  const router = useRouter();
  const { push } = useToast();
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const refreshedForProfile = useRef(false);

  const enabled = isUploadInFlight(initialStatus) || retrying;

  const progress = usePoll<ProgressPayload>(
    () => getUploadProgress(uploadId),
    {
      enabled,
      intervalMs: POLL_MS,
      isSettled: (next) => !isUploadInFlight(next.status),
      onSettle: () => router.refresh(),
      onTick: (next) => {
        const profileStage = next.stages.find((s) => s.key === "profile");
        if (profileStage?.state === "done" && !refreshedForProfile.current) {
          refreshedForProfile.current = true;
          router.refresh();
        }
      },
    },
    [uploadId, enabled],
  );

  const runningStageKey = progress?.stages.find((s) => s.state === "running")?.key ?? null;
  const runningStepNode = progress?.stages
    .find((s) => s.key === "interpret")
    ?.steps?.find((step) => step.state === "running")?.node;
  const elapsed = useElapsedSeconds(runningStageKey !== null);

  const status = progress?.status ?? initialStatus;
  const error = progress?.error ?? initialError;
  const retryTopic = RETRY_LABEL[status];

  async function handleRetry() {
    setRetryError(null);
    const result = await retryUpload(uploadId);
    if (result.error) {
      setRetryError(result.error);
      push(result.error, "error");
      return;
    }
    refreshedForProfile.current = false;
    setRetrying(true);
    push("Retry queued.", "success");
  }

  if (retryTopic) {
    return (
      <div className="card" style={{ marginTop: 14 }}>
        <p className="alert error">
          {error ?? "This step failed."} The original file is preserved and nothing was written
          to the data plane beyond what already succeeded.
        </p>
        <div className="run-processing-actions">
          <button type="button" className="btn-dark" onClick={handleRetry} disabled={retrying}>
            {retrying ? "Retrying…" : retryTopic}
          </button>
          <a href="/data/intake/new" className="btn-outline">
            Back to intake
          </a>
        </div>
        {retryError ? <p className="alert error">{retryError}</p> : null}
      </div>
    );
  }

  if (!isUploadInFlight(initialStatus) && !progress) return null;

  if (!progress) {
    return <p className="empty">Checking progress…</p>;
  }

  return (
    <div className="card run-timeline" style={{ marginTop: 14 }} aria-live="polite">
      <span className="panel-label">Processing</span>
      <ul className="run-timeline-list">
        {progress.stages
          .filter((stage) => stage.key !== "land")
          .map((stage) => (
            <li key={stage.key}>
              <StatusWord word={stateWord(stage.state)} />
              {" "}
              {stage.label}
              {stage.state === "running" && stage.key === runningStageKey && !runningStepNode ? (
                <span className="run-elapsed mono">{formatElapsed(elapsed)}</span>
              ) : null}
              {stage.steps ? (
                <ul className="run-timeline-steps">
                  {stage.steps.map((step) => (
                    <li key={step.node} className="meta">
                      <StatusWord word={stateWord(step.state)} /> {step.label}
                      {step.state === "running" ? (
                        <span className="run-elapsed mono">{formatElapsed(elapsed)}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
      </ul>
    </div>
  );
}
