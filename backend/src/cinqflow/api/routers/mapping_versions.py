"""Mapping version lifecycle: draft, edit, diff, preview (S5), G2 approve (S6)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Body, Depends, HTTPException

from cinqflow.api.deps import make_get_current_user, require_capability
from cinqflow.auth.models import CurrentUser
from cinqflow.engine.mapping_exec import (
    DEFAULT_STRATEGY,
    SAMPLE_STRATEGIES,
    sample_selector,
    spec_fingerprint,
)
from cinqflow.engine.mapping_spec import (
    ALLOWED_CASTS,
    ALLOWED_ON_NULL,
    ALLOWED_ON_UNMAPPED,
    ALLOWED_OPS,
    CAST_FOR_TYPE,
    ON_NULL_NEEDS_DEFAULT,
    ON_UNMAPPED_NEEDS_VALUE_MAP,
    REQUIRED_ARGS,
    InvalidSpec,
    assert_valid,
    diff_specs,
    spec_from_proposal,
)
from cinqflow.knowledge.canonical import load_canonical
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings
from cinqflow.workers import promote_silver, run_preview
from cinqflow.workflow.dag import feed_version_scope
from cinqflow.workflow.g2_gate import g2_blockers
from cinqflow.workflow.models import (
    MappingSpec,
    build_step_progress,
    mask_facts,
    mask_preview_rows,
)
from cinqflow.workflow.store import (
    DraftAlreadyOpen,
    NotEditable,
    StepLedger,
    UnknownMappingVersion,
    WorkflowStore,
)


def _canonical_for(settings: Settings, domain: str):
    """Governed target model for a domain. Landing domains are plural."""
    singular = domain[:-1] if domain.endswith("s") else domain
    return load_canonical(YamlKnowledgeProvider(settings), singular), singular


def build_router(settings: Settings, get_conn: Callable[[], Iterator]) -> APIRouter:
    s = settings
    router = APIRouter()
    # G2 is a decision: same capability gate as G1 (auth/persona.py).
    get_current_user = make_get_current_user(s, get_conn)
    require_decide = require_capability("can_decide_gates", get_current_user)

    @router.get("/api/mapping-proposals/{proposal_id}")
    def get_mapping_proposal(proposal_id: str, conn=Depends(get_conn)) -> dict:
        """A Stage 3 proposal by its own id, independent of its batch.

        The Mapping Studio's empty state (no draft yet) only ever carries
        `?proposal=<id>` in its URL, not the batch it came from - this is what
        lets it show the analyst what "Start draft" is about to seed from,
        before they commit to it, the same table `GET /batches/{id}/proposal`
        already renders on the batch page.
        """
        proposal = WorkflowStore(conn, s).get_proposal_by_id(proposal_id)
        if proposal is None:
            raise HTTPException(404, detail=f"unknown proposal: {proposal_id}")
        return {
            **proposal.model_dump(mode="json"),
            "counts": proposal.content.counts,
            "authoritative": False,
        }

    # ----------------------------------------------------- mapping versions (S4)
    @router.post("/api/feeds/{feed}/mapping-versions", status_code=201)
    def create_mapping_version(
        feed: str,
        from_proposal_id: str | None = Body(None),
        derive_from_version: int | None = Body(None),
        domain: str | None = Body(None),
        created_by: str = Body("analyst@cinqcare.com"),
        conn=Depends(get_conn),
    ) -> dict:
        """A new draft: seeded from an AI proposal, derived from an earlier version,
        or empty. Always a draft - authority comes only from G2 in Stage 6."""
        store = WorkflowStore(conn, s)
        origin_proposal_id: str | None = None
        derived_from: int | None = None

        if from_proposal_id:
            proposal = store.get_proposal_by_id(from_proposal_id)
            if proposal is None:
                raise HTTPException(404, detail=f"unknown proposal: {from_proposal_id}")
            if proposal.feed != feed:
                raise HTTPException(
                    409,
                    detail={
                        "message": "proposal belongs to a different feed",
                        "proposal_feed": proposal.feed,
                    },
                )
            canonical, singular = _canonical_for(s, proposal.domain)
            spec = spec_from_proposal(proposal, canonical)
            origin_proposal_id = proposal.proposal_id
            resolved_domain = singular
        elif derive_from_version is not None:
            parent = store.get_mapping_version(feed, derive_from_version)
            if parent is None:
                raise HTTPException(
                    404, detail=f"unknown mapping version: {feed} v{derive_from_version}"
                )
            canonical, resolved_domain = _canonical_for(s, parent.domain)
            spec = parent.spec
            derived_from = parent.version
        else:
            canonical, resolved_domain = _canonical_for(s, domain or "enrollment")
            spec = MappingSpec(target_table=f"silver_raw.{canonical.tables[0]}", fields=[])
            if not canonical.legal_targets:
                raise HTTPException(
                    409, detail={"message": f"no canonical model for domain '{domain}'"}
                )

        try:
            created = store.create_mapping_version(
                feed=feed,
                domain=resolved_domain,
                spec=spec,
                created_by=created_by,
                origin_proposal_id=origin_proposal_id,
                derived_from=derived_from,
            )
        except DraftAlreadyOpen as exc:
            raise HTTPException(
                409,
                detail={
                    "message": "this feed already has an open draft",
                    "version": exc.version,
                },
            ) from None
        conn.commit()
        return {
            **created.model_dump(mode="json"),
            "origin": created.origin,
            "editable": created.editable,
        }

    @router.put("/api/feeds/{feed}/mapping-versions/{version}")
    def update_mapping_version(
        feed: str,
        version: int,
        spec: MappingSpec = Body(...),
        conn=Depends(get_conn),
    ) -> dict:
        """Edit a draft. Refused for anything frozen; validated before it is saved."""
        store = WorkflowStore(conn, s)
        current = store.get_mapping_version(feed, version)
        if current is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")
        if not current.editable:
            raise HTTPException(
                409,
                detail={
                    "message": f"v{version} is {current.status} and cannot be edited",
                    "status": current.status,
                    "hint": "POST a new version with derive_from_version to continue editing",
                },
            )

        canonical, _ = _canonical_for(s, current.domain)
        try:
            assert_valid(spec, canonical)
        except InvalidSpec as exc:
            # Field-level errors so the studio can annotate each row.
            raise HTTPException(
                422, detail={"message": "spec is not valid", "errors": exc.as_list()}
            ) from None

        try:
            saved = store.update_draft_spec(feed=feed, version=version, spec=spec)
        except NotEditable as exc:
            raise HTTPException(409, detail={"message": str(exc), "status": exc.status}) from None
        except UnknownMappingVersion:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}") from None
        conn.commit()
        return {**saved.model_dump(mode="json"), "editable": saved.editable}

    @router.get("/api/feeds/{feed}/mapping-versions")
    def list_mapping_versions(feed: str, conn=Depends(get_conn)) -> dict:
        versions = WorkflowStore(conn, s).list_mapping_versions(feed)
        return {
            "feed": feed,
            "versions": [
                {**v.model_dump(mode="json"), "origin": v.origin, "editable": v.editable}
                for v in versions
            ],
        }

    @router.get("/api/feeds/{feed}/mapping-versions/{version}")
    def get_mapping_version(feed: str, version: int, conn=Depends(get_conn)) -> dict:
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")
        canonical, _ = _canonical_for(s, mapping.domain)

        # Per-entity primary keys, restricted to columns that are legal mapping
        # targets - the studio uses this to warn the moment a touched entity is
        # missing the field a Silver row needs to be identifiable, before the
        # analyst ever reaches the G2 wall that enforces the same rule.
        primary_keys = {
            table: list(canonical.required_targets([table]))
            for table in canonical.tables
            if canonical.required_targets([table])
        }

        # The AI's own rationale for each field, carried forward from the
        # proposal this draft was seeded from (if any) so the studio can show
        # confidence/evidence/concept next to the row the analyst is editing,
        # not only at the moment the draft was first created.
        ai_context: dict[str, dict] = {}
        if mapping.origin_proposal_id:
            origin = store.get_proposal_by_id(mapping.origin_proposal_id)
            if origin is not None:
                ai_context = {
                    f.source: {
                        "confidence": f.confidence,
                        "evidence": f.evidence,
                        "concept": f.concept,
                        "status": f.status,
                        # Which target the confidence is *about*. Without it the
                        # studio renders a model's number beside whatever target
                        # the analyst has since chosen, so a 0.98 could sit next
                        # to a mapping the model never proposed. The studio
                        # compares this with the field's current target and
                        # withdraws the meter when they differ.
                        "target": f.target,
                        # Why this column was left for a person: the target the
                        # model named that does not exist, and the reason the
                        # candidate was not carried into the draft. Already on
                        # the proposal; carried here so the studio can say what
                        # happened without re-fetching it.
                        "rejected_target": f.rejected_target,
                        "reason": f.reason,
                    }
                    for f in origin.content.fields
                }

        return {
            **mapping.model_dump(mode="json"),
            "origin": mapping.origin,
            "editable": mapping.editable,
            "ai_context": ai_context,
            # The studio needs the legal vocabulary to offer choices, not free text.
            "vocabulary": {
                "targets": sorted(canonical.legal_targets),
                "target_types": canonical.types,
                "ops": sorted(ALLOWED_OPS),
                "casts": sorted(ALLOWED_CASTS),
                "on_null": sorted(ALLOWED_ON_NULL),
                "on_unmapped_value": sorted(ALLOWED_ON_UNMAPPED),
                "primary_keys": primary_keys,
                # The *dependencies* between those choices, not just the choices.
                # A save is all-or-nothing over one artifact, which is right - a
                # spec is a single thing and half a spec is not a spec. But it
                # meant four edits an analyst can make with nothing but a
                # dropdown ("nulls -> default", "unmapped -> quarantine", any
                # transform taking an argument, a cast the target's declared
                # type cannot accept) rejected the entire table, discarding
                # every unrelated edit with it. These four tables let the editor
                # require the box the rule needs at the moment the dropdown
                # selects it, so the analyst never reaches the refusal.
                #
                # Published rather than reimplemented on the client on purpose:
                # `validate_spec` reads the same constants, so the editor cannot
                # drift from the validator that will judge it.
                "op_args": {op: list(args) for op, args in sorted(REQUIRED_ARGS.items())},
                "casts_for_type": {
                    declared: sorted(casts) for declared, casts in sorted(CAST_FOR_TYPE.items())
                },
                "on_null_needs_default": sorted(ON_NULL_NEEDS_DEFAULT),
                "on_unmapped_needs_value_map": sorted(ON_UNMAPPED_NEEDS_VALUE_MAP),
            },
        }

    @router.get("/api/feeds/{feed}/mapping-versions/{version}/columns")
    def mapping_version_columns(feed: str, version: int, conn=Depends(get_conn)) -> dict:
        """Every source column in the batch, mapped or not.

        The studio has only ever been able to show the columns that made it into
        the spec. The ones the model was not confident enough to place - exactly
        the ones needing a person - were computed, dropped at the render
        boundary, and never seen again. An analyst could finish the table, see
        no unfinished work, and be refused at G2 for a required target sitting
        in a column the screen never showed.

        The roster resolves its batch server-side, the same feed -> batch join
        `mapping_version_progress` already does. Deliberately *not* built from
        `ai_context` alone: that is `{}` on any version created with
        `derive_from_version`, which is the normal path after the first
        approval, so an `ai_context`-only roster would silently degrade to
        today's table from v2 onward - correct in the demo, wrong in use.

        Facts come from the batch's own Bronze profile, through `mask_facts`:
        PHI columns keep their name, type and null ratio and lose their example
        values, because "what is in this column" is the one question a mapping
        screen must answer without showing anyone a patient.
        """
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")

        preview = store.get_preview(feed, version)
        batch_id = preview.sample.batch_id if preview else None
        if batch_id is None:
            latest = store.latest_batch_for_feed(feed)
            batch_id = latest.batch_id if latest else None

        profile = store.get_bronze_profile(batch_id) if batch_id else None
        if profile is None:
            # No landing to enumerate against. An empty roster with its reason
            # beats a roster silently narrowed to the spec, which is the failure
            # this endpoint exists to remove.
            return {
                "feed": feed,
                "version": version,
                "batch_id": batch_id,
                "resolved_from": None,
                "unresolved_reason": (
                    "no bronze profile for this feed yet, so the full column list is unknown"
                ),
                "columns": [],
            }

        facts = mask_facts(profile.facts)

        # The AI's own read of each column, from the proposal this draft was
        # seeded from. Absent on a derived version - which is why it decorates
        # the roster rather than defining it.
        candidates: dict[str, dict] = {}
        origin = (
            store.get_proposal_by_id(mapping.origin_proposal_id)
            if mapping.origin_proposal_id
            else None
        )
        if origin is not None:
            candidates = {
                f.source: {
                    "target": f.target,
                    "rejected_target": f.rejected_target,
                    "concept": f.concept,
                    "confidence": f.confidence,
                    "evidence": f.evidence,
                    "status": f.status,
                    "reason": f.reason,
                }
                for f in origin.content.fields
            }

        in_spec = {field.source: field.target for field in mapping.spec.fields}

        # What the last preview actually did to each column. Counted over the
        # persisted run, so "4 issues" is a fact about rows that ran, not a
        # prediction - and it is 0, not absent, when a preview exists and the
        # column was clean.
        issue_counts: dict[str, int] = {}
        if preview is not None:
            for row in preview.row_results:
                for outcome in row.fields:
                    if outcome.outcome in ("failure", "quarantined", "rejected"):
                        issue_counts[outcome.source] = issue_counts.get(outcome.source, 0) + 1

        columns = [
            {
                "name": column.name,
                "inferred_type": column.inferred_type,
                "role": column.hint,
                "null_ratio": column.null_ratio,
                "distinct_count": column.distinct_count,
                "sentinel_count": column.sentinel_count,
                "constant": column.constant,
                "sample_values": column.sample_values,
                "phi_masked": column.phi_candidate,
                "in_spec": column.name in in_spec,
                "mapped_target": in_spec.get(column.name),
                "candidate": candidates.get(column.name),
                "issue_count": issue_counts.get(column.name, 0) if preview else None,
            }
            for column in facts.columns
        ]

        return {
            "feed": feed,
            "version": version,
            "batch_id": batch_id,
            # Said out loud because the roster's authority depends on it: these
            # are the columns of *this* batch, profiled over however many rows
            # the profiler actually read.
            "resolved_from": {
                "batch_id": batch_id,
                "row_count": facts.row_count,
                "is_sample": profile.is_sample,
                "profiled_ts": profile.profiled_ts.isoformat(),
            },
            "unresolved_reason": None,
            "counts": {
                "total": len(columns),
                "in_spec": sum(1 for c in columns if c["in_spec"]),
                "unplaced": sum(1 for c in columns if not c["in_spec"]),
            },
            "columns": columns,
        }

    @router.get("/api/feeds/{feed}/mapping-versions/{version}/diff")
    def diff_mapping_version(
        feed: str, version: int, against: int | None = None, conn=Depends(get_conn)
    ) -> dict:
        """This version against another (default: the latest approved, else v(N-1))."""
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")

        if against is not None:
            baseline = store.get_mapping_version(feed, against)
        else:
            baseline = store.latest_mapping_version(feed, status="approved")
            if baseline is None or baseline.version == version:
                baseline = store.get_mapping_version(feed, version - 1) if version > 1 else None

        return {
            "feed": feed,
            "version": version,
            "against": baseline.version if baseline else None,
            "against_status": baseline.status if baseline else None,
            "diff": diff_specs(baseline.spec if baseline else None, mapping.spec),
        }

    # ----------------------------------------------------------- preview (S5)
    @router.post("/api/feeds/{feed}/mapping-versions/{version}/preview", status_code=202)
    def request_preview(
        feed: str,
        version: int,
        batch_id: str | None = Body(None),
        rows: int | None = Body(None),
        strategy: str | None = Body(None),
        conn=Depends(get_conn),
    ) -> dict:
        """Queue a deterministic preview. The handler validates and enqueues only -
        executing the spec is the worker's job."""
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")
        if not mapping.spec.fields:
            raise HTTPException(409, detail={"message": "this version has no fields to preview"})

        target_batch = batch_id
        if target_batch is None:
            run = store.latest_batch_for_feed(feed)
            if run is None:
                raise HTTPException(
                    409,
                    detail={
                        "message": "no completed Bronze batch for this feed to preview against",
                        "hint": "approve an upload at G1 so a batch exists",
                    },
                )
            target_batch = run.batch_id
        elif store.get_run(target_batch) is None:
            raise HTTPException(404, detail=f"unknown batch: {target_batch}")

        chosen = strategy or DEFAULT_STRATEGY
        if chosen not in SAMPLE_STRATEGIES:
            raise HTTPException(
                422,
                detail={
                    "message": f"'{chosen}' is not a sampling strategy",
                    "allowed": sorted(SAMPLE_STRATEGIES),
                },
            )

        fingerprint = spec_fingerprint(mapping.spec)
        sample_rows = min(rows or run_preview.DEFAULT_SAMPLE_ROWS, run_preview.MAX_SAMPLE_ROWS)
        message_id = Queue(conn, s).enqueue(
            run_preview.TOPIC,
            {
                "feed": feed,
                "version": version,
                "batch_id": target_batch,
                "rows": sample_rows,
                "strategy": chosen,
            },
            # Same spec + same sample is the same preview, so a repeat is deduped.
            dedupe_key=(
                f"{run_preview.TOPIC}/{feed}/{version}/{fingerprint}"
                f"/{target_batch}/{sample_selector(sample_rows, chosen)}"
            ),
        )
        if message_id:
            StepLedger(conn, s).queued(
                "feed_version", feed_version_scope(feed, version), "preview", message_id=message_id
            )
        conn.commit()
        return {
            "feed": feed,
            "version": version,
            "batch_id": target_batch,
            "rows": sample_rows,
            "selector": sample_selector(sample_rows, chosen),
            "queued": run_preview.TOPIC,
            "spec_fingerprint": fingerprint,
        }

    @router.get("/api/feeds/{feed}/mapping-versions/{version}/preview")
    def get_preview(feed: str, version: int, limit: int = 50, conn=Depends(get_conn)) -> dict:
        """The persisted preview, plus whether it still describes this spec."""
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")

        preview = store.get_preview(feed, version)
        if preview is None:
            raise HTTPException(404, detail={"message": f"no preview yet for {feed} v{version}"})

        fingerprint = spec_fingerprint(mapping.spec)
        is_current = preview.spec_fingerprint == fingerprint

        # Row-by-row is real, per-record source/mapped values - actual PHI, not
        # the bounded example values `GET .../bronze-profile` already masks.
        # Masked by two independent, cheap-to-check signals: the columns the
        # upload's own profiler flagged, and the fields the canonical model
        # itself declares `phi: true`.
        canonical, _ = _canonical_for(s, mapping.domain)
        landing = store.get_run(preview.sample.batch_id, kind="land_bronze")
        profile = store.get_profile(landing.upload_id) if landing else None
        phi_sources = set(profile.facts.phi_candidates) if profile else set()
        rows, phi_masked = mask_preview_rows(
            preview.row_results, phi_sources=phi_sources, phi_targets=canonical.phi
        )

        payload = preview.model_dump(mode="json")
        payload["row_results"] = [r.model_dump(mode="json") for r in rows[: min(limit, 200)]]
        return {
            **payload,
            "row_results_total": len(preview.row_results),
            "phi_masked": phi_masked,
            "is_current": is_current,
            # Stage 6 will not open G2 without a current preview; surfaced now so
            # the studio can say why the gate is closed.
            "approvable": is_current,
            "stale_reason": None
            if is_current
            else "the draft changed after this preview; run it again",
            "sample_is_partial": preview.sample.is_sample,
        }

    @router.get("/api/feeds/{feed}/mapping-versions/{version}/progress")
    def mapping_version_progress(feed: str, version: int, conn=Depends(get_conn)) -> dict:
        """The ledger's view of one mapping version: its preview and G2 decision
        (`feed_version` scope) plus the promotion of the batch it was previewed
        against (`batch` scope). What the studio's `WorkflowSteps` polls."""
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")
        ledger = StepLedger(conn, s)
        step_runs = ledger.list_for("feed_version", feed_version_scope(feed, version))
        preview = store.get_preview(feed, version)
        batch_id = preview.sample.batch_id if preview else None
        if batch_id is None:
            latest = store.latest_batch_for_feed(feed)
            batch_id = latest.batch_id if latest else None
        if batch_id is not None:
            step_runs += ledger.list_for("batch", batch_id)
        return {
            "feed": feed,
            "version": version,
            "status": mapping.status,
            "batch_id": batch_id,
            "steps": [p.model_dump(mode="json") for p in build_step_progress(step_runs)],
        }

    @router.get("/api/feeds/{feed}/mapping-versions/{version}/gate")
    def mapping_version_gate(feed: str, version: int, conn=Depends(get_conn)) -> dict:
        """Whether G2 will open, and every reason it will not.

        `GET .../preview` already reported `approvable`, but that field is
        literally `is_current` - it answers "does a preview describe this spec",
        which is one of four rules. A draft with a current preview and an
        unmapped `members.source_system_id` rendered an enabled Approve button
        that 409s. This endpoint answers the whole question from the same list
        the approve handler refuses from, so the two cannot disagree.

        Not gated on `can_decide_gates`: knowing what is left to do is not
        deciding. Whoever presses the button still needs the capability.
        """
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")
        canonical, _ = _canonical_for(s, mapping.domain)
        blockers = g2_blockers(store, canonical, mapping, feed, version)
        return {
            "feed": feed,
            "version": version,
            "approvable": not blockers,
            "blockers": [b.as_dict() for b in blockers],
        }

    # --------------------------------------------------------------- G2 (S6)
    @router.post("/api/feeds/{feed}/mapping-versions/{version}/approve", status_code=202)
    def approve_mapping_version(
        feed: str,
        version: int,
        note: str | None = Body(None, embed=True),
        conn=Depends(get_conn),
        user: CurrentUser = Depends(require_decide),
    ) -> dict:
        """G2: the analyst takes responsibility for this mapping - whoever holds
        the session and `can_decide_gates`, recorded under their own email.

        Refused without a preview of *this* spec, because approving a version
        nobody has seen run is the one thing the gate exists to prevent. The
        promotion itself is queued - this handler only decides and records.
        """
        store = WorkflowStore(conn, s)
        mapping = store.get_mapping_version(feed, version)
        if mapping is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")

        # One list, shared with `GET .../gate`, so the button the studio renders
        # and the answer this handler gives are derived from the same rules. The
        # refusal keeps the shape it always had: the first blocker's own body.
        canonical, _ = _canonical_for(s, mapping.domain)
        blockers = g2_blockers(store, canonical, mapping, feed, version)
        if blockers:
            raise HTTPException(409, detail=blockers[0].as_dict())

        preview = store.get_current_preview(feed, version, spec_fingerprint(mapping.spec))
        landing = store.get_run(preview.sample.batch_id, kind="land_bronze")

        approval, frozen = store.approve_mapping_version(
            feed=feed,
            version=version,
            upload_id=landing.upload_id,
            approver=user.email,
            note=note,
        )
        ledger = StepLedger(conn, s)
        ledger.decide(
            "feed_version",
            feed_version_scope(feed, version),
            "gate_g2",
            approved=True,
            approval_id=approval.approval_id,
            approver=user.email,
            note=note,
        )
        message_id = Queue(conn, s).enqueue(
            promote_silver.TOPIC,
            {"feed": feed, "version": version, "batch_id": preview.sample.batch_id},
            dedupe_key=(
                f"{promote_silver.TOPIC}/{feed}/{version}/{preview.sample.batch_id}"
                f"/{approval.approval_id}"
            ),
        )
        if message_id:
            ledger.queued("batch", preview.sample.batch_id, "promote", message_id=message_id)
        conn.commit()
        return {
            "approval": approval.model_dump(mode="json"),
            "status": frozen.status,
            "batch_id": preview.sample.batch_id,
            "preview_id": preview.preview_id,
            "sample_was_partial": preview.sample.is_sample,
            "queued": promote_silver.TOPIC,
        }

    return router
