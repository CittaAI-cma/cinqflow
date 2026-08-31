"""CF-V2-E5-04 — the proposed contract v2 a compatible rename produces.

    "Never block ingestion on compatible drift — log it and propose the
     contract update."
    "Auto-modify a contract — even a compatible rename becomes a proposed new
     contract version for approval." — the documented don't

ONE PLAIN SYNCHRONOUS FUNCTION, like every Wave-2 worker. The runner already
classified the rename (deterministically, from the glossary) and read the
batch through it; what remains is the paperwork the story demands: a DRAFT
proposal in the SAME review queue every agent writes to, whose acceptance
produces contract v(n+1) through the SAME apply path schema inference uses —
so a drift-proposed contract is reviewed, corrected, versioned and audited
exactly like an inferred one, and no second acceptance machinery exists.

NO MODEL WAS CALLED and the proposal says so: confidence is 1.0 with the
glossary rows as grounding, because "these two spellings carry one concept"
was settled by an approved term, not judged by anything.

IDEMPOTENT PER RENAME SET: a feed delivering the renamed file daily must not
grow a proposal per delivery. An undecided proposal for the same renames
stands; a new proposal is written only when the rename set is new.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cinqflow.core.agents.mapping_suggestion.graph import AGENT as MAPPING_SUGGESTION_AGENT
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.drift import Rename
from cinqflow.core.mapping import FeedMapping
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.proposals import Proposal, ProposalState, submit
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.ports.metadata_db import MetadataDbPort

AGENT = "drift-detection"
CAPABILITY = "propose_contract_update"
AGENT_ACTOR = Actor(subject=AGENT, actor_type=ActorType.AI, display_name="Drift detection")

#: W1-32 — the SAME drift-detection process (no model, a glossary-settled
#: `Rename`) proposes a SECOND, distinct thing: not a new contract version,
#: but a redirect for the one mapping line that still reads the old spelling.
#: `agent` here is deliberately `MAPPING_SUGGESTION_AGENT`, not `AGENT` above —
#: `api.app.approve_proposal` routes a proposal to the mapping-acceptance path
#: by `proposal.agent`, not by `capability`, so this MUST claim that identity
#: to travel the one door CF-V1-E6-03's editor and CF-V1-E6-02's mapping
#: suggestions already built, rather than a second one nobody wired approval
#: for. `capability` is what keeps it from reading as "a routine
#: mapping-suggestion run" in the queue: a reviewer, or a filter on
#: `GET /api/proposals`, can tell "the model proposed this" from "the estate's
#: own glossary settled this" apart at a glance.
MAPPING_REDIRECT_CAPABILITY = "propose_mapping_redirect"
MAPPING_REDIRECT_ACTOR = Actor(
    subject=MAPPING_SUGGESTION_AGENT,
    actor_type=ActorType.AI,
    display_name="Drift detection (mapping redirect)",
)
#: The `settled_by` vocabulary a mapping-suggestion record already carries
#: (`intelligence.demo`'s seed uses "published_mapping" and "inference") gets
#: a THIRD value here — not a fourth proposal shape, one more word in a
#: vocabulary that already existed. The commit this ships in is named for
#: this: the estate's own vocabulary was already enough to know a column just
#: moved.
SETTLED_BY_REDIRECT = "rename_redirect"


def propose_contract_update(
    metadata: MetadataDbPort,
    *,
    feed_id: str,
    contract: SchemaContract,
    renames: tuple[Rename, ...],
    run_id: str,
    now: datetime | None = None,
) -> Proposal | None:
    """Write the draft the steward will decide. Returns None when an
    undecided proposal for the same rename set already stands — a feed
    delivering its renamed file daily earns one proposal, not one per day."""
    if not renames:
        return None
    stamp = now or datetime.now(UTC)
    wanted = {(r.was, r.now) for r in renames}
    for pending in metadata.list_proposals(feed_id=feed_id, agent=AGENT):
        if (
            pending.state in {ProposalState.DRAFT, ProposalState.PENDING_REVIEW}
            and {(str(r.get("was")), str(r.get("now"))) for r in pending.payload.get("renames", ())}
            == wanted
        ):
            return None

    reads_as = {r.was: r.now for r in renames}
    records = [
        {
            "source_name": reads_as.get(column.reads_from, column.reads_from),
            "name": column.name,
            "type": column.type.value,
            "nullable": column.nullable,
            "is_phi": column.is_phi,
            "date_format": column.date_formats[0] if column.date_formats else None,
            "needs_input": False,
        }
        for column in contract.columns
    ]
    proposal = submit(
        Proposal(
            proposal_id=str(uuid.uuid4()),
            agent=AGENT,
            capability=CAPABILITY,
            risk_class=RiskClass.R2,
            run_id=run_id,
            feed_id=feed_id,
            payload={
                "key": "source_name",
                "contract_version": contract.version,
                "records": records,
                "renames": [
                    {
                        "was": r.was,
                        "now": r.now,
                        "glossary_id": r.glossary_id,
                        "term": r.term,
                        "evidence": r.explain(),
                    }
                    for r in renames
                ],
                "refusals": [],
                "needs_input": [],
            },
            created_by=AGENT_ACTOR,
            created_ts=stamp,
            # Settled by an approved glossary term, not judged by a model —
            # the one case where 1.0 is a statement of method, not confidence
            # in a guess.
            confidence=1.0,
            grounding_citations=tuple(
                CitationId(kind=CitationKind.TERM, subject=r.term_slug)
                for r in renames
                if r.term_slug
            ),
        ),
        now=stamp,
    )
    return metadata.record_proposal(proposal)


def propose_mapping_redirect(
    metadata: MetadataDbPort,
    *,
    feed_id: str,
    mapping: FeedMapping,
    renames: tuple[Rename, ...],
    run_id: str,
    now: datetime | None = None,
) -> Proposal | None:
    """W1-32 (F2's remaining half) — the mapping-line redirect W2-27's
    `reads_as` overlay never touched.

    A settled `Rename` already lets THIS run's `reads_as` read the new
    spelling (W2-27), and already proposes contract v(n+1) (CF-V2-E5-04,
    `propose_contract_update` above). Neither of those touches the feed's
    PUBLISHED `FeedMapping` — its `MappingLine.source_columns` still says the
    old name, forever, unless something proposes otherwise. This is that
    something.

    THE ONE QUESTION THIS ASKS: is `rename.was` a source column of an ACTIVE
    line in the mapping THE PIPELINE ACTUALLY RUNS ON? A settled rename with
    no mapping line reading the old name has nothing to redirect — returns
    `None` rather than a proposal that changes nothing.

    Returns None also when an undecided redirect for the same (line, was,
    now) set already stands, for the same reason `propose_contract_update`
    is idempotent: a feed delivering its renamed file daily must earn one
    proposal, not one per delivery.

    WHY THIS NEVER TOUCHES THE STORED MAPPING. "Agents propose; humans
    dispose" is not a slogan this function almost obeys by writing the
    proposals table instead of the mapping table — accepting it is still the
    SAME approve-a-suggestion act CF-V1-E6-02 built, through the same
    `POST /api/proposals/{id}/approve` route, which authors the reviewer as
    the resulting DRAFT mapping v(n+1)'s creator and leaves the PUBLISHED
    version this pipeline reads exactly as it was. A redirect that mutated
    `source_columns` in place would be a governed object changing itself.
    """
    redirects = {
        rename.was: rename
        for rename in renames
        for line in mapping.lines
        if line.is_mapped and line.reads_from == rename.was
    }
    if not redirects:
        return None
    stamp = now or datetime.now(UTC)
    wanted = {(was, r.now, r.glossary_id) for was, r in redirects.items()}
    for pending in metadata.list_proposals(feed_id=feed_id, agent=MAPPING_SUGGESTION_AGENT):
        if (
            pending.capability == MAPPING_REDIRECT_CAPABILITY
            and pending.state in {ProposalState.DRAFT, ProposalState.PENDING_REVIEW}
            and {
                (
                    str(r.get("source_column_before")),
                    str(r.get("source_column")),
                    r.get("glossary_id"),
                )
                for r in pending.payload.get("records", ())
                if r.get("settled_by") == SETTLED_BY_REDIRECT
            }
            == wanted
        ):
            return None

    # Every line the published mapping carries, verbatim — a mapping
    # proposal's acceptance REPLACES the whole set of lines
    # (`api.app._mapping_body_from`), so leaving an untouched line out of
    # `records` would silently drop it from the accepted draft. Only the
    # lines whose SOLE reading source is a settled rename's old spelling
    # change; every other line is carried forward, labelled the same way
    # `intelligence.demo`'s own seed already labels an untouched line.
    records: list[dict[str, object]] = []
    for line in mapping.lines:
        if line.platform_supplied:
            # A settled rename never touches these — `batch_id`, `record_hash`
            # read from nothing a payer sends — and the existing mapping-
            # acceptance record shape (`api.app._mapping_body_from`) has no
            # `platform_supplied` field to carry one through faithfully. That
            # is a pre-existing gap in the mechanism this redirect reuses, not
            # one this slab introduces; representing it with a fabricated
            # source would be worse than the honest gap the loss-acknowledgement
            # gate already catches on the later approve-and-publish step.
            continue
        if not line.is_mapped:
            records.append(
                {
                    "source_column": "",
                    "target_entity": line.target_entity,
                    "target_field": line.target_field,
                    "unmapped": True,
                    "unmapped_reason": line.unmapped_reason,
                    "glossary_id": line.glossary_id,
                    # `1.0` when the line itself carries no confidence — a
                    # PUBLISHED decision is not a guess with an unknown score,
                    # exactly the reading `intelligence.demo`'s own seed gives
                    # an untouched, already-approved line.
                    "confidence": line.confidence if line.confidence is not None else 1.0,
                    "settled_by": "published_mapping",
                    "rationale": "Matches this feed's own currently published mapping.",
                    "like_feed_id": None,
                }
            )
            continue
        rename = redirects.get(line.reads_from)
        records.append(
            {
                "source_column": rename.now if rename else line.reads_from,
                "source_column_before": rename.was if rename else None,
                "target_entity": line.target_entity,
                "target_field": line.target_field,
                "unmapped": False,
                "unmapped_reason": "",
                "glossary_id": rename.glossary_id if rename else line.glossary_id,
                "confidence": (1.0 if rename or line.confidence is None else line.confidence),
                "settled_by": SETTLED_BY_REDIRECT if rename else "published_mapping",
                "rationale": (
                    rename.explain()
                    if rename
                    else "Matches this feed's own currently published mapping."
                ),
                "like_feed_id": None,
            }
        )

    proposal = submit(
        Proposal(
            proposal_id=str(uuid.uuid4()),
            agent=MAPPING_SUGGESTION_AGENT,
            capability=MAPPING_REDIRECT_CAPABILITY,
            risk_class=RiskClass.R2,
            run_id=run_id,
            feed_id=feed_id,
            payload={"records": records},
            created_by=MAPPING_REDIRECT_ACTOR,
            created_ts=stamp,
            # Settled by an approved glossary term, exactly like
            # `propose_contract_update` — 1.0 is a statement of method (a
            # deterministic glossary lookup), not confidence in a guess.
            confidence=1.0,
            grounding_citations=tuple(
                CitationId(kind=CitationKind.TERM, subject=rename.term_slug)
                for rename in redirects.values()
                if rename.term_slug
            ),
        ),
        now=stamp,
    )
    return metadata.record_proposal(proposal)
