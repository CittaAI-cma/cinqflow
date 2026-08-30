import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { Tag } from "@/components/Tag";
import { attempt, isRefused } from "@/lib/api";
import { checkMark, checkTone, verdictTone } from "@/lib/certification";
import type { Certification, Principal } from "@/lib/types";

/**
 * CF-V2-E13-04 — one batch's certification, computed on THIS read from
 * retained history: recon rows, rule verdicts, drift, the SLA cycle and the
 * variance ledger. No route anywhere sets this; a test holds that absence
 * over the OpenAPI document, and this page renders whatever `certify()`
 * actually returned rather than a status somebody chose.
 */
export default async function CertificationDetail({
  params,
}: {
  params: Promise<{ batchId: string }>;
}) {
  const { batchId } = await params;
  const [certification, me] = await Promise.all([
    attempt<Certification>(`/api/operations/batches/${encodeURIComponent(batchId)}/certification`),
    attempt<Principal>("/api/me"),
  ]);
  if (isRefused(certification)) return <RefusalNotice refusal={certification} />;

  const mayExport = !isRefused(me) && me.permitted_actions.includes("certify_export");

  return (
    <>
      <p className="note">
        <Link href="/operations/certification">Certification</Link> / batch {batchId}
      </p>
      <h1>Batch {batchId}</h1>
      <p className="lede">
        <Tag tone={verdictTone(certification.verdict)}>{certification.verdict}</Tag>
        {certification.derived_ts ? (
          <span className="note"> · derived {certification.derived_ts.slice(0, 19).replace("T", " ")}</span>
        ) : null}
      </p>
      <p className="note">
        {certification.publishable
          ? "Publishable to Silver ODS."
          : "Not publishable to Silver ODS while the verdict stands."}
      </p>

      <div className="card flush scroll">
        <table>
          <caption className="sr-only">Certification checks for batch {batchId}</caption>
          <thead>
            <tr>
              <th scope="col">Check</th>
              <th scope="col">Result</th>
              <th scope="col">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {certification.checks.map((check) => (
              <tr className="row" key={check.kind}>
                <td className="mono">{check.kind}</td>
                <td>
                  <Tag tone={checkTone(check)}>{checkMark(check)}</Tag>
                </td>
                <td>{check.evidence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {certification.variances.length > 0 ? (
        <div className="card">
          <strong>Variances</strong>
          <ul className="tree-list">
            {certification.variances.map((variance) => (
              <li key={variance.variance_id}>
                {variance.kind} — {variance.outcome}
                {variance.waiver_reason ? (
                  <span className="note"> · waived: {variance.waiver_reason}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="note">No variances recorded against this batch.</p>
      )}

      <p className="inline action-row">
        {mayExport ? (
          <a className="action" href={`/operations/certification/batch/${batchId}/export`}>
            Export evidence →
          </a>
        ) : (
          <span className="note">
            Exporting evidence is <span className="mono">certify_export</span>. Your role can read
            this page but not hand it to a payer.
          </span>
        )}
      </p>
    </>
  );
}
