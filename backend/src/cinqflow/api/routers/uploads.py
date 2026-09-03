"""Uploads and the G1 gate (analyst approve/reject of an interpretation)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from cinqflow.dataplane.filestore import (
    FileStore,
    Folder,
    UnsafeFilename,
    fingerprint_bytes,
    landing_key,
)
from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings
from cinqflow.workers import interpret_upload, land_bronze, profile_upload, reject_upload
from cinqflow.workflow.models import UploadDetail, build_upload_progress, mask_row
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import AlreadyDecided, DuplicateUpload, UnknownUpload, WorkflowStore

ALLOWED_TYPES = {"csv": {".csv"}, "xlsx": {".xlsx", ".xlsm"}}

#: `LEGAL_TRANSITIONS` (workflow/states.py) already permits every one of these
#: - `profile_failed -> profiling`, `interpret_failed -> interpreting`,
#: `land_failed -> landing` - and each worker already re-enters that state
#: itself when it picks the message up (see e.g. `profile_upload.handle`'s
#: `if upload.status in (RECEIVED, PROFILE_FAILED): set_status(PROFILING)`).
#: So retrying is only ever "enqueue the topic again" - no status write here.
_RETRY_TOPIC: dict[UploadStatus, str] = {
    UploadStatus.PROFILE_FAILED: profile_upload.TOPIC,
    UploadStatus.INTERPRET_FAILED: interpret_upload.TOPIC,
    UploadStatus.LAND_FAILED: land_bronze.TOPIC,
}


def _file_type(filename: str) -> str:
    lowered = filename.lower()
    for file_type, suffixes in ALLOWED_TYPES.items():
        if any(lowered.endswith(sfx) for sfx in suffixes):
            return file_type
    raise HTTPException(415, detail=f"unsupported file type: {filename} (expected .csv or .xlsx)")


def build_router(
    settings: Settings, get_conn: Callable[[], Iterator]
) -> APIRouter:
    s = settings
    router = APIRouter()

    @router.post("/api/uploads", status_code=202)
    async def create_upload(
        file: UploadFile = File(...),
        source_system: str = Form(...),
        feed: str = Form(...),
        domain: str = Form(...),
        business_date: date = Form(...),
        uploader: str = Form("analyst@cinqcare.com"),
        conn=Depends(get_conn),
    ) -> dict:
        """Validate, preserve the original, persist the record, enqueue profiling."""
        filename = file.filename or ""
        file_type = _file_type(filename)
        content = await file.read()
        if not content:
            raise HTTPException(400, detail="file is empty")

        try:
            key = landing_key(
                domain=domain,
                source_system=source_system,
                feed=feed,
                folder=Folder.INCOMING,
                business_date=business_date.isoformat(),
                filename=filename,
            )
        except UnsafeFilename:
            raise HTTPException(400, detail=f"unsafe filename: {filename}") from None

        store = WorkflowStore(conn, s)
        filestore = FileStore(s)
        fingerprint = fingerprint_bytes(content)

        try:
            upload = store.create_upload(
                fingerprint=fingerprint,
                filename=filename,
                file_type=file_type,
                size_bytes=len(content),
                uploader=uploader,
                source_system=source_system,
                feed=feed,
                domain=domain,
                business_date=business_date,
                landing_key=key,
            )
        except DuplicateUpload as exc:
            raise HTTPException(
                409,
                detail={
                    "message": "this file has already been uploaded",
                    "fingerprint": exc.fingerprint,
                    "upload_id": exc.existing_upload_id,
                },
            ) from None

        # The record exists before the bytes land, so a crash can never leave a
        # file nobody knows about. A failed write rolls the record back.
        try:
            if not filestore.exists(key):
                filestore.place(key, content)
        except OSError as exc:
            conn.rollback()
            raise HTTPException(500, detail=f"could not preserve original: {exc}") from None

        Queue(conn, s).enqueue(
            profile_upload.TOPIC,
            {"upload_id": upload.upload_id},
            dedupe_key=f"{profile_upload.TOPIC}/{upload.upload_id}",
        )
        conn.commit()
        return {
            "upload_id": upload.upload_id,
            "status": upload.status,
            "fingerprint": upload.fingerprint,
            "landing_key": upload.landing_key,
        }

    @router.get("/api/uploads")
    def list_uploads(limit: int = 50, conn=Depends(get_conn)) -> dict:
        uploads = WorkflowStore(conn, s).list_uploads(limit)
        return {"uploads": [u.model_dump(mode="json") for u in uploads]}

    @router.get("/api/uploads/{upload_id}")
    def get_upload(upload_id: str, conn=Depends(get_conn)) -> dict:
        store = WorkflowStore(conn, s)
        try:
            upload = store.get_upload(upload_id)
        except (UnknownUpload, Exception) as exc:
            if isinstance(exc, UnknownUpload):
                raise HTTPException(404, detail=f"unknown upload: {upload_id}") from None
            raise

        profile = store.get_profile(upload_id)
        if profile is not None:
            phi = set(profile.facts.phi_candidates)
            profile = profile.model_copy(
                update={
                    "facts": profile.facts.model_copy(
                        update={
                            "sample_rows": [mask_row(r, phi) for r in profile.facts.sample_rows]
                        }
                    )
                }
            )

        detail = UploadDetail(
            upload=upload,
            profile=profile,
            interpretation=store.get_interpretation(upload_id),
            approvals=store.list_approvals(upload_id),
            runs=store.list_runs(upload_id=upload_id),
        )
        return detail.model_dump(mode="json")

    @router.get("/api/uploads/{upload_id}/progress")
    def get_upload_progress(upload_id: str, conn=Depends(get_conn)) -> dict:
        """A lightweight poll target: the upload's stage-by-stage journey, with
        LangGraph node detail for whichever stage is the AI interpretation - the
        one step a poll would otherwise see only as a single opaque status for
        however long the LLM call takes."""
        store = WorkflowStore(conn, s)
        try:
            upload = store.get_upload(upload_id)
        except UnknownUpload:
            raise HTTPException(404, detail=f"unknown upload: {upload_id}") from None

        run = store.get_interpretation_run(upload_id)
        land_run = next(
            (r for r in store.list_runs(upload_id=upload_id) if r.kind == "land_bronze"), None
        )
        return build_upload_progress(upload, run, land_run).model_dump(mode="json")

    @router.post("/api/uploads/{upload_id}/retry", status_code=202)
    def retry_upload(upload_id: str, conn=Depends(get_conn)) -> dict:
        """Re-enqueue the work a `*_failed` upload failed at. A transient failure
        (the API restarting mid-parse, an LLM timeout, a dropped connection to
        the data plane) should not require re-uploading the file - the original
        is already preserved and the profile/interpretation/lineage already on
        record stay exactly where they are."""
        store = WorkflowStore(conn, s)
        try:
            upload = store.get_upload(upload_id)
        except UnknownUpload:
            raise HTTPException(404, detail=f"unknown upload: {upload_id}") from None

        topic = _RETRY_TOPIC.get(upload.status)
        if topic is None:
            raise HTTPException(
                409,
                detail={
                    "message": f"nothing to retry from status '{upload.status}'",
                    "status": upload.status,
                },
            )

        # A fresh token per call (not a fixed key like `{topic}/{upload_id}`)
        # because the upload can fail, retry, and fail again from the same
        # named status - a fixed key would collide with the first attempt's
        # already-consumed queue row and silently no-op every retry after it.
        retry_id = uuid.uuid4().hex
        Queue(conn, s).enqueue(
            topic,
            {"upload_id": upload_id},
            dedupe_key=f"{topic}/{upload_id}/retry/{retry_id}",
        )
        conn.commit()
        return {"upload_id": upload_id, "status": upload.status, "queued": topic}

    @router.delete("/api/uploads/{upload_id}", status_code=200)
    def delete_upload(upload_id: str, conn=Depends(get_conn)) -> dict:
        """Purge an upload's workflow-schema rows (profile, interpretation,
        approvals, lineage, its batch runs, and - when no sibling upload
        shares the feed - its mapping drafts/previews too), free its
        fingerprint for a fresh upload of the same bytes, and remove the
        original from the landing zone.

        Refuses while a batch of this upload is still `received`/`in_progress`
        - deleting out from under a worker mid-write would leave it holding an
        `upload_id` that no longer resolves. Bronze/Silver/quarantine rows are
        never touched by this: they carry a DB-level append-only guard
        (dataplane/pg.py) that this endpoint does not attempt to bypass - any
        batch this upload landed stays queryable by its `batch_id` even after
        the upload itself is gone; the response says which ones.
        """
        store = WorkflowStore(conn, s)
        try:
            upload = store.get_upload(upload_id)
        except UnknownUpload:
            raise HTTPException(404, detail=f"unknown upload: {upload_id}") from None

        active = [
            r
            for r in store.list_runs(upload_id=upload_id, limit=1000)
            if r.state in ("received", "in_progress")
        ]
        if active:
            raise HTTPException(
                409,
                detail={
                    "message": "a batch for this upload is still running",
                    "hint": "wait for it to finish (or fail) and try again",
                    "batch_ids": [r.batch_id for r in active],
                },
            )

        summary = store.delete_upload(upload_id)
        conn.commit()

        # The DB commit is the durable part; a filesystem miss (already moved,
        # already gone) is not worth failing an otherwise-successful delete over.
        try:
            FileStore(s).remove(upload.landing_key)
            summary["file_removed"] = True
        except OSError:
            summary["file_removed"] = False

        return summary

    # ------------------------------------------------------------------- G1 gate
    def _decide(upload_id: str, decision: str, approver: str, note: str | None, conn) -> dict:
        """Persist the analyst's decision, then enqueue what it authorises."""
        store = WorkflowStore(conn, s)
        try:
            upload = store.get_upload(upload_id)
        except UnknownUpload:
            raise HTTPException(404, detail=f"unknown upload: {upload_id}") from None

        interpretation = store.get_interpretation(upload_id)

        # A decision that already exists is the more useful thing to report, so it
        # is checked before the status precondition.
        if interpretation is not None:
            prior = next(
                (
                    a
                    for a in store.list_approvals(upload_id)
                    if a.gate == "G1"
                    and a.artifact_id == interpretation.interpretation_id
                    and a.artifact_version == interpretation.version
                ),
                None,
            )
            if prior is not None:
                raise HTTPException(
                    409,
                    detail={
                        "message": f"already {prior.decision}",
                        "approval_id": prior.approval_id,
                        "decided_ts": prior.decided_ts.isoformat(),
                    },
                )

        if upload.status != UploadStatus.INTERPRETED or interpretation is None:
            raise HTTPException(
                409,
                detail={
                    "message": "G1 requires an interpreted upload",
                    "status": upload.status,
                },
            )

        try:
            approval = store.put_approval(
                gate="G1",
                artifact_type="interpretation",
                artifact_id=interpretation.interpretation_id,
                artifact_version=interpretation.version,
                upload_id=upload_id,
                decision=decision,
                approver=approver,
                note=note,
            )
        except AlreadyDecided as exc:
            raise HTTPException(
                409, detail={"message": f"already {exc.decision}", "approval_id": exc.approval_id}
            ) from None

        new_status = (
            UploadStatus.APPROVED if decision == "approved" else UploadStatus.REJECTED
        )
        store.set_status(upload_id, new_status)

        topic = land_bronze.TOPIC if decision == "approved" else reject_upload.TOPIC
        Queue(conn, s).enqueue(
            topic,
            {"upload_id": upload_id},
            dedupe_key=f"{topic}/{upload_id}/{approval.approval_id}",
        )
        conn.commit()
        return {
            "approval": approval.model_dump(mode="json"),
            "status": new_status,
            "queued": topic,
        }

    @router.post("/api/uploads/{upload_id}/approve", status_code=202)
    def approve_upload(
        upload_id: str,
        approver: str = Body("analyst@cinqcare.com"),
        note: str | None = Body(None),
        conn=Depends(get_conn),
    ) -> dict:
        return _decide(upload_id, "approved", approver, note, conn)

    @router.post("/api/uploads/{upload_id}/reject", status_code=202)
    def reject_upload_route(
        upload_id: str,
        approver: str = Body("analyst@cinqcare.com"),
        note: str | None = Body(None),
        conn=Depends(get_conn),
    ) -> dict:
        return _decide(upload_id, "rejected", approver, note, conn)

    return router
