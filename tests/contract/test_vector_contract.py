"""The ONE contract suite for the `vector` pin.

    "vector: index/retrieve_chunks   mock: list   dev: REAL_pgvector
     target: pgvector|ai_search"
    — docs/architecture/plates/04-pin-out-map.md

    "retrieval applies the caller's RBAC scopes BEFORE any similarity
     computation"
    — docs/architecture/INVARIANTS.md, intelligence

W1-29 · CF-V1-E16-05. A prior adversarial review found this pin with no
dedicated contract suite at all — its `vector` section previously lived
folded into `test_intelligence_contracts.py`, covering only `count() == 0`
and one scope-filter check, and its real adapter (`PgVectorStore`) was never
constructed by name anywhere (`grep -rln "PgVectorStore"` found it only in
its own module and two docstrings — the `fitted("vector")` registry
constructs it, which grep for the class name cannot see). `supersede()`, the
verb this pin gained in W1-26, had NEVER been run against real Postgres: every
existing caller (`KnowledgeIngestWorker`, the runbook-publish hook) is tested
only against `adapters.mock.vector.ListVector`.

This file closes both gaps: `adapters_for("vector")` (the SAME mechanism
`test_intelligence_contracts.py` uses for `llm`) parametrizes every ordinary
test below over BOTH adapters — `mock` and the real `pgvector` — the latter
running genuinely against the rung-0.5 Postgres plane through the shared
`plane` fixture (`tests/conftest.py`), lazily requested only for the adapter
that declares a `connection` parameter (`tests/contract/conftest.py:make`),
and skipping rather than silently passing when that plane is unreachable.

The bottom section proves the transactional fix made alongside this suite:
`PgVectorStore.index()`/`supersede()` now open their OWN transaction whenever
the connection they were given is `autocommit` (`pg_control.connect()`'s
default, and the only connection-constructor actually wired for non-test
use) — closing a genuine partial-commit risk a mid-loop failure could
previously leave on a real engine. Those tests use a real `autocommit=True`
connection directly (never `plane`, which is deliberately non-autocommit and
would hide the exact defect being proven) and clean up everything they write.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest

from cinqflow.adapters.local.pg_control import Connection, connect
from cinqflow.adapters.local.pg_vector import PgVectorStore
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.installer.profile import Profile
from cinqflow.ports.vector import Chunk, VectorPort

from .conftest import adapters_for

pytestmark = pytest.mark.contract


def _chunk(chunk_id: str, *, domain: str, text: str | None = None) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text or f"text for {chunk_id}",
        citation=CitationId(kind=CitationKind.TERM, subject="w1-29-vector-contract"),
        metadata={"domain": domain},
    )


# ── the ONE suite, every adapter ─────────────────────────────────────────────
@pytest.fixture(params=adapters_for("vector"))
def vector(request: pytest.FixtureRequest, make: Callable[..., Any]) -> VectorPort:
    return make(request.param)


def test_wave_0_provisions_the_store_and_leaves_it_empty(vector: VectorPort) -> None:
    """ "pgvector stays provisioned and empty, exactly as specified."

    The knowledge plane is Wave 1. Provisioning now and asserting empty is the
    honest way to say the seat exists and the capability does not.
    """
    assert vector.count() == 0


def test_index_then_retrieve_returns_exactly_what_was_indexed(vector: VectorPort) -> None:
    """Round-trip content, not just presence: the chunk that comes back from
    `retrieve()` must carry the SAME text, citation and metadata that went
    into `index()` — a store that merely remembered chunk ids would pass a
    weaker version of this test while silently corrupting content."""
    chunk = Chunk(
        chunk_id="w1-29-roundtrip-1",
        text="the exact text a runbook step was chunked into",
        citation=CitationId(kind=CitationKind.TERM, subject="roundtrip"),
        metadata={"domain": "enrollments", "kind": "k2"},
    )
    vector.index([chunk], [(0.6, 0.8)])

    (found,) = vector.retrieve((0.6, 0.8), scope_filter={"domain": "enrollments"})
    assert found.chunk.chunk_id == chunk.chunk_id
    assert found.chunk.text == chunk.text
    assert found.chunk.citation == chunk.citation
    assert found.chunk.metadata["domain"] == "enrollments"


def test_scope_filters_the_candidate_set_before_similarity(vector: VectorPort) -> None:
    """ "Apply a scope filter to results rather than to the query" is a
    documented don't. Filtering results is the version that leaks: the row was
    fetched, and every future path that forgets the filter exposes it. This
    proves the NEGATIVE: a chunk outside the filter is not merely ranked
    lower — it is ABSENT, even though it is the closer vector match."""
    in_scope = _chunk("w1-29-scope-in", domain="enrollments")
    out_of_scope = _chunk("w1-29-scope-out", domain="claims")
    # Both chunks are given the IDENTICAL vector, so if scope were applied to
    # the ranking rather than the candidate set, both would still tie for
    # first place — only filtering the candidate set BEFORE similarity drops
    # the out-of-scope chunk entirely.
    vector.index([in_scope, out_of_scope], [(1.0, 0.0), (1.0, 0.0)])

    found = vector.retrieve((1.0, 0.0), scope_filter={"domain": "enrollments"})
    assert [s.chunk.chunk_id for s in found] == ["w1-29-scope-in"]


def test_supersede_leaves_only_the_new_versions_chunks_retrievable(vector: VectorPort) -> None:
    """CF-V1-W1-26's own property, run for the first time against the REAL
    adapter as well as the mock: after `supersede()`, the OLD version's chunks
    are gone — not merely outranked, ABSENT — and every NEW chunk is
    retrievable. `count()` after the call is the new version's size exactly,
    proving the retire actually removed rows rather than leaving them orphaned
    but unindexed.
    """
    old = [_chunk("w1-29-super-old-1", domain="k2"), _chunk("w1-29-super-old-2", domain="k2")]
    vector.index(old, [(1.0, 0.0), (0.0, 1.0)])
    assert vector.count() == 2

    new = [
        _chunk("w1-29-super-new-1", domain="k2"),
        _chunk("w1-29-super-new-2", domain="k2"),
        _chunk("w1-29-super-new-3", domain="k2"),
    ]
    vector.supersede(
        retire=[c.chunk_id for c in old], chunks=new, vectors=[(1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    )

    found = vector.retrieve((1.0, 0.0), limit=10, scope_filter={"domain": "k2"})
    found_ids = {s.chunk.chunk_id for s in found}
    assert found_ids == {c.chunk_id for c in new}
    assert found_ids.isdisjoint({c.chunk_id for c in old}), (
        "a superseded version's chunks must be ABSENT, not merely low-ranked"
    )
    assert vector.count() == 3


def test_count_reflects_reality_after_each_operation(vector: VectorPort) -> None:
    """`count()` is not a cached statistic — it is read back after every
    write in this test, and must match exactly, because it is what a
    steward's "how many chunks does this guide have" screen calls directly."""
    assert vector.count() == 0

    vector.index([_chunk("w1-29-count-1", domain="k2")], [(1.0, 0.0)])
    assert vector.count() == 1

    vector.index([_chunk("w1-29-count-2", domain="k2")], [(0.0, 1.0)])
    assert vector.count() == 2

    vector.supersede(
        retire=["w1-29-count-1", "w1-29-count-2"],
        chunks=[_chunk("w1-29-count-3", domain="k2")],
        vectors=[(1.0, 1.0)],
    )
    assert vector.count() == 1


def test_a_chunk_cannot_exist_without_a_citation() -> None:
    """A chunk that cannot be cited cannot ground a claim, and an ungrounded
    claim is a defect class."""
    with pytest.raises(TypeError):
        Chunk(chunk_id="c", text="t")  # type: ignore[call-arg]


# ── W1-29 · the real adapter's transaction discipline ───────────────────────
#
#     "chunk 3 of 5 hits a transient error midway through a multi-step index
#      loop ... could leave the store holding BOTH a partial new version AND
#      the untouched old version" — the review this slab answers.
#
# `plane` is deliberately NOT used below: it is `autocommit=False` and always
# rolled back, which is exactly the connection shape that was NEVER at risk.
# The defect lives on `pg_control.connect()`'s default — `autocommit=True`,
# the only connection-constructor actually wired for non-test use — so
# proving the fix means using that default for real, against the real plane,
# and cleaning up afterward.


class _FlakyConnection:
    """Wraps a REAL `Connection`, raising on the Nth `execute()` call — the
    fault-injection point for "chunk 3 of 5 hits a transient error". `.raw`
    is forwarded UNCHANGED, so `PgVectorStore._own_transaction_if_none_is_open`
    runs against the real psycopg connection exactly as it would in
    production: this exercises the actual transaction-boundary code, not a
    stand-in for it.
    """

    def __init__(self, real: Connection, *, fail_at: int) -> None:
        self._real = real
        self._fail_at = fail_at
        self.calls = 0

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> None:
        self.calls += 1
        if self.calls == self._fail_at:
            raise RuntimeError("simulated transient failure")
        self._real.execute(statement, parameters)

    def fetch_all(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        return self._real.fetch_all(statement, parameters)

    def fetch_one(self, statement: str, parameters: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        return self._real.fetch_one(statement, parameters)

    @property
    def raw(self) -> Any:
        return self._real.raw


@pytest.fixture
def real_autocommit_connection(pg_profile: Profile) -> Iterator[Connection]:
    """A GENUINE `autocommit=True` connection — `pg_control.connect()`'s
    default. Skips, like `plane`, when rung 0.5 is unreachable rather than
    silently passing; unlike `plane`, nothing here is rolled back for you, so
    every test using this fixture cleans up its own rows in a `finally`.
    """
    try:
        with connect(pg_profile, autocommit=True) as connection:
            yield connection
    except psycopg.OperationalError as exc:  # pragma: no cover - environment, not logic
        pytest.skip(f"rung 0.5 unreachable: {exc}")


def _count_matching(connection: Connection, prefix: str) -> int:
    row = connection.fetch_one(
        "SELECT count(*) FROM knowledge.chunk WHERE chunk_id LIKE %s", (f"{prefix}%",)
    )
    return int(row[0]) if row else 0


def test_a_transient_failure_partway_through_index_commits_none_of_it(
    real_autocommit_connection: Connection,
) -> None:
    """THE EXACT SCENARIO THE REVIEW DESCRIBED: chunk 3 of 5 fails midway
    through a real `index()` call under `autocommit`. Before this slab, each
    per-chunk `execute()` committed on its own — this failure could leave
    chunks 1-2 durably on disk and 3-5 never attempted. After this slab,
    `index()` opens its own transaction, so the failure leaves EXACTLY ZERO
    of the five: the complete prior state (empty, since nothing existed
    before this call), never a partial mix.
    """
    prefix = f"w1-29-atomic-{uuid.uuid4().hex[:8]}"
    chunks = [_chunk(f"{prefix}-{i}", domain="atomicity-proof") for i in range(5)]
    flaky = _FlakyConnection(real_autocommit_connection, fail_at=3)
    store = PgVectorStore(flaky)  # type: ignore[arg-type]

    try:
        with pytest.raises(RuntimeError, match="simulated transient failure"):
            store.index(chunks, [(1.0, 0.0)] * 5)

        assert _count_matching(real_autocommit_connection, prefix) == 0, (
            "a mid-loop failure left a partial write — some but not all of the "
            "five chunks landed, exactly the defect the review found"
        )
    finally:
        real_autocommit_connection.execute(
            "DELETE FROM knowledge.chunk WHERE chunk_id LIKE %s", (f"{prefix}%",)
        )


def test_a_transient_failure_partway_through_supersede_leaves_the_old_version_intact(
    real_autocommit_connection: Connection,
) -> None:
    """The `supersede()` counterpart, with an OLD version already present
    when the failure hits chunk 3 of 5 new chunks — the scenario the (now
    corrected) docstring used to claim a caller's transaction protected
    against, when no caller ever opened one. `supersede()` now wraps `index()`
    and the retire-delete in ONE transaction, so this failure rolls back
    everything attempted: the old version survives complete and unmodified,
    and the retire-delete — reached only after `index()` would have
    succeeded — never even runs.
    """
    prefix = f"w1-29-supersede-atomic-{uuid.uuid4().hex[:8]}"
    old = [_chunk(f"{prefix}-old-{i}", domain="atomicity-proof") for i in range(2)]
    real_store = PgVectorStore(real_autocommit_connection)
    real_store.index(old, [(1.0, 0.0)] * 2)
    assert _count_matching(real_autocommit_connection, f"{prefix}-old-") == 2

    new = [_chunk(f"{prefix}-new-{i}", domain="atomicity-proof") for i in range(5)]
    flaky = _FlakyConnection(real_autocommit_connection, fail_at=3)
    flaky_store = PgVectorStore(flaky)  # type: ignore[arg-type]

    try:
        with pytest.raises(RuntimeError, match="simulated transient failure"):
            flaky_store.supersede(
                retire=[c.chunk_id for c in old], chunks=new, vectors=[(1.0, 0.0)] * 5
            )

        assert _count_matching(real_autocommit_connection, f"{prefix}-old-") == 2, (
            "the untouched old version must survive a failed supersede completely, "
            "not partially retired"
        )
        assert _count_matching(real_autocommit_connection, f"{prefix}-new-") == 0, (
            "none of the new version's chunks may land when the supersede indexing them failed"
        )
    finally:
        real_autocommit_connection.execute(
            "DELETE FROM knowledge.chunk WHERE chunk_id LIKE %s", (f"{prefix}%",)
        )


def test_a_transient_failure_during_the_retire_delete_rolls_back_the_new_chunks_too(
    real_autocommit_connection: Connection,
) -> None:
    """The mirror image: every new chunk inserts cleanly, and the failure
    hits the retire-delete AFTER them. Because `supersede()` wraps `index()`
    and the delete in ONE transaction rather than two separately-committed
    steps, the already-inserted new chunks are rolled back along with the
    failed delete — the old version is left exactly as it was, not "new
    chunks landed but the old ones were never retired," which is itself a
    form of the partial-mix defect the review named.
    """
    prefix = f"w1-29-supersede-retire-atomic-{uuid.uuid4().hex[:8]}"
    old = [_chunk(f"{prefix}-old-{i}", domain="atomicity-proof") for i in range(2)]
    real_store = PgVectorStore(real_autocommit_connection)
    real_store.index(old, [(1.0, 0.0)] * 2)

    new = [_chunk(f"{prefix}-new-{i}", domain="atomicity-proof") for i in range(3)]
    # Calls 1-3 are the three new-chunk inserts, which all succeed; call 4 is
    # the retire DELETE, which fails.
    flaky = _FlakyConnection(real_autocommit_connection, fail_at=4)
    flaky_store = PgVectorStore(flaky)  # type: ignore[arg-type]

    try:
        with pytest.raises(RuntimeError, match="simulated transient failure"):
            flaky_store.supersede(
                retire=[c.chunk_id for c in old], chunks=new, vectors=[(1.0, 0.0)] * 3
            )

        assert _count_matching(real_autocommit_connection, f"{prefix}-old-") == 2
        assert _count_matching(real_autocommit_connection, f"{prefix}-new-") == 0, (
            "a failed retire-delete must roll back the new chunks it was meant "
            "to accompany, not strand them alongside the un-retired old version"
        )
    finally:
        real_autocommit_connection.execute(
            "DELETE FROM knowledge.chunk WHERE chunk_id LIKE %s", (f"{prefix}%",)
        )
