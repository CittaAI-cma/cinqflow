"""W1-37 — the certification-evidence mirror between the API and the agent
tool.

    "a caller asking a model is answered by the identical arithmetic a human
     reading the screen would see — never a second, slightly different,
     opinion."
    — `intelligence.tools`'s own module note on why `_certification_checks`
      is written twice at all

`api.app._certification_checks` and `intelligence.tools._certification_
checks` are DELIBERATELY duplicated — the layering contract (`intelligence`
may import neither `api` nor `workers`) leaves no other way for a tool runner
to read the same control-plane rows a human's screen reads. W1-32 taught the
API side to surface a SCHEMA_CONTRACT drift finding's own blast-radius detail
text; the tool side was never updated to match, so `cinqflow ask`'s
`get_certification` kept reporting a generic "N drift finding(s), none
blocking" for the SAME batch the UI showed real detail for. This runs both
functions against byte-identical fixture batches and asserts their evidence
text never diverges again.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.api.app import _certification_checks as api_certification_checks
from cinqflow.core.certification import CheckKind
from cinqflow.core.model.vocabulary import BatchState
from cinqflow.intelligence.tools import _certification_checks as tool_certification_checks
from cinqflow.ports.control_tables import BatchControl, SchemaDrift

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
BATCH_ID = "9100"
FEED_ID = "fidelis-downstate-roster"


def _batch(control: MemStoreControlTables, *drift: SchemaDrift) -> BatchControl:
    batch = BatchControl(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        feed_version=1,
        business_date="2026-08-01",
        state=BatchState.COMPLETED,
        started_ts=NOW,
        completed_ts=NOW,
    )
    control.open_batch(batch)
    for finding in drift:
        control.record_schema_drift(finding)
    return batch


def _schema_contract_evidence(checks: tuple) -> str:  # type: ignore[type-arg]
    return next(c.evidence for c in checks if c.kind is CheckKind.SCHEMA_CONTRACT)


def test_the_no_drift_evidence_is_identical() -> None:
    control = MemStoreControlTables()
    batch = _batch(control)

    api_evidence = _schema_contract_evidence(api_certification_checks(control, batch))
    tool_evidence = _schema_contract_evidence(tool_certification_checks(control, batch))

    assert api_evidence == tool_evidence == "0 drift finding(s), none blocking"


def test_a_blocking_drift_evidence_is_identical() -> None:
    control = MemStoreControlTables()
    batch = _batch(
        control,
        SchemaDrift(
            batch_id=BATCH_ID,
            feed_id=FEED_ID,
            classification="removed",
            column_name="plan_code",
            detail="plan_code is gone and no term in the glossary carries it forward",
            blocked_batch=True,
            detected_ts=NOW,
        ),
    )

    api_evidence = _schema_contract_evidence(api_certification_checks(control, batch))
    tool_evidence = _schema_contract_evidence(tool_certification_checks(control, batch))

    assert api_evidence == tool_evidence == "blocking drift: plan_code"


def test_a_notable_non_blocking_drifts_blast_radius_text_is_identical() -> None:
    """The exact case W1-37 fixes: a RENAMED finding that never blocked the
    batch still carries its own blast-radius detail — `attach_blast_radius`'s
    whole point — and BOTH surfaces must show that detail verbatim, not a
    bare count."""
    control = MemStoreControlTables()
    detail = "DOB -> date_of_birth; read by DQ-002, mapped by 1 line"
    batch = _batch(
        control,
        SchemaDrift(
            batch_id=BATCH_ID,
            feed_id=FEED_ID,
            classification="renamed",
            column_name="DOB",
            detail=detail,
            blocked_batch=False,
            detected_ts=NOW,
        ),
    )

    api_evidence = _schema_contract_evidence(api_certification_checks(control, batch))
    tool_evidence = _schema_contract_evidence(tool_certification_checks(control, batch))

    assert api_evidence == tool_evidence == detail
    assert "drift finding(s), none blocking" not in tool_evidence, (
        "the tool side must surface the SAME blast-radius detail the API does, "
        "not fall back to a generic count the way it did before W1-37"
    )
