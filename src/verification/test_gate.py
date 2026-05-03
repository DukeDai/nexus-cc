"""Test Execution Gate — Baseline comparison for regression detection.

This module implements the test execution gate that:
1. Runs the full test suite
2. Compares against baseline failure count
3. NEW failures = regression = block commit
4. FIXED failures = bonus (previous bugs fixed)

The gate is non-blocking for fixes but BLOCKS new regressions.
"""

from __future__ import annotations

import subprocess
import tempfile
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any, Callable


class TestGateMode(Enum):
    """Test gate execution modes.

    STRICT: Block on ANY new failure.
    BASELINE: Only block if failures exceed baseline count.
    LENIENT: Report only, don't block.
    """

    STRICT = auto()
    BASELINE = auto()
    LENIENT = auto()


class TestFailureType(Enum):
    """Categorization of test failures.

    NEW: New failure not in baseline (BLOCKING).
    REGRESSION: Previously passing test now failing (BLOCKING).
    EXISTING: Already failing in baseline (non-blocking).
    FIXED: Previously failing, now passing (positive).
    UNKNOWN: Cannot determine (treat as blocking).
    """

    NEW = "new"
    REGRESSION = "regression"
    EXISTING = "existing"
    FIXED = "fixed"
    UNKNOWN = "unknown"


@dataclass
class TestResult:
    """Result of a single test execution.

    Attributes:
        name: Test name or identifier.
        passed: True if test passed.
        failure_type: Category of failure if not passed.
        message: Failure message or None if passed.
        duration: Test duration in seconds.
        details: Additional details about the test.
    """

    name: str
    passed: bool
    failure_type: TestFailureType = TestFailureType.UNKNOWN
    message: Optional[str] = None
    duration: Optional[float] = None
    details: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "name": self.name,
            "passed": self.passed,
            "failure_type": self.failure_type.value,
            "message": self.message,
            "duration": self.duration,
            "details": self.details,
        }


@dataclass
class BaselineData:
    """Baseline test execution data for comparison.

    Attributes:
        test_results: Dict of test name to TestResult.
        total_count: Total number of tests.
        passed_count: Number of tests passing.
        failed_count: Number of tests failing.
        hash: Hash of the baseline for comparison.
        timestamp: When the baseline was captured.
    """

    test_results: dict[str, TestResult]
    total_count: int
    passed_count: int
    failed_count: int
    hash: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Convert baseline to dictionary."""
        return {
            "test_results": {k: v.to_dict() for k, v in self.test_results.items()},
            "total_count": self.total_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "hash": self.hash,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineData:
        """Create BaselineData from dictionary."""
        test_results = {}
        for name, result_dict in data.get("test_results", {}).items():
            test_results[name] = TestResult(
                name=result_dict["name"],
                passed=result_dict["passed"],
                failure_type=TestFailureType(result_dict.get("failure_type", "unknown")),
                message=result_dict.get("message"),
                duration=result_dict.get("duration"),
                details=result_dict.get("details"),
            )
        return cls(
            test_results=test_results,
            total_count=data["total_count"],
            passed_count=data["passed_count"],
            failed_count=data["failed_count"],
            hash=data["hash"],
            timestamp=data["timestamp"],
        )


@dataclass
class TestGateResult:
    """Result of test gate verification.

    Attributes:
        passed: True if tests pass within acceptable thresholds.
        mode: The test gate mode used.
        baseline: The baseline data used for comparison (if any).
        current_results: Dict of current test results.
        total_tests: Total number of tests.
        passed_tests: Number of passing tests.
        failed_tests: Number of failing tests.
        new_failures: List of NEW failures (regressions).
        fixed_failures: List of FIXED failures.
        existing_failures: List of pre-existing failures.
        blocked: True if commit should be blocked.
        message: Human-readable message.
    """

    passed: bool
    mode: TestGateMode
    baseline: Optional[BaselineData] = None
    current_results: dict[str, TestResult] = field(default_factory=dict)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    new_failures: list[TestResult] = field(default_factory=list)
    fixed_failures: list[TestResult] = field(default_factory=list)
    existing_failures: list[TestResult] = field(default_factory=list)
    blocked: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "passed": self.passed,
            "mode": self.mode.name,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "current_results": {k: v.to_dict() for k, v in self.current_results.items()},
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "new_failures": [f.to_dict() for f in self.new_failures],
            "fixed_failures": [f.to_dict() for f in self.fixed_failures],
            "existing_failures": [f.to_dict() for f in self.existing_failures],
            "blocked": self.blocked,
            "message": self.message,
        }


class TestGate:
    """Test execution gate with baseline comparison.

    TestGate runs the test suite and compares against a baseline to detect:
    - NEW failures (regressions) = BLOCK commit
    - FIXED failures = bonus (bugs fixed)
    - EXISTING failures = non-blocking (pre-existing issues)

    Usage:
        gate = TestGate(mode=TestGateMode.BASELINE)
        result = gate.run(
            test_paths=["tests/", "src/tests/"],
            baseline_path=".nexus/baseline.json",
            command=["pytest", "tests/", "-v"],
        )
        if result.blocked:
            print("COMMIT BLOCKED: New regressions detected")
            for failure in result.new_failures:
                print(f"  - {failure.name}: {failure.message}")

    Attributes:
        mode: Test gate mode (STRICT/BASELINE/LENIENT).
        baseline: Optional baseline data for comparison.
        baseline_path: Path to load/save baseline data.
        test_command: Default command to run tests.
        timeout: Maximum time for test execution in seconds.
    """

    def __init__(
        self,
        mode: TestGateMode = TestGateMode.BASELINE,
        baseline: Optional[BaselineData] = None,
        baseline_path: Optional[str] = None,
        test_command: Optional[list[str]] = None,
        timeout: int = 300,
    ) -> None:
        """Initialize TestGate.

        Args:
            mode: Test gate mode (STRICT/BASELINE/LENIENT).
            baseline: Optional baseline data for comparison.
            baseline_path: Path to load/save baseline data.
            test_command: Default command to run tests.
            timeout: Maximum time for test execution in seconds.
        """
        self._mode = mode
        self._baseline = baseline
        self._baseline_path = baseline_path
        self._test_command = test_command or ["pytest", "-v"]
        self._timeout = timeout

    @property
    def mode(self) -> TestGateMode:
        """Get test gate mode."""
        return self._mode

    @property
    def baseline(self) -> Optional[BaselineData]:
        """Get current baseline data."""
        return self._baseline

    def set_baseline(self, baseline: BaselineData) -> None:
        """Set baseline data for comparison.

        Args:
            baseline: BaselineData to use for comparison.
        """
        self._baseline = baseline

    def run(
        self,
        test_paths: Optional[list[str]] = None,
        command: Optional[list[str]] = None,
        baseline_override: Optional[BaselineData] = None,
    ) -> TestGateResult:
        """Run tests and compare against baseline.

        Args:
            test_paths: List of test file/directory paths.
            command: Test command to run (overrides default).
            baseline_override: Use this baseline instead of stored one.

        Returns:
            TestGateResult with pass/fail status and details.
        """
        test_cmd = command or self._test_command
        if test_paths:
            test_cmd = test_cmd + test_paths

        # Run tests
        run_result = self._execute_tests(test_cmd)

        # Compare with baseline if available
        baseline = baseline_override or self._baseline

        if baseline:
            return self._compare_with_baseline(run_result, baseline)
        else:
            # No baseline - just report current results
            return self._report_current(run_result)

    def _execute_tests(self, command: list[str]) -> dict[str, TestResult]:
        """Execute test command and parse results.

        Args:
            command: The test command to run.

        Returns:
            Dict of test name to TestResult.
        """
        results: dict[str, TestResult] = {}

        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )

            output = proc.stdout + proc.stderr
            results = self._parse_test_output(output)
            results["_exit_code"] = proc.returncode
            results["_output"] = output

        except subprocess.TimeoutExpired:
            results["_TIMEOUT"] = TestResult(
                name="_TIMEOUT",
                passed=False,
                failure_type=TestFailureType.UNKNOWN,
                message="Test execution timed out",
            )
        except Exception as e:
            results["_ERROR"] = TestResult(
                name="_ERROR",
                passed=False,
                failure_type=TestFailureType.UNKNOWN,
                message=f"Test execution error: {str(e)}",
            )

        return results

    def _parse_test_output(self, output: str) -> dict[str, TestResult]:
        """Parse test output into structured results.

        Args:
            output: Raw test output string.

        Returns:
            Dict of test name to TestResult.
        """
        results: dict[str, TestResult] = {}
        lines = output.split("\n")

        current_test = None
        current_failure = None

        for line in lines:
            # Parse pytest-style output
            if line.startswith("tests/") or line.startswith("src/") or line.startswith("test_"):
                # Test file or function line
                parts = line.split("::")
                if len(parts) >= 2:
                    test_name = parts[1].split("[")[0] if "[" in parts[1] else parts[1]
                    if current_test and current_failure:
                        results[current_test] = current_failure
                    current_test = test_name
                    current_failure = None

            if "PASSED" in line:
                if current_test:
                    results[current_test] = TestResult(
                        name=current_test,
                        passed=True,
                        failure_type=TestFailureType.UNKNOWN,
                    )
                    current_test = None

            elif "FAILED" in line:
                if current_test:
                    # Extract failure message
                    msg_parts = line.split(" - ")
                    message = msg_parts[1] if len(msg_parts) > 1 else "Test failed"
                    current_failure = TestResult(
                        name=current_test,
                        passed=False,
                        failure_type=TestFailureType.UNKNOWN,
                        message=message,
                    )

            elif line.startswith("AssertionError:") or line.startswith("  AssertionError"):
                if current_failure:
                    current_failure.message = (current_failure.message or "") + " " + line.strip()

        # Don't forget the last test
        if current_test and current_failure:
            results[current_test] = current_failure

        # If no structured results, try to parse summary
        if not results and "failed" in output.lower():
            # Count from summary line
            summary_match = None
            for line in lines:
                if "failed" in line.lower() and "passed" in line.lower():
                    summary_match = line
                    break

            if summary_match:
                # This is a fallback - we can't identify specific tests
                results["_SUMMARY"] = TestResult(
                    name="_SUMMARY",
                    passed=False,
                    failure_type=TestFailureType.UNKNOWN,
                    message=summary_match.strip(),
                )

        return results

    def _compare_with_baseline(
        self, current: dict[str, TestResult], baseline: BaselineData
    ) -> TestGateResult:
        """Compare current results against baseline.

        Args:
            current: Current test results.
            baseline: Baseline data to compare against.

        Returns:
            TestGateResult with comparison details.
        """
        new_failures: list[TestResult] = []
        fixed_failures: list[TestResult] = []
        existing_failures: list[TestResult] = []
        current_results: dict[str, TestResult] = {}

        # Filter out meta entries
        for name, result in current.items():
            if name.startswith("_"):
                continue
            current_results[name] = result

        # Find new, fixed, and existing failures
        for name, result in current_results.items():
            if name in baseline.test_results:
                baseline_result = baseline.test_results[name]
                if not result.passed and baseline_result.passed:
                    # Was passing, now failing = REGRESSION
                    result.failure_type = TestFailureType.REGRESSION
                    new_failures.append(result)
                elif result.passed and not baseline_result.passed:
                    # Was failing, now passing = FIXED
                    result.failure_type = TestFailureType.FIXED
                    fixed_failures.append(result)
                elif not result.passed and not baseline_result.passed:
                    # Still failing = EXISTING
                    result.failure_type = TestFailureType.EXISTING
                    existing_failures.append(result)
            else:
                # New test that wasn't in baseline
                if not result.passed:
                    result.failure_type = TestFailureType.NEW
                    new_failures.append(result)

        # Count totals
        total_tests = len(current_results)
        passed_tests = sum(1 for r in current_results.values() if r.passed)
        failed_tests = sum(1 for r in current_results.values() if not r.passed)

        # Determine if we should block
        blocked = False
        message = ""

        if self._mode == TestGateMode.STRICT:
            # Block on ANY failure
            if failed_tests > 0:
                blocked = True
                message = f"STRICT mode: {failed_tests} test(s) failing - commit blocked"
        elif self._mode == TestGateMode.BASELINE:
            # Only block if NEW failures exceed baseline
            if new_failures:
                blocked = True
                message = f"BASELINE mode: {len(new_failures)} new regression(s) - commit blocked"
            elif failed_tests > baseline.failed_count:
                blocked = True
                message = f"BASELINE mode: {failed_tests} failures exceed baseline {baseline.failed_count}"
            else:
                message = f"BASELINE mode: {failed_tests} failures within baseline {baseline.failed_count}"
        else:  # LENIENT
            message = f"LENIENT mode: {failed_tests} failure(s), {len(new_failures)} new, {len(fixed_failures)} fixed"

        return TestGateResult(
            passed=len(new_failures) == 0,
            mode=self._mode,
            baseline=baseline,
            current_results=current_results,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            new_failures=new_failures,
            fixed_failures=fixed_failures,
            existing_failures=existing_failures,
            blocked=blocked,
            message=message,
        )

    def _report_current(self, current: dict[str, TestResult]) -> TestGateResult:
        """Report current test results without baseline comparison.

        Args:
            current: Current test results.

        Returns:
            TestGateResult with current status.
        """
        results: dict[str, TestResult] = {}
        for name, result in current.items():
            if name.startswith("_"):
                continue
            results[name] = result

        total_tests = len(results)
        passed_tests = sum(1 for r in results.values() if r.passed)
        failed_tests = sum(1 for r in results.values() if not r.passed)

        return TestGateResult(
            passed=failed_tests == 0,
            mode=self._mode,
            current_results=results,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            blocked=failed_tests > 0 if self._mode == TestGateMode.STRICT else False,
            message=f"No baseline: {failed_tests}/{total_tests} tests failing",
        )

    def save_baseline(self, path: Optional[str] = None) -> None:
        """Save current baseline data to file.

        Args:
            path: Path to save baseline (uses self._baseline_path if None).
        """
        if not self._baseline:
            raise ValueError("No baseline data to save")

        save_path = path or self._baseline_path
        if not save_path:
            raise ValueError("No baseline path specified")

        with open(save_path, "w") as f:
            json.dump(self._baseline.to_dict(), f, indent=2)

    def load_baseline(self, path: Optional[str] = None) -> BaselineData:
        """Load baseline data from file.

        Args:
            path: Path to load baseline from (uses self._baseline_path if None).

        Returns:
            Loaded BaselineData.
        """
        load_path = path or self._baseline_path
        if not load_path:
            raise ValueError("No baseline path specified")

        with open(load_path, "r") as f:
            data = json.load(f)

        self._baseline = BaselineData.from_dict(data)
        return self._baseline

    def capture_baseline(
        self,
        test_paths: Optional[list[str]] = None,
        command: Optional[list[str]] = None,
    ) -> BaselineData:
        """Capture current test state as new baseline.

        Args:
            test_paths: List of test file/directory paths.
            command: Test command to run.

        Returns:
            BaselineData representing current test state.
        """
        results = self.run(test_paths=test_paths, command=command)

        # Calculate hash of results
        results_json = json.dumps(
            {k: v.to_dict() for k, v in results.current_results.items()},
            sort_keys=True,
        )
        results_hash = hashlib.sha256(results_json.encode()).hexdigest()[:16]

        baseline = BaselineData(
            test_results=results.current_results,
            total_count=results.total_tests,
            passed_count=results.passed_tests,
            failed_count=results.failed_tests,
            hash=results_hash,
            timestamp=subprocess.run(
                ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                capture_output=True,
                text=True,
            ).stdout.strip(),
        )

        self._baseline = baseline
        return baseline
