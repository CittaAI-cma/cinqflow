"use client";

import Confidence from "@/components/ui/Confidence";
import { evidenceClass } from "@/lib/evidence";
import type { MappingColumn } from "@/lib/api";

/** How much of a column the screen can show. PHI columns keep their name,
 *  type and shape and lose their examples — the profiler drops them and
 *  `mask_facts` drops them again, so this only has to say so. */
function Facts({ column }: { column: MappingColumn }) {
  return (
    <div className="col-facts">
      <span className="mono">{column.inferred_type}</span>
      {column.null_ratio > 0 ? (
        <span className="meta small">{Math.round(column.null_ratio * 100)}% empty</span>
      ) : null}
      {column.constant ? <span className="meta small">one value throughout</span> : null}
      {column.sentinel_count > 0 ? (
        <span className="meta small">
          {column.sentinel_count} placeholder{column.sentinel_count === 1 ? "" : "s"}
        </span>
      ) : null}
      {column.phi_masked ? (
        <span className="tag">PHI · examples withheld</span>
      ) : column.sample_values.length ? (
        <span className="col-samples">
          {column.sample_values.slice(0, 3).map((value) => (
            <span key={value} className="mono">
              {value}
            </span>
          ))}
        </span>
      ) : null}
    </div>
  );
}

/** The columns the batch has and the spec does not.
 *
 *  These were always computed and always discarded: the studio could render
 *  only what the spec carried, so the columns the model declined to place —
 *  precisely the ones needing a person — left no trace on the screen. An
 *  analyst could finish every visible row, see no unfinished work, and be
 *  refused at G2 for a required target sitting in a column they were never
 *  shown.
 *
 *  Taking a suggestion is one column, one act. Not a bulk "Auto Map All":
 *  `edited` becomes `decided_by: analyst` in the YAML that grounds the *next*
 *  feed's proposal, so a button that sets it across a table writes a claim
 *  nobody made into the knowledge base. Confidence pre-selects; a person
 *  still accepts.
 *
 *  A candidate whose target is already mapped from another column is offered
 *  as a *contest*, not a Take: the validator allows one source per target, so
 *  a Take button on both is a guaranteed whole-table 422. */
export default function UnplacedColumns({
  columns,
  takenTargets,
  onTake,
  requiredTargets,
}: {
  columns: MappingColumn[];
  /** target -> the source already claiming it, from the live form state. */
  takenTargets: Map<string, string>;
  onTake: (column: MappingColumn, target: string) => void;
  requiredTargets: Set<string>;
}) {
  if (!columns.length) return null;

  return (
    <div className="card" style={{ marginTop: 14 }}>
      <span className="panel-label">
        {columns.length} column{columns.length === 1 ? "" : "s"} in the batch, not in this mapping
      </span>
      <ul className="col-roster">
        {columns.map((column) => {
          const proposed = column.candidate?.target ?? null;
          const heldBy = proposed ? takenTargets.get(proposed) : undefined;
          const contested = Boolean(proposed && heldBy);
          return (
            <li key={column.name} className="col-row">
              <div className="col-identity">
                <span className="mono col-name">{column.name}</span>
                <Facts column={column} />
              </div>

              <div className="col-claim">
                {proposed ? (
                  <>
                    <span className="meta small">
                      {column.candidate?.concept ? `${column.candidate.concept} — ` : ""}proposed{" "}
                      <span className="mono">{proposed}</span>
                      {requiredTargets.has(proposed) ? (
                        <span className="tag danger">required</span>
                      ) : null}
                    </span>
                    <Confidence value={column.candidate?.confidence ?? 0} />
                    {column.candidate?.evidence.length ? (
                      <span className="evidence-list">
                        {column.candidate.evidence.map((item) => (
                          <span key={item} className={`evidence-chip ${evidenceClass(item)}`}>
                            {item}
                          </span>
                        ))}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="meta small">
                    {column.candidate?.rejected_target ? (
                      <>
                        the model named <span className="mono">{column.candidate.rejected_target}</span>
                        , which the canonical model does not have
                      </>
                    ) : (
                      (column.candidate?.reason ?? "no candidate — this one is yours to decide")
                    )}
                  </span>
                )}
              </div>

              <div className="col-act">
                {contested ? (
                  // Not a Take. One source per target is a validator rule, so
                  // offering the button here would build a spec the save
                  // refuses in full — the failure mode this whole session has
                  // been removing.
                  <span className="meta small">
                    <span className="mono">{proposed}</span> is already mapped from{" "}
                    <span className="mono">{heldBy}</span> — change that row first
                  </span>
                ) : proposed ? (
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => onTake(column, proposed)}
                    title={`Add ${column.name} → ${proposed} to this mapping`}
                  >
                    Take
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
