"""TDD Enforcement Gate — Test-First Discipline Enforcement.

This module implements the TDD (Test-Driven Development) enforcement gate
that ensures the Red-Green-Refactor discipline:

1. RED: Write a failing test (test must fail before code)
2. GREEN: Write minimal implementation (test passes)
3. REFACTOR: Improve code while maintaining tests passing

The gate enforces that:
- Tests are written BEFORE implementation code
- Tests fail initially (proving they're testing behavior, not tautologies)
- Implementation is minimal (just enough to pass tests)
"""

from __future__ import annotations

import ast
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any, Callable


class TDDPhase(Enum):
    """TDD cycle phases.

    RED: Test written, test failing (expected)
    GREEN: Implementation added, test passing
    REFACTOR: Code improved, tests still passing
    COMPLETE: TDD cycle complete
    """

    RED = auto()
    GREEN = auto()
    REFACTOR = auto()
    COMPLETE = auto()


class TDDFailureReason(Enum):
    """Reasons for TDD gate failure.

    NO_TEST_WRITTEN: No test file found before implementation.
    TEST_PASSED_BEFORE_IMPLEMENTATION: Test passes without real implementation (tautology).
    IMPLEMENTATION_BEFORE_TEST: Implementation code found before tests.
    TEST_STILL_FAILING: Test still failing after implementation.
    BASELINE_BROKEN: Existing tests broken by changes.
    """

    NO_TEST_WRITTEN = "no_test_written"
    TEST_PASSED_BEFORE_IMPLEMENTATION = "test_passed_before_implementation"
    IMPLEMENTATION_BEFORE_TEST = "implementation_before_test"
    TEST_STILL_FAILING = "test_still_failing"
    BASELINE_BROKEN = "baseline_broken"
    UNKNOWN = "unknown"


@dataclass
class TDDResult:
    """Result of TDD gate verification.

    Attributes:
        passed: True if TDD discipline was followed correctly.
        phase: The TDD phase when verification occurred.
        reason: Reason for failure if not passed.
        test_path: Path to the test file.
        implementation_path: Path to the implementation file.
        test_output: Output from running tests.
        message: Human-readable message about the result.
        new_failures: List of new test failures introduced.
        fixed_failures: List of previously failing tests now passing.
    """

    passed: bool
    phase: TDDPhase
    reason: Optional[TDDFailureReason] = None
    test_path: Optional[str] = None
    implementation_path: Optional[str] = None
    test_output: Optional[str] = None
    message: str = ""
    new_failures: list[str] = field(default_factory=list)
    fixed_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "passed": self.passed,
            "phase": self.phase.name if self.phase else None,
            "reason": self.reason.value if self.reason else None,
            "test_path": self.test_path,
            "implementation_path": self.implementation_path,
            "test_output": self.test_output,
            "message": self.message,
            "new_failures": self.new_failures,
            "fixed_failures": self.fixed_failures,
        }


class TDDGate:
    """TDD Enforcement Gate for test-first discipline.

    TDDGate verifies that code changes follow Test-Driven Development discipline:
    1. Tests are written BEFORE implementation code
    2. Initial tests FAIL (proving they're not tautological)
    3. Implementation is minimal and makes tests pass

    Usage:
        gate = TDDGate()
        result = gate.verify(
            test_code=test_source,
            implementation_code=impl_source,
            test_command=["pytest", "tests/"],
        )
        if not result.passed:
            print(f"TDD violation: {result.reason.value}")
            print(result.message)

    Attributes:
        enforce_test_first: If True, fail if implementation exists before tests.
        enforce_tautology_check: If True, verify tests fail before implementation.
        min_test_quality: Minimum test quality score (0-1).
        baseline_test_paths: List of existing test files for baseline comparison.
    """

    def __init__(
        self,
        enforce_test_first: bool = True,
        enforce_tautology_check: bool = True,
        min_test_quality: float = 0.5,
        baseline_test_paths: Optional[list[str]] = None,
    ) -> None:
        """Initialize TDDGate.

        Args:
            enforce_test_first: If True, fail if implementation exists before tests.
            enforce_tautology_check: If True, verify tests fail before implementation.
            min_test_quality: Minimum test quality score (0-1).
            baseline_test_paths: List of existing test files for baseline comparison.
        """
        self._enforce_test_first = enforce_test_first
        self._enforce_tautology_check = enforce_tautology_check
        self._min_test_quality = min_test_quality
        self._baseline_test_paths = baseline_test_paths or []

    def verify(
        self,
        test_code: str,
        implementation_code: str,
        test_command: Optional[list[str]] = None,
        test_path: Optional[str] = None,
        implementation_path: Optional[str] = None,
    ) -> TDDResult:
        """Verify TDD discipline for a code change.

        This performs multiple checks:
        1. Test code analysis (structure, quality)
        2. Implementation code analysis (structure)
        3. Temporal ordering (test vs implementation)
        4. Test execution (if test_command provided)

        Args:
            test_code: The test source code.
            implementation_code: The implementation source code.
            test_command: Command to run tests (e.g., ["pytest", "tests/"]).
            test_path: Optional path to the test file for context.
            implementation_path: Optional path to the implementation file.

        Returns:
            TDDResult with pass/fail status and details.
        """
        # Phase 1: Analyze test code quality
        test_analysis = self._analyze_test_quality(test_code)
        if test_analysis["quality_score"] < self._min_test_quality:
            return TDDResult(
                passed=False,
                phase=TDDPhase.RED,
                reason=TDDFailureReason.NO_TEST_WRITTEN,
                test_path=test_path,
                implementation_path=implementation_path,
                message=f"Test quality too low: {test_analysis['quality_score']:.2f} < {self._min_test_quality:.2f}",
            )

        # Phase 2: Analyze implementation code
        impl_analysis = self._analyze_implementation(implementation_code)

        # Phase 3: Check temporal ordering
        if self._enforce_test_first:
            order_check = self._check_temporal_order(test_code, implementation_code)
            if not order_check["valid"]:
                return TDDResult(
                    passed=False,
                    phase=TDDPhase.RED,
                    reason=TDDFailureReason.IMPLEMENTATION_BEFORE_TEST,
                    test_path=test_path,
                    implementation_path=implementation_path,
                    message=order_check["reason"],
                )

        # Phase 4: If we have a test command, run it
        test_output = None
        if test_command:
            result = self._run_tests(test_code, test_command)
            test_output = result["output"]

            # Check if tests fail before implementation (tautology check)
            if self._enforce_tautology_check:
                if result["passed"] and not impl_analysis["has_implementation"]:
                    return TDDResult(
                        passed=False,
                        phase=TDDPhase.RED,
                        reason=TDDFailureReason.TEST_PASSED_BEFORE_IMPLEMENTATION,
                        test_path=test_path,
                        implementation_path=implementation_path,
                        test_output=test_output,
                        message="Test passes without implementation - test may be tautological",
                    )

            # If implementation exists and tests still fail
            if impl_analysis["has_implementation"] and not result["passed"]:
                return TDDResult(
                    passed=False,
                    phase=TDDPhase.GREEN,
                    reason=TDDFailureReason.TEST_STILL_FAILING,
                    test_path=test_path,
                    implementation_path=implementation_path,
                    test_output=test_output,
                    message="Tests still failing after implementation",
                    new_failures=result.get("failures", []),
                )

        return TDDResult(
            passed=True,
            phase=TDDPhase.COMPLETE,
            test_path=test_path,
            implementation_path=implementation_path,
            test_output=test_output,
            message="TDD discipline verified: test-first approach confirmed",
        )

    def _analyze_test_quality(self, test_code: str) -> dict[str, Any]:
        """Analyze test code quality.

        Args:
            test_code: The test source code.

        Returns:
            Dict with quality metrics.
        """
        result = {
            "quality_score": 0.0,
            "test_count": 0,
            "assertion_count": 0,
            "has_setup": False,
            "has_teardown": False,
            "uses_parametrize": False,
        }

        if not test_code.strip():
            return result

        try:
            tree = ast.parse(test_code)
        except SyntaxError:
            return result

        # Count test functions
        test_functions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name.startswith("test_") or node.name.endswith("_test"):
                    test_functions.append(node)

        result["test_count"] = len(test_functions)

        # Count assertions
        assertion_count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in ("assertTrue", "assertFalse", "assertEqual", "assertRaises",
                                          "assertIs", "assertIsNone", "assertIn", "assertNotIn",
                                          "assertGreater", "assertLess", "assertRaisesRegex"):
                        assertion_count += 1
                elif isinstance(node.func, ast.Name):
                    if node.func.id.startswith("assert"):
                        assertion_count += 1

        result["assertion_count"] = assertion_count

        # Check for setup/teardown patterns
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name in ("setup", "setup_method", "setUp", "setUpClass"):
                    result["has_setup"] = True
                if node.name in ("teardown", "teardown_method", "tearDown", "tearDownClass"):
                    result["has_teardown"] = True

        # Check for pytest parametrize
        if "parametrize" in test_code or "@pytest.mark.parametrize" in test_code:
            result["uses_parametrize"] = True

        # Calculate quality score
        score = 0.0
        if result["test_count"] > 0:
            score += 0.3
        if result["assertion_count"] >= result["test_count"]:  # At least 1 assertion per test
            score += 0.3
        if result["has_setup"]:
            score += 0.15
        if result["has_teardown"]:
            score += 0.1
        if result["uses_parametrize"]:
            score += 0.15

        result["quality_score"] = min(1.0, score)
        return result

    def _analyze_implementation(self, code: str) -> dict[str, Any]:
        """Analyze implementation code.

        Args:
            code: The implementation source code.

        Returns:
            Dict with implementation metrics.
        """
        result = {
            "has_implementation": False,
            "function_count": 0,
            "class_count": 0,
            "has_logic": False,
        }

        if not code.strip():
            return result

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return result

        # Count functions and classes
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                result["function_count"] += 1
            if isinstance(node, ast.ClassDef):
                result["class_count"] += 1

        # Check for actual logic (not just pass/None)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
                result["has_logic"] = True
            elif isinstance(node, ast.Assign):
                result["has_logic"] = True
            elif isinstance(node, ast.If) or isinstance(node, (ast.For, ast.While)):
                result["has_logic"] = True

        result["has_implementation"] = (
            result["function_count"] > 0 or result["class_count"] > 0 or result["has_logic"]
        )

        return result

    def _check_temporal_order(self, test_code: str, impl_code: str) -> dict[str, Any]:
        """Check if tests were written before implementation.

        This is a heuristic check based on code analysis. In practice,
        this would be enhanced with git history analysis.

        Args:
            test_code: The test source code.
            impl_code: The implementation source code.

        Returns:
            Dict with 'valid' (bool) and 'reason' (str).
        """
        # If both are empty, can't determine
        if not test_code.strip() and not impl_code.strip():
            return {"valid": True, "reason": "No code to analyze"}

        # If we have tests but no implementation, that's fine (RED phase)
        if test_code.strip() and not impl_code.strip():
            return {"valid": True, "reason": "Tests written, no implementation yet (valid RED phase)"}

        # If we have implementation but no tests, that's a violation
        if impl_code.strip() and not test_code.strip():
            return {"valid": False, "reason": "Implementation exists without tests (test-first violated)"}

        # Both have content - this is a GREEN/REFACTOR phase
        # For a full check, we'd analyze git history to see which came first
        return {"valid": True, "reason": "Both test and implementation exist"}

    def _run_tests(self, test_code: str, command: list[str]) -> dict[str, Any]:
        """Run tests and return results.

        Args:
            test_code: The test source code.
            command: The test command to run.

        Returns:
            Dict with 'passed' (bool), 'output' (str), and 'failures' (list).
        """
        result = {
            "passed": False,
            "output": "",
            "failures": [],
        }

        # Write test code to a temporary file if needed
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(test_code)
            temp_path = f.name

        try:
            # Run the test command
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
            )
            result["output"] = proc.stdout + proc.stderr
            result["passed"] = proc.returncode == 0

            # Parse failures from output
            if not result["passed"]:
                result["failures"] = self._parse_failures(result["output"])

        except subprocess.TimeoutExpired:
            result["output"] = "Test execution timed out after 60 seconds"
            result["failures"] = ["TIMEOUT"]
        except Exception as e:
            result["output"] = f"Test execution error: {str(e)}"
            result["failures"] = ["ERROR"]
        finally:
            # Cleanup temp file
            import os
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        return result

    def _parse_failures(self, output: str) -> list[str]:
        """Parse test failures from output.

        Args:
            output: The test output string.

        Returns:
            List of failure messages.
        """
        failures = []
        lines = output.split("\n")
        for i, line in enumerate(lines):
            if "FAILED" in line or "ERROR" in line or "FAIL:" in line:
                failures.append(line.strip())
            elif line.startswith("AssertionError:"):
                # Attach assertion error to previous failure
                if failures:
                    failures[-1] += f" | {line.strip()}"

        return failures

    def verify_baseline(
        self,
        baseline_test_paths: list[str],
        current_test_paths: list[str],
        test_command: Optional[list[str]] = None,
    ) -> TDDResult:
        """Verify TDD discipline against a baseline of existing tests.

        This compares new/changed tests against a baseline to detect:
        - New tests that don't exist in baseline
        - Tests that have different behavior than baseline

        Args:
            baseline_test_paths: List of test file paths from baseline.
            current_test_paths: List of current test file paths to verify.
            test_command: Optional command to run tests.

        Returns:
            TDDResult with pass/fail status.
        """
        if not baseline_test_paths and not current_test_paths:
            return TDDResult(
                passed=True,
                phase=TDDPhase.COMPLETE,
                message="No tests to verify",
            )

        # Check for new tests
        baseline_set = set(baseline_test_paths)
        current_set = set(current_test_paths)
        new_tests = current_set - baseline_set

        if new_tests and not current_test_paths:
            return TDDResult(
                passed=False,
                phase=TDDPhase.RED,
                reason=TDDFailureReason.NO_TEST_WRITTEN,
                message=f"New tests required but not found: {new_tests}",
            )

        # If we have a test command, verify tests pass
        if test_command:
            result = self._run_tests("\n".join([]), test_command)  # Run actual tests
            if not result["passed"]:
                return TDDResult(
                    passed=False,
                    phase=TDDPhase.GREEN,
                    reason=TDDFailureReason.TEST_STILL_FAILING,
                    test_output=result["output"],
                    message="Tests failing against baseline",
                    new_failures=result.get("failures", []),
                )

        return TDDResult(
            passed=True,
            phase=TDDPhase.COMPLETE,
            message="TDD baseline verification passed",
        )
