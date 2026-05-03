"""Nexus Verification Pipeline — Pre-commit verification gates.

This module implements the verification pipeline that runs BEFORE any commit:
1. Security scan (auto-FAIL on any finding)
2. TDD gate (test-first enforcement)
3. Test gate (baseline comparison)
4. Independent review gate

All modules under src/verification/:
    - tdd_gate: TDD enforcement (test-first discipline)
    - security_scan: Security vulnerability scanning
    - test_gate: Test execution with baseline comparison
    - review_gate: Independent review via delegate_task
    - pipeline: Pipeline orchestrator combining all gates
"""

from __future__ import annotations

from .tdd_gate import TDDGate, TDDResult
from .security_scan import SecurityScan, SecurityFinding
from .test_gate import TestGate, TestResult
from .review_gate import ReviewGate, ReviewResult
from .pipeline import VerificationPipeline, PipelineResult

__all__ = [
    # TDD Gate
    "TDDGate",
    "TDDResult",
    # Security Scan
    "SecurityScan",
    "SecurityFinding",
    # Test Gate
    "TestGate",
    "TestResult",
    # Review Gate
    "ReviewGate",
    "ReviewResult",
    # Pipeline
    "VerificationPipeline",
    "PipelineResult",
]
