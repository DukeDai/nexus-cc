"""nexus memory <command> — interact with the memory layer."""

from __future__ import annotations

from pathlib import Path

import click


@click.group(invoke_without_command=True)
@click.pass_context
def memory(ctx: click.Context) -> None:
    """Inspect and manage the memory layer."""
    pass


@memory.command("warm")
@click.option("--workdir", "-w", type=click.Path(Path), default=".")
def warm_command(workdir: str) -> None:
    """Rebuild memory indexes from current WAL."""
    from src.context.wal import WALManager
    from src.context.memory import MemoryStore

    workdir_path = Path(workdir)
    wal_path = workdir_path / ".nexus" / "wal.jsonl"
    if not wal_path.exists():
        click.echo(f"No WAL at {wal_path}; nothing to warm.")
        raise SystemExit(1)
    wal = WALManager(path=wal_path)
    store = MemoryStore(wal=wal, project_root=workdir_path)
    store.warm()
    click.echo(f"Memory warmed: {len(store.episodic()._entries)} episodic entries.")


@memory.command("stats")
@click.option("--workdir", "-w", type=click.Path(Path), default=".")
def stats_command(workdir: str) -> None:
    """Show memory index stats."""
    from src.context.wal import WALManager
    from src.context.memory import MemoryStore

    workdir_path = Path(workdir)
    wal_path = workdir_path / ".nexus" / "wal.jsonl"
    wal = WALManager(path=wal_path) if wal_path.exists() else None
    if wal is None:
        click.echo("No WAL; memory is empty.")
        return
    store = MemoryStore(wal=wal, project_root=workdir_path)
    store.warm()
    epi = store.episodic()
    sem = store.semantic()
    click.echo(f"Episodic: {len(epi._entries)} plans indexed")
    click.echo(f"Semantic: {len(sem._chunks)} chunks indexed")


@memory.command("search")
@click.argument("query")
@click.option("--workdir", "-w", type=click.Path(Path), default=".")
@click.option("--k", default=5, type=int)
def search_command(query: str, workdir: str, k: int) -> None:
    """Semantic search across indexed chunks."""
    from src.context.wal import WALManager
    from src.context.memory import MemoryStore

    workdir_path = Path(workdir)
    wal_path = workdir_path / ".nexus" / "wal.jsonl"
    wal = WALManager(path=wal_path) if wal_path.exists() else None
    store = MemoryStore(wal=wal or WALManager(path=workdir_path / ".nexus" / "wal.jsonl"), project_root=workdir_path)
    sem = store.semantic()
    results = sem.search(query, k=k)
    for r in results:
        click.echo(f"{r.path}:{r.start_line}-{r.end_line}: {r.content[:80].strip()}")
