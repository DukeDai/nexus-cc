"""AgentPool — Load-aware task claiming for multi-agent execution.

Extends TaskGraph with:
- Per-agent load tracking (active task count)
- Load-aware claim eligibility
- Agent capacity management
- Automatic load balancing

Usage:
    pool = AgentPool(max_load_per_agent=3)
    pool.register_agent("agent-1")
    pool.register_agent("agent-2")

    # Load-aware claiming
    result = pool.claim_next_available("agent-1", task_graph)
    # agent-1 can only claim if their load < max_load

    # Query load
    load = pool.get_agent_load("agent-1")  # e.g., 2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from threading import Lock
from typing import Optional


class AgentStatus(Enum):
    """Agent availability status."""
    IDLE = auto()      # No tasks, available for claiming
    BUSY = auto()      # Has tasks in progress
    DRAINED = auto()   # At max capacity, not accepting new tasks


@dataclass
class AgentInfo:
    """Information about a registered agent."""
    agent_id: str
    active_tasks: set[str] = field(default_factory=set)  # task IDs
    max_load: int = 3
    registered_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_active: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_count: int = 0
    failed_count: int = 0

    @property
    def load(self) -> int:
        """Current number of active tasks."""
        return len(self.active_tasks)

    @property
    def status(self) -> AgentStatus:
        """Current agent status."""
        if self.load >= self.max_load:
            return AgentStatus.DRAINED
        elif self.load > 0:
            return AgentStatus.BUSY
        return AgentStatus.IDLE

    @property
    def can_accept_work(self) -> bool:
        """Whether agent can take more tasks."""
        return self.load < self.max_load


class AgentPool:
    """Pool of agents with load tracking and capacity management.

    Key features:
    - Register/deregister agents
    - Track active task count per agent
    - Load-aware eligibility checking
    - Automatic load balancing via claim ordering

    Usage:
        pool = AgentPool(max_load_per_agent=3)
        pool.register_agent("agent-1")
        pool.register_agent("agent-2")

        # When claiming tasks from a TaskGraph:
        if pool.can_claim("agent-1", task):
            pool.track_claim("agent-1", task.id)
            # ... do work ...
            pool.track_complete("agent-1", task.id)
    """

    DEFAULT_MAX_LOAD = 3
    DEFAULT_MAX_LOAD_GLOBAL = 10  # Total tasks across all agents

    def __init__(self, max_load_per_agent: int = DEFAULT_MAX_LOAD):
        self.max_load_per_agent = max_load_per_agent
        self._agents: dict[str, AgentInfo] = {}
        self._lock = Lock()

    def register_agent(self, agent_id: str, max_load: Optional[int] = None) -> AgentInfo:
        """Register an agent with the pool."""
        with self._lock:
            if agent_id in self._agents:
                return self._agents[agent_id]

            info = AgentInfo(
                agent_id=agent_id,
                max_load=max_load or self.max_load_per_agent,
            )
            self._agents[agent_id] = info
            return info

    def deregister_agent(self, agent_id: str) -> bool:
        """Remove an agent from the pool."""
        with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]
                return True
            return False

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """Get agent info."""
        return self._agents.get(agent_id)

    def get_agent_load(self, agent_id: str) -> int:
        """Get current load for an agent."""
        agent = self._agents.get(agent_id)
        return agent.load if agent else 0

    def get_agent_status(self, agent_id: str) -> AgentStatus:
        """Get agent availability status."""
        agent = self._agents.get(agent_id)
        return agent.status if agent else AgentStatus.DRAINED

    def can_claim(self, agent_id: str, task_id: Optional[str] = None) -> bool:
        """Check if agent can claim a task (load check).

        Args:
            agent_id: The agent wanting to claim
            task_id: Optional task ID for additional checks
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return False

        # Check individual capacity
        if not agent.can_accept_work:
            return False

        return True

    def claim_next_available(
        self,
        agent_id: str,
        task_graph,  # TaskGraph instance
    ) -> 'TaskClaimResult':  # Forward reference
        """Claim next available task with load awareness.

        Wrapper around task_graph.claim_next_available that first
        checks agent load.

        Returns:
            TaskClaimResult from the task graph
        """
        from .task_graph import TaskClaimResult

        if not self.can_claim(agent_id):
            return TaskClaimResult(
                success=False,
                task=None,
                reason=f"Agent {agent_id} at max capacity ({self.get_agent_load(agent_id)})"
            )

        result = task_graph.claim_next_available(agent_id)

        if result.success and result.task:
            self.track_claim(agent_id, result.task.id)

        return result

    def track_claim(self, agent_id: str, task_id: str) -> bool:
        """Track that an agent claimed a task."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return False
            agent.active_tasks.add(task_id)
            agent.last_active = datetime.now().isoformat()
            return True

    def track_complete(self, agent_id: str, task_id: str, success: bool = True) -> bool:
        """Track task completion."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return False

            agent.active_tasks.discard(task_id)
            agent.last_active = datetime.now().isoformat()

            if success:
                agent.completed_count += 1
            else:
                agent.failed_count += 1

            return True

    def track_fail(self, agent_id: str, task_id: str) -> bool:
        """Track task failure (convenience method)."""
        return self.track_complete(agent_id, task_id, success=False)

    def get_idle_agents(self) -> list[str]:
        """Get list of agents with IDLE status."""
        with self._lock:
            return [
                aid for aid, info in self._agents.items()
                if info.status == AgentStatus.IDLE
            ]

    def get_available_agents(self) -> list[str]:
        """Get agents that can accept new work (IDLE or BUSY but not DRAINED)."""
        with self._lock:
            return [
                aid for aid, info in self._agents.items()
                if info.can_accept_work
            ]

    def get_least_loaded_agent(self) -> Optional[str]:
        """Get the agent with the lowest current load."""
        with self._lock:
            if not self._agents:
                return None

            return min(
                self._agents.keys(),
                key=lambda aid: self._agents[aid].load
            )

    def get_total_active_tasks(self) -> int:
        """Get total active tasks across all agents."""
        with self._lock:
            return sum(info.load for info in self._agents.values())

    def get_pool_stats(self) -> dict:
        """Get pool-wide statistics."""
        with self._lock:
            total_agents = len(self._agents)
            idle = sum(1 for a in self._agents.values() if a.status == AgentStatus.IDLE)
            busy = sum(1 for a in self._agents.values() if a.status == AgentStatus.BUSY)
            drained = sum(1 for a in self._agents.values() if a.status == AgentStatus.DRAINED)
            total_active = sum(a.load for a in self._agents.values())
            total_completed = sum(a.completed_count for a in self._agents.values())

            return {
                "total_agents": total_agents,
                "idle": idle,
                "busy": busy,
                "drained": drained,
                "total_active_tasks": total_active,
                "total_completed": total_completed,
                "avg_load": total_active / total_agents if total_agents else 0.0,
            }

    def get_agent_stats(self, agent_id: str) -> dict:
        """Get detailed stats for a specific agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return {}

        return {
            "agent_id": agent.agent_id,
            "status": agent.status.name,
            "load": agent.load,
            "max_load": agent.max_load,
            "active_tasks": list(agent.active_tasks),
            "completed": agent.completed_count,
            "failed": agent.failed_count,
            "registered_at": agent.registered_at,
            "last_active": agent.last_active,
        }

    def clear(self) -> None:
        """Clear all agents from the pool."""
        with self._lock:
            self._agents.clear()