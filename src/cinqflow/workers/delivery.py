"""CF-V1-E3-05 — the delivery worker. Land it, register it, profile it.

    "Landing is the Control Entry Point… Every arriving file is registered —
     INCLUDING UNEXPECTED ONES, which are parked and surfaced, never ignored."
    — cinqflow.core.landing

    "1. Upload sample, 2. Approve schema, 3. Map fields, 4. Define and test
     rules, 5. Publish and schedule"
    — CF-V1-E4-01

THE SEAM BETWEEN A CONNECTOR AND THE CONTROL PLANE. A connector puts bytes in
the zone and has no opinion about them; landing controls decide what those
bytes are; the input registry records the decision. This worker is the only
thing that holds both pins, and it performs those three steps in that order,
every time, for every connector — which is what makes "no delivery path
bypasses registration" true rather than intended.

IT IS NOT THE PIPELINE, AND THAT IS THE POINT. `PipelineRunner` lands a file
and then runs Landing→Bronze→Silver Raw, which needs a contract, a mapping and
rules. The first file a BA ever uploads has none of those — it is the thing
the contract will be inferred FROM. So this worker stops after landing and
profiling, and the wizard's five steps proceed from the profile.

The same worker serves the poller. `deliver_available()` walks a pull
connector's remote listing, and every file it finds takes exactly the path an
uploaded one takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cinqflow.core.delivery import Delivery, Manifest
from cinqflow.core.landing import LandingDecision, LandingOutcome, classify
from cinqflow.core.model.files import FileRef
from cinqflow.core.model.vocabulary import FileState
from cinqflow.core.profiling import FileProfile
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.ports.connector import ConnectorPort
from cinqflow.ports.control_tables import ControlTablesPort, InputFile
from cinqflow.ports.metadata_db import FileProfileRecord, MetadataDbPort
from cinqflow.ports.storage import StoragePort
from cinqflow.workers.profiler import Profiler

__all__ = ["DeliveryOutcome", "DeliveryWorker"]


@dataclass(frozen=True)
class DeliveryOutcome:
    """What happened to one delivery, and what the platform learned from it.

    `decision` is never None: every delivery gets a landing decision, and a
    rejected one is as much a result as an accepted one. `profile` IS None when
    the file was not accepted — profiling a rejected file would produce facts
    about bytes the platform has declined to load, and those facts would be
    cited later as though they described the feed.
    """

    delivery: Delivery
    decision: LandingDecision
    #: WHO asked for this. Distinct from `delivery.delivered_by`, which is the
    #: connector — `upload-endpoint`, `folder-drop`, `fidelis-sftp`. Both
    #: matter and they answer different questions: the source says HOW a file
    #: arrived and survives into a poller's audit row where no person exists;
    #: this says WHICH PERSON pressed Upload, and is the subject an approver
    #: looks for when asking who put this content in the estate.
    requested_by: str = ""
    profile_id: str | None = None
    profile: FileProfile | None = None

    @property
    def source(self) -> str:
        """The connector that landed it."""
        return self.delivery.delivered_by

    @property
    def accepted(self) -> bool:
        return self.decision.outcome is LandingOutcome.ACCEPTED

    @property
    def citation(self) -> str:
        return self.delivery.citation

    def headline(self) -> str:
        """One sentence, for a person who just pressed Upload.

        The outcome AND its reason, because "REJECTED" alone sends somebody to
        read logs — the whole reason `LandingDecision` refuses to carry a
        rejection without a named check.
        """
        if self.accepted:
            return f"Accepted as {self.delivery.file.filename} and registered."
        reason = self.decision.reason or "no reason was recorded"
        return f"{self.decision.outcome.value}: {reason}"


@dataclass
class DeliveryWorker:
    """The only component holding a connector. Lands, registers, profiles."""

    connector: ConnectorPort
    storage: StoragePort
    control: ControlTablesPort
    metadata: MetadataDbPort | None = None

    def deliver(
        self,
        content: bytes,
        *,
        filename: str,
        feed: FeedRecord,
        feed_version: int,
        business_date: str,
        delivered_by: str,
        manifest: Manifest | None = None,
        profile_it: bool = True,
        now: datetime | None = None,
    ) -> DeliveryOutcome:
        """One delivery, all the way to a profile.

        ORDER IS THE GUARANTEE, and it is the same order `PipelineRunner`
        uses because it is the same trust boundary:

          1. the connector lands the bytes under the feed's layout
          2. `classify` decides — replay first, then feed match, then pre-flight
          3. the file is REGISTERED whatever the decision was
          4. it is moved to the folder the decision names
          5. only an ACCEPTED file is profiled

        A caller cannot reorder these because a caller cannot reach them: the
        connector is held here and nowhere else.
        """
        stamp = now or datetime.now(UTC)
        delivery = self.connector.deliver(
            content,
            filename=filename,
            feed_id=feed.feed_id,
            landing_path=feed.landing_path,
            business_date=business_date,
            manifest=manifest if manifest is not None else Manifest(),
            now=stamp,
        )
        return self._land(
            delivery,
            feed=feed,
            feed_version=feed_version,
            profile_it=profile_it,
            requested_by=delivered_by,
        )

    def deliver_available(
        self,
        *,
        feed: FeedRecord,
        feed_version: int,
        business_date: str,
        since: datetime | None = None,
        profile_it: bool = True,
    ) -> tuple[DeliveryOutcome, ...]:
        """Everything a PULL connector is offering, landed.

        Returns empty for a push connector, which lists nothing — so a poller
        can be pointed at every connector without asking which kind it is.
        """
        outcomes: list[DeliveryOutcome] = []
        for remote in self.connector.list_available(since=since):
            outcomes.append(
                self.deliver(
                    self.connector.fetch(remote),
                    filename=remote.filename,
                    feed=feed,
                    feed_version=feed_version,
                    business_date=business_date,
                    delivered_by=self.connector.source,
                    manifest=Manifest(checksum=remote.declared_checksum),
                    profile_it=profile_it,
                )
            )
        return tuple(outcomes)

    # ── the trust boundary, in one place ─────────────────────────────────────

    def _land(
        self,
        delivery: Delivery,
        *,
        feed: FeedRecord,
        feed_version: int,
        profile_it: bool,
        requested_by: str,
    ) -> DeliveryOutcome:
        file = delivery.file
        seen = self.control.find_input_by_fingerprint(delivery.fingerprint) is not None
        decision = classify(file, feeds=(feed.for_landing(feed_version),), fingerprint_seen=seen)
        self._register(file, decision)
        self.storage.move(file.key, decision.move_to)

        if decision.outcome is not LandingOutcome.ACCEPTED or not profile_it:
            return DeliveryOutcome(delivery=delivery, decision=decision, requested_by=requested_by)

        moved = self._moved_key(file.key, decision)
        record = self._profile(
            feed.feed_id, moved, feed.file_format, requested_by or delivery.delivered_by
        )
        if record is None:
            return DeliveryOutcome(delivery=delivery, decision=decision, requested_by=requested_by)
        return DeliveryOutcome(
            delivery=delivery,
            decision=decision,
            requested_by=requested_by,
            profile_id=record.profile_id,
            profile=record.profile,
        )

    def _register(self, file: FileRef, decision: LandingDecision) -> None:
        """100% of arriving files have a registry entry — the measurable bar.

        The same mapping `PipelineRunner._register` uses. Two workers land
        files and both answer to one input registry, so a delivery that
        registered differently would make "every arriving file" mean two
        things depending on how it arrived.
        """
        state = {
            LandingOutcome.ACCEPTED: FileState.ACCEPTED,
            LandingOutcome.REJECTED: FileState.REJECTED,
            LandingOutcome.UNEXPECTED: FileState.RECEIVED,
            LandingOutcome.SKIPPED: FileState.PROCESSED,
        }[decision.outcome]
        self.control.register_input_file(
            InputFile(
                batch_id=None,
                feed_id=decision.feed_id,
                key=file.key,
                filename=file.filename,
                size_bytes=file.size_bytes,
                fingerprint=file.fingerprint or "",
                state=state,
                arrived_ts=file.modified_ts,
                rejection_reason=decision.reason,
            )
        )

    @staticmethod
    def _moved_key(key: str, decision: LandingDecision) -> str:
        """Where the file is NOW, after landing moved it.

        The profiler reads by key, and reading the pre-move key would be a
        file-not-found on every accepted delivery. The layout puts the folder
        in a fixed position, so this is a substitution rather than a second
        composition of the path.
        """
        parts = key.split("/")
        for index, part in enumerate(parts):
            if part in {"incoming", "processed", "rejected", "archive", "parked"}:
                parts[index] = decision.move_to.value
                return "/".join(parts)
        return key

    def _profile(
        self, feed_id: str, key: str, file_format: str, profiled_by: str
    ) -> FileProfileRecord | None:
        """The deterministic facts, computed. NO MODEL IS CALLED.

        This is CF-V1-E5-01's profiler, unchanged: row counts, type readings,
        null counts, key candidates. The insight a person gets on upload is
        ARITHMETIC — which is what makes it citable, and what lets the schema
        inference agent downstream cite `profile:<id>#<column>` for every fact
        it interprets rather than for facts it invented.
        """
        if self.metadata is None:
            return None
        profiler = Profiler(storage=self.storage, metadata=self.metadata)
        return profiler.profile(
            feed_id=feed_id, file_key=key, file_format=file_format, profiled_by=profiled_by
        )
