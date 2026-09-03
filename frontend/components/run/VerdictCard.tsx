import type { Interpretation, Profile } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  observed_fact: "observed",
  governed_knowledge: "governed",
  inference: "inference",
  recommendation: "recommendation",
};

/** "Would I sign my name to this?" answered in one glance, without scrolling —
 *  computed straight from `ProfileFacts` and the claim counts, never
 *  requiring the analyst to read a claim to know how many there are. Sticky
 *  on wide viewports (`.verdict-card`, ≥1100px) so it stays visible while the
 *  evidence column, which can run long, scrolls past it. */
export default function VerdictCard({
  profile,
  interpretation,
}: {
  profile: Profile;
  interpretation: Interpretation;
}) {
  const { facts } = profile;
  const claims = interpretation.content.claims;
  const unknowns = interpretation.content.unknowns;
  const risks = interpretation.content.risks;

  const counts = claims.reduce<Record<string, number>>((acc, claim) => {
    acc[claim.kind] = (acc[claim.kind] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="card verdict-card">
      <span className="panel-label">Verdict</span>
      <p className="verdict-facts mono">
        {facts.row_count.toLocaleString()} rows · {facts.columns.length} columns ·{" "}
        {facts.phi_candidates.length} PHI candidates
        <br />
        key{" "}
        {facts.candidate_keys.length
          ? facts.candidate_keys.map((k) => k.join(" + ")).join(", ")
          : "none found"}{" "}
        · {facts.duplicate_rows} duplicate rows
      </p>
      <p className="verdict-claim-counts">
        {(["observed_fact", "governed_knowledge", "inference", "recommendation"] as const).map(
          (kind, index) => (
            <span key={kind}>
              {index > 0 ? " · " : ""}
              {counts[kind] ?? 0} {KIND_LABEL[kind]}
              {(counts[kind] ?? 0) === 1 ? "" : "s"}
            </span>
          ),
        )}
        {unknowns.length ? ` · ${unknowns.length} unknown${unknowns.length === 1 ? "" : "s"}` : ""}
      </p>
      {risks.length ? (
        <div className="verdict-risks">
          <span className="verdict-section-label">Risks</span>
          <ul>
            {risks.map((risk) => (
              <li key={risk} className="risk">
                {risk}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {unknowns.length ? (
        <p className="alert warn verdict-blockers">
          Blockers: {unknowns.length} unknown{unknowns.length === 1 ? "" : "s"} unresolved
        </p>
      ) : null}
    </div>
  );
}
