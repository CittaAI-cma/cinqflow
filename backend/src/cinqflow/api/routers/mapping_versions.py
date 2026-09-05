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
from cinqflow.workflow.models import MappingSpec, mask_preview_rows
from cinqflow.workflow.store import (
    DraftAlreadyOpen,
    NotEditable,
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
            },
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
        Queue(conn, s).enqueue(
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

        decided = store.approval_for_mapping(feed=feed, version=version)
        if decided is not None:
            raise HTTPException(
                409,
                detail={
                    "message": f"{feed} v{version} is already approved",
                    "approval_id": decided.approval_id,
                    "approver": decided.approver,
                    "decided_ts": decided.decided_ts.isoformat(),
                },
            )
        if mapping.status == "superseded":
            raise HTTPException(
                409,
                detail={"message": f"{feed} v{version} was superseded by a later version"},
            )

        canonical, _ = _canonical_for(s, mapping.domain)
        touched_tables = {canonical.table_of(t) for t in mapping.spec.targets if t}
        missing_required = [
            target
            for target in canonical.required_targets(t for t in touched_tables if t)
            if target not in mapping.spec.targets
        ]
        if missing_required:
            raise HTTPException(
                409,
                detail={
                    "message": (
                        f"{len(missing_required)} required field(s) are not mapped: "
                        + ", ".join(sorted(missing_required))
                    ),
                    "hint": "map each entity's primary key before approving",
                    "missing_required": sorted(missing_required),
                },
            )

        preview = store.get_current_preview(feed, version, spec_fingerprint(mapping.spec))
        if preview is None:
            raise HTTPException(
                409,
                detail={
                    "message": "this version has no preview of its current spec",
                    "hint": "run a preview and approve what you saw",
                },
            )

        landing = store.get_run(preview.sample.batch_id, kind="land_bronze")
        if landing is None:  # pragma: no cover - a preview always samples a batch
            raise HTTPException(409, detail={"message": "the previewed batch has no landing run"})

        approval, frozen = store.approve_mapping_version(
            feed=feed,
            version=version,
            upload_id=landing.upload_id,
            approver=user.email,
            note=note,
        )
        Queue(conn, s).enqueue(
            promote_silver.TOPIC,
            {"feed": feed, "version": version, "batch_id": preview.sample.batch_id},
            dedupe_key=(
                f"{promote_silver.TOPIC}/{feed}/{version}/{preview.sample.batch_id}"
                f"/{approval.approval_id}"
            ),
        )
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
