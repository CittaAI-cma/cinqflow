"""The persona home, and the merge rule that constrains it.

    "Persona shapes the home and the ranking. It never shapes the vocabulary
     or the depth."
    — ADR-0020

The reason these are unit tests rather than browser tests: a permission matrix
tested by clicking is a permission matrix nobody tests, and the same is true of
a ranking matrix. Every role against every slot, in milliseconds, with no
server running.
"""

from __future__ import annotations

import pytest

from cinqflow.core.model.identity import Role
from cinqflow.core.navigation import ACTIVE_WAVE
from cinqflow.core.persona import _HOME, SLOTS, HomeSlot, home_for
from cinqflow.core.security import Action

VIEW_ONLY = frozenset({Action.VIEW})
VIEW_AND_ASK = frozenset({Action.VIEW, Action.ASK_AGENT})


def test_every_wave0_role_has_a_home() -> None:
    """A role with no home falls through to a generic screen, which is how a
    persona-shaped product quietly stops being one."""
    for role in Role:
        assert home_for(frozenset({role}), VIEW_AND_ASK), f"{role} has no home slots"


def test_no_slot_from_an_unactivated_wave_is_ever_returned() -> None:
    """A Wave-1 slot is ABSENT, never a stub. Same rule navigation.py applies
    to Wave-1 destinations, and for the same reason: an empty card teaches a
    user the platform is broken."""
    for role in Role:
        for slot in home_for(frozenset({role}), VIEW_AND_ASK):
            assert slot.wave <= ACTIVE_WAVE, f"{slot.key} is Wave {slot.wave}"


def test_wave1_slots_are_declared_but_inert() -> None:
    """They exist in the vocabulary so the whole set is reviewable at once, and
    so activating one is a wave bump rather than a home-screen rewrite."""
    assert SLOTS[HomeSlot.TRUST_TODAY].wave == 1
    assert not SLOTS[HomeSlot.TRUST_TODAY].active


def test_a_slot_whose_action_is_refused_is_not_offered() -> None:
    """Hiding is a courtesy; the refusal is the control. But offering a card
    the server will refuse is worse than either — it is a promise the build
    cannot keep."""
    read_only = home_for(frozenset({Role.READ_ONLY}), VIEW_AND_ASK)
    assert HomeSlot.ASK_SHORTCUT in [s.key for s in read_only]

    without_ask = home_for(frozenset({Role.READ_ONLY}), VIEW_ONLY)
    assert HomeSlot.ASK_SHORTCUT not in [s.key for s in without_ask]


def test_the_engineer_leads_with_harm_and_read_only_leads_with_arrival() -> None:
    """The ranking half of the merge rule, stated as the assertion it is.

    An engineer opens the screen to find the most expensive thing to ignore.
    Read-Only cannot act on harm, so leading with it would hand them a list of
    things to go and ask somebody else about.
    """
    engineer = home_for(frozenset({Role.ENGINEER}), VIEW_AND_ASK)
    assert engineer[0].key is HomeSlot.NEEDS_YOU

    analyst = home_for(frozenset({Role.READ_ONLY}), VIEW_AND_ASK)
    assert analyst[0].key is HomeSlot.ARRIVED


def test_administrator_leads_with_governance_evidence() -> None:
    """An administrator manages ACCESS and cannot approve or operate — so
    their home is what was refused and what changed, not a work queue."""
    admin = home_for(frozenset({Role.ADMINISTRATOR}), VIEW_AND_ASK)
    assert admin[0].key is HomeSlot.REFUSALS_TODAY


def test_a_multi_role_user_gets_each_slot_once() -> None:
    keys = [slot.key for slot in home_for(frozenset(Role), VIEW_AND_ASK)]
    assert len(keys) == len(set(keys))


def test_no_principal_ever_gets_an_empty_home() -> None:
    """Including a role combination nobody planned for. An empty home is the
    'broken shell' the no-access page exists to avoid."""
    assert home_for(frozenset(), VIEW_ONLY)


@pytest.mark.parametrize("role", list(Role))
def test_every_ranked_slot_exists_in_the_catalogue(role: Role) -> None:
    """_HOME names slots by key; a typo there would silently drop a card."""
    for key in _HOME.get(role, ()):
        assert key in SLOTS, f"{role} ranks unknown slot {key}"


def test_every_slot_answers_a_question() -> None:
    """A slot nobody can write a one-line answer for is a slot that has not
    been designed. Same discipline as navigation.Destination.answers."""
    for slot in SLOTS.values():
        assert slot.answers.strip(), f"{slot.key} answers nothing"
        assert slot.answers.endswith("."), f"{slot.key} answers is not a sentence"
