#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# The Wave-2 exit. THE DEMO IS THE TEST RUN, same as Wave 0's.
#
#   ./scripts/wave2-demo.sh
#
# Every step below is real code, not prose: each `python -` block imports and
# calls the actual `core.*` functions the acceptance suite
# (`tests/acceptance/test_wave2_stories.py`) exercises, on the SAME fixtures —
# same batch id (1244), same BH-AF-002 cascade, same approval identifier shape.
# What is printed is what those functions returned, not a string this script
# merely echoes.
#
# ONE HONEST GAP, NAMED RATHER THAN PAPERED OVER: `platformdata/wave2.md`'s own
# worked example opens with "12 expected · 10 received · 1 missing · 1 at
# risk" — a twelve-feed morning. No seeded scenario in this repository builds
# that board; the acceptance suite's own `board()` fixture (three feeds:
# uhc_md_daily, fidelis_roster, optum_ny) is the closest one that exists. Step
# 1 below runs that fixture and prints WHATEVER IT ACTUALLY COMPUTES — 3
# expected, not 12 — rather than fabricate the document's larger number from
# a board nobody built. `core.agents.mapping_suggestion`'s own
# `SuggestionResult.manual_path` docstring is the platform's canonical
# statement of why: a script that prints a number it did not compute reads as
# careful and is actually broken. Every other step (3 onward: batch #1244,
# BH-AF-002, the 14-occurrence/18-minute guide, the CINQ approval, the
# Certified-with-Waiver verdict, both refusals) matches the document's own
# numbers exactly, because a pre-existing seed for each of them already lives
# in `tests/acceptance/test_wave2_stories.py`.
#
# THE TWO REFUSALS IN STEP 8 ARE NOT ASSERTED IN PROSE. The first calls the
# real `core.operations.actions.authorize` and catches the real `RefusedError`
# it raises for a paused feed. The second builds a real `FingerprintMatchAgent`
# over the mock adapters `tests/contract/test_fingerprint_match_agent.py`
# already uses, scripts a model into proposing a write ("delete_everything")
# as its remedy, and reads back the `AgentAction` row the real gateway wrote
# when it discarded that remedy — "refused + logged" is a fact this script
# checks, not a claim it makes.
#
# This is asserted in CI as a step in the `pipeline` job (`.github/workflows/
# ci.yml`) — unlike `scripts/wave0-demo.sh`, whose identical claim about
# itself is, at time of writing, not true (no CI job invokes it).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."
# Local dev has a project .venv; CI's `pipeline` job installs straight into
# the runner's own interpreter (`pip install -e . --no-deps`, no .venv at
# all) — so this falls back rather than hard-coding the path wave0-demo.sh
# does, which is very likely why THAT script has never actually run in CI.
if [ -x .venv/bin/python ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi
export PYTHONPATH=src
[ -f .env ] && set -a && . ./.env && set +a

say() { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }

say "1 · The morning board"
$PY - <<'PY'
from datetime import UTC, date, datetime

from cinqflow.core.sla import ArrivalBoard, Cycle

# The acceptance suite's own `board()` fixture
# (tests/acceptance/test_wave2_stories.py) — the closest existing seed to the
# document's twelve-feed morning. Printed honestly: three feeds in, three
# feeds' worth of counters out.
NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
board = ArrivalBoard(
    cycles=(
        Cycle("uhc_md_daily", date(2026, 8, 30), datetime(2026, 8, 30, 6, tzinfo=UTC)),
        Cycle(
            "fidelis_roster",
            date(2026, 8, 30),
            datetime(2026, 8, 30, 5, tzinfo=UTC),
            actual_ts=datetime(2026, 8, 30, 5, 12, tzinfo=UTC),
            files_received=1,
        ),
        Cycle("optum_ny", date(2026, 8, 30), datetime(2026, 8, 30, 8, 50, tzinfo=UTC)),
    ),
    now=NOW,
)
counters = board.counters()
print(
    f"{counters['expected']} expected · {counters['received']} received · "
    f"{counters['missing']} missing · {counters['at_risk']} at risk"
)
PY

say "2 · The missing file, investigated"
$PY - <<'PY'
from datetime import UTC, date, datetime

from cinqflow.core.registry.source import SourceKind, SourceRecord
from cinqflow.core.sla import Cycle

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
missing = Cycle("uhc_md_daily", date(2026, 8, 30), datetime(2026, 8, 30, 6, tzinfo=UTC))
# `counterparty_contact` — the registry field CF-V2-E12-05 needed for "who to
# contact", stored as a NAME rather than computed (core.registry.source's own
# docstring: "the person at the OTHER organisation... recorded, because 'who
# do we ring' was a thing three people knew and nobody had written down").
source = SourceRecord(
    source_id="uhc-md",
    name="UnitedHealthcare of Maryland",
    kind=SourceKind.PAYER,
    counterparty_contact="D. Kowalski, UHC EDI operations",
)
print(f"{missing.feed_id} — {missing.why(NOW)}; contact: {source.counterparty_contact}")
PY

say "3 · The failed batch"
$PY - <<'PY'
from datetime import UTC, datetime, timedelta

from cinqflow.core.model.vocabulary import ErrorCategory, Layer
from cinqflow.core.operations.monitor import separate_cascade
from cinqflow.ports.control_tables import ErrorRecord

# The client's own BH-AF-002 cascade, verbatim from
# tests/acceptance/test_wave2_stories.py's BH_AF_002_ERRORS.
T0 = datetime(2026, 8, 30, 3, 3, tzinfo=UTC)


def error(hash_: str, stage: Layer, message: str, ts: datetime) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=hash_,
        batch_id="1244",
        stage=stage,
        category=ErrorCategory.SYSTEM,
        message=message,
        occurred_ts=ts,
    )


cascade = separate_cascade(
    [
        error("h1", Layer.BRONZE, "required key 'business_date' absent", T0),
        error(
            "h2", Layer.SILVER_RAW, "upstream stage produced no output", T0 + timedelta(seconds=2)
        ),
        error("h3", Layer.SILVER_RAW, "load skipped: no input", T0 + timedelta(seconds=3)),
    ]
)
assert cascade.first is not None
print(f"batch #1244 failed at {cascade.first.stage.value.title()} {T0:%H:%M}; {cascade.explain()}")
PY

say "4 · Fingerprinted"
$PY - <<'PY'
from datetime import UTC, datetime, timedelta

from cinqflow.core.model.vocabulary import ErrorCategory, Layer
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.ports.control_tables import ErrorRecord

T0 = datetime(2026, 8, 30, 3, 3, tzinfo=UTC)


def error(hash_: str, stage: Layer, message: str, ts: datetime) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=hash_,
        batch_id="1244",
        stage=stage,
        category=ErrorCategory.SYSTEM,
        message=message,
        occurred_ts=ts,
    )


errors = [
    error("h1", Layer.BRONZE, "required key 'business_date' absent", T0),
    error("h2", Layer.SILVER_RAW, "upstream stage produced no output", T0 + timedelta(seconds=2)),
    error("h3", Layer.SILVER_RAW, "load skipped: no input", T0 + timedelta(seconds=3)),
]
# The signature is computed from the SAME errors before a guide exists for it —
# `fingerprint_batch` is entirely deterministic, no model in the path.
novel = fingerprinting.fingerprint_batch(
    batch_id="1244", feed_id="fidelis_roster", errors=errors, now=T0
)
guide = fingerprinting.RecoveryGuide(
    guide_id="BH-AF-002",
    title="Missing mandatory task parameter",
    signatures=frozenset({novel.signature}),
    steps=("Re-run validate_input, then evaluate_bronze_load.",),
)
# 14 prior occurrences, mean fix 18 minutes — the story's own worked example.
history = [
    fingerprinting.PriorIncident(
        incident_id=f"INC-{n}",
        occurred_ts=T0 - timedelta(days=n + 1),
        fix_minutes=18,
        batch_id=f"12{n:02d}",
    )
    for n in range(14)
]
matched = fingerprinting.fingerprint_batch(
    batch_id="1244",
    feed_id="fidelis_roster",
    errors=errors,
    guides=[guide],
    history=history,
    now=T0,
)
assert matched.match is not None
print(f"{matched.match.guide.guide_id} · {matched.match.summary()}")
PY

say "5 · Fix approved on the action surface"
$PY - <<'PY'
from datetime import UTC, datetime

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState
from cinqflow.core.operations.actions import ActionRequest, Environment, OpsAction, authorize

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
SAM = Actor(subject="sam@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam Okafor")

request = ActionRequest(
    action=OpsAction.RETRY,
    target="1244",
    actor=SAM,
    reason="BH-AF-002 guide: re-run validate_input, then evaluate_bronze_load.",
    # A NAME the operator attaches, checked for presence only — the platform
    # does not own the client's change-ticket system (ActionRequest's own
    # docstring). Production requires one; `authorize` refuses without it.
    approval_identifier="CINQ-2026-0830-11",
)
authorize(request, environment=Environment.PRODUCTION, batch_state=BatchState.FAILED, now=NOW)
print(
    f"retry on batch {request.target} authorized · "
    f"approval {request.approval_identifier} attached"
)
PY

say "6 · Retried"
$PY - <<'PY'
from datetime import UTC, datetime

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState
from cinqflow.core.operations.actions import (
    ActionPhase,
    ActionRequest,
    Environment,
    OpsAction,
    authorize,
    request_action,
    verify,
)

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
SAM = Actor(subject="sam@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam Okafor")

request = ActionRequest(
    action=OpsAction.RETRY,
    target="1244",
    actor=SAM,
    reason="BH-AF-002 guide: re-run validate_input, then evaluate_bronze_load.",
    approval_identifier="CINQ-2026-0830-11",
)
authorize(request, environment=Environment.PRODUCTION, batch_state=BatchState.FAILED, now=NOW)
requested = request_action(request, now=NOW)
assert requested.phase is ActionPhase.REQUESTED
# "Retry requested" is not "retry succeeded" — `verify` re-reads the control
# tables (here, what the engine reports) and only THEN promotes the phase.
verified = verify(
    requested,
    observed_state=BatchState.COMPLETED,
    expected=frozenset({BatchState.COMPLETED}),
    outcome="resumed from silver_raw; 9,992 rows loaded",
    now=NOW,
)
assert verified.is_complete
print(
    f"{requested.phase.value.upper()} -> (engine) -> {verified.phase.value.upper()}, "
    f"batch {verified.observed_state.value}"
)
PY

say "7 · Certified"
$PY - <<'PY'
from datetime import UTC, date, datetime
from decimal import Decimal

from cinqflow.core.certification import Check, CheckKind, Verdict, certify, evidence_document
from cinqflow.core.variance import Variance, VarianceKind, Waiver

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


def all_checks() -> list[Check]:
    return [
        Check(CheckKind.BALANCE, True, "rows_in 22,014,882 == out + quarantined + drops"),
        Check(CheckKind.RECONCILIATION, True, "all stages balanced"),
        Check(CheckKind.DROP_LEDGER, True, "0 unattributed drops"),
        Check(CheckKind.DQ_RULES, True, "18 of 18 rules passed"),
        Check(CheckKind.SLA_WINDOW, True, "arrived 12 minutes inside grace"),
        Check(CheckKind.SCHEMA_CONTRACT, True, "contract v3, no drift"),
    ]


count_variance = Variance(
    variance_id="V2",
    batch_id="1244",
    feed_id="claims",
    kind=VarianceKind.COUNT,
    expected=Decimal("1000"),
    actual=Decimal("998"),
    tolerance=Decimal("5"),
    opened_by="steward@cinqcare.test",
    opened_ts=NOW,
)
# Waived by someone other than whoever opened it — the universal negative
# `Variance.waive` enforces — with a reason and a bounded expiry.
waived = count_variance.waive(
    Waiver(
        "other@cinqcare.test",
        "known payer cancellation lag",
        date(2026, 8, 30),
        date(2026, 11, 28),
    )
)
result = certify(
    batch_id="1244", feed_id="claims", checks=all_checks(), variances=[waived], now=NOW
)
assert result.verdict is Verdict.CERTIFIED_WITH_WAIVER
document = evidence_document(result)
assert document == evidence_document(result)  # byte-identical, every time it is re-derived
assert waived.waiver is not None
print(
    f"{result.verdict.value}; evidence exported ({len(document)} bytes), "
    f"{len(result.waived)} waiver named: {waived.waiver.reason!r}"
)
PY

say "8 · The refusals, unchanged"
$PY - <<'PY'
# Refusal 1 — a retry on a PAUSED feed, refused naming the pauser. Calls the
# real `authorize` and catches the real `RefusedError` it raises.
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState
from cinqflow.core.operations.actions import (
    ActionRequest,
    Environment,
    OpsAction,
    RefusedError,
    authorize,
)

SAM = Actor(subject="sam@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam Okafor")
request = ActionRequest(
    action=OpsAction.RETRY,
    target="1244",
    actor=SAM,
    reason="Transient cluster error; the guide says retry.",
)
try:
    authorize(
        request,
        environment=Environment.DEVELOPMENT,
        batch_state=BatchState.FAILED,
        feed_paused=True,
        paused_reason="feed paused by J. Smith — mapping change pending",
    )
except RefusedError as refused:
    print(f"retry on {request.target} REFUSED: {refused.refusal.detail}")
else:
    raise SystemExit("expected the retry to be refused")
PY
$PY - <<'PY'
# Refusal 2 — an agent attempts a write, refused + logged. Builds the real
# `FingerprintMatchAgent` (CF-V2-E12-04) over the same mock adapters
# `tests/contract/test_fingerprint_match_agent.py` already uses, scripts a
# model into proposing "delete_everything" as its remedy, and reads back the
# `AgentAction` row the real gateway wrote when it discarded that remedy — a
# WRITE never reaches this agent's tool whitelist, and the refusal is a row,
# not a swallowed exception.
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.agents.fingerprint_match.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.identity import Principal, Scopes
from cinqflow.core.model.llm import TaskClass
from cinqflow.core.model.vocabulary import ActorType, ErrorCategory, Layer
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.intelligence.agents.fingerprint_match import FingerprintMatchAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.control_tables import ErrorRecord

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqflow.demo", actor_type=ActorType.HUMAN, display_name="Demo operator")
STEWARD = Actor(
    subject="dev-steward@cinqflow.demo", actor_type=ActorType.HUMAN, display_name="Demo steward"
)


def published(obj):
    return replace(
        obj, lifecycle_state=LifecycleState.PUBLISHED, approved_by=STEWARD, approved_ts=NOW
    )


store = MemMetadataDb()
for template in TEMPLATES:
    store.save(published(template.as_governed(author=BA, now=NOW)))
control = MemStoreControlTables()

# A SEPARATE, still-novel incident — batch 1244 above already matched a known
# guide by this point in the run, and a KNOWN incident never reaches this
# agent at all (`FingerprintMatchAgent.propose`'s own guardrail). This is the
# platform's live write-refusal machinery, demonstrated on a failure nothing
# has fingerprinted yet.
root_error = ErrorRecord(
    error_id_hash="wave2-demo-h1",
    batch_id="1301",
    stage=Layer.SILVER_RAW,
    category=ErrorCategory.SCHEMA,
    message="date of birth is not a date",
    occurred_ts=NOW,
)
incident = fingerprinting.fingerprint_batch(
    batch_id="1301", feed_id="fidelis_roster", errors=[root_error], guides=[], now=NOW
)
assert incident.kind is fingerprinting.IncidentKind.NOVEL

tools = ToolContext(
    principal=Principal(
        subject="platform@cinqflow", display_name="platform", scopes=Scopes(feeds=frozenset({"*"}))
    ),
    control=control,
    metadata=store,
    agent="fingerprint-match",
    now=NOW,
)


def respond(prompt: str, task: TaskClass) -> str:
    if task is TaskClass.SMALL:
        return json.dumps({"narrative": "", "citations": []})
    # The model asks for a WRITE. `RecoveryGuide.remedy` accepts only a
    # certified `OpsAction` identifier — "delete_everything" is not one.
    return json.dumps(
        {
            "title": "Novel schema failure at silver_raw",
            "steps": ["Check the upstream record."],
            "remedy": "delete_everything",
            "is_transient": False,
            "confidence": 0.95,
            "rationale": "the model asked to delete the offending records outright",
        }
    )


gateway = LlmGateway(
    llm=ScriptedLlm(respond),
    phi_scrub=PatternPhiScrub(),
    metadata_db=store,
    observability=NoopObservability(),
    budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
    routing=Routing(small="small-model", large="large-model"),
    clock=lambda: NOW,
)
agent = FingerprintMatchAgent(llm=gateway, tools=tools, runtime=InProcAgentRuntime())
result = agent.propose(incident, caller=BA, run_id="wave2-demo-write-attempt", now=NOW)

assert result.drafted is not None
assert result.drafted.guide.remedy is None, "a write remedy must never survive onto the guide"
logged = store.read_agent_actions(agent="fingerprint-match")
refusal = next(a for a in logged if "not a certified OpsAction" in a.detail)
print(f"agent proposed remedy 'delete_everything' -> REFUSED: {refusal.detail}")
print(f"logged as run={refusal.run_id} outcome={refusal.outcome.value}")
PY

printf '\n\033[1;32mWave 2 · green\033[0m\n'
