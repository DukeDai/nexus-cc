"""`nexus model` — Inspect model policy resolution.

Subcommands:
    nexus model list                  # all known models + sources
    nexus model show NAME             # details for one model
    nexus model resolve HINT          # resolve a hint to a concrete model
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click

from src.llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy
from src.llm.model_router import ModelRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_project_root() -> Path:
    """Project root for policy lookup. CWD is good enough for CLI use."""
    return Path.cwd()


def _load_policy(project_root: Path | None = None, cli_model: str | None = None) -> ModelPolicy:
    root = project_root or _resolve_project_root()
    return ModelPolicy.load(root, cli_model=cli_model)


def _source_for_hint(policy: ModelPolicy, hint: ModelHint, project_root: Path) -> str:
    """Best-effort: which layer contributed the current resolution for `hint`."""
    if policy.cli_override:
        return "cli"
    yaml_present = (project_root / ".nexus" / "policy.yaml").exists()
    if yaml_present:
        # We can't tell exactly whether this hint was overridden by the yaml
        # without re-parsing, but if defaults differ from DEFAULT_POLICY it's
        # likely overridden.
        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load((project_root / ".nexus" / "policy.yaml").read_text()) or {}
            yaml_defaults = data.get("defaults") or {}
            if hint.value in yaml_defaults:
                return "yaml"
        except Exception:
            pass
    if hint in policy.env_overrides:
        return "env"
    return "default"


def _resolve_precedence_chain(policy: ModelPolicy, hint: ModelHint, role: str | None) -> list[tuple[str, str | None]]:
    """Return [(layer, value), ...] in precedence order (highest wins).

    Layers: cli, per_role, env, defaults, tier-derived.
    """
    chain: list[tuple[str, str | None]] = []

    chain.append(("cli", policy.cli_override))
    if role is not None:
        chain.append((f"per_role[{role}]", policy.per_role.get(role)))
    chain.append((f"env[{hint.value}]", policy.env_overrides.get(hint)))
    chain.append((f"defaults[{hint.value}]", policy.defaults.get(hint)))

    # Tier-derived fallback (best-effort): if defaults is missing, what would
    # ModelRouter.DEFAULT_MODELS suggest? This is informational only — actual
    # resolution would raise ValueError.
    tier_suggestion = None
    try:
        router_models = ModelRouter.DEFAULT_MODELS
        # Pick the most common tier (sonnet for general use).
        if "claude-sonnet-4-6" in router_models:
            tier_suggestion = "claude-sonnet-4-6"
    except Exception:
        pass
    chain.append(("tier-derived", tier_suggestion))

    return chain


def _known_models() -> list[tuple[str, str, str]]:
    """Return (model_name, hint_or_blank, source) tuples.

    Sources are derived purely from DEFAULT_POLICY + .nexus/policy.yaml +
    env vars + cli_override. Aliases (i.e. models available in ModelRouter but
    not necessarily active in policy) are surfaced as source='alias'.
    """
    policy = _load_policy()
    root = _resolve_project_root()

    rows: list[tuple[str, str, str]] = []

    # 1) Defaults that resolve a hint
    for hint, model in DEFAULT_POLICY.items():
        if model not in {m for m, _, _ in rows}:
            rows.append((model, hint.value, "default"))

    # 2) YAML overrides
    yaml_path = root / ".nexus" / "policy.yaml"
    if yaml_path.exists():
        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load(yaml_path.read_text()) or {}
        except Exception:
            data = {}
        for hint_name, model_name in (data.get("defaults") or {}).items():
            if not any(r[0] == str(model_name) for r in rows):
                rows.append((str(model_name), str(hint_name), "yaml"))
        for role_name, model_name in (data.get("per_role") or {}).items():
            label = f"per_role:{role_name}"
            if not any(r[0] == str(model_name) and r[1] == label for r in rows):
                rows.append((str(model_name), label, "yaml"))

    # 3) Env overrides
    for hint in ModelHint:
        var = f"NEXUS_MODEL_{hint.value.upper()}"
        model = os.environ.get(var)
        if model and not any(r[0] == model for r in rows):
            rows.append((model, hint.value, "env"))

    # 4) CLI override
    if policy.cli_override and not any(r[0] == policy.cli_override for r in rows):
        rows.append((policy.cli_override, "—", "cli"))

    # 5) Aliases from ModelRouter.DEFAULT_MODELS that aren't already represented
    for name in ModelRouter.DEFAULT_MODELS.keys():
        if not any(r[0] == name for r in rows):
            rows.append((name, "—", "alias"))

    return rows


# ---------------------------------------------------------------------------
# Click group + subcommands
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.pass_context
def model(ctx: click.Context) -> None:
    """Inspect Model Policy (v1.2 Router)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_models)


@model.command("list")
def list_models() -> None:
    """List all known models and their sources."""
    rows = _known_models()
    if not rows:
        click.echo("No models registered.")
        return

    # Width for each column.
    table_rows = [
        (m, h, s) for (m, h, s) in sorted(rows, key=lambda r: (r[2], r[0]))
    ]
    widths = [max(len(r[i]) for r in table_rows + [("model", "hint", "source")]) for i in range(3)]
    header = "  ".join(
        ("model".ljust(widths[0]), "hint".ljust(widths[1]), "source".ljust(widths[2]))
    )
    sep = "  ".join("-" * w for w in widths)
    click.echo(header)
    click.echo(sep)
    for m, h, s in table_rows:
        click.echo(f"{m.ljust(widths[0])}  {h.ljust(widths[1])}  {s.ljust(widths[2])}")


@model.command("show")
@click.argument("name")
def show_model(name: str) -> None:
    """Show details for a specific model."""
    policy = _load_policy()
    root = _resolve_project_root()

    # Find which hints map to this model.
    hint_strs: list[str] = []
    for hint, model in DEFAULT_POLICY.items():
        if model == name:
            hint_strs.append(hint.value)
    yaml_path = root / ".nexus" / "policy.yaml"
    if yaml_path.exists():
        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load(yaml_path.read_text()) or {}
        except Exception:
            data = {}
        for hint_name, model_name in (data.get("defaults") or {}).items():
            if str(model_name) == name and hint_name not in hint_strs:
                hint_strs.append(str(hint_name))
        for role_name, model_name in (data.get("per_role") or {}).items():
            if str(model_name) == name:
                hint_strs.append(f"per_role:{role_name}")

    # CLI override case.
    if policy.cli_override == name and not hint_strs:
        hint_strs.append("(cli override)")

    # Alias case.
    if not hint_strs and name in ModelRouter.DEFAULT_MODELS:
        hint_strs.append("(alias — not active in policy)")

    click.echo(f"Model: {name}")
    click.echo(f"  hints:    {', '.join(hint_strs) if hint_strs else '<none — unknown model>'}")
    click.echo(f"  source:   "
               f"{'cli' if policy.cli_override == name else _find_source(name, policy, root)}")
    click.echo(f"  in router: {'yes' if name in ModelRouter.DEFAULT_MODELS else 'no'}")
    click.echo("")
    click.echo("Resolution precedence (highest wins):")
    for layer, value in _resolve_precedence_chain(policy, ModelHint.PLANNER, role=None):
        marker = " <- active" if value == name and layer == "defaults[planner]" else ""
        click.echo(f"  {layer:24s} = {value}{marker}")


def _find_source(name: str, policy: ModelPolicy, project_root: Path) -> str:
    """Best-effort label for which layer a given model name comes from."""
    if policy.cli_override == name:
        return "cli"
    if name in policy.env_overrides.values():
        return "env"
    yaml_path = project_root / ".nexus" / "policy.yaml"
    if yaml_path.exists():
        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load(yaml_path.read_text()) or {}
        except Exception:
            data = {}
        for v in (data.get("defaults") or {}).values():
            if str(v) == name:
                return "yaml"
        for v in (data.get("per_role") or {}).values():
            if str(v) == name:
                return "yaml"
    if name in {m for m in DEFAULT_POLICY.values()}:
        return "default"
    if name in ModelRouter.DEFAULT_MODELS:
        return "alias"
    return "unknown"


@model.command("resolve")
@click.argument("hint_name")
@click.option("--role", default=None, help="Optional role to factor into resolution.")
def resolve_hint(hint_name: str, role: str | None) -> None:
    """Show what model a hint resolves to."""
    try:
        hint = ModelHint(hint_name)
    except ValueError:
        click.echo(f"Unknown hint: {hint_name!r}", err=True)
        click.echo(f"Valid hints: {', '.join(h.value for h in ModelHint)}", err=True)
        raise SystemExit(2)

    policy = _load_policy()
    try:
        resolved = policy.resolve(hint, role=role)
    except ValueError as exc:
        click.echo(f"Resolution failed: {exc}", err=True)
        raise SystemExit(1)

    source = _source_for_hint(policy, hint, _resolve_project_root())
    click.echo(f"Hint: {hint.value}")
    if role:
        click.echo(f"Role: {role}")
    click.echo(f"Resolved model: {resolved}")
    click.echo(f"Source: {source}")
    click.echo("")
    click.echo("Resolution chain (highest precedence wins):")
    for layer, value in _resolve_precedence_chain(policy, hint, role):
        marker = " <- active" if value == resolved else ""
        click.echo(f"  {layer:24s} = {value}{marker}")


__all__ = ["model"]