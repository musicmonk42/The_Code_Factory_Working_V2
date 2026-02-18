# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical stub implementations for Arbiter components.

This module provides production-quality stub implementations for all Arbiter components
to enable graceful degradation when real implementations are unavailable.

Features:
- Logs warnings on first use indicating stub mode
- Tracks usage via Prometheus counters
- Production mode detection with critical logging
- Consistent, safe default values
- Thread-safe initialization

Usage:
    from self_fixing_engineer.arbiter.stubs import (
        ArbiterStub,
        PolicyEngineStub,
        BugManagerStub,
        KnowledgeGraphStub,
        HumanInLoopStub,
        MessageQueueServiceStub,
        FeedbackManagerStub,
        ArbiterArenaStub,
        KnowledgeLoaderStub,
    )

Environment Variables:
    PRODUCTION_MODE: Set to "true" to enable production checks
    TEST_MODE: Set to "true" to suppress warnings in tests
"""

import asyncio
import json
import logging
import os
import threading
import warnings
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Prometheus metrics for tracking stub usage
try:
    from prometheus_client import Counter
    
    ARBITER_STUB_USAGE = Counter(
        'arbiter_stub_usage_total',
        'Count of stub method invocations',
        ['component', 'method']
    )
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    
    class _NoOpCounter:
        def labels(self, **kwargs):
            return self
        def inc(self):
            pass
    
    ARBITER_STUB_USAGE = _NoOpCounter()

logger = logging.getLogger(__name__)

# Global state for production mode checks
_production_mode = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
_test_mode = os.getenv("TEST_MODE", "false").lower() == "true"
_stub_warnings_shown = set()
_stub_lock = threading.Lock()

# Storage paths for persistent stubs
_STORAGE_DIR = Path(os.getenv("STUB_STORAGE_DIR", "/tmp/arbiter_stubs"))
_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _log_stub_usage(component: str, method: str = "__init__"):
    """
    Log stub usage with appropriate severity based on environment.
    
    Args:
        component: Name of the stub component
        method: Name of the method being called
    """
    # Track metrics
    ARBITER_STUB_USAGE.labels(component=component, method=method).inc()
    
    # Log warnings (only once per component)
    key = f"{component}.{method}"
    with _stub_lock:
        if key in _stub_warnings_shown:
            return
        _stub_warnings_shown.add(key)
    
    # Always emit warnings for observability
    warning_msg = (
        f"{component}.{method}() is using stub implementation. "
        f"Real implementation unavailable."
    )
    
    if _production_mode:
        logger.critical(
            f"PRODUCTION ALERT: {component} stub active in PRODUCTION mode! "
            f"Method: {method}. This may result in degraded functionality."
        )
        if not _test_mode:
            warnings.warn(
                f"PRODUCTION: {warning_msg}",
                RuntimeWarning,
                stacklevel=3
            )
    elif not _test_mode:
        logger.warning(warning_msg)
        warnings.warn(warning_msg, UserWarning, stacklevel=3)
    else:
        logger.debug(f"{component} stub initialized (test mode)")


# =============================================================================
# ARBITER CORE STUB
# =============================================================================

class ArbiterStub:
    """
    Stub implementation of the main Arbiter class.
    
    Provides no-op methods for all core Arbiter operations.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize Arbiter stub."""
        _log_stub_usage("Arbiter")
    
    async def start_async_services(self):
        """No-op async services startup."""
        _log_stub_usage("Arbiter", "start_async_services")
        warnings.warn(
            "Arbiter stub: Async services not started (stub mode)",
            UserWarning,
            stacklevel=2
        )
    
    async def stop_async_services(self):
        """No-op async services shutdown."""
        _log_stub_usage("Arbiter", "stop_async_services")
    
    async def respond(self, *args, **kwargs) -> str:
        """Stub response indicating unavailability."""
        _log_stub_usage("Arbiter", "respond")
        warnings.warn(
            "Arbiter stub: respond() called - returning stub response",
            UserWarning,
            stacklevel=2
        )
        return "Arbiter unavailable (stub mode)"
    
    async def plan_decision(self, *args, **kwargs) -> Dict[str, Any]:
        """Stub decision planning."""
        _log_stub_usage("Arbiter", "plan_decision")
        warnings.warn(
            "Arbiter stub: plan_decision() called - returning idle action",
            UserWarning,
            stacklevel=2
        )
        return {"action": "idle", "reason": "stub_mode"}
    
    async def evolve(self, *args, **kwargs):
        """No-op evolution cycle."""
        _log_stub_usage("Arbiter", "evolve")
        warnings.warn(
            "Arbiter stub: evolve() called - no evolution in stub mode",
            UserWarning,
            stacklevel=2
        )


# =============================================================================
# POLICY ENGINE STUB
# =============================================================================

class PolicyEngineStub:
    """
    Stub implementation of PolicyEngine.
    
    Defaults to DENY for safety. Tracks circuit breaker state.
    Logs critical warnings in production.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize PolicyEngine stub."""
        _log_stub_usage("PolicyEngine")
        self._circuit_breaker_calls: Dict[str, int] = {}
        self._circuit_breaker_threshold = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "100"))
        self._circuit_breaker_open: Dict[str, bool] = {}
    
    async def should_auto_learn(
        self, 
        component: str, 
        action: str, 
        *args, 
        **kwargs
    ) -> Tuple[bool, str]:
        """
        Stub policy check that DENIES by default for security.
        
        Only allows if explicitly configured via STUB_ALLOW_AUTO_LEARN=true.
        
        Returns:
            Tuple of (allowed, reason)
        """
        _log_stub_usage("PolicyEngine", "should_auto_learn")
        
        # Security-first: Default to DENY
        allow_override = os.getenv("STUB_ALLOW_AUTO_LEARN", "false").lower() == "true"
        
        if _production_mode:
            logger.critical(
                f"PolicyEngine stub: Policy check for {component}.{action} in PRODUCTION! "
                f"Result: {'ALLOWED (override)' if allow_override else 'DENIED (default secure)'}"
            )
            warnings.warn(
                f"PolicyEngine stub used in PRODUCTION for {component}.{action}",
                RuntimeWarning,
                stacklevel=2
            )
        
        if allow_override:
            return True, "Stub policy: Allowed (STUB_ALLOW_AUTO_LEARN=true)"
        else:
            return False, "Stub policy: DENIED by default (security-first)"
    
    async def evaluate_policy(
        self,
        action: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Stub policy evaluation.
        
        Defaults to DENY for security.
        
        Returns:
            Tuple of (allowed, reason)
        """
        _log_stub_usage("PolicyEngine", "evaluate_policy")
        
        # Security-first: Default to DENY
        allow_override = os.getenv("STUB_ALLOW_POLICY", "false").lower() == "true"
        
        if allow_override:
            return True, "Stub policy: Allowed (STUB_ALLOW_POLICY=true)"
        else:
            return False, "Stub policy: DENIED by default (security-first)"
    
    async def check_circuit_breaker(self, service: str = "default", *args, **kwargs) -> Tuple[bool, str]:
        """
        Stub circuit breaker that tracks calls and trips after threshold.
        
        Args:
            service: Service name to track
            
        Returns:
            Tuple of (is_open, reason)
        """
        _log_stub_usage("PolicyEngine", "check_circuit_breaker")
        
        # Track calls per service
        self._circuit_breaker_calls[service] = self._circuit_breaker_calls.get(service, 0) + 1
        
        # Check if circuit should open
        if self._circuit_breaker_calls[service] >= self._circuit_breaker_threshold:
            self._circuit_breaker_open[service] = True
            logger.warning(
                f"Circuit breaker OPENED for {service} after {self._circuit_breaker_calls[service]} calls"
            )
            return True, f"Circuit breaker: OPEN (threshold {self._circuit_breaker_threshold} exceeded)"
        
        # Circuit is closed
        return False, f"Circuit breaker: Closed ({self._circuit_breaker_calls[service]}/{self._circuit_breaker_threshold} calls)"


# =============================================================================
# BUG MANAGER STUB
# =============================================================================

class BugManagerStub:
    """
    Stub implementation of BugManager.
    
    Persists bug reports to local JSON file as fallback.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize BugManager stub."""
        _log_stub_usage("BugManager")
        self._bug_file = _STORAGE_DIR / "bugs.json"
        self._bugs: Dict[str, Dict[str, Any]] = {}
        self._load_bugs()
    
    def _load_bugs(self):
        """Load bugs from JSON file."""
        if self._bug_file.exists():
            try:
                with open(self._bug_file, "r") as f:
                    self._bugs = json.load(f)
                logger.debug(f"Loaded {len(self._bugs)} bugs from {self._bug_file}")
            except Exception as e:
                logger.warning(f"Failed to load bugs from {self._bug_file}: {e}")
                self._bugs = {}
    
    def _save_bugs(self):
        """Save bugs to JSON file."""
        try:
            with open(self._bug_file, "w") as f:
                json.dump(self._bugs, f, indent=2, default=str)
            logger.debug(f"Saved {len(self._bugs)} bugs to {self._bug_file}")
        except Exception as e:
            logger.error(f"Failed to save bugs to {self._bug_file}: {e}")
    
    async def report_bug(self, bug_data: Dict[str, Any]) -> Optional[str]:
        """
        Persist bug report to local JSON file.
        
        Args:
            bug_data: Bug information dictionary
        
        Returns:
            Bug ID (string)
        """
        _log_stub_usage("BugManager", "report_bug")
        
        import time
        bug_id = f"bug_{int(time.time() * 1000)}"
        
        self._bugs[bug_id] = {
            "id": bug_id,
            "title": bug_data.get("title", "Untitled"),
            "data": bug_data,
            "timestamp": time.time(),
            "status": "open"
        }
        
        self._save_bugs()
        
        logger.info(
            f"BugManager stub: Persisted bug {bug_id} - {bug_data.get('title', 'Untitled')} "
            f"to {self._bug_file}"
        )
        warnings.warn(
            f"BugManager stub: Bug {bug_id} saved to local file only",
            UserWarning,
            stacklevel=2
        )
        
        return bug_id
    
    async def get_bug(self, bug_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve bug from local storage."""
        _log_stub_usage("BugManager", "get_bug")
        return self._bugs.get(bug_id)
    
    async def update_bug(self, bug_id: str, updates: Dict[str, Any]) -> bool:
        """Update bug in local storage."""
        _log_stub_usage("BugManager", "update_bug")
        if bug_id in self._bugs:
            self._bugs[bug_id].update(updates)
            self._save_bugs()
            return True
        return False


# =============================================================================
# KNOWLEDGE GRAPH STUB
# =============================================================================

class KnowledgeGraphStub:
    """
    Stub implementation of KnowledgeGraph.
    
    Maintains persistent storage via JSON file for basic functionality.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize KnowledgeGraph stub with persistent storage."""
        _log_stub_usage("KnowledgeGraph")
        self._graph_file = _STORAGE_DIR / "knowledge_graph.json"
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Tuple[str, str, str]] = []
        self._load_graph()
    
    def _load_graph(self):
        """Load graph from JSON file."""
        if self._graph_file.exists():
            try:
                with open(self._graph_file, "r") as f:
                    data = json.load(f)
                    self._nodes = data.get("nodes", {})
                    # Convert edge lists back to tuples
                    self._edges = [tuple(e) for e in data.get("edges", [])]
                logger.debug(
                    f"Loaded knowledge graph: {len(self._nodes)} nodes, "
                    f"{len(self._edges)} edges from {self._graph_file}"
                )
            except Exception as e:
                logger.warning(f"Failed to load knowledge graph from {self._graph_file}: {e}")
                self._nodes = {}
                self._edges = []
    
    def _save_graph(self):
        """Save graph to JSON file."""
        try:
            data = {
                "nodes": self._nodes,
                "edges": self._edges  # Will be serialized as lists
            }
            with open(self._graph_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
            logger.debug(
                f"Saved knowledge graph: {len(self._nodes)} nodes, "
                f"{len(self._edges)} edges to {self._graph_file}"
            )
        except Exception as e:
            logger.error(f"Failed to save knowledge graph to {self._graph_file}: {e}")
    
    async def add_fact(
        self, 
        domain: str, 
        key: str, 
        data: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Add a fact to the persistent graph.
        
        Args:
            domain: Fact domain/category
            key: Unique fact identifier
            data: Fact data
        
        Returns:
            Status dictionary with operation result
        """
        _log_stub_usage("KnowledgeGraph", "add_fact")
        fact_id = f"{domain}:{key}"
        self._nodes[fact_id] = {"domain": domain, "key": key, **data}
        self._save_graph()
        
        logger.debug(f"KnowledgeGraph stub: Added fact {fact_id}")
        warnings.warn(
            f"KnowledgeGraph stub: Fact {fact_id} saved to local file only",
            UserWarning,
            stacklevel=2
        )
        
        return {"status": "success", "fact_id": fact_id, "stub_mode": True}
    
    async def find_related_facts(
        self,
        domain: str,
        key: str,
        value: Any
    ) -> List[Dict[str, Any]]:
        """Find related facts in the graph."""
        _log_stub_usage("KnowledgeGraph", "find_related_facts")
        # Simple search in persisted nodes
        results = []
        for node_id, node_data in self._nodes.items():
            if node_data.get("domain") == domain:
                results.append({"id": node_id, **node_data})
        return results
    
    async def add_node(self, node_id: str, properties: Dict[str, Any]) -> None:
        """Add a node to the graph."""
        _log_stub_usage("KnowledgeGraph", "add_node")
        self._nodes[node_id] = properties
        self._save_graph()
    
    async def add_relationship(
        self,
        from_node: str,
        to_node: str,
        relationship_type: str
    ) -> None:
        """Add a relationship between nodes."""
        _log_stub_usage("KnowledgeGraph", "add_relationship")
        self._edges.append((from_node, to_node, relationship_type))
        self._save_graph()
    
    async def query(self, query: str) -> List[Dict[str, Any]]:
        """Stub query method - returns all nodes."""
        _log_stub_usage("KnowledgeGraph", "query")
        warnings.warn(
            "KnowledgeGraph stub: query() returns all nodes (no filtering)",
            UserWarning,
            stacklevel=2
        )
        return [{"id": k, **v} for k, v in self._nodes.items()]
    
    async def connect(self):
        """No-op connection method."""
        _log_stub_usage("KnowledgeGraph", "connect")
    
    async def close(self):
        """Save and close."""
        _log_stub_usage("KnowledgeGraph", "close")
        self._save_graph()


# =============================================================================
# HUMAN IN LOOP STUB
# =============================================================================

class HumanInLoopStub:
    """
    Stub implementation of HumanInLoop.
    
    DENIES all requests by default for security.
    Auto-approves only if STUB_AUTO_APPROVE=true is set.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize HumanInLoop stub."""
        _log_stub_usage("HumanInLoop")
    
    async def request_approval(
        self,
        action: str,
        context: Dict[str, Any],
        timeout: Optional[int] = None
    ) -> bool:
        """
        DENY all requests by default for security.
        
        Only auto-approves if STUB_AUTO_APPROVE=true is explicitly set.
        
        Args:
            action: Action requiring approval
            context: Context for the approval request
            timeout: Request timeout in seconds
        
        Returns:
            bool: Approval status (False by default)
        """
        _log_stub_usage("HumanInLoop", "request_approval")
        
        # Security-first: Default to DENY
        auto_approve = os.getenv("STUB_AUTO_APPROVE", "false").lower() == "true"
        
        if _production_mode:
            logger.critical(
                f"HumanInLoop stub: Approval request for '{action}' in PRODUCTION! "
                f"Result: {'AUTO-APPROVED (override)' if auto_approve else 'DENIED (default secure)'}"
            )
            warnings.warn(
                f"HumanInLoop stub used in PRODUCTION for approval of '{action}'",
                RuntimeWarning,
                stacklevel=2
            )
        
        if auto_approve:
            logger.warning(
                f"HumanInLoop stub: AUTO-APPROVING '{action}' "
                f"(STUB_AUTO_APPROVE=true)"
            )
            return True
        else:
            logger.info(
                f"HumanInLoop stub: DENYING '{action}' "
                f"(no human approval - default secure)"
            )
            warnings.warn(
                f"HumanInLoop stub: Request '{action}' DENIED (no human oversight)",
                UserWarning,
                stacklevel=2
            )
            return False
    
    async def notify(self, message: str, severity: str = "info") -> bool:
        """Log notification without sending."""
        _log_stub_usage("HumanInLoop", "notify")
        logger.info(f"HumanInLoop stub notification [{severity}]: {message}")
        warnings.warn(
            f"HumanInLoop stub: Notification not sent (stub mode)",
            UserWarning,
            stacklevel=2
        )
        return True


# =============================================================================
# MESSAGE QUEUE SERVICE STUB
# =============================================================================

class MessageQueueServiceStub:
    """
    Stub implementation of MessageQueueService.
    
    Provides in-memory queue for local event delivery.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize MessageQueueService stub."""
        _log_stub_usage("MessageQueueService")
        # In-memory queues per topic
        self._queues: Dict[str, deque] = {}
        # Subscribers per topic
        self._subscribers: Dict[str, List[Callable]] = {}
        self._max_queue_size = int(os.getenv("STUB_QUEUE_SIZE", "1000"))
    
    async def publish(
        self,
        topic: str,
        message: Dict[str, Any],
        **kwargs
    ) -> bool:
        """
        Publish event to in-memory queue and deliver to subscribers.
        
        Args:
            topic: Event topic/channel
            message: Event data
        
        Returns:
            True if published successfully
        """
        _log_stub_usage("MessageQueueService", "publish")
        
        # Initialize queue if needed
        if topic not in self._queues:
            self._queues[topic] = deque(maxlen=self._max_queue_size)
        
        # Add to queue
        self._queues[topic].append(message)
        
        logger.debug(
            f"MessageQueue stub: Published to {topic}: {message}. "
            f"Queue size: {len(self._queues[topic])}"
        )
        
        # Deliver to subscribers
        if topic in self._subscribers:
            for handler in self._subscribers[topic]:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(message)
                    else:
                        handler(message)
                except Exception as e:
                    logger.error(f"Error in subscriber handler for {topic}: {e}")
        
        return True
    
    async def subscribe(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], None]
    ) -> None:
        """
        Subscribe to topic with handler.
        
        Args:
            topic: Topic to subscribe to
            handler: Callback function for messages
        """
        _log_stub_usage("MessageQueueService", "subscribe")
        
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        
        self._subscribers[topic].append(handler)
        
        logger.debug(
            f"MessageQueue stub: Subscribed to {topic}. "
            f"Total subscribers: {len(self._subscribers[topic])}"
        )
        
        warnings.warn(
            f"MessageQueue stub: Local in-memory subscription to {topic}",
            UserWarning,
            stacklevel=2
        )
    
    async def start(self):
        """No-op start method."""
        _log_stub_usage("MessageQueueService", "start")
    
    async def stop(self):
        """No-op stop method."""
        _log_stub_usage("MessageQueueService", "stop")


# =============================================================================
# FEEDBACK MANAGER STUB
# =============================================================================

class FeedbackManagerStub:
    """
    Stub implementation of FeedbackManager.
    
    Persists feedback to local JSON file.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize FeedbackManager stub."""
        _log_stub_usage("FeedbackManager")
        self._feedback_file = _STORAGE_DIR / "feedback.json"
        self._feedback: List[Dict[str, Any]] = []
        self._load_feedback()
    
    def _load_feedback(self):
        """Load feedback from JSON file."""
        if self._feedback_file.exists():
            try:
                with open(self._feedback_file, "r") as f:
                    self._feedback = json.load(f)
                logger.debug(f"Loaded {len(self._feedback)} feedback entries from {self._feedback_file}")
            except Exception as e:
                logger.warning(f"Failed to load feedback from {self._feedback_file}: {e}")
                self._feedback = []
    
    def _save_feedback(self):
        """Save feedback to JSON file."""
        try:
            with open(self._feedback_file, "w") as f:
                json.dump(self._feedback, f, indent=2, default=str)
            logger.debug(f"Saved {len(self._feedback)} feedback entries to {self._feedback_file}")
        except Exception as e:
            logger.error(f"Failed to save feedback to {self._feedback_file}: {e}")
    
    async def record_feedback(
        self,
        component: str,
        feedback_type: str,
        data: Dict[str, Any]
    ) -> bool:
        """
        Persist feedback to local JSON file.
        
        Args:
            component: Component providing feedback
            feedback_type: Type of feedback
            data: Feedback data
        
        Returns:
            True if saved successfully
        """
        _log_stub_usage("FeedbackManager", "record_feedback")
        
        import time
        feedback_entry = {
            "component": component,
            "feedback_type": feedback_type,
            "data": data,
            "timestamp": time.time()
        }
        
        self._feedback.append(feedback_entry)
        self._save_feedback()
        
        logger.debug(f"Feedback stub: Persisted {component} - {feedback_type}")
        warnings.warn(
            f"FeedbackManager stub: Feedback saved to local file only",
            UserWarning,
            stacklevel=2
        )
        
        return True


# =============================================================================
# ARBITER ARENA STUB
# =============================================================================

class ArbiterArenaStub:
    """
    Stub implementation of ArbiterArena.
    
    Provides no-op multi-arbiter coordination.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize ArbiterArena stub."""
        _log_stub_usage("ArbiterArena")
    
    async def coordinate(self, arbiters: List[Any]) -> Dict[str, Any]:
        """Stub coordination that returns empty result."""
        _log_stub_usage("ArbiterArena", "coordinate")
        warnings.warn(
            f"ArbiterArena stub: coordinate() called with {len(arbiters) if arbiters else 0} arbiters - no coordination in stub mode",
            UserWarning,
            stacklevel=2
        )
        return {"status": "stub_mode", "result": None}


# =============================================================================
# KNOWLEDGE LOADER STUB
# =============================================================================

class KnowledgeLoaderStub:
    """
    Stub implementation of KnowledgeLoader.
    
    Returns empty knowledge sets.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize KnowledgeLoader stub."""
        _log_stub_usage("KnowledgeLoader")
    
    async def load_knowledge(self, domain: str) -> Dict[str, Any]:
        """Return empty knowledge set."""
        _log_stub_usage("KnowledgeLoader", "load_knowledge")
        warnings.warn(
            f"KnowledgeLoader stub: load_knowledge({domain}) returning empty knowledge set",
            UserWarning,
            stacklevel=2
        )
        return {"domain": domain, "facts": [], "stub_mode": True}


# =============================================================================
# HEALTH CHECK HELPER
# =============================================================================

def is_using_stubs() -> Dict[str, bool]:
    """
    Check which Arbiter components are using stub implementations.
    
    Returns:
        Dictionary mapping component names to stub status (True if using stub)
    """
    # Try to import real implementations
    stub_status = {}
    
    components = [
        ("Arbiter", "self_fixing_engineer.arbiter.arbiter", "Arbiter"),
        ("PolicyEngine", "self_fixing_engineer.arbiter.policy.core", "PolicyEngine"),
        ("BugManager", "self_fixing_engineer.arbiter.bug_manager.bug_manager", "BugManager"),
        ("KnowledgeGraph", "self_fixing_engineer.arbiter.knowledge_graph.core", "KnowledgeGraph"),
        ("HumanInLoop", "self_fixing_engineer.arbiter.human_loop", "HumanInLoop"),
        ("MessageQueueService", "self_fixing_engineer.arbiter.message_queue_service", "MessageQueueService"),
        ("FeedbackManager", "self_fixing_engineer.arbiter.feedback", "FeedbackManager"),
    ]
    
    for component_name, module_path, class_name in components:
        try:
            __import__(module_path)
            stub_status[component_name] = False
        except (ImportError, AttributeError):
            stub_status[component_name] = True
    
    return stub_status


# =============================================================================
# CONVENIENCE EXPORTS
# =============================================================================

__all__ = [
    "ArbiterStub",
    "PolicyEngineStub",
    "BugManagerStub",
    "KnowledgeGraphStub",
    "HumanInLoopStub",
    "MessageQueueServiceStub",
    "FeedbackManagerStub",
    "ArbiterArenaStub",
    "KnowledgeLoaderStub",
    "is_using_stubs",
]
