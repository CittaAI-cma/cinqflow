"""Populate a CINQFLOW plane from the client's real de-identified extracts.

    python -m population.populate --profile profiles/local.yaml --all
    python -m population.populate --source fidelis-upstate
    python -m population.populate --report

TWO PATHS, AND THE SPLIT IS THE POINT.

  MEMBER-GRAIN sources go through `PipelineRunner` — the platform's own runner,
  the same one `cinqflow ingest` uses. Landing controls, fingerprint check,
  drift classification, cast, DQ rules, quarantine attribution, reconciliation
  and the balance equation all run for real, and every control table fills as a
  consequence.

  SEGMENT-GRAIN sources stop at Bronze, via `PostgresCompute.land_bronze` —
  again the platform's own adapter method, the same call the runner makes for
  its Bronze stage. They stop because `silver_raw.members` is unique on
  `(batch_id, feed_id, source_member_id)` and these sources send 2.4 to 11.9
  rows per member: at full grain they collide by construction. Running them
  anyway would roll back the whole transaction and lose Bronze too, so
  "everything to Bronze, nothing to Silver" is the outcome that keeps the data
  and tells the truth. `control.batch_stage_status` records BRONZE completed
  and no Silver Raw row at all, which is exactly what happened.

WHY NOT `terminal_layer=Layer.BRONZE` ON THE PLAN: `compile_feed` accepts it and
the IR checks it, but `PipelineRunner.run` advances to Silver Raw
unconditionally — it does not read `plan.terminal_layer`. Honouring it there
would be a change to the chip, so the driver does the stopping instead and this
comment records where the seam actually is. It is a good candidate for a small
platform story later.

NOTHING HERE IS IMPORTED BY THE PLATFORM. `src/cinqflow/` is untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from population import sources as src
from population.plane import Plane, open_plane, table_counts

#: The business date every load is filed under unless told otherwise. The
#: extracts are Feb-Mar 2026 deliveries, so this is their real cycle rather
#: than "today" — a population dated by when the script ran would make every
#: SLA and freshness figure on the screens meaningless.
DEFAULT_BUSINESS_DATE = "2026-03-01"


@dataclass(frozen=True)
class _Recovered:
    """A landing file that was already on disk from a rolled-back run.

    Shaped like the connector's own outcome — `.file` is the `FileRef` — so the
    two load paths take one code path and neither has to know which happened.
    """

    file: Any


@dataclass
class Outcome:
    """What one source's load did. Printed, and returned for the report."""

    key: str
    feed_id: str
    files: int = 0
    delivered: int = 0
    refused: int = 0
    bronze_rows: int = 0
    silver_rows: int = 0
    quarantined: int = 0
    batches: list[str] = field(default_factory=list)
    stopped_at: str = ""
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0


# ── building the metadata the platform asks for ──────────────────────────────
def feed_record(source: src.Source) -> Any:
    from cinqflow.core.registry.feed import FeedRecord

    return FeedRecord(
        feed_id=source.feed_id,
        domain=source.domain,
        source_system=source.source_system,
        file_format=source.file_format,
        landing_path=source.landing_path,
        file_pattern=source.file_pattern,
        schedule_cron=source.schedule_cron,
        # The PRODUCTION name, not the de-identified one — see
        # `Source.production_filenames`. FeedRecord refuses a pattern that does
        # not match its own sample, which is what caught this.
        sample_filename=source.production_filenames[0],
        # Generous, and deliberately so: these are the REAL sizes, from 768 KB
        # to 67 MB. A min/max copied from the golden roster would reject the
        # actual estate at the landing gate, which is the kind of guard that
        # gets disabled rather than fixed.
        min_size_bytes=100,
        max_size_bytes=200_000_000,
    )


def schema_contract(source: src.Source) -> Any:
    """The contract, from the source's own measured header.

    `is_phi` is set here, per column, and it is the ONLY thing that drives
    masking on every screen and in every API response. Getting it right at this
    line is what keeps a member's name off a browser; getting it wrong is a
    disclosure no downstream check will catch.
    """
    from cinqflow.core.registry.contract import ContractColumn, SchemaContract
    from cinqflow.core.schema_spec import TypeName

    kinds = {"string": TypeName.STRING, "date": TypeName.DATE}
    return SchemaContract(
        feed_id=source.feed_id,
        version=1,
        columns=tuple(
            ContractColumn(
                m.canonical,
                kinds[m.kind],
                # `source_member_id` is NOT NULL in the target table, so the
                # contract must say so too — otherwise a null id reaches the
                # insert and fails as a database error rather than as an
                # attributed quarantine row.
                nullable=m.canonical != "source_member_id",
                source_name=m.source,
                is_phi=m.is_phi,
            )
            for m in source.mappings
        ),
        key_columns=("source_member_id",),
    )


def dq_rules(source: src.Source) -> tuple[Any, ...]:
    """The completeness subset the platform's one rule constructor expresses.

    DQ-002 (first name not null) is the programme's canonical quarantine
    reason, harvested from the client's own 110-rule workbook. The other 29
    Enrollment rules in that workbook need constructors this platform does not
    have yet — so they are absent rather than approximated, and the gap is the
    input to the rule-authoring agent's next story.
    """
    from cinqflow.core.registry.contract import Severity, not_null

    rules = []
    for canonical in source.not_null:
        rules.append(
            not_null(
                "DQ-002" if canonical == "first_name" else f"DQ-{canonical}",
                canonical,
                name=f"Member {canonical.replace('_', ' ').title()} Not Null",
                severity=Severity.HIGH,
                description=("Required for member outreach, care coordination and CMS submissions"),
                glossary_id="BG-002" if canonical == "first_name" else "",
            )
        )
    return tuple(rules)


def logical_plan(source: src.Source, contract: Any, rules: tuple[Any, ...]) -> Any:
    from cinqflow.core.compiler import compile_feed

    return compile_feed(feed=feed_record(source), feed_version=1, contract=contract, rules=rules)


# ── delivery ─────────────────────────────────────────────────────────────────
def deliver(plane: Plane, source: src.Source, path: Path, business_date: str) -> Any | None:
    """Hand the real bytes to the `connector` pin. Refusals are ANSWERS.

    A fingerprint already in `control.input_registry` means this exact file was
    delivered before, and the refusal is the replay guard demonstrating itself —
    so it is reported and skipped, never retried and never forced.
    """
    from cinqflow.core.delivery import DeliveryError, Manifest
    from cinqflow.core.model.files import FileRef
    from cinqflow.ports.connector import AlreadyDeliveredError

    feed = feed_record(source)
    try:
        return plane.connector.deliver(
            path.read_bytes(),
            # The same transformation the `sample_filename` above used, from
            # the same function. The bytes are unchanged; only the name the
            # registry sees is the name the payer would have sent.
            filename=src.production_filename(path.name),
            feed_id=feed.feed_id,
            landing_path=feed.landing_path,
            business_date=business_date,
            manifest=Manifest(),
        )
    except (AlreadyDeliveredError, DeliveryError) as refused:
        # TWO REFUSALS WEAR ONE EXCEPTION, and the difference decides what to do.
        #
        #   "already in the landing zone" — the FILENAME is taken. `deliver()`
        #     writes to the filesystem, which is NOT inside the database
        #     transaction, so a run that rolled its rows back still left its
        #     file on disk while the registry kept no row for it: an orphan.
        #     Reading the file already there is correct and idempotent, whereas
        #     delivering under a second name would put two copies of one
        #     delivery in the zone — exactly what the refusal protects against.
        #
        #   anything else — most importantly a fingerprint already in
        #     `control.input_registry`, which is the replay guard demonstrating
        #     itself. Reported and skipped, never retried and never forced.
        if "already in the landing zone" in str(refused):
            key = (
                f"{feed.landing_path}/incoming/{business_date}/{src.production_filename(path.name)}"
            )
            if plane.storage.exists(key):
                print("      recovered an orphaned landing file (a rolled-back run left it)")
                return _Recovered(
                    FileRef(
                        key=key,
                        size_bytes=path.stat().st_size,
                        modified_ts=datetime.now(UTC),
                        fingerprint=plane.storage.fingerprint(key),
                    )
                )
        print(f"      not delivered (refused): {refused}")
        return None


# ── the two load paths ───────────────────────────────────────────────────────
def run_full_spine(
    plane: Plane, source: src.Source, landed: Any, business_date: str
) -> tuple[str | None, int, int, int, str | None]:
    """Landing -> Bronze -> Silver Raw, through the platform's own runner."""
    from cinqflow.workers.pipeline import PipelineRunner

    contract = schema_contract(source)
    rules = dq_rules(source)
    outcome = PipelineRunner(
        storage=plane.storage,
        control=plane.control,
        compute=plane.compute,
        source_system=source.source_system,
    ).run(
        landed,
        feed=feed_record(source),
        feed_version=1,
        contract=contract,
        rules=rules,
        plan=logical_plan(source, contract, rules),
        business_date=business_date,
    )
    if outcome.batch_id is None:
        return None, 0, 0, 0, f"no batch opened: {outcome.decision.outcome.value}"
    bronze = plane.compute.count_bronze(outcome.batch_id)
    silver = plane.compute.count_silver_raw(outcome.batch_id)
    quarantined = outcome.result.reconciliation.quarantined if outcome.result else 0
    return outcome.batch_id, bronze, silver, quarantined, outcome.failure


def run_bronze_only(
    plane: Plane, source: src.Source, landed: Any, business_date: str
) -> tuple[str, int, str | None]:
    """Landing -> Bronze, and stop. See the module note for why.

    This reproduces the runner's Bronze stage using the same adapter call, and
    writes the same control rows, so the batch is indistinguishable from a
    real one except in the one respect that is true: it has no Silver Raw stage.
    """
    from cinqflow.core.model.vocabulary import BatchState, FileState, Layer
    from cinqflow.core.parsers import parse
    from cinqflow.ports.control_tables import BatchControl, InputFile, StageStatus

    contract = schema_contract(source)
    rules = dq_rules(source)
    plan = logical_plan(source, contract, rules)
    now = datetime.now(UTC)
    batch_id = uuid.uuid4().hex[:12]

    plane.control.open_batch(
        BatchControl(
            batch_id=batch_id,
            feed_id=source.feed_id,
            feed_version=1,
            business_date=business_date,
            state=BatchState.IN_PROGRESS,
            started_ts=now,
            model_version="silver_raw.members@v1",
        )
    )
    plane.control.register_input_file(
        InputFile(
            batch_id=batch_id,
            feed_id=source.feed_id,
            key=landed.key,
            filename=landed.filename,
            size_bytes=landed.size_bytes,
            fingerprint=landed.fingerprint,
            state=FileState.PROCESSED,
            arrived_ts=now,
            record_count=None,
        )
    )

    content = plane.storage.read_bytes(landed.key)
    parsed = parse(content, file_format=source.file_format)
    rows = _rows_of(parsed)
    landed_count = plane.compute.land_bronze(
        plan=plan, batch_id=batch_id, rows=rows, source_system=source.source_system
    )
    plane.control.record_stage(
        StageStatus(
            batch_id=batch_id,
            stage=Layer.BRONZE,
            state=BatchState.COMPLETED,
            started_ts=now,
            completed_ts=datetime.now(UTC),
            records_in=len(rows),
            records_out=landed_count,
        )
    )
    # COMPLETED, not FAILED. Nothing failed: the batch did what its grain
    # allows. A FAILED state here would put a false incident on the operations
    # board every month for a feed that is behaving correctly.
    plane.control.update_batch_state(batch_id, BatchState.COMPLETED)
    return batch_id, landed_count, None


def _rows_of(parsed: Any) -> list[dict[str, str]]:
    """The parsed table as row dicts, all values as strings.

    Bronze stores the whole row as JSON, so this is the shape `land_bronze`
    wants. Arrow -> pylist once, rather than per-row attribute access.
    """
    columns = list(parsed.columns)
    data = parsed.table.to_pydict()
    length = parsed.row_count
    return [
        {name: ("" if data[name][i] is None else str(data[name][i])) for name in columns}
        for i in range(length)
    ]


# ── orchestration ────────────────────────────────────────────────────────────
def populate_source(
    source: src.Source, *, profile: str, business_date: str, dry_run: bool
) -> Outcome:
    """One source, all its files, in ONE atomic transaction.

    Atomic per SOURCE rather than per file: Molina's four files are one feed and
    one cycle, and a load that committed two of them would leave a feed
    half-delivered with no way to tell which half. Per-source also means a
    failure in Molina cannot roll back Fidelis.
    """
    result = Outcome(key=source.key, feed_id=source.feed_id, files=len(source.files))
    started = time.monotonic()
    missing = source.missing()
    if missing:
        result.failures.append(f"missing: {', '.join(p.name for p in missing)}")
        return result

    full_spine = source.loads_to_silver
    result.stopped_at = "silver_raw" if full_spine else "bronze"
    print(f"\n  {source.label}")
    print(
        f"      feed={source.feed_id}  grain={source.grain.value}  "
        f"format={source.file_format}  -> {result.stopped_at}"
    )
    print(
        f"      expect ~{source.measured_rows:,} rows "
        f"({source.measured_distinct:,} distinct {source.key_column!r})"
    )
    if source.notes:
        print(f"      note: {source.notes}")
    if dry_run:
        result.seconds = time.monotonic() - started
        return result

    with open_plane(profile, atomic=True) as plane:
        for path in source.paths:
            landed = deliver(plane, source, path, business_date)
            if landed is None:
                result.refused += 1
                continue
            result.delivered += 1
            file_ref = landed.file
            if full_spine:
                batch, bronze, silver, quarantined, failure = run_full_spine(
                    plane, source, file_ref, business_date
                )
                if batch:
                    result.batches.append(batch)
                result.bronze_rows += bronze
                result.silver_rows += silver
                result.quarantined += quarantined
                if failure:
                    result.failures.append(f"{path.name}: {failure}")
                print(
                    f"      batch={batch}  bronze={bronze:,}  silver={silver:,}  "
                    f"quarantined={quarantined:,}" + (f"  FAILED: {failure}" if failure else "")
                )
            else:
                batch, bronze, failure = run_bronze_only(plane, source, file_ref, business_date)
                result.batches.append(batch)
                result.bronze_rows += bronze
                if failure:
                    result.failures.append(f"{path.name}: {failure}")
                print(f"      batch={batch}  bronze={bronze:,}  silver=— (grain)")
    result.seconds = time.monotonic() - started
    return result


# ── metadata: the feeds as GOVERNED OBJECTS ──────────────────────────────────
#: Who the population runs as. A real actor, because every governed object
#: records its author and `registry.governed_object` is an audit surface — a
#: population authored by nobody would be indistinguishable from a hand-edit.
POPULATOR = "population-driver@cinqcare.test"


def register_metadata(profile: str, chosen: tuple[src.Source, ...]) -> None:
    """Write each feed, contract and rule set into `registry.governed_object`.

    WHY THIS IS A SEPARATE PHASE from the load. The pipeline runner takes its
    feed and contract as arguments and never reads the registry — so a load
    succeeds whether or not the metadata was ever stored. That is convenient
    and it is also how a plane ends up holding 569,000 rows attributed to feeds
    that officially do not exist. Registering them makes the population
    reviewable in the UI and in the audit ledger, which is the difference
    between a populated database and an operable platform.

    DRAFT, not APPROVED. A governed object arrives in DRAFT because that is
    what `as_governed` produces and what the lifecycle requires; promoting it
    is an approval, and a populator that self-approved its own metadata would
    be forging the one signature the registry exists to record.
    """
    from cinqflow.core.model.governed import Actor, ActorType
    from cinqflow.core.registry.contract import contract_as_governed, rules_as_governed
    from cinqflow.ports.control_tables import FeedSlaConfig

    author = Actor(subject=POPULATOR, actor_type=ActorType.HUMAN, display_name="Population driver")
    written = {"feed": 0, "contract": 0, "rules": 0, "sla": 0}

    with open_plane(profile, atomic=True) as plane:
        for source in chosen:
            feed = feed_record(source)
            contract = schema_contract(source)
            rules = dq_rules(source)

            plane.metadata.save(feed.as_governed(author=author))
            written["feed"] += 1
            plane.metadata.save(contract_as_governed(contract, author=author))
            written["contract"] += 1
            if rules:
                plane.metadata.save(rules_as_governed(source.feed_id, rules, author=author))
                written["rules"] += 1

            # The delivery contract the SLA screens judge arrivals against.
            # `control.feed_sla_config` was empty, so every freshness and
            # lateness figure on those screens had nothing to compare to.
            plane.control.upsert_feed_sla_config(
                FeedSlaConfig(
                    feed_id=source.feed_id,
                    feed_version=1,
                    domain=source.domain,
                    source_system=source.source_system,
                    file_format=source.file_format,
                    landing_path=source.landing_path,
                    file_pattern=source.file_pattern,
                    schedule_cron=source.schedule_cron,
                    created_ts=datetime.now(UTC),
                    expected_file_count=len(source.files),
                    min_size_bytes=100,
                    max_size_bytes=200_000_000,
                    # Six hours. Stated per feed rather than defaulted globally:
                    # a monthly roster and a daily census are owed different
                    # patience, and one number for both makes the alert useless.
                    grace_period_minutes=360,
                )
            )
            written["sla"] += 1
            print(f"      registered {source.feed_id}")

    print(
        f"\n  governed objects: {written['feed']} feeds, {written['contract']} contracts, "
        f"{written['rules']} rule sets  ·  sla configs: {written['sla']}"
    )


def report(profile: str) -> None:
    """Every table and its count, plus the layer census. The populator's receipt."""
    from cinqflow.core.layers import spine

    with open_plane(profile) as plane:
        print(f"\n  plane: {plane.dsn_label}    landing: {plane.landing_root}\n")
        counts = table_counts(plane)
        width = max(len(name) for name in counts)
        filled = {k: v for k, v in counts.items() if v}
        print(f"  {'table':{width}}  rows")
        print(f"  {'-' * width}  ------")
        for name, count in counts.items():
            mark = " " if count else "·"
            print(f"{mark} {name:{width}}  {count:>9,}")
        print(f"\n  {len(filled)} of {len(counts)} tables populated")

        if plane.reader:
            print("\n  medallion census")
            for spec in spine():
                census = plane.reader.census(spec)
                rows = census.row_count
                print(
                    f"    {spec.label:12} {spec.status.value:18} "
                    f"{'—' if rows is None else format(rows, ',')}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="profiles/local.yaml")
    parser.add_argument("--source", action="append", help="Source key; repeatable.")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--business-date", default=DEFAULT_BUSINESS_DATE)
    parser.add_argument("--dry-run", action="store_true", help="Plan only; write nothing.")
    parser.add_argument("--report", action="store_true", help="Counts and census, then exit.")
    parser.add_argument("--list", action="store_true", help="List the sources and exit.")
    parser.add_argument(
        "--metadata",
        action="store_true",
        help="Register feeds/contracts/rules/SLA as governed objects, then exit.",
    )
    parser.add_argument("--json", default="", help="Write the outcome summary to this path.")
    args = parser.parse_args()

    if args.list:
        for source in src.SOURCES:
            print(
                f"  {source.key:22} {source.grain.value:8} "
                f"{source.measured_rows:>9,} rows  {source.label}"
            )
        print(
            f"\n  {len(src.SOURCES)} sources  {src.TOTAL_ROWS:,} rows  "
            f"silver ceiling {src.SILVER_CEILING:,}"
        )
        print(f"  ADT held back: {src.ADT_ROWS} rows — no bronze.adt_events table exists")
        return 0

    if args.report:
        report(args.profile)
        return 0

    chosen = src.SOURCES if args.all else tuple(src.by_key(key) for key in (args.source or []))
    if not chosen:
        parser.error("pass --all, or --source KEY (repeatable), or --list")

    if args.metadata:
        print(f"cinqflow populate --metadata  profile={args.profile}")
        register_metadata(args.profile, chosen)
        return 0

    print(f"cinqflow populate  profile={args.profile}  business_date={args.business_date}")
    print(f"  {len(chosen)} source(s), {sum(s.measured_rows for s in chosen):,} rows expected")

    outcomes = [
        populate_source(
            source,
            profile=args.profile,
            business_date=args.business_date,
            dry_run=args.dry_run,
        )
        for source in chosen
    ]

    print(f"\n{'source':22} {'bronze':>10} {'silver':>10} {'quar':>7} {'sec':>7}  notes")
    print(f"{'-' * 22} {'-' * 10} {'-' * 10} {'-' * 7} {'-' * 7}  -----")
    for out in outcomes:
        note = "; ".join(out.failures) or (
            "bronze only (grain)" if out.stopped_at == "bronze" else ""
        )
        print(
            f"{out.key:22} {out.bronze_rows:>10,} {out.silver_rows:>10,} "
            f"{out.quarantined:>7,} {out.seconds:>7.1f}  {note}"
        )
    print(
        f"{'TOTAL':22} {sum(o.bronze_rows for o in outcomes):>10,} "
        f"{sum(o.silver_rows for o in outcomes):>10,} "
        f"{sum(o.quarantined for o in outcomes):>7,} "
        f"{sum(o.seconds for o in outcomes):>7.1f}"
    )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [
                    {
                        "key": o.key,
                        "feed_id": o.feed_id,
                        "bronze_rows": o.bronze_rows,
                        "silver_rows": o.silver_rows,
                        "quarantined": o.quarantined,
                        "batches": o.batches,
                        "stopped_at": o.stopped_at,
                        "failures": o.failures,
                        "seconds": round(o.seconds, 2),
                    }
                    for o in outcomes
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nsummary -> {args.json}")

    return 1 if any(o.failures for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
