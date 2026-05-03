"""SpecifierAgent - Requirements to Specification.

Per SPEC.md Section 3.2, the SpecifierAgent transforms natural language
requirements into structured specifications.

Model Tier Selection:
    - FAST (Haiku): Trivial tasks (boilerplate, simple functions)
    - SONNET (Sonnet): Normal complexity tasks
    - OPUS (Opus): Complex architecture or ambiguous requirements

Responsibilities:
    - Parse and clarify requirements
    - Generate structured specification document
    - Ensure spec is testable/verifiable
    - Return confidence-weighted result
"""

from __future__ import annotations

import re
import time
from typing import Any

from .base import AgentResult, BaseAgent, ModelTier, AgentRole


# Complexity indicators for model tier selection
COMPLEXITY_KEYWORDS_TRIVIAL = {
    "add", "create", "simple", "basic", "hello", "print",
    "return", "function", "method", "class", "variable",
    "constant", "enum", "type", "alias",
}

COMPLEXITY_KEYWORDS_COMPLEX = {
    "architecture", "design", "system", "framework", "protocol",
    "distributed", "concurrent", "parallel", "async", "performance",
    "optimize", "scale", "security", "authentication", "authorization",
    "database", "migration", "api", "rest", "graphql", "websocket",
    "microservice", "container", "deployment", "pipeline",
}


class SpecifierAgent(BaseAgent):
    """Agent that converts requirements into structured specifications.

    The SpecifierAgent analyzes natural language requirements and produces
    a detailed specification document that can be used by the ImplementerAgent.

    Specification Format:
        # Title
        ## Overview
        ## Functionality
        ### Features
        ### User Interactions
        ### Data Handling
        ### Edge Cases
        ## Acceptance Criteria
        ## Technical Notes

    Usage:
        agent = SpecifierAgent()
        result = agent.execute({"requirements": "Build a user auth system"})

    Attributes:
        include_examples: Include code examples in spec.
        validation_strictness: How strictly to validate requirements.
    """

    def __init__(
        self,
        model_tier: ModelTier = ModelTier.SONNET,
        include_examples: bool = True,
        validation_strictness: float = 0.8,
        **kwargs,
    ):
        """Initialize SpecifierAgent.

        Args:
            model_tier: Default model tier for this agent.
            include_examples: Include code examples in generated spec.
            validation_strictness: 0.0-1.0, how strictly to validate spec quality.
        """
        super().__init__(
            role=AgentRole.SPECIFIER,
            model_tier=model_tier,
            tools=["requirement_parser", "spec_generator", "example_generator"],
            **kwargs,
        )
        self.include_examples = include_examples
        self.validation_strictness = validation_strictness

    def select_model_tier(self, task: dict[str, Any]) -> ModelTier:
        """Select model tier based on requirements complexity.

        Args:
            task: Task dict with 'requirements' string.

        Returns:
            ModelTier based on complexity analysis:
                - Trivial: Has mostly boilerplate/simple keywords
                - Complex: Has architecture/design/system keywords
                - Normal: Everything else
        """
        requirements = task.get("requirements", "").lower()
        words = set(re.findall(r'\w+', requirements))

        trivial_score = len(words & COMPLEXITY_KEYWORDS_TRIVIAL)
        complex_score = len(words & COMPLEXITY_KEYWORDS_COMPLEX)

        if trivial_score > 0 and complex_score == 0:
            return ModelTier.FAST
        elif complex_score >= 2:
            return ModelTier.OPUS
        return ModelTier.SONNET

    def execute(self, task: dict[str, Any]) -> AgentResult:
        """Execute specification generation.

        Args:
            task: Dict with 'requirements' (str), optional 'context' (dict).

        Returns:
            AgentResult with:
                - success: Whether spec was generated successfully
                - confidence: 0.0-1.0 based on spec quality/clarity
                - output: Generated specification document
                - errors: Any validation errors encountered
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

        requirements = task["requirements"]
        context = task.get("context", {})

        # Analyze requirements complexity
        tier = self.select_model_tier(task)

        # Delegate to appropriate subagent based on tier
        sub_task = {
            "requirements": requirements,
            "context": context,
            "include_examples": self.include_examples,
            "validation_strictness": self.validation_strictness,
            "agent_tier": tier.name,
        }

        result = self.delegate_task(sub_task, model_tier=tier)

        # Post-process and validate spec
        if result.success and result.output:
            spec = result.output
            validation_errors = self._validate_spec(spec)
            errors.extend(validation_errors)

            # Calculate confidence based on validation
            confidence = self._calculate_confidence(spec, validation_errors)
        else:
            spec = ""
            confidence = 0.0
            errors.extend(result.errors)

        duration = time.time() - start_time

        return AgentResult(
            success=len(errors) == 0 and bool(spec),
            confidence=confidence,
            output=spec,
            errors=errors,
            agent_id=self.agent_id,
            duration_seconds=duration,
            metadata={
                "tier_selected": tier.name,
                "requirements_length": len(requirements),
                "spec_length": len(spec),
            },
        )

    def _validate_spec(self, spec: str) -> list[str]:
        """Validate generated specification meets quality criteria.

        Args:
            spec: Generated specification text.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = []

        if not spec or not spec.strip():
            errors.append("Specification is empty")
            return errors

        # Check required sections (simplified)
        required_sections = ["## Acceptance Criteria", "## Functionality"]
        for section in required_sections:
            if section not in spec:
                errors.append(f"Missing required section: {section}")

        # Check minimum length
        if len(spec) < 200:
            errors.append("Specification is too short (< 200 chars)")

        # Check for boilerplate detection
        if "TODO" in spec or "FIXME" in spec:
            errors.append("Specification contains unfinished markers")

        return errors

    def _calculate_confidence(self, spec: str, validation_errors: list[str]) -> float:
        """Calculate confidence score based on spec quality.

        Args:
            spec: Generated specification.
            validation_errors: List of validation errors.

        Returns:
            Confidence score 0.0-1.0.
        """
        if not spec:
            return 0.0

        # Base confidence from validation
        base_confidence = 1.0 - (len(validation_errors) * 0.15)

        # Length-based adjustment
        length_factor = min(len(spec) / 1000, 1.0) * 0.2

        # Section completeness factor
        sections_found = sum(1 for s in [
            "## Overview", "## Functionality", "## Acceptance Criteria"
        ] if s in spec)
        section_factor = (sections_found / 3) * 0.3

        confidence = base_confidence * 0.5 + length_factor + section_factor

        return max(0.0, min(1.0, confidence))

    def parse_requirements(self, requirements: str) -> dict[str, Any]:
        """Parse requirements string into structured components.

        Args:
            requirements: Natural language requirements.

        Returns:
            Dict with parsed components: actions, entities, constraints.
        """
        # Extract action verbs
        action_pattern = r'\b(write|create|build|implement|add|modify|delete|update|fix|handle|process)\b'
        actions = re.findall(action_pattern, requirements.lower())

        # Extract potential entity names (capitalized words)
        entity_pattern = r'\b[A-Z][a-zA-Z]+\b'
        entities = re.findall(entity_pattern, requirements)

        # Extract quoted strings as specific requirements
        quoted_pattern = r'"([^"]+)"|\'([^\']+)\''
        quoted = [m[0] or m[1] for m in re.findall(quoted_pattern, requirements)]

        return {
            "actions": actions,
            "entities": entities,
            "quoted_requirements": quoted,
            "original_length": len(requirements),
        }
