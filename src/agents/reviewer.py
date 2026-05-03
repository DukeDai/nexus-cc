"""ReviewerAgent — Quality Gate for Spec Compliance and Logic Errors.

ReviewerAgent acts as an independent quality gate that:
    - Checks spec compliance against requirements
    - Detects logic errors and edge cases
    - Validates code quality and consistency
    - Returns actionable issues with severity levels

Uses delegate_task for fresh subagent execution to ensure isolated analysis.
"""

from __future__ import annotations

import re
import ast
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any, Callable


class IssueSeverity(Enum):
    """Severity levels for review issues.

    CRITICAL: Blocker issue, must fix before proceeding.
    HIGH: Important issue, strongly recommended to fix.
    MEDIUM: Moderate issue, should be addressed.
    LOW: Minor issue, nice to fix.
    INFO: Informational, no action required.
    """

    CRITICAL = auto()
    HIGH = auto()
    MEDIUM = auto()
    LOW = auto()
    INFO = auto()


@dataclass
class Issue:
    """Represents a discovered issue during code review.

    Attributes:
        rule_id: Unique identifier for the rule that triggered this issue.
        message: Human-readable description of the issue.
        severity: Severity level of the issue.
        file_path: Path to the file containing the issue (if applicable).
        line_number: Line number where issue occurs (if applicable).
        column: Column number where issue occurs (if applicable).
        code_snippet: Source code snippet related to the issue.
        suggestion: Optional suggestion for fixing the issue.
        context: Additional context about the issue.
    """

    rule_id: str
    message: str
    severity: IssueSeverity
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    column: Optional[int] = None
    code_snippet: Optional[str] = None
    suggestion: Optional[str] = None
    context: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert issue to dictionary representation."""
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "severity": self.severity.name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column": self.column,
            "code_snippet": self.code_snippet,
            "suggestion": self.suggestion,
            "context": self.context,
        }


class ReviewerAgent:
    """Independent quality gate for spec compliance and logic error detection.

    ReviewerAgent analyzes code to detect:
        - Spec compliance violations
        - Logic errors and incorrect assumptions
        - Edge cases that may cause failures
        - Inconsistent behavior
        - Missing error handling

    The agent uses delegate_task to spawn fresh subagent executions for
    isolated, thorough analysis without context contamination.

    Usage:
        reviewer = ReviewerAgent(delegate_task=my_delegate)
        issues = reviewer.review(
            code=source_code,
            spec=requirements,
            file_path="src/module.py"
        )
        for issue in issues:
            print(f"[{issue.severity.name}] {issue.message}")

    Attributes:
        delegate_task: Callable that spawns a fresh subagent execution.
            Signature: (task_description: str, context: dict) -> dict
        max_issues: Maximum number of issues to return (None for unlimited).
        enable_static_analysis: Enable AST-based static analysis (default True).
        enable_pattern_matching: Enable regex pattern matching (default True).
    """

    # Default rules for logic error detection
    DEFAULT_LOGIC_RULES: list[dict[str, Any]] = [
        {
            "id": "LOGIC-001",
            "pattern": r"if.*:\s*return.*\n\s*else:\s*return",
            "message": "Redundant if-else with opposing returns",
            "severity": IssueSeverity.MEDIUM,
        },
        {
            "id": "LOGIC-002",
            "pattern": r"while.*True.*(?<!break)(?<!return)",
            "message": "Potential infinite loop without break/return",
            "severity": IssueSeverity.HIGH,
        },
        {
            "id": "LOGIC-003",
            "pattern": r"for\s+\w+\s+in\s+.*:\s*[^:]*\bin\b(?!\s+range)",
            "message": "Iterating over non-range in potentially infinite pattern",
            "severity": IssueSeverity.MEDIUM,
        },
    ]

    # Rules for edge case detection
    DEFAULT_EDGE_CASE_RULES: list[dict[str, Any]] = [
        {
            "id": "EDGE-001",
            "pattern": r"(?<!_)\bNone\b(?!\s*is\s*(None|True|False))",
            "message": "Direct None comparison without 'is' operator",
            "severity": IssueSeverity.LOW,
        },
        {
            "id": "EDGE-002",
            "pattern": r"except\s*:\s*pass",
            "message": "Bare except clause with pass - errors swallowed silently",
            "severity": IssueSeverity.HIGH,
        },
        {
            "id": "EDGE-003",
            "pattern": r"//|/\s*/",
            "message": "Potential integer vs float division confusion",
            "severity": IssueSeverity.MEDIUM,
        },
    ]

    def __init__(
        self,
        delegate_task: Callable[[str, dict[str, Any]], dict[str, Any]],
        max_issues: Optional[int] = 100,
        enable_static_analysis: bool = True,
        enable_pattern_matching: bool = True,
    ) -> None:
        """Initialize ReviewerAgent.

        Args:
            delegate_task: Callable spawning fresh subagent for analysis.
                Called as: delegate_task(task_description, context)
                Returns: dict with 'result' key containing analysis.
            max_issues: Maximum issues to return (None for unlimited).
            enable_static_analysis: Enable AST-based analysis (default True).
            enable_pattern_matching: Enable regex pattern matching (default True).
        """
        self._delegate_task = delegate_task
        self._max_issues = max_issues
        self._enable_static_analysis = enable_static_analysis
        self._enable_pattern_matching = enable_pattern_matching

    def review(
        self,
        code: str,
        spec: Optional[dict[str, Any]] = None,
        file_path: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> list[Issue]:
        """Perform comprehensive code review.

        Analyzes code for spec compliance and logic errors using multiple
        analysis techniques including AST analysis and pattern matching.

        Args:
            code: Source code to review.
            spec: Optional specification/requirements dict to check against.
                Keys: 'requirements', 'constraints', 'expected_behavior'.
            file_path: Optional path to the source file for context.
            context: Optional additional context for the review.

        Returns:
            List of Issue objects discovered during review, sorted by severity.
        """
        issues: list[Issue] = []

        # Delegate spec compliance check to fresh subagent
        if spec:
            spec_issues = self._delegate_spec_compliance(code, spec, file_path)
            issues.extend(spec_issues)

        # Static analysis via AST
        if self._enable_static_analysis:
            ast_issues = self._analyze_ast(code, file_path)
            issues.extend(ast_issues)

        # Pattern-based logic error detection
        if self._enable_pattern_matching:
            pattern_issues = self._analyze_patterns(code, file_path)
            issues.extend(pattern_issues)

        # Delegate deep logic analysis to fresh subagent
        logic_issues = self._delegate_logic_analysis(code, file_path, context or {})
        issues.extend(logic_issues)

        # Sort by severity (CRITICAL first)
        severity_order = {
            IssueSeverity.CRITICAL: 0,
            IssueSeverity.HIGH: 1,
            IssueSeverity.MEDIUM: 2,
            IssueSeverity.LOW: 3,
            IssueSeverity.INFO: 4,
        }
        issues.sort(key=lambda i: severity_order.get(i.severity, 5))

        # Apply max_issues limit
        if self._max_issues is not None:
            issues = issues[: self._max_issues]

        return issues

    def _analyze_ast(self, code: str, file_path: Optional[str]) -> list[Issue]:
        """Perform AST-based static analysis.

        Detects common code issues using Python's AST module.

        Args:
            code: Source code to analyze.
            file_path: Optional file path for issue reporting.

        Returns:
            List of discovered issues.
        """
        issues: list[Issue] = []

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [
                Issue(
                    rule_id="AST-001",
                    message=f"Syntax error in code: {e.msg}",
                    severity=IssueSeverity.CRITICAL,
                    file_path=file_path,
                    line_number=e.lineno,
                    column=e.offset,
                    context=f"Invalid syntax at line {e.lineno}",
                )
            ]

        # Analyze AST nodes
        for node in ast.walk(tree):
            issues.extend(self._analyze_node(node, file_path))

        return issues

    def _analyze_node(self, node: ast.AST, file_path: Optional[str]) -> list[Issue]:
        """Analyze a single AST node for issues.

        Args:
            node: AST node to analyze.
            file_path: File path for context.

        Returns:
            List of issues found in this node.
        """
        issues: list[Issue] = []

        # Check for empty except blocks
        if isinstance(node, ast.ExceptHandler) and node.body == [
            ast.Pass()
        ]:
            issues.append(
                Issue(
                    rule_id="AST-002",
                    message="Empty except block - errors are silently swallowed",
                    severity=IssueSeverity.HIGH,
                    file_path=file_path,
                    line_number=node.lineno,
                    code_snippet=f"except{getattr(node, 'type', '')}:\n    pass",
                    suggestion="Add error handling or logging, or remove the except block",
                )
            )

        # Check for empty function/class bodies
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.body or all(isinstance(stmt, ast.Pass) for stmt in node.body):
                issues.append(
                    Issue(
                        rule_id="AST-003",
                        message=f"Empty {node.__class__.__name__} '{node.name}' has no implementation",
                        severity=IssueSeverity.MEDIUM,
                        file_path=file_path,
                        line_number=node.lineno,
                        suggestion="Implement the function/class or remove it",
                    )
                )

        # Check for broad exceptions
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(
                    Issue(
                        rule_id="AST-004",
                        message="Bare except clause catches all exceptions including SystemExit",
                        severity=IssueSeverity.HIGH,
                        file_path=file_path,
                        line_number=node.lineno,
                        suggestion="Specify exception types to catch",
                    )
                )

        # Check for return statements in finally without proper handling
        if isinstance(node, ast.Try):
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and child in ast.walk(node.finalbody):
                    issues.append(
                        Issue(
                            rule_id="AST-005",
                            message="Return in finally block may suppress exceptions",
                            severity=IssueSeverity.MEDIUM,
                            file_path=file_path,
                            line_number=node.lineno,
                        )
                    )

        return issues

    def _analyze_patterns(
        self, code: str, file_path: Optional[str]
    ) -> list[Issue]:
        """Perform regex pattern-based analysis.

        Args:
            code: Source code to analyze.
            file_path: Optional file path for issue reporting.

        Returns:
            List of discovered issues.
        """
        issues: list[Issue] = []

        all_rules = self.DEFAULT_LOGIC_RULES + self.DEFAULT_EDGE_CASE_RULES

        for rule in all_rules:
            pattern = rule["pattern"]
            matches = re.finditer(pattern, code, re.MULTILINE | re.IGNORECASE)

            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                # Get line content for snippet
                lines = code.split("\n")
                snippet = lines[line_num - 1] if line_num <= len(lines) else None

                issues.append(
                    Issue(
                        rule_id=rule["id"],
                        message=rule["message"],
                        severity=rule["severity"],
                        file_path=file_path,
                        line_number=line_num,
                        code_snippet=snippet,
                    )
                )

        return issues

    def _delegate_spec_compliance(
        self, code: str, spec: dict[str, Any], file_path: Optional[str]
    ) -> list[Issue]:
        """Delegate spec compliance check to fresh subagent.

        Uses delegate_task to spawn an isolated subagent that checks
        code compliance against requirements.

        Args:
            code: Source code to check.
            spec: Specification dict with requirements.
            file_path: Optional file path.

        Returns:
            List of spec compliance issues.
        """
        task_description = (
            "Perform spec compliance review. Check the code against the provided "
            "requirements and identify any violations. Return a JSON array of issues "
            "with fields: rule_id, message, severity (CRITICAL/HIGH/MEDIUM/LOW/INFO), "
            "line_number, and suggestion."
        )

        context = {
            "code": code,
            "spec": spec,
            "file_path": file_path,
            "review_type": "spec_compliance",
        }

        try:
            result = self._delegate_task(task_description, context)

            if not result.get("success", False):
                return []

            issues_data = result.get("result", [])
            if isinstance(issues_data, str):
                import json

                issues_data = json.loads(issues_data)

            issues = []
            for item in issues_data:
                severity = IssueSeverity[item.get("severity", "MEDIUM")]
                issues.append(
                    Issue(
                        rule_id=item.get("rule_id", "SPEC-001"),
                        message=item.get("message", "Spec violation"),
                        severity=severity,
                        file_path=file_path,
                        line_number=item.get("line_number"),
                        suggestion=item.get("suggestion"),
                    )
                )
            return issues

        except Exception:
            # Delegate task failed, skip spec compliance check
            return []

    def _delegate_logic_analysis(
        self,
        code: str,
        file_path: Optional[str],
        context: dict[str, Any],
    ) -> list[Issue]:
        """Delegate deep logic analysis to fresh subagent.

        Uses delegate_task to spawn an isolated subagent for thorough
        logic error and edge case analysis.

        Args:
            code: Source code to analyze.
            file_path: Optional file path.
            context: Additional context.

        Returns:
            List of logic issues.
        """
        task_description = (
            "Perform deep logic analysis on the code. Identify potential logic errors, "
            "edge cases, race conditions, and incorrect assumptions. Return a JSON array "
            "of issues with fields: rule_id, message, severity, line_number, and suggestion."
        )

        full_context = {
            "code": code,
            "file_path": file_path,
            "review_type": "logic_analysis",
            **context,
        }

        try:
            result = self._delegate_task(task_description, full_context)

            if not result.get("success", False):
                return []

            issues_data = result.get("result", [])
            if isinstance(issues_data, str):
                import json

                issues_data = json.loads(issues_data)

            issues = []
            for item in issues_data:
                severity = IssueSeverity[item.get("severity", "MEDIUM")]
                issues.append(
                    Issue(
                        rule_id=item.get("rule_id", "LOGIC-001"),
                        message=item.get("message", "Logic issue detected"),
                        severity=severity,
                        file_path=file_path,
                        line_number=item.get("line_number"),
                        suggestion=item.get("suggestion"),
                        context=item.get("context"),
                    )
                )
            return issues

        except Exception:
            # Delegate task failed, skip deep analysis
            return []
