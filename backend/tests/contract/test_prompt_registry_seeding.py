"""CF-V0-E16-02 — the prompt registry, ON a plane rather than only in git.

    "no agent is ever built outside the registry"
    "Zero agent calls without a registry-resolvable prompt hash"
    — CF-V0-E16-02

THE BUG THIS LOCKS DOWN WAS INVISIBLE IN CI BY CONSTRUCTION. `LlmGateway
.complete()` resolves its template from the metadata store, and
`intelligence.demo.seed()` wrote those rows into the in-memory store the mock
socket uses — so every test and the whole rung-0 demo passed. `cinqflow
install` never wrote them, so a freshly installed Postgres plane answered
`POST /api/ask` with `ObjectNotFoundError: prompt:pipeline-insight.route`.
Every test ran on the one socket where the rows already existed.

So what is asserted here is not that a prompt renders. It is that the
CATALOGUE is complete, that seeding is governed and idempotent, and that
every prompt any agent asks for by name is in the catalogue — the last being
the check that would have caught `rule-authoring.author` missing from
`demo.py`'s hand-written tuple.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.agents import ALL_TEMPLATES
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.installer.prompts import SEED_AUTHOR, seed_prompts

pytestmark = pytest.mark.contract

APPROVER = Actor(
    subject="steve@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Steve Mathews"
)
AGENTS_ROOT = Path(__file__).resolve().parents[2] / "src" / "cinqflow" / "intelligence" / "agents"


# ── the catalogue is complete ────────────────────────────────────────────────


def test_every_prompt_id_an_agent_asks_for_is_in_the_catalogue() -> None:
    """The check that would have caught the missing `rule-authoring.author`.

    Read off the SOURCE rather than by running the agents: a prompt id only
    fails at the moment its node executes, so a code path no test exercises
    hides a missing template until production. Every `prompt_id="..."` keyword
    passed to a gateway call is a name the registry must be able to resolve.
    """
    catalogue = {template.prompt_id for template in ALL_TEMPLATES}
    asked: set[str] = set()
    for path in AGENTS_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "prompt_id" and isinstance(keyword.value, ast.Constant):
                    asked.add(str(keyword.value.value))

    assert asked, "no prompt_id found at all — this check has stopped checking"
    missing = sorted(asked - catalogue)
    assert not missing, (
        f"{missing} are asked for by name and are in no catalogue entry. Every one of them "
        "raises ObjectNotFoundError the first time its node runs."
    )


def test_no_two_templates_share_an_id_and_version() -> None:
    references = [template.reference for template in ALL_TEMPLATES]
    assert len(references) == len(set(references))


# ── seeding is governed ──────────────────────────────────────────────────────


def test_seeding_publishes_every_template_through_the_real_lifecycle() -> None:
    store = MemMetadataDb()
    report = seed_prompts(store, approver=APPROVER)

    assert report.total == len(ALL_TEMPLATES)
    assert len(report.published) == len(ALL_TEMPLATES)
    for template in ALL_TEMPLATES:
        stored = store.get(ObjectType.PROMPT, template.prompt_id, template.version)
        assert stored.lifecycle_state is LifecycleState.PUBLISHED
        assert stored.approved_by is not None, "Published with no named approver"
        assert stored.approved_by.subject == APPROVER.subject


def test_the_author_never_approves_its_own_prompt() -> None:
    store = MemMetadataDb()
    seed_prompts(store, approver=APPROVER)
    stored = store.get(ObjectType.PROMPT, ALL_TEMPLATES[0].prompt_id)
    assert stored.created_by.subject == SEED_AUTHOR.subject
    assert stored.approved_by is not None
    assert stored.approved_by.subject != stored.created_by.subject


def test_a_system_actor_cannot_publish_a_prompt() -> None:
    """The platform refused this when the seeder first tried it, and keeping
    the refusal is what makes the seeder honest about who signed."""
    with pytest.raises(ValueError, match="named person"):
        seed_prompts(MemMetadataDb(), approver=SEED_AUTHOR)


# ── seeding is idempotent, on VERSION ────────────────────────────────────────


def test_re_running_publishes_nothing_and_does_not_fail() -> None:
    store = MemMetadataDb()
    seed_prompts(store, approver=APPROVER)
    second = seed_prompts(store, approver=APPROVER)
    assert second.published == ()
    assert len(second.already_present) == len(ALL_TEMPLATES)


def test_a_new_version_publishes_beside_the_old_one() -> None:
    """Idempotent on (id, VERSION), never on id alone — otherwise every later
    prompt change is a silent no-op and the platform runs last quarter's
    prompt while reporting success."""
    from dataclasses import replace

    store = MemMetadataDb()
    first = ALL_TEMPLATES[0]
    seed_prompts(store, approver=APPROVER, templates=(first,))
    report = seed_prompts(
        store, approver=APPROVER, templates=(replace(first, version=first.version + 1),)
    )
    assert len(report.published) == 1
    assert store.get(ObjectType.PROMPT, first.prompt_id, first.version) is not None
    assert store.get(ObjectType.PROMPT, first.prompt_id, first.version + 1) is not None


def test_a_gateway_can_resolve_every_seeded_template() -> None:
    """The end this whole module exists for: `LlmGateway.complete()` calls
    `executable(store.get(ObjectType.PROMPT, prompt_id))`, and that call is
    what raised on every installed plane."""
    from cinqflow.core.prompts import executable

    store = MemMetadataDb()
    seed_prompts(store, approver=APPROVER)
    for template in ALL_TEMPLATES:
        resolved = executable(store.get(ObjectType.PROMPT, template.prompt_id))
        assert resolved.prompt_id == template.prompt_id
