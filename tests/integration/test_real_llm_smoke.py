"""Real LLM smoke tests — exercise full Plan → Walk pipeline.

These tests REQUIRE a real LLM (ANTHROPIC_API_KEY env var). They skip if not set.
Each test creates a temp project, runs a task via AgentRuntime, and verifies behavior.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.agent.control import ControlChannel
from src.agent.runtime import AgentRuntime
from src.context.wal import WALManager
from src.tools.registry import ToolRegistry


def _llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _make_runtime(workdir: Path, wal_path: Path):
    """Build a real LLM AgentRuntime. Returns None if no key."""
    if not _llm_available():
        return None

    from anthropic import AsyncAnthropic
    from src.cli.commands.run import _AnthropicLLM

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    client = AsyncAnthropic(api_key=api_key)
    llm = _AnthropicLLM(client)

    channel = ControlChannel()
    wal = WALManager(path=wal_path)
    tools = ToolRegistry.with_defaults(workdir=str(workdir))

    return AgentRuntime(
        llm=llm,
        tools=tools,
        verification=None,
        wal=wal,
        channel=channel,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="No ANTHROPIC_API_KEY")
async def test_smoke_add_comment(tmp_path):
    """LLM plans and executes: add a comment to src/foo.py.

    These smoke tests assert on the user-visible outcome (file content),
    not on the LLM's internal step-kind distribution, because the LLM is
    free to choose TOOL/VERIFY/CRITIQUE steps for any given task. The
    distribution is non-deterministic across model versions/temperatures.
    """
    # Setup: create src/foo.py
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    foo_file = src_dir / "foo.py"
    foo_file.write_text("def hello():\n    return 'world'\n")

    runtime = _make_runtime(tmp_path, tmp_path / "wal.jsonl")
    assert runtime is not None

    task = "在 src/foo.py 加一行注释 '# updated by nexus'"
    plan = await runtime.plan(task)

    # Sanity: plan has at least one step
    assert len(plan.steps) >= 1, f"plan had no steps: {plan.steps}"

    # Execute
    await runtime.walk(plan)

    # Verify file changed (the user-visible outcome)
    new_content = foo_file.read_text()
    assert "# updated by nexus" in new_content, f"file not modified: {new_content}"


@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="No ANTHROPIC_API_KEY")
async def test_smoke_rename_files(tmp_path):
    """LLM plans and executes: rename tests/ files to snake_case.

    Asserts on the user-visible outcome (renamed files exist) rather than
    on the LLM's step-kind distribution, which is non-deterministic.
    """
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "TestFoo.py").write_text("# file 1\n")
    (tests_dir / "TestBar.py").write_text("# file 2\n")
    (tests_dir / "test_baz.py").write_text("# file 3\n")

    runtime = _make_runtime(tmp_path, tmp_path / "wal.jsonl")
    assert runtime is not None

    task = "把 tests/TestFoo.py 和 tests/TestBar.py 改名为 snake_case (test_foo.py, test_bar.py)"
    plan = await runtime.plan(task)

    # Sanity: plan has at least one step
    assert len(plan.steps) >= 1, f"plan had no steps: {plan.steps}"

    await runtime.walk(plan)

    # Verify files renamed (the user-visible outcome)
    assert (tests_dir / "test_foo.py").exists()
    assert (tests_dir / "test_bar.py").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="No ANTHROPIC_API_KEY")
async def test_smoke_fix_pytest(tmp_path):
    """LLM plans and executes: run pytest and fix any failing tests.

    The planner occasionally hallucinates a tool name (e.g. 'run_command')
    that is not registered. The strengthened SYSTEM_PROMPT now lists the
    8 real tool names explicitly. This test asserts on the user-visible
    outcome (broken test was fixed) rather than on step-kind distribution
    or the LLM's tool-name choice (both non-deterministic).
    """
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    # Create a deliberately broken test file
    broken = tests_dir / "test_broken.py"
    broken.write_text("def test_passes():\n    assert 1 == 2  # this fails\n")

    # Create a passing test that should stay passing
    passing = tests_dir / "test_passes.py"
    passing.write_text("def test_ok():\n    assert 1 + 1 == 2\n")

    runtime = _make_runtime(tmp_path, tmp_path / "wal.jsonl")
    assert runtime is not None

    task = "运行 pytest tests/ 并修复所有失败的测试"
    plan = await runtime.plan(task)

    # Sanity: plan has at least one step
    assert len(plan.steps) >= 1, f"plan had no steps: {plan.steps}"

    await runtime.walk(plan)

    # Verify the broken test got modified (the user-visible outcome).
    # The LLM may also rewrite the file differently; we just check the
    # failing assertion is gone or the test is skipped.
    new_content = broken.read_text()
    assert "1 == 2" not in new_content or "skip" in new_content.lower(), \
        f"broken test not fixed: {new_content}"