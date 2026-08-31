import { ActionSurfacePanel } from "@/components/ActionSurfacePanel";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { EmptyState } from "@/components/ui/EmptyState";
import { PanelTabs } from "@/components/ui/PanelTabs";
import { attempt, isRefused } from "@/lib/api";
import type { ActionSurface, Rows } from "@/lib/types";

export const PANELS = ["stages", "inputs", "errors", "quarantine", "recon", "drift"] as const;
export type Panel = (typeof PANELS)[number];

export function asPanel(value: string | undefined): Panel {
  return (PANELS as readonly string[]).includes(value ?? "") ? (value as Panel) : "recon";
}

/**
 * The one drawer's body — five panels, five certified tools, no private query.
 *
 * Rendered by BOTH the intercepted overlay and the standalone page, from this
 * one file. That is the point: a shared link and a clicked row must show the
 * same thing, or the URL stops being a reliable way to point at a fact.
 *
 * Every panel is served by the same tool the agent calls, so a figure on this
 * screen and a figure in an answer cannot disagree.
 */
export async function BatchPanels({
  batchId,
  panel,
  drop,
  outcome,
  headline,
}: {
  batchId: string;
  panel: Panel;
  drop?: string;
  /** CF-V2-E12-03. Set only right after an action's form redirects back —
   *  the same "outcome in the URL, rendered once" shape `deliverFile` and
   *  `incidents/actions.ts` already use, so a refresh does not repost. */
  outcome?: string;
  headline?: string;
}) {
  const [result, surface] = await Promise.all([
    attempt<Rows>(`/api/batches/${encodeURIComponent(batchId)}/${panel}`),
    attempt<ActionSurface>(`/api/operations/batches/${encodeURIComponent(batchId)}/actions`),
  ]);

  return (
    <>
      <h1>Batch {batchId}</h1>
      <p className="lede">
        <CitationChip citationId={`batch:${batchId}`} />
      </p>

      {outcome ? (
        <div className="card outcome" data-outcome={outcome}>
          <strong className="outcome-word">{outcome}</strong>
          <p>{headline}</p>
        </div>
      ) : null}

      <PanelTabs
        panels={PANELS}
        current={panel}
        href={(p) => `/operations/control/batch/${batchId}?panel=${p}`}
      />

      {isRefused(result) ? (
        <RefusalNotice refusal={result} />
      ) : result.out_of_scope ? (
        <EmptyState kind="scope" what="this run" />
      ) : result.row_count === 0 ? (
        <EmptyState kind="recorded" what={result.tool} />
      ) : (
        <div className="card flush scroll">
          <table>
            <caption className="sr-only">
              {panel} for batch {batchId}
            </caption>
            <thead>
              <tr>
                {Object.keys(result.rows[0])
                  .filter((column) => column !== "citation_id")
                  .map((column) => (
                    <th key={column} scope="col">
                      {column.replace(/_/g, " ")}
                    </th>
                  ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, index) => {
                // The `#DQ-002` fragment of a citation lands on a ROW, not just
                // a panel. Without the highlight the deep link drops you beside
                // the fact instead of on it.
                const highlighted = Boolean(drop) && String(row.rule_id) === drop;
                return (
                  <tr
                    className="row"
                    key={index}
                    data-highlight={highlighted ? "true" : undefined}
                    style={
                      highlighted
                        ? {
                            outline: "2px solid var(--accent)",
                            outlineOffset: "-2px",
                            background: "var(--accent-wash)",
                          }
                        : undefined
                    }
                  >
                    {Object.entries(row)
                      .filter(([column]) => column !== "citation_id")
                      .map(([column, value]) => (
                        <td
                          key={column}
                          className={typeof value === "number" ? "num mono" : undefined}
                        >
                          {Array.isArray(value) ? value.join(", ") : String(value ?? "—")}
                        </td>
                      ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {result.note && (
            <p className="note" style={{ padding: "var(--s-3)" }}>
              {result.note}
            </p>
          )}
        </div>
      )}

      {isRefused(surface) ? (
        <div className="card">
          <strong>The action surface could not be read</strong>
          <RefusalNotice refusal={surface} />
        </div>
      ) : (
        <ActionSurfacePanel batchId={batchId} panel={panel} surface={surface} />
      )}
    </>
  );
}
