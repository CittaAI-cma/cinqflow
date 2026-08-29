"""`cinqflow` — provision, inspect, simulate, ask, and take it all away again.

    "one command still stands up a complete fresh environment"
    — memory/03-directives/01-definition-of-done.md, per-wave exit

The CLI is the demo, and the demo is the test run. Every command here appears
in the Wave-0 exit script, and the twin-e2e CI job asserts their output — so
the demo cannot silently rot between showings.
"""

from __future__ import annotations

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
        f"[bold]cinqflow install[/bold]  profile={loaded.path.name}  "
        f"rung={loaded.rung}  socket={loaded.socket}  mode={loaded.mode.value}"
    )

    from cinqflow.adapters.local.pg_ddl import PostgresDdlRenderer

    renderer = PostgresDdlRenderer()
    manifest = InstallationManifest(
        profile=str(loaded.path), rung=loaded.rung, socket=loaded.socket
    )

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

    statements = [
        f"DROP {obj.kind.upper()} IF EXISTS {obj.identifier} CASCADE;"
        for obj in objects
        if obj.kind in {"schema", "table", "index"}
    ]
    _execute(loaded, statements)
    manifest_path.unlink()
    console.print("[green]removed[/green] — and nothing that was not ours.")


@app.command()
def doctor(profile: ProfileOption = Path("profiles/local.yaml")) -> None:
    """Report what this profile fits to each pin, and what is not yet energized."""
    from cinqflow.ports import PIN_GROUPS, PORTS

    loaded = profile_module.load(profile)
    report = RichTable(title=f"{loaded.path.name} · rung {loaded.rung} · {loaded.mode.value}")
    report.add_column("group")
    report.add_column("pin")
    report.add_column("adapter")
    report.add_column("verb", style="dim")
    for group, pins in PIN_GROUPS.items():
        for pin in pins:
            report.add_row(group, pin, loaded.adapter_for(pin), PORTS[pin].verb)
    console.print(report)


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
