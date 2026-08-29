"""CF-V0-E1-01 — the execution-plane contract register.

    "Every story's execution-plane contract — which control tables and platform
     APIs it reads and writes, and which facts about the real Databricks/Airflow
     environment remain unconfirmed — is still recorded as a mandatory field on
     the story template. The unknowns register lives on as a checklist field,
     cost-free."
    — ADR-0010, the one practice that survives Epic 1's descoping

So this story is deliberately SMALL. ADR-0010 descopes Epic 1's harvester;
memory/04-corpus/02-known-discrepancies.md D16 marks E1-01 alone as
scheduled-but-reduced, "since the DoD depends on it". What Wave 0 owes is not
an inventory tool — it is a machine-checkable version of a checklist field, so
that "every story declares its execution-plane contract" is a CI gate rather
than a habit that decays by Wave 2.

The design decision worth stating: an UNKNOWN is a first-class member of the
contract, not a gap in it. A story that reads `control.batch_control` and does
not know whether Airflow can even see that schema has declared two facts, and
the second one is the one that will hurt. A register that only holds what we
know is a register that reads as complete while the unconfirmed facts live in
somebody's head.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum, unique


class ExecutionPlaneError(ValueError):
    """A contract that does not describe reality well enough to be recorded."""


class UndeclaredObjectError(ExecutionPlaneError):
    """A contract naming a plane object the register has never heard of.

    Refused rather than auto-created: a typo that silently registers a new
    object turns the register into a list of typos, and the whole value of the
    register is that its names are the same names twice.
    """


class ReferencedEntryError(ExecutionPlaneError):
    """An attempt to remove a register entry some story's contract still names.

    The documented negative for this story. The register's only real job is to
    be the thing contracts point at; an entry that can vanish underneath a
    contract makes every contract provisional.
    """


@unique
class PlaneObjectKind(StrEnum):
    """What sort of thing on the execution plane is being touched.

    Control tables and data layers are ours. `PLATFORM_API` and
    `EXTERNAL_SYSTEM` are not, which is exactly why they attract unknowns.
    """

    CONTROL_TABLE = "control_table"
    DATA_LAYER = "data_layer"
    REGISTRY_TABLE = "registry_table"
    PLATFORM_API = "platform_api"
    EXTERNAL_SYSTEM = "external_system"


@dataclass(frozen=True)
class PlaneObject:
    """One addressable thing on the execution plane."""

    object_id: str
    kind: PlaneObjectKind
    description: str

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise ExecutionPlaneError("a plane object without an id is not addressable")
        if not self.description.strip():
            raise ExecutionPlaneError(
                f"{self.object_id} has no description — an entry nobody can read is "
                "not a register entry, it is a string"
            )


@dataclass(frozen=True)
class Unknown:
    """A fact about the real environment that is NOT confirmed.

    `owner` is required and must name someone who can answer. An unknown with
    no owner is a worry; an unknown with an owner is a question, and questions
    get closed.
    """

    question: str
    owner: str
    blocks: bool = False

    def __post_init__(self) -> None:
        if not self.question.strip().endswith("?"):
            raise ExecutionPlaneError(
                f"{self.question!r} is not phrased as a question. An unknown that is not "
                "a question cannot be answered, so it is never closed."
            )
        if not self.owner.strip():
            raise ExecutionPlaneError(
                f"{self.question!r} has no owner — an unowned unknown is a worry, not a question"
            )


@dataclass(frozen=True)
class ExecutionPlaneContract:
    """What one story reads, what it writes, and what it does not know.

    Stored as the body of a governed object of type
    `ObjectType.EXECUTION_PLANE_CONTRACT`, so it inherits the one lifecycle and
    the audit trail like everything else — a register that could be edited
    without an audit row would be the one ungoverned object in a platform whose
    entire argument is that there are none.
    """

    story_id: str
    reads: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()
    unknowns: tuple[Unknown, ...] = ()

    def __post_init__(self) -> None:
        if not self.story_id.strip():
            raise ExecutionPlaneError("a contract belongs to a story")
        if not self.reads and not self.writes and not self.unknowns:
            raise ExecutionPlaneError(
                f"{self.story_id} declares nothing. A story that genuinely touches no "
                "control table, no data layer and no platform API should say so with an "
                "unknown, not with silence — silence is indistinguishable from "
                "not having asked."
            )

    @property
    def touches(self) -> frozenset[str]:
        return self.reads | self.writes

    @property
    def blocking_unknowns(self) -> tuple[Unknown, ...]:
        return tuple(u for u in self.unknowns if u.blocks)

    def narrate(self) -> str:
        """The contract in prose, for the story page and for the agent.

        Same words in the register, the UI and an answer — one vocabulary, not
        three renderings of it.
        """
        lines = [f"{self.story_id} execution-plane contract:"]
        lines.append(f"  reads:  {', '.join(sorted(self.reads)) or 'nothing'}")
        lines.append(f"  writes: {', '.join(sorted(self.writes)) or 'nothing'}")
        if self.unknowns:
            lines.append("  unconfirmed:")
            for unknown in self.unknowns:
                mark = "BLOCKS" if unknown.blocks else "open"
                lines.append(f"    [{mark}] {unknown.question}  ({unknown.owner})")
        else:
            lines.append("  unconfirmed: none declared")
        return "\n".join(lines)


@dataclass
class ExecutionPlaneRegister:
    """The objects, and the contracts that point at them.

    Mutable on purpose — it is a register — but every mutation that could
    invalidate an existing contract is refused rather than cascaded.
    """

    _objects: dict[str, PlaneObject] = field(default_factory=dict)
    _contracts: dict[str, ExecutionPlaneContract] = field(default_factory=dict)

    @classmethod
    def of(
        cls, objects: Iterable[PlaneObject], contracts: Iterable[ExecutionPlaneContract] = ()
    ) -> ExecutionPlaneRegister:
        register = cls()
        for obj in objects:
            register.declare(obj)
        for contract in contracts:
            register.record(contract)
        return register

    @property
    def objects(self) -> Mapping[str, PlaneObject]:
        return dict(self._objects)

    @property
    def contracts(self) -> Mapping[str, ExecutionPlaneContract]:
        return dict(self._contracts)

    def declare(self, obj: PlaneObject) -> None:
        existing = self._objects.get(obj.object_id)
        if existing is not None and existing != obj:
            raise ExecutionPlaneError(
                f"{obj.object_id} is already declared as {existing.kind.value} "
                f"({existing.description!r}). Redeclaring it differently would silently "
                "change what every existing contract means."
            )
        self._objects[obj.object_id] = obj

    def record(self, contract: ExecutionPlaneContract) -> None:
        unknown_names = sorted(contract.touches - self._objects.keys())
        if unknown_names:
            known = ", ".join(sorted(self._objects)) or "nothing yet"
            raise UndeclaredObjectError(
                f"{contract.story_id} names {', '.join(unknown_names)}, which the register "
                f"does not hold. Declared objects: {known}."
            )
        self._contracts[contract.story_id] = contract

    def referenced_by(self, object_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                story_id
                for story_id, contract in self._contracts.items()
                if object_id in contract.touches
            )
        )

    def retire(self, object_id: str) -> None:
        """Remove an entry — only if no contract still names it.

        The negative test for this story makes the attempt and asserts both the
        refusal and the named referrers, because a refusal that does not say WHO
        still needs the entry sends someone grepping.
        """
        if object_id not in self._objects:
            raise ExecutionPlaneError(f"{object_id} is not in the register")
        referrers = self.referenced_by(object_id)
        if referrers:
            raise ReferencedEntryError(
                f"{object_id} is still named by {', '.join(referrers)}. Retire the "
                "contract's reference first — an entry that vanishes underneath a "
                "contract makes every contract provisional."
            )
        del self._objects[object_id]

    def missing_contracts(self, story_ids: Iterable[str]) -> tuple[str, ...]:
        """Which of these stories have not declared one.

        This is the DoD gate: the Definition of Done requires the field on
        every story, so the check is a function and not a review question.
        """
        return tuple(sorted(s for s in story_ids if s not in self._contracts))
