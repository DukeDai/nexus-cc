"""nexus session migrate — convert v1 WAL records to v2 format."""

from __future__ import annotations

import json
from pathlib import Path

import typer

# Parent group that will be added to main CLI as a subcommand.
# Exposed as `migrate_app` so tests can use:
#   runner.invoke(migrate_app, ["migrate", "p1"])
migrate_app = typer.Typer(help="Migrate session WAL files.")


@migrate_app.command("migrate")
def migrate_command(
    plan_id: str,
    workdir: Path = typer.Option(".", "--workdir", "-w", help="Working directory."),
):
    wal_path = workdir / ".nexus" / "wal.jsonl"
    if not wal_path.exists():
        typer.echo(f"No WAL at {wal_path}.")
        raise typer.Exit(1)
    v2_path = wal_path.parent / "wal_v2.jsonl"
    if v2_path.exists():
        typer.echo(f"v2 WAL already exists at {v2_path}; skipping.")
        return

    plan_records: list[dict] = []
    for line in wal_path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("plan_id") == plan_id:
            plan_records.append(rec)

    if not plan_records:
        typer.echo(f"No records for plan {plan_id}.")
        raise typer.Exit(1)

    # Check if already migrated (any record has format_version >= 2).
    if any(rec.get("format_version", 1) >= 2 for rec in plan_records):
        typer.echo(f"Plan {plan_id} is already in v2 format.")
        return

    # Write v2 file with header + upgraded records.
    with v2_path.open("w") as f:
        f.write(json.dumps({
            "format_version": 2,
            "kind": "wal_header",
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "nexus_version": "1.1.0",
        }) + "\n")
        for rec in plan_records:
            rec["format_version"] = 2
            f.write(json.dumps(rec, default=str) + "\n")

    typer.echo(f"Migrated plan {plan_id}: wrote {v2_path}")
