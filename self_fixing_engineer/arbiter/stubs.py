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

import logging
import os
import threading
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
    
    if _production_mode:
        logger.critical(
            f"PRODUCTION ALERT: {component} stub active in PRODUCTION mode! "
            f"Method: {method}. This may result in degraded functionality."
        )
    elif not _test_mode:
        logger.warning(
            f"{component} using stub implementation. "
            f"Real implementation unavailable. Method: {method}"
        )
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
    
    async def stop_async_services(self):
        """No-op async services shutdown."""
        _log_stub_usage("Arbiter", "stop_async_services")
    
    async def respond(self, *args, **kwargs) -> str:
        """Stub response indicating unavailability."""
        _log_stub_usage("Arbiter", "respond")
        return "Arbiter unavailable (stub mode)"
    
    async def plan_decision(self, *args, **kwargs) -> Dict[str, Any]:
        """Stub decision planning."""
        _log_stub_usage("Arbiter", "plan_decision")
        return {"action": "idle", "reason": "stub_mode"}
    
    async def evolve(self, *args, **kwargs):
        """No-op evolution cycle."""
        _log_stub_usage("Arbiter", "evolve")


# =============================================================================
# POLICY ENGINE STUB
# =============================================================================

class PolicyEngineStub:
    """
    Stub implementation of PolicyEngine.
    
    Always allows operations by default. Logs critical warnings in production.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize PolicyEngine stub."""
        _log_stub_usage("PolicyEngine")
    
    async def should_auto_learn(
        self, 
        component: str, 
        action: str, 
        *args, 
        **kwargs
    ) -> Tuple[bool, str]:
        """
        Stub policy check that always allows operations.
        
        Returns:
            Tuple of (True, reason) indicating operation is allowed
        """
        _log_stub_usage("PolicyEngine", "should_auto_learn")
        if _production_mode:
            logger.critical(
                f"PolicyEngine stub: Auto-allowing {component}.{action} in PRODUCTION!"
            )
        return True, "Stub policy: Allowed (development mode)"
    
    async def evaluate_policy(
        self,
        action: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Stub policy evaluation.
        
        Returns:
            Tuple of (True, reason) indicating operation is allowed
        """
        _log_stub_usage("PolicyEngine", "evaluate_policy")
        return True, "Stub policy: Allowed"
    
    async def check_circuit_breaker(self, *args, **kwargs) -> Tuple[bool, str]:
        """Stub circuit breaker that never opens."""
        _log_stub_usage("PolicyEngine", "check_circuit_breaker")
        return False, "Circuit breaker: Closed (stub mode)"


# =============================================================================
# BUG MANAGER STUB
# =============================================================================

class BugManagerStub:
    """
    Stub implementation of BugManager.
    
    Logs bug reports but takes no action.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize BugManager stub."""
        _log_stub_usage("BugManager")
    
    async def report_bug(self, bug_data: Dict[str, Any]) -> Optional[str]:
        """
        Log bug report without taking action.
        
        Args:
            bug_data: Bug information dictionary
        
        Returns:
            None (no bug tracking ID in stub mode)
        """
        _log_stub_usage("BugManager", "report_bug")
        logger.info(f"BugManager stub: Would report bug - {bug_data.get('title', 'Untitled')}")
        return None
    
    async def get_bug(self, bug_id: str) -> Optional[Dict[str, Any]]:
        """Stub bug retrieval."""
        _log_stub_usage("BugManager", "get_bug")
        return None
    
    async def update_bug(self, bug_id: str, updates: Dict[str, Any]) -> bool:
        """Stub bug update."""
        _log_stub_usage("BugManager", "update_bug")
        return False


# =============================================================================
# KNOWLEDGE GRAPH STUB
# =============================================================================

class KnowledgeGraphStub:
    """
    Stub implementation of KnowledgeGraph.
    
    Maintains an in-memory graph for basic functionality.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize KnowledgeGraph stub with in-memory storage."""
        _log_stub_usage("KnowledgeGraph")
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Tuple[str, str, str]] = []
    
    async def add_fact(
        self, 
        domain: str, 
        key: str, 
        data: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Add a fact to the in-memory graph.
        
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
        logger.debug(f"KnowledgeGraph stub: Added fact {fact_id}")
        return {"status": "success", "fact_id": fact_id, "stub_mode": True}
    
    async def find_related_facts(
        self,
        domain: str,
        key: str,
        value: Any
    ) -> List[Dict[str, Any]]:
        """Find related facts in the graph."""
        _log_stub_usage("KnowledgeGraph", "find_related_facts")
        return []
    
    async def add_node(self, node_id: str, properties: Dict[str, Any]) -> None:
        """Add a node to the graph."""
        _log_stub_usage("KnowledgeGraph", "add_node")
        self._nodes[node_id] = properties
    
    async def add_relationship(
        self,
        from_node: str,
        to_node: str,
        relationship_type: str
    ) -> None:
        """Add a relationship between nodes."""
        _log_stub_usage("KnowledgeGraph", "add_relationship")
        self._edges.append((from_node, to_node, relationship_type))
    
    async def query(self, query: str) -> List[Dict[str, Any]]:
        """Stub query method."""
        _log_stub_usage("KnowledgeGraph", "query")
        return []
    
    async def connect(self):
        """No-op connection method."""
        _log_stub_usage("KnowledgeGraph", "connect")
    
    async def close(self):
        """No-op close method."""
        _log_stub_usage("KnowledgeGraph", "close")


# =============================================================================
# HUMAN IN LOOP STUB
# =============================================================================

class HumanInLoopStub:
    """
    Stub implementation of HumanInLoop.
    
    Auto-approves all requests in stub mode.
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
        Auto-approve all requests in stub mode.
        
        Args:
            action: Action requiring approval
            context: Context for the approval request
            timeout: Request timeout in seconds
        
        Returns:
            True (auto-approved in stub mode)
        """
        _log_stub_usage("HumanInLoop", "request_approval")
        logger.info(f"HumanInLoop stub: Auto-approving {action}")
        return True
    
    async def notify(self, message: str, severity: str = "info") -> bool:
        """Log notification without sending."""
        _log_stub_usage("HumanInLoop", "notify")
        logger.info(f"HumanInLoop stub notification [{severity}]: {message}")
        return True


# =============================================================================
# MESSAGE QUEUE SERVICE STUB
# =============================================================================

class MessageQueueServiceStub:
    """
    Stub implementation of MessageQueueService.
    
    Logs events but doesn't deliver them.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize MessageQueueService stub."""
        _log_stub_usage("MessageQueueService")
    
    async def publish(
        self,
        topic: str,
        message: Dict[str, Any],
        **kwargs
    ) -> bool:
        """
        Log event publication without actual delivery.
        
        Args:
            topic: Event topic/channel
            message: Event data
        
        Returns:
            True (always succeeds in stub mode)
        """
        _log_stub_usage("MessageQueueService", "publish")
        logger.debug(f"MessageQueue stub: Would publish to {topic}: {message}")
        return True
    
    async def subscribe(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], None]
    ) -> None:
        """No-op subscription."""
        _log_stub_usage("MessageQueueService", "subscribe")
        logger.debug(f"MessageQueue stub: Would subscribe to {topic}")
    
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
    
    Logs feedback but doesn't persist it.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize FeedbackManager stub."""
        _log_stub_usage("FeedbackManager")
    
    async def record_feedback(
        self,
        component: str,
        feedback_type: str,
        data: Dict[str, Any]
    ) -> bool:
        """
        Log feedback without persistence.
        
        Args:
            component: Component providing feedback
            feedback_type: Type of feedback
            data: Feedback data
        
        Returns:
            True (always succeeds in stub mode)
        """
        _log_stub_usage("FeedbackManager", "record_feedback")
        logger.debug(f"Feedback stub: {component} - {feedback_type}")
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
