"""Security Scan — Fail-closed security vulnerability detection.

This module implements the security scanning gate that runs BEFORE any commit.
It uses SecurityAgent to scan for:
    - Hardcoded secrets (API keys, passwords, tokens)
    - SQL injection vulnerabilities
    - XSS vulnerabilities
    - Command injection
    - Path traversal vulnerabilities

FAIL-CLOSED: Any finding triggers an automatic failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Any, Callable

# Re-export FindingSeverity from agents.security for consistency
try:
    from agents.security import FindingSeverity
except ImportError:
    # Fallback if agents not available
    class FindingSeverity(Enum):
        CRITICAL = auto()
        HIGH = auto()
        MEDIUM = auto()
        LOW = auto()
        INFO = auto()


@dataclass
class SecurityFinding:
    """Represents a discovered security vulnerability.

    Attributes:
        rule_id: Unique identifier for the security check.
        title: Short title describing the vulnerability.
        message: Detailed description of the issue.
        severity: Severity level (CRITICAL/HIGH/MEDIUM/LOW/INFO).
        file_path: Path to the file containing the vulnerability.
        line_number: Line number where issue occurs.
        column: Column number where issue occurs.
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


class SecurityScan:
    """Fail-closed security scanner for pre-commit verification.

    SecurityScan performs comprehensive security analysis using pattern
    matching and (optionally) delegate_task for deep analysis. Any finding
    results in automatic failure of the gate.

    Usage:
        scanner = SecurityScan(delegate_task=my_delegate)
        result = scanner.scan(
            code=source_code,
            file_path="src/module.py"
        )
        if not result.passed:
            for finding in result.findings:
                print(f"[{finding.severity.name}] {finding.title}")

    Attributes:
        delegate_task: Optional callable that spawns a fresh subagent
            for deep security analysis. Signature: (task_description, context) -> dict
        max_findings: Maximum findings to return before FAIL.
        scan_secrets: Enable hardcoded secret scanning (default True).
        scan_injection: Enable injection vulnerability scanning (default True).
        scan_path_traversal: Enable path traversal scanning (default True).
    """

    # Secret patterns with CWE references
    SECRET_PATTERNS: list[dict[str, Any]] = [
        {
            "id": "SECRET-001",
            "pattern": r"(?i)(api[_-]?key|apikey|api[_-]?secret)\s*[=:]\s*['\"]([a-zA-Z0-9_\-]{20,})['\"]",
            "title": "Hardcoded API Key",
            "cwe": "CWE-798",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "SECRET-002",
            "pattern": r"(?i)(aws[_-]?(access[_-]?key[_-]?id|secret[_-]?access[_-]?key))\s*[=:]\s*['\"][A-Za-z0-9/+=]{20,}['\"]",
            "title": "Hardcoded AWS Credential",
            "cwe": "CWE-798",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "SECRET-003",
            "pattern": r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{8,}['\"]",
            "title": "Hardcoded Password",
            "cwe": "CWE-259",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "SECRET-004",
            "pattern": r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            "title": "Private Key Embedded in Code",
            "cwe": "CWE-798",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "SECRET-005",
            "pattern": r"(?i)(jwt|bearer[_-]?token|authorization)\s*[=:]\s*['\"](eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+)['\"]",
            "title": "Hardcoded JWT Token",
            "cwe": "CWE-798",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SECRET-006",
            "pattern": r"(?i)(mysql|postgres|postgresql|mongodb|redis):\/\/[^\s'\"]{10,}",
            "title": "Hardcoded Database Connection String",
            "cwe": "CWE-798",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SECRET-007",
            "pattern": r"(?i)(secret|token|auth)\s*[=:]\s*['\"][a-zA-Z0-9_\-]{32,}['\"]",
            "title": "Potential Hardcoded Secret",
            "cwe": "CWE-798",
            "severity": FindingSeverity.MEDIUM,
        },
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
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*f['\"]",
            "title": "Potential SQL Injection via f-string",
            "cwe": "CWE-89",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SQLI-002",
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*['\"].*\%",
            "title": "Potential SQL Injection via % formatting",
            "cwe": "CWE-89",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SQLI-003",
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*['\"].*\+",
            "title": "Potential SQL Injection via string concatenation",
            "cwe": "CWE-89",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "SQLI-004",
            "pattern": r"(?i)(execute|executemany|cursor\.execute)\s*\(\s*['\"].*format\(",
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
    ]

    # Command injection patterns
    COMMAND_INJECTION_PATTERNS: list[dict[str, Any]] = [
        {
            "id": "CMDI-001",
            "pattern": r"(?i)(os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*f['\"]",
            "title": "Potential Command Injection via f-string",
            "cwe": "CWE-78",
            "owasp": "A1",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "CMDI-002",
            "pattern": r"(?i)(os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*['\"].*\%",
            "title": "Potential Command Injection via % formatting",
            "cwe": "CWE-78",
            "owasp": "A1",
            "severity": FindingSeverity.CRITICAL,
        },
        {
            "id": "CMDI-003",
            "pattern": r"(?i)(os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*['\"].*\+",
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
        {
            "id": "CMDI-006",
            "pattern": r"(?i)pickle\.loads",
            "title": "Dangerous Deserialization (pickle)",
            "cwe": "CWE-502",
            "owasp": "A8",
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
            "pattern": r"(?i)(open|file|Path)\s*\([^)]*f['\"]",
            "title": "Potential Path Traversal via f-string",
            "cwe": "CWE-22",
            "owasp": "A1",
            "severity": FindingSeverity.HIGH,
        },
        {
            "id": "PATHT-004",
            "pattern": r"(?i)(join|Path)\s*\([^)]*\.\.[\/\\]",
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
        delegate_task: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
        max_findings: Optional[int] = 0,  # 0 = fail on ANY finding
        scan_secrets: bool = True,
        scan_injection: bool = True,
        scan_path_traversal: bool = True,
    ) -> None:
        """Initialize SecurityScan.

        Args:
            delegate_task: Optional callable spawning fresh subagent for deep analysis.
                Called as: delegate_task(task_description, context)
                Returns: dict with 'result' key containing analysis.
            max_findings: Maximum findings before FAIL. Default 0 = fail on ANY finding.
            scan_secrets: Enable hardcoded secret scanning (default True).
            scan_injection: Enable injection vulnerability scanning (default True).
            scan_path_traversal: Enable path traversal scanning (default True).
        """
        self._delegate_task = delegate_task
        self._max_findings = max_findings
        self._do_scan_secrets = scan_secrets
        self._do_scan_injection = scan_injection
        self._do_scan_path_traversal = scan_path_traversal

    def scan(
        self,
        code: str,
        file_path: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> SecurityScanResult:
        """Perform comprehensive security scan.

        Analyzes code for security vulnerabilities using pattern matching.
        FAIL-CLOSED: Any finding triggers failure.

        Args:
            code: Source code to scan.
            file_path: Optional path to the source file for context.
            context: Optional additional context for the scan.

        Returns:
            SecurityScanResult with passed=False if any findings, otherwise passed=True.
        """
        findings: list[SecurityFinding] = []

        # Scan for hardcoded secrets
        if self._do_scan_secrets:
            secret_findings = self._scan_secrets(code, file_path)
            findings.extend(secret_findings)

        # Scan for injection vulnerabilities
        if self._do_scan_injection:
            injection_findings = self._scan_injection_vulnerabilities(code, file_path)
            findings.extend(injection_findings)

        # Scan for path traversal
        if self._do_scan_path_traversal:
            path_findings = self._scan_path_traversal(code, file_path)
            findings.extend(path_findings)

        # Delegate deep security analysis to fresh subagent if available
        if self._delegate_task:
            deep_findings = self._delegate_deep_analysis(code, file_path, context or {})
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

        # FAIL-CLOSED: Any findings = failure
        passed = len(findings) == 0

        return SecurityScanResult(
            passed=passed,
            findings=findings,
            total_count=len(findings),
            scan_type="security_scan",
        )

    def _scan_secrets(self, code: str, file_path: Optional[str]) -> list[SecurityFinding]:
        """Scan for hardcoded secrets.

        Args:
            code: Source code to scan.
            file_path: Optional file path.

        Returns:
            List of secret findings.
        """
        findings: list[SecurityFinding] = []

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
                    SecurityFinding(
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
    ) -> list[SecurityFinding]:
        """Scan for injection vulnerabilities (SQL, XSS, Command).

        Args:
            code: Source code to scan.
            file_path: Optional file path.

        Returns:
            List of injection findings.
        """
        findings: list[SecurityFinding] = []

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

    def _scan_path_traversal(self, code: str, file_path: Optional[str]) -> list[SecurityFinding]:
        """Scan for path traversal vulnerabilities.

        Args:
            code: Source code to scan.
            file_path: Optional file path.

        Returns:
            List of path traversal findings.
        """
        findings: list[SecurityFinding] = []

        for rule in self.PATH_TRAVERSAL_PATTERNS:
            findings.extend(self._match_pattern(code, file_path, rule))

        return findings

    def _match_pattern(
        self, code: str, file_path: Optional[str], rule: dict[str, Any]
    ) -> list[SecurityFinding]:
        """Match a pattern against code and return findings.

        Args:
            code: Source code to scan.
            file_path: Optional file path.
            rule: Rule dict with id, pattern, title, cwe, owasp, severity.

        Returns:
            List of matching findings.
        """
        findings: list[SecurityFinding] = []
        pattern = rule["pattern"]

        try:
            matches = re.finditer(pattern, code, re.MULTILINE | re.IGNORECASE)
        except re.error:
            return findings

        for match in matches:
            line_num = code[: match.start()].count("\n") + 1
            lines = code.split("\n")
            snippet = lines[line_num - 1] if line_num <= len(lines) else None

            findings.append(
                SecurityFinding(
                    rule_id=rule["id"],
                    title=rule["title"],
                    message=f"{rule['title']} detected. {rule.get('cwe', 'CWE-unknown')} vulnerability.",
                    severity=rule["severity"],
                    file_path=file_path,
                    line_number=line_num,
                    code_snippet=snippet,
                    recommendation=f"Fix: {rule.get('cwe', 'Review for security')}",
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
    ) -> list[SecurityFinding]:
        """Delegate deep security analysis to fresh subagent.

        Args:
            code: Source code to analyze.
            file_path: Optional file path.
            context: Additional context.

        Returns:
            List of findings from deep analysis.
        """
        if not self._delegate_task:
            return []

        task_description = (
            "Perform deep security analysis on the code. Identify potential "
            "security vulnerabilities including: authentication bypass, insecure "
            "dependencies, crypto weaknesses, race conditions, and other "
            "security issues. Return a JSON array of findings with fields: "
            "rule_id, title, message, severity (CRITICAL/HIGH/MEDIUM/LOW/INFO), "
            "line_number, cwe_id, and recommendation."
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

            findings: list[SecurityFinding] = []
            for item in findings_data:
                severity_name = item.get("severity", "MEDIUM")
                try:
                    severity = FindingSeverity[severity_name]
                except KeyError:
                    severity = FindingSeverity.MEDIUM

                findings.append(
                    SecurityFinding(
                        rule_id=item.get("rule_id", "DEEP-001"),
                        title=item.get("title", "Security issue"),
                        message=item.get("message", "Security vulnerability detected"),
                        severity=severity,
                        file_path=file_path,
                        line_number=item.get("line_number"),
                        recommendation=item.get("recommendation"),
                        cwe_id=item.get("cwe_id"),
                    )
                )
            return findings

        except Exception:
            # Delegate task failed, skip deep analysis
            return []

    @staticmethod
    def _mask_secret(evidence: str) -> str:
        """Mask a secret value for safe logging.

        Args:
            evidence: The matched secret string.

        Returns:
            Masked string showing only first and last 3 chars.
        """
        if len(evidence) <= 8:
            return "*" * len(evidence)
        return evidence[:3] + "*" * (len(evidence) - 6) + evidence[-3:]


@dataclass
class SecurityScanResult:
    """Result of a security scan operation.

    Attributes:
        passed: True if no security issues found (scan passed).
        findings: List of SecurityFinding objects.
        total_count: Total number of findings.
        scan_type: Type of scan performed.
    """

    passed: bool
    findings: list[SecurityFinding]
    total_count: int
    scan_type: str = "security_scan"

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
            "total_count": self.total_count,
            "scan_type": self.scan_type,
        }
