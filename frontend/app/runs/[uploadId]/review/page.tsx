import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import GateActions from "@/components/GateActions";
import RetryButton from "@/components/run/RetryButton";
import ReviewEvidence from "@/components/run/ReviewEvidence";
import VerdictCard from "@/components/run/VerdictCard";
import StatusWord from "@/components/StatusWord";
import AnnounceOnMount from "@/components/ui/AnnounceOnMount";
import { getUpload } from "@/lib/api";
import { canonicalStep, runHref } from "@/lib/runStep";

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
 *    phase can honestly offer past G1 — `/batches/{id}` once a run exists
 *    (built in an earlier phase; S3 itself isn't built yet), or a retry if
 *    landing failed.
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

  const { upload, profile, interpretation, approvals, runs } = detail;

  if (canonicalStep(upload.status) !== "review") {
    redirect(runHref(uploadId, canonicalStep(upload.status)));
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
        <ReviewEvidence profile={profile} interpretation={interpretation} />

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
                <Link href={`/batches/${landRun.batch_id}`} className="btn-dark">
                  Continue to Bronze
                </Link>
                {upload.status === "land_failed" ? (
                  <>
                    <p className="alert error" style={{ margin: 0, flex: "1 1 100%" }}>
                      {upload.error ?? "Landing failed."} The write was rolled back — Bronze has
                      no rows from this batch.
                    </p>
                    <RetryButton uploadId={uploadId} label="Retry landing" />
                  </>
                ) : null}
              </div>
            ) : (
              <p className="empty" style={{ marginTop: 12 }}>
                Approved. Landing to Bronze is queued — run{" "}
                <span className="mono">make worker</span> and reload.
              </p>
            )}
          </div>
        ) : (
          <GateActions
            uploadId={uploadId}
            filename={upload.filename}
            phiCount={profile.facts.phi_candidates.length}
            unknownCount={
              interpretation.content.signals.filter((s) => s.severity === "blocker").length
            }
          />
        )}
      </div>
    </div>
  );
}
