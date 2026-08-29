"""CF-V0-E1-01 — the register, and the entry that will not vanish.

ADR-0010 reduced this story to a checklist field. The tests below are what turn
a checklist field into a gate: the DoD asks that every story declare its
execution-plane contract, and `missing_contracts()` answers that without a
human reading thirteen story pages.
"""

from __future__ import annotations

import pytest

from cinqflow.core.registry.execution_plane import (
    ExecutionPlaneContract,
    ExecutionPlaneError,
    ExecutionPlaneRegister,
    PlaneObject,
    PlaneObjectKind,
    ReferencedEntryError,
    UndeclaredObjectError,
    Unknown,
)
from cinqflow.core.registry.wave0 import WAVE_0_STORIES, wave_0_register

pytestmark = pytest.mark.unit


def _object(object_id: str = "control.batch_control") -> PlaneObject:
    return PlaneObject(
        object_id=object_id,
        kind=PlaneObjectKind.CONTROL_TABLE,
        description="one row per run",
    )


# ── the DoD gate ─────────────────────────────────────────────────────────────


def test_every_wave_0_story_declares_an_execution_plane_contract() -> None:
    """The Definition of Done requires the field on every story.

    A review question becomes an assertion: adding a Wave-0 story without its
    contract fails here, at the point the story is added.
    """
    register = wave_0_register()
    missing = register.missing_contracts(WAVE_0_STORIES)
    assert missing == (), (
        f"{', '.join(missing)} have no execution-plane contract. "
        "The DoD requires the field on every story."
    )


def test_wave_0_declares_thirteen_stories() -> None:
    assert len(WAVE_0_STORIES) == 13
    assert len(set(WAVE_0_STORIES)) == 13


def test_every_contract_names_only_declared_objects() -> None:
    register = wave_0_register()
    known = set(register.objects)
    for story_id, contract in register.contracts.items():
        assert contract.touches <= known, f"{story_id} names an object nobody declared"


def test_unknowns_carry_an_owner_who_can_answer() -> None:
    """An unknown is a question with a name on it, or it never closes."""
    register = wave_0_register()
    for contract in register.contracts.values():
        for unknown in contract.unknowns:
            assert unknown.owner.strip(), f"{contract.story_id}: {unknown.question}"


def test_no_wave_0_story_is_blocked_by_an_unknown() -> None:
    """Wave 0 runs entirely on the Postgres plane.

    Every Databricks/Airflow unknown in the register blocks the socket-ladder
    rung that assumes it, not Wave 0 itself. If this ever fails, a Wave-0 story
    has acquired a dependency on an environment nobody has seen.
    """
    register = wave_0_register()
    blocked = {
        story_id: contract.blocking_unknowns
        for story_id, contract in register.contracts.items()
        if contract.blocking_unknowns
    }
    assert blocked == {}


# ── the negative this story exists for ───────────────────────────────────────


def test_retiring_a_referenced_entry_is_refused_and_names_the_referrers() -> None:
    register = ExecutionPlaneRegister.of(
        [_object()],
        [
            ExecutionPlaneContract(
                story_id="CF-V0-E8-01", writes=frozenset({"control.batch_control"})
            )
        ],
    )
    with pytest.raises(ReferencedEntryError) as raised:
        register.retire("control.batch_control")
    assert "CF-V0-E8-01" in str(
        raised.value
    ), "a refusal that does not say WHO still needs the entry sends someone grepping"
    assert "control.batch_control" in register.objects


def test_an_unreferenced_entry_can_be_retired() -> None:
    register = ExecutionPlaneRegister.of([_object("control.legacy_thing")])
    register.retire("control.legacy_thing")
    assert register.objects == {}


def test_retiring_something_never_declared_is_refused() -> None:
    with pytest.raises(ExecutionPlaneError, match="not in the register"):
        ExecutionPlaneRegister().retire("control.imaginary")


def test_a_contract_naming_an_undeclared_object_is_refused_not_auto_created() -> None:
    register = ExecutionPlaneRegister.of([_object()])
    with pytest.raises(UndeclaredObjectError) as raised:
        register.record(
            ExecutionPlaneContract(
                story_id="CF-V0-E9-01", reads=frozenset({"control.batch_controll"})
            )
        )
    assert "control.batch_controll" in str(raised.value)
    assert "CF-V0-E9-01" not in register.contracts


def test_redeclaring_an_object_differently_is_refused() -> None:
    register = ExecutionPlaneRegister.of([_object()])
    with pytest.raises(ExecutionPlaneError, match="already declared"):
        register.declare(
            PlaneObject(
                object_id="control.batch_control",
                kind=PlaneObjectKind.DATA_LAYER,
                description="something else entirely",
            )
        )


def test_redeclaring_an_object_identically_is_idempotent() -> None:
    register = ExecutionPlaneRegister.of([_object()])
    register.declare(_object())
    assert len(register.objects) == 1


# ── what a contract must say ─────────────────────────────────────────────────


def test_a_contract_that_declares_nothing_is_refused() -> None:
    with pytest.raises(ExecutionPlaneError, match="declares nothing"):
        ExecutionPlaneContract(story_id="CF-V0-E9-01")


def test_an_unknown_must_be_phrased_as_a_question() -> None:
    with pytest.raises(ExecutionPlaneError, match="not phrased as a question"):
        Unknown(question="Airflow restart semantics", owner="platform team")


def test_an_unknown_without_an_owner_is_refused() -> None:
    with pytest.raises(ExecutionPlaneError, match="no owner"):
        Unknown(question="Can Airflow restart from a stage?", owner="  ")


def test_a_plane_object_without_a_description_is_refused() -> None:
    with pytest.raises(ExecutionPlaneError, match="no description"):
        PlaneObject(object_id="control.x", kind=PlaneObjectKind.CONTROL_TABLE, description="")


def test_narrate_states_reads_writes_and_unconfirmed() -> None:
    contract = ExecutionPlaneContract(
        story_id="CF-V0-E8-01",
        reads=frozenset({"control.batch_control"}),
        writes=frozenset({"bronze.members_raw"}),
        unknowns=(Unknown(question="Does COPY INTO work?", owner="data engineering"),),
    )
    text = contract.narrate()
    assert "control.batch_control" in text
    assert "bronze.members_raw" in text
    assert "Does COPY INTO work?" in text
    assert "data engineering" in text


def test_a_story_with_no_unknowns_says_so_rather_than_staying_silent() -> None:
    contract = ExecutionPlaneContract(
        story_id="CF-V0-E3-01", reads=frozenset({"registry.governed_object"})
    )
    assert "unconfirmed: none declared" in contract.narrate()


def test_referenced_by_is_sorted_and_covers_reads_and_writes() -> None:
    register = ExecutionPlaneRegister.of(
        [_object()],
        [
            ExecutionPlaneContract(
                story_id="CF-V0-E9-01", writes=frozenset({"control.batch_control"})
            ),
            ExecutionPlaneContract(
                story_id="CF-V0-E8-01", reads=frozenset({"control.batch_control"})
            ),
        ],
    )
    assert register.referenced_by("control.batch_control") == ("CF-V0-E8-01", "CF-V0-E9-01")
