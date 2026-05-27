"""Cross-Session RL Strategy Learning — Self-Evolution from Session Outcomes.

This module adds learning feedback loop:
- SelfEvolutionEngine cross-session strategy learning
- Learn from SessionOutcome: what strategy works in what context
- Update strategy selection model
- Persist learned policies

Key insight: 跨会话强化学习——什么策略在什么上下文下有效.
This builds on the AdaptiveReasoningConfig which only does intra-session learning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional
import json
import os
from threading import Lock


class StrategyType(Enum):
    """Strategy types for task execution."""
    DIRECT = auto()           # Execute directly
    DECOMPOSE = auto()        # Break into subtasks
    RETRY = auto()           # Retry on failure
    ESCALATE = auto()         # Escalate to human
    PARALLEL = auto()         # Execute in parallel
    VERIFY_FIRST = auto()     # Verify before proceeding
    SIMPLIFY = auto()         # Simplify approach


class ContextFeature(Enum):
    """Context features that influence strategy selection."""
    COMPLEXITY_HIGH = auto()
    COMPLEXITY_LOW = auto()
    TIME_PRESSURE_HIGH = auto()
    ERROR_HISTORY_HIGH = auto()
    CONTEXT_BUDGET_LOW = auto()
    SUCCESS_STREAK_HIGH = auto()


@dataclass
class ContextVector:
    """A vector of context features.

    Represents the context state for strategy selection.
    """
    complexity: float = 0.5        # 0.0-1.0 (0=simple, 1=complex)
    time_pressure: float = 0.5     # 0.0-1.0
    error_history: float = 0.0      # 0.0-1.0 (error rate)
    context_budget: float = 1.0    # 0.0-1.0 (remaining)
    success_streak: int = 0        # Consecutive successes

    def to_features(self) -> list[float]:
        """Convert to feature vector for learning."""
        return [
            self.complexity,
            self.time_pressure,
            self.error_history,
            self.context_budget,
            min(self.success_streak / 10.0, 1.0),  # Normalize to 0-1
        ]

    def to_context_set(self) -> set[ContextFeature]:
        """Convert to discrete context features."""
        features = set()
        if self.complexity > 0.7:
            features.add(ContextFeature.COMPLEXITY_HIGH)
        elif self.complexity < 0.3:
            features.add(ContextFeature.COMPLEXITY_LOW)
        if self.time_pressure > 0.8:
            features.add(ContextFeature.TIME_PRESSURE_HIGH)
        if self.error_history > 0.5:
            features.add(ContextFeature.ERROR_HISTORY_HIGH)
        if self.context_budget < 0.3:
            features.add(ContextFeature.CONTEXT_BUDGET_LOW)
        if self.success_streak > 5:
            features.add(ContextFeature.SUCCESS_STREAK_HIGH)
        return features


@dataclass
class StrategyOutcome:
    """Outcome of applying a strategy."""
    strategy: StrategyType
    context: ContextVector
    success: bool
    duration_ms: float
    context_used: float  # Context budget consumed
    error_category: Optional[str] = None

    @property
    def reward(self) -> float:
        """Compute reward for this outcome.

        Higher is better:
        - success = +1.0
        - failure = -0.5
        - Time efficiency bonus
        - Context efficiency bonus
        """
        base = 1.0 if self.success else -0.5

        # Time efficiency (faster is better)
        time_bonus = max(0, 1.0 - (self.duration_ms / 60000.0)) * 0.2  # Up to 0.2 for <1min

        # Context efficiency (less is better)
        context_bonus = max(0, 0.5 - self.context_used / 100.0) * 0.3  # Up to 0.3 for low usage

        return base + time_bonus + context_bonus


@dataclass
class StrategyPolicy:
    """A learned policy mapping contexts to strategies.

    For each context vector, stores Q-values for each strategy.
    Q(s, a) = expected reward from taking action a in state s
    """
    context_features: set[ContextFeature]
    q_values: dict[StrategyType, float]  # Q(s, a) for each strategy

    def get_best_strategy(self) -> StrategyType:
        """Get the strategy with highest Q-value."""
        if not self.q_values:
            return StrategyType.DIRECT
        return max(self.q_values, key=lambda s: self.q_values.get(s, 0.0))

    def update_q(self, strategy: StrategyType, reward: float, learning_rate: float = 0.1) -> None:
        """Update Q-value using Q-learning update.

        Q(s, a) = Q(s, a) + alpha * (reward - Q(s, a))
        """
        current = self.q_values.get(strategy, 0.0)
        self.q_values[strategy] = current + learning_rate * (reward - current)


@dataclass
class SessionOutcome:
    """Complete outcome of a session for learning.

    Aggregates all strategy outcomes from a session.
    """
    session_id: str
    start_time: str
    end_time: str
    task_description: str
    final_success: bool
    total_duration_ms: float
    context_used: float
    strategy_outcomes: list[StrategyOutcome] = field(default_factory=list)

    @property
    def average_reward(self) -> float:
        """Average reward across all strategy outcomes."""
        if not self.strategy_outcomes:
            return 0.0
        return sum(o.reward for o in self.strategy_outcomes) / len(self.strategy_outcomes)

    @property
    def success_rate(self) -> float:
        """Success rate across strategies."""
        if not self.strategy_outcomes:
            return 0.0
        successes = sum(1 for o in self.strategy_outcomes if o.success)
        return successes / len(self.strategy_outcomes)


class SelfEvolutionEngine:
    """Cross-session strategy learning engine.

    Uses Q-learning to learn which strategies work in which contexts:
    - Maintains policy per context type
    - Updates Q-values from session outcomes
    - Persists policies to disk for cross-session learning

    Usage:
        engine = SelfEvolutionEngine()
        engine.record_strategy_outcome(outcome)
        best = engine.get_best_strategy_for_context(context)
        engine.record_session_outcome(session_outcome)  # For persistence
    """

    # Learning hyperparameters
    LEARNING_RATE = 0.1
    DISCOUNT_FACTOR = 0.9
    EXPLORATION_RATE = 0.2  # Epsilon-greedy exploration

    # Policy storage
    POLICY_DIR = ".ralphloop/policies"
    POLICY_FILE = "strategy_policies.json"

    def __init__(self):
        self._policies: dict[frozenset[ContextFeature], StrategyPolicy] = {}
        self._session_history: list[SessionOutcome] = []
        self._current_session_outcomes: list[StrategyOutcome] = []
        self._lock = Lock()
        self._load_policies()

    # ─── Strategy Outcome Recording ──────────────────────────────────────────

    def record_strategy_outcome(self, outcome: StrategyOutcome) -> None:
        """Record a single strategy outcome for learning.

        This updates the Q-values immediately for online learning.
        """
        with self._lock:
            self._current_session_outcomes.append(outcome)

            # Get context features
            context_features = frozenset(outcome.context.to_context_set())

            # Get or create policy for this context
            if context_features not in self._policies:
                self._policies[context_features] = StrategyPolicy(
                    context_features=set(context_features),
                    q_values={s: 0.0 for s in StrategyType}
                )

            policy = self._policies[context_features]

            # Update Q-value for the strategy used
            policy.update_q(outcome.strategy, outcome.reward, self.LEARNING_RATE)

    def get_best_strategy_for_context(self, context: ContextVector) -> StrategyType:
        """Get the best strategy for a given context.

        Uses epsilon-greedy exploration:
        - With probability EXPLORATION_RATE, explore (random)
        - Otherwise, exploit (use best known strategy)
        """
        with self._lock:
            context_features = frozenset(context.to_context_set())
            policy = self._policies.get(context_features)

            if not policy or not policy.q_values:
                return StrategyType.DIRECT

            import random
            if random.random() < self.EXPLORATION_RATE:
                # Explore: pick random strategy
                return random.choice(list(StrategyType))

            # Exploit: pick best known
            return policy.get_best_strategy()

    # ─── Session Recording ──────────────────────────────────────────────────

    def start_session(self, session_id: str, task_description: str) -> None:
        """Start recording a new session."""
        self._current_session_outcomes = []

    def end_session(
        self,
        session_id: str,
        task_description: str,
        final_success: bool,
        total_duration_ms: float,
        context_used: float
    ) -> SessionOutcome:
        """End recording a session and store outcome."""
        outcome = SessionOutcome(
            session_id=session_id,
            start_time=datetime.now().isoformat(),
            end_time=datetime.now().isoformat(),
            task_description=task_description,
            final_success=final_success,
            total_duration_ms=total_duration_ms,
            context_used=context_used,
            strategy_outcomes=list(self._current_session_outcomes)
        )

        with self._lock:
            self._session_history.append(outcome)
            self._current_session_outcomes = []

            # Trim history if too long
            if len(self._session_history) > 100:
                self._session_history = self._session_history[-100:]

        # Persist policies
        self._save_policies()

        return outcome

    # ─── Policy Persistence ──────────────────────────────────────────────────

    def _get_policy_path(self) -> str:
        """Get path for policy persistence."""
        return os.path.join(self.POLICY_DIR, self.POLICY_FILE)

    def _load_policies(self) -> None:
        """Load policies from disk."""
        path = self._get_policy_path()
        if not os.path.exists(path):
            return

        try:
            with open(path, 'r') as f:
                data = json.load(f)

            for key, value in data.items():
                features = frozenset([ContextFeature[int(f)] for f in value.get('features', [])])
                q_values = {StrategyType[int(k)]: v for k, v in value.get('q_values', {}).items()}
                self._policies[features] = StrategyPolicy(
                    context_features=set(features),
                    q_values=q_values
                )
        except Exception:
            pass  # Start fresh if load fails

    def _save_policies(self) -> None:
        """Save policies to disk."""
        os.makedirs(self.POLICY_DIR, exist_ok=True)
        path = self._get_policy_path()

        data = {}
        for features, policy in self._policies.items():
            data[str(hash(features))] = {
                'features': [f.value for f in features],
                'q_values': {s.value: policy.q_values.get(s, 0.0) for s in StrategyType}
            }

        try:
            with open(path, 'w') as f:
                json.dump(data, f)
        except Exception:
            pass  # Best effort save

    # ─── Strategy Recommendations ───────────────────────────────────────────

    def get_recommendation(self, context: ContextVector) -> dict:
        """Get full recommendation for a context.

        Returns:
            dict with best_strategy, confidence, alternatives
        """
        context_features = context.to_context_set()
        policy = self._policies.get(frozenset(context_features))

        if not policy or not policy.q_values:
            return {
                "strategy": StrategyType.DIRECT.name,
                "confidence": 0.0,
                "alternatives": []
            }

        best_strategy = policy.get_best_strategy()
        best_q = policy.q_values.get(best_strategy, 0.0)

        # Sort strategies by Q-value
        sorted_strategies = sorted(
            policy.q_values.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # Confidence = how much better is best vs random
        if len(sorted_strategies) > 1:
            second_best = sorted_strategies[1][1]
            confidence = (best_q - second_best) / max(abs(second_best) + 0.1, 0.1)
            confidence = max(0.0, min(1.0, confidence))
        else:
            confidence = 0.5

        alternatives = [
            {"strategy": s.name, "q_value": q}
            for s, q in sorted_strategies[:3]
            if s != best_strategy
        ]

        return {
            "strategy": best_strategy.name,
            "confidence": confidence,
            "q_value": best_q,
            "alternatives": alternatives,
            "context_features": [f.name for f in context_features]
        }

    def get_stats(self) -> dict:
        """Get engine statistics."""
        return {
            "policy_count": len(self._policies),
            "session_history_count": len(self._session_history),
            "current_session_outcomes": len(self._current_session_outcomes),
            "exploration_rate": self.EXPLORATION_RATE,
        }

    def reset_learning(self) -> None:
        """Reset all learned policies."""
        with self._lock:
            self._policies.clear()
            self._session_history.clear()
            self._current_session_outcomes = []
            self._save_policies()