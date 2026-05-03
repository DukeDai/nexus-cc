"""Context budget monitor with 4-tier degradation tracking."""

from enum import Enum
from typing import Optional


class BudgetTier(Enum):
    """Four-tier context budget degradation model."""

    PEAK = "peak"       # 0-30%: Full operations
    GOOD = "good"       # 30-50%: Normal ops, prefer frontmatter reads
    DEGRADING = "degrading"  # 50-70%: Economize, warn user
    POOR = "poor"       # 70%+: Checkpoint and stop immediately


# Tier thresholds (fraction of max_context_tokens used)
_TIER_THRESHOLDS = {
    BudgetTier.PEAK: 0.30,
    BudgetTier.GOOD: 0.50,
    BudgetTier.DEGRADING: 0.70,
    # POOR is anything >= 0.70
}


class ContextBudgetMonitor:
    """Monitor context budget and provide tier-based degradation guidance."""

    def __init__(
        self,
        max_context_tokens: int = 200_000,
        current_tokens: int = 0,
    ) -> None:
        """
        Initialize the context budget monitor.

        Args:
            max_context_tokens: Maximum context window size (default: 200k).
            current_tokens: Current estimated token usage.
        """
        if max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")
        if current_tokens < 0:
            raise ValueError("current_tokens cannot be negative")
        if current_tokens > max_context_tokens:
            raise ValueError("current_tokens cannot exceed max_context_tokens")

        self._max_context_tokens = max_context_tokens
        self._current_tokens = current_tokens

    @property
    def max_context_tokens(self) -> int:
        """Maximum context window size in tokens."""
        return self._max_context_tokens

    @property
    def current_tokens(self) -> int:
        """Current estimated token usage."""
        return self._current_tokens

    @current_tokens.setter
    def current_tokens(self, value: int) -> None:
        """Set current token count with validation."""
        if value < 0:
            raise ValueError("current_tokens cannot be negative")
        if value > self._max_context_tokens:
            raise ValueError(
                f"current_tokens ({value}) cannot exceed max_context_tokens "
                f"({self._max_context_tokens})"
            )
        self._current_tokens = value

    @property
    def usage_fraction(self) -> float:
        """Fraction of context budget used (0.0 to 1.0)."""
        return self._current_tokens / self._max_context_tokens

    @property
    def remaining_tokens(self) -> int:
        """Tokens remaining in the context budget."""
        return self._max_context_tokens - self._current_tokens

    @property
    def tier(self) -> BudgetTier:
        """
        Current budget tier based on usage fraction.

        - PEAK (0-30%): Full operations
        - GOOD (30-50%): Normal operations, prefer frontmatter reads
        - DEGRADING (50-70%): Economize, warn user
        - POOR (70%+): Checkpoint and stop immediately
        """
        fraction = self.usage_fraction
        if fraction < _TIER_THRESHOLDS[BudgetTier.PEAK]:
            return BudgetTier.PEAK
        elif fraction < _TIER_THRESHOLDS[BudgetTier.GOOD]:
            return BudgetTier.GOOD
        elif fraction < _TIER_THRESHOLDS[BudgetTier.DEGRADING]:
            return BudgetTier.DEGRADING
        else:
            return BudgetTier.POOR

    def should_warn(self) -> bool:
        """
        Check if a warning should be issued.

        Returns True when tier is DEGRADING or POOR.
        """
        return self.tier in (BudgetTier.DEGRADING, BudgetTier.POOR)

    def should_checkpoint(self) -> bool:
        """
        Check if a checkpoint should be created.

        Returns True when tier is POOR (70%+ usage).
        """
        return self.tier == BudgetTier.POOR

    def add_tokens(self, count: int) -> None:
        """
        Add tokens to the current usage count.

        Args:
            count: Number of tokens to add.

        Raises:
            ValueError: If count is negative or would exceed max.
        """
        if count < 0:
            raise ValueError("Cannot add negative tokens")
        new_total = self._current_tokens + count
        if new_total > self._max_context_tokens:
            raise ValueError(
                f"Adding {count} tokens would exceed max_context_tokens. "
                f"Current: {self._current_tokens}, Max: {self._max_context_tokens}"
            )
        self._current_tokens = new_total

    def reset(self) -> None:
        """Reset token count to zero."""
        self._current_tokens = 0

    def to_dict(self) -> dict:
        """Serialize monitor state to a dictionary."""
        return {
            "max_context_tokens": self._max_context_tokens,
            "current_tokens": self._current_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextBudgetMonitor":
        """Deserialize monitor state from a dictionary."""
        return cls(
            max_context_tokens=data["max_context_tokens"],
            current_tokens=data["current_tokens"],
        )

    def __repr__(self) -> str:
        return (
            f"ContextBudgetMonitor("
            f"tier={self.tier.value}, "
            f"usage={self.usage_fraction:.1%}, "
            f"tokens={self._current_tokens}/{self._max_context_tokens})"
        )
