"use client";

import { useState } from "react";
import ClaimCard from "@/components/ClaimCard";
import ReadingMode, { type ReadingModeKey } from "@/components/run/ReadingMode";
import type { Interpretation, Profile } from "@/lib/api";

/** The evidence column, right of the sticky `VerdictCard` — claims already
 *  arrive from the API in trust-ladder order (facts, then governed
 *  knowledge, then inferences, then recommendations), so this only filters
 *  by reading mode, it never re-sorts. */
export default function ReviewEvidence({
  profile,
  interpretation,
}: {
  profile: Profile;
  interpretation: Interpretation;
}) {
  const [mode, setMode] = useState<ReadingModeKey>("evidence");
  const claims = interpretation.content.claims;
  const visibleClaims = mode === "verdict" ? claims.filter((c) => c.kind === "recommendation") : claims;

  return (
    <div className="review-evidence">
      <ReadingMode mode={mode} onChange={setMode} />

      {visibleClaims.length ? (
        visibleClaims.map((claim, index) => (
          <ClaimCard key={`${claim.field}-${index}`} claim={claim} />
        ))
      ) : (
        <p className="meta">No recommendations from the model on this file.</p>
      )}

      {mode === "forensic" ? (
        <div className="card" style={{ marginTop: 14 }}>
          <span className="panel-label">Forensic detail</span>
          <div className="card scroll" style={{ padding: 0, marginTop: 10 }}>
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th className="num">Nulls</th>
                  <th className="num">Distinct</th>
                  <th>Sample values</th>
                </tr>
              </thead>
              <tbody>
                {profile.facts.columns.map((column) => (
                  <tr key={column.name}>
                    <td className="mono">
                      {column.name}{" "}
                      {column.phi_candidate ? <span className="tag phi">PHI</span> : null}
                    </td>
                    <td className="mono">{column.inferred_type}</td>
                    <td className="num">{column.null_count.toLocaleString()}</td>
                    <td className="num">{column.distinct_count.toLocaleString()}</td>
                    <td className="mono small">
                      {column.phi_candidate
                        ? <span className="unc">•••• masked</span>
                        : column.sample_values.slice(0, 3).join(" · ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="meta mono" style={{ marginTop: 10 }}>
            interpretation {interpretation.interpretation_id.slice(0, 12)}… (v
            {interpretation.version}) · profile {profile.profile_id.slice(0, 12)}…
          </p>
          <p className="meta mono">
            prompt {interpretation.provenance.prompt} · model {interpretation.provenance.model} ·
            knowledge {interpretation.provenance.knowledge.join(", ") || "none"}
          </p>
        </div>
      ) : null}
    </div>
  );
}
