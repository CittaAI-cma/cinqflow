import { notFound, redirect } from "next/navigation";
import Kpi from "@/components/Kpi";
import RunProcessing from "@/components/run/RunProcessing";
import { getUpload } from "@/lib/api";
import { canonicalStep, runHref } from "@/lib/runStep";

export const dynamic = "force-dynamic";

/** S1 — "Is it working, and is it stuck?"
 *
 *  Facts render the moment `profile` exists, which can be well before this
 *  upload leaves "in flight": profiling takes ~1s, interpretation is a
 *  30–60s LLM call, and the artifact's rule is that dead time never renders
 *  as nothing happening. `RunProcessing` (client) owns the live timeline and
 *  calls back into this server render (`router.refresh()`) both when
 *  profiling finishes and again once the poll settles. */
export default async function ProcessingPage({
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

  const { upload, profile } = detail;

  // No URL may show a step ahead of the control plane. Once interpretation
  // has settled (one way or another) this run belongs on the review screen.
  if (canonicalStep(upload.status) !== "processing") {
    redirect(runHref(uploadId, canonicalStep(upload.status)));
  }

  return (
    <>
      {profile ? (
        <>
          <h2>
            Deterministic profile{" "}
            <span className="meta">
              · computed by code · profiler v{profile.profiler_version}
            </span>
          </h2>
          <Kpi
            items={[
              { key: "rows", value: profile.facts.row_count.toLocaleString(), label: "rows" },
              { key: "columns", value: profile.facts.columns.length, label: "columns" },
              { key: "dupes", value: profile.facts.duplicate_rows, label: "duplicate rows" },
              {
                key: "keys",
                value: profile.facts.candidate_keys.length
                  ? profile.facts.candidate_keys.map((k) => k.join(" + ")).join(", ")
                  : "none",
                label: "candidate key",
              },
              { key: "phi", value: profile.facts.phi_candidates.length, label: "PHI candidates" },
            ]}
          />
        </>
      ) : (
        <p className="meta">Parsing and profiling the file…</p>
      )}

      <RunProcessing
        uploadId={uploadId}
        initialStatus={upload.status}
        initialError={upload.error}
      />
    </>
  );
}
