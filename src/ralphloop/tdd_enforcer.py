"""TDD Enforcer — Real TDD Enforcement with RED→GREEN→REFACTOR Loop.

This module implements TDDEnforcer, the component that enforces Test-Driven Development
discipline during RalphLoop execution. Unlike TDDGate (post-hoc verification),
TDDEnforcer provides proactive enforcement of the TDD cycle.

The enforcement follows the strict RED→GREEN→REFACTOR loop:
1. RED: Write a failing test that describes expected behavior
2. GREEN: Write minimal implementation to make tests pass
3. REFACTOR: Improve code quality without breaking tests

After 3 failed GREEN iterations, the cycle returns FAIL with ESCALATE flag.
"""

from __future__ import annotations

import tempfile
import subprocess
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class TDDPhase(Enum):
    """TDD cycle phases.
    
    RED: Test written, test failing (expected)
    GREEN: Implementation added, test passing
    REFACTOR: Code improved, tests still passing
    COMPLETE: TDD cycle complete
    FAIL: Cycle failed after max debug iterations
    """
    
    RED = auto()
    GREEN = auto()
    REFACTOR = auto()
    COMPLETE = auto()
    FAIL = auto()


@dataclass
class TDDCycle:
    """Represents a single TDD cycle state.
    
    Attributes:
        phase: Current phase in the TDD cycle.
        test_code: The test code written in RED phase.
        impl_code: The implementation code in GREEN phase.
        test_path: Optional path to the test file.
        impl_path: Optional path to the implementation file.
        debug_iterations: Number of debug iterations in GREEN phase (max 3).
        error_log: List of error messages from failed iterations.
    """
    
    phase: TDDPhase = TDDPhase.RED
    test_code: str = ""
    impl_code: str = ""
    test_path: Optional[str] = None
    impl_path: Optional[str] = None
    debug_iterations: int = 0
    error_log: list[str] = field(default_factory=list)
    
    MAX_DEBUG_ITERATIONS: int = 3  # Class constant for max iterations


@dataclass
class TDDCycleResult:
    """Result of a complete TDD cycle run.
    
    Attributes:
        success: True if the TDD cycle completed successfully.
        final_phase: The final phase reached (COMPLETE or FAIL).
        test_code: The final test code.
        impl_code: The final implementation code.
        debug_output: Human-readable log of what happened in each iteration.
        final_test_output: Output from the final test run.
    """
    
    success: bool = False
    final_phase: TDDPhase = TDDPhase.RED
    test_code: str = ""
    impl_code: str = ""
    debug_output: str = ""
    final_test_output: str = ""


class TDDEnforcer:
    """Enforces Test-Driven Development discipline with RED→GREEN→REFACTOR loop.
    
    This class orchestrates the full TDD cycle:
    1. RED: Ask LLM to write a failing test for the given task
    2. GREEN: Ask LLM to write minimal implementation to pass tests
    3. REFACTOR: Ask LLM to improve code quality without changing behavior
    
    The enforcer tracks debug iterations - if GREEN phase fails more than
    MAX_DEBUG_ITERATIONS times, it returns FAIL with ESCALATE flag.
    
    Usage:
        enforcer = TDDEnforcer()
        cycle = enforcer.start_cycle("implement user authentication")
        
        # RED phase - write failing test
        test_code = enforcer.write_red(llm_client, messages, task)
        passed, output = enforcer.run_red()
        
        # GREEN phase - write minimal implementation
        impl_code = enforcer.write_green(llm_client, messages, test_code)
        passed, output = enforcer.run_green()
        
        # Or run the full cycle
        result = enforcer.run_cycle(llm_client, messages, task)
    """
    
    MAX_DEBUG_ITERATIONS: int = 3
    
    def __init__(self, test_command: Optional[list[str]] = None):
        """Initialize TDDEnforcer.
        
        Args:
            test_command: Command to run tests (e.g., ["pytest", "tests/"]).
                         If None, tests are not executed.
        """
        self.test_command = test_command or ["python3", "-m", "pytest"]
        self._temp_dir: Optional[str] = None
    
    def start_cycle(self, task: str) -> TDDCycle:
        """Begin a new TDD cycle in RED phase.
        
        Args:
            task: The task description for the TDD cycle.
            
        Returns:
            TDDCycle in RED phase with empty impl_code.
        """
        return TDDCycle(
            phase=TDDPhase.RED,
            test_code="",
            impl_code="",
            test_path=None,
            impl_path=None,
            debug_iterations=0,
            error_log=[],
        )
    
    def write_red(
        self,
        llm_client,
        messages: list[dict],
        test_description: str,
    ) -> str:
        """Ask LLM to write a failing test for the given task.
        
        In RED phase, we ask the LLM to write a test that FAILS - the test
        must describe the expected behavior but cannot pass until implementation
        is added.
        
        Args:
            llm_client: LLM client with complete() method.
            messages: Conversation history to include.
            test_description: Description of what to test.
            
        Returns:
            The test code string written by the LLM.
        """
        prompt = f"""You are in the RED phase of Test-Driven Development.
Write a failing test for the following task. The test MUST FAIL because
the implementation doesn't exist yet.

Task: {test_description}

Requirements:
- Write the test to describe the EXPECTED behavior
- The test should fail with assertion errors or import errors because
  the implementation doesn't exist
- Do NOT write any implementation code, only tests
- Use pytest style (def test_...)

Write ONLY the test code, no explanations:"""
        
        # Build messages for LLM
        red_messages = messages + [{"role": "user", "content": prompt}]
        
        response = llm_client.complete(red_messages)
        test_code = response.content.strip()
        
        return test_code
    
    def run_red(self) -> tuple[bool, str]:
        """Execute tests in RED phase, expecting failures.
        
        In RED phase, tests SHOULD fail because no implementation exists.
        If tests pass, something is wrong (tautology or no actual testing).
        
        Returns:
            Tuple of (passed: bool, output: str).
            passed=True means tests passed (BAD in RED - indicates tautology)
            passed=False means tests failed (GOOD in RED - tests are valid)
        """
        return self._run_tests_internal(expect_pass=False)
    
    def write_green(
        self,
        llm_client,
        messages: list[dict],
        test_code: str,
    ) -> str:
        """Ask LLM to write minimal implementation to pass tests.
        
        In GREEN phase, we ask the LLM to write the MINIMAL implementation
        needed to make the tests pass. Don't add extra features.
        
        Args:
            llm_client: LLM client with complete() method.
            messages: Conversation history to include.
            test_code: The test code that should pass.
            
        Returns:
            The implementation code string.
        """
        prompt = f"""You are in the GREEN phase of Test-Driven Development.
Write the MINIMAL implementation needed to make these tests pass.
Do NOT add extra features or functionality - just what's needed
to make the tests green.

Tests:
```python
{test_code}
```

Requirements:
- Write ONLY the implementation code, no tests
- Keep it minimal - only what's needed to pass
- Do NOT modify the tests

Write ONLY the implementation code:"""
        
        green_messages = messages + [{"role": "user", "content": prompt}]
        
        response = llm_client.complete(green_messages)
        impl_code = response.content.strip()
        
        return impl_code
    
    def run_green(self) -> tuple[bool, str]:
        """Execute tests in GREEN phase, expecting passes.
        
        In GREEN phase, tests SHOULD pass if implementation is correct.
        
        Returns:
            Tuple of (passed: bool, output: str).
        """
        return self._run_tests_internal(expect_pass=True)
    
    def refactor(
        self,
        llm_client,
        messages: list[dict],
        impl_code: str,
    ) -> str:
        """Ask LLM to improve code quality without changing behavior.
        
        In REFACTOR phase, we ask the LLM to improve the code while
        ensuring tests still pass.
        
        Args:
            llm_client: LLM client with complete() method.
            messages: Conversation history to include.
            impl_code: The current implementation code.
            
        Returns:
            The refactored implementation code.
        """
        prompt = f"""You are in the REFACTOR phase of Test-Driven Development.
Improve the code quality WITHOUT changing its behavior.
The tests must continue to pass after refactoring.

Current implementation:
```python
{impl_code}
```

Improvements to consider:
- Readability and naming
- Remove duplication
- Better structure/organization
- Performance optimizations if safe
- Add comments for complex logic

Requirements:
- Write ONLY the refactored implementation
- Do NOT change functionality
- Tests must still pass after refactoring

Write ONLY the refactored implementation code:"""
        
        refactor_messages = messages + [{"role": "user", "content": prompt}]
        
        response = llm_client.complete(refactor_messages)
        refactored_code = response.content.strip()
        
        return refactored_code
    
    def run_cycle(
        self,
        llm_client,
        messages: list[dict],
        task: str,
    ) -> TDDCycleResult:
        """Run the complete RED→GREEN→REFACTOR TDD cycle.
        
        This is the main entry point that orchestrates the full cycle:
        1. RED: Write failing test
        2. GREEN: Write minimal implementation (with up to 3 debug iterations)
        3. REFACTOR: Improve code quality
        
        Args:
            llm_client: LLM client with complete() method.
            messages: Conversation history to include.
            task: The task description.
            
        Returns:
            TDDCycleResult with success status and details.
        """
        debug_log: list[str] = []
        cycle = self.start_cycle(task)
        
        # ===== RED PHASE =====
        debug_log.append("=== RED PHASE ===")
        debug_log.append(f"Task: {task}")
        
        try:
            test_code = self.write_red(llm_client, messages, task)
            cycle.test_code = test_code
            debug_log.append("Wrote failing test (RED phase)")
        except Exception as e:
            debug_log.append(f"Error in RED phase: {str(e)}")
            return TDDCycleResult(
                success=False,
                final_phase=TDDPhase.RED,
                debug_output="\n".join(debug_log),
                final_test_output=str(e),
            )
        
        # Run RED tests - expect failures
        red_passed, red_output = self.run_red()
        cycle.test_path = self._temp_dir  # Store temp path if used
        debug_log.append(f"RED test run: {'PASSED (BAD - tautology?)' if red_passed else 'FAILED (expected)'}")
        debug_log.append(f"RED output:\n{red_output[:500]}")
        
        if red_passed and not cycle.impl_code:
            debug_log.append("WARNING: Tests pass without implementation - may be tautological")
        
        # ===== GREEN PHASE =====
        debug_log.append("\n=== GREEN PHASE ===")
        
        try:
            impl_code = self.write_green(llm_client, messages, test_code)
            cycle.impl_code = impl_code
            debug_log.append("Wrote minimal implementation (GREEN phase)")
        except Exception as e:
            debug_log.append(f"Error writing implementation: {str(e)}")
            cycle.error_log.append(str(e))
            return TDDCycleResult(
                success=False,
                final_phase=TDDPhase.GREEN,
                test_code=cycle.test_code,
                impl_code=cycle.impl_code,
                debug_output="\n".join(debug_log),
                final_test_output=str(e),
            )
        
        # Run GREEN tests with debug iterations
        green_passed = False
        green_output = ""
        
        for iteration in range(1, self.MAX_DEBUG_ITERATIONS + 1):
            cycle.debug_iterations = iteration
            debug_log.append(f"\n--- GREEN iteration {iteration}/{self.MAX_DEBUG_ITERATIONS} ---")
            
            green_passed, green_output = self.run_green()
            
            if green_passed:
                debug_log.append(f"GREEN iteration {iteration}: PASSED")
                break
            else:
                debug_log.append(f"GREEN iteration {iteration}: FAILED")
                debug_log.append(f"Output:\n{green_output[:500]}")
                cycle.error_log.append(f"Iteration {iteration}: {green_output[:200]}")
                
                # If failed, ask LLM to fix based on error
                if iteration < self.MAX_DEBUG_ITERATIONS:
                    debug_log.append(f"Asking LLM to fix based on error...")
                    try:
                        fix_prompt = f"""Fix the implementation to pass the tests.
                        
Current implementation:
```python
{cycle.impl_code}
```

Test output (error):
{green_output}

Write ONLY the corrected implementation:"""
                        
                        fixed_messages = messages + [{"role": "user", "content": fix_prompt}]
                        fixed_response = llm_client.complete(fixed_messages)
                        cycle.impl_code = fixed_response.content.strip()
                        debug_log.append("Got revised implementation from LLM")
                    except Exception as e:
                        debug_log.append(f"Error getting fix: {str(e)}")
                        cycle.error_log.append(f"Fix error: {str(e)}")
        
        # ===== REFACTOR PHASE =====
        if green_passed:
            debug_log.append("\n=== REFACTOR PHASE ===")
            
            try:
                refactored = self.refactor(llm_client, messages, cycle.impl_code)
                
                # Verify refactored code still passes tests
                cycle.impl_code = refactored
                refactor_passed, refactor_output = self.run_green()
                
                if refactor_passed:
                    debug_log.append("Refactoring: PASSED - code improved, tests still green")
                else:
                    debug_log.append("Refactoring: FAILED - reverting to pre-refactor code")
                    debug_log.append(f"Refactor output:\n{refactor_output[:500]}")
                    # Keep the pre-refactor version
                    cycle.impl_code = impl_code
                    
            except Exception as e:
                debug_log.append(f"Error in REFACTOR phase: {str(e)}")
                cycle.error_log.append(f"Refactor error: {str(e)}")
                # Continue with pre-refactor code
        
        # ===== FINAL RESULT =====
        debug_log.append("\n=== CYCLE COMPLETE ===")
        
        if green_passed:
            final_phase = TDDPhase.COMPLETE if green_passed else TDDPhase.FAIL
            final_passed, final_output = self.run_green()
            
            return TDDCycleResult(
                success=green_passed,
                final_phase=TDDPhase.COMPLETE,
                test_code=cycle.test_code,
                impl_code=cycle.impl_code,
                debug_output="\n".join(debug_log),
                final_test_output=final_output,
            )
        else:
            debug_log.append(f"ESCALATE: Failed after {cycle.debug_iterations} GREEN iterations")
            
            return TDDCycleResult(
                success=False,
                final_phase=TDDPhase.FAIL,
                test_code=cycle.test_code,
                impl_code=cycle.impl_code,
                debug_output="\n".join(debug_log),
                final_test_output=green_output,
            )
    
    def _run_tests_internal(
        self,
        expect_pass: bool,
    ) -> tuple[bool, str]:
        """Internal method to run tests.
        
        Args:
            expect_pass: If True, tests are expected to pass (GREEN/REFACTOR).
                        If False, tests are expected to fail (RED).
                        
        Returns:
            Tuple of (passed: bool, output: str).
        """
        if not self.test_command:
            return (not expect_pass, "No test command configured")
        
        # If we don't have code to test yet, return based on expect_pass
        if not self.test_code and not self.impl_code:
            return (not expect_pass, "No code to test")
        
        # Create temp directory for test files
        self._temp_dir = tempfile.mkdtemp(prefix="tdd_enforcer_")
        
        test_path = os.path.join(self._temp_dir, "test_enforcer.py")
        impl_path = os.path.join(self._temp_dir, "impl_enforcer.py")
        
        # Write test file
        with open(test_path, "w") as f:
            if self.impl_code:
                # Prepend impl to test file so imports work
                f.write(f"# Implementation\n{self.impl_code}\n\n# Tests\n{self.test_code}")
            else:
                f.write(self.test_code)
        
        output = ""
        passed = False
        
        try:
            # Run pytest on the test file
            proc = subprocess.run(
                ["python3", "-m", "pytest", test_path, "-v"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = proc.stdout + proc.stderr
            passed = proc.returncode == 0
            
        except subprocess.TimeoutExpired:
            output = "Test execution timed out after 60 seconds"
        except Exception as e:
            output = f"Test execution error: {str(e)}"
        finally:
            # Cleanup
            try:
                os.unlink(test_path)
                if os.path.exists(impl_path):
                    os.unlink(impl_path)
                os.rmdir(self._temp_dir)
            except OSError:
                pass
        
        return (passed, output)
