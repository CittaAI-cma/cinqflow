"""Workflow + queue DDL, rendered from one declaration and applied idempotently.

Later stages extend this file; every statement is idempotent, so installing is
safe to repeat. Stage 6 needed two shapes widened rather than added - the run key
and the lineage record - so it also carries guarded ALTERs for databases created
before it. New databases get the final shape straight from the CREATE TABLE.
"""

from __future__ import annotations

from cinqflow.settings import Settings


def statements(settings: Settings) -> list[str]:
    wf, q = settings.workflow_schema, settings.queue_schema
    return [
        f"CREATE SCHEMA IF NOT EXISTS {wf}",
        f"CREATE SCHEMA IF NOT EXISTS {q}",
        # --- uploads: one row per arrival; fingerprint is exactly-once -------------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.upload (
            upload_id      UUID PRIMARY KEY,
            fingerprint    TEXT NOT NULL,
            filename       TEXT NOT NULL,
            file_type      TEXT NOT NULL,
            size_bytes     BIGINT NOT NULL,
            uploader       TEXT NOT NULL,
            source_system  TEXT NOT NULL,
            feed           TEXT NOT NULL,
            domain         TEXT NOT NULL,
            business_date  DATE NOT NULL,
            landing_key    TEXT NOT NULL,
            status         TEXT NOT NULL,
            error          TEXT,
            created_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_ts     TIMESTAMPTZ
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS upload_fingerprint_key ON {wf}.upload (fingerprint)",
        f"CREATE INDEX IF NOT EXISTS upload_feed_idx ON {wf}.upload (feed, business_date)",
        # --- profiles: immutable, id is the hash of the facts ---------------------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.profile (
            profile_id       TEXT NOT NULL,
            upload_id        UUID NOT NULL,
            profiler_version TEXT NOT NULL,
            facts            JSONB NOT NULL,
            profiled_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (profile_id, upload_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS profile_upload_idx ON {wf}.profile (upload_id)",
        # --- interpretations: versioned against one profile -----------------------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.interpretation (
            interpretation_id UUID PRIMARY KEY,
            upload_id         UUID NOT NULL,
            profile_id        TEXT NOT NULL,
            version           INTEGER NOT NULL,
            status            TEXT NOT NULL,
            provenance        JSONB NOT NULL,
            content           JSONB NOT NULL,
            created_ts        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""CREATE UNIQUE INDEX IF NOT EXISTS interpretation_version_key
            ON {wf}.interpretation (upload_id, profile_id, version)""",
        # --- interpretation runs: live progress of the interpret_file graph --------
        # One row per upload, overwritten on every attempt - a poll only ever wants
        # the current or most recent run, never the history of past retries.
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.interpretation_run (
            upload_id       UUID PRIMARY KEY,
            profile_id      TEXT NOT NULL,
            status          TEXT NOT NULL,
            completed_steps JSONB NOT NULL DEFAULT '[]'::jsonb,
            error           TEXT,
            started_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_ts     TIMESTAMPTZ
        )
        """,
        # --- approvals: append-only record of analyst decisions (G1, later G2) ----
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.approval (
            approval_id      UUID PRIMARY KEY,
            gate             TEXT NOT NULL,
            artifact_type    TEXT NOT NULL,
            artifact_id      UUID NOT NULL,
            artifact_version INTEGER NOT NULL,
            upload_id        UUID NOT NULL,
            decision         TEXT NOT NULL,
            approver         TEXT NOT NULL,
            note             TEXT,
            decided_ts       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS approval_upload_idx ON {wf}.approval (upload_id, gate)",
        # One decision per artifact version per gate; re-approving is not a new fact.
        f"""CREATE UNIQUE INDEX IF NOT EXISTS approval_artifact_key
            ON {wf}.approval (gate, artifact_type, artifact_id, artifact_version)""",
        # --- runs: one row per execution against the data plane -------------------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.run (
            batch_id        TEXT NOT NULL,
            upload_id       UUID NOT NULL,
            feed            TEXT NOT NULL,
            kind            TEXT NOT NULL,
            mapping_version INTEGER,
            state           TEXT NOT NULL,
            counts          JSONB,
            balanced        BOOLEAN,
            error           TEXT,
            started_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_ts     TIMESTAMPTZ,
            -- One batch, one run per kind: landing and promotion are separate
            -- executions of the same batch (templates.md 1.8).
            PRIMARY KEY (batch_id, kind)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS run_upload_idx ON {wf}.run (upload_id)",
        # --- lineage: source file -> batch -> bronze (-> silver in Stage 6) -------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.lineage (
            batch_id        TEXT PRIMARY KEY,
            upload_id       UUID NOT NULL,
            fingerprint     TEXT NOT NULL,
            landing_key     TEXT NOT NULL,
            bronze_table    TEXT,
            mapping_version INTEGER,
            silver_table    TEXT,
            silver_tables   JSONB,
            created_ts      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS lineage_upload_idx ON {wf}.lineage (upload_id)",
        f"CREATE INDEX IF NOT EXISTS lineage_fingerprint_idx ON {wf}.lineage (fingerprint)",
        # --- bronze profiles: deterministic facts about what landed ---------------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.bronze_profile (
            profile_id       TEXT NOT NULL,
            batch_id         TEXT NOT NULL,
            bronze_table     TEXT NOT NULL,
            profiler_version TEXT NOT NULL,
            rows_in_batch    BIGINT NOT NULL,
            rows_profiled    BIGINT NOT NULL,
            facts            JSONB NOT NULL,
            profiled_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (batch_id, profile_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS bronze_profile_batch_idx ON {wf}.bronze_profile (batch_id)",
        # --- proposals: AI mapping candidates, never authoritative ----------------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.proposal (
            proposal_id        UUID PRIMARY KEY,
            batch_id           TEXT NOT NULL,
            upload_id          UUID NOT NULL,
            feed               TEXT NOT NULL,
            domain             TEXT NOT NULL,
            bronze_profile_id  TEXT NOT NULL,
            status             TEXT NOT NULL,
            provenance         JSONB NOT NULL,
            content            JSONB NOT NULL,
            created_ts         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS proposal_batch_idx ON {wf}.proposal (batch_id, created_ts)",
        # --- canonical field proposals: requests to extend the governed model -----
        # Never applied automatically - a steward hand-edits the canonical YAML
        # after accepting one. This table is the record of the request, not a
        # second source of legal targets.
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.canonical_field_proposal (
            proposal_id       UUID PRIMARY KEY,
            domain            TEXT NOT NULL,
            entity            TEXT NOT NULL,
            field_name        TEXT NOT NULL,
            type              TEXT NOT NULL,
            concept           TEXT,
            reason            TEXT NOT NULL,
            evidence          JSONB NOT NULL DEFAULT '[]'::jsonb,
            source_batch_id   TEXT,
            source_upload_id  UUID,
            requested_by      TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending_review',
            decided_by        TEXT,
            decision_note     TEXT,
            created_ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_ts        TIMESTAMPTZ
        )
        """,
        f"""CREATE INDEX IF NOT EXISTS canonical_field_proposal_domain_idx
            ON {wf}.canonical_field_proposal (domain, status)""",
        # --- mapping versions: analyst-owned, append-only across versions --------
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.mapping_version (
            feed                TEXT NOT NULL,
            version             INTEGER NOT NULL,
            domain              TEXT NOT NULL,
            status              TEXT NOT NULL,
            derived_from        INTEGER,
            origin_proposal_id  UUID,
            spec                JSONB NOT NULL,
            created_by          TEXT NOT NULL,
            created_ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_ts          TIMESTAMPTZ,
            PRIMARY KEY (feed, version)
        )
        """,
        f"""CREATE INDEX IF NOT EXISTS mapping_version_feed_idx
            ON {wf}.mapping_version (feed, version DESC)""",
        # At most one draft per feed: two open drafts would race for the next G2.
        f"""CREATE UNIQUE INDEX IF NOT EXISTS mapping_version_one_draft
            ON {wf}.mapping_version (feed) WHERE status = 'draft'""",
        # --- previews: deterministic, immutable per spec fingerprint + sample ----
        f"""
        CREATE TABLE IF NOT EXISTS {wf}.preview (
            preview_id        UUID PRIMARY KEY,
            feed              TEXT NOT NULL,
            version           INTEGER NOT NULL,
            spec_fingerprint  TEXT NOT NULL,
            sample            JSONB NOT NULL,
            aggregates        JSONB NOT NULL,
            row_results       JSONB NOT NULL,
            created_ts        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""CREATE INDEX IF NOT EXISTS preview_version_idx
            ON {wf}.preview (feed, version, created_ts DESC)""",
        # One preview per (version, spec, sample): re-running is not a new fact.
        f"""CREATE UNIQUE INDEX IF NOT EXISTS preview_identity_key
            ON {wf}.preview (feed, version, spec_fingerprint, (sample->>'batch_id'),
                             (sample->>'selector'))""",
        # --- Stage 6: widen shapes that predate promotion -------------------------
        # A batch now has two runs (land_bronze, promote_silver), so the run key is
        # composite. Databases created before Stage 6 still carry the single-column
        # key; converting it is safe and runs once.
        f"""
        DO $migrate$
        DECLARE existing_pk text;
        BEGIN
            SELECT c.conname INTO existing_pk
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = '{wf}' AND t.relname = 'run'
              AND c.contype = 'p' AND array_length(c.conkey, 1) = 1;
            IF existing_pk IS NOT NULL THEN
                EXECUTE format('ALTER TABLE {wf}.run DROP CONSTRAINT %I', existing_pk);
                EXECUTE 'ALTER TABLE {wf}.run
                         ADD CONSTRAINT run_batch_kind_key PRIMARY KEY (batch_id, kind)';
            END IF;
        END
        $migrate$
        """,
        f"ALTER TABLE {wf}.run ADD COLUMN IF NOT EXISTS mapping_version INTEGER",
        f"ALTER TABLE {wf}.lineage ADD COLUMN IF NOT EXISTS mapping_version INTEGER",
        f"ALTER TABLE {wf}.lineage ADD COLUMN IF NOT EXISTS silver_table TEXT",
        f"ALTER TABLE {wf}.lineage ADD COLUMN IF NOT EXISTS silver_tables JSONB",
        # --- durable queue: claim with FOR UPDATE SKIP LOCKED ---------------------
        f"""
        CREATE TABLE IF NOT EXISTS {q}.message (
            message_id  UUID PRIMARY KEY,
            topic       TEXT NOT NULL,
            dedupe_key  TEXT NOT NULL,
            payload     JSONB NOT NULL,
            state       TEXT NOT NULL DEFAULT 'pending',
            attempts    INTEGER NOT NULL DEFAULT 0,
            last_error  TEXT,
            enqueued_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            claimed_ts  TIMESTAMPTZ,
            done_ts     TIMESTAMPTZ
        )
        """,
        f"CREATE UNIQUE INDEX IF NOT EXISTS message_dedupe_key ON {q}.message (dedupe_key)",
        f"""CREATE INDEX IF NOT EXISTS message_pending_idx
            ON {q}.message (topic, enqueued_ts) WHERE state = 'pending'""",
    ]


def install(conn, settings: Settings) -> None:
    with conn.cursor() as cur:
        for sql in statements(settings):
            cur.execute(sql)
