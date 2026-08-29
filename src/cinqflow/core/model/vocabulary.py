"""The programme's fixed vocabulary — every closed set the architecture names.

Vocabulary is load-bearing. The incumbent platform's defining failure was
knowledge living in spreadsheets and individual memories with contradictory
versions; a platform that lets each screen invent its own words for "late"
rebuilds that failure in software. So every set here is closed, and adding a
member means changing a plate.

Cited, never paraphrased:
  docs/architecture/plates/06-the-medallion-spine-and-its-gates.md   stages, gates, states
  docs/architecture/plates/11-agent-runtime-and-the-risk-router.md   risk classes
  docs/architecture/plates/05-socket-ladder.md                       modes
  docs/architecture/plates/13-three-lane-ai-testing.md               lanes
"""

from __future__ import annotations

from enum import Enum, IntEnum, StrEnum, unique


@unique
class StatusWord(StrEnum):
    """The seven words a user is ever shown. There is no eighth.

    "No synonyms, no per-screen dialects, no 'Perfect'."

    The richer machines below (BatchState, FileState) are internal. Every
    user-facing surface projects onto these seven, and a CI lexicon test from
    Wave 2 fails the build if a synonym appears in a rendered surface.
    """

    EXPECTED = "Expected"  # expected, but not arrived yet
    RECEIVED = "Received"  # it has arrived
    PROCESSING = "Processing"  # loading, validating or transforming
    COMPLETED = "Completed"  # done, and available
    NEEDS_REVIEW = "Needs Review"  # a person must review or approve something
    NEEDS_ATTENTION = "Needs Attention"  # an issue requires action
    MISSING = "Missing"  # expected data has not arrived


@unique
class Layer(StrEnum):
    """The medallion spine. Data cannot skip a layer, and cannot advance
    until that layer's gate passes."""

    LANDING = "landing"
    BRONZE = "bronze"
    SILVER_RAW = "silver_raw"
    IDENTITY = "identity"
    SILVER_ODS = "silver_ods"
    GOLD = "gold"

    @classmethod
    def after(cls, layer: Layer) -> Layer | None:
        """The next layer, or None at the end of the spine."""
        ordered = list(cls)
        index = ordered.index(layer)
        return ordered[index + 1] if index + 1 < len(ordered) else None


@unique
class Gate(StrEnum):
    """The five gates. Each guards exactly one layer transition.

    G1 structural     landing    -> bronze      filename, size, structure, fingerprint,
                                                 arrival SLA, idempotency
    G2 schema + DQ    bronze     -> silver_raw  drift classified BY MEANING, contract
                                                 enforced, DQ rules by severity
    G3 completeness   silver_raw -> identity    record-level reconciliation, EVERY drop
                                                 attributed
    G4 resolution     identity   -> silver_ods  submitted == resolved + unresolved + failed;
                                                 unresolved NEVER loads
    G5 certification  silver_ods -> gold        relationship validation, consumer
                                                 compatibility, atomic publish
    """

    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"

    @property
    def between(self) -> tuple[Layer, Layer]:
        transitions = {
            Gate.G1: (Layer.LANDING, Layer.BRONZE),
            Gate.G2: (Layer.BRONZE, Layer.SILVER_RAW),
            Gate.G3: (Layer.SILVER_RAW, Layer.IDENTITY),
            Gate.G4: (Layer.IDENTITY, Layer.SILVER_ODS),
            Gate.G5: (Layer.SILVER_ODS, Layer.GOLD),
        }
        return transitions[self]

    @property
    def name_in_plain_english(self) -> str:
        return {
            Gate.G1: "structural",
            Gate.G2: "schema and data quality",
            Gate.G3: "completeness",
            Gate.G4: "resolution",
            Gate.G5: "certification",
        }[self]


@unique
class ErrorCategory(StrEnum):
    """A fixed set. Thresholds are evaluated on structured metrics
    (records_in, error_count) — never on naming conventions."""

    FILE = "FILE_ERROR"
    SCHEMA = "SCHEMA_ERROR"
    VALIDATION = "VALIDATION_ERROR"
    TRANSFORMATION = "TRANSFORMATION_ERROR"
    INTEGRATION = "INTEGRATION_ERROR"
    SYSTEM = "SYSTEM_ERROR"


@unique
class BatchState(StrEnum):
    """Internal. Users see a StatusWord."""

    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RESTARTED = "RESTARTED"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    BLOCKED = "BLOCKED"


@unique
class FileState(StrEnum):
    """Internal. RECEIVED -> ACCEPTED | REJECTED -> PROCESSED."""

    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PROCESSED = "PROCESSED"


@unique
class LandingFolder(StrEnum):
    """Landing is the Control Entry Point: structural validation only.

    PARKED is not decoration. "Every arriving file is registered — including
    unexpected ones, which are parked and surfaced, never ignored."
    """

    INCOMING = "incoming"
    PROCESSED = "processed"
    REJECTED = "rejected"
    ARCHIVE = "archive"
    PARKED = "parked"


class RiskClass(Enum):
    """Risk class gates capability. Confidence only routes WITHIN a class.

    This is the single most consequential rule in the intelligence plane, and
    it is expressed as behaviour rather than documentation: `at_confidence`
    returns the same class it was asked of, always. A confidence value has no
    mechanism by which to raise a class, because none is offered.
    """

    R0 = ("observe", True, True, True)
    R1 = ("safe_ops", False, True, True)
    R2 = ("config_proposal", False, True, True)
    R3 = ("code_change", False, True, True)
    # R4 is human-always. Never automated. NOT configurable — at any confidence.
    R4 = ("phi_consequential", False, False, False)

    def __init__(
        self, label: str, always_allowed: bool, automatable: bool, configurable: bool
    ) -> None:
        self.label = label
        self.always_allowed = always_allowed
        self.automatable = automatable
        self.configurable = configurable

    def at_confidence(self, confidence: float) -> RiskClass:
        """Confidence routes within a class. It never changes the class.

        Deliberately ignores its argument. That is not an oversight — it is the
        invariant, written where a future edit would have to delete it on purpose.
        """
        _ = confidence
        return self


@unique
class Mode(StrEnum):
    """A profile field. Partial permission is a mode, not a failure.

    The conformance kit's verdict sets it: GREEN -> full, AMBER -> propose_only,
    RED -> refuse to operate. Every feature must behave correctly in all three.
    """

    FULL = "full"
    PROPOSE_ONLY = "propose_only"
    OBSERVE_ONLY = "observe_only"


class TestLane(IntEnum):
    """Lane 1 mock proves machinery · Lane 2 replay proves wiring ·
    Lane 3 real proves quality.

    Neither prohibition is negotiable: no threshold may be claimed from Lane 1
    or 2, and no machinery test may require Lane 3.
    """

    # Not a pytest class, despite the name. The name is the architecture's
    # ("test lanes 1 mock, 2 replay, 3 real API") and the vocabulary wins.
    __test__ = False

    MOCK = 1
    REPLAY = 2
    REAL = 3

    @property
    def may_claim_quality(self) -> bool:
        return self is TestLane.REAL

    @property
    def holds_credentials(self) -> bool:
        """Lanes 1 and 2 hold no live credentials, so a misclassified test
        fails loudly rather than passing against a stand-in."""
        return self is TestLane.REAL


@unique
class ActorType(StrEnum):
    """Every audit row names its actor type. Unambiguously."""

    HUMAN = "human"
    SYSTEM = "system"
    AI = "ai"
