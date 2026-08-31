import type { ImpactPacket } from "@/lib/types";

/**
 * CF-V1-E11-02 — the change, both sides of its impact, and the evidence, on
 * one screen, COMPUTED. An author who forgets to mention the four jobs their
 * mapping feeds does not thereby hide them from whoever is reading it: every
 * entry here came from the reference graph, not from a field the author
 * filled in.
 *
 * Shared between the proposal review console (where an approver sees it at
 * the moment of decision) and `/operations/lineage` (where anybody can ask
 * "what does this reach" outside a review, on demand) — one computation, one
 * rendering, so the two screens cannot show different answers for the same
 * object.
 */
export function ImpactPacketCard({ packet }: { packet: ImpactPacket }) {
  return (
    <div className="card">
      <strong>What this reaches</strong>
      {packet.is_empty ? (
        <p className="note">Nothing else in the estate references this object yet.</p>
      ) : (
        <>
          {packet.diff.length > 0 ? (
            <ul className="mono">
              {packet.diff.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
          <ImpactList title="Engineering impact" items={packet.engineering_impact} />
          <ImpactList title="Business impact" items={packet.business_impact} />
        </>
      )}
      {packet.unknowns.length > 0 ? (
        <div className="card" style={{ marginTop: "var(--s-3)" }}>
          <strong>
            {packet.unknowns.length} declared consumer{packet.unknowns.length === 1 ? "" : "s"}{" "}
            could not be resolved
          </strong>
          <p className="note">
            Shown rather than hidden — a blank where a downstream item should be is how
            rubber-stamping happens.
          </p>
          <ul>
            {packet.unknowns.map((unknown) => (
              <li key={unknown.name}>
                <span className="mono">{unknown.name}</span> — {unknown.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {packet.blocks_production ? (
        <p className="note" style={{ color: "var(--st-needs-attention)" }}>
          This change blocks production approval until every unresolved reference above is
          accounted for.
        </p>
      ) : null}
    </div>
  );
}

function ImpactList({ title, items }: { title: string; items: ImpactPacket["engineering_impact"] }) {
  if (items.length === 0) {
    return <p className="note">{title}: nothing downstream of this kind depends on it today.</p>;
  }
  return (
    <>
      <p className="note">{title}:</p>
      <ul>
        {items.map((touched) => (
          <li key={`${touched.object_type}:${touched.object_id}@v${touched.version}`}>
            <span className="mono">
              {touched.object_type}:{touched.object_id}@v{touched.version}
            </span>{" "}
            <span className="note">
              ({touched.lifecycle_state}, via {touched.via})
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}
