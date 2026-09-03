import Link from "next/link";
import { notFound } from "next/navigation";
import BatchProcessing from "@/components/batch/BatchProcessing";
import Kpi from "@/components/Kpi";
import LineageChain from "@/components/LineageChain";
import StatusWord from "@/components/StatusWord";
import {
  getBatchRows,
  getBatchQuarantine,
  getBronzeProfile,
  getLineage,
  getProposal,
  type FieldStatus,
} from "@/lib/api";
import { proposalStatusWord, runStatusWord } from "@/lib/statusWords";

export const dynamic = "force-dynamic";

const STATUS_ORDER: FieldStatus[] = ["invalid", "candidate", "ambiguous", "unknown"];

const FIELD_KIND: Record<FieldStatus, string> = {
  candidate: "governed_knowledge",
  ambiguous: "inference",
  unknown: "inference",
  invalid: "recommendation",
};

/** Evidence strings are free text, but two prefixes carry real meaning worth a
 * viewer noticing at a glance: `precedent:` is a human-approved governance
 * decision applied deterministically (strong); `semantic:` is an unverified
 * lexical-similarity lead surfaced only where nothing structured could place
 * the column (weak, never itself a decision). Everything else renders plain. */
function evidenceClass(item: string): string {
  if (item.startsWith("precedent:")) return "evidence-chip--precedent";
  if (item.startsWith("semantic:")) return "evidence-chip--semantic";
  return "";
}

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
          <h2>
            AI mapping proposal{" "}
            <span className="meta">
              · {proposal.provenance.prompt} · {proposal.provenance.model} · advisory only ·{" "}
              <StatusWord word={proposalStatusWord(proposal.status)} />
            </span>
          </h2>

          {proposal.status === "invalid" ? (
            <p className="alert error">
              This proposal failed validation: the model named at least one target the
              canonical model does not have. The offending targets are shown below and were
              not kept.
            </p>
          ) : null}

          <div className="chip-row">
            {STATUS_ORDER.filter((status) => (proposal.counts?.[status] ?? 0) > 0).map((status) => (
              <span key={status} className="chip">
                {status} <span className="mono">{proposal.counts?.[status]}</span>
              </span>
            ))}
          </div>

          <div className="card scroll" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Source column</th>
                  <th>Concept</th>
                  <th>Proposed target</th>
                  <th>Transform</th>
                  <th>Status</th>
                  <th>Confidence</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {proposal.content.fields.map((field) => (
                  <tr key={field.source}>
                    <td className="mono">{field.source}</td>
                    <td className="meta">{field.concept ?? "—"}</td>
                    <td className="mono">
                      {field.target ?? <span className="unc">—</span>}
                      {field.rejected_target ? (
                        <div className="error" style={{ fontSize: 12 }}>
                          rejected: {field.rejected_target}
                        </div>
                      ) : null}
                    </td>
                    <td className="mono">
                      {field.transform
                        ? `${field.transform.op}(${Object.values(field.transform.args).join(", ")})`
                        : "—"}
                    </td>
                    <td>
                      <span className={`claim-kind ${FIELD_KIND[field.status]}`}>{field.status}</span>
                    </td>
                    <td className="num">
                      <span className="confidence-bar">
                        <i style={{ width: `${Math.round(field.confidence * 100)}%` }} />
                      </span>{" "}
                      {field.confidence.toFixed(2)}
                    </td>
                    <td>
                      <span className="evidence-list">
                        {field.evidence.map((item) => (
                          <span key={item} className={`evidence-chip ${evidenceClass(item)}`}>
                            {item}
                          </span>
                        ))}
                      </span>
                      {field.reason ? <div className="meta">{field.reason}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {proposal.content.notes.length ? (
            <div className="card" style={{ marginTop: 14 }}>
              <label>Notes</label>
              <ul className="plain">
                {proposal.content.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="card" style={{ marginTop: 14 }}>
            <label>Knowledge cited</label>
            <span className="mono">{proposal.provenance.knowledge.join(" · ") || "none"}</span>
          </div>

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
