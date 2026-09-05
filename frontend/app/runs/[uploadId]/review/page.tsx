import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import GateActions from "@/components/GateActions";
import GateLocked from "@/components/run/GateLocked";
import RetryButton from "@/components/run/RetryButton";
import ReviewEvidence from "@/components/run/ReviewEvidence";
import VerdictCard from "@/components/run/VerdictCard";
import WorkflowSteps from "@/components/run/WorkflowSteps";
import StatusWord from "@/components/StatusWord";
import AnnounceOnMount from "@/components/ui/AnnounceOnMount";
import { getUpload } from "@/lib/api";
import { requireUser } from "@/lib/auth";
import { personaDefaults, RERUN_LOCKED_REASON } from "@/lib/persona";
import { loadRunSteps, resolveCanonical } from "@/lib/runProgress";
import { isStepViewable, runHref } from "@/lib/runStep";

export const dynamic = "force-dynamic";

/** S2 — "Is this file what it claims to be, and will I authorize it into
 *  Bronze?" The most important screen in the flow; everything else is
 *  transport. Renders three ways depending on where the control plane's
 *  state actually is:
 *
 *  - `interpreted`, undecided: the live gate — verdict, evidence, G1.
 *  - `rejected`: read-only, the decision record, and the one legal way
 *    forward (re-upload — reject has no other legal transition, states.py).
 *  - `approved` and later: read-only, the decision record, and whatever this
 *    phase can honestly offer past G1 — Bronze review (S4) once `landed`, or
 *    `/batches/{id}` while landing is still in flight or has failed (S3
 *    itself isn't built), with a retry in the latter case. Stays viewable
 *    (not redirected away) once canonical has moved past it - see
 *    `isStepViewable`.
 */
export default async function ReviewPage({
  params,
}: {
  params: Promise<{ uploadId: string }>;
}) {
  const { uploadId } = await params;

  let detail;
  try {
    detail = await getUpload(uploadId);
  } catch {
    notFound();
  }

  // Defence in depth, same as `submitDecision` itself: middleware already
  // gates this route on a session cookie, but the actual decided-by identity
  // for a still-undecided run comes from here, not a placeholder.
  const user = await requireUser();
  const defaults = personaDefaults(user.persona);

  const { upload, profile, interpretation, approvals, runs } = detail;

  const steps = await loadRunSteps(uploadId);
  const canonical = resolveCanonical(steps, upload.status);
  if (!isStepViewable("review", canonical)) {
    redirect(runHref(uploadId, canonical));
  }

  if (!profile || !interpretation) {
    // Reachable only if the control plane reports `interpreted` without both
    // artifacts on record, which the pipeline itself never does — treated as
    // "not ready" rather than asserted, since this page has no way to fix it.
    return (
      <p className="alert error">
        This run says <span className="mono">{upload.status}</span> but is missing its profile or
        interpretation. That should not happen — check the worker logs for upload{" "}
        <span className="mono">{uploadId}</span>.
      </p>
    );
  }

  const decision = approvals.find((a) => a.gate === "G1") ?? null;
  const landRun = runs.find((r) => r.kind === "land_bronze") ?? null;

  return (
    <div className="review-layout">
      <VerdictCard profile={profile} interpretation={interpretation} />

      <div className="review-right">
        <ReviewEvidence
          profile={profile}
          interpretation={interpretation}
          initialMode={defaults.readingMode}
        />

        {decision ? (
          <div className="card gate-box">
            <AnnounceOnMount storageKey={uploadId} />
            <span className="panel-label">Decision — G1</span>
            <p style={{ marginTop: 8 }}>
              <StatusWord word="Completed" />{" "}
              <span className={`tag${decision.decision === "rejected" ? " danger" : ""}`}>
                {decision.decision}
              </span>{" "}
              by <span className="mono">{decision.approver}</span> ·{" "}
              <span className="meta">{new Date(decision.decided_ts).toLocaleString()}</span>
            </p>
            {decision.note ? (
              <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: "8px 0 0" }}>
                {decision.note}
              </pre>
            ) : null}

            {decision.decision === "rejected" ? (
              <div className="run-processing-actions" style={{ marginTop: 12 }}>
                <p className="alert error" style={{ margin: 0, flex: "1 1 100%" }}>
                  Rejected — this is terminal. The file must be re-uploaded; nothing here can be
                  resubmitted.
                </p>
                <Link
                  href={`/data/intake/new?feed=${encodeURIComponent(upload.feed)}`}
                  className="btn-dark"
                >
                  Upload a corrected file
                </Link>
              </div>
            ) : landRun ? (
              <div className="run-processing-actions" style={{ marginTop: 12 }}>
                {/* Bronze review's own guard only opens once `landed` - while
                 *  landing is still running or has failed, the batch page's
                 *  `BatchProcessing` is what actually polls/handles that, so
                 *  this keeps pointing there until landing is real. */}
                <Link
                  href={
                    upload.status === "landed"
                      ? runHref(uploadId, "bronze")
                      : `/batches/${landRun.batch_id}`
                  }
                  className="btn-dark"
                >
                  Continue to Bronze
                </Link>
                {upload.status === "land_failed" ? (
                  <>
                    <p className="alert error" style={{ margin: 0, flex: "1 1 100%" }}>
                      {upload.error ?? "Landing failed."} The write was rolled back — Bronze has
                      no rows from this batch.
                    </p>
                    {user.capabilities.can_rerun_steps ? (
                      <RetryButton uploadId={uploadId} label="Retry landing" />
                    ) : (
                      <p className="meta" style={{ margin: 0, flex: "1 1 100%" }}>
                        {RERUN_LOCKED_REASON}
                      </p>
                    )}
                  </>
                ) : null}
              </div>
            ) : (
              <div style={{ marginTop: 12 }}>
                <WorkflowSteps
                  source={{ kind: "upload", uploadId }}
                  initial={steps}
                  only={["land"]}
                  expanded={defaults.workflowStepsExpanded}
                  canRerun={user.capabilities.can_rerun_steps}
                  what="landing to Bronze"
                  stalledCopy="The approval is recorded and the file is safe, but no worker has claimed the batch.land_bronze job. Nothing lands until one does — this page will pick it up on its own the moment that happens."
                />
              </div>
            )}
          </div>
        ) : user.capabilities.can_decide_gates ? (
          <GateActions
            uploadId={uploadId}
            filename={upload.filename}
            approverEmail={user.email}
            phiCount={profile.facts.phi_candidates.length}
            unknownCount={
              interpretation.content.signals.filter((s) => s.severity === "blocker").length
            }
          />
        ) : (
          <GateLocked gate="G1" />
        )}
      </div>
    </div>
  );
}
