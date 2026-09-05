"use client";

import { useState } from "react";
import ClaimCard from "@/components/ClaimCard";
import ReadingMode, { type ReadingModeKey } from "@/components/run/ReadingMode";
import RecommendedFields from "@/components/run/RecommendedFields";
import SignalCard from "@/components/run/SignalCard";
import CollapsibleSection from "@/components/ui/CollapsibleSection";
import type { ColumnFacts, ColumnRole, Interpretation, Profile } from "@/lib/api";
import { ROLE_LABEL, groupByRole, rolesByColumn } from "@/lib/columnRoles";

/** The evidence column, right of the sticky `VerdictCard` — claims already
 *  arrive from the API in trust-ladder order (facts, then governed
 *  knowledge, then inferences, then recommendations), so this only filters
 *  by reading mode, it never re-sorts. Signals (risks/unknowns) render above
 *  the claims and in every mode, Verdict included — they require a decision,
 *  which is the one thing a reading mode must never hide
 *  (docs/blueprints/analyst-forward-flow.md §S2, "Risks and unknowns go
 *  ABOVE the claims").
 *
 *  PR-7: Evidence and Verdict open with the recommended fields (the
 *  high-importance columns, by role) between the signals and the claims;
 *  Forensic groups every column by role, the platform's own `technical`
 *  columns collapsed by default for the Data Analyst persona, and each row
 *  carries the v2 facts (null ratio, range, top values - never for PHI). A
 *  142-column ADT file becomes seven groups. Facts are never hidden: every
 *  column is reachable here. */
export default function ReviewEvidence({
  profile,
  interpretation,
  initialMode = "evidence",
  collapseTechnical = true,
}: {
  profile: Profile;
  interpretation: Interpretation;
  /** The persona's default (`lib/persona.ts`). `ReadingMode` restores the
   *  analyst's own saved choice on mount, which still wins over this. */
  initialMode?: ReadingModeKey;
  /** Persona default (`technicalCollapsed`): start the Forensic `technical`
   *  group closed. Data Platform sees everything open. */
  collapseTechnical?: boolean;
}) {
  const [mode, setMode] = useState<ReadingModeKey>(initialMode);
  const { claims, signals } = interpretation.content;
  const visibleClaims = mode === "verdict" ? claims.filter((c) => c.kind === "recommendation") : claims;
  // Bookkeeping about discarded model output never earns the analyst's
  // attention on its own — it's forensic detail, not a decision.
  const visibleSignals = mode === "forensic" ? signals : signals.filter((s) => s.severity !== "info");

  const roles = rolesByColumn(profile, interpretation);
  const groups = groupByRole(profile.facts.columns, roles);

  return (
    <div className="review-evidence">
      <ReadingMode mode={mode} onChange={setMode} />

      {visibleSignals.map((signal, index) => (
        <SignalCard key={`${signal.kind}-${index}`} signal={signal} />
      ))}

      {mode !== "forensic" ? <RecommendedFields roles={Object.values(roles)} /> : null}

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
          <p className="meta" style={{ marginTop: 6 }}>
            {profile.facts.columns.length} columns in {groups.length} role group
            {groups.length === 1 ? "" : "s"} · roles judged by the model against the profiler&apos;s
            rules; a row marked <span className="meta">from the profiler</span> is the rule alone.
          </p>
          {groups.map((group) => (
            <CollapsibleSection
              key={group.role}
              title={`${ROLE_LABEL[group.role]} · ${group.columns.length}`}
              defaultOpen={!(group.role === "technical" && collapseTechnical)}
            >
              <ForensicTable columns={group.columns} roles={roles} />
            </CollapsibleSection>
          ))}
          <p className="meta mono" style={{ marginTop: 10 }}>
            interpretation {interpretation.interpretation_id.slice(0, 12)}… (v
            {interpretation.version}) · profile {profile.profile_id.slice(0, 12)}… · profiler v
            {profile.profiler_version}
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

function range(column: ColumnFacts): string {
  if (column.phi_candidate) return "•••• masked";
  if (column.min == null && column.max == null) return "—";
  return `${column.min ?? "?"} → ${column.max ?? "?"}`;
}

function topValues(column: ColumnFacts): string {
  if (column.phi_candidate) return "•••• masked";
  const top = column.top_values ?? [];
  if (!top.length) return column.sample_values.slice(0, 3).join(" · ") || "—";
  return top
    .slice(0, 3)
    .map((t) => `${t.value} ×${t.count.toLocaleString()}`)
    .join(" · ");
}

function ForensicTable({
  columns,
  roles,
}: {
  columns: ColumnFacts[];
  roles: Record<string, ColumnRole>;
}) {
  return (
    <div className="card scroll forensic-group" style={{ padding: 0, marginTop: 10 }}>
      <table>
        <thead>
          <tr>
            <th>Column</th>
            <th>Type</th>
            <th className="num">Nulls</th>
            <th className="num">Distinct</th>
            <th>Range</th>
            <th>Top values</th>
            <th>Importance</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((column) => {
            const role = roles[column.name];
            return (
              <tr key={column.name}>
                <td className="mono">
                  {column.name}{" "}
                  {column.phi_candidate ? <span className="tag phi">PHI</span> : null}
                  {column.constant ? (
                    <span className="tag" title="One value across every populated row">
                      constant
                    </span>
                  ) : null}
                  {(column.sentinel_count ?? 0) > 0 ? (
                    <span
                      className="tag danger"
                      title="Placeholder values such as 1900-01-01 or 9999-12-31"
                    >
                      {column.sentinel_count} sentinel{column.sentinel_count === 1 ? "" : "s"}
                    </span>
                  ) : null}
                </td>
                <td className="mono">{column.inferred_type}</td>
                <td className="num">
                  {column.null_count.toLocaleString()}
                  {column.null_ratio !== undefined ? (
                    <span className="meta"> · {(column.null_ratio * 100).toFixed(1)}%</span>
                  ) : null}
                </td>
                <td className="num">{column.distinct_count.toLocaleString()}</td>
                <td className="mono small">{range(column)}</td>
                <td className="mono small">{topValues(column)}</td>
                <td>
                  {role ? (
                    <>
                      <span className={`importance ${role.importance}`}>{role.importance}</span>
                      <div className="meta small">
                        {role.reason}
                        {role.source === "hint" ? " · from the profiler" : ""}
                      </div>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
