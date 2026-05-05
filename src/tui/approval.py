"""Approval workflow for dangerous operations.

Provides pause points before:
    - Committing code changes
    - Running dangerous commands
    - Exceeding context budget thresholds

Integrates with RalphLoop via an approval callback that pauses execution
until user approves or rejects.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Callable, Optional


class ApprovalType(Enum):
    """Types of approval required."""
    COMMIT = auto()
    DANGEROUS_COMMAND = auto()
    CONTEXT_THRESHOLD = auto()
    LARGE_CHANGE = auto()


@dataclass
class ApprovalRequest:
    """An approval request waiting for user response."""
    type: ApprovalType
    description: str
    details: dict
    timestamp: str
    blocking: bool = True


class ApprovalWorkflow:
    """Manages approval requests and responses.

    Uses threading.Event for blocking wait on the RalphLoop thread,
    while the TUI main thread consumes approval commands and triggers
    the event to resume.
    """

    def __init__(
        self,
        on_approve: Callable[[ApprovalRequest], None],
        on_reject: Callable[[ApprovalRequest], None],
    ):
        self._on_approve = on_approve
        self._on_reject = on_reject
        self._pending: Optional[ApprovalRequest] = None
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._approved: bool = False

    def request_approval(self, request: ApprovalRequest) -> bool:
        """Request approval - blocks until user responds.

        Args:
            request: The approval request.

        Returns:
            True if approved, False if rejected.
        """
        with self._lock:
            self._pending = request
            self._event.clear()
            self._approved = False

        # Block the calling thread (RalphLoop state executor)
        self._event.wait()

        with self._lock:
            self._pending = None
            return self._approved

    def approve(self) -> None:
        """User approved the pending request."""
        with self._lock:
            self._approved = True
        self._event.set()
        if self._pending and self._on_approve is not None:
            self._on_approve(self._pending)

    def reject(self) -> None:
        """User rejected the pending request."""
        with self._lock:
            self._approved = False
        self._event.set()
        if self._pending and self._on_reject is not None:
            self._on_reject(self._pending)

    @property
    def pending(self) -> Optional[ApprovalRequest]:
        """Get pending approval request if any."""
        with self._lock:
            return self._pending
