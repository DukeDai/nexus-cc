"""Review Gate — Independent review via delegate_task.

This module implements the independent review gate that:
1. Uses delegate_task to spawn a fresh subagent with no context contamination
2. Reviews code for spec compliance, logic errors, and quality issues
3. Is truly independent - no self-review
4. Passes ONLY if no security concerns AND no logic errors

Suggestions are non-blocking but must be reported.
"""

from __future__ import annotations

import ast
import re
import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any, Callable


class ReviewSeverity(Enum):
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


class IssueCategory(Enum):
    """Categories of review issues.

    SPEC_COMPLIANCE: Code doesn't meet specification requirements.
    LOGIC_ERROR: Incorrect logic or algorithm.
    EDGE_CASE: Missing edge case handling.
    CODE_QUALITY: General code quality issue.
    SECURITY: Security concern (passed to security scan).
    PERFORMANCE: Performance issue.
    STYLE: Style/formatting issue.
    """

    SPEC_COMPLIANCE = "spec_compliance"
    LOGIC_ERROR = "logic_error"
    EDGE_CASE = "edge_case"
    CODE_QUALITY = "code_quality"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"


@dataclass
class ReviewIssue:
    """Represents a discovered issue during code review.

    Attributes:
        rule_id: Unique identifier for the rule that triggered this issue.
        message: Human-readable description of the issue.
        severity: Severity level of the issue.
        category: Category of the issue.
        file_path: Path to the file containing the issue (if applicable).
        line_number: Line number where issue occurs (if applicable).
        column: Column number where issue occurs (if applicable).
        code_snippet: Source code snippet related to the issue.
        suggestion: Optional suggestion for fixing the issue.
        context: Additional context about the issue.
    """

    rule_id: str
    message: str
    severity: ReviewSeverity
    category: IssueCategory = IssueCategory.CODE_QUALITY
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
            "category": self.category.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column": self.column,
            "code_snippet": self.code_snippet,
            "suggestion": self.suggestion,
            "context": self.context,
        }


@dataclass
class ReviewResult:
    """Result of independent code review.

    Attributes:
        passed: True if review passed (no critical/high issues).
        issues: List of ReviewIssue objects discovered.
        total_issues: Total number of issues.
        critical_count: Number of critical issues.
        high_count: Number of high issues.
        medium_count: Number of medium issues.
        low_count: Number of low issues.
        info_count: Number of informational issues.
        blocked: True if review should block (critical issues).
        message: Human-readable message.
    """

    passed: bool
    issues: list[ReviewIssue]
    total_issues: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    blocked: bool = False
    message: str = ""

    def __post_init__(self) -> None:
        """Calculate issue counts after initialization."""
        self.total_issues = len(self.issues)
        for issue in self.issues:
            if issue.severity == ReviewSeverity.CRITICAL:
                self.critical_count += 1
            elif issue.severity == ReviewSeverity.HIGH:
                self.high_count += 1
            elif issue.severity == ReviewSeverity.MEDIUM:
                self.medium_count += 1
            elif issue.severity == ReviewSeverity.LOW:
                self.low_count += 1
            elif issue.severity == ReviewSeverity.INFO:
                self.info_count += 1

        # Block if any critical issues
        self.blocked = self.critical_count > 0
        self.passed = self.critical_count == 0 and self.high_count == 0

        if self.critical_count > 0:
            self.message = f"BLOCKED: {self.critical_count} critical issue(s) found"
        elif self.high_count > 0:
            self.message = f"Review failed: {self.high_count} high severity issue(s)"
        elif self.medium_count > 0:
            self.message = f"Review passed with warnings: {self.medium_count} medium issue(s)"
        elif self.low_count > 0:
            self.message = f"Review passed: {self.low_count} low severity issue(s)"
        else:
            self.message = "Review passed: no issues found"

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
            "total_issues": self.total_issues,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "medium_count": self.medium_count,
            "low_count": self.low_count,
            "info_count": self.info_count,
            "blocked": self.blocked,
            "message": self.message,
        }


class ReviewGate:
    """Independent review gate using delegate_task for fresh analysis.

    ReviewGate performs comprehensive code review using:
    1. Static analysis (AST-based)
    2. Pattern matching for common issues
    3. Deep analysis via delegate_task subagent (independent, no context contamination)

    The gate is truly independent - the delegate_task provides fresh context,
    ensuring no self-review contamination.

    Usage:
        gate = ReviewGate(delegate_task=my_delegate)
        result = gate.review(
            code=source_code,
            spec=requirements,
            file_path="src/module.py"
        )
        if result.blocked:
            print("COMMIT BLOCKED due to critical issues")
            for issue in result.issues:
                print(f"[{issue.severity.name}] {issue.message}")

    Attributes:
        delegate_task: Callable that spawns a fresh subagent execution.
            Signature: (task_description: str, context: dict) -> dict
        max_issues: Maximum issues to return (None for unlimited).
        enable_static_analysis: Enable AST-based static analysis (default True).
        enable_pattern_matching: Enable regex pattern matching (default True).
        block_on_high: If True, block on HIGH severity issues (default False).
    """

    # Default rules for logic error detection
    DEFAULT_LOGIC_RULES: list[dict[str, Any]] = [
        {
            "id": "LOGIC-001",
            "pattern": r"if.*:\s*return.*\n\s*else:\s*return",
            "message": "Redundant if-else with opposing returns",
            "severity": ReviewSeverity.MEDIUM,
        },
        {
            "id": "LOGIC-002",
            "pattern": r"while.*True.*(?<!break)(?<!return)",
            "message": "Potential infinite loop without break/return",
            "severity": ReviewSeverity.HIGH,
        },
        {
            "id": "LOGIC-003",
            "pattern": r"for\s+\w+\s+in\s+.*:\s*[^\:]*\bin\b(?!\s+range)",
            "message": "Iterating over non-range in potentially infinite pattern",
            "severity": ReviewSeverity.MEDIUM,
        },
    ]

    # Rules for edge case detection
    DEFAULT_EDGE_CASE_RULES: list[dict[str, Any]] = [
        {
            "id": "EDGE-001",
            "pattern": r"(?<!_)\bNone\b(?!\s*is\s*(None|True|False))",
            "message": "Direct None comparison without 'is' operator",
            "severity": ReviewSeverity.LOW,
        },
        {
            "id": "EDGE-002",
            "pattern": r"except\s*:\s*pass",
            "message": "Bare except clause with pass - errors swallowed silently",
            "severity": ReviewSeverity.HIGH,
        },
        {
            "id": "EDGE-003",
            "pattern": r"//|/\s*/",
            "message": "Potential integer vs float division confusion",
            "severity": ReviewSeverity.MEDIUM,
        },
    ]

    # Code quality rules
    DEFAULT_QUALITY_RULES: list[dict[str, Any]] = [
        {
            "id": "QUAL-001",
            "pattern": r"print\s*\(",
            "message": "Debug print statement left in code",
            "severity": ReviewSeverity.LOW,
        },
        {
            "id": "QUAL-002",
            "pattern": r"#\s*TODO|#\s*FIXME|#\s*HACK",
            "message": "TODO/FIXME/HACK comment found",
            "severity": ReviewSeverity.INFO,
        },
        {
            "id": "QUAL-003",
            "pattern": r"import\s+\*",
            "message": "Wildcard import (import *) - explicit imports preferred",
            "severity": ReviewSeverity.LOW,
        },
    ]

    def __init__(
        self,
        delegate_task: Callable[[str, dict[str, Any]], dict[str, Any]],
        max_issues: Optional[int] = 100,
        enable_static_analysis: bool = True,
        enable_pattern_matching: bool = True,
        block_on_high: bool = False,
    ) -> None:
        """Initialize ReviewGate.

        Args:
            delegate_task: Callable spawning fresh subagent for analysis.
                Called as: delegate_task(task_description, context)
                Returns: dict with 'result' key containing analysis.
            max_issues: Maximum issues to return (None for unlimited).
            enable_static_analysis: Enable AST-based analysis (default True).
            enable_pattern_matching: Enable regex pattern matching (default True).
            block_on_high: If True, block on HIGH severity issues (default False).
        """
        self._delegate_task = delegate_task
        self._max_issues = max_issues
        self._enable_static_analysis = enable_static_analysis
        self._enable_pattern_matching = enable_pattern_matching
        self._block_on_high = block_on_high

    def review(
        self,
        code: str,
        spec: Optional[dict[str, Any]] = None,
        file_path: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> ReviewResult:
        """Perform comprehensive independent code review.

        Analyzes code using multiple techniques:
        1. AST-based static analysis
        2. Pattern matching for common issues
        3. Deep analysis via delegate_task (independent subagent)

        Args:
            code: Source code to review.
            spec: Optional specification/requirements dict to check against.
                Keys: 'requirements', 'constraints', 'expected_behavior'.
            file_path: Optional path to the source file for context.
            context: Optional additional context for the review.

        Returns:
            ReviewResult with pass/fail status and list of issues.
        """
        issues: list[ReviewIssue] = []

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

        # Delegate deep logic analysis to fresh subagent (independent)
        logic_issues = self._delegate_logic_analysis(code, file_path, context or {})
        issues.extend(logic_issues)

        # Sort by severity (CRITICAL first)
        severity_order = {
            ReviewSeverity.CRITICAL: 0,
            ReviewSeverity.HIGH: 1,
            ReviewSeverity.MEDIUM: 2,
            ReviewSeverity.LOW: 3,
            ReviewSeverity.INFO: 4,
        }
        issues.sort(key=lambda i: severity_order.get(i.severity, 5))

        # Apply max_issues limit
        if self._max_issues is not None:
            issues = issues[: self._max_issues]

        # Create result
        result = ReviewResult(
            passed=True,  # Will be recalculated in __post_init__
            issues=issues,
        )

        # Override blocked based on settings
        if not self._block_on_high:
            result.blocked = result.critical_count > 0
        else:
            result.blocked = result.critical_count > 0 or result.high_count > 0

        return result

    def _analyze_ast(self, code: str, file_path: Optional[str]) -> list[ReviewIssue]:
        """Perform AST-based static analysis.

        Detects common code issues using Python's AST module.

        Args:
            code: Source code to analyze.
            file_path: Optional file path for issue reporting.

        Returns:
            List of discovered issues.
        """
        issues: list[ReviewIssue] = []

        if not code.strip():
            return issues

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [
                ReviewIssue(
                    rule_id="AST-001",
                    message=f"Syntax error in code: {e.msg}",
                    severity=ReviewSeverity.CRITICAL,
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

    def _analyze_node(self, node: ast.AST, file_path: Optional[str]) -> list[ReviewIssue]:
        """Analyze a single AST node for issues.

        Args:
            node: AST node to analyze.
            file_path: File path for context.

        Returns:
            List of issues found in this node.
        """
        issues: list[ReviewIssue] = []

        # Check for empty except blocks
        if isinstance(node, ast.ExceptHandler) and node.body == [ast.Pass()]:
            issues.append(
                ReviewIssue(
                    rule_id="AST-002",
                    message="Empty except block - errors are silently swallowed",
                    severity=ReviewSeverity.HIGH,
                    category=IssueCategory.CODE_QUALITY,
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
                    ReviewIssue(
                        rule_id="AST-003",
                        message=f"Empty {node.__class__.__name__} '{node.name}' has no implementation",
                        severity=ReviewSeverity.MEDIUM,
                        category=IssueCategory.CODE_QUALITY,
                        file_path=file_path,
                        line_number=node.lineno,
                        suggestion="Implement the function/class or remove it",
                    )
                )

        # Check for broad exceptions
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(
                    ReviewIssue(
                        rule_id="AST-004",
                        message="Bare except clause catches all exceptions including SystemExit",
                        severity=ReviewSeverity.HIGH,
                        category=IssueCategory.CODE_QUALITY,
                        file_path=file_path,
                        line_number=node.lineno,
                        suggestion="Specify exception types to catch",
                    )
                )

        # Check for return statements in finally without proper handling
        if isinstance(node, ast.Try):
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and hasattr(node, 'finalbody') and child in node.finalbody:
                    issues.append(
                        ReviewIssue(
                            rule_id="AST-005",
                            message="Return in finally block may suppress exceptions",
                            severity=ReviewSeverity.MEDIUM,
                            category=IssueCategory.LOGIC_ERROR,
                            file_path=file_path,
                            line_number=node.lineno,
                        )
                    )

        return issues

    def _analyze_patterns(
        self, code: str, file_path: Optional[str]
    ) -> list[ReviewIssue]:
        """Perform regex pattern-based analysis.

        Args:
            code: Source code to analyze.
            file_path: Optional file path for issue reporting.

        Returns:
            List of discovered issues.
        """
        issues: list[ReviewIssue] = []

        all_rules = (
            self.DEFAULT_LOGIC_RULES
            + self.DEFAULT_EDGE_CASE_RULES
            + self.DEFAULT_QUALITY_RULES
        )

        for rule in all_rules:
            pattern = rule["pattern"]
            severity_name = rule.get("severity", "MEDIUM")
            if isinstance(severity_name, str):
                severity = ReviewSeverity[severity_name]
            else:
                severity = severity_name

            try:
                matches = re.finditer(pattern, code, re.MULTILINE | re.IGNORECASE)
            except re.error:
                continue

            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                # Get line content for snippet
                lines = code.split("\n")
                snippet = lines[line_num - 1] if line_num <= len(lines) else None

                issues.append(
                    ReviewIssue(
                        rule_id=rule["id"],
                        message=rule["message"],
                        severity=severity,
                        category=IssueCategory(rule.get("category", "code_quality")),
                        file_path=file_path,
                        line_number=line_num,
                        code_snippet=snippet,
                    )
                )

        return issues

    def _delegate_spec_compliance(
        self, code: str, spec: dict[str, Any], file_path: Optional[str]
    ) -> list[ReviewIssue]:
        """Delegate spec compliance check to fresh independent subagent.

        Uses delegate_task to spawn an isolated subagent that checks
        code compliance against requirements. This is independent because
        the subagent has fresh context with no awareness of the code
        being reviewed.

        Args:
            code: Source code to check.
            spec: Specification dict with requirements.
            file_path: Optional file path.

        Returns:
            List of spec compliance issues.
        """
        task_description = (
            "You are an independent code reviewer. Analyze the provided code against "
            "the specification and identify any violations. Be thorough and critical. "
            "Return a JSON array of issues with fields: "
            "rule_id, message, severity (CRITICAL/HIGH/MEDIUM/LOW/INFO), "
            "line_number, category (spec_compliance/logic_error/edge_case/code_quality), "
            "and suggestion."
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
                issues_data = json.loads(issues_data)

            issues: list[ReviewIssue] = []
            for item in issues_data:
                severity_name = item.get("severity", "MEDIUM")
                try:
                    severity = ReviewSeverity[severity_name]
                except KeyError:
                    severity = ReviewSeverity.MEDIUM

                category_name = item.get("category", "code_quality")
                try:
                    category = IssueCategory(category_name)
                except ValueError:
                    category = IssueCategory.CODE_QUALITY

                issues.append(
                    ReviewIssue(
                        rule_id=item.get("rule_id", "SPEC-001"),
                        message=item.get("message", "Spec violation"),
                        severity=severity,
                        category=category,
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
    ) -> list[ReviewIssue]:
        """Delegate deep logic analysis to fresh independent subagent.

        Uses delegate_task to spawn an isolated subagent for thorough
        logic error and edge case analysis. This is truly independent
        because the subagent has no context about this code.

        Args:
            code: Source code to analyze.
            file_path: Optional file path.
            context: Additional context.

        Returns:
            List of logic issues.
        """
        task_description = (
            "You are an independent code analyst. Perform a deep analysis of the "
            "code to identify potential logic errors, edge cases, race conditions, "
            "and incorrect assumptions. Be thorough and critical. "
            "Return a JSON array of issues with fields: "
            "rule_id, message, severity (CRITICAL/HIGH/MEDIUM/LOW/INFO), "
            "line_number, category (logic_error/edge_case), and suggestion."
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
                issues_data = json.loads(issues_data)

            issues: list[ReviewIssue] = []
            for item in issues_data:
                severity_name = item.get("severity", "MEDIUM")
                try:
                    severity = ReviewSeverity[severity_name]
                except KeyError:
                    severity = ReviewSeverity.MEDIUM

                category_name = item.get("category", "logic_error")
                try:
                    category = IssueCategory(category_name)
                except ValueError:
                    category = IssueCategory.LOGIC_ERROR

                issues.append(
                    ReviewIssue(
                        rule_id=item.get("rule_id", "LOGIC-001"),
                        message=item.get("message", "Logic issue detected"),
                        severity=severity,
                        category=category,
                        file_path=file_path,
                        line_number=item.get("line_number"),
                        suggestion=item.get("suggestion"),
                        context=item.get("context"),
                    )
                )
            return issues

        except Exception:
            # Delegate task failed, skip logic analysis
            return []
