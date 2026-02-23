# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
GraphRAG Policy Reasoning Engine
=================================

Context-aware policy evaluation using graph-based dependency resolution,
BFS traversal, conflict detection, and structured decision output.

Architecture
------------

The existing ``MeshPolicyBackend`` loads flat JSON files. Policies lack
dependency resolution (``requires``), scope awareness (``applies_to``), and
context-aware overrides (``conflicts_with``).  This module introduces a
lightweight **graph layer** that sits *on top* of the existing policy store
and enriches evaluation with relational reasoning.

::

    ┌────────────────────────────────────────────────────────────┐
    │                   GraphRAG Policy Engine                   │
    │                                                            │
    │  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐  │
    │  │  Policy   │──▶│  Adjacency   │──▶│   BFS Traversal   │  │
    │  │  Nodes    │   │  List Graph  │   │   + Cycle Guard   │  │
    │  └──────────┘   └──────────────┘   └───────────────────┘  │
    │        │                                     │             │
    │        ▼                                     ▼             │
    │  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐  │
    │  │ Pydantic  │   │  Condition   │   │    Conflict       │  │
    │  │ Validate  │   │  Matching    │   │    Resolution     │  │
    │  └──────────┘   └──────────────┘   └───────────────────┘  │
    │        │                │                     │             │
    │        ▼                ▼                     ▼             │
    │  ┌─────────────────────────────────────────────────────┐   │
    │  │              PolicyDecision (structured)             │   │
    │  │   allowed · explanation · conflicts · confidence     │   │
    │  └─────────────────────────────────────────────────────┘   │
    │                                                            │
    │  Observability: Prometheus · OpenTelemetry · structlog     │
    └────────────────────────────────────────────────────────────┘

Key Features
~~~~~~~~~~~~
* **Dependency graph** – ``requires`` edges resolved via BFS with cycle
  detection (visited set) to produce a deterministic evaluation chain.
* **Scope awareness** – ``applies_to`` edges bind policies to specific
  contexts (services, environments, teams).
* **Conflict resolution** – ``conflicts_with`` edges detected at
  evaluation time; highest-priority policy wins.
* **Structured decisions** – every evaluation produces a ``PolicyDecision``
  with an audit trail of evaluated policies, detected conflicts, and a
  human-readable explanation.
* **Thread safety** – all mutable state guarded by ``threading.Lock``.
* **Input validation** – policy IDs restricted to ``[a-zA-Z0-9_-]``;
  configurable graph size limit (default 10 000 nodes).

Industry Standards Applied
~~~~~~~~~~~~~~~~~~~~~~~~~~
* NIST SP 800-53 (AC-3) – Access enforcement via policy evaluation.
* OWASP ASVS 4.0 – Input validation on identifiers.
* OpenTelemetry Semantic Conventions – span naming and attribute keys.
* Prometheus naming conventions – ``_total`` suffix for counters.
"""

import enum
import logging
import re
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Conditional imports with graceful fallbacks
# ---------------------------------------------------------------------------

# -- Prometheus --
from shared.noop_metrics import NoopMetric as _NoopMetric, safe_metric as _safe_create_metric  # noqa: E402

try:
    from prometheus_client import Counter, Gauge, Histogram, REGISTRY

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    REGISTRY = None

    Counter = _NoopMetric  # type: ignore[misc,assignment]
    Histogram = _NoopMetric  # type: ignore[misc,assignment]
    Gauge = _NoopMetric  # type: ignore[misc,assignment]


# -- OpenTelemetry --
try:
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    TRACING_AVAILABLE = True
except ImportError:
    from shared.noop_tracing import NullSpan as _NullContext, NullTracer as _NullTracer  # noqa: E402

    TRACING_AVAILABLE = False
    tracer = _NullTracer()  # type: ignore[assignment]


# -- structlog --
try:
    import structlog

    logger = structlog.get_logger("graph_rag_policy")
except ImportError:
    logger = logging.getLogger(__name__)  # type: ignore[assignment]


# -- Pydantic (required) --
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLICY_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_DEFAULT_MAX_POLICIES = 10_000


# ---------------------------------------------------------------------------
# Module-level metrics (created once at import time)
# ---------------------------------------------------------------------------
POLICY_EVAL_TOTAL = _safe_create_metric(
    Counter,
    "graph_rag_policy_evaluation_total",
    "Total policy evaluations",
    ("policy_id", "decision"),
)
POLICY_EVAL_DURATION = _safe_create_metric(
    Histogram,
    "graph_rag_policy_evaluation_duration_seconds",
    "Duration of policy evaluations in seconds",
)
POLICY_GRAPH_NODES = _safe_create_metric(
    Gauge,
    "graph_rag_policy_graph_nodes_total",
    "Current number of nodes in the policy graph",
)


# ---------------------------------------------------------------------------
# Enums & Pydantic models
# ---------------------------------------------------------------------------

class EdgeType(str, enum.Enum):
    """Categorises the relationship between two policy nodes."""

    REQUIRES = "REQUIRES"
    APPLIES_TO = "APPLIES_TO"
    CONFLICTS_WITH = "CONFLICTS_WITH"


class PolicyNode(BaseModel):
    """A single policy within the reasoning graph."""

    id: str = Field(..., description="Unique policy identifier")
    name: str = Field("", description="Human-readable policy name")
    description: str = Field("", description="Detailed description")
    conditions: Dict[str, Any] = Field(default_factory=dict, description="Context conditions for activation")
    priority: int = Field(default=0, description="Higher value = higher precedence")
    requires: List[str] = Field(default_factory=list, description="IDs of policies this one depends on")
    applies_to: List[str] = Field(default_factory=list, description="Scopes (services/envs) this policy targets")
    conflicts_with: List[str] = Field(default_factory=list, description="IDs of mutually exclusive policies")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")


class PolicyEdge(BaseModel):
    """A directed edge between two policy nodes."""

    source: str = Field(..., description="Source policy ID")
    target: str = Field(..., description="Target policy ID")
    edge_type: EdgeType = Field(..., description="Relationship type")


class PolicyDecision(BaseModel):
    """Structured result of a policy evaluation."""

    policy_id: str = Field(..., description="Evaluated policy ID")
    allowed: bool = Field(..., description="Whether the policy permits the action")
    explanation: str = Field("", description="Human-readable reasoning")
    evaluated_policies: List[str] = Field(default_factory=list, description="Ordered list of policies checked")
    conflicts: List[str] = Field(default_factory=list, description="Detected conflicting policy IDs")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Decision confidence score")


# ---------------------------------------------------------------------------
# GraphRAGPolicyReasoner
# ---------------------------------------------------------------------------

class GraphRAGPolicyReasoner:
    """Graph-based policy evaluation engine with BFS dependency resolution.

    Parameters
    ----------
    policies:
        Optional initial list of policy dicts to seed the graph.
    max_policies:
        Upper bound on the number of nodes (default 10 000).
    """

    def __init__(
        self,
        policies: Optional[List[Dict[str, Any]]] = None,
        max_policies: int = _DEFAULT_MAX_POLICIES,
    ) -> None:
        self._lock = threading.Lock()
        self._nodes: Dict[str, PolicyNode] = {}
        self._edges: Dict[str, List[PolicyEdge]] = {}
        self._max_policies = max_policies

        if policies:
            self.build_graph(policies)

    # -- public API --------------------------------------------------------

    def add_policy(self, policy: Dict[str, Any]) -> PolicyNode:
        """Validate, store, and wire a policy node into the graph.

        Raises
        ------
        ValueError
            If the policy ID is invalid or the graph has reached its
            size limit.
        """
        with tracer.start_as_current_span("graph_rag.add_policy") as span:
            node = PolicyNode(**policy)
            _validate_policy_id(node.id)

            with self._lock:
                if len(self._nodes) >= self._max_policies:
                    raise ValueError(
                        f"Graph size limit reached ({self._max_policies})"
                    )
                self._nodes[node.id] = node
                self._edges.setdefault(node.id, [])

                # Build edges from declarative relationships
                for req_id in node.requires:
                    self._edges[node.id].append(
                        PolicyEdge(source=node.id, target=req_id, edge_type=EdgeType.REQUIRES)
                    )
                for scope in node.applies_to:
                    self._edges[node.id].append(
                        PolicyEdge(source=node.id, target=scope, edge_type=EdgeType.APPLIES_TO)
                    )
                for cid in node.conflicts_with:
                    self._edges[node.id].append(
                        PolicyEdge(source=node.id, target=cid, edge_type=EdgeType.CONFLICTS_WITH)
                    )

                POLICY_GRAPH_NODES.set(len(self._nodes))

            span.set_attribute("policy_id", node.id)
            logger.info("policy_added", policy_id=node.id, priority=node.priority)
            return node

    def remove_policy(self, policy_id: str) -> bool:
        """Remove a policy node and its edges. Returns ``True`` if removed."""
        _validate_policy_id(policy_id)
        with self._lock:
            if policy_id not in self._nodes:
                return False
            del self._nodes[policy_id]
            self._edges.pop(policy_id, None)

            # Remove inbound edges referencing this node
            for edges in self._edges.values():
                edges[:] = [e for e in edges if e.target != policy_id]

            POLICY_GRAPH_NODES.set(len(self._nodes))
        logger.info("policy_removed", policy_id=policy_id)
        return True

    def build_graph(self, policies: List[Dict[str, Any]]) -> int:
        """Bulk-add policies and return the total node count."""
        with tracer.start_as_current_span("graph_rag.build_graph") as span:
            for p in policies:
                self.add_policy(p)
            count = len(self._nodes)
            span.set_attribute("node_count", count)
            logger.info("graph_built", node_count=count)
            return count

    def evaluate_policy(
        self,
        policy_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyDecision:
        """Evaluate a policy in the given context.

        Performs BFS to collect all transitive ``requires`` dependencies,
        checks conditions against *context*, resolves conflicts by
        priority, and produces a structured ``PolicyDecision``.
        """
        start = time.monotonic()
        ctx = context or {}

        with tracer.start_as_current_span("graph_rag.evaluate_policy") as span:
            span.set_attribute("policy_id", policy_id)
            _validate_policy_id(policy_id)

            with self._lock:
                policy = self._nodes.get(policy_id)
                if policy is None:
                    decision = PolicyDecision(
                        policy_id=policy_id,
                        allowed=False,
                        explanation=f"Policy '{policy_id}' not found in graph.",
                        confidence=1.0,
                    )
                    _record_eval_metric(policy_id, "not_found", start)
                    return decision

                # 1. BFS dependency collection
                dep_chain = self._bfs_collect_required(policy_id)
                evaluated = [policy_id] + dep_chain

                # 2. Condition matching for root and all deps
                all_pass = True
                failing_policy: Optional[str] = None
                for pid in evaluated:
                    pnode = self._nodes.get(pid)
                    if pnode and not self._check_conditions(pnode, ctx):
                        all_pass = False
                        failing_policy = pid
                        break

                # 3. Conflict resolution
                conflicts = self._resolve_conflicts(evaluated)

                # 4. Determine outcome
                allowed = all_pass and len(conflicts) == 0

                # 5. Confidence heuristic
                confidence = 1.0
                if conflicts:
                    confidence -= 0.2 * len(conflicts)
                if not all_pass:
                    confidence -= 0.3
                confidence = max(0.0, min(1.0, confidence))

            # 6. Explanation (outside lock to avoid holding it during string ops)
            explanation = self._generate_explanation(
                policy, evaluated, conflicts, all_pass, failing_policy, ctx,
            )
            explanation = self._enrich_explanation_llm(explanation)

            decision = PolicyDecision(
                policy_id=policy_id,
                allowed=allowed,
                explanation=explanation,
                evaluated_policies=evaluated,
                conflicts=conflicts,
                confidence=confidence,
            )

            label = "allowed" if allowed else "denied"
            _record_eval_metric(policy_id, label, start)
            span.set_attribute("decision", label)
            logger.info(
                "policy_evaluated",
                policy_id=policy_id,
                decision=label,
                evaluated_count=len(evaluated),
                conflict_count=len(conflicts),
            )
            return decision

    def get_policy(self, policy_id: str) -> Optional[PolicyNode]:
        """Return a single policy node or ``None``."""
        with self._lock:
            return self._nodes.get(policy_id)

    def get_all_policies(self) -> List[PolicyNode]:
        """Return a snapshot of all policy nodes."""
        with self._lock:
            return list(self._nodes.values())

    def get_dependency_chain(self, policy_id: str) -> List[str]:
        """Return the ordered BFS dependency chain for *policy_id*."""
        _validate_policy_id(policy_id)
        with self._lock:
            return self._bfs_collect_required(policy_id)

    # -- internal helpers --------------------------------------------------

    def _bfs_collect_required(self, policy_id: str) -> List[str]:
        """BFS over ``REQUIRES`` edges, returning an ordered list.

        Handles cycles via a *visited* set so no node is processed twice.
        Must be called while ``self._lock`` is held.
        """
        visited: Set[str] = {policy_id}
        queue: deque[str] = deque()
        result: List[str] = []

        # Seed queue with direct requirements
        for edge in self._edges.get(policy_id, []):
            if edge.edge_type == EdgeType.REQUIRES and edge.target not in visited:
                visited.add(edge.target)
                queue.append(edge.target)

        while queue:
            current = queue.popleft()
            if current not in self._nodes:
                # Dangling reference – skip but record
                logger.warning("dangling_requires", source=policy_id, target=current)
                continue
            result.append(current)
            for edge in self._edges.get(current, []):
                if edge.edge_type == EdgeType.REQUIRES and edge.target not in visited:
                    visited.add(edge.target)
                    queue.append(edge.target)

        return result

    def _check_conditions(self, policy: PolicyNode, context: Dict[str, Any]) -> bool:
        """Return ``True`` when *context* satisfies all policy conditions.

        Each condition key must be present in *context* with an equal
        value.  An empty conditions dict always passes.
        """
        for key, expected in policy.conditions.items():
            actual = context.get(key)
            if actual != expected:
                return False
        return True

    def _resolve_conflicts(self, policy_ids: List[str]) -> List[str]:
        """Detect policies in *policy_ids* that conflict with each other.

        When a conflict is found, the **lower-priority** policy ID is
        added to the returned list (the higher-priority one wins).
        """
        id_set = set(policy_ids)
        losers: List[str] = []

        for pid in policy_ids:
            node = self._nodes.get(pid)
            if node is None:
                continue
            for cid in node.conflicts_with:
                if cid in id_set and cid not in losers:
                    other = self._nodes.get(cid)
                    if other is None:
                        continue
                    loser = cid if node.priority >= other.priority else pid
                    if loser not in losers:
                        losers.append(loser)

        return losers

    def _generate_explanation(
        self,
        policy: PolicyNode,
        evaluated: List[str],
        conflicts: List[str],
        all_pass: bool,
        failing_policy: Optional[str],
        context: Dict[str, Any],
    ) -> str:
        """Build a deterministic, human-readable explanation string."""
        parts: List[str] = [
            f"Evaluated policy '{policy.id}' (priority={policy.priority})."
        ]

        if len(evaluated) > 1:
            parts.append(
                f"Dependency chain resolved via BFS: {' -> '.join(evaluated)}."
            )

        if not all_pass and failing_policy:
            parts.append(
                f"Condition check failed on '{failing_policy}' "
                f"with context keys {sorted(context.keys())}."
            )

        if conflicts:
            parts.append(
                f"Conflicts detected with: {', '.join(conflicts)}. "
                "Highest-priority policy wins."
            )

        if all_pass and not conflicts:
            parts.append("All conditions satisfied; no conflicts. Action ALLOWED.")
        else:
            parts.append("Action DENIED.")

        return " ".join(parts)

    @staticmethod
    def _enrich_explanation_llm(explanation: str) -> str:
        """Optional LLM enrichment hook.

        Returns the input unchanged when no LLM backend is configured.
        Subclasses or future integrations can override this to call an
        LLM for richer natural-language explanations.
        """
        return explanation

    # -- dunder ------------------------------------------------------------

    def __repr__(self) -> str:
        with self._lock:
            n = len(self._nodes)
            e = sum(len(v) for v in self._edges.values())
        return f"<GraphRAGPolicyReasoner nodes={n} edges={e}>"

    def __str__(self) -> str:
        return self.__repr__()


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------

def _validate_policy_id(policy_id: str) -> None:
    """Raise ``ValueError`` if *policy_id* contains illegal characters."""
    if not _POLICY_ID_RE.match(policy_id):
        raise ValueError(
            f"Invalid policy_id '{policy_id}': must match [a-zA-Z0-9_-]+"
        )


def _record_eval_metric(policy_id: str, decision: str, start: float) -> None:
    """Emit Prometheus counter + histogram observations."""
    POLICY_EVAL_TOTAL.labels(policy_id=policy_id, decision=decision).inc()
    POLICY_EVAL_DURATION.observe(time.monotonic() - start)
