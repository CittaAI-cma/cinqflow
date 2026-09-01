"""The corpus gate: a skip a developer needs, and a failure CI needs.

    "No evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)." — docs/architecture/plates/13-three-lane-ai-testing.md

Lane 3 is the only lane that may claim quality, and every Lane-3 gate grades
against the client's workbooks, which live outside this repository. CI checks
out the repository. So the gates skipped, the job went green, and the green
meant nothing — the failure mode this gate closes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import REQUIRE_CORPUS_ENV, require_corpus

pytestmark = pytest.mark.unit

ABSENT = Path("/nonexistent/clientdata/Uploads/2-Design/Data lake data model.xlsx")


def test_an_absent_corpus_skips_for_a_developer_who_has_only_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clone alone cannot grade against workbooks it does not have, and a
    suite that hard-failed there would train people to ignore failures."""
    monkeypatch.delenv(REQUIRE_CORPUS_ENV, raising=False)
    with pytest.raises(BaseException) as caught:
        require_corpus(ABSENT)
    assert caught.typename == "Skipped"


def test_an_absent_corpus_fails_where_the_environment_claims_to_measure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point. A green Lane 3 must mean the gates RAN."""
    monkeypatch.setenv(REQUIRE_CORPUS_ENV, "1")
    with pytest.raises(BaseException) as caught:
        require_corpus(ABSENT)
    assert caught.typename == "Failed"
    assert "stop reporting these gates as measured" in str(caught.value)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_the_flag_is_read_the_way_people_write_it(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(REQUIRE_CORPUS_ENV, value)
    with pytest.raises(BaseException) as caught:
        require_corpus(ABSENT)
    assert caught.typename == "Failed"


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_anything_else_leaves_the_developer_skip_alone(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(REQUIRE_CORPUS_ENV, value)
    with pytest.raises(BaseException) as caught:
        require_corpus(ABSENT)
    assert caught.typename == "Skipped"


def test_a_present_corpus_neither_skips_nor_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(REQUIRE_CORPUS_ENV, "1")
    workbook = tmp_path / "Uploads" / "2-Design" / "Data lake data model.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"")
    require_corpus(workbook)


def test_every_corpus_dependent_suite_routes_through_the_gate() -> None:
    """A new grading suite that writes its own `pytest.skip` re-opens the hole
    silently, so the hole is closed by grepping for it rather than by memory."""
    tests = Path(__file__).parent.parent
    offenders = [
        path.relative_to(tests).as_posix()
        for path in tests.rglob("test_*.py")
        if "the client corpus is not on this machine" in path.read_text()
        and path.name != "test_corpus_gate.py"
    ]
    assert offenders == [], (
        "these suites skip on an absent corpus without going through "
        f"`require_corpus`, so CI cannot tell measured from unmeasured: {offenders}"
    )
