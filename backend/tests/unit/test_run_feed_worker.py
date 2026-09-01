"""CF-V1-E8-03 / CF-V0-E8-01 — the consumer, and "no feed-specific code".

    "Contain any feed-specific code — everything feed-specific must come from
     metadata." — CF-V0-E8-01's first don't

`cinqflow ingest` ran the spine for exactly ONE feed and could only ever run
that one: `FEED`, `CONTRACT`, `DQ_002` and `PLAN` are module constants in
`installer/cli.py`. So the compiler was generic and every caller of it was
not, and a second payer could not be run without editing Python.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.workers.run_feed import FeedRunError, FeedRunWorker, RunRequest

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 2, tzinfo=UTC)
AUTHOR = Actor(subject="ba@x", actor_type=ActorType.HUMAN, display_name="BA")
APPROVER = Actor(subject="steward@x", actor_type=ActorType.HUMAN, display_name="Steward")
FEED = "payer-b-roster"
#: A COMPLETE feed body — `FeedRecord.from_governed` needs the whole registry
#: envelope, and a half-built one would fail for a reason these tests are not
#: about.
_FEED_BODY: dict[str, object] = {
    "feed_id": FEED,
    "domain": "enrollments",
    "source_system": "payer-b",
    "file_format": "csv",
    "landing_path": "enrollments/payer-b/roster",
    "file_pattern": r".*[.]csv$",
    "schedule_cron": "0 4 1 * *",
    "sample_filename": "roster.csv",
}


def _object(object_type: ObjectType, body: dict[str, object], *, published: bool) -> GovernedObject:
    return GovernedObject(
        object_type=object_type,
        object_id=FEED,
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED if published else LifecycleState.DRAFT,
        created_by=AUTHOR,
        created_ts=NOW,
        approved_by=APPROVER if published else None,
        approved_ts=NOW if published else None,
        body=body,
    )


def _worker(store: object) -> FeedRunWorker:
    from cinqflow.adapters.mock.storage import MemFsStorage

    return FeedRunWorker(
        metadata=store,  # type: ignore[arg-type]
        storage=MemFsStorage(),
        runner=None,  # type: ignore[arg-type] - never reached in these tests
    )


# ── the message ──────────────────────────────────────────────────────────────


def test_a_message_missing_a_field_raises_rather_than_retrying_forever() -> None:
    """A malformed payload is a programming error upstream, not a transient
    failure — the queue would return it to `pending` on every sweep and the
    topic would never drain."""
    for payload in ({}, {"feed_id": "x"}, {"business_date": "2026-09-01"}):
        with pytest.raises(FeedRunError, match="feed_id and business_date"):
            RunRequest.from_payload(payload)


def test_a_well_formed_message_parses() -> None:
    request = RunRequest.from_payload({"feed_id": FEED, "business_date": "2026-09-01"})
    assert request.feed_id == FEED
    assert request.business_date == "2026-09-01"


# ── published only, and the reader is the gate ───────────────────────────────


def test_a_feed_with_no_published_contract_refuses_by_name() -> None:
    from cinqflow.adapters.mock.metadata_db import MemMetadataDb

    store = MemMetadataDb()
    store.save(_object(ObjectType.FEED, _FEED_BODY, published=True))
    store.save(_object(ObjectType.CONTRACT, {"columns": []}, published=False))

    with pytest.raises(FeedRunError, match="PUBLISHED contract"):
        _worker(store).run(RunRequest(feed_id=FEED, business_date="2026-09-01"))


def test_an_unpublished_feed_refuses() -> None:
    from cinqflow.adapters.mock.metadata_db import MemMetadataDb

    store = MemMetadataDb()
    store.save(_object(ObjectType.FEED, _FEED_BODY, published=False))
    with pytest.raises(FeedRunError, match="PUBLISHED feed"):
        _worker(store).run(RunRequest(feed_id=FEED, business_date="2026-09-01"))


# ── no feed-specific code ────────────────────────────────────────────────────


def test_the_worker_names_no_feed_anywhere_in_its_source() -> None:
    """CF-V0-E8-01's first don't, asserted mechanically. The Wave-0 anchor's
    identifiers are exactly what `installer/cli.py:ingest` hardcodes, and the
    point of this module is that it does not."""
    import inspect

    from cinqflow.workers import run_feed

    # The CODE, not the prose: this module's own docstring names the
    # Wave-0 anchor's constants in order to explain what it exists to
    # replace, and a check that read the docstring would forbid saying so.
    source = _code_only(inspect.getsource(run_feed))
    for anchor in ("fidelis", "DQ_002", "CINQDOWNSTATE", "centene", "molina"):
        assert anchor not in source, f"{anchor!r} is feed-specific code in a generic runner"


def test_rules_are_optional_because_a_feed_may_legitimately_have_none() -> None:
    """A feed with no rules loads every row it can cast. That is a
    configuration, not a missing one, and refusing it would make rules
    mandatory in a way no story asks for."""
    import inspect

    from cinqflow.workers import run_feed

    assert "Rules are OPTIONAL" in inspect.getsource(run_feed.FeedRunWorker._rules)


def test_the_handler_returns_none_so_the_consumer_can_acknowledge() -> None:
    import inspect

    from cinqflow.workers.consumer import Handler
    from cinqflow.workers.run_feed import FeedRunWorker

    signature = inspect.signature(FeedRunWorker.handle)
    assert signature.return_annotation in (None, "None"), (
        f"`Handler` is {Handler}; a handler returning a value would be discarded silently"
    )


def _code_only(source: str) -> str:
    """Source with docstrings and comments stripped, via the AST rather than
    by guessing at quote characters."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
    return ast.unparse(tree)
