import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * The agent review queue — every R2 proposal awaiting a person, from every
 * agent, in one list.
 *
 * ONE QUEUE, NOT ONE PER AGENT. `proposals.proposal` is the only table an
 * agent writes that a human reads, and a second queue would be a second place
 * for "an agent suggested something and nobody looked" to happen. The agent
 * column says who suggested it; the shape of the review is the same either way.
 */
type Proposal = {
  proposal_id: string;
  agent: string;
  capability: string;
  risk_class: string;
  state: string;
  feed_id: string | null;
  confidence: number | null;
  created_ts: string;
  model_called: boolean;
  needs_input: string[];
  needs_steward_review: string[];
  refusals: string[];
};

const AGENT_LABEL: Record<string, string> = {
  "schema-inference": "Schema inference",
  "phi-detection": "PHI detection",
};

export default async function ProposalQueuePage({
  searchParams,
}: {
  searchParams: Promise<{ state?: string; agent?: string }>;
}) {
  const { state = "pending_review", agent } = await searchParams;
  const query = new URLSearchParams({ state });
  if (agent) query.set("agent", agent);
  const result = await attempt<Proposal[]>(`/api/proposals?${query.toString()}`);

  if (isRefused(result)) {
    return (
      <>
        <p className="note">
          <Link href="/data/intake">Data Intake</Link> / proposals
        </p>
        <h1>Agent proposals</h1>
        <RefusalNotice refusal={result} />
      </>
    );
  }

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / proposals
      </p>
      <h1>Agent proposals</h1>
      <p className="lede">
        Everything an agent has suggested and nobody has decided. Nothing here is in effect —
        approving one creates a draft that you author and somebody else approves.
      </p>

      <p className="note">
        <Link href="/data/intake/proposals?state=pending_review">Awaiting review</Link> ·{" "}
        <Link href="/data/intake/proposals?state=applied">Applied</Link> ·{" "}
        <Link href="/data/intake/proposals?state=rejected">Rejected</Link>
      </p>

      {result.length === 0 ? (
        <div className="card">
          <strong>Nothing is waiting</strong>
          <p className="note">
            No proposal is in this state. Profile a feed&apos;s sample and run schema inference or
            PHI detection to produce one.
          </p>
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Feed</th>
              <th>Agent</th>
              <th>Risk</th>
              <th>Confidence</th>
              <th>Needs a person</th>
              <th>Refused</th>
            </tr>
          </thead>
          <tbody>
            {result.map((p) => (
              <tr key={p.proposal_id}>
                <td>
                  <Link href={`/data/intake/proposals/${p.proposal_id}`}>
                    {p.feed_id ?? p.proposal_id}
                  </Link>
                </td>
                <td>
                  {AGENT_LABEL[p.agent] ?? p.agent}
                  {p.model_called ? "" : " (no model was called)"}
                </td>
                <td>{p.risk_class}</td>
                <td>
                  {/* The WEAKEST column's confidence, not the mean — so forty
                      easy columns cannot hide the one nobody could settle. */}
                  {p.confidence === null ? "—" : `${Math.round(p.confidence * 100)}%`}
                </td>
                <td>{p.needs_input.length + p.needs_steward_review.length}</td>
                <td>{p.refusals.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
