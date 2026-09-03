import Link from "next/link";
import { notFound } from "next/navigation";
import BatchProcessing from "@/components/batch/BatchProcessing";
import Kpi from "@/components/Kpi";
import LineageChain from "@/components/LineageChain";
import ProposalTable from "@/components/ProposalTable";
import StatusWord from "@/components/StatusWord";
import EmptyState from "@/components/ui/EmptyState";
import { getBatchRows, getBatchQuarantine, getBronzeProfile, getLineage, getProposal } from "@/lib/api";
import { runStatusWord } from "@/lib/statusWords";

export const dynamic = "force-dynamic";

export default async function BatchPage({
  params,
}: {
  params: Promise<{ batchId: string }>;
}) {
  const { batchId } = await params;

  let lineage;
  let bronze;
  try {
    [lineage, bronze] = await Promise.all([getLineage(batchId), getBatchRows(batchId)]);
  } catch {
    notFound();
  }
  const [bronzeProfile, proposal, quarantine] = await Promise.all([
    getBronzeProfile(batchId),
    getProposal(batchId),
    getBatchQuarantine(batchId),
  ]);

  const { chain, run, promotion, gates } = lineage;
  const columns = bronze.rows.length ? Object.keys(bronze.rows[0].raw_row) : [];

  return (
    <>
      <p className="meta">
        <Link href={`/uploads/${chain.upload_id}`}>← Back to upload</Link>
      </p>

      <h2 style={{ marginTop: 12 }}>
        Batch <span className="mono">{batchId}</span>{" "}
        <StatusWord word={runStatusWord(run)} raw={run?.state} />
      </h2>

      <Kpi
        items={[
          { key: "in", value: run?.counts?.records_in.toLocaleString() ?? "—", label: "records in" },
          { key: "out", value: run?.counts?.records_out.toLocaleString() ?? "—", label: "records out" },
          { key: "q", value: run?.counts?.quarantined.toLocaleString() ?? "—", label: "quarantined" },
          {
            key: "drops",
            value: run?.counts?.attributed_drops.toLocaleString() ?? "—",
            label: "attributed drops",
          },
          {
            key: "balanced",
            value: run?.balanced === null || run?.balanced === undefined ? "—" : String(run.balanced),
            label: "in = out + q + drops",
            tone: run?.balanced ? "ok" : run?.balanced === false ? "danger" : undefined,
          },
        ]}
      />
      {run?.error ? <p className="alert error mono">{run.error}</p> : null}

      <BatchProcessing
        batchId={batchId}
        initialLandRun={run}
        initialPromotionRun={promotion}
        hasProposal={proposal !== null}
      />

      <h2>Lineage</h2>
      <div className="card scroll">
        <LineageChain chain={chain} gates={gates} />
      </div>

      {promotion ? (
        <>
          <h2>
            Silver Raw{" "}
            <span className="meta">
              · mapping v{promotion.mapping_version} · executed in code, no model involved ·{" "}
              <StatusWord word={runStatusWord(promotion)} raw={promotion.state} />
            </span>
          </h2>
          <Kpi
            items={[
              { key: "read", value: promotion.counts?.records_in.toLocaleString() ?? "—", label: "rows read" },
              { key: "promoted", value: promotion.counts?.records_out.toLocaleString() ?? "—", label: "promoted" },
              {
                key: "q",
                value: promotion.counts?.quarantined.toLocaleString() ?? "—",
                label: "quarantined",
              },
              {
                key: "drops",
                value: promotion.counts?.attributed_drops.toLocaleString() ?? "—",
                label: "nothing to write",
              },
              {
                key: "balanced",
                value: String(promotion.balanced),
                label: "balanced",
                tone: promotion.balanced ? "ok" : "danger",
              },
            ]}
          />
          {promotion.error ? <p className="alert error mono">{promotion.error}</p> : null}
          {quarantine && quarantine.total ? (
            <p className="meta" style={{ marginTop: 10 }}>
              <span className="mono">{quarantine.total.toLocaleString()}</span> rows were refused —{" "}
              {Object.entries(quarantine.by_outcome)
                .map(([outcome, rows]) => `${rows.toLocaleString()} ${outcome}`)
                .join(" · ")}
              . A fix is a new mapping version; re-promoting the batch re-drives them.
            </p>
          ) : null}
          {Object.keys(chain.silver_tables ?? {}).length ? (
            <div className="card" style={{ marginTop: 14 }}>
              <label>Entities written</label>
              <ul className="plain">
                {Object.entries(chain.silver_tables).map(([table, rows]) => (
                  <li key={table} className="mono small">
                    {table} · {rows.toLocaleString()} rows
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : (
        <>
          <h2>Silver Raw</h2>
          <EmptyState
            title="This batch has not been promoted yet."
            detail={
              <>
                Bronze holds the source-aligned rows. Reaching Silver Raw needs an
                approved mapping version for this feed — drafted from the proposal
                below, previewed, then approved at G2. Nothing is written to Silver
                until that approval exists.
              </>
            }
            action={
              chain.mapping?.feed ? (
                <Link href={`/mapping/${encodeURIComponent(chain.mapping.feed)}`} className="btn-dark">
                  Open mapping studio
                </Link>
              ) : null
            }
          />
        </>
      )}

      {promotion && !(quarantine && quarantine.rows.length) ? (
        <>
          <h2>Quarantine</h2>
          <EmptyState
            tone="result"
            title="No rows were refused."
            detail={
              <>
                Every row this batch read was written to Silver Raw by the
                approved mapping — the balance check on the promotion run
                confirms it: in = out + quarantined + drops.
              </>
            }
          />
        </>
      ) : null}

      {quarantine && quarantine.rows.length ? (
        <>
          <h2>
            Quarantine{" "}
            <span className="meta">
              · {quarantine.rows.length} of {quarantine.total.toLocaleString()} refused rows ·
              PHI-candidate values masked
            </span>
          </h2>
          <div className="card scroll" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th className="num">Row</th>
                  <th>Outcome</th>
                  <th>Refused by</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {quarantine.rows.map((row) => (
                  <tr key={row.row_number}>
                    <td className="num mono">{row.row_number}</td>
                    <td>
                      <span className="outcome bad">{row.outcome}</span>
                    </td>
                    <td className="mono small">
                      {row.reasons
                        .map((reason) => `${reason.source}:${reason.rule ?? "—"}`)
                        .join(", ")}
                    </td>
                    <td className="meta">{row.reasons[0]?.reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {bronzeProfile ? (
        <>
          <h2>
            Bronze profile{" "}
            <span className="meta">
              · computed by code · {bronzeProfile.rows_profiled.toLocaleString()} of{" "}
              {bronzeProfile.rows_in_batch.toLocaleString()} rows
              {bronzeProfile.is_sample ? " (sampled window)" : " (whole batch)"}
            </span>
          </h2>
          <div className="card scroll" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th className="num">Nulls</th>
                  <th className="num">Distinct</th>
                </tr>
              </thead>
              <tbody>
                {bronzeProfile.facts.columns.map((column) => (
                  <tr key={column.name}>
                    <td className="mono">
                      {column.name}{" "}
                      {column.phi_candidate ? <span className="tag phi">PHI</span> : null}
                    </td>
                    <td className="mono">{column.inferred_type}</td>
                    <td className="num">{column.null_count.toLocaleString()}</td>
                    <td className="num">{column.distinct_count.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {proposal ? (
        <>
          <h2>AI mapping proposal</h2>
          <ProposalTable proposal={proposal} />

          <div className="row" style={{ justifyContent: "space-between", marginTop: 14 }}>
            <p className="meta">
              Nothing here is a mapping yet. Stage 4 turns this proposal into an editable,
              versioned mapping the analyst owns; only an approved version reaches Silver Raw.
            </p>
            <Link
              href={`/mapping/${encodeURIComponent(proposal.feed)}?proposal=${proposal.proposal_id}`}
              className="button-link"
            >
              Open mapping studio →
            </Link>
          </div>
        </>
      ) : (
        <p className="empty">
          No mapping proposal yet. Bronze analysis is queued — run{" "}
          <span className="mono">make worker</span> and reload.
        </p>
      )}

      <h2>
        Bronze rows{" "}
        <span className="meta">
          · {bronze.table} · {bronze.total.toLocaleString()} rows in this batch · showing{" "}
          {bronze.rows.length} · source-aligned, unmapped
        </span>
      </h2>

      {bronze.rows.length === 0 ? (
        <p className="empty">No Bronze rows for this batch.</p>
      ) : (
        <div className="card scroll" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th className="num">Row</th>
                {columns.map((column) => (
                  <th key={column}>
                    {column}
                    {bronze.phi_masked.includes(column) ? <span className="tag phi"> PHI</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bronze.rows.map((row) => (
                <tr key={row.row_number}>
                  <td className="num mono">{row.row_number}</td>
                  {columns.map((column) => (
                    <td key={column} className="mono">
                      {row.raw_row[column] || "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
