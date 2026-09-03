import type { LineageChain as LineageChainData } from "@/lib/api";

function Node({
  label,
  value,
  gate,
}: {
  label: string;
  value: React.ReactNode;
  gate?: boolean;
}) {
  return (
    <div className={`chain-node${gate ? " gate" : ""}`}>
      <span className="chain-node-label">{label}</span>
      <span className="chain-node-value">{value}</span>
    </div>
  );
}

const Arrow = () => <i className="chain-arrow">→</i>;

/** Both approval nodes are what turns this from a data-flow picture into an audit record. */
export default function LineageChain({
  chain,
  gates,
}: {
  chain: LineageChainData["chain"];
  gates: LineageChainData["gates"];
}) {
  return (
    <div className="chain">
      <Node label="upload" value={chain.upload_id.slice(0, 12)} />
      <Arrow />
      <Node label="file" value={chain.fingerprint.slice(0, 18)} />
      <Arrow />
      <Node label="landing" value={chain.landing_key} />
      <Arrow />
      {gates.G1 ? (
        <>
          <Node
            label="G1 approval"
            value={`${new Date(gates.G1.decided_ts).toLocaleString()} · ${gates.G1.approver}`}
            gate
          />
          <Arrow />
        </>
      ) : null}
      <Node label="batch" value={chain.batch_id.slice(0, 12)} />
      <Arrow />
      <Node label="bronze" value={chain.bronze_table ?? "—"} />
      <Arrow />
      <Node
        label="mapping"
        value={chain.mapping ? `${chain.mapping.feed} v${chain.mapping.version}` : "not mapped yet"}
      />
      <Arrow />
      {gates.G2 ? (
        <>
          <Node
            label="G2 approval"
            value={`${new Date(gates.G2.decided_ts).toLocaleString()} · ${gates.G2.approver}`}
            gate
          />
          <Arrow />
        </>
      ) : null}
      <Node label="silver" value={chain.silver_table ?? "not promoted yet"} />
    </div>
  );
}
