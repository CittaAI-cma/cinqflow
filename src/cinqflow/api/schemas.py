"""The wire shapes. The OpenAPI document generated from these IS the UI's contract.

    "types generated from OpenAPI — no hand-written client"
    — the Wave-0 plan, §2

Which is why these models are deliberate rather than dumped dataclasses: every
figure the UI renders is wrapped with its `citation_id`, so an uncited number
cannot reach a screen. That is enforced here, at the boundary, rather than by a
convention the frontend is asked to remember.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from cinqflow.core.citations import CitationId
from cinqflow.core.model.vocabulary import ActorType, StatusWord


class Cited(BaseModel):
    """A value and where it came from. The UI's `<Cited>` primitive, on the wire.

    `citation_id` is not decoration: it parses to a route, so every figure is
    clickable and every claim is openable. `route` travels with it so the
    browser never has to re-implement the parser.
    """

    value: str | int | float | None
    citation_id: str
    route: str

    @classmethod
    def of(cls, value: str | int | float | None, citation: CitationId) -> Cited:
        return cls(value=value, citation_id=str(citation), route=citation.route)


class PrincipalOut(BaseModel):
    """Who the caller is, and what the UI may therefore offer.

    `permitted_actions` is sent so the UI can hide what the server would refuse.
    Hiding is a courtesy; the refusal is the control, and it lives on the route.
    """

    subject: str
    display_name: str
    roles: list[str]
    has_access: bool
    permitted_actions: list[str]


class FeedOut(BaseModel):
    feed_id: str
    domain: str
    source_system: str
    file_format: str
    landing_path: str
    file_pattern: str
    schedule_cron: str
    version: int
    lifecycle_state: str
    status: StatusWord = Field(
        description="One of the seven words. There is no eighth, and no synonym."
    )
    citation_id: str
    route: str


class FeedIn(BaseModel):
    """A feed as a Business Analyst states it — six fields plus a real sample.

    `sample_filename` is required on creation because the pattern is validated
    against a real filename BEFORE save; a pattern nobody tested is the defect
    this story exists to prevent (incident #1, the leading underscore).
    """

    feed_id: str
    domain: str
    source_system: str
    file_format: str
    landing_path: str
    file_pattern: str
    schedule_cron: str
    sample_filename: str
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    allows_leading_underscore: bool = True


class AuditOut(BaseModel):
    object_type: str
    object_id: str
    version: int
    action: str
    actor_subject: str
    actor_type: ActorType = Field(
        description="human, system or ai — recorded, never inferred from the route"
    )
    occurred_ts: datetime
    detail: str


class UnknownOut(BaseModel):
    question: str
    owner: str
    blocks: bool


class ContractOut(BaseModel):
    story_id: str
    reads: list[str]
    writes: list[str]
    unknowns: list[UnknownOut]


class ProblemOut(BaseModel):
    """A refusal that explains itself.

    Every "don't" in this platform is a negative test that makes the attempt;
    the response it asserts against needs to say WHY, or the test is asserting
    on a status code and the user is left guessing.
    """

    detail: str
