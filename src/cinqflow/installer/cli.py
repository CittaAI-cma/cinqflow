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
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Render the DDL; write nothing.")
    ] = False,
) -> None:
    """Provision a complete plane from nothing. Idempotent."""
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

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.write(manifest_path)

    summary = RichTable(title="provisioned", show_edge=False)
    summary.add_column("kind")
    summary.add_column("count", justify="right")
    for kind in ("extension", "schema", "table", "index"):
        summary.add_row(kind, str(sum(1 for o in manifest.objects if o.kind == kind)))
    console.print(summary)
    console.print(f"manifest → [bold]{manifest_path}[/bold]")


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
        from cinqflow.workers.incidents import IncidentWorker

        control_tables = PostgresControlTables(connection)
        # CF-V2-E12-04: a failed batch opens its incident in the SAME
        # transaction that records the failure — the ledger row and the error
        # rows land or roll back together.
        incidents = IncidentWorker(control=control_tables, metadata=PostgresMetadataDb(connection))
        runner = PipelineRunner(
            storage=storage,
            control=control_tables,
            compute=PostgresCompute(connection),
            source_system="fidelis",
            on_batch_failed=incidents.on_batch_failed,
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
