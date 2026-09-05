import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import BronzeAnalysisWait from "@/components/run/BronzeAnalysisWait";
import ProposalTable from "@/components/ProposalTable";
import { getBronzeProfile, getProposal, getUpload, type FieldStatus } from "@/lib/api";
import { requireUser } from "@/lib/auth";
import { personaDefaults } from "@/lib/persona";
import { canonicalStep, isStepViewable, runHref } from "@/lib/runStep";

export const dynamic = "force-dynamic";

//: The statuses that require an analyst decision, in the order S4 defaults
//: to showing them - matches `proposalStatusWord`/`FieldStatus`'s own vocabulary.
const NEEDS_DECISION: FieldStatus[] = ["invalid", "ambiguous", "unknown"];

/** S4 - "Did what landed match what I approved, and is the proposed mapping
 *  defensible?" Reconciliation first: because Bronze landing preserves every
 *  row, row counts match by construction, so this panel is really about
 *  columns/PHI/sampling - any mismatch there is a genuine alarm, not expected
 *  attrition. Then the AI proposal, filtered by default to what needs a
 *  decision. Bronze rows, quarantine and the full profile table stay on
 *  `/batches/{batchId}` - real forensic detail, never needed just to decide. */
export default async function BronzePage({
  params,
  searchParams,
}: {
  params: Promise<{ uploadId: string }>;
  searchParams: Promise<{ filter?: string }>;
}) {
  const { uploadId } = await params;
  const { filter } = await searchParams;

  let detail;
  try {
    detail = await getUpload(uploadId);
  } catch {
    notFound();
  }
  const { upload, profile, runs } = detail;
  const user = await requireUser();
  const defaults = personaDefaults(user.persona);

  if (!isStepViewable("bronze", canonicalStep(upload.status))) {
    redirect(runHref(uploadId, canonicalStep(upload.status)));
  }

  const landRun = runs.find((r) => r.kind === "land_bronze");
  if (!landRun) {
    // Guarded by isStepViewable above (only reachable once `landed`), but the
    // land run itself is discovered from `runs`, not asserted by status alone.
    return (
      <p className="alert error">
        This run is landed but has no <span className="mono">land_bronze</span> run on record.
        Check the worker logs for upload <span className="mono">{uploadId}</span>.
      </p>
    );
  }

  const [bronzeProfile, proposal] = await Promise.all([
    getBronzeProfile(landRun.batch_id),
    getProposal(landRun.batch_id),
  ]);

  // Persona default (analyst: what needs a decision; platform: everything),
  // overridable either way from the URL.
  const showAll =
    filter === "all" || (filter !== "decisions" && defaults.proposalFilter === "all");
  const decisionCount = proposal
    ? NEEDS_DECISION.reduce((n, status) => n + (proposal.counts?.[status] ?? 0), 0)
    : 0;
  const statuses = !showAll && decisionCount > 0 ? NEEDS_DECISION : undefined;
  const totalFields = proposal?.content.fields.length ?? 0;

  return (
    <>
      <h2>Bronze review</h2>

      <div className="card grid">
        <span className="panel-label">Reconciliation</span>
        <div className="row">
          <div>
            <label>Rows</label>
            <span className="mono">
              {profile?.facts.row_count.toLocaleString() ?? "—"} →{" "}
              {bronzeProfile?.rows_in_batch.toLocaleString() ?? "—"}
            </span>
          </div>
          <div>
            <label>Profiled</label>
            <span className="mono">
              {bronzeProfile
                ? `${bronzeProfile.rows_profiled.toLocaleString()} of ${bronzeProfile.rows_in_batch.toLocaleString()}`
                : "—"}
            </span>
          </div>
          <div>
            <label>Columns</label>
            <span className="mono">
              {profile?.facts.columns.length ?? "—"} → {bronzeProfile?.facts.columns.length ?? "—"}
            </span>
          </div>
          <div>
            <label>PHI candidates</label>
            <span className="mono">
              {profile?.facts.phi_candidates.length ?? "—"} →{" "}
              {bronzeProfile?.facts.phi_candidates.length ?? "—"}
            </span>
          </div>
        </div>
        {bronzeProfile?.is_sample ? (
          <p className="meta" style={{ marginTop: 8 }}>
            Profiled {bronzeProfile.rows_profiled.toLocaleString()} of{" "}
            {bronzeProfile.rows_in_batch.toLocaleString()} landed rows — every figure above is a
            sample statistic, not a full count.
          </p>
        ) : null}
      </div>

      {proposal ? (
        <>
          <p className="alert" style={{ borderColor: "var(--gate)", color: "var(--ink)" }}>
            <b>Advisory.</b> {proposal.content.headline} This proposal is not applied to
            anything — it becomes real only when you create a mapping version from it and approve
            that version at G2, and the version is yours, not the model's.
          </p>

          <h2>
            AI mapping proposal{" "}
            {statuses ? (
              <span className="meta">
                · showing the {decisionCount} field{decisionCount === 1 ? "" : "s"} that need a
                decision —{" "}
                <Link href={`${runHref(uploadId, "bronze")}?filter=all`}>
                  show all {totalFields}
                </Link>
              </span>
            ) : (
              <span className="meta">
                · showing all {totalFields} fields
                {decisionCount > 0 ? (
                  <>
                    {" — "}
                    <Link href={`${runHref(uploadId, "bronze")}?filter=decisions`}>
                      only the {decisionCount} needing a decision
                    </Link>
                  </>
                ) : null}
              </span>
            )}
          </h2>
          <ProposalTable proposal={proposal} statuses={statuses} />
        </>
      ) : (
        <BronzeAnalysisWait batchId={landRun.batch_id} />
      )}

      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "baseline", marginTop: 14 }}
      >
        <Link href={`/batches/${landRun.batch_id}`} className="meta">
          Forensic — Bronze rows, quarantine and the full profile table →
        </Link>
        {proposal ? (
          <Link
            href={`${runHref(uploadId, "mapping")}?proposal=${proposal.proposal_id}`}
            className="btn-dark"
          >
            Build the mapping
          </Link>
        ) : null}
      </div>
    </>
  );
}
