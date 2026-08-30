import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * One agent proposal, as the reviewer reads it.
 *
 * THE BASIS COLUMN IS THE SCREEN. Both agents flag things; what a reviewer
 * needs is to tell apart "the client's own glossary says so", "every value in
 * the file fits the shape", and "nothing identified this, so we are protecting
 * it until you say otherwise". Those are the same flag and completely
 * different asks of a steward's half-hour, and sorting by them is what makes
 * the queue workable rather than a wall of amber.
 *
 * Refusals are shown FIRST, above the proposal. "The agent tried to clear a
 * PHI flag" is a governance event, and a review screen that buried it under
 * forty rows of agreement would be hiding the only line that matters.
 */
type ProposedColumn = {
  source_name: string;
  name: string | null;
  type: string | null;
  nullable: boolean;
  is_phi: boolean;
  glossary_id: string | null;
  confidence: number;
  settled_by: string;
  needs_input: boolean;
  rationale: string;
  citations: string[];
};
type PhiColumn = {
  source_name: string;
  is_phi: boolean;
  basis: string;
  phi_kind: string | null;
  code_set: string | null;
  confidence: number;
  needs_steward_review: boolean;
  glossary_id: string | null;
  rationale: string;
  citations: string[];
};
type ProposedMapping = {
  source_column: string;
  target_entity: string;
  target_field: string;
  unmapped: boolean;
  unmapped_reason: string;
  glossary_id: string | null;
  confidence: number;
  settled_by: string;
  rationale: string;
  like_feed_id: string | null;
  citations: string[];
};
type Proposal = {
  proposal_id: string;
  agent: string;
  capability: string;
  risk_class: string;
  state: string;
  feed_id: string | null;
  run_id: string;
  confidence: number | null;
  prompt_hash: string;
  created_by: string;
  created_ts: string;
  decided_by: string | null;
  decision_comment: string;
  applied_object_type: string | null;
  applied_object_id: string | null;
  applied_version: number | null;
  grounding_citations: string[];
  columns: ProposedColumn[];
  phi_columns: PhiColumn[];
  mapping_lines: ProposedMapping[];
  needs_input: string[];
  needs_steward_review: string[];
  masked_columns: string[];
  refusals: string[];
  corrections: { field_path: string; proposed: string; accepted: string }[];
  model_called: boolean;
};

/** What each basis means, in the words a steward should read. */
const BASIS_MEANING: Record<string, string> = {
  glossary: "your own glossary says so",
  computation: "every value in the sample fits the shape",
  scrub: "the scrubber found identifying entities in the values",
  inference: "the model read the name and the statistics",
  precaution: "nothing identified it — protected until you decide",
};

export default async function ProposalPage({
  params,
}: {
  params: Promise<{ proposalId: string }>;
}) {
  const { proposalId } = await params;
  const result = await attempt<Proposal>(`/api/proposals/${encodeURIComponent(proposalId)}`);

  if (isRefused(result)) {
    return (
      <>
        <p className="note">
          <Link href="/data/intake/proposals">Proposals</Link> / {proposalId}
        </p>
        <h1>Proposal</h1>
        <RefusalNotice refusal={result} />
      </>
    );
  }

  const isPhi = result.agent === "phi-detection";
  const isMapping = result.agent === "mapping-suggestion";
  const unmapped = result.mapping_lines.filter((line) => line.unmapped);
  const mapped = result.mapping_lines.filter((line) => !line.unmapped);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href="/data/intake/proposals">Proposals</Link> /{" "}
        {result.feed_id ? (
          <Link href={`/data/intake/feed/${result.feed_id}`}>{result.feed_id}</Link>
        ) : (
          result.proposal_id
        )}
      </p>
      <h1>
        {isPhi
          ? "What this file holds"
          : isMapping
            ? "Where each column would land"
            : "A proposed data contract"}
      </h1>
      <p className="lede">
        Suggested by {result.agent} at {result.risk_class}. {result.state.replace("_", " ")}.
        {result.model_called
          ? " A model was called for the columns nothing else settled."
          : " No model was called — the evidence settled every column."}
      </p>

      {result.refusals.length > 0 ? (
        <div className="card">
          <strong>The platform refused something the agent proposed</strong>
          <ul>
            {result.refusals.map((refusal) => (
              <li key={refusal}>{refusal}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {isMapping ? (
        <>
          {/*
            THE DECLINED COLUMNS COME FIRST. They are the shortest read on the
            page and the only part a steward has to do something about — a
            mapped line with a cited precedent needs a glance, and a declined
            one needs a decision. Putting the ninety agreements above the
            twelve questions is how a review screen becomes a scroll.
          */}
          {unmapped.length > 0 ? (
            <div className="card">
              <strong>
                {unmapped.length} column{unmapped.length === 1 ? "" : "s"} the agent would not
                place
              </strong>
              <p className="note">
                Every one says why. Declining is a correct answer here — a wrong mapping nobody
                questions loads real values into the wrong field and reconciles perfectly while
                doing it.
              </p>
              <dl>
                {unmapped.map((line) => (
                  <div key={line.source_column}>
                    <dt className="mono">{line.source_column}</dt>
                    <dd>{line.unmapped_reason}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}

          <h2>Proposed mapping</h2>
          <div className="card scroll">
            <table>
              <thead>
                <tr>
                  <th>Source column</th>
                  <th>Would land in</th>
                  <th>Decided by</th>
                  <th>Like</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {mapped.map((line) => (
                  <tr className="row" key={line.source_column}>
                    <td className="mono">{line.source_column}</td>
                    <td className="mono">
                      <Link href={`/data/canonical/${encodeURIComponent(line.target_entity)}`}>
                        {line.target_entity}
                      </Link>
                      .{line.target_field}
                    </td>
                    <td>
                      {line.settled_by === "glossary"
                        ? "your own glossary"
                        : line.settled_by === "published_mapping"
                          ? "already approved on this feed"
                          : "the model"}
                    </td>
                    <td>
                      {line.like_feed_id ? (
                        <Link href={`/data/intake/mapping/${line.like_feed_id}`}>
                          {line.like_feed_id}
                        </Link>
                      ) : (
                        <span className="note">—</span>
                      )}
                    </td>
                    <td>
                      {line.settled_by === "inference"
                        ? `${Math.round(line.confidence * 100)}%`
                        : "settled"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2>Why each column was placed</h2>
          {mapped
            .filter((line) => line.rationale)
            .map((line) => (
              <div className="card" key={`why-${line.source_column}`}>
                <strong>{line.source_column}</strong>
                <p>{line.rationale}</p>
                <p className="note">
                  {line.citations.map((citation) => (
                    <CitationChip key={citation} citationId={citation} />
                  ))}
                </p>
              </div>
            ))}
        </>
      ) : isPhi ? (
        <>
          <div className="card">
            <strong>{result.masked_columns.length} column(s) would be masked</strong>
            <p className="note">
              Including the {result.needs_steward_review.length} still awaiting a steward —
              protection is in place while the decision is pending, not after it.
            </p>
          </div>

          <h2>Columns</h2>
          <table>
            <thead>
              <tr>
                <th>Column</th>
                <th>Protected</th>
                <th>What it is</th>
                <th>Why we say so</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {result.phi_columns.map((c) => (
                <tr key={c.source_name}>
                  <td>{c.source_name}</td>
                  <td>{c.is_phi ? "yes" : "no"}</td>
                  <td>{c.code_set ?? c.phi_kind ?? "—"}</td>
                  <td>
                    {c.basis}
                    <span className="note"> — {BASIS_MEANING[c.basis] ?? c.basis}</span>
                  </td>
                  <td>
                    {c.basis === "precaution"
                      ? "needs a steward"
                      : `${Math.round(c.confidence * 100)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2>Why each column was decided</h2>
          {result.phi_columns.map((c) => (
            <div className="card" key={`why-${c.source_name}`}>
              <strong>{c.source_name}</strong>
              <p>{c.rationale}</p>
              <p className="note">
                {c.citations.map((citation) => (
                  <CitationChip key={citation} citationId={citation} />
                ))}
              </p>
            </div>
          ))}
        </>
      ) : (
        <>
          <h2>Columns</h2>
          <table>
            <thead>
              <tr>
                <th>Source column</th>
                <th>Proposed name</th>
                <th>Type</th>
                <th>PHI</th>
                <th>Settled by</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {result.columns.map((c) => (
                <tr key={c.source_name}>
                  <td>{c.source_name}</td>
                  <td>
                    {c.needs_input ? (
                      <span className="note">needs your input</span>
                    ) : (
                      (c.name ?? "—")
                    )}
                  </td>
                  <td>{c.type ?? "—"}</td>
                  <td>{c.is_phi ? "yes" : "no"}</td>
                  <td>{c.settled_by}</td>
                  <td>{Math.round(c.confidence * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {result.corrections.length > 0 ? (
        <>
          <h2>What the reviewer changed</h2>
          <table>
            <thead>
              <tr>
                <th>Field</th>
                <th>The agent proposed</th>
                <th>The reviewer accepted</th>
              </tr>
            </thead>
            <tbody>
              {result.corrections.map((c) => (
                <tr key={c.field_path}>
                  <td>{c.field_path}</td>
                  <td>{String(c.proposed)}</td>
                  <td>{String(c.accepted)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      <h2>Evidence</h2>
      <p className="note">
        {result.grounding_citations.map((citation) => (
          <CitationChip key={citation} citationId={citation} />
        ))}
      </p>
      <p className="note">
        run {result.run_id}
        {result.prompt_hash ? ` · prompt ${result.prompt_hash.slice(0, 12)}` : ""}
        {result.decided_by ? ` · decided by ${result.decided_by}` : ""}
        {result.applied_object_id
          ? ` · applied to ${result.applied_object_type}:${result.applied_object_id}@v${result.applied_version}`
          : ""}
      </p>
    </>
  );
}
