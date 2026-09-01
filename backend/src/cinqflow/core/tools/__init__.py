"""CF-V0-E16-09 — the certified query catalogue, declared as DATA.

    "Expose every tool as a typed, named operation with a JSON-Schema
     signature; the agent selects a tool and arguments, and never composes SQL.
     The sql_query port's free-form verb is not in any Wave-0 whitelist."
    "no tool in the catalogue can emit a data-layer row"
    — CF-V0-E16-09

Two decisions here pay for themselves for the rest of the programme.

FIRST: the catalogue is DATA, so the guarantees are asserted over the
catalogue rather than repeated per tool. "No tool returns member rows" as
sixteen careful implementations is sixteen chances to be careless; as one
test over `CATALOGUE` it is a property of the surface, and the seventeenth
tool inherits it before it is written.

SECOND: every tool declares what it READS using the same plane-object
identifiers as CF-V0-E1-01's execution-plane register. So "which tools touch
the quarantine table?" is answerable without reading code, and a tool that
declared a Bronze read would fail the register's own vocabulary check.

Deliberately absent: free-form SQL, any tool that names a member, and any
citation kind addressing a row. Text-to-tool, never text-to-SQL, until
CF-V4-E14-04 — and the Wave-0 agent declines that question BY NAME.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.citations import CitationKind
from cinqflow.core.model.governed import LifecycleState
from cinqflow.core.model.vocabulary import ErrorCategory


class ToolError(ValueError):
    """A tool that could not be declared, or an invocation that cannot be honoured."""


class UnknownToolError(ToolError):
    """A name that is not in the catalogue.

    Refused rather than attempted. A model that hallucinates a tool name must
    get a refusal naming the catalogue, not a stack trace — and never a
    fallback to something "close enough", which is how an agent quietly answers
    a different question than it was asked.
    """


class ArgumentError(ToolError):
    """Arguments that do not match the declared signature."""


@unique
class ArgType(StrEnum):
    """The whole type vocabulary. Small on purpose — a tool argument is an
    identifier, a small enum or a window, never a structure."""

    STRING = "string"
    INTEGER = "integer"
    #: A number of days back from now. Never a free-text date range: "last
    #: quarter" parsed by a model is a different window every time it is asked.
    WINDOW_DAYS = "window_days"


@dataclass(frozen=True)
class ToolParameter:
    name: str
    arg_type: ArgType
    description: str
    required: bool = False
    choices: tuple[str, ...] = ()
    default: Any = None

    def json_schema(self) -> dict[str, Any]:
        node: dict[str, Any] = {
            "type": "integer" if self.arg_type is not ArgType.STRING else "string",
            "description": self.description,
        }
        if self.choices:
            node["enum"] = list(self.choices)
        if self.arg_type is ArgType.WINDOW_DAYS:
            node["minimum"] = 1
            node["maximum"] = 400
        return node


#: Plane objects a tool may read. Every one is CONTROL or REGISTRY.
#: The data layers — bronze.*, silver_raw.*, quarantine.quarantined_rows — are
#: absent, and their absence is asserted by a test rather than described here.
READABLE = frozenset(
    {
        "registry.governed_object",
        "knowledge.reference",
        #: CF-V1-E16-04/E16-05's K2 half — `search_knowledge`'s pin, holding
        #: PHI-verified chunks of Published knowledge only. Every row already
        #: passed the SAME "no member row, no Draft, no secret" gate
        #: `chunk_incident`/`chunk_runbook`/`chunk_document` enforce before a
        #: chunk ever reaches here — this identifier could not, by construction,
        #: name a data-layer row the way `FORBIDDEN_READS` below refuses.
        "knowledge.chunk",
        "control.feed_sla_config",
        "control.input_registry",
        "control.schema_registry",
        "control.schema_drift_log",
        "control.batch_control",
        "control.batch_stage_status",
        "control.error_log",
        "control.quarantine_records",
        "control.batch_reconciliation",
        "control.sla_instance",
        "control.sla_alerts",
        # Wave-2 Phase 3's ops ledgers and DQ history — CF-V2-E12-04/E13-03/E7-05.
        # Decisions and rule verdicts, never a member row, so they join the same
        # vocabulary the eleven control tables already speak.
        "ops.incident_event",
        "ops.variance_event",
        "recon.rule_results",
    }
)

#: Objects no tool may name. Stated so the refusal can explain itself.
FORBIDDEN_READS = frozenset(
    {
        "bronze.members_raw",
        "silver_raw.members",
        "silver_ods.members",
        "quarantine.quarantined_rows",
        "recon.recon_history",
    }
)


@dataclass(frozen=True)
class ToolSpec:
    """One certified operation. Typed, named, read-only, cited.

    `aggregates_only` is not a flag the implementation is asked to respect — it
    is documentation of a fact the catalogue test proves independently by
    running every tool against a plane seeded with a canary value and asserting
    the canary never appears in a result.
    """

    name: str
    answers: str
    parameters: tuple[ToolParameter, ...] = ()
    reads: frozenset[str] = frozenset()
    cites: tuple[CitationKind, ...] = ()
    scoped_by_feed: bool = True
    aggregates_only: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        if forbidden := self.reads & FORBIDDEN_READS:
            raise ToolError(
                f"{self.name} declares a read of {', '.join(sorted(forbidden))}. "
                "No tool in the catalogue may reach a data layer — operational truth "
                "reaches a model as counts, reasons, rule ids and column names."
            )
        if unknown := self.reads - READABLE:
            raise ToolError(
                f"{self.name} reads {', '.join(sorted(unknown))}, which is not a declared "
                "plane object. The register in core/registry/wave0.py holds the vocabulary."
            )
        if not self.cites:
            raise ToolError(
                f"{self.name} cites nothing. Every result carries a resolvable citation; "
                "uncited claims are a defect class."
            )
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ToolError(f"{self.name} declares a parameter twice")

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.parameters if p.required)

    def json_schema(self) -> dict[str, Any]:
        """The signature the model is given. Nothing else describes a tool."""
        return {
            "name": self.name,
            "description": self.answers,
            "parameters": {
                "type": "object",
                "required": list(self.required),
                "properties": {p.name: p.json_schema() for p in self.parameters},
                "additionalProperties": False,
            },
        }

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Check and normalise arguments, or refuse.

        Refusal names the parameter. A tool call rejected with "invalid
        arguments" gives the one bounded retry nothing to act on.
        """
        declared = {p.name: p for p in self.parameters}
        if unknown := sorted(set(arguments) - set(declared)):
            raise ArgumentError(
                f"{self.name} has no parameter {', '.join(unknown)}. "
                f"Declared: {', '.join(declared) or 'none'}."
            )
        if absent := [name for name in self.required if arguments.get(name) in (None, "")]:
            raise ArgumentError(f"{self.name} requires {', '.join(absent)}")

        normalised: dict[str, Any] = {}
        for name, parameter in declared.items():
            value = arguments.get(name, parameter.default)
            if value is None:
                continue
            if parameter.arg_type is ArgType.STRING:
                value = str(value)
                if parameter.choices and value not in parameter.choices:
                    raise ArgumentError(
                        f"{self.name}.{name}={value!r} is not one of {', '.join(parameter.choices)}"
                    )
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ArgumentError(
                        f"{self.name}.{name} must be a whole number, got {value!r}"
                    ) from None
                if parameter.arg_type is ArgType.WINDOW_DAYS and not 1 <= value <= 400:
                    raise ArgumentError(
                        f"{self.name}.{name}={value} is outside 1-400 days. A window nobody "
                        "bounded is a full table scan with a friendly name."
                    )
            normalised[name] = value
        return normalised


def _p(
    name: str,
    arg_type: ArgType,
    description: str,
    *,
    required: bool = False,
    choices: tuple[str, ...] = (),
    default: Any = None,
) -> ToolParameter:
    return ToolParameter(
        name=name,
        arg_type=arg_type,
        description=description,
        required=required,
        choices=choices,
        default=default,
    )


FEED_ID = _p("feed_id", ArgType.STRING, "The feed's identifier.", required=True)
BATCH_ID = _p("batch_id", ArgType.STRING, "The run's identifier.", required=True)
VERSION = _p("version", ArgType.INTEGER, "A specific version; omit for the live one.")
WINDOW = _p("window_days", ArgType.WINDOW_DAYS, "How many days back to look.", default=30)
ERROR_ID_HASH = _p(
    "error_id_hash", ArgType.STRING, "The error's deterministic hash.", required=True
)
FINGERPRINT = _p("fingerprint", ArgType.STRING, "The file's content fingerprint.", required=True)
INCIDENT_ID = _p("incident_id", ArgType.STRING, "The incident's identifier.", required=True)
CYCLE_DATE = _p(
    "cycle_date",
    ArgType.STRING,
    "The cycle's business date, ISO YYYY-MM-DD. Omit for today.",
)


#: The twenty-three. Adding a twenty-fourth means adding a row here and
#: nothing else — the schema, the audit row, the citation wrapping and the
#: catalogue-wide guarantees all come from this declaration.
CATALOGUE: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            name="list_feeds",
            answers="What feeds exist, optionally filtered by domain or lifecycle state.",
            parameters=(
                _p("domain", ArgType.STRING, "Restrict to one domain."),
                _p(
                    "state",
                    ArgType.STRING,
                    "Restrict to one lifecycle state.",
                    choices=tuple(s.value for s in LifecycleState),
                ),
            ),
            reads=frozenset({"registry.governed_object"}),
            cites=(CitationKind.FEED,),
            # The list is BUILT scoped; there is no feed_id to check.
            scoped_by_feed=False,
        ),
        ToolSpec(
            name="get_feed",
            answers="What this feed is configured to do: format, schedule, landing path, owner.",
            parameters=(FEED_ID, VERSION),
            reads=frozenset({"registry.governed_object", "control.feed_sla_config"}),
            cites=(CitationKind.FEED,),
        ),
        ToolSpec(
            name="get_schema_contract",
            answers="Which columns and types are enforced for this feed, and at what severity.",
            parameters=(FEED_ID, VERSION),
            reads=frozenset({"control.schema_registry", "registry.governed_object"}),
            cites=(CitationKind.CONTRACT,),
        ),
        ToolSpec(
            name="get_dq_rules",
            answers="Which data-quality rules run for this feed, and which of them reject.",
            parameters=(FEED_ID,),
            reads=frozenset({"registry.governed_object"}),
            cites=(CitationKind.RULE,),
        ),
        ToolSpec(
            name="get_compiled_plan",
            answers="The intermediate representation the engine will actually run, step by step.",
            parameters=(FEED_ID, VERSION),
            reads=frozenset({"registry.governed_object", "control.schema_registry"}),
            cites=(CitationKind.PLAN,),
            note=(
                "The plan is what the engine runs, what the agent explains, and what grades "
                "the agent — one artifact, three jobs."
            ),
        ),
        ToolSpec(
            name="get_batch",
            answers="What happened in one run: state, feed version, business date, timings.",
            parameters=(BATCH_ID,),
            reads=frozenset({"control.batch_control"}),
            cites=(CitationKind.BATCH,),
        ),
        ToolSpec(
            name="list_batches",
            answers="Recent run history for a feed.",
            parameters=(FEED_ID, WINDOW),
            reads=frozenset({"control.batch_control"}),
            cites=(CitationKind.BATCH,),
        ),
        ToolSpec(
            name="get_stage_status",
            answers="How far a run got, stage by stage, and when each stage finished.",
            parameters=(BATCH_ID,),
            reads=frozenset({"control.batch_stage_status"}),
            cites=(CitationKind.BATCH,),
        ),
        ToolSpec(
            name="get_reconciliation",
            answers="Whether a run balanced: rows in, rows out, quarantined, attributed drops.",
            parameters=(BATCH_ID,),
            reads=frozenset({"control.batch_reconciliation"}),
            cites=(CitationKind.RECON,),
        ),
        ToolSpec(
            name="get_drop_ledger",
            answers="Every attributed drop in a run, by named reason and rule id.",
            parameters=(BATCH_ID,),
            reads=frozenset({"control.batch_reconciliation", "control.quarantine_records"}),
            cites=(CitationKind.RECON, CitationKind.RULE),
            note="Counts and reasons. There is no drop category 'other' or 'unknown'.",
        ),
        ToolSpec(
            name="list_errors",
            answers="What failed in a run and why, by category.",
            parameters=(
                BATCH_ID,
                _p(
                    "category",
                    ArgType.STRING,
                    "Restrict to one error category.",
                    # Generated from the enum, never typed out: a hand-written
                    # copy of a closed vocabulary is a copy that drifts, and
                    # the model would be offered a category the platform has
                    # never heard of.
                    choices=tuple(c.value for c in ErrorCategory),
                ),
            ),
            reads=frozenset({"control.error_log"}),
            cites=(CitationKind.ERROR,),
        ),
        ToolSpec(
            name="get_error_by_hash",
            answers="One error, by its deterministic hash — the `error:<hash>` citation's own "
            "lookup, with no batch_id required to find it.",
            parameters=(ERROR_ID_HASH,),
            reads=frozenset({"control.error_log"}),
            cites=(CitationKind.ERROR,),
        ),
        ToolSpec(
            name="get_quarantine_summary",
            answers=(
                "Counts, reasons, rule identifiers and column names for rows held back. "
                "Never row contents."
            ),
            parameters=(BATCH_ID,),
            reads=frozenset({"control.quarantine_records"}),
            cites=(CitationKind.RECON, CitationKind.RULE),
            note=(
                "The tool the guardrail is named after. It returns aggregates in every "
                "environment, including the ones that hold no PHI — because a tool that is "
                "safe only because the data is synthetic is not a safe tool."
            ),
        ),
        ToolSpec(
            name="list_schema_drift",
            answers=(
                "What this batch's arriving structure did against the contract, classified by "
                "meaning — a rename is one event, not a dropped column plus a new one."
            ),
            parameters=(BATCH_ID,),
            reads=frozenset({"control.schema_drift_log"}),
            cites=(CitationKind.BATCH,),
            note=(
                "CF-V2-E5-04's read side. Recorded for every non-NONE finding, whether or not "
                "it blocked the batch — an ADDED or REORDERED column is drift a steward should "
                "see even when it never stopped anything."
            ),
        ),
        ToolSpec(
            name="get_input_registry",
            answers="Files seen for a feed: accepted, rejected, parked, skipped as duplicates.",
            parameters=(FEED_ID, WINDOW),
            reads=frozenset({"control.input_registry"}),
            cites=(CitationKind.FILE,),
        ),
        ToolSpec(
            name="list_batch_inputs",
            answers="Files registered against one run: accepted, rejected, parked, duplicates.",
            parameters=(BATCH_ID,),
            reads=frozenset({"control.input_registry"}),
            cites=(CitationKind.FILE,),
        ),
        ToolSpec(
            name="get_file_by_fingerprint",
            answers="One file's registry entry, by its content fingerprint — the `file:<hash>` "
            "citation's own lookup, with no feed_id required to find it.",
            parameters=(FINGERPRINT,),
            reads=frozenset({"control.input_registry"}),
            cites=(CitationKind.FILE,),
        ),
        ToolSpec(
            name="lookup_reference",
            answers=(
                "The approved definition of a platform term or the description of a DQ rule, "
                "by lexical match."
            ),
            parameters=(
                _p(
                    "query",
                    ArgType.STRING,
                    "A term, a rule id, or words from either.",
                    required=True,
                ),
                _p("limit", ArgType.INTEGER, "How many matches to return.", default=5),
            ),
            reads=frozenset({"knowledge.reference"}),
            cites=(CitationKind.TERM, CitationKind.RULE),
            scoped_by_feed=False,
            note=(
                "Lexical (tsvector), not semantic. Healthcare vocabulary is code-heavy and "
                "lexical is what catches NPI, DQ-002 and BH-AF-002 that embeddings blur. "
                "The vector store stays provisioned and EMPTY until Wave 1."
            ),
        ),
        ToolSpec(
            name="search_knowledge",
            answers=(
                "The nearest prior document pages, runbook steps and closed-incident "
                "narratives, by semantic similarity — 'has anything LIKE this happened "
                "before', not 'what does this term mean'."
            ),
            parameters=(
                _p(
                    "query",
                    ArgType.STRING,
                    "Free text describing the failure or the concept to search for.",
                    required=True,
                ),
                _p("limit", ArgType.INTEGER, "How many matches to return.", default=5),
            ),
            reads=frozenset({"knowledge.chunk"}),
            cites=(
                CitationKind.DOCUMENT,
                CitationKind.RUNBOOK,
                CitationKind.BATCH,
                CitationKind.ERROR,
            ),
            scoped_by_feed=False,
            note=(
                "CF-V1-E16-04/E16-05 — the semantic (K2) half of hybrid retrieval, over "
                "chunked, PHI-verified, Published knowledge. Unscoped by feed on purpose: "
                "the same failure fingerprint recurring on a second feed is exactly what "
                "this exists to surface (ADR-0023). Degrades to an honest empty result — "
                "never a fabricated one — when the vector plane holds nothing yet."
            ),
        ),
        # ── Wave 2 · the ops ledgers reach the catalogue ──────────────────────
        ToolSpec(
            name="get_arrival_board",
            answers=(
                "Expected, received, missing and at-risk counts for one feed's cycle "
                "on a given business date."
            ),
            parameters=(FEED_ID, CYCLE_DATE),
            reads=frozenset({"control.sla_instance"}),
            cites=(CitationKind.FEED,),
            note=(
                "cycle_date is a plain ISO day, never a free-text range — an unparseable "
                "or omitted date falls back to today, the fallback is never silent."
            ),
        ),
        ToolSpec(
            name="get_sla_history",
            answers=(
                "This feed's arrival record over the trailing window: on time, "
                "delayed or breached, cycle by cycle."
            ),
            parameters=(FEED_ID, WINDOW),
            reads=frozenset({"control.sla_instance"}),
            cites=(CitationKind.FEED,),
        ),
        ToolSpec(
            name="get_incident",
            answers=(
                "One incident: root cause, its consequences, any matched recovery guide, "
                "and the evidence behind all three."
            ),
            parameters=(INCIDENT_ID,),
            reads=frozenset(
                {
                    "ops.incident_event",
                    "control.error_log",
                    "registry.governed_object",
                    "control.batch_control",
                }
            ),
            cites=(CitationKind.ERROR, CitationKind.BATCH),
            note=(
                "The cascade and guide match are RECOMPUTED from the error log on every "
                "call; only what a human decided (acknowledged, assigned, resolved) comes "
                "from the ledger, so the two can never drift apart."
            ),
        ),
        ToolSpec(
            name="list_incidents",
            answers="Open incidents — not yet resolved or closed — newest first.",
            parameters=(
                _p(
                    "feed_id",
                    ArgType.STRING,
                    "Restrict to one feed; omit for every feed in scope.",
                ),
            ),
            reads=frozenset({"ops.incident_event"}),
            cites=(CitationKind.BATCH,),
            # A fleet-wide question by default, exactly like `list_feeds`: the
            # list is built scoped, row by row, rather than checked against one
            # feed_id up front.
            scoped_by_feed=False,
        ),
        ToolSpec(
            name="get_reliability_score",
            answers="The six-signal reliability score for one feed, decomposed, with the overall.",
            parameters=(FEED_ID,),
            reads=frozenset(
                {
                    "recon.rule_results",
                    "control.sla_instance",
                    "control.batch_control",
                    "control.batch_reconciliation",
                    "control.schema_drift_log",
                }
            ),
            cites=(CitationKind.FEED,),
            note="A signal with nothing to measure is UNMEASURED, never scored zero.",
        ),
        ToolSpec(
            name="get_certification",
            answers="The derived certification verdict for one batch, and every check behind it.",
            parameters=(BATCH_ID,),
            reads=frozenset(
                {
                    "control.batch_reconciliation",
                    "recon.rule_results",
                    "control.schema_drift_log",
                    "control.sla_instance",
                    "ops.variance_event",
                    "control.batch_control",
                }
            ),
            cites=(CitationKind.BATCH,),
            note=(
                "Derived fresh on every read from retained history — there is no stored "
                "verdict to drift."
            ),
        ),
    )
}

#: The Wave-0 agent's whitelist. R0: read tools only, at any confidence.
#: It is the whole catalogue because the whole catalogue is read-only — which
#: is the point of certifying it rather than granting a database connection.
READ_ONLY_WHITELIST: frozenset[str] = frozenset(CATALOGUE)


@dataclass(frozen=True)
class ToolSignatureSet:
    """What is handed to a model. Never the implementations, never a connection."""

    specs: tuple[ToolSpec, ...] = field(default_factory=tuple)

    def as_json_schemas(self) -> list[dict[str, Any]]:
        return [spec.json_schema() for spec in self.specs]

    def narrate(self) -> str:
        return "\n".join(
            f"- {spec.name}({', '.join(p.name for p in spec.parameters)}): {spec.answers}"
            for spec in self.specs
        )


def signatures(names: frozenset[str] = READ_ONLY_WHITELIST) -> ToolSignatureSet:
    unknown = sorted(names - set(CATALOGUE))
    if unknown:
        raise UnknownToolError(f"not in the catalogue: {', '.join(unknown)}")
    return ToolSignatureSet(tuple(CATALOGUE[name] for name in sorted(names)))


def spec_for(name: str) -> ToolSpec:
    try:
        return CATALOGUE[name]
    except KeyError:
        raise UnknownToolError(
            f"{name!r} is not a certified tool. The catalogue is: {', '.join(sorted(CATALOGUE))}."
        ) from None
