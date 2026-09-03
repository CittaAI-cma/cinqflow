import EvidenceList from "@/components/EvidenceList";
import type { Claim } from "@/lib/api";

/** The trust ladder's rule made visible: a fact or a citation is read, not
 *  scored — confidence is only shown for the two kinds the model actually
 *  reasoned its way to. The API still carries a `confidence` number on every
 *  claim (the schema requires one), but showing it on an `observed_fact`
 *  would imply the profiler is ever unsure of a byte count, which it isn't. */
const SCORED_KINDS = new Set(["inference", "recommendation"]);

export default function ClaimCard({ claim }: { claim: Claim }) {
  const scored = SCORED_KINDS.has(claim.kind);
  return (
    <div className="claim">
      <div className="claim-head">
        <span className={`claim-kind ${claim.kind}`}>{claim.kind.replace(/_/g, " ")}</span>
        <span className="claim-field">{claim.field}</span>
        {scored ? (
          <>
            <span className="confidence-bar">
              <i style={{ width: `${Math.round(claim.confidence * 100)}%` }} />
            </span>
            <span className="confidence-value">{claim.confidence.toFixed(2)}</span>
          </>
        ) : null}
      </div>
      <div className="claim-value">{claim.value}</div>
      <EvidenceList items={claim.evidence} />
    </div>
  );
}
