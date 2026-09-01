import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * One published recovery guide, by guide_id. The destination a
 * `runbook:<id>` citation opens (CF-V1-W1-25) — `RecoveryGuide.citation`
 * points here, and so does every per-step `runbook:<id>#step-<n>` citation
 * `workers.knowledge.chunk_runbook` produces for the knowledge plane.
 *
 * A step fragment scrolls to that step rather than opening a page of its
 * own — one depth level, like every other citation with a fragment.
 */
type Runbook = {
  guide_id: string;
  title: string;
  steps: string[];
  signatures: string[];
  remedy: string | null;
  is_transient: boolean;
  version: number;
  lifecycle_state: string;
  status: string;
};

export default async function RunbookPage({
  params,
}: {
  params: Promise<{ runbookId: string }>;
}) {
  const { runbookId } = await params;
  const result = await attempt<Runbook>(`/api/operations/runbooks/${encodeURIComponent(runbookId)}`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / runbook {runbookId}
      </p>
      <h1>{isRefused(result) ? runbookId : result.title}</h1>
      <p className="lede">
        <CitationChip citationId={`runbook:${runbookId}`} />
      </p>

      {isRefused(result) ? (
        <RefusalNotice refusal={result} />
      ) : (
        <>
          <p className="note">
            v{result.version} · {result.lifecycle_state}
            {result.is_transient ? " · transient failure" : ""}
          </p>

          <h2>Steps</h2>
          <ol>
            {result.steps.map((step, index) => (
              <li id={`step-${index + 1}`} key={`${result.guide_id}-step-${index + 1}`}>
                {step}
              </li>
            ))}
          </ol>

          {result.signatures.length > 0 ? (
            <p className="note">
              Covers {result.signatures.length} failure signature
              {result.signatures.length === 1 ? "" : "s"}.
            </p>
          ) : null}

          {result.remedy ? (
            <div className="card">
              <strong>Proposed remedy</strong>
              <p className="note">{result.remedy} — pressed on the action surface, never here.</p>
            </div>
          ) : null}
        </>
      )}
    </>
  );
}
