"""`nexus run` — Execute a task through AgentRuntime (plan-first)."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import click

from src.agent.control import ControlChannel
from src.agent.runtime import AgentRuntime
from src.context.wal import WALManager
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def ensure_nexus_policy(project_root: Path) -> None:
    """Create `.nexus/policy.yaml` if it doesn't already exist.

    This writes a starter template (all sections commented out) so users
    have a discoverable way to override the v1.2 defaults. It does NOT
    overwrite an existing file — if the user has already authored one, we
    leave it untouched.
    """
    policy_path = project_root / ".nexus" / "policy.yaml"
    if policy_path.exists():
        return
    try:
        from src.llm.model_policy import ModelPolicy

        ModelPolicy.create_default_yaml(policy_path)
        logger.debug("Created starter policy at %s", policy_path)
    except Exception as exc:
        # Failing to seed the template must not block a run.
        logger.debug("ensure_nexus_policy: could not create %s: %s", policy_path, exc)


@click.command()
@click.option("--task", "-t", required=True, help="Task description")
@click.option("--workdir", "-C", type=click.Path(file_okay=False), help="Working directory")
@click.option("--wal-path", type=click.Path(), help="WAL file path")
@click.option("--spec", "-s", help="Additional spec")
@click.option("--model", "-m", default=None, help="Override model name (v1.2 router only)")
def run(task: str, workdir: str | None, wal_path: str | None, spec: str | None, model: str | None) -> int:
    """Run a task through AgentRuntime (plan-first architecture)."""
    project_path = Path(workdir or os.getcwd()).expanduser().resolve()
    wal_file = Path(wal_path).expanduser() if wal_path else (project_path / ".nexus" / "wal.jsonl")

    # Auto-create a starter policy template on first nexus run. This is a
    # discoverability aid only — we never overwrite an existing file.
    ensure_nexus_policy(project_path)

    channel = ControlChannel()
    wal = WALManager(path=wal_file)
    tools = ToolRegistry.with_defaults(workdir=str(project_path))

    # LLM client — minimal stub for v1
    llm = _build_llm_client(project_root=project_path, wal=wal, cli_model=model)
    if llm is None:
        click.echo("Error: ANTHROPIC_API_KEY not set and no LLM available", err=True)
        return 1

    runtime = AgentRuntime(
        llm=llm,
        tools=tools,
        verification=None,  # v1: optional
        wal=wal,
        channel=channel,
    )

    click.echo(f"Nexus | Task: {task[:80]}")
    click.echo(f"Project: {project_path}")

    # plan-then-walk
    async def run_async():
        plan = await runtime.plan(task, spec=spec)
        click.echo(f"Plan: {plan.spec}")
        click.echo(f"Steps: {len(plan.steps)}")
        results = await runtime.walk(plan)
        return results

    results = asyncio.run(run_async())

    failed = sum(1 for r in results if getattr(r, "status", None) == "failed")
    skipped = sum(1 for r in results if getattr(r, "status", None) == "skipped")
    done = sum(1 for r in results if getattr(r, "status", None) == "done")

    click.echo(f"\nResult: {done} done, {skipped} skipped, {failed} failed")
    return 0 if failed == 0 else 1


def _build_llm_client(
    project_root: Path | None = None,
    wal: WALManager | None = None,
    cli_model: str | None = None,
) -> Any:
    """Build LLM client.

    v1.1 behavior (NEXUS_USE_MODEL_ROUTER unset / "0"): return the minimal
    _AnthropicLLM wrapper exactly as before — no behavior change.

    v1.2 behavior (NEXUS_USE_MODEL_ROUTER=1): return a _RouterAdapter that
    exposes the same .complete(system=, messages=) shape but routes through
    ModelRouter → LLMClient. This unblocks the 13 downstream touchpoints
    without touching them yet.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        return None

    use_router = os.environ.get("NEXUS_USE_MODEL_ROUTER", "0") == "1"

    if not use_router:
        # v1.1 path — unchanged.
        try:
            from anthropic import AsyncAnthropic
            return _AnthropicLLM(AsyncAnthropic(api_key=api_key))
        except ImportError:
            return None

    # v1.2 path — feature-flagged Router.
    try:
        from src.llm.cost_tracker import CostTracker
        from src.llm.model_policy import ModelHint, ModelPolicy
        from src.llm.model_router import ModelRouter

        policy = ModelPolicy.load(
            project_root or Path("."),
            cli_model=cli_model,
        )
        tracker = CostTracker(project_root=project_root or Path("."), wal=wal)
        router = ModelRouter(policy=policy, cost_tracker=tracker)
        return _RouterAdapter(router=router, hint=ModelHint.PLANNER)
    except Exception:
        # Router init failure → silently fall back to v1.1 behavior rather
        # than blocking the run. The integration test will surface real bugs.
        try:
            from anthropic import AsyncAnthropic
            return _AnthropicLLM(AsyncAnthropic(api_key=api_key))
        except ImportError:
            return None


class _AnthropicLLM:
    """Minimal wrapper exposing .complete(system=, messages=)."""

    def __init__(self, client):
        self._client = client

    async def complete(self, *, system: str, messages: list[dict], **kwargs) -> "_AnthropicResponse":
        # `**kwargs` swallows v1.2 routing kwargs (e.g. model_hint) so callers
        # like Planner.plan() can pass them uniformly; legacy client ignores them.
        msg = await self._client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        return _AnthropicResponse(msg)


class _AnthropicResponse:
    def __init__(self, msg):
        self.content = msg.content


class _RouterAdapter:
    """Adapter: ModelRouter → .complete(system=, messages=) shape.

    Maps the existing planner/walker/verifier call sites onto ModelRouter.route.
    Each call may pass ``model_hint=...`` to override the adapter's default
    hint (used for sub-plan call sites that need a different ModelHint than
    the surrounding context, e.g. CRITIQUE sub-plans).
    """

    def __init__(self, router, hint):
        self._router = router
        self._hint = hint

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        model_hint: Any | None = None,
    ) -> Any:
        # ModelRouter.route is sync. We run it in a thread to keep the async
        # contract for callers that awaited .complete().
        import asyncio

        hint = model_hint if model_hint is not None else self._hint

        def _call():
            return self._router.route(
                messages=messages,
                hint=hint,
                system_prompt=system,
            )

        # We can't actually make HTTP calls in tests; callers handle that.
        _, response = await asyncio.to_thread(_call)
        return response