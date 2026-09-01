import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { ImpactPacketCard } from "@/components/ImpactPacket";
import { RefusalNotice } from "@/components/Refusal";
import { MetricTile } from "@/components/ui/MetricTile";
import { attempt, isRefused, type Refused } from "@/lib/api";
import type { ImpactPacket, Mapping, MappingDiff, Principal } from "@/lib/types";
import {
  acceptProposal,
  approveObject,
  publishObject,
  rejectProposal,
  submitForReview,
} from "./actions";
import { SubmitButton } from "./SubmitButton";

/**
 * W1-31 (CF-V1-E6-03) — the platform's first WRITE-capable proposal review.
 *
 * Every agent's proposal used to land here read-only: a reviewer could see
 * what was suggested and nowhere press anything but back. This is the same
 * screen `ui/app/data/intake/proposal/[proposalId]` drew, with the missing
 * half added — an editable target per line, a real `accepts_loss` checkbox
 * list, and the two buttons ("Accept", "Reject") that were always the point
 * of a REVIEW queue.
 *
 * GENERIC BY AGENT, RICH FOR MAPPING. `_proposal_out`'s discriminated shape
 * (one populated list per agent — `columns`, `phi_columns`, `mapping_lines`,
 * `rules`) still decides what renders; every shape gets the same "accept as
 * written, or reject with a reason" pair, and only a mapping proposal's own
 * per-line correction is worth a bespoke form — F4 was never mapping-specific,
 * mapping is only the sharpest example of what was missing.
 *
 * THREE DOORS, TWO PEOPLE. Accepting a proposal is EDIT_FEED (authoring);
 * carrying the DRAFT it produces to Published is SUBMIT_FOR_REVIEW, then
 * APPROVE, then PUBLISH — a data steward's lane, never the same person who
 * authored the draft (`core.model.governed`'s own author-approves-own
 * refusal holds even if a role were mis-granted both). So this page's own
 * "lifecycle console", below the decision, is not a shortcut around that
 * segregation — it is the same three routes `/data/intake/feed` and its
 * siblings would offer, reached from where the draft was born instead of
 * from a second screen somebody has to go find.
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
type ProposedRule = {
  stated: string;
  unsupported: boolean;
  unsupported_reason: string;
  rule_id: string | null;
  name: string;
  explanation: string;
  column: string | null;
  severity: string | null;
  confidence: number | null;
  settled_by: string;
  rationale: string;
};
type Correction = {
  field_path: string;
  proposed: unknown;
  accepted: unknown;
  is_addition: boolean;
};
/** CF-V1-E16-06 — the guide said one thing, the file showed another.
 *  BOTH numbers and BOTH citations: a truncated delivery and a bad
 *  specification look identical from here, and a reviewer shown only the
 *  winner cannot tell them apart. */
type DocumentConflict = {
  what: string;
  document_says: number;
  sample_shows: number;
  document_citation: string;
  sample_citation: string;
  quote: string;
  resolution: string;
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
  decided_ts: string | null;
  applied_object_type: string | null;
  applied_object_id: string | null;
  applied_version: number | null;
  grounding_citations: string[];
  columns: ProposedColumn[];
  phi_columns: PhiColumn[];
  mapping_lines: ProposedMapping[];
  rules: ProposedRule[];
  needs_input: string[];
  refusals: string[];
  needs_steward_review: string[];
  masked_columns: string[];
  corrections: Correction[];
  model_called: boolean;
  document_conflicts: DocumentConflict[];
};
type Acceptance = {
  total: number;
  accepted: number;
  corrected: number;
  rate: number;
  deterministic_total: number;
  deterministic_corrected: number;
  inferred_total: number;
  inferred_corrected: number;
  inferred_rate: number;
  additions: number;
  report: string;
};

const AGENT_LABEL: Record<string, string> = {
  "schema-inference": "Schema inference",
  "phi-detection": "PHI detection",
  "mapping-suggestion": "Mapping suggestion",
  "rule-authoring": "Rule authoring",
  "fingerprint-match": "Fingerprint match",
};

const BASIS_MEANING: Record<string, string> = {
  glossary: "your own glossary says so",
  computation: "every value in the sample fits the shape",
  scrub: "the scrubber found identifying entities in the values",
  inference: "the model read the name and the statistics",
  precaution: "nothing identified it — protected until you decide",
};

export default async function ProposalPage({
  params,
  searchParams,
}: {
  params: Promise<{ proposalId: string }>;
  searchParams: Promise<{ outcome?: string; headline?: string }>;
}) {
  const { proposalId } = await params;
  const outcome = await searchParams;
  const id = encodeURIComponent(proposalId);

  const [result, acceptance, me] = await Promise.all([
    attempt<Proposal>(`/api/proposals/${id}`),
    attempt<Acceptance>(`/api/proposals/${id}/acceptance`),
    attempt<Principal>("/api/me"),
  ]);

  const crumbs = (
    <p className="note">
      <Link href="/data/intake">Data Intake</Link> /{" "}
      <Link href="/data/intake/proposals">Proposals</Link> / {proposalId}
    </p>
  );

  if (isRefused(result)) {
    return (
      <>
        {crumbs}
        <h1>Proposal</h1>
        <RefusalNotice refusal={result} />
      </>
    );
  }

  const mayDecide = !isRefused(me) && me.permitted_actions.includes("edit_feed");
  const decideDisabledBecause = mayDecide
    ? undefined
    : "Deciding a proposal is edit_feed. Your role can read this screen but not accept or reject it.";

  const isPhi = result.agent === "phi-detection";
  const isMapping = result.agent === "mapping-suggestion";
  const isRule = result.agent === "rule-authoring";
  const unmapped = result.mapping_lines.filter((line) => line.unmapped);
  const mapped = result.mapping_lines.filter((line) => !line.unmapped);
  const isOpen = result.state === "pending_review" || result.state === "draft";

  // The governed object the acceptance produced, if it has. Only a MAPPING
  // gets its own lifecycle console today — the other agents' targets
  // (contract, runbook) have no accepts_loss question of their own, and a
  // console with nothing CF-V1-E6-04-shaped to say would be furniture.
  const mappingFeedId =
    result.applied_object_type === "mapping" ? result.applied_object_id : null;
  const mapping = mappingFeedId
    ? await attempt<Mapping>(`/api/feeds/${encodeURIComponent(mappingFeedId)}/mapping`)
    : null;
  const diff =
    mapping && !isRefused(mapping) && mapping.lifecycle_state === "pending_review" && mapping.version > 1
      ? await attempt<MappingDiff>(
          `/api/feeds/${encodeURIComponent(mappingFeedId as string)}/mapping/diff?to_version=${mapping.version}`,
        )
      : null;
  const losses = diff && !isRefused(diff) ? diff.lines.filter((line) => line.loses_its_source) : [];

  // CF-V1-E11-02. Fetched only at the moment an approver is actually
  // deciding — "both-sides impact" is the reason to have this screen open,
  // not something worth a round-trip while the draft is still being written.
  const packet =
    mapping && !isRefused(mapping) && mapping.lifecycle_state === "pending_review"
      ? await attempt<ImpactPacket>(
          `/api/objects/mapping/${encodeURIComponent(mappingFeedId as string)}/packet`,
        )
      : null;

  return (
    <>
      {crumbs}
      <h1>
        {isPhi
          ? "What this file holds"
          : isMapping
            ? "Where each column would land"
            : isRule
              ? "Proposed data-quality rules"
              : "A proposed data contract"}
      </h1>
      <p className="lede">
        Suggested by {AGENT_LABEL[result.agent] ?? result.agent} at {result.risk_class}.{" "}
        {result.state.replace(/_/g, " ")}.{" "}
        {result.model_called
          ? "A model was called for the columns nothing else settled."
          : "No model was called — the evidence settled every column."}
      </p>

      {outcome.outcome ? (
        <div className="card outcome" data-outcome={outcome.outcome}>
          <strong className="outcome-word">{outcome.outcome}</strong>
          <p>{outcome.headline}</p>
        </div>
      ) : null}

      {!isRefused(acceptance) ? (
        <div className="grid">
          <MetricTile
            label="Acceptance rate"
            value={`${Math.round(acceptance.rate * 100)}%`}
            hint={`${acceptance.accepted} of ${acceptance.total} accepted, ${acceptance.corrected} corrected`}
          />
          <MetricTile
            label="Deterministic"
            value={`${acceptance.deterministic_total - acceptance.deterministic_corrected}/${acceptance.deterministic_total}`}
            hint="settled without a model, untouched by the reviewer"
          />
          <MetricTile
            label="Inferred"
            value={`${Math.round(acceptance.inferred_rate * 100)}%`}
            hint={`${acceptance.inferred_corrected} of ${acceptance.inferred_total} model-touched lines corrected`}
          />
        </div>
      ) : null}

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
        <MappingReview
          proposal={result}
          mapped={mapped}
          unmapped={unmapped}
          isOpen={isOpen}
          mayDecide={mayDecide}
          decideDisabledBecause={decideDisabledBecause}
        />
      ) : isPhi ? (
        <PhiReview proposal={result} />
      ) : isRule ? (
        <RuleReview proposal={result} />
      ) : (
        <ColumnReview proposal={result} />
      )}

      {isOpen ? (
        <>
          <h2>Decide</h2>
          <p className="note">
            Accepting creates a DRAFT you author; somebody else — never you — carries it the rest
            of the way. Rejecting leaves nothing behind but the reason.
          </p>
          <div className="inline">
            <form id="accept-mapping" action={acceptProposal}>
              <input type="hidden" name="proposal_id" value={result.proposal_id} />
              <p className="field">
                <label htmlFor="accept-comment">Comment (optional)</label>
                <textarea id="accept-comment" name="comment" rows={2} />
              </p>
              <SubmitButton
                label="Accept"
                pendingLabel="Accepting…"
                primary
                disabledBecause={decideDisabledBecause}
              />
            </form>
            <form action={rejectProposal}>
              <input type="hidden" name="proposal_id" value={result.proposal_id} />
              <p className="field">
                <label htmlFor="reject-comment">Reason (required)</label>
                <textarea id="reject-comment" name="comment" rows={2} required />
              </p>
              <SubmitButton
                label="Reject"
                pendingLabel="Rejecting…"
                disabledBecause={decideDisabledBecause}
              />
            </form>
          </div>
        </>
      ) : (
        <>
          <h2>Decided</h2>
          <p className="note">
            {result.decided_by ? `Decided by ${result.decided_by}` : "Decided"}
            {result.decision_comment ? ` — ${result.decision_comment}` : ""}
            {result.applied_object_id
              ? ` · applied to ${result.applied_object_type}:${result.applied_object_id}@v${result.applied_version}`
              : ""}
          </p>
        </>
      )}

      {mappingFeedId && mapping && !isRefused(mapping) ? (
        <MappingLifecycle
          proposalId={result.proposal_id}
          feedId={mappingFeedId}
          mapping={mapping}
          losses={losses}
          me={me}
          packet={packet}
        />
      ) : null}

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
                  <td>{c.is_addition ? <span className="note">nothing proposed</span> : String(c.proposed)}</td>
                  <td>{String(c.accepted)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      {/* CF-V1-E16-06's exception path. ABOVE the citations, because a
          reviewer who scrolls past a contradiction to read the evidence has
          read the evidence in the wrong order. */}
      {result.document_conflicts.length > 0 ? (
        <>
          <h2>The uploaded document contradicts the sample</h2>
          <ul className="tree-list">
            {result.document_conflicts.map((conflict) => (
              <li key={`${conflict.what}-${conflict.document_says}`}>
                <strong>{conflict.what}</strong> — the document says{" "}
                {conflict.document_says}, the file shows {conflict.sample_shows}.{" "}
                <CitationChip citationId={conflict.document_citation} />{" "}
                <CitationChip citationId={conflict.sample_citation} />
                <p className="note">&ldquo;{conflict.quote}&rdquo;</p>
                <p className="note">{conflict.resolution}</p>
              </li>
            ))}
          </ul>
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
      </p>
    </>
  );
}

/**
 * The mapping proposal's own review — the one agent shape CF-V1-E6-03's
 * lifecycle exists for, and the only one with a per-line EDIT form: a target
 * a reviewer can retype before accepting, and an unmapped checkbox with its
 * own reason field, wired straight into `MappingDecisionIn`.
 */
function MappingReview({
  proposal,
  mapped,
  unmapped,
  isOpen,
  mayDecide,
  decideDisabledBecause,
}: {
  proposal: Proposal;
  mapped: ProposedMapping[];
  unmapped: ProposedMapping[];
  isOpen: boolean;
  mayDecide: boolean;
  decideDisabledBecause: string | undefined;
}) {
  if (!isOpen) {
    // Decided already — nothing left to correct, so render the plain record
    // the way the read-only screen always has.
    return (
      <>
        {unmapped.length > 0 ? (
          <div className="card">
            <strong>
              {unmapped.length} column{unmapped.length === 1 ? "" : "s"} the agent would not place
            </strong>
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
                <th>Settled by</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {mapped.map((line) => (
                <tr className="row" key={line.source_column}>
                  <td className="mono">{line.source_column}</td>
                  <td className="mono">
                    {line.target_entity}.{line.target_field}
                  </td>
                  <td>{line.settled_by}</td>
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
      </>
    );
  }

  return (
    <>
      <h2>Proposed mapping — edit before accepting</h2>
      <p className="note">
        Every target below is a real field: change one before you accept, or mark it unmapped with
        a reason and it will be proposed that way instead. Nothing here posts until you press
        Accept, below.
      </p>
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Source column</th>
              <th>Confidence</th>
              <th>Why the agent said so</th>
              <th>Target entity</th>
              <th>Target field</th>
              <th>Unmapped</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {proposal.mapping_lines.map((line, index) => (
              <tr className="row" key={line.source_column}>
                <td className="mono">
                  {line.source_column}
                  <input
                    type="hidden"
                    name="source_column"
                    value={line.source_column}
                    form="accept-mapping"
                  />
                </td>
                <td>
                  {line.settled_by === "inference"
                    ? `${Math.round(line.confidence * 100)}%`
                    : "settled"}
                </td>
                <td className="note">
                  {line.settled_by}
                  {line.rationale ? ` — ${line.rationale}` : ""}
                </td>
                <td>
                  <input
                    aria-label={`${line.source_column} target entity`}
                    name={`target_entity_${index}`}
                    defaultValue={line.target_entity}
                    form="accept-mapping"
                  />
                </td>
                <td>
                  <input
                    aria-label={`${line.source_column} target field`}
                    name={`target_field_${index}`}
                    defaultValue={line.target_field}
                    form="accept-mapping"
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${line.source_column} unmapped`}
                    name={`unmapped_${index}`}
                    defaultChecked={line.unmapped}
                    form="accept-mapping"
                  />
                </td>
                <td>
                  <input
                    aria-label={`${line.source_column} unmapped reason`}
                    name={`unmapped_reason_${index}`}
                    defaultValue={line.unmapped_reason}
                    form="accept-mapping"
                    placeholder="required if unmapped"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* The accept form itself lives below, under "Decide" — every input
          above points at it by id (`form="accept-mapping"`), which is the one
          standards-track way to keep table cells editable without nesting a
          <form> inside a <table>. */}
    </>
  );
}

function PhiReview({ proposal }: { proposal: Proposal }) {
  return (
    <>
      <div className="card">
        <strong>{proposal.masked_columns.length} column(s) would be masked</strong>
        <p className="note">
          Including the {proposal.needs_steward_review.length} still awaiting a steward —
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
          {proposal.phi_columns.map((c) => (
            <tr key={c.source_name}>
              <td>{c.source_name}</td>
              <td>{c.is_phi ? "yes" : "no"}</td>
              <td>{c.code_set ?? c.phi_kind ?? "—"}</td>
              <td>
                {c.basis}
                <span className="note"> — {BASIS_MEANING[c.basis] ?? c.basis}</span>
              </td>
              <td>{c.basis === "precaution" ? "needs a steward" : `${Math.round(c.confidence * 100)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">
        Clearing a PHI flag is a data steward&apos;s decision, made with a name and a reason at{" "}
        <Link href="/data/intake/glossary">the glossary</Link>&apos;s own reclassify door — accepting
        here only ever keeps or raises a flag.
      </p>
    </>
  );
}

function RuleReview({ proposal }: { proposal: Proposal }) {
  return (
    <>
      <h2>Proposed rules</h2>
      <table>
        <thead>
          <tr>
            <th>Stated</th>
            <th>Check</th>
            <th>Column</th>
            <th>Severity</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {proposal.rules.map((rule) => (
            <tr key={rule.stated}>
              <td>{rule.stated}</td>
              <td>{rule.unsupported ? <span className="note">not supported</span> : rule.explanation}</td>
              <td className="mono">{rule.column ?? "—"}</td>
              <td>{rule.severity ?? "—"}</td>
              <td>{rule.confidence === null ? "—" : `${Math.round(rule.confidence * 100)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function ColumnReview({ proposal }: { proposal: Proposal }) {
  return (
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
          {proposal.columns.map((c) => (
            <tr key={c.source_name}>
              <td>{c.source_name}</td>
              <td>{c.needs_input ? <span className="note">needs your input</span> : (c.name ?? "—")}</td>
              <td>{c.type ?? "—"}</td>
              <td>{c.is_phi ? "yes" : "no"}</td>
              <td>{c.settled_by}</td>
              <td>{Math.round(c.confidence * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/**
 * The draft mapping's OWN lifecycle, reached from the proposal that authored
 * it rather than from a second screen somebody has to go find. CF-V1-E6-04's
 * accepts_loss question is asked here, and nowhere else: `TransitionIn` is
 * the shipped field, and it belongs to the governance approve route on the
 * OBJECT, never to the proposal's own accept.
 */
function MappingLifecycle({
  proposalId,
  feedId,
  mapping,
  losses,
  me,
  packet,
}: {
  proposalId: string;
  feedId: string;
  mapping: Mapping;
  losses: MappingDiff["lines"];
  me: Principal | Refused;
  packet: ImpactPacket | Refused | null;
}) {
  const principal = isRefused(me) ? null : me;
  const may = (action: string, verb: string) =>
    principal?.permitted_actions.includes(action)
      ? undefined
      : `${verb} is ${action}. Your role can read this screen but not ${verb.toLowerCase()}.`;

  return (
    <>
      <h2>The draft mapping this produced</h2>
      <p className="lede">
        <Link href={`/data/intake/mapping/${feedId}`}>{feedId}</Link> · v{mapping.version} ·{" "}
        {mapping.status}
      </p>

      {mapping.lifecycle_state === "draft" ? (
        <form action={submitForReview} className="inline">
          <input type="hidden" name="proposal_id" value={proposalId} />
          <input type="hidden" name="object_type" value="mapping" />
          <input type="hidden" name="object_id" value={feedId} />
          <p className="field">
            <label htmlFor="submit-comment">Comment (optional)</label>
            <textarea id="submit-comment" name="comment" rows={1} />
          </p>
          <SubmitButton
            label="Submit for review"
            pendingLabel="Submitting…"
            primary
            disabledBecause={may("submit_for_review", "Submitting")}
          />
        </form>
      ) : null}

      {mapping.lifecycle_state === "pending_review" ? (
        <>
          {packet && !isRefused(packet) ? <ImpactPacketCard packet={packet} /> : null}
          {losses.length > 0 ? (
            <div className="card">
              <strong>
                {losses.length} field{losses.length === 1 ? "" : "s"} would stop being populated
              </strong>
              <p className="note">
                Nothing will fail. The batch will run, the row counts will reconcile and the
                ledger will balance — these columns will simply be empty from the next delivery
                onward. Check each one you mean to let happen; approving is refused until every
                loss below is named.
              </p>
              <ul className="stack">
                {losses.map((line) => (
                  <li key={line.address}>
                    <label className="inline">
                      <input
                        type="checkbox"
                        name="accepts_loss"
                        value={line.address}
                        form="approve-mapping"
                      />
                      {line.explanation}
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="card note">No field loses its source in this change.</div>
          )}
          <form id="approve-mapping" action={approveObject} className="inline">
            <input type="hidden" name="proposal_id" value={proposalId} />
            <input type="hidden" name="object_type" value="mapping" />
            <input type="hidden" name="object_id" value={feedId} />
            <p className="field">
              <label htmlFor="approve-comment">Comment (optional)</label>
              <textarea id="approve-comment" name="comment" rows={1} />
            </p>
            <SubmitButton
              label="Approve"
              pendingLabel="Approving…"
              primary
              disabledBecause={may("approve", "Approving")}
            />
          </form>
        </>
      ) : null}

      {mapping.lifecycle_state === "approved" ? (
        <form action={publishObject} className="inline">
          <input type="hidden" name="proposal_id" value={proposalId} />
          <input type="hidden" name="object_type" value="mapping" />
          <input type="hidden" name="object_id" value={feedId} />
          <SubmitButton
            label="Publish"
            pendingLabel="Publishing…"
            primary
            disabledBecause={may("publish", "Publishing")}
          />
        </form>
      ) : null}

      {mapping.lifecycle_state === "published" ? (
        <div className="card outcome" data-outcome="ACCEPTED">
          <strong className="outcome-word">PUBLISHED</strong>
          <p>
            v{mapping.version} is live. The pipeline reads published metadata and nothing else —
            this is the version it runs against next.
          </p>
        </div>
      ) : null}
    </>
  );
}
