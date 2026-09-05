import Confidence from "@/components/ui/Confidence";
import StatusWord from "@/components/StatusWord";
import type { FieldStatus, MappingProposal } from "@/lib/api";
import { ROLE_WORD, roleIndex, type RoleKey } from "@/lib/columnRoles";
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
 *  see it: the batch detail page (after the fact), the Mapping Studio's
 *  empty state (before "Start draft" commits to it), and S4's Bronze review
 *  — one component so those moments never drift into showing different
 *  information. `statuses` restricts which rows render; omit it to show
 *  every field (the batch page's original, unfiltered behaviour). `roles`
 *  (PR-7) - source column → role from the upload's interpretation - adds a role
 *  column and orders the rows identifiers → measures → dimensions → dates →
 *  business → technical; without it the table is exactly as before. */
export default function ProposalTable({
  proposal,
  statuses,
  roles,
}: {
  proposal: MappingProposal;
  statuses?: FieldStatus[];
  roles?: Record<string, RoleKey>;
}) {
  const filtered = statuses
    ? proposal.content.fields.filter((field) => statuses.includes(field.status))
    : proposal.content.fields;
  // Stable: within a role the proposal's own order (the file's column order) holds.
  const fields = roles
    ? filtered
        .map((field, index) => ({ field, index }))
        .sort(
          (a, b) =>
            roleIndex(roles[a.field.source] ?? "unclassified") -
              roleIndex(roles[b.field.source] ?? "unclassified") || a.index - b.index,
        )
        .map((entry) => entry.field)
    : filtered;

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
              {roles ? <th>Role</th> : null}
              <th>Concept</th>
              <th>Proposed target</th>
              <th>Transform</th>
              <th>Status</th>
              <th>Confidence</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <tr key={field.source}>
                <td className="mono">{field.source}</td>
                {roles ? (
                  <td>
                    <span className={`role-pill ${roles[field.source] ?? "unclassified"}`}>
                      {ROLE_WORD[roles[field.source] ?? "unclassified"]}
                    </span>
                  </td>
                ) : null}
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
