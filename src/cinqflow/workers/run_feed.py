"""CF-V1-E8-03 — the handler on the other end of `pipeline.run_feed`.

    "I want the engine to run feeds on their registry schedules"
    — CF-V1-E8-03

    "onboarding a feed stops meaning writing a new pipeline — the pipeline is
     generated from configuration"
    — CF-V0-E8-01

THE THIRD MISSING CALLER IN ONE CHAIN, AND THE LAST. `workers.scheduler`
decides what is due and enqueues it; `workers.consumer` routes a topic to a
handler; `workers.pipeline.PipelineRunner` runs a file. Nothing joined them.
`cinqflow ingest` ran the spine for exactly ONE feed, and it could only ever
run that one: `FEED`, `CONTRACT`, `DQ_002` and `PLAN` are module constants in
`installer/cli.py` — the Wave-0 anchor, hardcoded. So "the pipeline is
generated from configuration" was true of the COMPILER and false of everything
that called it, and a second feed could not be run at all without editing
Python.

THIS MODULE IS THAT SENTENCE MADE TRUE. It takes a feed id and a business
date, reads the PUBLISHED contract, rules and mapping for that feed out of
metadata, compiles the plan, lists the feed's own incoming folder and runs
whatever it finds. There is no feed-specific anything in it. Registering a
new payer is a registry row.

PUBLISHED ONLY, AND THE READER IS THE GATE. `is_executable` refuses anything
that is not Published, so a draft contract cannot run even if this module
asked for one — the same discipline `core.registry.wave0` states for the
engine. That is also why this worker and `workers.sample_test` are DIFFERENT
modules rather than one with a flag: the sample test deliberately runs the
DRAFT a BA is still building, and a single code path with a `published=False`
parameter is one flag away from running an unapproved mapping in production.

IDEMPOTENT BECAUSE LANDING IS. Every file goes through `PipelineRunner.run`,
which registers the fingerprint and SKIPS one it has seen — so a message
redelivered after a crash re-reads the same folder and loads nothing twice.
This module keeps no "already done" set of its own; the guarantee lives where
`CF-V0-E8-02` put it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cinqflow.core.compiler import compile_feed
from cinqflow.core.delivery import landing_key
from cinqflow.core.model.files import FileRef
from cinqflow.core.model.governed import ObjectType
from cinqflow.core.model.vocabulary import LandingFolder
from cinqflow.core.registry import contract as contract_registry
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry.contract import DqRule, SchemaContract
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.ports.metadata_db import MetadataDbPort, ObjectNotFoundError
from cinqflow.ports.storage import StoragePort
from cinqflow.workers.pipeline import PipelineRunner, RunOutcome

__all__ = ["FeedRunError", "FeedRunWorker", "RunRequest"]


class FeedRunError(RuntimeError):
    """This feed cannot be run — as distinct from a run that failed, which is
    a batch in FAILED with error rows, not an exception."""


@dataclass(frozen=True)
class RunRequest:
    feed_id: str
    business_date: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RunRequest:
        """The queue's message, validated at the edge.

        A payload missing either field is a programming error upstream, not a
        transient failure, so it raises rather than retrying forever — the
        queue's `claim()` would otherwise return it to `pending` on every
        sweep and the topic would never drain.
        """
        feed_id = str(payload.get("feed_id") or "").strip()
        business_date = str(payload.get("business_date") or "").strip()
        if not feed_id or not business_date:
            raise FeedRunError(f"a run message needs feed_id and business_date; got {payload!r}")
        return cls(feed_id=feed_id, business_date=business_date)


@dataclass(frozen=True)
class FeedRunWorker:
    """One feed, one business date, every file waiting in its incoming folder."""

    metadata: MetadataDbPort
    storage: StoragePort
    runner: PipelineRunner

    def handle(self, payload: dict[str, Any]) -> None:
        """The `Consumer` handler, registered against `RUN_FEED_TOPIC`.

        Returns None, matching `workers.consumer.Handler`, and that is not a
        formality: the consumer ACKNOWLEDGES on a clean return, so a handler
        whose value it discarded would still have to be typed as if the value
        mattered. Callers that want the outcomes call `run` directly — a test,
        a CLI command reporting what it did — and get them without the queue
        in the way.
        """
        self.run(RunRequest.from_payload(payload))

    def run(self, request: RunRequest) -> tuple[RunOutcome, ...]:
        feed_object = self._published(ObjectType.FEED, request.feed_id)
        feed = feed_registry.from_governed(feed_object)
        contract_object = self._published(ObjectType.CONTRACT, request.feed_id)
        contract: SchemaContract = contract_registry.from_governed(contract_object)
        rules = self._rules(request.feed_id)
        mapping = self._mapping(request.feed_id)
        glossary = self._glossary()

        plan = compile_feed(
            feed=feed,
            feed_version=feed_object.version,
            contract=contract,
            rules=rules,
        )

        outcomes: list[RunOutcome] = []
        for file in self._waiting(feed.landing_path, request.business_date):
            outcomes.append(
                self.runner.run(
                    file,
                    feed=feed,
                    feed_version=feed_object.version,
                    contract=contract,
                    rules=rules,
                    plan=plan,
                    business_date=request.business_date,
                    glossary=glossary,
                    mapping=mapping,
                )
            )
        return tuple(outcomes)

    # ── what is waiting ──────────────────────────────────────────────────────

    def _waiting(self, landing_path: str, business_date: str) -> tuple[FileRef, ...]:
        """Every file in this feed's incoming folder for this date.

        The prefix is built by `core.delivery.landing_key` — THE one place the
        layout is spelled — with an empty filename, so the lister and the
        writer cannot disagree about where files are. A second composition
        here is how a file ends up on disk and invisible.
        """
        prefix = landing_key(
            landing_path=landing_path,
            filename="x",
            business_date=business_date,
            folder=LandingFolder.INCOMING,
        ).rsplit("/", 1)[0]
        return tuple(self.storage.list_files(prefix))

    # ── published metadata, and nothing else ─────────────────────────────────

    def _published(self, object_type: ObjectType, feed_id: str):  # type: ignore[no-untyped-def]
        """The highest EXECUTABLE version, never the highest version number.

        `metadata.get` returns the latest version whatever its state, so a
        draft amendment sitting on top of a published object would shadow it —
        the exact bug `installer/cli.py`'s W1-36 note records for mappings,
        generalised here because a contract has the same failure mode and a
        worse consequence.
        """
        executable = [
            obj for obj in self.metadata.history(object_type, feed_id) if obj.is_executable
        ]
        if not executable:
            raise FeedRunError(
                f"feed {feed_id!r} has no PUBLISHED {object_type.value}. The engine reads "
                "published metadata and nothing else — approve it before it can run."
            )
        return max(executable, key=lambda obj: obj.version)

    def _rules(self, feed_id: str) -> tuple[DqRule, ...]:
        """Rules are OPTIONAL. A feed with none loads every row it can cast,
        which is a legitimate configuration and not a missing one."""
        try:
            executable = [
                obj
                for obj in self.metadata.history(ObjectType.DQ_RULE, feed_id)
                if obj.is_executable
            ]
        except ObjectNotFoundError:  # pragma: no cover - history returns empty
            return ()
        if not executable:
            return ()
        return contract_registry.rules_from_governed(max(executable, key=lambda o: o.version))

    def _mapping(self, feed_id: str):  # type: ignore[no-untyped-def]
        from cinqflow.core import mapping as mapping_core

        executable = [
            obj for obj in self.metadata.history(ObjectType.MAPPING, feed_id) if obj.is_executable
        ]
        if not executable:
            return None
        return mapping_core.from_governed(max(executable, key=lambda o: o.version))

    def _glossary(self) -> Glossary:
        """CF-V2-E5-04's classifier needs one: a payer's rename is drift by
        MEANING rather than a dropped column plus a new one."""
        return Glossary(
            terms=tuple(
                GlossaryTerm.from_governed(obj)
                for obj in self.metadata.list(ObjectType.GLOSSARY_TERM)
            )
        )
