"""Causal Error Graph — Root Cause Inference from Failed Trajectories.

This module replaces static recovery_hint with causal inference:
- Build error chains from tool_call sequences
- Distinguish root causes (network latency vs real permission issues)
- Extract actionable context for decision-making

Key insight: NOT all errors are equal. "Permission denied" after a timeout
has different root cause than "Permission denied" in isolation.

The main task only receives:
1. Causal chain (what caused what)
2. Root cause summary
3. Actionable recommendations (not full traces)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional
import hashlib


class CausalLinkType(Enum):
    """Types of causal relationships between events."""
    TIMEOUT = auto()         # A caused B to timeout
    PERMISSION_ERROR = auto()  # A's permission error caused B's failure
    DEPENDENCY_FAILURE = auto()  # A's failure caused B to fail
    RESOURCE_CONTENTION = auto()  # A consumed resources B needed
    UNRELATED = auto()       # A and B are coincidental


@dataclass
class CausalNode:
    """A single node in the causal graph.

    Attributes:
        event_id: Unique identifier for this event
        event_type: What kind of event (tool_call, error, result)
        description: Human-readable description
        timestamp: When this event occurred
        metadata: Additional event-specific data
        parent_links: Causal links leading TO this node (causes)
        child_links: Causal links leading FROM this node (effects)
    """
    event_id: str
    event_type: str  # "tool_call", "error", "timeout", "result"
    description: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: dict = field(default_factory=dict)
    parent_links: list[CausalLink] = field(default_factory=list)
    child_links: list[CausalLink] = field(default_factory=list)

    def is_root_cause(self) -> bool:
        """Check if this node has no parents (root cause)."""
        return len(self.parent_links) == 0

    def is_symptom(self) -> bool:
        """Check if this node has no children (symptom)."""
        return len(self.child_links) == 0

    def get_root_causes(self) -> list["CausalNode"]:
        """Walk up the tree to find root causes."""
        if self.is_root_cause():
            return [self]
        roots = []
        for link in self.parent_links:
            roots.extend(link.source.get_root_causes())
        return roots

    def get_causal_chain(self) -> list["CausalNode"]:
        """Get the full causal chain from root to this node."""
        if self.is_root_cause():
            return [self]
        chain = []
        for link in self.parent_links:
            chain.extend(link.source.get_causal_chain())
        chain.append(self)
        return chain


@dataclass
class CausalLink:
    """A directed edge in the causal graph."""
    source: CausalNode
    target: CausalNode
    link_type: CausalLinkType
    confidence: float = 1.0  # 0.0-1.0, how certain is this link
    reason: str = ""  # Why we believe this link exists


class CausalGraph:
    """A directed graph of cause-effect relationships.

    Built from a sequence of tool calls, errors, and results.
    Used to infer root causes and actionable recommendations.
    """

    def __init__(self):
        self.nodes: dict[str, CausalNode] = {}
        self._event_sequence: list[dict] = []

    def add_node(self, node: CausalNode) -> None:
        """Add a node to the graph."""
        self.nodes[node.event_id] = node

    def get_node(self, event_id: str) -> Optional[CausalNode]:
        """Get a node by ID."""
        return self.nodes.get(event_id)

    def add_link(self, source: CausalNode, target: CausalNode, link_type: CausalLinkType, reason: str = "") -> CausalLink:
        """Add a causal link between two nodes."""
        link = CausalLink(source=source, target=target, link_type=link_type, reason=reason)
        source.child_links.append(link)
        target.parent_links.append(link)
        return link

    def get_root_causes(self) -> list[CausalNode]:
        """Find all root cause nodes (nodes with no parents)."""
        return [n for n in self.nodes.values() if n.is_root_cause()]

    def get_symptoms(self) -> list[CausalNode]:
        """Find all symptom nodes (nodes with no children)."""
        return [n for n in self.nodes.values() if n.is_symptom()]

    def to_trace(self) -> str:
        """Generate a readable causal trace."""
        roots = self.get_root_causes()
        traces = []
        for root in roots:
            chain = root.get_causal_chain()
            trace = " → ".join(n.description[:50] for n in chain)
            traces.append(trace)
        return "\n".join(traces)


# ─── Causal Inference Engine ────────────────────────────────────────────────


def _infer_causal_link_type(
    earlier_node: CausalNode,
    later_node: CausalNode,
    sequence_index: int
) -> CausalLinkType:
    """Infer the type of causal relationship between two events."""
    earlier_type = earlier_node.event_type
    later_type = later_node.event_type

    # Timeout patterns
    if earlier_node.metadata.get("timeout") and later_type == "error":
        return CausalLinkType.TIMEOUT

    # Permission error patterns
    if earlier_node.metadata.get("permission_error") or later_node.metadata.get("permission_error"):
        return CausalLinkType.PERMISSION_ERROR

    # Dependency: if earlier failed, later likely failed due to dependency
    if earlier_node.metadata.get("success") is False and later_type == "error":
        return CausalLinkType.DEPENDENCY_FAILURE

    # Sequential tool calls with errors
    if earlier_type == "tool_call" and later_type == "error":
        return CausalLinkType.DEPENDENCY_FAILURE

    return CausalLinkType.UNRELATED


def _generate_event_id(event_type: str, description: str, index: int) -> str:
    """Generate a deterministic event ID."""
    content = f"{event_type}:{description}:{index}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


@dataclass
class ActionableRecommendation:
    """A single actionable recommendation extracted from causal graph."""
    action: str  # e.g., "extend_timeout", "narrow_permission_scope"
    target: str  # Which tool/task this applies to
    confidence: float  # How confident we are
    reason: str  # Why this action is recommended
    root_cause_id: str  # Which root cause this addresses


@dataclass
class CausalAnalysisResult:
    """Result of causal error analysis.

    This is what the main task receives — NOT full error traces.
    Contains only causal graph, root causes, and recommendations.
    """
    original_error: str
    causal_graph: CausalGraph
    root_causes: list[str]  # Human-readable root cause descriptions
    causal_trace: str  # Human-readable causal chain
    recommendations: list[ActionableRecommendation]
    error_category: str  # Classification from causal analysis
    recovery_strategy: str  # Recommended high-level strategy

    def to_clean_context(self) -> dict:
        """Generate clean context for orchestrator decision-making.

        Main task receives ONLY this — no full traces.
        """
        return {
            "root_causes": self.root_causes,
            "causal_trace": self.causal_trace,
            "recovery_strategy": self.recovery_strategy,
            "recommendations": [
                {
                    "action": r.action,
                    "target": r.target,
                    "reason": r.reason
                }
                for r in self.recommendations
            ],
            "error_category": self.error_category
        }


class CausalErrorAnalyzer:
    """Build causal graphs from failed trajectories.

    This replaces static recovery_hint generation with causal inference.

    Usage:
        analyzer = CausalErrorAnalyzer()
        result = analyzer.analyze(
            phase="ACT",
            tool_calls=[...],
            error="Tool call failed after timeout"
        )
        decision_context = result.to_clean_context()  # For orchestrator
    """

    # Patterns that indicate specific root causes
    TIMEOUT_PATTERNS = [
        "timeout", "timed out", "took too long", "exceeded",
        "deadline", "request timeout"
    ]

    PERMISSION_PATTERNS = [
        "permission", "denied", "unauthorized", "forbidden",
        "access denied", "not allowed", " insufficient permissions"
    ]

    RESOURCE_PATTERNS = [
        "memory", "quota", "rate limit", "budget", "exhausted",
        "too many", "limit exceeded"
    ]

    def analyze(
        self,
        phase: str,
        tool_calls: list[dict],
        error: str
    ) -> CausalAnalysisResult:
        """Build causal graph from failed trajectory.

        Args:
            phase: RalphLoop phase where failure occurred
            tool_calls: The tool call sequence that failed
            error: The final error message

        Returns:
            CausalAnalysisResult with causal graph and recommendations
        """
        graph = CausalGraph()

        if not tool_calls:
            # No tool calls — pure error analysis
            return self._analyze_pure_error(error)

        # Build nodes for each tool call
        nodes_by_index: dict[int, CausalNode] = {}

        for i, tc in enumerate(tool_calls):
            tool_name = tc.get("name", "unknown")
            tool_args = tc.get("args", {})
            tool_result = tc.get("result", {})
            success = tool_result.get("success", True) if tool_result else True

            metadata = {
                "tool_name": tool_name,
                "args": tool_args,
                "success": success,
                "timeout": False,
                "permission_error": False,
            }

            # Detect error types in result
            if tool_result and not success:
                result_str = str(tool_result.get("output", ""))
                metadata["timeout"] = any(p in result_str.lower() for p in self.TIMEOUT_PATTERNS)
                metadata["permission_error"] = any(p in result_str.lower() for p in self.PERMISSION_PATTERNS)
                metadata["resource_error"] = any(p in result_str.lower() for p in self.RESOURCE_PATTERNS)

            node = CausalNode(
                event_id=_generate_event_id("tool_call", tool_name, i),
                event_type="tool_call",
                description=f"{tool_name}({self._summarize_args(tool_args)})",
                metadata=metadata
            )
            graph.add_node(node)
            nodes_by_index[i] = node

        # Add error node as the terminal event
        error_node = CausalNode(
            event_id=_generate_event_id("error", error[:50], len(tool_calls)),
            event_type="error",
            description=error[:100],
            metadata={"final_error": True}
        )
        graph.add_node(error_node)

        # Build causal links between sequential events
        indices = sorted(nodes_by_index.keys())
        for i in range(len(indices) - 1):
            earlier = nodes_by_index[indices[i]]
            later = nodes_by_index[indices[i + 1]]

            link_type = _infer_causal_link_type(earlier, later, i)
            graph.add_link(earlier, later, link_type, reason=f"Sequential dependency at step {i}")

        # Link last tool to error
        last_tool = nodes_by_index[indices[-1]]
        link_type = _infer_causal_link_type(last_tool, error_node, len(indices))
        graph.add_link(last_tool, error_node, link_type, reason="Terminal failure")

        # Infer root causes and recommendations
        root_causes = self._infer_root_causes(graph)
        recommendations = self._generate_recommendations(graph, root_causes)
        error_category = self._classify_from_causal_graph(graph, error)
        recovery_strategy = self._determine_recovery_strategy(error_category, root_causes)

        return CausalAnalysisResult(
            original_error=error,
            causal_graph=graph,
            root_causes=root_causes,
            causal_trace=graph.to_trace(),
            recommendations=recommendations,
            error_category=error_category,
            recovery_strategy=recovery_strategy
        )

    def _analyze_pure_error(self, error: str) -> CausalAnalysisResult:
        """Analyze an error with no tool call history."""
        graph = CausalGraph()

        # Simple single-node analysis
        error_node = CausalNode(
            event_id=_generate_event_id("error", error[:50], 0),
            event_type="error",
            description=error[:100],
            metadata={}
        )
        graph.add_node(error_node)

        category = self._classify_error_string(error)
        strategy = self._determine_recovery_strategy(category, [error[:80]])

        return CausalAnalysisResult(
            original_error=error,
            causal_graph=graph,
            root_causes=[error[:80]],
            causal_trace=error[:100],
            recommendations=[],
            error_category=category,
            recovery_strategy=strategy
        )

    def _infer_root_causes(self, graph: CausalGraph) -> list[str]:
        """Infer root causes from the causal graph."""
        roots = graph.get_root_causes()
        causes = []

        for root in roots:
            if root.event_type == "tool_call":
                meta = root.metadata
                if meta.get("timeout"):
                    causes.append(f"Timeout in {meta.get('tool_name', 'unknown')}")
                elif meta.get("permission_error"):
                    causes.append(f"Permission error in {meta.get('tool_name', 'unknown')}")
                elif meta.get("resource_error"):
                    causes.append(f"Resource exhaustion in {meta.get('tool_name', 'unknown')}")
                elif meta.get("success") is False:
                    causes.append(f"Tool failure in {meta.get('tool_name', 'unknown')}")
                else:
                    causes.append(f"Root: {root.description[:60]}")
            else:
                causes.append(f"Root: {root.description[:60]}")

        return causes if causes else ["Unknown root cause"]

    def _generate_recommendations(
        self,
        graph: CausalGraph,
        root_causes: list[str]
    ) -> list[ActionableRecommendation]:
        """Generate actionable recommendations from causal graph."""
        recommendations = []
        roots = graph.get_root_causes()

        for root in roots:
            meta = root.metadata
            tool_name = meta.get("tool_name", "unknown")

            if meta.get("timeout"):
                recommendations.append(ActionableRecommendation(
                    action="extend_timeout",
                    target=tool_name,
                    confidence=0.9,
                    reason=f"Timeout detected in {tool_name} — extend timeout value",
                    root_cause_id=root.event_id
                ))
                # Check if subsequent tools also failed (cascading timeout)
                has_cascading = any(
                    link.link_type == CausalLinkType.TIMEOUT
                    for link in root.child_links
                )
                if has_cascading:
                    recommendations.append(ActionableRecommendation(
                        action="decompose_task",
                        target=tool_name,
                        confidence=0.8,
                        reason="Cascading timeout suggests task too large — decompose",
                        root_cause_id=root.event_id
                    ))

            elif meta.get("permission_error"):
                recommendations.append(ActionableRecommendation(
                    action="narrow_permission_scope",
                    target=tool_name,
                    confidence=0.9,
                    reason=f"Permission error in {tool_name} — check required permissions",
                    root_cause_id=root.event_id
                ))
                recommendations.append(ActionableRecommendation(
                    action="escalate",
                    target="user",
                    confidence=0.7,
                    reason="Permission issues may require user authorization",
                    root_cause_id=root.event_id
                ))

            elif meta.get("resource_error"):
                recommendations.append(ActionableRecommendation(
                    action="decompose_task",
                    target=tool_name,
                    confidence=0.9,
                    reason="Resource exhaustion — break into smaller units",
                    root_cause_id=root.event_id
                ))

        # If no specific recommendations, add generic one
        if not recommendations:
            recommendations.append(ActionableRecommendation(
                action="retry_with_alternative",
                target="task",
                confidence=0.5,
                reason="No specific root cause identified — try alternative approach",
                root_cause_id=""
            ))

        return recommendations

    def _classify_from_causal_graph(self, graph: CausalGraph, error: str) -> str:
        """Classify error type based on causal graph structure."""
        roots = graph.get_root_causes()

        # Check for permission patterns
        for root in roots:
            if root.metadata.get("permission_error"):
                return "PERMISSION_DENIED"
            if root.metadata.get("timeout"):
                return "TIMEOUT"
            if root.metadata.get("resource_error"):
                return "RESOURCE_EXHAUSTED"

        # Fall back to string classification
        return self._classify_error_string(error)

    def _classify_error_string(self, error: str) -> str:
        """Classify error from string content."""
        error_lower = error.lower()

        if any(p in error_lower for p in self.PERMISSION_PATTERNS):
            return "PERMISSION_DENIED"
        if any(p in error_lower for p in self.TIMEOUT_PATTERNS):
            return "TIMEOUT"
        if any(p in error_lower for p in self.RESOURCE_PATTERNS):
            return "RESOURCE_EXHAUSTED"

        return "UNKNOWN"

    def _determine_recovery_strategy(
        self,
        error_category: str,
        root_causes: list[str]
    ) -> str:
        """Determine recovery strategy from error category and root causes."""
        category_strategies = {
            "PERMISSION_DENIED": "escalate",
            "RESOURCE_EXHAUSTED": "decompose",
            "TIMEOUT": "retry_with_timeout_extended",
            "UNKNOWN": "retry_then_escalate"
        }
        return category_strategies.get(error_category, "retry_then_escalate")

    def _summarize_args(self, args: dict) -> str:
        """Summarize tool arguments for readability."""
        if not args:
            return ""
        # Just show keys to keep it short
        keys = list(args.keys())[:3]
        if len(args) > 3:
            return f"... ({len(args)} args)"
        return ", ".join(keys)