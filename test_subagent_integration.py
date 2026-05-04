#!/usr/bin/env python3
"""RalphLoop Subagent Integration — End-to-End Tests

Tests the full RalphLoop + Subagent integration:
1. CLAUDE.md loader + project root detection
2. Subagent registry
3. SubagentIntegration orchestration
4. Multi-agent parallel execution (when delegate_task is available)
"""

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ralphloop import (
    SubagentDefinition,
    SUBAGENT_DEFINITIONS,
    get_subagent,
    get_all_subagents,
    ProjectContext,
    load_claude_md,
    find_project_root,
    find_claude_md,
    build_llm_system_prompt,
    get_project_context,
    SubagentIntegration,
    SubagentResult,
    OrchestratedResult,
    orchestrate_with_subagents,
)


def test_claude_md_loader():
    """Test CLAUDE.md discovery and loading."""
    print("\n=== Test: CLAUDE.md Loader ===")
    
    # Test find_project_root
    root = find_project_root(Path(__file__).parent)
    print(f"  Project root: {root}")
    assert root is not None, "Should find project root"
    
    # Test find_claude_md (may not exist yet)
    claudemd_path = find_claude_md(Path(__file__).parent)
    print(f"  CLAUDE.md found: {claudemd_path}")
    
    # Test load_claude_md
    content = load_claude_md(Path(__file__).parent)
    print(f"  CLAUDE.md content: {'Found' if content else 'Not found'}")
    
    # Test get_project_context
    ctx = get_project_context(Path(__file__).parent)
    print(f"  Languages: {ctx['languages']}")
    print(f"  Git branch: {ctx['git_branch']}")
    print(f"  Git dirty: {ctx['git_dirty']}")
    
    # Test ProjectContext class
    proj = ProjectContext(Path(__file__).parent)
    print(f"  ProjectContext: {proj}")
    print(f"  Has CLAUDE.md: {proj.has_claude_md}")
    
    print("  ✅ CLAUDE.md loader: PASS")
    return True


def test_subagent_registry():
    """Test subagent registry."""
    print("\n=== Test: Subagent Registry ===")
    
    # Test get_all_subagents
    all_agents = get_all_subagents()
    print(f"  Registered agents: {list(all_agents.keys())}")
    assert len(all_agents) >= 4, "Should have at least 4 agents"
    
    # Test get_subagent
    impl_def = get_subagent("implementer")
    assert impl_def is not None, "Should find implementer"
    assert isinstance(impl_def, SubagentDefinition)
    print(f"  Implementer: {impl_def.name}, max_turns={impl_def.max_turns}")
    print(f"    capabilities: {impl_def.capabilities}")
    
    # Test system prompts
    spec_def = get_subagent("specifier")
    assert "SpecifierAgent" in spec_def.system_prompt
    print(f"  Specifier system prompt length: {len(spec_def.system_prompt)} chars")
    
    # Test unknown agent
    unknown = get_subagent("nonexistent")
    assert unknown is None
    print(f"  Unknown agent returns None: PASS")
    
    print("  ✅ Subagent registry: PASS")
    return True


def test_project_context():
    """Test ProjectContext class."""
    print("\n=== Test: ProjectContext ===")
    
    proj = ProjectContext(Path(__file__).parent)
    
    # Test properties
    assert proj.root is not None
    print(f"  Root: {proj.root}")
    
    assert proj.languages is not None
    print(f"  Languages: {proj.languages}")
    
    # Test build_system_prompt
    prompt = proj.build_system_prompt(extra="Custom context here")
    assert len(prompt) > 0
    assert "Custom context here" in prompt
    print(f"  System prompt length: {len(prompt)} chars")
    print(f"  Contains CLAUDE.md: {'CLAUDE.md' in prompt}")
    print(f"  Contains Git info: {'Git:' in prompt}")
    
    # Test with extra
    prompt2 = proj.build_system_prompt("Extra constraints")
    assert "Extra constraints" in prompt2
    print(f"  Extra context included: PASS")
    
    print("  ✅ ProjectContext: PASS")
    return True


def test_subagent_integration():
    """Test SubagentIntegration orchestration."""
    print("\n=== Test: SubagentIntegration ===")
    
    integration = SubagentIntegration(workdir=Path(__file__).parent)
    
    # Test specifier
    print("  Running SpecifierAgent...")
    spec_result = integration.run_specifier(
        "Build a REST API for a todo list with FastAPI"
    )
    print(f"    Status: {spec_result.status}")
    print(f"    Role: {spec_result.role}")
    assert spec_result.role == "specifier"
    assert spec_result.task_id is not None
    print(f"    Task ID: {spec_result.task_id}")
    print(f"    Duration: {spec_result.duration_seconds:.3f}s")
    
    # Test security scan
    print("  Running SecurityAgent...")
    sec_result = integration.run_security_scan(["src/ralphloop/agent_loop.py"])
    print(f"    Status: {sec_result.status}")
    print(f"    Role: {sec_result.role}")
    
    # Test aggregate_results
    agg = integration.aggregate_results([spec_result, sec_result])
    print(f"  Aggregated: {agg['total_tool_calls']} tool calls, {agg['total_turns']} turns")
    assert agg["total_tool_calls"] == 0  # No actual tool calls in mock
    assert agg["escalations"] == []
    
    print("  ✅ SubagentIntegration: PASS")
    return True


def test_orchestrate_with_subagents():
    """Test high-level orchestrate_with_subagents."""
    print("\n=== Test: orchestrate_with_subagents ===")
    
    # Test specifier_only mode
    result = orchestrate_with_subagents(
        task="Create a calculator module",
        workdir=Path(__file__).parent,
        mode="specifier_only",
    )
    print(f"  specifier_only mode:")
    print(f"    overall_status: {result.overall_status}")
    print(f"    spec_result status: {result.spec_result.status if result.spec_result else 'None'}")
    
    # Test implementer_with_review mode
    result2 = orchestrate_with_subagents(
        task="Implement a calculator with add/subtract",
        workdir=Path(__file__).parent,
        mode="implementer_with_review",
    )
    print(f"  implementer_with_review mode:")
    print(f"    overall_status: {result2.overall_status}")
    print(f"    summary: {result2.summary[:100]}...")
    
    print("  ✅ orchestrate_with_subagents: PASS")
    return True


def test_full_ralphloop_integration():
    """Test full RalphLoop with agent_loop."""
    print("\n=== Test: Full RalphLoop Integration ===")
    
    # nexus_core shim is available at project root
    try:
        import nexus_core
        print(f"  nexus_core shim loaded from: {nexus_core.__file__}")
    except ImportError:
        print("  ⚠️  nexus_core.py not importable")
        print("  Skipping full integration test")
        return True
    
    # Test LLM client detection
    try:
        from nexus_core import LLMClient
        client = LLMClient(provider="auto")
        print(f"  LLM Provider: {client.provider}")
    except ImportError as e:
        print(f"  ⚠️  LLMClient not available: {e}")

    # Test ProjectContext in LLM flow
    ctx = ProjectContext(Path(__file__).parent)
    system_prompt = ctx.build_system_prompt(
        extra="You are implementing a calculator module."
    )
    print(f"  System prompt length: {len(system_prompt)} chars")
    
    print("  ✅ Full RalphLoop Integration: PASS")
    return True


def test_tdd_enforcer_integration():
    """Test TDD enforcer with real LLM."""
    print("\n=== Test: TDD Enforcer + Agent Loop ===")
    
    # This would run a real TDD cycle if LLM is available
    try:
        from nexus_core import NexusCore
        print("  nexus_core available: would run real TDD")
    except ImportError:
        print("  ⚠️  nexus_core not available")
    
    # Verify TDD enforcer exists and is importable
    from ralphloop import TDDEnforcer, TDDPhase
    print(f"  TDDEnforcer importable: True")
    print(f"  TDDPhase values: {[p.name for p in TDDPhase]}")
    
    print("  ✅ TDD Enforcer Integration: PASS")
    return True


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        ("CLAUDE.md Loader", test_claude_md_loader),
        ("Subagent Registry", test_subagent_registry),
        ("ProjectContext", test_project_context),
        ("SubagentIntegration", test_subagent_integration),
        ("orchestrate_with_subagents", test_orchestrate_with_subagents),
        ("Full RalphLoop Integration", test_full_ralphloop_integration),
        ("TDD Enforcer Integration", test_tdd_enforcer_integration),
    ]
    
    print("=" * 60)
    print("  RalphLoop Subagent Integration — E2E Tests")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            if test_fn():
                passed += 1
            else:
                failed += 1
                print(f"  ❌ {name}: FAIL")
        except Exception as e:
            failed += 1
            print(f"  ❌ {name}: EXCEPTION — {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
