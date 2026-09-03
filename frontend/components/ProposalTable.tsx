import Confidence from "@/components/ui/Confidence";
import StatusWord from "@/components/StatusWord";
import type { FieldStatus, MappingProposal } from "@/lib/api";
import { proposalStatusWord } from "@/lib/statusWords";

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

/** The AI mapping proposal, rendered identically wherever an analyst needs to
 *  see it: the batch detail page (after the fact) and the Mapping Studio's
 *  empty state (before "Start draft" commits to it) — one component so the
 *  two moments never drift into showing different information. */
export default function ProposalTable({ proposal }: { proposal: MappingProposal }) {
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="meta">
          {proposal.provenance.prompt} · {proposal.provenance.model} · advisory only
        </span>
        <StatusWord word={proposalStatusWord(proposal.status)} />
      </div>

      {proposal.status === "invalid" ? (
        <p className="alert error">
          This proposal failed validation: the model named at least one target the canonical
          model does not have. The offending targets are shown below and were not kept.
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
                  <Confidence value={field.confidence} />
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
    </>
  );
}
