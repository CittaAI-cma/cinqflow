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

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.drift import Rename
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.proposals import Proposal, ProposalState, submit
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.ports.metadata_db import MetadataDbPort

AGENT = "drift-detection"
CAPABILITY = "propose_contract_update"
AGENT_ACTOR = Actor(subject=AGENT, actor_type=ActorType.AI, display_name="Drift detection")


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
