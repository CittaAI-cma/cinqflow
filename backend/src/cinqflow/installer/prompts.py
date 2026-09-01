"""CF-V0-E16-02 — put the prompt registry ON the plane. Idempotently.

    "no agent is ever built outside the registry"
    "Zero agent calls without a registry-resolvable prompt hash"
    — CF-V0-E16-02

WHAT WAS BROKEN, AND HOW IT HID. `LlmGateway.complete()` resolves its template
from the metadata store — `self._store.get(ObjectType.PROMPT, prompt_id)` —
which is exactly right, and is what makes a prompt a governed, versioned,
reviewable object rather than a string in a function. But nothing ever WROTE
those rows to a real plane. `intelligence.demo.seed()` published them into the
in-memory store the mock socket uses, so every test and the whole rung-0
demo passed; `cinqflow install` provisioned schemas, control tables and the
ODS model and never touched `governance.object`. The first model call on a
freshly installed rung-0.5 plane therefore raised

    ObjectNotFoundError: prompt:pipeline-insight.route

— a 500 on `POST /api/ask`, on every deployment that had ever been installed.
The bug was invisible in CI precisely because CI runs on the socket that seeds
them.

PUBLISHED THROUGH THE LIFECYCLE, NEVER BY SETTING A FIELD. A prompt is a
governed object (`core.lifecycle` routes `ObjectType.PROMPT` to the platform
engineers), and seeding a Published row directly would bypass the two
universal negatives this platform proves it cannot bypass. So the transitions
run for real, with a named author and a DIFFERENT named approver — because
`GovernedObject.transition_to` refuses a self-approval, and a seeder that
worked around that refusal would be the one place in the codebase where it
did not hold.

IDEMPOTENT, AND ON VERSION RATHER THAN EXISTENCE. Re-running an install must
not fail, and must not silently leave an OLD prompt in place either. A
template whose `(prompt_id, version)` is already published is skipped; a
template carrying a NEW version is published beside it, which is what makes a
prompt change a reviewable version bump rather than an in-place edit. That is
E16-02's own "a prompt change is a code change — reviewed, versioned, and
regression-gated", enforced at the seam where prompts reach a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cinqflow.core.agents import ALL_TEMPLATES
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.prompts import PromptTemplate
from cinqflow.ports.metadata_db import MetadataDbPort, ObjectNotFoundError

__all__ = ["PromptSeedReport", "seed_prompts"]

#: Who a seeded prompt is AUTHORED by. A system actor is legitimate here and
#: only here: the build authored these templates, and saying so is truer than
#: attributing them to whoever happened to run the installer.
SEED_AUTHOR = Actor(
    subject="cinqflow.installer",
    actor_type=ActorType.SYSTEM,
    display_name="CINQFLOW installer",
)


@dataclass(frozen=True)
class PromptSeedReport:
    """What the seeder did, so an installer can print it rather than guess."""

    published: tuple[str, ...] = ()
    already_present: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.published) + len(self.already_present)

    def explain(self) -> str:
        if not self.published:
            return f"prompt registry: {self.total} templates already published."
        return (
            f"prompt registry: published {len(self.published)} of {self.total} "
            f"({', '.join(self.published)})."
        )


def seed_prompts(
    store: MetadataDbPort,
    *,
    approver: Actor,
    templates: tuple[PromptTemplate, ...] = ALL_TEMPLATES,
    now: datetime | None = None,
) -> PromptSeedReport:
    """Publish every template this build carries. Safe to run again.

    `approver` IS REQUIRED AND MUST BE A NAMED HUMAN, because
    `GovernedObject.transition_to` refuses a SYSTEM approver outright —
    "agents propose; humans dispose — an approver is a named person" — and
    the first version of this seeder tried to approve as the installer and
    was correctly refused by the platform it was installing.

    That refusal is worth keeping rather than working around, and it makes
    the seeder honest about what it is: the person running `cinqflow install`
    is signing for the prompts this build ships. The substantive review
    happened in the pull request that added the template to
    `core.agents.ALL_TEMPLATES` — which is where E16-02 puts prompt review —
    and this records who accepted that build onto this plane.

    Prompts differ from `seed-glossary`'s terms in this one respect, and the
    difference is structural rather than a relaxation: `LlmGateway.complete()`
    resolves a PUBLISHED template, so a plane holding only drafts is a plane
    where every agent still 500s. A glossary term nobody has approved is a
    definition nobody uses; a prompt nobody has approved is a platform that
    does not run.
    """
    if approver.actor_type is not ActorType.HUMAN:
        raise ValueError(
            f"{approver.subject} is a {approver.actor_type.value} actor. Publishing a prompt "
            "needs a named person — the same rule every other governed object is held to."
        )
    stamp = now or datetime.now(UTC)
    published: list[str] = []
    present: list[str] = []

    for template in templates:
        if _already_published(store, template):
            present.append(template.reference)
            continue
        store.save(
            _through_the_lifecycle(
                template.as_governed(author=SEED_AUTHOR, now=stamp), approver=approver
            )
        )
        published.append(template.reference)

    return PromptSeedReport(published=tuple(published), already_present=tuple(present))


def _already_published(store: MetadataDbPort, template: PromptTemplate) -> bool:
    """On (id, VERSION), never on id alone.

    Checking the id alone would leave a v1 in place forever and make every
    later prompt change a silent no-op on an installed plane — the failure
    mode that is worse than the one this module fixes, because the platform
    would report success while running last quarter's prompt.
    """
    try:
        existing = store.get(ObjectType.PROMPT, template.prompt_id, template.version)
    except ObjectNotFoundError:
        return False
    return existing.lifecycle_state is LifecycleState.PUBLISHED


def _through_the_lifecycle(draft: GovernedObject, *, approver: Actor) -> GovernedObject:
    """Draft -> In Review -> Approved -> Published, for real.

    Three transitions rather than a constructed Published row, because
    `GovernedObject.__post_init__` refuses a Published object with no named
    approver and `transition_to` refuses a self-approval — and a seeder that
    sidestepped either would be the one writer in this codebase exempt from
    the rules every screen enforces.
    """
    reviewing, _ = draft.transition_to(LifecycleState.PENDING_REVIEW, actor=SEED_AUTHOR)
    approved, _ = reviewing.transition_to(LifecycleState.APPROVED, actor=approver)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=approver)
    return published
