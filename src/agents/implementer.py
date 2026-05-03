"""ImplementerAgent - Test-Driven Development Implementation.

Per SPEC.md Section 3.2, the ImplementerAgent follows TDD methodology:
    1. Write a failing test first
    2. Implement the minimal code to pass
    3. Verify with the test
    4. Refactor if needed

Key TDD Principles:
    - Red: Write failing test before any implementation
    - Green: Write minimal code to make test pass
    - Refactor: Improve code while keeping tests green

Model Tier Selection:
    - FAST: Simple implementations, single function/class
    - SONNET: Normal complexity, multiple components
    - OPUS: Complex systems, architecture decisions

Responsibilities:
    - Generate failing test from specification
    - Implement minimal code to pass test
    - Verify implementation against spec
    - Enforce TDD discipline
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

from .base import AgentResult, BaseAgent, ModelTier, AgentRole


class TDDPhase(Enum):
    """TDD red-green-refactor phases."""
    RED = auto()      # Write failing test
    GREEN = auto()     # Make test pass
    REFACTOR = auto()  # Improve code


@dataclass
class TDDContext:
    """Context for TDD workflow.

    Attributes:
        phase: Current TDD phase.
        spec: The specification to implement.
        test_code: Generated test code.
        implementation: Generated implementation code.
        test_output: Output from running tests.
        failures: Number of test failures.
        passes: Number of test passes.
    """
    phase: TDDPhase = TDDPhase.RED
    spec: str = ""
    test_code: str = ""
    implementation: str = ""
    test_output: str = ""
    failures: int = 0
    passes: int = 0


class ImplementerAgent(BaseAgent):
    """Agent that implements code using Test-Driven Development.

    The ImplementerAgent takes a specification and produces working code
    by first writing failing tests, then implementing to make them pass.

    TDD Workflow:
        1. Parse specification
        2. RED phase: Generate failing test(s)
        3. GREEN phase: Generate minimal implementation
        4. Run tests to verify
        5. REFACTOR phase: Clean up if needed

    Usage:
        agent = ImplementerAgent()
        result = agent.execute({
            "spec": "# User Auth Spec...",
            "language": "python",
            "test_framework": "pytest",
        })

    Attributes:
        tdd_strict: Enforce strict TDD (no implementation until test fails).
        test_framework: Preferred test framework (pytest, unittest, etc.).
        language: Target implementation language.
    """

    def __init__(
        self,
        model_tier: ModelTier = ModelTier.SONNET,
        tdd_strict: bool = True,
        test_framework: str = "pytest",
        language: str = "python",
        **kwargs,
    ):
        """Initialize ImplementerAgent.

        Args:
            model_tier: Default model tier.
            tdd_strict: If True, enforce test-first methodology.
            test_framework: Test framework to use.
            language: Target implementation language.
        """
        super().__init__(
            role=AgentRole.IMPLEMENTER,
            model_tier=model_tier,
            tools=["test_generator", "code_generator", "tdd_enforcer"],
            **kwargs,
        )
        self.tdd_strict = tdd_strict
        self.test_framework = test_framework
        self.language = language

    def select_model_tier(self, task: dict[str, Any]) -> ModelTier:
        """Select model tier based on implementation complexity.

        Args:
            task: Task dict with 'spec' and other context.

        Returns:
            ModelTier based on spec complexity:
                - Trivial: Simple, single-component specs
                - Complex: Multi-component, architecture specs
                - Normal: Standard implementation tasks
        """
        spec = task.get("spec", "").lower()
        context = task.get("context", {})

        # Check for complexity indicators
        complexity_indicators = [
            "architecture", "system", "framework", "api",
            "database", "concurrent", "async", "distributed",
            "multiple", "several", "complex", "advanced",
        ]

        word_count = len(spec.split())
        indicator_count = sum(1 for ind in complexity_indicators if ind in spec)

        if word_count < 50 and indicator_count == 0:
            return ModelTier.FAST
        elif indicator_count >= 2 or word_count > 500:
            return ModelTier.OPUS
        return ModelTier.SONNET

    def execute(self, task: dict[str, Any]) -> AgentResult:
        """Execute TDD implementation workflow.

        Args:
            task: Dict with:
                - spec: Specification document (required)
                - language: Target language (default: python)
                - test_framework: Test framework (default: pytest)
                - context: Additional context dict

        Returns:
            AgentResult with:
                - success: Whether implementation passed all tests
                - confidence: 0.0-1.0 based on test coverage and quality
                - output: Implementation code
                - errors: Any errors encountered
                - metadata: Contains test_code, test_output, tdd_phases
        """
        start_time = time.time()
        errors = []

        # Validate task
        if err := self._validate_task(task):
            return AgentResult(
                success=False,
                confidence=0.0,
                errors=[err],
                agent_id=self.agent_id,
            )

        spec = task["spec"]
        language = task.get("language", self.language)
        test_framework = task.get("test_framework", self.test_framework)
        context = task.get("context", {})

        # Initialize TDD context
        tdd_context = TDDContext(spec=spec)

        # Select model tier based on complexity
        tier = self.select_model_tier(task)

        # Phase 1: RED - Generate failing test
        red_task = {
            "spec": spec,
            "language": language,
            "test_framework": test_framework,
            "context": context,
            "phase": TDDPhase.RED.name,
        }

        red_result = self.delegate_task(red_task, model_tier=tier)
        if not red_result.success:
            errors.append(f"RED phase failed: {red_result.errors}")
            tdd_context.test_code = red_result.output
        else:
            tdd_context.test_code = red_result.output
            tdd_context.phase = TDDPhase.GREEN

        # Phase 2: GREEN - Generate implementation
        green_task = {
            "spec": spec,
            "test_code": tdd_context.test_code,
            "language": language,
            "context": context,
            "phase": TDDPhase.GREEN.name,
        }

        green_result = self.delegate_task(green_task, model_tier=tier)
        if not green_result.success:
            errors.append(f"GREEN phase failed: {green_result.errors}")
            tdd_context.implementation = green_result.output
        else:
            tdd_context.implementation = green_result.output

        # Phase 3: VERIFY - Run tests (delegate to subagent)
        verify_task = {
            "test_code": tdd_context.test_code,
            "implementation": tdd_context.implementation,
            "language": language,
            "test_framework": test_framework,
        }

        verify_result = self.delegate_task(verify_task, model_tier=ModelTier.FAST)
        if verify_result.success:
            tdd_context.test_output = verify_result.output
            tdd_context.failures = verify_result.metadata.get("failures", 0)
            tdd_context.passes = verify_result.metadata.get("passes", 0)
        else:
            errors.append(f"Verification failed: {verify_result.errors}")
            tdd_context.test_output = verify_result.output

        # Phase 4: REFACTOR - If time permits and tests pass
        if tdd_context.failures == 0 and self._should_refactor(spec):
            refactor_task = {
                "spec": spec,
                "implementation": tdd_context.implementation,
                "test_code": tdd_context.test_code,
                "language": language,
                "phase": TDDPhase.REFACTOR.name,
            }
            refactor_result = self.delegate_task(refactor_task, model_tier=tier)
            if refactor_result.success:
                tdd_context.implementation = refactor_result.output
                tdd_context.phase = TDDPhase.REFACTOR

        duration = time.time() - start_time

        # Calculate confidence
        confidence = self._calculate_confidence(tdd_context)

        return AgentResult(
            success=tdd_context.failures == 0 and bool(tdd_context.implementation),
            confidence=confidence,
            output=tdd_context.implementation,
            errors=errors,
            agent_id=self.agent_id,
            duration_seconds=duration,
            metadata={
                "test_code": tdd_context.test_code,
                "test_output": tdd_context.test_output,
                "tdd_phase": tdd_context.phase.name,
                "failures": tdd_context.failures,
                "passes": tdd_context.passes,
                "language": language,
                "test_framework": test_framework,
            },
        )

    def _validate_task(self, task: dict[str, Any]) -> Optional[str]:
        """Validate task has required fields for implementation."""
        if "spec" not in task:
            return "Task missing required field: 'spec'"
        if not isinstance(task["spec"], str):
            return "'spec' must be a string"
        if not task["spec"].strip():
            return "'spec' cannot be empty"
        return None

    def _should_refactor(self, spec: str) -> bool:
        """Determine if refactoring phase should be attempted.

        Args:
            spec: Specification document.

        Returns:
            True if spec suggests complexity worth refactoring.
        """
        refactor_keywords = ["multiple", "several", "complex", "advanced", "optimize"]
        return any(kw in spec.lower() for kw in refactor_keywords)

    def _calculate_confidence(self, ctx: TDDContext) -> float:
        """Calculate confidence based on TDD workflow quality.

        Args:
            ctx: TDD context with phase results.

        Returns:
            Confidence score 0.0-1.0.
        """
        if not ctx.implementation:
            return 0.0

        # Base confidence from test results
        if ctx.failures > 0:
            return max(0.0, 0.3 - (ctx.failures * 0.1))

        # Passed tests = base confidence
        base = 0.7

        # Additional factors
        if ctx.test_code:
            base += 0.1  # Has tests
        if ctx.phase == TDDPhase.REFACTOR:
            base += 0.1  # Completed refactor
        if ctx.passes >= 3:
            base += 0.05  # Good test coverage

        return min(1.0, base)

    def generate_test_template(
        self,
        spec: str,
        language: str = "python",
        test_framework: str = "pytest",
    ) -> str:
        """Generate test template from specification.

        Args:
            spec: Specification document.
            language: Target language.
            test_framework: Test framework to use.

        Returns:
            Test code template string.
        """
        # Extract function/class names from spec
        name_pattern = r'(?:class|def|function|method)\s+(\w+)'
        names = re.findall(name_pattern, spec)

        if not names:
            names = ["example"]

        template = {
            "python": f'''"""Tests for implementation."""
import pytest

# Test class/function: {names[0]}


class Test{self._to_class_name(names[0])}:
    """Test suite for {names[0]}."""

    def test_{names[0]}_basic(self):
        """Basic test case."""
        # TODO: Implement test
        assert False, "Test not implemented"
''',
            "javascript": f'''// Tests for implementation

describe('{names[0]}', () => {{
    it('should work', () => {{
        // TODO: Implement test
        expect(false).toBe(true);
    }});
}});
''',
        }

        return template.get(language, template["python"])

    def _to_class_name(self, name: str) -> str:
        """Convert function name to PascalCase class name."""
        return "".join(word.capitalize() for word in re.split(r'[_\-]', name))
