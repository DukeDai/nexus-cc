#!/usr/bin/env python3
"""
RalphLoop Real TDD Test — Uses Nexus modules to drive a real TDD cycle.

Tests: RED → GREEN → REFACTOR loop with actual LLM calls.
"""

import sys
import os
import subprocess
from pathlib import Path

# Ensure src/ is on path
sys.path.insert(0, '/Users/dukedai/Dev/nexus')

from nexus_core import LLMClient
from src.ralphloop import (
    ImplementationContext,
    TDDEnforcer,
    AgentLoopConfig,
    ToolExecutor,
    TOOL_DEFINITIONS,
)


def run_tdd_test():
    """Run a real TDD cycle using the Nexus RalphLoop modules."""
    workdir = Path("/tmp/nexus_tdd_test")
    workdir.mkdir(exist_ok=True)

    # Initialize components
    llm = LLMClient(provider="auto")
    executor = ToolExecutor(workdir=workdir)
    context = ImplementationContext(task="", context_window=100000)
    tdd_enforcer = TDDEnforcer()
    config = AgentLoopConfig(max_turns=15)

    print("=" * 70)
    print("RALPHLOOP TDD CLOSED-LOOP TEST")
    print("=" * 70)
    print(f"LLM Provider: {llm.provider}")
    print(f"Workdir: {workdir}")
    print()

    task = (
        "Create a Python module in /tmp/nexus_tdd_test/calculator.py "
        "with a function `multiply(a, b)` that returns the product. "
        "Write tests first (RED), then implementation (GREEN), then refactor (REFACTOR). "
        "Follow TDD discipline strictly."
    )

    # Phase 1: RED — Write failing test
    print("-" * 70)
    print("PHASE 1: RED — Write failing test")
    print("-" * 70)

    red_messages = [
        {"role": "user", "content": (
            "You are following TDD discipline. Write a FAILED test for the function `multiply(a, b)` "
            "that returns the product of two numbers. Save the test to /tmp/nexus_tdd_test/test_calculator.py. "
            "The test should fail because the implementation doesn't exist yet. "
            "Use pytest. Write ONLY the test file."
        )}
    ]

    red_response = llm.complete(red_messages, tools=TOOL_DEFINITIONS)
    red_test_code = ""

    if red_response.get("tool_calls"):
        for tc in red_response["tool_calls"]:
            if tc["name"] == "write_file":
                red_test_code = tc["args"]["content"]
                result = executor.execute(tc["name"], tc["args"])
                print(f"write_file result: {result[:200]}")
            elif tc["name"] == "bash":
                result = executor.execute(tc["name"], tc["args"])
                print(f"bash result: {result[:200]}")
    elif red_response.get("content"):
        red_test_code = red_response["content"]
        # Extract code from markdown if needed
        import re
        match = re.search(r'```python\n(.*?)```', red_test_code, re.DOTALL)
        if match:
            red_test_code = match.group(1)
        if red_test_code.strip():
            test_path = workdir / "test_calculator.py"
            test_path.write_text(red_test_code)
            print(f"Wrote test to {test_path}")

    # Find pytest
    import shutil
    pytest_path = shutil.which("pytest") or "/opt/anaconda3/bin/pytest"
    test_cmd = f"{pytest_path} /tmp/nexus_tdd_test/test_calculator.py -v --tb=short 2>&1"
    red_result = executor.execute("bash", {
        "command": f"cd /tmp/nexus_tdd_test && {test_cmd}",
        "timeout": 30
    })
    red_failures = "FAILED" in red_result or "ERROR" in red_result
    print(red_result[:500])
    print(f"RED test failed as expected: {red_failures}")

    # Phase 2: GREEN — Write minimal implementation
    print("\n" + "-" * 70)
    print("PHASE 2: GREEN — Write minimal implementation")
    print("-" * 70)

    green_messages = [
        {"role": "user", "content": (
            f"You are following TDD discipline. The test is:\n\n{red_test_code}\n\n"
            "Write ONLY the minimal implementation of `multiply(a, b)` in Python "
            "to make this test pass. Save to /tmp/nexus_tdd_test/calculator.py. "
            "Write ONLY the implementation file."
        )}
    ]

    green_response = llm.complete(green_messages, tools=TOOL_DEFINITIONS)
    green_impl_code = ""

    if green_response.get("tool_calls"):
        for tc in green_response["tool_calls"]:
            if tc["name"] == "write_file":
                green_impl_code = tc["args"]["content"]
                result = executor.execute(tc["name"], tc["args"])
                print(f"write_file result: {result[:200]}")
    elif green_response.get("content"):
        green_impl_code = green_response["content"]
        import re
        match = re.search(r'```python\n(.*?)```', green_impl_code, re.DOTALL)
        if match:
            green_impl_code = match.group(1)
        if green_impl_code.strip():
            impl_path = workdir / "calculator.py"
            impl_path.write_text(green_impl_code)
            print(f"Wrote impl to {impl_path}")

    # Run GREEN test — should pass
    print("\nRunning GREEN test (expecting PASS)...")
    green_result = executor.execute("bash", {
        "command": f"cd /tmp/nexus_tdd_test && {test_cmd}",
        "timeout": 30
    })
    green_passes = "passed" in green_result and "FAILED" not in green_result
    print(green_result[:500])
    print(f"GREEN test passed: {green_passes}")

    # Phase 3: REFACTOR — Improve code quality
    print("\n" + "-" * 70)
    print("PHASE 3: REFACTOR — Improve code quality")
    print("-" * 70)

    refactor_messages = [
        {"role": "user", "content": (
            f"Current implementation:\n\n{green_impl_code}\n\n"
            "Improve the code quality (add docstrings, type hints, clean up) "
            "WITHOUT changing the behavior. The tests must still pass after refactoring. "
            "Save to /tmp/nexus_tdd_test/calculator.py."
        )}
    ]

    refactor_response = llm.complete(refactor_messages, tools=TOOL_DEFINITIONS)

    if refactor_response.get("tool_calls"):
        for tc in refactor_response["tool_calls"]:
            if tc["name"] == "write_file":
                refactor_impl_code = tc["args"]["content"]
                result = executor.execute(tc["name"], tc["args"])
                print(f"write_file result: {result[:200]}")
            elif tc["name"] == "bash":
                result = executor.execute(tc["name"], tc["args"])
                print(f"bash result: {result[:200]}")
    elif refactor_response.get("content"):
        refactor_impl_code = refactor_response["content"]
        import re
        match = re.search(r'```python\n(.*?)```', refactor_impl_code, re.DOTALL)
        if match:
            refactor_impl_code = match.group(1)
        if refactor_impl_code.strip():
            impl_path = workdir / "calculator.py"
            impl_path.write_text(refactor_impl_code)
            print(f"Wrote refactored impl to {impl_path}")

    # Run final tests
    print("\nRunning final tests after refactor...")
    final_result = executor.execute("bash", {
        "command": f"cd /tmp/nexus_tdd_test && {test_cmd}",
        "timeout": 30
    })
    final_passes = "passed" in final_result and "FAILED" not in final_result
    print(final_result[:500])

    # Show final files
    print("\n" + "=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)

    test_file = workdir / "test_calculator.py"
    impl_file = workdir / "calculator.py"

    if test_file.exists():
        print(f"\ntest_calculator.py:\n{test_file.read_text()}")

    if impl_file.exists():
        print(f"\ncalculator.py:\n{impl_file.read_text()}")

    print("\n" + "=" * 70)
    print("TDD CYCLE SUMMARY")
    print("=" * 70)
    print(f"RED test written and failed as expected: {red_failures}")
    print(f"GREEN implementation written and tests pass: {green_passes}")
    print(f"REFACTOR completed with tests still passing: {final_passes}")
    print(f"\nOverall TDD cycle: {'✅ PASS' if (red_failures and green_passes and final_passes) else '❌ FAIL'}")
    print("=" * 70)


if __name__ == "__main__":
    # Clean up
    import shutil
    td = Path("/tmp/nexus_tdd_test")
    if td.exists():
        shutil.rmtree(td)
    td.mkdir()

    run_tdd_test()
