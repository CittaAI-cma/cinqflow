import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Rows } from "@/lib/types";

const PANELS = ["stages", "inputs", "errors", "quarantine", "recon"] as const;
type Panel = (typeof PANELS)[number];

/**
 * THE drawer. Five panels, five certified tools, no private query.
 *
 * Every panel is served by the same tool the agent calls, so a figure on this
 * screen and a figure in an answer cannot disagree — and a citation like
 * `recon:8842#DQ-002` lands exactly here, on the row it cites.
 */
export default async function BatchDrawer({
  params,
  searchParams,
}: {
  params: Promise<{ batchId: string }>;
  searchParams: Promise<{ panel?: string; drop?: string }>;
}) {
  const { batchId } = await params;
  const { panel = "recon", drop } = await searchParams;
  const chosen: Panel = (PANELS as readonly string[]).includes(panel)
    ? (panel as Panel)
    : "recon";

  const result = await attempt<Rows>(
    `/api/batches/${encodeURIComponent(batchId)}/${chosen}`,
  );

  return (
    <>
      <p className="note">
        <Link href="/operations/control">Control Operations</Link> / batch {batchId}
      </p>
      <h1>Batch {batchId}</h1>
      <p className="lede">
        <CitationChip citationId={`batch:${batchId}`} />
      </p>

      <div className="tabs">
        {PANELS.map((p) => (
          <Link
            key={p}
            href={`/operations/control/batch/${batchId}?panel=${p}`}
            aria-current={p === chosen ? "page" : undefined}
            data-panel={p}
          >
            {p}
          </Link>
        ))}
      </div>

      {isRefused(result) ? (
        <RefusalNotice refusal={result} />
      ) : result.out_of_scope ? (
        <div className="card note">
          Nothing to show. This run is either not recorded, or not one you have access to —
          the platform deliberately answers both the same way.
        </div>
      ) : result.row_count === 0 ? (
        <div className="card note">
          Nothing recorded for <span className="mono">{result.tool}</span> yet. That is a
          fact, not a gap to fill in.
        </div>
      ) : (
        <div className="card scroll">
          <table>
            <thead>
              <tr>
                {Object.keys(result.rows[0])
                  .filter((column) => column !== "citation_id")
                  .map((column) => (
                    <th key={column}>{column.replace(/_/g, " ")}</th>
                  ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, index) => (
                <tr
                  className="row"
                  key={index}
                  data-highlight={drop && String(row.rule_id) === drop ? "true" : undefined}
                  style={
                    drop && String(row.rule_id) === drop
                      ? { outline: "1px solid var(--accent)" }
                      : undefined
                  }
                >
                  {Object.entries(row)
                    .filter(([column]) => column !== "citation_id")
                    .map(([column, value]) => (
                      <td key={column} className={typeof value === "number" ? "mono" : undefined}>
                        {Array.isArray(value) ? value.join(", ") : String(value ?? "—")}
                      </td>
                    ))}
                </tr>
              ))}
            </tbody>
          </table>
          {result.note && <p className="note">{result.note}</p>}
        </div>
      )}

      <div className="card">
        <strong>This drawer has no write buttons</strong>
        <p className="note">
          Retry, pause and reprocess are Wave 1 (CF-V1-E16-06), where they arrive as proposals
          a human approves. A button that does not exist here is also refused at the server.
        </p>
      </div>
    </>
  );
}
