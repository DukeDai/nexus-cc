"""`nexus cost` — Cost tracking reports from WAL CostRecords.

Reads `.nexus/wal.jsonl` (or a path supplied via --wal-path) and reports
totals by model / role / hint / session / day. Supports CSV and JSON
exports for downstream tooling.

Subcommands:
    nexus cost                       # default summary (today + total)
    nexus cost today
    nexus cost by-model
    nexus cost by-role
    nexus cost session SESSION_ID
    nexus cost export --format csv|json --output PATH
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterable

import click


# ---------------------------------------------------------------------------
# Record loading
# ---------------------------------------------------------------------------


@dataclass
class _CostEntry:
    """Normalized cost record loaded from WAL.

    `session` is the plan_id under which the record was emitted (best-effort,
    derived by scanning the WAL linearly).
    """

    model: str
    hint: str
    role: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    timestamp: float
    session: str | None = None

    @property
    def ts(self) -> float:
        return self.timestamp


def _load_cost_records(wal_path: Path) -> list[_CostEntry]:
    """Read all cost_record entries from a WAL JSONL file.

    Returns an empty list if the file doesn't exist or no cost records are
    present. Malformed lines are skipped (logged) so a single bad record
    doesn't take down the whole report.
    """
    if not wal_path.exists():
        return []

    records: list[_CostEntry] = []

    # First pass: scan linearly to attach a session (plan_id) to each cost
    # record. We track the most recently seen plan_id by reading step_complete
    # records in chronological order.
    active_plan: str | None = None
    session_by_line: dict[int, str | None] = {}
    raw_by_line: dict[int, dict[str, Any]] = {}

    with wal_path.open() as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = rec.get("kind")
            if kind == "step_complete" or rec.get("tx") == "checkpoint":
                active_plan = rec.get("plan_id") or active_plan
            elif kind == "cost_record":
                session_by_line[idx] = active_plan
                raw_by_line[idx] = rec

    # Second pass: materialize _CostEntry objects.
    for idx, raw in raw_by_line.items():
        try:
            records.append(
                _CostEntry(
                    model=str(raw.get("model", "<unknown>")),
                    hint=str(raw.get("hint", "<unknown>")),
                    role=raw.get("role"),
                    prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(raw.get("completion_tokens", 0) or 0),
                    cost_usd=float(raw.get("cost_usd", 0.0) or 0.0),
                    timestamp=float(raw.get("timestamp", 0.0) or 0.0),
                    session=session_by_line.get(idx),
                )
            )
        except (TypeError, ValueError):
            # Skip records we can't parse cleanly.
            continue

    return records


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate_by(
    records: Iterable[_CostEntry],
    key_fn,
) -> dict[str, dict[str, float]]:
    """Aggregate CostRecords into {key: {metric: total}}.

    Metrics: prompt_tokens, completion_tokens, cost_usd, count.
    """
    out: dict[str, dict[str, float]] = {}
    for r in records:
        key = key_fn(r)
        bucket = out.setdefault(
            key,
            {
                "prompt_tokens": 0.0,
                "completion_tokens": 0.0,
                "cost_usd": 0.0,
                "count": 0.0,
            },
        )
        bucket["prompt_tokens"] += r.prompt_tokens
        bucket["completion_tokens"] += r.completion_tokens
        bucket["cost_usd"] += r.cost_usd
        bucket["count"] += 1
    return out


def _today_records(records: list[_CostEntry], *, now: datetime | None = None) -> list[_CostEntry]:
    """Filter records to those emitted since midnight today (local time)."""
    now = now or datetime.now()
    midnight = datetime.combine(now.date(), time.min)
    midnight_ts = midnight.timestamp()
    return [r for r in records if r.timestamp >= midnight_ts]


def _total(records: list[_CostEntry]) -> dict[str, float]:
    return _aggregate_by(records, lambda _r: "_total")["_total"]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_usd(v: float) -> str:
    return f"${v:,.4f}"


def _fmt_int(v: float) -> str:
    return f"{int(v):,}"


def _render_table(
    rows: list[tuple[str, ...]],
    headers: tuple[str, ...],
    aligns: tuple[str, ...] | None = None,
) -> str:
    """Render a simple text table. `aligns` ∈ {'l','r'} per column."""
    aligns = aligns or tuple("l" for _ in headers)
    if not rows:
        return f"(no rows)\n"

    # Column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def _line(cells: tuple[str, ...]) -> str:
        parts: list[str] = []
        for i, cell in enumerate(cells):
            s = str(cell)
            if aligns[i] == "r":
                parts.append(s.rjust(widths[i]))
            else:
                parts.append(s.ljust(widths[i]))
        return "  ".join(parts)

    sep = "  ".join("-" * w for w in widths)
    out = [_line(headers), sep]
    out.extend(_line(r) for r in rows)
    return "\n".join(out) + "\n"


def _resolve_wal(wal_path: str | None) -> Path:
    if wal_path:
        return Path(wal_path).expanduser()
    return Path.cwd() / ".nexus" / "wal.jsonl"


# ---------------------------------------------------------------------------
# Click group + subcommands
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--wal-path", type=click.Path(), default=None, help="Override WAL path")
@click.pass_context
def cost(ctx: click.Context, wal_path: str | None) -> None:
    """Cost tracking reports from WAL CostRecords."""
    # Default subcommand (no args) → show summary.
    if ctx.invoked_subcommand is None:
        ctx.invoke(summary, wal_path=wal_path)


@cost.command("today")
@click.option("--wal-path", type=click.Path(), default=None, help="Override WAL path")
def today(wal_path: str | None) -> None:
    """Show today's cost (since midnight local time)."""
    records = _load_cost_records(_resolve_wal(wal_path))
    today_recs = _today_records(records)
    if not today_recs:
        click.echo("No cost records today.")
        return

    t = _total(today_recs)
    click.echo(f"Today (since midnight, local)")
    click.echo(f"  calls:    {_fmt_int(t['count'])}")
    click.echo(f"  tokens:   {_fmt_int(t['prompt_tokens'] + t['completion_tokens'])} "
               f"(in {_fmt_int(t['prompt_tokens'])} / out {_fmt_int(t['completion_tokens'])})")
    click.echo(f"  cost:     {_fmt_usd(t['cost_usd'])}")

    # Break down by model for free.
    by_model = _aggregate_by(today_recs, lambda r: r.model)
    click.echo("")
    click.echo("By model:")
    rows = []
    for model in sorted(by_model):
        b = by_model[model]
        rows.append((
            model,
            _fmt_int(b["count"]),
            _fmt_int(b["prompt_tokens"] + b["completion_tokens"]),
            _fmt_usd(b["cost_usd"]),
        ))
    click.echo(_render_table(
        rows,
        ("model", "calls", "tokens", "cost_usd"),
        aligns=("l", "r", "r", "r"),
    ), nl=False)


@cost.command("by-model")
@click.option("--wal-path", type=click.Path(), default=None, help="Override WAL path")
def by_model(wal_path: str | None) -> None:
    """Aggregate costs by model name."""
    records = _load_cost_records(_resolve_wal(wal_path))
    if not records:
        click.echo("No cost records found.")
        return

    agg = _aggregate_by(records, lambda r: r.model)
    rows = []
    for model in sorted(agg):
        b = agg[model]
        rows.append((
            model,
            _fmt_int(b["count"]),
            _fmt_int(b["prompt_tokens"]),
            _fmt_int(b["completion_tokens"]),
            _fmt_usd(b["cost_usd"]),
        ))
    click.echo(_render_table(
        rows,
        ("model", "calls", "prompt_tok", "completion_tok", "cost_usd"),
        aligns=("l", "r", "r", "r", "r"),
    ), nl=False)


@cost.command("by-role")
@click.option("--wal-path", type=click.Path(), default=None, help="Override WAL path")
def by_role(wal_path: str | None) -> None:
    """Aggregate costs by role."""
    records = _load_cost_records(_resolve_wal(wal_path))
    if not records:
        click.echo("No cost records found.")
        return

    agg = _aggregate_by(records, lambda r: r.role or "<none>")
    rows = []
    for role in sorted(agg):
        b = agg[role]
        rows.append((
            role,
            _fmt_int(b["count"]),
            _fmt_int(b["prompt_tokens"] + b["completion_tokens"]),
            _fmt_usd(b["cost_usd"]),
        ))
    click.echo(_render_table(
        rows,
        ("role", "calls", "tokens", "cost_usd"),
        aligns=("l", "r", "r", "r"),
    ), nl=False)


@cost.command("session")
@click.argument("session_id")
@click.option("--wal-path", type=click.Path(), default=None, help="Override WAL path")
def session_cmd(session_id: str, wal_path: str | None) -> None:
    """Show cost for a specific session (plan_id)."""
    records = _load_cost_records(_resolve_wal(wal_path))
    matching = [r for r in records if r.session == session_id]
    if not matching:
        click.echo(f"No cost records for session {session_id!r}.")
        return

    t = _total(matching)
    click.echo(f"Session {session_id}")
    click.echo(f"  calls:    {_fmt_int(t['count'])}")
    click.echo(f"  tokens:   {_fmt_int(t['prompt_tokens'] + t['completion_tokens'])}")
    click.echo(f"  cost:     {_fmt_usd(t['cost_usd'])}")

    by_model = _aggregate_by(matching, lambda r: r.model)
    click.echo("")
    click.echo("By model:")
    rows = []
    for model in sorted(by_model):
        b = by_model[model]
        rows.append((
            model,
            _fmt_int(b["count"]),
            _fmt_int(b["prompt_tokens"] + b["completion_tokens"]),
            _fmt_usd(b["cost_usd"]),
        ))
    click.echo(_render_table(
        rows,
        ("model", "calls", "tokens", "cost_usd"),
        aligns=("l", "r", "r", "r"),
    ), nl=False)


@cost.command("export")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), required=True,
              help="Export format.")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output file path.")
@click.option("--wal-path", type=click.Path(), default=None, help="Override WAL path")
def export(fmt: str, output: str, wal_path: str | None) -> None:
    """Export all CostRecords to CSV or JSON."""
    records = _load_cost_records(_resolve_wal(wal_path))
    out_path = Path(output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        payload = [
            {
                "model": r.model,
                "hint": r.hint,
                "role": r.role,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cost_usd": r.cost_usd,
                "timestamp": r.timestamp,
                "session": r.session,
            }
            for r in records
        ]
        out_path.write_text(json.dumps(payload, indent=2))
        click.echo(f"Wrote {len(records)} records to {out_path}")
    else:
        with out_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "model", "hint", "role", "prompt_tokens",
                "completion_tokens", "cost_usd", "timestamp", "session",
            ])
            for r in records:
                writer.writerow([
                    r.model, r.hint, r.role or "", r.prompt_tokens,
                    r.completion_tokens, f"{r.cost_usd:.6f}",
                    f"{r.timestamp:.3f}", r.session or "",
                ])
        click.echo(f"Wrote {len(records)} records to {out_path}")


@cost.command("summary")
@click.option("--wal-path", type=click.Path(), default=None, help="Override WAL path")
def summary(wal_path: str | None) -> None:
    """Show summary (today + total)."""
    records = _load_cost_records(_resolve_wal(wal_path))
    if not records:
        click.echo("No cost records found.")
        return

    today_total = _total(_today_records(records))
    all_total = _total(records)
    click.echo("Cost summary")
    click.echo(f"  today:   {_fmt_usd(today_total['cost_usd'])} "
               f"({_fmt_int(today_total['count'])} calls)")
    click.echo(f"  total:   {_fmt_usd(all_total['cost_usd'])} "
               f"({_fmt_int(all_total['count'])} calls)")
    click.echo(f"  tokens:  {_fmt_int(all_total['prompt_tokens'] + all_total['completion_tokens'])} "
               f"(in {_fmt_int(all_total['prompt_tokens'])} / "
               f"out {_fmt_int(all_total['completion_tokens'])})")


# Backwards-compat: the existing `cost` symbol used to be a click.Command;
# other code may import `from src.cli.commands.cost import cost`. Make sure
# the group object is exposed under that name.
__all__ = ["cost"]