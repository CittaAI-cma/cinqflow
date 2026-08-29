"""The citation address space.

CF-V0-E16-09 requires every tool result to carry a resolvable citation_id, and
CF-V0-E16-10 requires every factual claim to carry one:

    "every factual claim carries a resolvable citation; uncited claims are a
     defect class"
    — docs/architecture/INVARIANTS.md, intelligence

This module makes that vocabulary the platform's ADDRESS SPACE: one parser and
one resolver serve the agent's citations AND the UI's routes, so "clicking a
citation opens that registry row" is not agent plumbing — it is the same
resolve() the breadcrumb and the deep link already use.

Consequence worth naming: the Lane-3 gate "citation resolvability = 100%"
becomes a test over the router, computable with no model in the loop.
"""

from __future__ import annotations

import pytest

from cinqflow.core.citations import CitationId, CitationKind, UnresolvableCitationError, parse


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "kind", "subject", "version", "fragment"),
    [
        (
            "feed:fidelis-downstate-roster@v3",
            CitationKind.FEED,
            "fidelis-downstate-roster",
            3,
            None,
        ),
        (
            "plan:fidelis-downstate-roster@v3",
            CitationKind.PLAN,
            "fidelis-downstate-roster",
            3,
            None,
        ),
        (
            "contract:fidelis-downstate-roster@v1",
            CitationKind.CONTRACT,
            "fidelis-downstate-roster",
            1,
            None,
        ),
        ("batch:8842", CitationKind.BATCH, "8842", None, None),
        ("batch:8842#silver_raw", CitationKind.BATCH, "8842", None, "silver_raw"),
        ("recon:8842", CitationKind.RECON, "8842", None, None),
        ("recon:8842#DQ-002", CitationKind.RECON, "8842", None, "DQ-002"),
        ("error:9f3c1a7b", CitationKind.ERROR, "9f3c1a7b", None, None),
        ("file:sha256-abc123", CitationKind.FILE, "sha256-abc123", None, None),
        ("rule:DQ-002", CitationKind.RULE, "DQ-002", None, None),
        ("term:member-date-of-birth", CitationKind.TERM, "member-date-of-birth", None, None),
    ],
)
def test_the_whole_citation_vocabulary_parses(
    raw: str, kind: CitationKind, subject: str, version: int | None, fragment: str | None
) -> None:
    """These shapes ARE the vocabulary — memory/05-ground-truth/05-certified-query-tools.md."""
    cid = parse(raw)
    assert (cid.kind, cid.subject, cid.version, cid.fragment) == (kind, subject, version, fragment)
    assert str(cid) == raw, "a citation must round-trip, or a UI link is not stable"


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "",
        "feed",  # no subject
        "feed:",  # empty subject
        "wibble:8842",  # not in the vocabulary
        "feed:a@v0",  # versions start at 1
        "feed:a@vx",  # not a version
        "batch:8842@v2",  # a batch is not versioned
        "term:Bad Slug",  # a term is a slug
        "feed:../../etc/passwd",  # a citation is not a path
    ],
)
def test_anything_outside_the_vocabulary_is_refused(raw: str) -> None:
    """A citation the UI cannot open is worse than no citation: it looks cited."""
    with pytest.raises(UnresolvableCitationError):
        parse(raw)


@pytest.mark.unit
def test_a_citation_is_a_ui_route_and_that_is_the_whole_point() -> None:
    """The agent and the navigation share ONE address space."""
    assert parse("batch:8842").route == "/operations/control/batch/8842"
    assert parse("batch:8842#silver_raw").route == "/operations/control/batch/8842?panel=silver_raw"
    assert (
        parse("recon:8842#DQ-002").route == "/operations/control/batch/8842?panel=recon&drop=DQ-002"
    )
    assert parse("feed:fidelis-downstate-roster@v3").route == (
        "/data/intake/feed/fidelis-downstate-roster?version=3"
    )
    assert parse("rule:DQ-002").route == "/data/intake/rule/DQ-002"
    assert parse("term:member-date-of-birth").route == "/data/intake/glossary/member-date-of-birth"


@pytest.mark.unit
def test_every_kind_has_a_route_so_no_citation_can_be_a_dead_end() -> None:
    """If a kind exists in the vocabulary, the UI must be able to open it."""
    samples = {
        CitationKind.FEED: "feed:f@v1",
        CitationKind.PLAN: "plan:f@v1",
        CitationKind.CONTRACT: "contract:f@v1",
        CitationKind.MAPPING: "mapping:f@v1",
        CitationKind.BATCH: "batch:1",
        CitationKind.RECON: "recon:1",
        CitationKind.ERROR: "error:abc",
        CitationKind.FILE: "file:abc",
        CitationKind.RULE: "rule:DQ-001",
        CitationKind.TERM: "term:a-term",
    }
    assert set(samples) == set(CitationKind), "a kind with no sample is a kind with no test"
    for raw in samples.values():
        assert parse(raw).route.startswith("/"), raw


@pytest.mark.unit
def test_citations_are_values_so_they_can_be_deduplicated_and_sorted() -> None:
    assert parse("batch:8842") == parse("batch:8842")
    assert len({parse("batch:8842"), parse("batch:8842")}) == 1
    assert parse("batch:8842") != parse("batch:8843")


@pytest.mark.unit
def test_a_citation_carries_no_member_data_by_construction() -> None:
    """No tool returns a member-level row, so no citation may address one.

    The vocabulary has no `member:` or `row:` kind, and this test is what stops
    one being added quietly.
    """
    assert not {k for k in CitationKind if k.value in {"member", "row", "record", "patient"}}


@pytest.mark.unit
def test_construction_is_validated_not_only_parsing() -> None:
    """The typed constructor is the path tool authors use; it must refuse too."""
    with pytest.raises(UnresolvableCitationError):
        CitationId(kind=CitationKind.BATCH, subject="8842", version=2)
    with pytest.raises(UnresolvableCitationError):
        CitationId(kind=CitationKind.FEED, subject="")
