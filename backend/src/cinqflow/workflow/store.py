"""The only place workflow SQL lives. Parameterised statements throughout."""

from __future__ import annotations

import json
import uuid
from datetime import date

import psycopg

from cinqflow.db import execute, fetch_all, fetch_one
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import (
    Approval,
    BronzeProfile,
    CanonicalFieldProposal,
    Interpretation,
    InterpretationContent,
    InterpretationRun,
    InterpretationStepRecord,
    Lineage,
    MappingSpec,
    MappingVersion,
    Preview,
    PreviewAggregates,
    PreviewRowResult,
    PreviewSample,
    Profile,
    ProfileFacts,
    Proposal,
    ProposalContent,
    Provenance,
    Run,
    RunCounts,
    Upload,
)
from cinqflow.workflow.states import RunState, UploadStatus, assert_transition


class DuplicateUpload(Exception):
    """Same bytes already landed. Replay is refused at the fingerprint."""

    def __init__(self, fingerprint: str, existing_upload_id: str) -> None:
        super().__init__(f"file already uploaded: {fingerprint}")
        self.fingerprint = fingerprint
        self.existing_upload_id = existing_upload_id


class UnknownUpload(Exception):
    pass


class NotEditable(Exception):
    """This mapping version is frozen. Derive the next version instead."""

    def __init__(self, feed: str, version: int, status: str) -> None:
        super().__init__(f"{feed} v{version} is {status} and cannot be edited")
        self.feed = feed
        self.version = version
        self.status = status


class UnknownMappingVersion(Exception):
    pass


class DraftAlreadyOpen(Exception):
    """One draft per feed: the open one must be approved or discarded first."""

    def __init__(self, feed: str, version: int) -> None:
        super().__init__(f"{feed} already has an open draft (v{version})")
        self.feed = feed
        self.version = version


class AlreadyDecided(Exception):
    """This artifact version already carries a decision at this gate."""

    def __init__(self, approval_id: str, decision: str) -> None:
        super().__init__(f"already {decision}")
        self.approval_id = approval_id
        self.decision = decision


class UnknownCanonicalFieldProposal(Exception):
    pass


class CanonicalFieldProposalAlreadyDecided(Exception):
    def __init__(self, proposal_id: str, status: str) -> None:
        super().__init__(f"already {status}")
        self.proposal_id = proposal_id
        self.status = status


#: A mapping version is identified by (feed, version), but an approval records a
#: UUID artifact id. Deriving it from the feed keeps that column meaningful and
#: keeps the one-decision-per-artifact-version index doing its job.
def mapping_artifact_id(feed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cinqflow:mapping_version:{feed}"))


class WorkflowStore:
    def __init__(self, conn: psycopg.Connection, settings: Settings | None = None) -> None:
        self.conn = conn
        self.s = settings or get_settings()

    # ----------------------------------------------------------------- uploads
    def create_upload(
        self,
        *,
        fingerprint: str,
        filename: str,
        file_type: str,
        size_bytes: int,
        uploader: str,
        source_system: str,
        feed: str,
        domain: str,
        business_date: date,
        landing_key: str,
    ) -> Upload:
        existing = fetch_one(
            self.conn,
            f"SELECT upload_id FROM {self.s.workflow_schema}.upload WHERE fingerprint = %s",
            (fingerprint,),
        )
        if existing:
            raise DuplicateUpload(fingerprint, str(existing["upload_id"]))

        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.upload
                (upload_id, fingerprint, filename, file_type, size_bytes, uploader,
                 source_system, feed, domain, business_date, landing_key, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                fingerprint,
                filename,
                file_type,
                size_bytes,
                uploader,
                source_system,
                feed,
                domain,
                business_date,
                landing_key,
                UploadStatus.RECEIVED,
            ),
        )
        return self._to_upload(row)

    def get_upload(self, upload_id: str) -> Upload:
        row = fetch_one(
            self.conn,
            f"SELECT * FROM {self.s.workflow_schema}.upload WHERE upload_id = %s",
            (upload_id,),
        )
        if not row:
            raise UnknownUpload(upload_id)
        return self._to_upload(row)

    def list_uploads(self, limit: int = 50) -> list[Upload]:
        rows = fetch_all(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.upload
                ORDER BY created_ts DESC LIMIT %s""",
            (limit,),
        )
        return [self._to_upload(r) for r in rows]

    def list_uploads_by_status(self, statuses: list[str], limit: int = 200) -> list[Upload]:
        """Cross-feed - the worklist endpoint's reason to exist. `list_uploads`
        alone can't answer "what's waiting at a gate" without pulling every
        upload in the system and filtering client-side."""
        rows = fetch_all(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.upload
                WHERE status = ANY(%s) ORDER BY created_ts DESC LIMIT %s""",
            (statuses, limit),
        )
        return [self._to_upload(r) for r in rows]

    def set_status(
        self, upload_id: str, status: UploadStatus, *, error: str | None = None
    ) -> Upload:
        current = self.get_upload(upload_id)
        assert_transition(current.status, status)
        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.upload
                SET status = %s, error = %s, updated_ts = now()
                WHERE upload_id = %s RETURNING *""",
            (status, error, upload_id),
        )
        return self._to_upload(row)

    def set_landing_key(self, upload_id: str, landing_key: str) -> Upload:
        """Kept in step whenever the engine moves the original between folders."""
        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.upload
                SET landing_key = %s, updated_ts = now()
                WHERE upload_id = %s RETURNING *""",
            (landing_key, upload_id),
        )
        return self._to_upload(row)

    def delete_upload(self, upload_id: str) -> dict:
        """Purge everything this upload owns in the workflow schema, and its
        pending queue messages, so a fresh upload of the same bytes is possible
        (the fingerprint UNIQUE index lives on this table).

        Deliberately does NOT touch Bronze/Silver/quarantine rows for any batch
        this upload landed: `dataplane/pg.py` puts a DB-level trigger on those
        tables that refuses UPDATE/DELETE/TRUNCATE even for a superuser - a
        stated platform guarantee ("Bronze append-only", checklist.md §0), not
        an oversight this method should route around. Their batch/table names
        are returned instead, so a caller can say plainly what still exists and
        why. A dev/test cleanup that truly needs those rows gone has to drop
        the feed's Bronze/Silver tables directly, outside the application.
        """
        upload = self.get_upload(upload_id)  # UnknownUpload if the id is wrong

        batch_ids = sorted({r.batch_id for r in self.list_runs(upload_id=upload_id, limit=1000)})
        preserved = [
            {
                "batch_id": r["batch_id"],
                "bronze_table": r["bronze_table"],
                "silver_tables": r["silver_tables"],
            }
            for r in fetch_all(
                self.conn,
                f"""SELECT batch_id, bronze_table, silver_tables
                    FROM {self.s.workflow_schema}.lineage
                    WHERE batch_id = ANY(%s)""",
                (batch_ids or [""],),
            )
        ]

        # Only the mapping-side artifacts (drafts, previews) are feed-scoped
        # rather than upload-scoped; purge them too, but only when no other
        # upload on the feed might still depend on them.
        sibling = fetch_one(
            self.conn,
            f"""SELECT 1 FROM {self.s.workflow_schema}.upload
                WHERE feed = %s AND upload_id != %s LIMIT 1""",
            (upload.feed, upload_id),
        )
        purge_feed = sibling is None

        def _delete(schema: str, table: str, where: str, params: tuple) -> int:
            row = fetch_one(
                self.conn,
                f"""WITH d AS (DELETE FROM {schema}.{table} WHERE {where} RETURNING 1)
                    SELECT count(*) AS n FROM d""",
                params,
            )
            return int(row["n"]) if row else 0

        wf = self.s.workflow_schema
        deleted = {
            "interpretation_run": _delete(wf, "interpretation_run", "upload_id = %s", (upload_id,)),
            "interpretation": _delete(wf, "interpretation", "upload_id = %s", (upload_id,)),
            "profile": _delete(wf, "profile", "upload_id = %s", (upload_id,)),
            "approval": _delete(wf, "approval", "upload_id = %s", (upload_id,)),
            "bronze_profile": _delete(
                wf, "bronze_profile", "batch_id = ANY(%s)", (batch_ids or [""],)
            ),
            "proposal": _delete(wf, "proposal", "upload_id = %s", (upload_id,)),
            "lineage": _delete(wf, "lineage", "upload_id = %s", (upload_id,)),
            "run": _delete(wf, "run", "upload_id = %s", (upload_id,)),
        }
        if purge_feed:
            deleted["preview"] = _delete(wf, "preview", "feed = %s", (upload.feed,))
            deleted["mapping_version"] = _delete(wf, "mapping_version", "feed = %s", (upload.feed,))

        # Any message still able to fire (pending, or mid-retry) against an
        # upload_id/batch_id that is about to stop existing. A `done` message
        # is inert history and is left alone.
        deleted["queue_message"] = _delete(
            self.s.queue_schema,
            "message",
            "state != 'done' AND (payload->>'upload_id' = %s OR payload->>'batch_id' = ANY(%s))",
            (upload_id, batch_ids or [""]),
        )

        # The upload row goes last: if anything above fails, the upload still
        # exists rather than being left as a phantom, already-gone reference.
        deleted["upload"] = _delete(wf, "upload", "upload_id = %s", (upload_id,))

        return {
            "upload_id": upload_id,
            "feed": upload.feed,
            "landing_key": upload.landing_key,
            "deleted": deleted,
            "feed_mapping_purged": purge_feed,
            "preserved_batches": preserved,
        }

    # ---------------------------------------------------------------- profiles
    def put_profile(
        self, *, profile_id: str, upload_id: str, profiler_version: str, facts: ProfileFacts
    ) -> Profile:
        """Immutable: re-profiling identical bytes writes the same row, not a new one."""
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.profile
                (profile_id, upload_id, profiler_version, facts)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (profile_id, upload_id) DO UPDATE
                SET profiler_version = EXCLUDED.profiler_version
            RETURNING *
            """,
            (profile_id, upload_id, profiler_version, json.dumps(facts.model_dump())),
        )
        return Profile(
            profile_id=row["profile_id"],
            upload_id=str(row["upload_id"]),
            profiler_version=row["profiler_version"],
            facts=ProfileFacts.model_validate(row["facts"]),
            profiled_ts=row["profiled_ts"],
        )

    def get_profile(self, upload_id: str) -> Profile | None:
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.profile
                WHERE upload_id = %s ORDER BY profiled_ts DESC LIMIT 1""",
            (upload_id,),
        )
        if not row:
            return None
        return Profile(
            profile_id=row["profile_id"],
            upload_id=str(row["upload_id"]),
            profiler_version=row["profiler_version"],
            facts=ProfileFacts.model_validate(row["facts"]),
            profiled_ts=row["profiled_ts"],
        )

    # --------------------------------------------------------- interpretations
    def put_interpretation(
        self,
        *,
        upload_id: str,
        profile_id: str,
        provenance: Provenance,
        content: InterpretationContent,
    ) -> Interpretation:
        """Appends a new version and supersedes the previous one for this profile."""
        prior = fetch_one(
            self.conn,
            f"""SELECT max(version) AS v FROM {self.s.workflow_schema}.interpretation
                WHERE upload_id = %s AND profile_id = %s""",
            (upload_id, profile_id),
        )
        version = (prior["v"] or 0) + 1
        execute(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.interpretation SET status = 'superseded'
                WHERE upload_id = %s AND profile_id = %s AND status = 'draft'""",
            (upload_id, profile_id),
        )
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.interpretation
                (interpretation_id, upload_id, profile_id, version, status, provenance, content)
            VALUES (%s,%s,%s,%s,'draft',%s,%s)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                upload_id,
                profile_id,
                version,
                json.dumps(provenance.model_dump()),
                json.dumps(content.model_dump()),
            ),
        )
        return self._to_interpretation(row)

    def get_interpretation(self, upload_id: str) -> Interpretation | None:
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.interpretation
                WHERE upload_id = %s AND status = 'draft'
                ORDER BY version DESC LIMIT 1""",
            (upload_id,),
        )
        return self._to_interpretation(row) if row else None

    # ----------------------------------------------------- interpretation runs
    def start_interpretation_run(self, *, upload_id: str, profile_id: str) -> InterpretationRun:
        """A fresh progress row for a new attempt. Overwrites whatever a previous
        attempt (e.g. a retry after `interpret_failed`) left behind - a poll only
        ever wants the run in progress now, never stale history."""
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.interpretation_run
                (upload_id, profile_id, status, completed_steps, error, started_ts, finished_ts)
            VALUES (%s, %s, 'running', '[]'::jsonb, NULL, now(), NULL)
            ON CONFLICT (upload_id) DO UPDATE
                SET profile_id = EXCLUDED.profile_id,
                    status = 'running',
                    completed_steps = '[]'::jsonb,
                    error = NULL,
                    started_ts = now(),
                    finished_ts = NULL
            RETURNING *
            """,
            (upload_id, profile_id),
        )
        return self._to_interpretation_run(row)

    def record_interpretation_step(self, *, upload_id: str, node: str) -> None:
        """One LangGraph node finished. Appended, not overwritten, so the order
        the graph actually ran in is exactly the order this replays."""
        execute(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.interpretation_run
                SET completed_steps = completed_steps
                    || jsonb_build_object('node', %s::text, 'at_ts', now())
                WHERE upload_id = %s""",
            (node, upload_id),
        )

    def finish_interpretation_run(
        self, *, upload_id: str, status: str, error: str | None = None
    ) -> InterpretationRun:
        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.interpretation_run
                SET status = %s, error = %s, finished_ts = now()
                WHERE upload_id = %s RETURNING *""",
            (status, error, upload_id),
        )
        return self._to_interpretation_run(row)

    def get_interpretation_run(self, upload_id: str) -> InterpretationRun | None:
        row = fetch_one(
            self.conn,
            f"SELECT * FROM {self.s.workflow_schema}.interpretation_run WHERE upload_id = %s",
            (upload_id,),
        )
        return self._to_interpretation_run(row) if row else None

    # --------------------------------------------------------------- approvals
    def put_approval(
        self,
        *,
        gate: str,
        artifact_type: str,
        artifact_id: str,
        artifact_version: int,
        upload_id: str,
        decision: str,
        approver: str,
        note: str | None = None,
    ) -> Approval:
        """Append-only. A second decision on the same artifact version is refused."""
        existing = fetch_one(
            self.conn,
            f"""SELECT approval_id, decision FROM {self.s.workflow_schema}.approval
                WHERE gate = %s AND artifact_type = %s AND artifact_id = %s
                  AND artifact_version = %s""",
            (gate, artifact_type, artifact_id, artifact_version),
        )
        if existing:
            raise AlreadyDecided(str(existing["approval_id"]), existing["decision"])

        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.approval
                (approval_id, gate, artifact_type, artifact_id, artifact_version,
                 upload_id, decision, approver, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                gate,
                artifact_type,
                artifact_id,
                artifact_version,
                upload_id,
                decision,
                approver,
                note,
            ),
        )
        return self._to_approval(row)

    def list_approvals(self, upload_id: str) -> list[Approval]:
        rows = fetch_all(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.approval
                WHERE upload_id = %s ORDER BY decided_ts""",
            (upload_id,),
        )
        return [self._to_approval(r) for r in rows]

    # -------------------------------------------------------------------- runs
    def open_run(
        self,
        *,
        batch_id: str,
        upload_id: str,
        feed: str,
        kind: str,
        mapping_version: int | None = None,
    ) -> Run:
        """One run per (batch, kind). Re-running a kind - a replay - reopens the
        same row rather than inventing a second history for the same work."""
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.run
                (batch_id, upload_id, feed, kind, mapping_version, state)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (batch_id, kind) DO UPDATE
                SET mapping_version = EXCLUDED.mapping_version,
                    state = EXCLUDED.state,
                    counts = NULL,
                    balanced = NULL,
                    error = NULL,
                    started_ts = now(),
                    finished_ts = NULL
            RETURNING *
            """,
            (batch_id, upload_id, feed, kind, mapping_version, RunState.RECEIVED),
        )
        return self._to_run(row)

    def set_run_state(self, batch_id: str, state: RunState, *, kind: str = "land_bronze") -> Run:
        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.run SET state = %s
                WHERE batch_id = %s AND kind = %s RETURNING *""",
            (state, batch_id, kind),
        )
        return self._to_run(row)

    def finish_run(
        self,
        *,
        batch_id: str,
        counts: RunCounts | None = None,
        error: str | None = None,
        kind: str = "land_bronze",
    ) -> Run:
        """A run that does not balance is failed, not 'mostly fine'."""
        state = RunState.FAILED if error or counts is None or not counts.balanced else (
            RunState.COMPLETED
        )
        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.run
                SET state = %s, counts = %s, balanced = %s, error = %s, finished_ts = now()
                WHERE batch_id = %s AND kind = %s RETURNING *""",
            (
                state,
                json.dumps(counts.model_dump()) if counts else None,
                counts.balanced if counts else None,
                error,
                batch_id,
                kind,
            ),
        )
        return self._to_run(row)

    def get_run(self, batch_id: str, *, kind: str | None = None) -> Run | None:
        """Without a kind this answers with the landing, which is the run that
        defines the batch; `list_batch_runs` returns its whole history."""
        if kind:
            row = fetch_one(
                self.conn,
                f"""SELECT * FROM {self.s.workflow_schema}.run
                    WHERE batch_id = %s AND kind = %s""",
                (batch_id, kind),
            )
        else:
            row = fetch_one(
                self.conn,
                f"""SELECT * FROM {self.s.workflow_schema}.run WHERE batch_id = %s
                    ORDER BY (kind = 'land_bronze') DESC, started_ts LIMIT 1""",
                (batch_id,),
            )
        return self._to_run(row) if row else None

    def list_batch_runs(self, batch_id: str) -> list[Run]:
        rows = fetch_all(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.run WHERE batch_id = %s
                ORDER BY started_ts""",
            (batch_id,),
        )
        return [self._to_run(r) for r in rows]

    def list_runs(self, *, upload_id: str | None = None, limit: int = 50) -> list[Run]:
        if upload_id:
            rows = fetch_all(
                self.conn,
                f"""SELECT * FROM {self.s.workflow_schema}.run
                    WHERE upload_id = %s ORDER BY started_ts DESC LIMIT %s""",
                (upload_id, limit),
            )
        else:
            rows = fetch_all(
                self.conn,
                f"""SELECT * FROM {self.s.workflow_schema}.run
                    ORDER BY started_ts DESC LIMIT %s""",
                (limit,),
            )
        return [self._to_run(r) for r in rows]

    # --------------------------------------------------------- bronze profiles
    def put_bronze_profile(
        self,
        *,
        profile_id: str,
        batch_id: str,
        bronze_table: str,
        profiler_version: str,
        rows_in_batch: int,
        rows_profiled: int,
        facts: ProfileFacts,
    ) -> BronzeProfile:
        """Immutable: the same batch and facts write the same row."""
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.bronze_profile
                (profile_id, batch_id, bronze_table, profiler_version,
                 rows_in_batch, rows_profiled, facts)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (batch_id, profile_id) DO UPDATE
                SET rows_in_batch = EXCLUDED.rows_in_batch,
                    rows_profiled = EXCLUDED.rows_profiled
            RETURNING *
            """,
            (
                profile_id,
                batch_id,
                bronze_table,
                profiler_version,
                rows_in_batch,
                rows_profiled,
                json.dumps(facts.model_dump()),
            ),
        )
        return self._to_bronze_profile(row)

    def get_bronze_profile(self, batch_id: str) -> BronzeProfile | None:
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.bronze_profile
                WHERE batch_id = %s ORDER BY profiled_ts DESC LIMIT 1""",
            (batch_id,),
        )
        return self._to_bronze_profile(row) if row else None

    # --------------------------------------------------------------- proposals
    def put_proposal(
        self,
        *,
        batch_id: str,
        upload_id: str,
        feed: str,
        domain: str,
        bronze_profile_id: str,
        status: str,
        provenance: Provenance,
        content: ProposalContent,
    ) -> Proposal:
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.proposal
                (proposal_id, batch_id, upload_id, feed, domain,
                 bronze_profile_id, status, provenance, content)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                batch_id,
                upload_id,
                feed,
                domain,
                bronze_profile_id,
                status,
                json.dumps(provenance.model_dump()),
                json.dumps(content.model_dump()),
            ),
        )
        return self._to_proposal(row)

    def get_proposal(self, batch_id: str) -> Proposal | None:
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.proposal
                WHERE batch_id = %s ORDER BY created_ts DESC LIMIT 1""",
            (batch_id,),
        )
        return self._to_proposal(row) if row else None

    def get_proposal_by_id(self, proposal_id: str) -> Proposal | None:
        row = fetch_one(
            self.conn,
            f"SELECT * FROM {self.s.workflow_schema}.proposal WHERE proposal_id = %s",
            (proposal_id,),
        )
        return self._to_proposal(row) if row else None

    # ------------------------------------------------ canonical field proposals
    def create_canonical_field_proposal(
        self,
        *,
        domain: str,
        entity: str,
        field_name: str,
        type: str,
        reason: str,
        requested_by: str,
        concept: str | None = None,
        evidence: list[str] | None = None,
        source_batch_id: str | None = None,
        source_upload_id: str | None = None,
    ) -> CanonicalFieldProposal:
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.canonical_field_proposal
                (proposal_id, domain, entity, field_name, type, concept, reason,
                 evidence, source_batch_id, source_upload_id, requested_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                domain,
                entity,
                field_name,
                type,
                concept,
                reason,
                json.dumps(evidence or []),
                source_batch_id,
                source_upload_id,
                requested_by,
            ),
        )
        return self._to_canonical_field_proposal(row)

    def list_canonical_field_proposals(
        self, *, domain: str | None = None, status: str | None = None
    ) -> list[CanonicalFieldProposal]:
        clauses: list[str] = []
        params: list[str] = []
        if domain:
            clauses.append("domain = %s")
            params.append(domain)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = fetch_all(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.canonical_field_proposal
                {where} ORDER BY created_ts DESC""",
            tuple(params),
        )
        return [self._to_canonical_field_proposal(r) for r in rows]

    def get_canonical_field_proposal(self, proposal_id: str) -> CanonicalFieldProposal | None:
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.canonical_field_proposal
                WHERE proposal_id = %s""",
            (proposal_id,),
        )
        return self._to_canonical_field_proposal(row) if row else None

    def decide_canonical_field_proposal(
        self, *, proposal_id: str, decision: str, decided_by: str, note: str | None = None
    ) -> CanonicalFieldProposal:
        """Records the decision only. Accepting is a steward's signal to go
        hand-edit the canonical YAML - this call never touches that file."""
        current = self.get_canonical_field_proposal(proposal_id)
        if current is None:
            raise UnknownCanonicalFieldProposal(proposal_id)
        if current.status != "pending_review":
            raise CanonicalFieldProposalAlreadyDecided(proposal_id, current.status)

        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.canonical_field_proposal
                SET status = %s, decided_by = %s, decision_note = %s, decided_ts = now()
                WHERE proposal_id = %s RETURNING *""",
            (decision, decided_by, note, proposal_id),
        )
        return self._to_canonical_field_proposal(row)

    # ----------------------------------------------------------------- lineage
    def put_lineage(
        self,
        *,
        batch_id: str,
        upload_id: str,
        fingerprint: str,
        landing_key: str,
        bronze_table: str | None = None,
        mapping_version: int | None = None,
        silver_table: str | None = None,
        silver_tables: dict[str, int] | None = None,
    ) -> Lineage:
        """The chain is written in two passes - landing, then promotion - so an
        omitted link is left as it was rather than erased. Only replay overwrites
        the Silver links, and it overwrites them with its own results."""
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.lineage
                (batch_id, upload_id, fingerprint, landing_key, bronze_table,
                 mapping_version, silver_table, silver_tables)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (batch_id) DO UPDATE
                SET landing_key = EXCLUDED.landing_key,
                    bronze_table = COALESCE(
                        EXCLUDED.bronze_table, {self.s.workflow_schema}.lineage.bronze_table),
                    mapping_version = COALESCE(
                        EXCLUDED.mapping_version,
                        {self.s.workflow_schema}.lineage.mapping_version),
                    silver_table = COALESCE(
                        EXCLUDED.silver_table, {self.s.workflow_schema}.lineage.silver_table),
                    silver_tables = COALESCE(
                        EXCLUDED.silver_tables, {self.s.workflow_schema}.lineage.silver_tables)
            RETURNING *
            """,
            (
                batch_id,
                upload_id,
                fingerprint,
                landing_key,
                bronze_table,
                mapping_version,
                silver_table,
                json.dumps(silver_tables) if silver_tables is not None else None,
            ),
        )
        return self._to_lineage(row)

    def get_lineage(self, batch_id: str) -> Lineage | None:
        row = fetch_one(
            self.conn,
            f"SELECT * FROM {self.s.workflow_schema}.lineage WHERE batch_id = %s",
            (batch_id,),
        )
        return self._to_lineage(row) if row else None

    # ---------------------------------------------------------------- previews
    def put_preview(
        self,
        *,
        feed: str,
        version: int,
        spec_fingerprint: str,
        sample: PreviewSample,
        aggregates: PreviewAggregates,
        row_results: list[PreviewRowResult],
    ) -> Preview:
        """Immutable: the same spec over the same sample is the same preview."""
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.preview
                (preview_id, feed, version, spec_fingerprint, sample, aggregates, row_results)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (feed, version, spec_fingerprint, (sample->>'batch_id'),
                         (sample->>'selector'))
                DO UPDATE SET aggregates = EXCLUDED.aggregates,
                              row_results = EXCLUDED.row_results
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                feed,
                version,
                spec_fingerprint,
                json.dumps(sample.model_dump()),
                json.dumps(aggregates.model_dump()),
                json.dumps([r.model_dump() for r in row_results]),
            ),
        )
        return self._to_preview(row)

    def get_preview(self, feed: str, version: int) -> Preview | None:
        """The most recent preview for this version, current or not."""
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.preview
                WHERE feed = %s AND version = %s ORDER BY created_ts DESC LIMIT 1""",
            (feed, version),
        )
        return self._to_preview(row) if row else None

    def get_current_preview(
        self, feed: str, version: int, spec_fingerprint: str
    ) -> Preview | None:
        """A preview that describes THIS spec. Stage 6's G2 gate depends on this
        returning something: a stale preview must not authorise a Silver write."""
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.preview
                WHERE feed = %s AND version = %s AND spec_fingerprint = %s
                ORDER BY created_ts DESC LIMIT 1""",
            (feed, version, spec_fingerprint),
        )
        return self._to_preview(row) if row else None

    def latest_batch_for_feed(self, feed: str) -> Run | None:
        """The newest completed landing for a feed - what a preview samples from."""
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.run
                WHERE feed = %s AND kind = 'land_bronze' AND state = %s
                ORDER BY started_ts DESC LIMIT 1""",
            (feed, RunState.COMPLETED),
        )
        return self._to_run(row) if row else None

    @staticmethod
    def _to_preview(row: dict) -> Preview:
        return Preview(
            preview_id=str(row["preview_id"]),
            feed=row["feed"],
            version=row["version"],
            spec_fingerprint=row["spec_fingerprint"],
            sample=PreviewSample.model_validate(row["sample"]),
            aggregates=PreviewAggregates.model_validate(row["aggregates"]),
            row_results=[PreviewRowResult.model_validate(r) for r in row["row_results"]],
            created_ts=row["created_ts"],
        )

    # -------------------------------------------------------- mapping versions
    def create_mapping_version(
        self,
        *,
        feed: str,
        domain: str,
        spec: MappingSpec,
        created_by: str,
        origin_proposal_id: str | None = None,
        derived_from: int | None = None,
    ) -> MappingVersion:
        """Next version number for the feed, always as a fresh draft.

        Versions are never reused and never renumbered: v(N+1) records what it was
        derived from, so the trail from proposal to approved mapping stays readable.
        """
        open_draft = fetch_one(
            self.conn,
            f"""SELECT version FROM {self.s.workflow_schema}.mapping_version
                WHERE feed = %s AND status = 'draft'""",
            (feed,),
        )
        if open_draft:
            raise DraftAlreadyOpen(feed, open_draft["version"])

        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.workflow_schema}.mapping_version
                (feed, version, domain, status, derived_from, origin_proposal_id,
                 spec, created_by)
            VALUES (
                %s,
                COALESCE((SELECT max(version) FROM {self.s.workflow_schema}.mapping_version
                          WHERE feed = %s), 0) + 1,
                %s, 'draft', %s, %s, %s, %s
            )
            RETURNING *
            """,
            (
                feed,
                feed,
                domain,
                derived_from,
                origin_proposal_id,
                json.dumps(spec.model_dump()),
                created_by,
            ),
        )
        return self._to_mapping_version(row)

    def update_draft_spec(self, *, feed: str, version: int, spec: MappingSpec) -> MappingVersion:
        """Mutate a draft. Refuses anything frozen - the guard is here, in the
        store, so no caller can bypass it."""
        current = self.get_mapping_version(feed, version)
        if current is None:
            raise UnknownMappingVersion(f"{feed} v{version}")
        if not current.editable:
            raise NotEditable(feed, version, current.status)

        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.mapping_version
                SET spec = %s, status = 'draft', updated_ts = now()
                WHERE feed = %s AND version = %s AND status IN ('draft', 'previewed')
                RETURNING *""",
            (json.dumps(spec.model_dump()), feed, version),
        )
        if row is None:
            raise NotEditable(feed, version, current.status)
        return self._to_mapping_version(row)

    def set_mapping_status(self, *, feed: str, version: int, status: str) -> MappingVersion:
        """Status transitions owned by later stages (preview marks `previewed`,
        G2 marks `approved`). Exposed now so the immutability rule is testable."""
        row = fetch_one(
            self.conn,
            f"""UPDATE {self.s.workflow_schema}.mapping_version
                SET status = %s, updated_ts = now()
                WHERE feed = %s AND version = %s RETURNING *""",
            (status, feed, version),
        )
        if row is None:
            raise UnknownMappingVersion(f"{feed} v{version}")
        return self._to_mapping_version(row)

    def approval_for_mapping(self, *, feed: str, version: int) -> Approval | None:
        """The G2 decision on one mapping version, if it has been made."""
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.approval
                WHERE gate = 'G2' AND artifact_type = 'mapping_version'
                  AND artifact_id = %s AND artifact_version = %s""",
            (mapping_artifact_id(feed), version),
        )
        return self._to_approval(row) if row else None

    def approve_mapping_version(
        self,
        *,
        feed: str,
        version: int,
        upload_id: str,
        approver: str,
        note: str | None = None,
    ) -> tuple[Approval, MappingVersion]:
        """G2. Freezes vN and supersedes whatever was approved before it.

        Both facts are written together: an approved version with an earlier
        version still marked approved would leave two authoritative mappings.
        """
        approval = self.put_approval(
            gate="G2",
            artifact_type="mapping_version",
            artifact_id=mapping_artifact_id(feed),
            artifact_version=version,
            upload_id=upload_id,
            decision="approved",
            approver=approver,
            note=note,
        )
        with self.conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {self.s.workflow_schema}.mapping_version
                    SET status = 'superseded', updated_ts = now()
                    WHERE feed = %s AND version <> %s AND status = 'approved'""",
                (feed, version),
            )
        frozen = self.set_mapping_status(feed=feed, version=version, status="approved")
        return approval, frozen

    def get_mapping_version(self, feed: str, version: int) -> MappingVersion | None:
        row = fetch_one(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.mapping_version
                WHERE feed = %s AND version = %s""",
            (feed, version),
        )
        return self._to_mapping_version(row) if row else None

    def list_mapping_versions(self, feed: str) -> list[MappingVersion]:
        rows = fetch_all(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.mapping_version
                WHERE feed = %s ORDER BY version DESC""",
            (feed,),
        )
        return [self._to_mapping_version(r) for r in rows]

    def list_mapping_versions_by_status(
        self, status: str, limit: int = 200
    ) -> list[MappingVersion]:
        """Cross-feed, for the worklist endpoint. `previewed` is a proxy for "a G2
        decision is pending" - it does not mean every one of these is currently
        `approvable`, since that also requires the preview to still be of the
        version's *current* spec (see `stale_reason` on `GET .../preview`)."""
        rows = fetch_all(
            self.conn,
            f"""SELECT * FROM {self.s.workflow_schema}.mapping_version
                WHERE status = %s ORDER BY updated_ts DESC NULLS LAST LIMIT %s""",
            (status, limit),
        )
        return [self._to_mapping_version(r) for r in rows]

    def latest_mapping_version(
        self, feed: str, *, status: str | None = None
    ) -> MappingVersion | None:
        if status:
            row = fetch_one(
                self.conn,
                f"""SELECT * FROM {self.s.workflow_schema}.mapping_version
                    WHERE feed = %s AND status = %s ORDER BY version DESC LIMIT 1""",
                (feed, status),
            )
        else:
            row = fetch_one(
                self.conn,
                f"""SELECT * FROM {self.s.workflow_schema}.mapping_version
                    WHERE feed = %s ORDER BY version DESC LIMIT 1""",
                (feed,),
            )
        return self._to_mapping_version(row) if row else None

    @staticmethod
    def _to_mapping_version(row: dict) -> MappingVersion:
        return MappingVersion(
            feed=row["feed"],
            version=row["version"],
            domain=row["domain"],
            status=row["status"],
            derived_from=row["derived_from"],
            origin_proposal_id=str(row["origin_proposal_id"])
            if row["origin_proposal_id"]
            else None,
            spec=MappingSpec.model_validate(row["spec"]),
            created_by=row["created_by"],
            created_ts=row["created_ts"],
            updated_ts=row["updated_ts"],
        )

    # ----------------------------------------------------------------- mapping
    @staticmethod
    def _to_approval(row: dict) -> Approval:
        return Approval(
            approval_id=str(row["approval_id"]),
            gate=row["gate"],
            artifact_type=row["artifact_type"],
            artifact_id=str(row["artifact_id"]),
            artifact_version=row["artifact_version"],
            upload_id=str(row["upload_id"]),
            decision=row["decision"],
            approver=row["approver"],
            note=row["note"],
            decided_ts=row["decided_ts"],
        )

    @staticmethod
    def _to_run(row: dict) -> Run:
        return Run(
            batch_id=row["batch_id"],
            upload_id=str(row["upload_id"]),
            feed=row["feed"],
            kind=row["kind"],
            mapping_version=row.get("mapping_version"),
            state=RunState(row["state"]),
            counts=RunCounts.model_validate(row["counts"]) if row["counts"] else None,
            balanced=row["balanced"],
            error=row["error"],
            started_ts=row["started_ts"],
            finished_ts=row["finished_ts"],
        )

    @staticmethod
    def _to_bronze_profile(row: dict) -> BronzeProfile:
        return BronzeProfile(
            profile_id=row["profile_id"],
            batch_id=row["batch_id"],
            bronze_table=row["bronze_table"],
            profiler_version=row["profiler_version"],
            rows_in_batch=row["rows_in_batch"],
            rows_profiled=row["rows_profiled"],
            facts=ProfileFacts.model_validate(row["facts"]),
            profiled_ts=row["profiled_ts"],
        )

    @staticmethod
    def _to_proposal(row: dict) -> Proposal:
        return Proposal(
            proposal_id=str(row["proposal_id"]),
            batch_id=row["batch_id"],
            upload_id=str(row["upload_id"]),
            feed=row["feed"],
            domain=row["domain"],
            bronze_profile_id=row["bronze_profile_id"],
            status=row["status"],
            provenance=Provenance.model_validate(row["provenance"]),
            content=ProposalContent.model_validate(row["content"]),
            created_ts=row["created_ts"],
        )

    @staticmethod
    def _to_canonical_field_proposal(row: dict) -> CanonicalFieldProposal:
        return CanonicalFieldProposal(
            proposal_id=str(row["proposal_id"]),
            domain=row["domain"],
            entity=row["entity"],
            field_name=row["field_name"],
            type=row["type"],
            concept=row["concept"],
            reason=row["reason"],
            evidence=row["evidence"] or [],
            source_batch_id=row["source_batch_id"],
            source_upload_id=str(row["source_upload_id"]) if row["source_upload_id"] else None,
            requested_by=row["requested_by"],
            status=row["status"],
            decided_by=row["decided_by"],
            decision_note=row["decision_note"],
            created_ts=row["created_ts"],
            decided_ts=row["decided_ts"],
        )

    @staticmethod
    def _to_lineage(row: dict) -> Lineage:
        return Lineage(
            batch_id=row["batch_id"],
            upload_id=str(row["upload_id"]),
            fingerprint=row["fingerprint"],
            landing_key=row["landing_key"],
            bronze_table=row["bronze_table"],
            mapping_version=row.get("mapping_version"),
            silver_table=row.get("silver_table"),
            silver_tables=row.get("silver_tables") or {},
            created_ts=row["created_ts"],
        )

    @staticmethod
    def _to_upload(row: dict) -> Upload:
        return Upload(
            upload_id=str(row["upload_id"]),
            fingerprint=row["fingerprint"],
            filename=row["filename"],
            file_type=row["file_type"],
            size_bytes=row["size_bytes"],
            uploader=row["uploader"],
            source_system=row["source_system"],
            feed=row["feed"],
            domain=row["domain"],
            business_date=row["business_date"].isoformat(),
            landing_key=row["landing_key"],
            status=UploadStatus(row["status"]),
            error=row["error"],
            created_ts=row["created_ts"],
        )

    @staticmethod
    def _to_interpretation(row: dict) -> Interpretation:
        return Interpretation(
            interpretation_id=str(row["interpretation_id"]),
            upload_id=str(row["upload_id"]),
            profile_id=row["profile_id"],
            version=row["version"],
            status=row["status"],
            provenance=Provenance.model_validate(row["provenance"]),
            content=InterpretationContent.model_validate(row["content"]),
            created_ts=row["created_ts"],
        )

    @staticmethod
    def _to_interpretation_run(row: dict) -> InterpretationRun:
        return InterpretationRun(
            upload_id=str(row["upload_id"]),
            profile_id=row["profile_id"],
            status=row["status"],
            completed_steps=[
                InterpretationStepRecord.model_validate(step) for step in row["completed_steps"]
            ],
            error=row["error"],
            started_ts=row["started_ts"],
            finished_ts=row["finished_ts"],
        )
