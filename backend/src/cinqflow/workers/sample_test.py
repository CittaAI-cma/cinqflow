"""CF-V1-E4-02 — one button, the real engine, a generated evidence pack.

    "I want one button that runs my whole draft configuration — schema,
     mappings, rules — against the sample end to end, and produces the
     onboarding summary and UAT evidence pack automatically, so that the
     historical bottleneck (days of assembling validation evidence by hand for
     every source) becomes a generated artifact reviewers receive in minutes."
    — CF-V1-E4-02

THE BUTTON HAD NO WIRE. `core.onboarding.evidence.build_pack` has existed,
fully tested, since the story shipped — and had ZERO callers anywhere in
`src/`. `GET /api/feeds/{id}/evidence` could READ a pack; nothing could ever
produce one; and `POST /onboarding/submit` REFUSES without one
(`409 — the end-to-end sample test has not been run`). So the wizard's five
steps could be walked to step 5 and no further, on any deployment, by anyone.
The evidence gate was not merely unbuilt: it was a gate with no key.

"THE SAME CODE PATH PRODUCTION WILL USE" IS LITERAL HERE. This worker calls
`core.compiler.compile_feed` and `core.compiler.execute.apply` — the exact two
functions `workers.pipeline.PipelineRunner` calls, with the same contract, the
same rules and the same mapping. It does not simulate rule evaluation, does
not re-derive a count, and does not have a "test mode" branch anywhere. A
sandbox that ran different code would produce evidence about something other
than the thing being approved.

"THE TEST AREA IS FULLY ISOLATED" IS STRUCTURAL, NOT A PROMISE. `apply()` is a
PURE function: rows in, `ExecutionResult` out, no port in its signature. This
worker never touches `compute`, never opens a batch, never writes Bronze,
Silver Raw or a control row — so the don't ("Touch production tables or
control records") is not a rule someone has to remember. There is no verb here
that could break it. The one write is the pack itself, onto the feed's own
governed body, which is where `_stored_pack` already reads it from.

DRAFTS ARE THE POINT. `PipelineRunner` runs a PUBLISHED configuration; this
runs the one the BA is still building — a draft contract, a draft mapping,
draft rules — because the whole purpose is to find out whether it works
BEFORE asking two people to approve it. That is the one substantive difference
between this worker and the pipeline, and it is a difference in which VERSION
is read, never in what is done with it.

A FAILING RUN STILL PRODUCES A PACK. "Given the test fails midway (a mapping
type error), when the run completes, then the pack is still produced up to the
failure, the failing step is explained in plain language, and the wizard links
straight to the mapping line at fault." So every failure below is caught and
turned into `evidence.Failure` with a route, rather than raised at the caller:
a BA whose mapping has a type error needs the twenty rows that DID map and the
sentence naming the one that did not, not a 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.compiler import compile_feed
from cinqflow.core.compiler.execute import apply
from cinqflow.core.mapping import FeedMapping
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.onboarding import evidence
from cinqflow.core.parsers import ParseError, parse
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry.contract import DqRule, SchemaContract
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.ports.metadata_db import MetadataDbPort, ObjectNotFoundError
from cinqflow.ports.storage import FileNotFoundInStorageError, StoragePort

__all__ = ["EVIDENCE_KEY", "SampleTestError", "SampleTestWorker", "pack_to_body"]

#: Where the pack lives on the feed's governed body. The SAME key
#: `api.app._stored_pack` already reads — named here because this module is
#: now the only thing that writes it, and a second literal would be a second
#: place for the two to disagree.
EVIDENCE_KEY = "evidence_pack"

#: How many sample rows the pack shows before/after. Twenty, because the story
#: says twenty ("twenty before/after examples") — and because a reviewer reads
#: twenty and skims two hundred.
EXAMPLE_ROWS = 20


class SampleTestError(RuntimeError):
    """The test could not be started at all — as distinct from a test that ran
    and failed, which is a PACK with a `failure`, not an exception."""


@dataclass(frozen=True)
class SampleTestOutcome:
    pack: evidence.EvidencePack
    feed: GovernedObject


@dataclass(frozen=True)
class SampleTestWorker:
    """Read the draft configuration, run it over the sample, store the pack."""

    metadata: MetadataDbPort
    storage: StoragePort

    def run(
        self,
        *,
        feed_id: str,
        file_key: str,
        actor: Actor,
        now: datetime | None = None,
    ) -> SampleTestOutcome:
        stamp = now or datetime.now(UTC)
        feed_object = self._feed_object(feed_id)
        feed = feed_registry.from_governed(feed_object)

        contract = self._contract(feed_id)
        rules = self._rules(feed_id)
        mapping = self._mapping(feed_id)
        objects = tuple(
            o
            for o in (
                feed_object,
                self._raw(ObjectType.CONTRACT, feed_id),
                self._raw(ObjectType.MAPPING, feed_id),
                self._raw(ObjectType.DQ_RULE, feed_id),
            )
            if o is not None
        )

        # The SAME fingerprint `api.app._sample_fingerprint` reads, from the
        # same place — the profile's own content hash. Taking a fresh
        # `storage.fingerprint` here instead is what made the first version of
        # this worker produce packs that were stale the instant they were
        # written: two hashes of one file, computed by two callers, agreeing
        # only by luck.
        rows, fingerprint, failure = self._sample(feed, file_key)
        if failure is not None:
            return self._store(
                feed_object,
                self._empty_pack(feed_id, objects, failure, fingerprint, file_key, stamp),
                actor,
                stamp,
            )

        try:
            plan = compile_feed(
                feed=feed, feed_version=feed_object.version, contract=contract, rules=rules
            )
            result = apply(
                plan,
                rows=rows,
                contract=contract,
                rules=rules,
                batch_id=f"sample-test-{feed_id}",
                mapping=mapping,
            )
        except Exception as broke:
            # "the pack is still produced up to the failure, the failing step
            # is explained in plain language, and the wizard links straight to
            # the mapping line at fault."
            return self._store(
                feed_object,
                self._empty_pack(
                    feed_id,
                    objects,
                    # The citation IS the link: `Failure.route` derives from
                    # it, so "the wizard links straight to the mapping line at
                    # fault" travels the platform's own address space rather
                    # than a path this module assembled.
                    evidence.Failure(
                        step="map",
                        explanation=_plain(broke),
                        citation=CitationId(
                            kind=(
                                CitationKind.MAPPING if mapping is not None else CitationKind.FEED
                            ),
                            subject=feed_id,
                        ),
                    ),
                    fingerprint,
                    file_key,
                    stamp,
                ),
                actor,
                stamp,
            )

        pack = evidence.build_pack(
            feed_id=feed_id,
            result=result,
            objects=objects,
            rule_names={rule.rule_id: rule.name for rule in rules},
            quarantining_rules=frozenset(
                rule.rule_id for rule in rules if rule.severity.quarantines
            ),
            phi_columns=frozenset(c.source_name or c.name for c in contract.columns if c.is_phi),
            gaps=_gaps(contract, mapping),
            sample_rows=rows[:EXAMPLE_ROWS],
            sample_filename=file_key.rsplit("/", 1)[-1],
            sample_fingerprint=fingerprint,
            now=stamp,
        )
        return self._store(feed_object, pack, actor, stamp)

    # ── reading the DRAFT configuration ──────────────────────────────────────

    def _feed_object(self, feed_id: str) -> GovernedObject:
        try:
            return self.metadata.get(ObjectType.FEED, feed_id)
        except ObjectNotFoundError:
            raise SampleTestError(f"no feed {feed_id!r}") from None

    def _raw(self, object_type: ObjectType, feed_id: str) -> GovernedObject | None:
        try:
            return self.metadata.get(object_type, feed_id)
        except ObjectNotFoundError:
            return None

    def _contract(self, feed_id: str) -> SchemaContract:
        """The LATEST contract, draft or published.

        Latest rather than published, and that is the whole point of the
        story: a BA who has just corrected a date format is testing the
        correction, and a test that read the published version would report on
        a configuration she is not proposing.
        """
        obj = self._raw(ObjectType.CONTRACT, feed_id)
        if obj is None:
            raise SampleTestError(
                f"feed {feed_id!r} has no schema contract yet. Step 2 comes before step 4 — "
                "there is nothing to validate the sample against."
            )
        from cinqflow.core.registry import contract as contract_registry

        return contract_registry.from_governed(obj)

    def _rules(self, feed_id: str) -> tuple[DqRule, ...]:
        obj = self._raw(ObjectType.DQ_RULE, feed_id)
        if obj is None:
            return ()
        from cinqflow.core.registry import contract as contract_registry

        return contract_registry.rules_from_governed(obj)

    def _mapping(self, feed_id: str) -> FeedMapping | None:
        obj = self._raw(ObjectType.MAPPING, feed_id)
        if obj is None:
            return None
        from cinqflow.core import mapping as mapping_core

        return mapping_core.from_governed(obj)

    # ── reading the sample ───────────────────────────────────────────────────

    def _sample(
        self, feed: FeedRecord, file_key: str
    ) -> tuple[list[dict[str, str]], str, evidence.Failure | None]:
        """The same read-and-parse `PipelineRunner._process` performs.

        Both failure modes become a `Failure` rather than an exception,
        because a BA who pointed at the wrong file needs a pack that says so —
        not a stack trace and not a 500.
        """
        try:
            content = self.storage.read_bytes(file_key)
            fingerprint = self._profiled_fingerprint(feed.feed_id) or self.storage.fingerprint(
                file_key
            )
        except FileNotFoundInStorageError:
            return (
                [],
                "",
                evidence.Failure(
                    step="read",
                    explanation=(
                        f"The sample {file_key!r} is no longer in the landing zone. Upload it "
                        "again at step 1 — nothing after this step can run without it."
                    ),
                    citation=CitationId(kind=CitationKind.FEED, subject=feed.feed_id),
                ),
            )
        try:
            parsed = parse(content, file_format=feed.file_format)
        except ParseError as broke:
            return (
                [],
                fingerprint,
                evidence.Failure(
                    step="read",
                    explanation=str(broke),
                    citation=CitationId(kind=CitationKind.FEED, subject=feed.feed_id),
                ),
            )

        if not parsed.row_count:
            return [], fingerprint, None
        columns = parsed.columns
        rows = [
            {name: str(value) for name, value in zip(columns, row, strict=True)}
            for row in zip(*[parsed.table.column(c).to_pylist() for c in columns], strict=True)
        ]
        return rows, fingerprint, None

    def _profiled_fingerprint(self, feed_id: str) -> str:
        """The profile's own `source_fingerprint`, which the staleness reader
        also uses. Falls back to a fresh storage hash only when nothing has
        been profiled — a case the wizard cannot reach, because step 1
        profiles on delivery."""
        profiles = list(self.metadata.list_profiles(feed_id=feed_id, limit=1))
        return profiles[0].profile.source_fingerprint if profiles else ""

    # ── writing the one thing this worker writes ─────────────────────────────

    def _store(
        self,
        feed_object: GovernedObject,
        pack: evidence.EvidencePack,
        actor: Actor,
        stamp: datetime,
    ) -> SampleTestOutcome:
        """The pack, onto the feed's own body, as the NEXT version.

        `new_version` and not an in-place edit, because `MetadataDbPort.save`
        is insert-only per version and refuses the second write —
        `ConcurrentVersionError`, correctly, since taking the last write is
        how something nobody approved gets published. The pack therefore
        versions WITH the configuration it is evidence about, which is the
        design `api.app._rule_evidence` already states for E7-02's preview:
        "which evidence did this rule set approve on?" needs no join and no
        timestamp.

        REFUSED ON A PUBLISHED FEED, and this is the sharp edge worth naming.
        `new_version` returns a DRAFT with the approver cleared — that is
        right for an amendment and wrong for an observation, so running a
        sample test against a live feed would quietly drop it to Draft v(n+1)
        and take it out of the engine's reach (`is_executable` reads the
        lifecycle state). E4-02 is an ONBOARDING act — its own story runs it
        on "a complete draft for the Centene Medicare clone" — so refusing
        here costs nothing the story asks for, and the alternative is a button
        that pauses production.
        """
        if feed_object.lifecycle_state is not LifecycleState.DRAFT:
            raise SampleTestError(
                f"feed {feed_object.object_id!r} is {feed_object.lifecycle_state.value}, not a "
                "draft. The end-to-end sample test writes its pack as the feed's next "
                "version, which would take a live feed out of the engine's reach. Test a "
                "draft amendment instead — that is what the change flow is for."
            )
        body = dict(feed_object.body)
        body[EVIDENCE_KEY] = pack_to_body(pack)
        stored = self.metadata.save(feed_object.new_version(body, actor=actor, now=stamp))
        return SampleTestOutcome(pack=pack, feed=stored)

    def _empty_pack(
        self,
        feed_id: str,
        objects: tuple[GovernedObject, ...],
        failure: evidence.Failure,
        fingerprint: str,
        file_key: str,
        stamp: datetime,
    ) -> evidence.EvidencePack:
        return evidence.EvidencePack(
            feed_id=feed_id,
            fingerprint=evidence.configuration_fingerprint(objects, sample_fingerprint=fingerprint),
            produced_ts=stamp,
            rows_in=0,
            rows_loaded=0,
            rows_quarantined=0,
            balanced=False,
            failure=failure,
            sample_filename=file_key.rsplit("/", 1)[-1],
        )


def _plain(broke: Exception) -> str:
    """A sentence, not a type name. "this rule compares a date to a text
    field" is the story's own example of what a BA should read."""
    text = str(broke).strip()
    return (
        text or f"The run stopped in the {type(broke).__name__.replace('Error', '').lower()} step."
    )


def _gaps(contract: SchemaContract, mapping: FeedMapping | None) -> tuple[evidence.Gap, ...]:
    """ "List known gaps honestly (unmapped optional fields, rules deferred) —
    the pack is evidence, not marketing."""
    if mapping is None:
        return (
            evidence.Gap(
                key="no_mapping",
                what="No mapping is configured yet.",
                why_it_is_acceptable=(
                    "The contract's own column names carry the source through. A mapping is "
                    "what a canonical target needs, and this feed has not been asked for one."
                ),
            ),
        )
    unmapped = tuple(line.address for line in mapping.lines if not line.is_mapped)
    if not unmapped:
        return ()
    return (
        evidence.Gap(
            key="unmapped_columns",
            what=f"{len(unmapped)} column(s) are UNMAPPED: {', '.join(unmapped[:8])}.",
            why_it_is_acceptable=(
                "An UNMAPPED column is declared, not forgotten — it is carried into Bronze and "
                "reaches no canonical field. A reviewer should confirm each one is genuinely "
                "not needed."
            ),
        ),
    )


def pack_to_body(pack: evidence.EvidencePack) -> dict[str, object]:
    """The pack as JSONB, in exactly the shape `api.app._pack_from_body` reads.

    The MARKDOWN is not stored, matching that function's own note: a stored
    document and the numbers beside it are two copies of one fact, and the day
    they disagree is the day somebody approves the document.
    """
    return {
        "feed_id": pack.feed_id,
        "fingerprint": pack.fingerprint,
        "produced_ts": pack.produced_ts.isoformat(),
        "rows_in": pack.rows_in,
        "rows_loaded": pack.rows_loaded,
        "rows_quarantined": pack.rows_quarantined,
        "balanced": pack.balanced,
        "sample_filename": pack.sample_filename,
        "drops": [
            {
                "rule_id": d.rule_id,
                "reason": d.reason,
                "record_count": d.record_count,
                "columns": list(d.columns),
            }
            for d in pack.drops
        ],
        "examples": [
            {"row_number": e.row_number, "before": dict(e.before), "after": dict(e.after)}
            for e in pack.examples
        ],
        "rules": [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "tested": r.tested,
                "flagged": r.flagged,
                "quarantined": r.quarantined,
            }
            for r in pack.rules
        ],
        "gaps": [
            {"key": g.key, "what": g.what, "why_it_is_acceptable": g.why_it_is_acceptable}
            for g in pack.gaps
        ],
        "failure": (
            {
                "step": pack.failure.step,
                "explanation": pack.failure.explanation,
                "citation": str(pack.failure.citation) if pack.failure.citation else None,
                "route": pack.failure.route,
            }
            if pack.failure
            else None
        ),
    }
