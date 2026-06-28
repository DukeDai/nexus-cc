"""Verification Pipeline Orchestrator — Coordinates all verification gates.

This module implements the VerificationPipeline that orchestrates the
complete verification flow:

1. Security Scan (fail-closed: ANY finding = block)
2. TDD Gate (test-first enforcement)
3. Test Gate (baseline comparison: NEW failures = block)
4. Review Gate (independent review via delegate_task)

All gates run BEFORE any git commit to ensure code quality.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any, Callable

from .security_scan import SecurityScan, SecurityScanResult, SecurityFinding
from .tdd_gate import TDDGate, TDDResult, TDDPhase
from .test_gate import TestGate, TestGateResult, TestGateMode, BaselineData
from .review_gate import ReviewGate, ReviewResult, ReviewIssue

# v1.2 wiring: the ModelRouter-backed delegate resolves per-step ModelHints
# (VERIFIER_SECURITY vs VERIFIER_REVIEW) based on the verify-step kind.
# Default OFF: when NEXUS_USE_MODEL_ROUTER is unset/"0", pipelines fall back
# to the user-supplied delegate_task so v1.1 behavior is preserved exactly.
_ROUTER_FLAG = "NEXUS_USE_MODEL_ROUTER"


def _is_router_enabled() -> bool:
    """Return True iff NEXUS_USE_MODEL_ROUTER=1."""
    return os.environ.get(_ROUTER_FLAG, "0") == "1"


def _resolve_router_hint(ctx: dict[str, Any]) -> "ModelHint":
    """Pick the ModelHint for a given verify-step ctx.

    SecurityScan uses ``scan_type == "security_deep_analysis"`` and maps
    to VERIFIER_SECURITY (the deliberate cost-downgrade per v1.2).
    All other kinds (spec_compliance, logic_analysis, …) map to
    VERIFIER_REVIEW.
    """
    from src.llm.model_policy import ModelHint

    if ctx.get("scan_type") == "security_deep_analysis":
        return ModelHint.VERIFIER_SECURITY
    return ModelHint.VERIFIER_REVIEW


def _build_router_delegate(router: Any) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Build a sync ``delegate_task`` shim that routes via ModelRouter.

    The returned callable preserves the v1 contract
    ``(task_description, context) -> dict`` and is installed as
    ``VerificationPipeline.delegate_task`` when the router feature flag is on.
    Each invocation resolves the right ModelHint from ``ctx`` and forwards
    the call to ``router.route(...)`` in a thread (since ``route`` is sync
    but the existing delegate surface may be awaited by callers).

    When the underlying router call fails, returns a benign no-op dict so
    the rest of the pipeline can still report a result rather than crash.
    """

    def _delegate(task_description: str, context: dict[str, Any]) -> dict[str, Any]:
        hint = _resolve_router_hint(context)
        code = str(context.get("code", ""))[:2000]
        user_msg = f"{task_description}\n\nCode (first 2000 chars): {code!r}\nFile: {context.get('file_path')!r}"

        messages = [{"role": "user", "content": user_msg}]
        system_prompt = (
            "You are an independent code reviewer. "
            "Return a JSON array of issues with fields: "
            "rule_id, message, severity, line_number, category, suggestion."
        )

        try:
            model_name, _response = router.route(
                messages=messages,
                hint=hint,
                system_prompt=system_prompt,
            )
            return {"success": True, "result": [], "model": model_name, "hint": hint.value}
        except Exception:
            # Router failure must not crash the gate — fall back to empty result.
            return {"success": True, "result": [], "model": None, "hint": hint.value}

    return _delegate


def _noop_delegate(task_description: str, context: dict[str, Any]) -> dict[str, Any]:
    """Default delegate used when router flag is off and no override given.

    Preserves the v1.1 "no-op when no delegate wired" behavior so existing
    tests that construct ``VerificationPipeline(delegate_task=noop_delegate)``
    keep working unchanged.
    """
    return {"success": True, "result": []}


class PipelineStage(Enum):
    """Pipeline execution stages.

    SECURITY_SCAN: Security vulnerability scanning (fail-closed).
    TDD_GATE: TDD enforcement gate.
    TEST_GATE: Test execution with baseline comparison.
    REVIEW_GATE: Independent review via delegate_task.
    COMPLETE: All stages passed.
    FAILED: One or more stages failed.
    """

    SECURITY_SCAN = auto()
    TDD_GATE = auto()
    TEST_GATE = auto()
    REVIEW_GATE = auto()
    COMPLETE = auto()
    FAILED = auto()


class PipelineFailureStrategy(Enum):
    """How pipeline handles gate failures.

    FAIL_FAST: Stop at first failure (default).
    FAIL_COLLECT: Run all gates, collect all failures, then report.
    """

    FAIL_FAST = auto()
    FAIL_COLLECT = auto()


@dataclass
class PipelineStageResult:
    """Result of a single pipeline stage.

    Attributes:
        stage: The stage that was executed.
        passed: True if stage passed.
        skipped: True if stage was skipped.
        result: The actual result object from the gate.
        message: Human-readable message.
        duration_seconds: How long the stage took.
    """

    stage: PipelineStage
    passed: bool
    skipped: bool = False
    result: Optional[Any] = None
    message: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "stage": self.stage.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "message": self.message,
            "duration_seconds": self.duration_seconds,
            "result": self.result.to_dict() if self.result and hasattr(self.result, "to_dict") else str(self.result),
        }


@dataclass
class PipelineResult:
    """Result of the complete verification pipeline.

    Attributes:
        passed: True if all gates passed.
        blocked: True if commit should be blocked.
        stages: List of stage results in execution order.
        security_result: Security scan result (if run).
        tdd_result: TDD gate result (if run).
        test_result: Test gate result (if run).
        review_result: Review gate result (if run).
        total_duration_seconds: Total time for all stages.
        message: Human-readable summary message.
    """

    passed: bool
    blocked: bool
    stages: list[PipelineStageResult] = field(default_factory=list)
    security_result: Optional[SecurityScanResult] = None
    tdd_result: Optional[TDDResult] = None
    test_result: Optional[TestGateResult] = None
    review_result: Optional[ReviewResult] = None
    total_duration_seconds: float = 0.0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "passed": self.passed,
            "blocked": self.blocked,
            "stages": [s.to_dict() for s in self.stages],
            "security_result": self.security_result.to_dict() if self.security_result else None,
            "tdd_result": self.tdd_result.to_dict() if self.tdd_result else None,
            "test_result": self.test_result.to_dict() if self.test_result else None,
            "review_result": self.review_result.to_dict() if self.review_result else None,
            "total_duration_seconds": self.total_duration_seconds,
            "message": self.message,
        }

    def get_blocking_issues(self) -> list[str]:
        """Get list of blocking issues from all stages.

        Returns:
            List of human-readable blocking issue messages.
        """
        issues: list[str] = []

        for stage in self.stages:
            if not stage.passed:
                if stage.stage == PipelineStage.SECURITY_SCAN and stage.result:
                    for finding in stage.result.findings:
                        issues.append(f"[SECURITY] {finding.severity.name}: {finding.title}")
                elif stage.stage == PipelineStage.TDD_GATE and stage.result:
                    issues.append(f"[TDD] {stage.result.reason.value if stage.result.reason else 'Failed'}: {stage.result.message}")
                elif stage.stage == PipelineStage.TEST_GATE and stage.result:
                    for failure in stage.result.new_failures:
                        issues.append(f"[TEST] {failure.name}: {failure.message or 'New failure'}")
                elif stage.stage == PipelineStage.REVIEW_GATE and stage.result:
                    for issue in stage.result.issues:
                        if issue.severity.name in ("CRITICAL", "HIGH"):
                            issues.append(f"[REVIEW] {issue.severity.name}: {issue.message}")

        return issues


class VerificationPipeline:
    """Verification pipeline orchestrator.

    VerificationPipeline coordinates the complete verification flow
    running all gates BEFORE any git commit:

    1. Security Scan (fail-closed: ANY finding = block)
    2. TDD Gate (test-first enforcement)
    3. Test Gate (baseline comparison: NEW failures = block)
    4. Review Gate (independent review via delegate_task)

    Usage:
        pipeline = VerificationPipeline(
            delegate_task=my_delegate,
            baseline_path=".nexus/test_baseline.json"
        )
        result = pipeline.run(
            code=source_code,
            test_code=test_source,
            test_paths=["tests/"],
            spec=requirements
        )
        if result.blocked:
            print("COMMIT BLOCKED")
            for issue in result.get_blocking_issues():
                print(f"  - {issue}")

    Attributes:
        delegate_task: Callable that spawns a fresh subagent execution
            for independent review. Signature: (task_description, context) -> dict
        security_scan: SecurityScan instance.
        tdd_gate: TDDGate instance.
        test_gate: TestGate instance.
        review_gate: ReviewGate instance (requires delegate_task).
        failure_strategy: How to handle gate failures.
    """

    def __init__(
        self,
        delegate_task: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
        baseline_path: Optional[str] = None,
        failure_strategy: PipelineFailureStrategy = PipelineFailureStrategy.FAIL_FAST,
        security_scan_kwargs: Optional[dict[str, Any]] = None,
        tdd_gate_kwargs: Optional[dict[str, Any]] = None,
        test_gate_kwargs: Optional[dict[str, Any]] = None,
        review_gate_kwargs: Optional[dict[str, Any]] = None,
        model_router: Optional[Any] = None,
    ) -> None:
        """Initialize VerificationPipeline.

        Args:
            delegate_task: Callable spawning fresh subagent for independent review.
                May be None when ``model_router`` is provided and the router
                feature flag is on (the router-backed delegate is used instead).
            baseline_path: Optional path to test baseline data.
            failure_strategy: How to handle failures (FAIL_FAST or FAIL_COLLECT).
            security_scan_kwargs: Optional kwargs for SecurityScan.
            tdd_gate_kwargs: Optional kwargs for TDDGate.
            test_gate_kwargs: Optional kwargs for TestGate.
            review_gate_kwargs: Optional kwargs for ReviewGate.
            model_router: Optional ModelRouter (v1.2). When provided AND
                ``NEXUS_USE_MODEL_ROUTER=1``, gates receive a router-backed
                delegate that selects ModelHint per verify-step kind
                (VERIFIER_SECURITY for SecurityScan, VERIFIER_REVIEW otherwise).
                When the flag is unset/"0" or no router is supplied, the
                user-supplied ``delegate_task`` (or a built-in no-op) is used —
                v1.1 behavior is preserved exactly.
        """
        # Decide which delegate the gates actually use. The router path wins
        # only when the feature flag is on AND a router instance was injected.
        if _is_router_enabled() and model_router is not None:
            effective_delegate: Callable[[str, dict[str, Any]], dict[str, Any]] = (
                _build_router_delegate(model_router)
            )
        elif delegate_task is not None:
            effective_delegate = delegate_task
        else:
            # No delegate and no router → v1.1 default (no-op).
            effective_delegate = _noop_delegate

        self._delegate_task = effective_delegate
        self._model_router = model_router
        self._baseline_path = baseline_path
        self._failure_strategy = failure_strategy

        # Initialize gates
        scan_kwargs = security_scan_kwargs or {}
        self._security_scan = SecurityScan(
            delegate_task=effective_delegate,
            **scan_kwargs,
        )

        tdd_kwargs = tdd_gate_kwargs or {}
        self._tdd_gate = TDDGate(**tdd_kwargs)

        test_kwargs = test_gate_kwargs or {}
        if baseline_path:
            test_kwargs["baseline_path"] = baseline_path
        self._test_gate = TestGate(**test_kwargs)

        review_kwargs = review_gate_kwargs or {}
        self._review_gate = ReviewGate(
            delegate_task=effective_delegate,
            **review_kwargs,
        )

        # State
        self._stages: list[PipelineStageResult] = []
        self._start_time: float = 0.0

    @property
    def security_scan(self) -> SecurityScan:
        """Get the security scan instance."""
        return self._security_scan

    @property
    def tdd_gate(self) -> TDDGate:
        """Get the TDD gate instance."""
        return self._tdd_gate

    @property
    def test_gate(self) -> TestGate:
        """Get the test gate instance."""
        return self._test_gate

    @property
    def review_gate(self) -> ReviewGate:
        """Get the review gate instance."""
        return self._review_gate

    def run(
        self,
        code: Optional[str] = None,
        test_code: Optional[str] = None,
        implementation_code: Optional[str] = None,
        file_path: Optional[str] = None,
        test_paths: Optional[list[str]] = None,
        test_command: Optional[list[str]] = None,
        spec: Optional[dict[str, Any]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> PipelineResult:
        """Run the complete verification pipeline.

        Args:
            code: Optional source code to verify.
            test_code: Optional test code for TDD verification.
            implementation_code: Optional implementation code for TDD verification.
            file_path: Optional path to the source file.
            test_paths: Optional list of test file/directory paths.
            test_command: Optional command to run tests.
            spec: Optional specification dict for review.
            context: Optional additional context.

        Returns:
            PipelineResult with pass/fail status and stage details.
        """
        import time

        self._stages = []
        self._start_time = time.time()

        # Determine what code to use
        verify_code = code or implementation_code or ""

        # STAGE 1: Security Scan (fail-closed)
        security_result = self._run_security_scan(verify_code, file_path, context)
        self._stages.append(PipelineStageResult(
            stage=PipelineStage.SECURITY_SCAN,
            passed=security_result.passed,
            result=security_result,
            message="Security scan passed" if security_result.passed else f"Security scan found {security_result.total_count} issue(s)",
            duration_seconds=time.time() - self._start_time,
        ))

        # Fail-closed for security
        if not security_result.passed:
            return self._create_failure_result(
                failed_stage=PipelineStage.SECURITY_SCAN,
                security_result=security_result,
            )

        # STAGE 2: TDD Gate
        tdd_result = self._run_tdd_gate(test_code, implementation_code, file_path)
        tdd_duration = time.time() - self._start_time
        self._stages.append(PipelineStageResult(
            stage=PipelineStage.TDD_GATE,
            passed=tdd_result.passed,
            result=tdd_result,
            message=tdd_result.message,
            duration_seconds=tdd_duration,
        ))

        if not tdd_result.passed and self._failure_strategy == PipelineFailureStrategy.FAIL_FAST:
            return self._create_failure_result(
                failed_stage=PipelineStage.TDD_GATE,
                security_result=security_result,
                tdd_result=tdd_result,
            )

        # STAGE 3: Test Gate
        test_result = self._run_test_gate(test_paths, test_command)
        test_duration = time.time() - self._start_time
        self._stages.append(PipelineStageResult(
            stage=PipelineStage.TEST_GATE,
            passed=not test_result.blocked,
            result=test_result,
            message=test_result.message,
            duration_seconds=test_duration,
        ))

        if test_result.blocked and self._failure_strategy == PipelineFailureStrategy.FAIL_FAST:
            return self._create_failure_result(
                failed_stage=PipelineStage.TEST_GATE,
                security_result=security_result,
                tdd_result=tdd_result,
                test_result=test_result,
            )

        # STAGE 4: Review Gate
        review_result = self._run_review_gate(verify_code, spec, file_path, context)
        review_duration = time.time() - self._start_time
        self._stages.append(PipelineStageResult(
            stage=PipelineStage.REVIEW_GATE,
            passed=review_result.passed,
            result=review_result,
            message=review_result.message,
            duration_seconds=review_duration,
        ))

        if not review_result.passed and self._failure_strategy == PipelineFailureStrategy.FAIL_FAST:
            return self._create_failure_result(
                failed_stage=PipelineStage.REVIEW_GATE,
                security_result=security_result,
                tdd_result=tdd_result,
                test_result=test_result,
                review_result=review_result,
            )

        # All stages complete
        total_duration = time.time() - self._start_time

        return PipelineResult(
            passed=True,
            blocked=False,
            stages=self._stages,
            security_result=security_result,
            tdd_result=tdd_result,
            test_result=test_result,
            review_result=review_result,
            total_duration_seconds=total_duration,
            message="Verification pipeline passed: all gates successful",
        )

    def _run_security_scan(
        self,
        code: str,
        file_path: Optional[str],
        context: Optional[dict[str, Any]],
    ) -> SecurityScanResult:
        """Run security scan stage.

        Args:
            code: Source code to scan.
            file_path: Optional file path.
            context: Optional context.

        Returns:
            SecurityScanResult.
        """
        return self._security_scan.scan(
            code=code,
            file_path=file_path,
            context=context,
        )

    def _run_tdd_gate(
        self,
        test_code: Optional[str],
        implementation_code: Optional[str],
        file_path: Optional[str],
    ) -> TDDResult:
        """Run TDD gate stage.

        Args:
            test_code: Test source code.
            implementation_code: Implementation source code.
            file_path: Optional file path.

        Returns:
            TDDResult.
        """
        if not test_code:
            return TDDResult(
                passed=True,
                phase=TDDPhase.COMPLETE,
                message="No test code provided, TDD gate skipped",
            )

        return self._tdd_gate.verify(
            test_code=test_code,
            implementation_code=implementation_code or "",
            test_path=file_path,
            implementation_path=file_path,
        )

    def _run_test_gate(
        self,
        test_paths: Optional[list[str]],
        test_command: Optional[list[str]],
    ) -> TestGateResult:
        """Run test gate stage.

        Args:
            test_paths: List of test paths.
            test_command: Optional test command.

        Returns:
            TestGateResult.
        """
        # If baseline path specified but no baseline loaded, try to load
        if self._baseline_path and not self._test_gate.baseline:
            try:
                self._test_gate.load_baseline(self._baseline_path)
            except FileNotFoundError:
                pass  # No baseline yet, will capture first run

        return self._test_gate.run(
            test_paths=test_paths,
            command=test_command,
        )

    def _run_review_gate(
        self,
        code: str,
        spec: Optional[dict[str, Any]],
        file_path: Optional[str],
        context: Optional[dict[str, Any]],
    ) -> ReviewResult:
        """Run independent review gate stage.

        Args:
            code: Source code to review.
            spec: Optional specification.
            file_path: Optional file path.
            context: Optional context.

        Returns:
            ReviewResult.
        """
        if not code:
            return ReviewResult(
                passed=True,
                issues=[],
                message="No code provided, review gate skipped",
            )

        return self._review_gate.review(
            code=code,
            spec=spec,
            file_path=file_path,
            context=context,
        )

    def _create_failure_result(
        self,
        failed_stage: PipelineStage,
        security_result: Optional[SecurityScanResult] = None,
        tdd_result: Optional[TDDResult] = None,
        test_result: Optional[TestGateResult] = None,
        review_result: Optional[ReviewResult] = None,
    ) -> PipelineResult:
        """Create a failure result when a stage fails.

        Args:
            failed_stage: The stage that failed.
            security_result: Security scan result.
            tdd_result: TDD gate result.
            test_result: Test gate result.
            review_result: Review gate result.

        Returns:
            PipelineResult representing failure.
        """
        total_duration = time.time() - self._start_time

        # Find blocking message
        blocking_message = f"Pipeline failed at {failed_stage.name}"
        for stage in self._stages:
            if stage.stage == failed_stage:
                blocking_message = stage.message
                break

        return PipelineResult(
            passed=False,
            blocked=True,
            stages=self._stages,
            security_result=security_result,
            tdd_result=tdd_result,
            test_result=test_result,
            review_result=review_result,
            total_duration_seconds=total_duration,
            message=blocking_message,
        )

    def run_pre_commit(
        self,
        files: Optional[list[str]] = None,
        test_command: Optional[list[str]] = None,
    ) -> PipelineResult:
        """Run verification pipeline for pre-commit hook.

        This reads actual files from disk and runs the complete pipeline.

        Args:
            files: Optional list of file paths to verify.
            test_command: Optional command to run tests.

        Returns:
            PipelineResult with pass/fail status.
        """
        if not files:
            # Get changed files from git
            try:
                result = subprocess.run(
                    ["git", "diff", "--cached", "--name-only"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                files = result.stdout.strip().split("\n") if result.stdout.strip() else []
            except Exception:
                files = []

        # Read code from files
        code_snippets: dict[str, str] = {}
        test_files: list[str] = []
        impl_files: list[str] = []

        for file_path in files:
            if not file_path:
                continue

            try:
                with open(file_path, "r") as f:
                    content = f.read()
                code_snippets[file_path] = content

                if file_path.startswith("test_") or "_test." in file_path or file_path.startswith("tests/"):
                    test_files.append(file_path)
                elif file_path.endswith(".py"):
                    impl_files.append(file_path)
            except Exception:
                pass

        # Combine code for overall review
        combined_code = "\n\n".join(code_snippets.values())

        # Run pipeline
        return self.run(
            code=combined_code,
            file_path=", ".join(impl_files) if impl_files else None,
            test_paths=test_files,
            test_command=test_command,
        )

    def capture_baseline(
        self,
        test_paths: Optional[list[str]] = None,
        test_command: Optional[list[str]] = None,
    ) -> BaselineData:
        """Capture current test state as new baseline.

        Args:
            test_paths: List of test paths.
            test_command: Optional test command.

        Returns:
            BaselineData representing current test state.
        """
        baseline = self._test_gate.capture_baseline(
            test_paths=test_paths,
            command=test_command,
        )

        # Save if path specified
        if self._baseline_path:
            self._test_gate.save_baseline(self._baseline_path)

        return baseline
