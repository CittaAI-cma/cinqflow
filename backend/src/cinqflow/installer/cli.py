"""`cinqflow` — provision, inspect, simulate, ask, and take it all away again.

    "one command still stands up a complete fresh environment"
    — memory/03-directives/01-definition-of-done.md, per-wave exit

The CLI is the demo, and the demo is the test run. Every command here appears
in the Wave-0 exit script, and the twin-e2e CI job asserts their output — so
the demo cannot silently rot between showings.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table as RichTable

from cinqflow.core.schema_spec import all_schemas
from cinqflow.installer import profile as profile_module
from cinqflow.installer.manifest import InstallationManifest
from cinqflow.installer.prompts import PromptSeedReport

app = typer.Typer(
    name="cinqflow",
    help="CINQFLOW — a metadata-driven healthcare data platform with a governed AI layer.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DEFAULT_MANIFEST = Path(".cinqflow/installation-manifest.json")

#: Every identifier this installer ever writes to a manifest is a bare name or
#: `schema.table` — nothing this codebase creates is ever quoted, dotted twice,
#: or contains a space. The manifest is a plain user-writable JSON file, so a
#: tampered identifier must be REFUSED here, before it reaches a DROP
#: statement — never trusted because "we wrote it, once".
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


class UnsafeManifestIdentifierError(ValueError):
    """A manifest entry that does not look like anything this installer made."""


ProfileOption = Annotated[
    Path,
    typer.Option(
        "--profile",
        "-p",
        help="The connection profile. ALL environment "
        "difference lives here — climbing a rung changes only this file.",
    ),
]


@app.command()
def install(
    profile: ProfileOption = Path("profiles/local.yaml"),
    manifest_path: Annotated[Path, typer.Option("--manifest")] = DEFAULT_MANIFEST,
    approved_by: Annotated[
        str,
        typer.Option(
            "--approved-by",
            help="The named person accepting this build's prompt registry onto the plane.",
        ),
    ] = "dev-platform@cinqcare.test",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Render the DDL; write nothing.")
    ] = False,
) -> None:
    """Provision a complete plane from nothing. Idempotent.

    CF-V0-E16-02: this now also publishes the PROMPT REGISTRY. Schemas alone
    produce a plane that provisions cleanly and then 500s on the first model
    call any agent makes — `ObjectNotFoundError: prompt:pipeline-insight
    .route` — because `LlmGateway.complete()` resolves its template from
    `governance.object` and nothing had ever written those rows outside the
    in-memory mock. A plane the installer declares complete must not be a
    plane whose intelligence cannot start.
    """
    loaded = profile_module.load(profile)
    console.print(
        f"[bold]cinqflow install[/bold]  profile={loaded.name}  "
        f"rung={loaded.rung}  socket={loaded.socket}  mode={loaded.mode.value}"
    )

    from cinqflow.adapters.local.pg_ddl import PostgresDdlRenderer

    renderer = PostgresDdlRenderer()
    manifest = InstallationManifest(profile=loaded.source, rung=loaded.rung, socket=loaded.socket)

    statements: list[str] = ["CREATE EXTENSION IF NOT EXISTS vector;"]
    manifest.record("extension", "vector")  # provisioned in W0, populated in W1

    for schema in all_schemas():
        manifest.spec_fingerprints[schema.name] = schema.fingerprint
        rendered = renderer.render_schema(schema)
        statements.extend(rendered)
        manifest.record("schema", schema.name)
        for spec_table in schema.tables:
            manifest.record("table", f"{schema.name}.{spec_table.name}")
            for index_columns in spec_table.indexes:
                manifest.record("index", f"ix_{spec_table.name}_{'_'.join(index_columns)}")

    # The knowledge plane's ENGINE-SPECIFIC halves. pgvector's vector column and
    # the generated tsvector are dialect, so they live in this rendering — never
    # in the portable spec, which the Databricks renderer also consumes
    # (docs/architecture/plates/12-knowledge-plane-and-retrieval.md).
    statements.extend(
        [
            "ALTER TABLE knowledge.chunk ADD COLUMN IF NOT EXISTS embedding_vec vector;",
            "ALTER TABLE knowledge.chunk ADD COLUMN IF NOT EXISTS tsv tsvector "
            "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;",
            "CREATE INDEX IF NOT EXISTS ix_chunk_tsv ON knowledge.chunk USING GIN (tsv);",
        ]
    )
    manifest.record("index", "ix_chunk_tsv")

    if dry_run:
        console.print("\n".join(statements))
        raise typer.Exit(0)

    _execute(loaded, statements)

    # CF-V0-E16-02. AFTER the DDL, because the rows need `governance.object`
    # to exist; inside the same command, because an install that leaves the
    # prompt registry empty has provisioned a platform that cannot answer.
    prompt_report = _publish_prompt_registry(loaded, approved_by)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.write(manifest_path)

    summary = RichTable(title="provisioned", show_edge=False)
    summary.add_column("kind")
    summary.add_column("count", justify="right")
    for kind in ("extension", "schema", "table", "index"):
        summary.add_row(kind, str(sum(1 for o in manifest.objects if o.kind == kind)))
    console.print(summary)
    console.print(prompt_report.explain())
    console.print(f"manifest → [bold]{manifest_path}[/bold]")


def _publish_prompt_registry(loaded: profile_module.Profile, approved_by: str) -> PromptSeedReport:
    """CF-V0-E16-02 — the prompt rows, through the real lifecycle.

    Extracted from `install` rather than inlined so `seed-prompts` and
    `install` share one path: two call sites that each published prompts
    their own way is how the mock plane came to seed seven templates while
    `rule-authoring.author` was missing from the list entirely.
    """
    from cinqflow.adapters.local.pg_control import commit
    from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
    from cinqflow.core.model.identity import Principal, Role, Scopes
    from cinqflow.installer.prompts import seed_prompts as publish

    actor = Principal(
        subject=approved_by,
        display_name=approved_by.split("@")[0],
        roles=frozenset({Role.PLATFORM_ENGINEER}),
        scopes=Scopes(domains=frozenset({"*"}), feeds=frozenset({"*"})),
    ).as_actor()
    with commit(loaded) as connection:
        return publish(PostgresMetadataDb(connection), approver=actor)


@app.command()
def uninstall(
    profile: ProfileOption = Path("profiles/local.yaml"),
    manifest_path: Annotated[Path, typer.Option("--manifest")] = DEFAULT_MANIFEST,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
) -> None:
    """Remove EXACTLY what the installer created — never a cascade drop.

    In the client's tenant, CINQFLOW is additive to an existing estate
    (ADR-0013). A drop that guessed would be a production incident, so the
    manifest written at install time is the uninstall plan.
    """
    if not manifest_path.exists():
        console.print(f"[red]no manifest at {manifest_path}[/red] — nothing to remove safely.")
        raise typer.Exit(1)

    loaded = profile_module.load(profile)
    manifest = InstallationManifest.read(manifest_path)
    objects = manifest.uninstall_order()

    console.print(f"[bold]cinqflow uninstall[/bold]  {len(objects)} objects from {manifest.socket}")
    if not yes and not typer.confirm("remove them?"):
        raise typer.Exit(1)

    droppable = [obj for obj in objects if obj.kind in {"schema", "table", "index"}]
    unsafe = [obj for obj in droppable if not _SAFE_IDENTIFIER.match(obj.identifier)]
    if unsafe:
        names = ", ".join(obj.identifier for obj in unsafe)
        raise UnsafeManifestIdentifierError(
            f"{manifest_path} names an identifier this installer would never have written: "
            f"{names!r} — refusing rather than interpolating it into a DROP statement"
        )

    statements = [
        f"DROP {obj.kind.upper()} IF EXISTS {obj.identifier} CASCADE;" for obj in droppable
    ]
    _execute(loaded, statements)
    manifest_path.unlink()
    console.print("[green]removed[/green] — and nothing that was not ours.")


@app.command()
def doctor(profile: ProfileOption = Path("profiles/local.yaml")) -> None:
    """Report what this profile fits to each pin, and what is not yet energized."""
    from cinqflow.ports import PIN_GROUPS, PORTS

    loaded = profile_module.load(profile)
    report = RichTable(title=f"{loaded.name} · rung {loaded.rung} · {loaded.mode.value}")
    report.add_column("group")
    report.add_column("pin")
    report.add_column("adapter")
    report.add_column("verb", style="dim")
    for group, pins in PIN_GROUPS.items():
        for pin in pins:
            report.add_row(group, pin, loaded.adapter_for(pin), PORTS[pin].verb)
    console.print(report)


@app.command()
def conformance(profile: ProfileOption | None = None) -> None:
    """Certify this socket — one check per energized pin, each naming its pin.

    Fitting a new adapter is a CERTIFICATION, not a migration. This is the
    command that makes that true: run it, read the pin names.
    """
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "conformance"))
    from conformance.kit import main as kit_main

    raise SystemExit(kit_main(["--profile", str(profile)] if profile else []))


@app.command()
def seed_glossary(
    workbook: Annotated[
        Path,
        typer.Option("--workbook", help="The client's `Data lake data model.xlsx`."),
    ] = Path("../clientdata/Uploads/2-Design/Data lake data model.xlsx"),
    profile: ProfileOption = Path("profiles/local.yaml"),
    author: Annotated[
        str, typer.Option("--as", help="Who is loading them. Seeded terms arrive as DRAFTS.")
    ] = "dev-ba@cinqcare.test",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report; write nothing.")] = False,
) -> None:
    """CF-V1-E14-01 — load the client's real 171-term glossary into the registry.

    Terms arrive as DRAFTS, not Published: the steward who will own them
    approves them, like every other governed object. Seeding straight to
    Published would hand the platform 171 definitions nobody signed.

    Idempotent — a term already present is left exactly as it is, because
    re-running a seeder must never overwrite a steward's edit.
    """
    from cinqflow.adapters.local.pg_control import commit
    from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
    from cinqflow.adapters.local.workbook_glossary import load_glossary
    from cinqflow.core.model.governed import ObjectType
    from cinqflow.core.model.identity import Principal, Role, Scopes
    from cinqflow.ports.metadata_db import ObjectNotFoundError

    loaded = profile_module.load(profile)
    glossary = load_glossary(workbook)
    console.print(
        f"[bold]cinqflow seed-glossary[/bold]  {workbook.name}  "
        f"{len(glossary.terms)} terms · [bold]{len(glossary.phi_terms)} PHI-flagged[/bold] · "
        f"{len(glossary.phi_columns())} PHI column names"
    )
    if dry_run:
        raise typer.Exit(0)

    actor = Principal(
        subject=author,
        display_name=author.split("@")[0],
        roles=frozenset({Role.BUSINESS_ANALYST}),
        scopes=Scopes(domains=frozenset({"*"}), feeds=frozenset({"*"})),
    ).as_actor()

    written = skipped = 0
    with commit(loaded) as connection:
        store = PostgresMetadataDb(connection)
        for term in glossary.terms:
            try:
                store.get(ObjectType.GLOSSARY_TERM, term.glossary_id)
            except ObjectNotFoundError:
                store.save(term.as_governed(author=actor))
                written += 1
            else:
                # Never overwrite: a steward's amendment outranks a re-run.
                skipped += 1

    summary = RichTable(title="seeded", show_edge=False)
    summary.add_column("outcome")
    summary.add_column("terms", justify="right")
    summary.add_row("written as Draft", str(written))
    summary.add_row("already present", str(skipped))
    console.print(summary)
    console.print(
        "[dim]Drafts. A steward approves them — the platform does not "
        "publish 171 definitions nobody signed.[/dim]"
    )


@app.command()
def seed_prompts(
    profile: ProfileOption = Path("profiles/local.yaml"),
    approved_by: Annotated[
        str,
        typer.Option(
            "--approved-by",
            help="The NAMED PERSON accepting this build's prompts onto this plane.",
        ),
    ] = "dev-platform@cinqcare.test",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report; write nothing.")] = False,
) -> None:
    """CF-V0-E16-02 — publish this build's prompt registry onto the plane.

    THE INSTALL STEP WHOSE ABSENCE MADE EVERY AGENT 500 ON A REAL PLANE.
    `LlmGateway.complete()` resolves its template from the metadata store, so
    a prompt is a governed, versioned, reviewable object rather than a string
    in a function — which is right, and which is why nothing works until the
    rows exist. `intelligence.demo.seed()` wrote them into the in-memory store
    the mock socket uses, so CI and the rung-0 demo were always fine; a freshly
    installed Postgres plane answered `POST /api/ask` with
    `ObjectNotFoundError: prompt:pipeline-insight.route`.

    Idempotent on (id, VERSION), not on id: re-running is safe, and a template
    carrying a NEW version publishes beside the old one rather than silently
    leaving last quarter's prompt in place.

    Unlike `seed-glossary`, these arrive PUBLISHED, and the difference is
    structural: a glossary term nobody approved is a definition nobody uses,
    while a prompt nobody approved is a platform that does not run. The
    approver is a named person for exactly that reason.
    """
    from cinqflow.adapters.local.pg_control import commit
    from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
    from cinqflow.core.agents import ALL_TEMPLATES
    from cinqflow.core.model.identity import Principal, Role, Scopes
    from cinqflow.installer.prompts import seed_prompts as publish

    loaded = profile_module.load(profile)
    console.print(
        f"[bold]cinqflow seed-prompts[/bold]  {len(ALL_TEMPLATES)} templates  "
        f"approved by [bold]{approved_by}[/bold]"
    )
    if dry_run:
        for template in ALL_TEMPLATES:
            console.print(f"  {template.reference}  [dim]{template.task_class.value}[/dim]")
        raise typer.Exit(0)

    actor = Principal(
        subject=approved_by,
        display_name=approved_by.split("@")[0],
        roles=frozenset({Role.PLATFORM_ENGINEER}),
        scopes=Scopes(domains=frozenset({"*"}), feeds=frozenset({"*"})),
    ).as_actor()

    with commit(loaded) as connection:
        report = publish(PostgresMetadataDb(connection), approver=actor)
    console.print(report.explain())


@app.command()
def ask(
    question: str,
    as_user: str = "dev-analyst@cinqcare.test",
) -> None:
    """Ask the Pipeline Insight Agent, from a terminal.

    Runs on the mock socket, so it needs no database and no credential. Every
    claim prints with its citation — and a refusal prints as a refusal, because
    "I will not do that" is an answer.
    """
    from rich.markup import escape

    from cinqflow.adapters.mock.authn import StaticAuthn
    from cinqflow.intelligence.demo import agent_for, plane

    store, control = plane()
    agent = agent_for(StaticAuthn().verify(as_user), control, store)
    answer = agent.ask(question, run_id="cli")

    if answer.refused:
        console.print(f"[yellow]REFUSED[/yellow] {escape(answer.refusal)}")
        raise SystemExit(0)
    for claim in answer.claims:
        # Escaped: a citation contains square brackets and colons, and rich
        # would eat `[batch:8842]` as markup — printing an answer with its
        # evidence silently removed, which is the one thing this must not do.
        cited = escape(" ".join(f"[{c}]" for c in claim.citations))
        console.print(f"{escape(claim.text)} [cyan]{cited}[/cyan]")
    for missing in answer.unanswered:
        console.print(f"[dim]unanswered: {escape(missing)}[/dim]")
    console.print(
        f"[dim]tools: {', '.join(answer.tools_called) or 'none'} · "
        f"confidence {answer.confidence} · ${answer.cost_usd}[/dim]"
    )


@app.command()
def tick(
    profile: ProfileOption = Path("profiles/local.yaml"),
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="ISO timestamp. Defaults to now — override to replay a tick."),
    ] = None,
) -> None:
    """CF-V1-E8-03 — run the scheduler once: what is due, what may start.

    THE COMMAND `pg_orchestration.due()` WAS WRITTEN FOR. Its docstring has
    named the missing caller since the pin was fitted — "the worker turns each
    due run into `queue.enqueue(...)`" — and until `workers.scheduler` there
    was nothing on the receiving end: `due()` had two references in the whole
    repository, both tests. A schedule nothing ticks is a cron expression in
    a database column.

    Idempotent by design rather than by care. The queue dedupes on
    `feed/business_date`, so running this twice in one minute enqueues once;
    run it from cron, from a terminal, or from a test, and the answer is the
    same.

    It RELEASES; it does not run. What starts a released run is the consumer
    on `pipeline.run_feed` — separate on purpose, so a tick that fires while
    the plane is busy costs one queue insert rather than blocking.
    """
    from datetime import datetime

    from cinqflow.adapters.local.pg_control import connect
    from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
    from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
    from cinqflow.adapters.local.pg_orchestration import PostgresOrchestration
    from cinqflow.adapters.local.pg_queue import PostgresQueue
    from cinqflow.workers.scheduler import SchedulerWorker

    loaded = profile_module.load(str(profile))
    stamp = datetime.fromisoformat(as_of) if as_of else None
    with connect(loaded, autocommit=True) as connection:
        report = SchedulerWorker(
            orchestration=PostgresOrchestration(connection),
            metadata=PostgresMetadataDb(connection),
            control=PostgresControlTables(connection),
            queue=PostgresQueue(connection),
            # No notification pin wired here, deliberately. A CLI tick prints
            # its holds to the operator standing in front of it; paging a
            # channel from an interactive command would send an alert nobody
            # asked for every time somebody checked the schedule.
            notify=None,
        ).tick(stamp)

    console.print(report.explain())
    for hold in report.held:
        console.print(f"[yellow]{hold.explanation}[/yellow]")


@app.command()
def work(
    profile: ProfileOption = Path("profiles/local.yaml"),
    once: Annotated[
        bool, typer.Option("--once", help="Process one message and stop, instead of draining.")
    ] = False,
) -> None:
    """CF-V1-E8-03 — run what the scheduler queued. The other end of `tick`.

    THE CHAIN, COMPLETE. `cinqflow tick` reads `orchestration.due()` and
    enqueues `pipeline.run_feed`; this command claims those messages and runs
    the spine for whichever feed they name. Before `workers.run_feed` existed
    the topic had a producer and no consumer, and the only thing that could
    run a pipeline at all was `cinqflow ingest` — hardcoded to the Wave-0
    anchor feed by four module constants, so a second payer could not be run
    without editing Python.

    GENERIC BY CONSTRUCTION. Nothing here names a feed. The handler reads the
    PUBLISHED contract, rules and mapping for whatever feed the message
    carries, compiles the plan and runs every file waiting in that feed's own
    incoming folder. Onboarding a new payer is a registry row.

    SAFE TO RUN TWICE. Landing's fingerprint check skips a file it has already
    processed, so a redelivered message after a crash re-reads the folder and
    loads nothing twice — the guarantee lives in CF-V0-E8-02, not here.
    """
    from cinqflow.adapters.local.localfs_storage import LocalFsStorage
    from cinqflow.adapters.local.pg_compute import PostgresCompute
    from cinqflow.adapters.local.pg_control import connect
    from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
    from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
    from cinqflow.adapters.local.pg_queue import PostgresQueue
    from cinqflow.workers.consumer import Consumer
    from cinqflow.workers.pipeline import PipelineRunner
    from cinqflow.workers.run_feed import FeedRunWorker
    from cinqflow.workers.scheduler import RUN_FEED_TOPIC

    loaded = profile_module.load(str(profile))
    # The landing zone the PROFILE names — the same line `ingest` reads, so
    # a file delivered by one command is visible to the other.
    storage = LocalFsStorage(
        root=str(loaded.pins.get("storage", {}).get("root") or ".cinqflow/landing")
    )
    with connect(loaded, autocommit=True) as connection:
        metadata_db = PostgresMetadataDb(connection)
        control_tables = PostgresControlTables(connection)
        worker = FeedRunWorker(
            metadata=metadata_db,
            storage=storage,
            runner=PipelineRunner(
                storage=storage,
                control=control_tables,
                compute=PostgresCompute(connection),
            ),
        )
        consumer = Consumer(PostgresQueue(connection))
        consumer.register(RUN_FEED_TOPIC, worker.handle)
        processed = 1 if consumer.run_once(RUN_FEED_TOPIC) else 0
        if not once:
            processed += consumer.drain_topic(RUN_FEED_TOPIC)

    if processed == 0:
        console.print("[dim]nothing queued.[/dim]")
    else:
        console.print(f"ran [bold]{processed}[/bold] queued feed run(s).")


@app.command()
def ingest(
    profile: ProfileOption = Path("profiles/local.yaml"),
    business_date: Annotated[
        str, typer.Option("--business-date", help="YYYY-MM-DD. Selects the roster's month.")
    ] = "2026-08-01",
    bad_dates: Annotated[
        int, typer.Option("--bad-dates", help="Rows to seed with an out-of-range DOB.")
    ] = 0,
    resume_from: Annotated[
        str | None,
        typer.Option(
            "--resume-from", help="'silver_raw' — resume a batch without re-landing Bronze."
        ),
    ] = None,
    batch_id: Annotated[
        str | None, typer.Option("--batch-id", help="Required with --resume-from.")
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            help="A file of YOUR OWN to land, instead of the generated roster.",
        ),
    ] = None,
) -> None:
    """Land a file for real — committed to the real Postgres plane.

    WITH `--file`, THIS IS THE ANSWER TO "can I put my own file in and watch it
    work". The bytes are handed to the `connector` pin, which lands them under
    the feed's layout in the REAL landing zone the profile names — so
    `.cinqflow/landing/...` becomes a directory you can look at, and the same
    landing controls, fingerprint check and drop ledger run over your file as
    over the generated one. Deliver the same file twice and the second is
    SKIPPED by fingerprint, which is the replay refusal demonstrating itself.

    Without it, the roster is generated by the simulator, as before.

    This is the Wave-0 exit criterion as a command, not only as a test: drop
    the roster and watch it flow with 5 quarantined rows and a balanced
    ledger; drop the SAME month again and watch it refused (the fingerprint is
    already in `control.input_registry`, so this needs no flag to demonstrate
    — running this command twice IS the negative test); kill it at Silver Raw
    with `--resume-from silver_raw --batch-id <id>` and watch it restart
    without reloading Bronze.

    Unlike `ask`, this runs on the REAL rung-0.5 socket: PostgresControlTables
    and PostgresCompute, inside `pg_control.commit` — one transaction, visible
    downstream in full or not at all. What it commits STAYS — this is the
    plane the pipeline test suite also runs against (inside a transaction it
    always rolls back), so a batch left here with the same content as the
    golden roster fixture (the default `--bad-dates 0` roster) will collide on
    fingerprint with that suite's own runs. Use a scratch database for
    exploration, or truncate `control.*`, `bronze.members_raw` and
    `silver_raw.members` before running `pytest` again.
    """
    from datetime import date

    from cinqflow.adapters.local.localfs_storage import LocalFsStorage
    from cinqflow.adapters.local.pg_compute import PostgresCompute
    from cinqflow.adapters.local.pg_control import commit
    from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
    from cinqflow.adapters.local.upload_connector import UploadConnector
    from cinqflow.core.delivery import DeliveryError, Manifest
    from cinqflow.core.model.vocabulary import Layer
    from cinqflow.core.registry.golden_fidelis import (
        CONTRACT,
        DQ_002,
        FEED,
        FEED_VERSION,
        PLAN,
        roster_csv,
    )
    from cinqflow.core.registry.golden_fidelis import landing_key as _key
    from cinqflow.ports.connector import AlreadyDeliveredError
    from cinqflow.workers.pipeline import PipelineRunner

    loaded = profile_module.load(profile)
    stage = Layer(resume_from) if resume_from else None
    if resume_from and not batch_id:
        console.print("[red]--resume-from requires --batch-id[/red] — there is no batch to resume.")
        raise typer.Exit(1)

    date.fromisoformat(business_date)  # a clear error beats a confusing one downstream

    # THE LANDING ZONE THE PROFILE NAMES, not a temp directory and not memory.
    # `storage: { adapter: localfs, root: ".cinqflow/landing" }` was declared
    # from the beginning and read by nothing — this command used MemFsStorage,
    # so no landing zone had ever existed on disk.
    root = str(loaded.pins.get("storage", {}).get("root") or ".cinqflow/landing")
    storage = LocalFsStorage(root=root)
    connector = UploadConnector(storage)

    if file is not None:
        content = file.read_bytes()
        filename = file.name
    else:
        content = roster_csv(bad_dates=bad_dates)
        filename = _key(business_date).rsplit("/", 1)[-1]

    try:
        delivery = connector.deliver(
            content,
            filename=filename,
            feed_id=FEED.feed_id,
            landing_path=FEED.landing_path,
            business_date=business_date,
            manifest=Manifest(),
        )
    except (AlreadyDeliveredError, DeliveryError) as refused:
        console.print(f"[yellow]not delivered:[/yellow] {refused}")
        raise typer.Exit(1) from None
    key = delivery.file.key
    landed = delivery.file

    with commit(loaded) as connection:
        from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
        from cinqflow.core import mapping as mapping_core
        from cinqflow.core.model.governed import ObjectType
        from cinqflow.core.registry import canonical
        from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
        from cinqflow.intelligence.demo import (
            fingerprint_match_agent_for,
            mapping_suggestion_agent_for,
        )
        from cinqflow.workers.drift import (
            propose_contract_update,
            propose_mapping_for_unmapped_columns,
            propose_mapping_redirect,
        )
        from cinqflow.workers.incidents import IncidentWorker
        from cinqflow.workers.ops import OpsVerifier

        control_tables = PostgresControlTables(connection)
        metadata_db = PostgresMetadataDb(connection)
        # CF-V2-E12-04: a failed batch opens its incident in the SAME
        # transaction that records the failure — the ledger row and the error
        # rows land or roll back together.
        #
        # W2-38: `fingerprint_match_agent_for` wires a REAL FingerprintMatchAgent
        # onto REAL Postgres control/metadata pins — same pattern `api/local.py`
        # already uses for `agent_for`'s `PipelineInsightAgent` on this rung: a
        # real data plane, the scripted intelligence plane, until wiring an
        # actual LLM endpoint earns its own pass (see `api/local.py`'s own
        # note). A NOVEL incident here drafts a real proposal in `metadata_db`.
        incidents = IncidentWorker(
            control=control_tables,
            metadata=metadata_db,
            fingerprint_agent=fingerprint_match_agent_for(control_tables, metadata_db),
        )
        # W1-33: the SAME scripted-intelligence-plane wiring `fingerprint_
        # match_agent_for` uses just above, for the same reason — a real
        # metadata pin, the scripted stand-in, until the real LLM endpoint
        # gets its own pass. `workers.drift.propose_mapping_for_unmapped_
        # columns` is the only caller.
        mapping_suggestion_agent = mapping_suggestion_agent_for(metadata_db)
        runner = PipelineRunner(
            storage=storage,
            control=control_tables,
            compute=PostgresCompute(connection),
            source_system="fidelis",
            on_batch_failed=incidents.on_batch_failed,
        )
        # CF-V2-E5-04: the published glossary rides along, so a payer's
        # rename is classified by MEANING at G2 rather than failing as a
        # dropped column plus a new one.
        glossary = Glossary(
            terms=tuple(
                GlossaryTerm.from_governed(obj)
                for obj in metadata_db.list(ObjectType.GLOSSARY_TERM)
            )
        )
        # W1-33: built the SAME way `api.app._canonical_of` builds it for the
        # canonical browser — from the spec and the CURRENT glossary, not
        # cached — because `propose_mapping_for_unmapped_columns` needs one to
        # ground a suggestion against.
        canonical_model = canonical.build(canonical.canonical_schemas(), glossary)
        # W1-30: the feed's PUBLISHED FeedMapping, when it has one. PUBLISHED
        # only — `is_executable` is the same gate CF-V1-E6-02's exemplar pool
        # already uses (api/app.py's `_own_published_mapping`), because the
        # engine reads published metadata and nothing else. Nothing publishes
        # a mapping automatically yet, so `feed_mapping` stays `None` for
        # this feed today — and `None` means the MAP step runs exactly as it
        # always has.
        #
        # W1-36: NOT `metadata_db.get(ObjectType.MAPPING, feed_id)` — that
        # returns the highest VERSION NUMBER regardless of lifecycle state,
        # so a DRAFT/PENDING_REVIEW/APPROVED version sitting on top of an
        # already-PUBLISHED one (exactly what starting to edit a mapping, or
        # accepting ANY mapping-suggestion proposal, produces) would shadow
        # the published version and silently fall back to the bare-rename
        # path. Walk the full history and take the highest version that is
        # ACTUALLY executable — the same pattern `_refuse_silent_row_loss`
        # in api/app.py uses to find "the currently PUBLISHED one".
        executable_versions = [
            obj
            for obj in metadata_db.history(ObjectType.MAPPING, FEED.feed_id)
            if obj.is_executable
        ]
        feed_mapping = (
            mapping_core.from_governed(max(executable_versions, key=lambda o: o.version))
            if executable_versions
            else None
        )
        outcome = runner.run(
            landed,
            feed=FEED,
            feed_version=FEED_VERSION,
            contract=CONTRACT,
            rules=(DQ_002,),
            plan=PLAN,
            business_date=business_date,
            resume_from=stage,
            batch_id=batch_id,
            glossary=glossary,
            mapping=feed_mapping,
        )
        if outcome.renames and outcome.batch_id is not None:
            # "Never block on compatible drift — log it and PROPOSE the
            # contract update." The proposal is a draft the steward decides;
            # the contract itself is untouched.
            propose_contract_update(
                metadata_db,
                feed_id=FEED.feed_id,
                contract=CONTRACT,
                renames=outcome.renames,
                run_id=outcome.batch_id,
            )
            # W1-32: the SAME settled renames, asked a second question — does
            # the PUBLISHED mapping still read the old spelling? A rename
            # proposes a contract update AND a mapping redirect independently;
            # neither implies the other, and a feed with no published mapping
            # simply has nothing for this one to redirect.
            if feed_mapping is not None:
                propose_mapping_redirect(
                    metadata_db,
                    feed_id=FEED.feed_id,
                    mapping=feed_mapping,
                    renames=outcome.renames,
                    run_id=outcome.batch_id,
                )
        if outcome.unmapped_columns and outcome.batch_id is not None:
            # W1-33 (F3): the SAME drift classification, asked a third
            # question — a column that is additive AND ungoverned earns its
            # own mapping-suggestion proposal, scoped to just the columns
            # this run found, the moment the finding exists. ADDITIVE AND
            # NON-BLOCKING, like the finding itself: best-effort, the same
            # posture `PipelineRunner._open_incident` takes toward its own
            # agent call, so a broken model call cannot turn an otherwise
            # successful ingest into a failed one.
            try:
                propose_mapping_for_unmapped_columns(
                    mapping_suggestion_agent,
                    feed_id=FEED.feed_id,
                    unmapped_columns=outcome.unmapped_columns,
                    contract_version=CONTRACT.version,
                    glossary=glossary,
                    model=canonical_model,
                    published_mapping=feed_mapping,
                    run_id=outcome.batch_id,
                )
            except Exception as broken:
                console.print(f"[yellow]mapping suggestion not proposed:[/yellow] {broken}")
        # CF-V2-E12-03's second act: the engine just ran, so any REQUESTED
        # action on this batch can now be verified against what the control
        # tables actually say — in the same transaction as the run itself.
        if outcome.batch_id is not None:
            OpsVerifier(control=control_tables, metadata=metadata_db).sweep(
                batch_id=outcome.batch_id
            )

    console.print(f"[bold]cinqflow ingest[/bold]  {key}")
    console.print(f"landing: {outcome.decision.outcome.value}")
    if outcome.batch_id is None:
        console.print(f"[yellow]{outcome.decision.reason or 'not accepted'}[/yellow]")
        raise typer.Exit(0)

    state = outcome.state.value if outcome.state else "?"
    console.print(f"batch: [bold]{outcome.batch_id}[/bold]  state: {state}")
    if outcome.failure:
        console.print(f"[red]{outcome.failure}[/red]")
    if outcome.result is not None:
        recon = outcome.result.reconciliation
        console.print(recon.explain())
        for drop in recon.drops:
            console.print(f"  [dim]· {drop.rule_id}: {drop.record_count} — {drop.reason}[/dim]")
    raise typer.Exit(0 if outcome.processed else 1)


def _execute(loaded: profile_module.Profile, statements: list[str]) -> None:
    """Run DDL through the metadata_db pin.

    Imported here rather than at module scope so that `--dry-run`, `doctor` and
    `--help` work with no database and no driver configured — the installer has
    to be usable before there is anything to install.
    """
    from cinqflow.adapters.local.pg_control import connect

    with connect(loaded) as connection:
        for statement in statements:
            connection.execute(statement)


if __name__ == "__main__":  # pragma: no cover
    app()
