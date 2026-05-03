"""SecurityAgent — Security Scanning for Secrets, Injection, and Path Traversal.

SecurityAgent performs comprehensive security analysis:
    - Detects hardcoded secrets, API keys, passwords, tokens
    - Identifies SQL injection, XSS, command injection vulnerabilities
    - Checks for path traversal vulnerabilities
    - Returns actionable findings with severity levels

Uses delegate_task for fresh subagent execution to ensure isolated analysis.
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any, Callable, Pattern


class FindingSeverity(Enum):
    """Severity levels for security findings.

    CRITICAL: Immediate security risk, critical vulnerability.
    HIGH: High priority security issue, should fix immediately.
    MEDIUM: Moderate security risk, should address soon.
    LOW: Low risk security issue, recommended to fix.
    INFO: Informational security observation.
    """

    CRITICAL = auto()
    HIGH = auto()
    MEDIUM = auto()
    LOW = auto()
    INFO = auto()


@dataclass
class Finding:
    """Represents a discovered security vulnerability.

    Attributes:
        rule_id: Unique identifier for the security check that triggered this finding.
        title: Short title describing the vulnerability.
        message: Detailed description of the issue.
        severity: Severity level of the finding.
        file_path: Path to the file containing the vulnerability (if applicable).
        line_number: Line number where issue occurs (if applicable).
        column: Column number where issue occurs (if applicable).
        code_snippet: Source code snippet related to the finding.
        recommendation: Recommended fix or remediation.
        cwe_id: CWE (Common Weakness Enumeration) ID if applicable.
        owasp_category: OWASP category if applicable.
        evidence: Additional evidence or context for the finding.
    """

    rule_id: str
    title: str
    message: str
    severity: FindingSeverity
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    column: Optional[int] = None
    code_snippet: Optional[str] = None
    recommendation: Optional[str] = None
    cwe_id: Optional[str] = None
    owasp_category: Optional[str] = None
    evidence: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert finding to dictionary representation."""
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "message": self.message,
            "severity": self.severity.name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column": self.column,
            "code_snippet": self.code_snippet,
            "recommendation": self.recommendation,
            "cwe_id": self.cwe_id,
            "owasp_category": self.owasp_category,
            "evidence": self.evidence,
        }


class SecurityAgent:
    """Security scanning agent for secrets, injection, and path traversal.

    SecurityAgent performs comprehensive security analysis:
        - Hardcoded secrets: API keys, passwords, tokens, private keys
        - Injection attacks: SQL injection, XSS, command injection
        - Path traversal: unsafe file operations

    Uses delegate_task to spawn fresh subagent executions for isolated analysis.

    Usage:
        scanner = SecurityAgent(delegate_task=my_delegate)
        findings = scanner.scan(
            code=source_code,
            file_path="src/module.py"
        )
        for finding in findings:
            if finding.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH):
                print(f"ALERT: {finding.title}")

    Attributes:
        delegate_task: Callable that spawns a fresh subagent execution.
            Signature: (task_description: str, context: dict) -> dict
        max_findings: Maximum number of findings to return (None for unlimited).
        scan_secrets: Enable hardcoded secret scanning (default True).
        scan_injection: Enable injection vulnerability scanning (default True).
        scan_path_traversal: Enable path traversal scanning (default True).
    """

    # Secret patterns with CWE references
    SECRET_PATTERNS: list[dict[str, Any]] = [
        # API Keys
        {
            "id": "SECRET-001",
            "pattern": r"(?i)(api[_-]?key|apikey|api[_-]?secret)\s*[=:]\s*['\"]([a-zA-Z0-9_\-]{20,})['\"]",
            "title": "Hardcoded API Key",
            "cwe": "CWE-798",
            "severity": FindingSeverity.CRITICAL,
        },
        # AWS Keys
        {
            "id": "SECRET-002",
            "pattern": r"(?i)(aws[_-]?(access[_-]?key[_-]?id|secret[_-]?access[_-]?key))\s*[=:]\s*['\"][A-Za-z0-9/+=]{20,}['\"]",
            "title": "Hardcoded AWS Credential",
            "cwe": "CWE-798",
            "severity": FindingSeverity.CRITICAL,
        },
        # Passwords
        {
            "id": "SECRET-003",
            "pattern": r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{8,}['\"]",
            "title": "Hardcoded Password",
            "cwe": "CWE-259",
            "severity": FindingSeverity.CRITICAL,
        },
        # Private keys
        {
            "id": "SECRET-004",
            "pattern": r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            "title": "Private Key Embedded in Code",
            "cwe": "CWE-798",
            "severity": FindingSeverity.CRITICAL,
        },
        # JWT Tokens
        {
            "id": "SECRET-005",
            "pattern": r"(?i)(jwt|bearer[_-]?token|authorization)\s*[=:]\s*['\"](eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+)['\"]",
            "title": "Hardcoded JWT Token",
            "cwe": "CWE-798",
            "severity": FindingSeverity.HIGH,
        },
        # Database connection strings
        {
            "id": "SECRET-006",
            "pattern": r"(?i)(mysql|postgres|postgresql|mongodb|redis):\/\/[^\s'\"]{10,}",
            "title": "Hardcoded Database Connection String",
            "cwe": "CWE-798",
            "severity": FindingSeverity.HIGH,
        },
        # Generic secret patterns
        {
            "id": "SECRET-007",
            "pattern": r"(?i)(secret|token|auth)\s*[=:]\s*['\"][a-zA-Z0-9_\-]{32,}['\"]",
            "title": "Potential Hardcoded Secret",
            "cwe": "CWE-798",
            "severity": FindingSeverity.MEDIUM,
        },
        # GitHub tokens
        {
            "id": "SECRET-008",
            "pattern": r"(?i)(github|ghtoken)\s*[=:]\s*['\"](ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36})['\"]",
            "title": "Hardcoded GitHub Token",
            "cwe": "CWE-798",
            "severity": FindingSeverity.CRITICAL,
        },
    ]

    # SQL Injection patterns
    SQL_INJECTION_PATTERNS: list[dict[str, Any]] = [
        {
            "id": "SQLI-001",
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*f[\"']",
            "title": "Potential SQL Injection via f-string",
            "cwe": "CWE-89",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SQLI-002",
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*[\"'].*\%",
            "title": "Potential SQL Injection via % formatting",
            "cwe": "CWE-89",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SQLI-003",
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*[\"'].*\+",
            "title": "Potential SQL Injection via string concatenation",
            "cwe": "CWE-89",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SQLI-004",
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*[\"'].*format\(",
            "title": "Potential SQL Injection via .format()",
            "cwe": "CWE-89",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
    ]

    # XSS patterns
    XSS_PATTERNS: list[dict[str, Any]] = [
        {
            "id": "XSS-001",
            "pattern": r"(?i)(innerHTML|outerHTML|insertAdjacentHTML)\s*\(",
            "title": "Potential XSS via innerHTML",
            "cwe": "CWE-79",
            "owasp": "A7",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "XSS-002",
            "pattern": r"(?i)(document\.write|dyndocument\.write)\s*\(",
            "title": "Potential XSS via document.write",
            "cwe": "CWE-79",
            "owasp": "A7",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "XSS-003",
            "pattern": r"(?i)(Response\.write|HttpResponse)\s*\(.*\+",
            "title": "Potential XSS via Response.Write concatenation",
            "cwe": "CWE-79",
            "owasp": "A7",
            "severity": FindingSeverity.MEDIUM,
        },
    ]

    # Command injection patterns
    COMMAND_INJECTION_PATTERNS: list[dict[str, Any]] = [
        {
            "id": "CMDI-001",
            "pattern": r"(?i)(os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*f[\"']",
            "title": "Potential Command Injection via f-string",
            "cwe": "CWE-78",
            "owasp": "A1",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "CMDI-002",
            "pattern": r"(?i)(os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*[\"'].*\%",
            "title": "Potential Command Injection via % formatting",
            "cwe": "CWE-78",
            "owasp": "A1",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "CMDI-003",
            "pattern": r"(?i)(os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*[\"'].*\+",
            "title": "Potential Command Injection via string concatenation",
            "cwe": "CWE-78",
            "owasp": "A1",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "CMDI-004",
            "pattern": r"(?i)eval\s*\(\s*request",
            "title": "Potential Code Injection via eval(request)",
            "cwe": "CWE-95",
            "owasp": "A1",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "CMDI-005",
            "pattern": r"(?i)exec\s*\(\s*request",
            "title": "Potential Code Injection via exec(request)",
            "cwe": "CWE-95",
            "owasp": "A1",
            "severity": FindingSeverity.CRITICAL,
        },
    ]

    # Path traversal patterns
    PATH_TRAVERSAL_PATTERNS: list[dict[str, Any]] = [
        {
            "id": "PATHT-001",
            "pattern": r"(?i)(open|file|Path)\s*\([^)]*\+[^)]*(request|user|input|param)",
            "title": "Potential Path Traversal via string concatenation",
            "cwe": "CWE-22",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "PATHT-002",
            "pattern": r"(?i)(open|file|Path)\s*\([^)]*\%[^)]*(request|user|input|param)",
            "title": "Potential Path Traversal via % formatting",
            "cwe": "CWE-22",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "PATHT-003",
            "pattern": r"(?i)(open|file|Path)\s*\([^)]*f[\"']",
            "title": "Potential Path Traversal via f-string",
            "cwe": "CWE-22",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "PATHT-004",
            "pattern": r"(?i)(join|Path)\s*\([^)]*\.\.[/\\]",
            "title": "Path Traversal: Directory traversal with '..' detected",
            "cwe": "CWE-22",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "PATHT-005",
            "pattern": r"(?i)send_file|send_from_directory.*\.\.",
            "title": "Potential Path Traversal in file send operations",
            "cwe": "CWE-22",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
    ]

    def __init__(
        self,
        delegate_task: Callable[[str, dict[str, Any]], dict[str, Any]],
        max_findings: Optional[int] = 100,
        scan_secrets: bool = True,
        scan_injection: bool = True,
        scan_path_traversal: bool = True,
    ) -> None:
        """Initialize SecurityAgent.

        Args:
            delegate_task: Callable spawning fresh subagent for analysis.
                Called as: delegate_task(task_description, context)
                Returns: dict with 'result' key containing analysis.
            max_findings: Maximum findings to return (None for unlimited).
            scan_secrets: Enable hardcoded secret scanning (default True).
            scan_injection: Enable injection vulnerability scanning (default True).
            scan_path_traversal: Enable path traversal scanning (default True).
        """
        self._delegate_task = delegate_task
        self._max_findings = max_findings
        self._scan_secrets = scan_secrets
        self._scan_injection = scan_injection
        self._scan_path_traversal = scan_path_traversal

    def scan(
        self,
        code: str,
        file_path: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> list[Finding]:
        """Perform comprehensive security scan.

        Analyzes code for security vulnerabilities using pattern matching
        and delegate_task for deep security analysis.

        Args:
            code: Source code to scan.
            file_path: Optional path to the source file for context.
            context: Optional additional context for the scan.

        Returns:
            List of Finding objects discovered during scan, sorted by severity.
        """
        findings: list[Finding] = []

        # Scan for hardcoded secrets
        if self._scan_secrets:
            secret_findings = self._scan_secrets(code, file_path)
            findings.extend(secret_findings)

        # Scan for injection vulnerabilities
        if self._scan_injection:
            injection_findings = self._scan_injection_vulnerabilities(code, file_path)
            findings.extend(injection_findings)

        # Scan for path traversal
        if self._scan_path_traversal:
            path_findings = self._scan_path_traversal(code, file_path)
            findings.extend(path_findings)

        # Delegate deep security analysis to fresh subagent
        deep_findings = self._delegate_deep_analysis(
            code, file_path, context or {}
        )
        findings.extend(deep_findings)

        # Sort by severity (CRITICAL first)
        severity_order = {
            FindingSeverity.CRITICAL: 0,
            FindingSeverity.HIGH: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.LOW: 3,
            FindingSeverity.INFO: 4,
        }
        findings.sort(key=lambda f: severity_order.get(f.severity, 5))

        # Apply max_findings limit
        if self._max_findings is not None:
            findings = findings[: self._max_findings]

        return findings

    def _scan_secrets(self, code: str, file_path: Optional[str]) -> list[Finding]:
        """Scan for hardcoded secrets.

        Args:
            code: Source code to scan.
            file_path: Optional file path.

        Returns:
            List of secret findings.
        """
        findings: list[Finding] = []

        for rule in self.SECRET_PATTERNS:
            pattern = rule["pattern"]
            matches = re.finditer(pattern, code, re.MULTILINE)

            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                lines = code.split("\n")
                snippet = lines[line_num - 1] if line_num <= len(lines) else None

                # Mask the secret in evidence
                evidence = match.group(0)
                masked = self._mask_secret(evidence)

                findings.append(
                    Finding(
                        rule_id=rule["id"],
                        title=rule["title"],
                        message=f"Potential {rule['title'].lower()} detected. "
                        f"Sensitive data should not be hardcoded.",
                        severity=rule["severity"],
                        file_path=file_path,
                        line_number=line_num,
                        code_snippet=snippet,
                        recommendation="Use environment variables or a secrets manager",
                        cwe_id=rule.get("cwe"),
                        evidence=masked,
                    )
                )

        return findings

    def _scan_injection_vulnerabilities(
        self, code: str, file_path: Optional[str]
    ) -> list[Finding]:
        """Scan for injection vulnerabilities (SQL, XSS, Command).

        Args:
            code: Source code to scan.
            file_path: Optional file path.

        Returns:
            List of injection findings.
        """
        findings: list[Finding] = []

        # SQL Injection
        for rule in self.SQL_INJECTION_PATTERNS:
            findings.extend(self._match_pattern(code, file_path, rule))

        # XSS
        for rule in self.XSS_PATTERNS:
            findings.extend(self._match_pattern(code, file_path, rule))

        # Command Injection
        for rule in self.COMMAND_INJECTION_PATTERNS:
            findings.extend(self._match_pattern(code, file_path, rule))

        return findings

    def _scan_path_traversal(self, code: str, file_path: Optional[str]) -> list[Finding]:
        """Scan for path traversal vulnerabilities.

        Args:
            code: Source code to scan.
            file_path: Optional file path.

        Returns:
            List of path traversal findings.
        """
        findings: list[Finding] = []

        for rule in self.PATH_TRAVERSAL_PATTERNS:
            findings.extend(self._match_pattern(code, file_path, rule))

        return findings

    def _match_pattern(
        self, code: str, file_path: Optional[str], rule: dict[str, Any]
    ) -> list[Finding]:
        """Match a single pattern rule against code.

        Args:
            code: Source code to scan.
            file_path: Optional file path.
            rule: Rule dict with id, pattern, title, cwe, owasp, severity.

        Returns:
            List of findings for this rule.
        """
        findings: list[Finding] = []
        pattern = rule["pattern"]

        try:
            compiled: Pattern[str] = re.compile(pattern, re.MULTILINE | re.IGNORECASE)
        except re.error:
            return findings

        matches = compiled.finditer(code)

        for match in matches:
            line_num = code[: match.start()].count("\n") + 1
            lines = code.split("\n")
            snippet = lines[line_num - 1] if line_num <= len(lines) else None

            findings.append(
                Finding(
                    rule_id=rule["id"],
                    title=rule["title"],
                    message=f"{rule['title']}. User input may be unsafely incorporated.",
                    severity=rule["severity"],
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=snippet,
                    recommendation=self._get_recommendation(rule["id"]),
                    cwe_id=rule.get("cwe"),
                    owasp_category=rule.get("owasp"),
                )
            )

        return findings

    def _delegate_deep_analysis(
        self,
        code: str,
        file_path: Optional[str],
        context: dict[str, Any],
    ) -> list[Finding]:
        """Delegate deep security analysis to fresh subagent.

        Uses delegate_task to spawn an isolated subagent for thorough
        security analysis beyond pattern matching.

        Args:
            code: Source code to analyze.
            file_path: Optional file path.
            context: Additional context.

        Returns:
            List of security findings.
        """
        task_description = (
            "Perform deep security analysis on the code. Identify vulnerabilities "
            "related to authentication, authorization, cryptography, data exposure, "
            "and other security concerns not caught by pattern matching. Return a JSON "
            "array of findings with fields: rule_id, title, message, severity "
            "(CRITICAL/HIGH/MEDIUM/LOW/INFO), line_number, recommendation, cwe_id, "
            "and owasp_category."
        )

        full_context = {
            "code": code,
            "file_path": file_path,
            "scan_type": "security_deep_analysis",
            **context,
        }

        try:
            result = self._delegate_task(task_description, full_context)

            if not result.get("success", False):
                return []

            findings_data = result.get("result", [])
            if isinstance(findings_data, str):
                import json

                findings_data = json.loads(findings_data)

            findings = []
            for item in findings_data:
                severity = FindingSeverity[item.get("severity", "MEDIUM")]
                findings.append(
                    Finding(
                        rule_id=item.get("rule_id", "DEEP-001"),
                        title=item.get("title", "Security Issue"),
                        message=item.get("message", "Security concern detected"),
                        severity=severity,
                        file_path=file_path,
                        line_number=item.get("line_number"),
                        recommendation=item.get("recommendation"),
                        cwe_id=item.get("cwe_id"),
                        owasp_category=item.get("owasp_category"),
                        evidence=item.get("evidence"),
                    )
                )
            return findings

        except Exception:
            # Delegate task failed, skip deep analysis
            return []

    @staticmethod
    def _mask_secret(evidence: str) -> str:
        """Mask a secret string for safe logging/display.

        Args:
            evidence: The secret string to mask.

        Returns:
            Masked string showing first and last few characters.
        """
        if len(evidence) <= 8:
            return "*" * len(evidence)

        visible_start = evidence[:4]
        visible_end = evidence[-4:]
        masked_middle = "*" * (len(evidence) - 8)
        return f"{visible_start}{masked_middle}{visible_end}"

    @staticmethod
    def _get_recommendation(rule_id: str) -> str:
        """Get security recommendation for a rule ID.

        Args:
            rule_id: The security rule identifier.

        Returns:
            Recommended remediation string.
        """
        recommendations = {
            # SQL Injection
            "SQLI-001": "Use parameterized queries/prepared statements instead of f-strings",
            "SQLI-002": "Use parameterized queries/prepared statements instead of % formatting",
            "SQLI-003": "Use parameterized queries/prepared statements instead of concatenation",
            "SQLI-004": "Use parameterized queries/prepared statements instead of .format()",
            # XSS
            "XSS-001": "Use textContent or sanitize input with DOMPurify before setting HTML",
            "XSS-002": "Avoid document.write(), use textContent or safe DOM APIs",
            "XSS-003": "Encode output appropriately for the context (HTML, JS, URL)",
            # Command Injection
            "CMDI-001": "Avoid shell commands with user input, use subprocess with list args",
            "CMDI-002": "Avoid shell commands with user input, use subprocess with list args",
            "CMDI-003": "Avoid shell commands with user input, use subprocess with list args",
            "CMDI-004": "Avoid eval() with user input, use safe parsing libraries",
            "CMDI-005": "Avoid exec() with user input, use safe evaluation libraries",
            # Path Traversal
            "PATHT-001": "Validate and sanitize path inputs, use os.path.realpath() to resolve",
            "PATHT-002": "Validate and sanitize path inputs, use os.path.realpath() to resolve",
            "PATHT-003": "Validate and sanitize path inputs, use os.path.realpath() to resolve",
            "PATHT-004": "Use os.path.realpath() to resolve and validate '../' sequences",
            "PATHT-005": "Validate path is within allowed directory using realpath comparison",
        }
        return recommendations.get(
            rule_id, "Review and validate user input before use"
        )
