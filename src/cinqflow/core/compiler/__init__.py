"""CF-V0-E8-01 — the pipeline compiler. Metadata in, a logical plan out.

    "I want the engine to read an approved feed record and run the file through
     Landing -> Bronze -> Silver Raw automatically, tracking every step in the
     standard control tables, so that ONBOARDING A FEED STOPS MEANING WRITING A
     NEW PIPELINE — the pipeline is generated from configuration, which is the
     foundation the whole platform stands on."

The don't that shapes this module:

    "Contain any feed-specific code — everything feed-specific must come from
     metadata."

`compile_feed` takes governed objects and returns a LogicalPlan. There is no
branch on feed_id anywhere in it, and there is nowhere in the IR to put one —
PlanStep has parameters, not conditionals. That absence is the platform's
central claim made structural: adding a feed adds a registry row.
"""

from __future__ import annotations

from cinqflow.core.compiler.plan import LogicalPlan, PlanStep, StepKind, compile_steps
from cinqflow.core.model.vocabulary import Layer
from cinqflow.core.registry.contract import DqRule, SchemaContract
from cinqflow.core.registry.feed import FeedRecord

__all__ = ["LogicalPlan", "PlanStep", "StepKind", "compile_feed", "compile_steps"]


def compile_feed(
    *,
    feed: FeedRecord,
    feed_version: int,
    contract: SchemaContract,
    rules: tuple[DqRule, ...] = (),
    mapping_version: int = 1,
    target_table: str = "silver_raw.members",
    terminal_layer: Layer = Layer.SILVER_RAW,
) -> LogicalPlan:
    """Compile approved metadata into the plan the engine will run.

    Wave 0 stops at Silver Raw. `resolve_identity` and the Silver ODS load sit
    behind G4 and are Wave 3 — a plan that quietly included them would make
    Wave 0 look finished while G4 had never been built, so `terminal_layer` is
    checked by the IR rather than assumed here.

    The casting step is emitted only when the contract actually declares
    non-string columns. A plan that always claimed to cast would make the
    agent's explanation say something untrue about a feed that does not.
    """
    cast_columns = tuple(
        column.name for column in contract.columns if column.type.value != "string"
    )
    return LogicalPlan(
        feed_id=feed.feed_id,
        feed_version=feed_version,
        steps=compile_steps(
            feed_id=feed.feed_id,
            feed_version=feed_version,
            contract_version=contract.version,
            mapping_version=mapping_version,
            file_pattern=feed.file_pattern,
            column_count=len(contract.columns),
            cast_columns=cast_columns,
            rule_ids=tuple(rule.rule_id for rule in rules),
            target_table=target_table,
            terminal_layer=terminal_layer,
        ),
        terminal_layer=terminal_layer,
    )
