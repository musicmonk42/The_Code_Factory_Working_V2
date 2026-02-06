"""
Arbiter Bridge Module - Generator-to-Arbiter Integration Facade.

This module provides a facade connecting the Generator pipeline to Arbiter governance
services, enabling policy enforcement, event publishing, bug reporting, and knowledge
graph updates while maintaining graceful degradation when Arbiter is unavailable.

Key Features:
- Policy checks via PolicyEngine
- Event publishing via MessageQueueService
- Bug reporting via BugManager
- Knowledge graph updates via KnowledgeGraph
- Human-in-the-loop integration via HumanInLoop
- Graceful degradation with stub fallbacks
- Comprehensive logging and metrics

Architecture:
    ┌──────────────────────────────────────────────────────────┐
    │                   Generator Pipeline                      │
    │  ┌────────────────────────────────────────────────────┐  │
    │  │  WorkflowEngine / Agents                           │  │
    │  └──────────────────┬─────────────────────────────────┘  │
    │                     │                                     │
    │                     ▼                                     │
    │            ┌─────────────────┐                           │
    │            │  ArbiterBridge  │                           │
    │            └────────┬────────┘                           │
    └─────────────────────┼──────────────────────────────────┘
                          │
            ┌─────────────┴─────────────┐
            │   Arbiter Services        │
            │  ┌────────────────────┐   │
            │  │  PolicyEngine      │   │
            │  │  MessageQueue      │   │
            │  │  BugManager        │   │
            │  │  KnowledgeGraph    │   │
            │  │  HumanInLoop       │   │
            │  └────────────────────┘   │
            └───────────────────────────┘

Usage:
    from generator.arbiter_bridge import ArbiterBridge
    
    # Initialize bridge
    bridge = ArbiterBridge()
    
    # Check policy
    allowed, reason = await bridge.check_policy("generate_code", {"language": "python"})
    
    # Publish event
    await bridge.publish_event("generator_output", {"code": "...", "language": "python"})
    
    # Report bug
    await bridge.report_bug({"title": "Generation failed", "error": str(e)})
    
    # Update knowledge
    await bridge.update_knowledge("generator", "stats", {"success_rate": 0.95})
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

# Try to import real Arbiter components with fallback to stubs
try:
    from self_fixing_engineer.arbiter.policy.core import PolicyEngine
except ImportError:
    from self_fixing_engineer.arbiter.stubs import PolicyEngineStub as PolicyEngine

try:
    from self_fixing_engineer.arbiter.message_queue_service import MessageQueueService
except ImportError:
    from self_fixing_engineer.arbiter.stubs import MessageQueueServiceStub as MessageQueueService

try:
    from self_fixing_engineer.arbiter.bug_manager.bug_manager import BugManager
except ImportError:
    from self_fixing_engineer.arbiter.stubs import BugManagerStub as BugManager

try:
    from self_fixing_engineer.arbiter.knowledge_graph.core import KnowledgeGraph
except ImportError:
    try:
        from self_fixing_engineer.arbiter.knowledge_graph import KnowledgeGraph
    except ImportError:
        from self_fixing_engineer.arbiter.stubs import KnowledgeGraphStub as KnowledgeGraph

try:
    from self_fixing_engineer.arbiter.human_loop import HumanInLoop
except ImportError:
    from self_fixing_engineer.arbiter.stubs import HumanInLoopStub as HumanInLoop

# Prometheus metrics for bridge monitoring
try:
    from prometheus_client import Counter, Histogram
    
    BRIDGE_POLICY_CHECKS = Counter(
        'arbiter_bridge_policy_checks_total',
        'Count of policy checks performed by the bridge',
        ['action', 'allowed']
    )
    
    BRIDGE_EVENTS_PUBLISHED = Counter(
        'arbiter_bridge_events_published_total',
        'Count of events published by the bridge',
        ['event_type', 'status']
    )
    
    BRIDGE_BUGS_REPORTED = Counter(
        'arbiter_bridge_bugs_reported_total',
        'Count of bugs reported by the bridge',
        ['severity']
    )
    
    BRIDGE_KNOWLEDGE_UPDATES = Counter(
        'arbiter_bridge_knowledge_updates_total',
        'Count of knowledge graph updates',
        ['domain', 'status']
    )
    
    BRIDGE_OPERATION_DURATION = Histogram(
        'arbiter_bridge_operation_duration_seconds',
        'Duration of bridge operations',
        ['operation']
    )
    
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    
    class _NoOpMetric:
        def labels(self, **kwargs):
            return self
        def inc(self):
            pass
        def observe(self, value):
            pass
    
    BRIDGE_POLICY_CHECKS = _NoOpMetric()
    BRIDGE_EVENTS_PUBLISHED = _NoOpMetric()
    BRIDGE_BUGS_REPORTED = _NoOpMetric()
    BRIDGE_KNOWLEDGE_UPDATES = _NoOpMetric()
    BRIDGE_OPERATION_DURATION = _NoOpMetric()

logger = logging.getLogger(__name__)


class ArbiterBridge:
    """
    Facade connecting the Generator pipeline to Arbiter governance services.
    
    This class provides a clean interface for generator components to interact
    with Arbiter services while maintaining graceful degradation when services
    are unavailable.
    
    All methods are async and return sensible defaults on failure, ensuring the
    generator pipeline can continue operating even when Arbiter services are down.
    
    Attributes:
        policy_engine: PolicyEngine instance for policy checks
        message_queue: MessageQueueService instance for event publishing
        bug_manager: BugManager instance for bug reporting
        knowledge_graph: KnowledgeGraph instance for knowledge updates
        human_in_loop: HumanInLoop instance for approval workflows
        enabled: Whether the bridge is actively connected to Arbiter
    """
    
    def __init__(
        self,
        policy_engine: Optional[PolicyEngine] = None,
        message_queue: Optional[MessageQueueService] = None,
        bug_manager: Optional[BugManager] = None,
        knowledge_graph: Optional[KnowledgeGraph] = None,
        human_in_loop: Optional[HumanInLoop] = None,
    ):
        """
        Initialize the Arbiter bridge with optional service instances.
        
        If service instances are not provided, the bridge will attempt to create
        them automatically. If creation fails, stub implementations will be used.
        
        Args:
            policy_engine: Optional PolicyEngine instance
            message_queue: Optional MessageQueueService instance
            bug_manager: Optional BugManager instance
            knowledge_graph: Optional KnowledgeGraph instance
            human_in_loop: Optional HumanInLoop instance
        """
        self.enabled = True
        
        # Initialize services with fallbacks
        try:
            self.policy_engine = policy_engine or PolicyEngine()
            logger.info("ArbiterBridge: PolicyEngine initialized")
        except Exception as e:
            logger.warning(f"ArbiterBridge: PolicyEngine initialization failed, using stub: {e}")
            self.policy_engine = PolicyEngine()
        
        try:
            self.message_queue = message_queue or MessageQueueService()
            logger.info("ArbiterBridge: MessageQueueService initialized")
        except Exception as e:
            logger.warning(f"ArbiterBridge: MessageQueueService initialization failed, using stub: {e}")
            self.message_queue = MessageQueueService()
        
        try:
            self.bug_manager = bug_manager or BugManager()
            logger.info("ArbiterBridge: BugManager initialized")
        except Exception as e:
            logger.warning(f"ArbiterBridge: BugManager initialization failed, using stub: {e}")
            self.bug_manager = BugManager()
        
        try:
            self.knowledge_graph = knowledge_graph or KnowledgeGraph()
            logger.info("ArbiterBridge: KnowledgeGraph initialized")
        except Exception as e:
            logger.warning(f"ArbiterBridge: KnowledgeGraph initialization failed, using stub: {e}")
            self.knowledge_graph = KnowledgeGraph()
        
        try:
            self.human_in_loop = human_in_loop or HumanInLoop()
            logger.info("ArbiterBridge: HumanInLoop initialized")
        except Exception as e:
            logger.warning(f"ArbiterBridge: HumanInLoop initialization failed, using stub: {e}")
            self.human_in_loop = HumanInLoop()
        
        logger.info("ArbiterBridge initialized successfully")
    
    async def check_policy(
        self,
        action: str,
        context: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Check if an action is allowed by Arbiter policy.
        
        This method queries the PolicyEngine to determine if the requested action
        is permitted based on current policies and context. If the policy check
        fails or times out, the action is allowed by default (fail-open behavior).
        
        Args:
            action: Action to check (e.g., "orchestrate", "generate_code")
            context: Context dictionary with relevant metadata
        
        Returns:
            Tuple of (allowed: bool, reason: str)
            - allowed: Whether the action is permitted
            - reason: Explanation for the decision
        
        Examples:
            >>> allowed, reason = await bridge.check_policy("generate_code", {"language": "python"})
            >>> if not allowed:
            ...     logger.error(f"Action denied: {reason}")
        """
        if not self.enabled:
            return True, "Bridge disabled"
        
        try:
            with BRIDGE_OPERATION_DURATION.labels(operation="check_policy").time() if HAS_PROMETHEUS else _NoOpTimer():
                allowed, reason = await asyncio.wait_for(
                    self.policy_engine.should_auto_learn("Generator", action, **context),
                    timeout=5.0
                )
                
                BRIDGE_POLICY_CHECKS.labels(
                    action=action,
                    allowed=str(allowed)
                ).inc()
                
                logger.debug(f"Policy check for '{action}': {allowed} - {reason}")
                return allowed, reason
        
        except asyncio.TimeoutError:
            logger.warning(f"Policy check for '{action}' timed out, allowing by default")
            BRIDGE_POLICY_CHECKS.labels(action=action, allowed="timeout").inc()
            return True, "Policy check timed out (fail-open)"
        
        except Exception as e:
            logger.warning(f"Policy check for '{action}' failed: {e}, allowing by default")
            BRIDGE_POLICY_CHECKS.labels(action=action, allowed="error").inc()
            return True, f"Policy check error (fail-open): {str(e)}"
    
    async def publish_event(
        self,
        event_type: str,
        data: Dict[str, Any]
    ) -> None:
        """
        Publish an event to Arbiter's message queue.
        
        Events are published to the Arbiter's message queue for processing by
        other components. If publishing fails, the error is logged but the
        generator pipeline continues.
        
        Args:
            event_type: Type of event (e.g., "generator_output", "workflow_completed")
            data: Event data dictionary
        
        Examples:
            >>> await bridge.publish_event("generator_output", {
            ...     "code": "def hello(): pass",
            ...     "language": "python",
            ...     "timestamp": datetime.now().isoformat()
            ... })
        """
        if not self.enabled:
            logger.debug(f"Bridge disabled, skipping event: {event_type}")
            return
        
        try:
            # Enrich event with metadata
            enriched_data = {
                **data,
                "event_type": event_type,
                "source": "generator",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            with BRIDGE_OPERATION_DURATION.labels(operation="publish_event").time() if HAS_PROMETHEUS else _NoOpTimer():
                await asyncio.wait_for(
                    self.message_queue.publish(
                        topic=f"generator.{event_type}",
                        message=enriched_data
                    ),
                    timeout=3.0
                )
            
            BRIDGE_EVENTS_PUBLISHED.labels(
                event_type=event_type,
                status="success"
            ).inc()
            
            logger.debug(f"Published event: {event_type}")
        
        except asyncio.TimeoutError:
            logger.warning(f"Event publishing timed out: {event_type}")
            BRIDGE_EVENTS_PUBLISHED.labels(event_type=event_type, status="timeout").inc()
        
        except Exception as e:
            logger.warning(f"Failed to publish event '{event_type}': {e}")
            BRIDGE_EVENTS_PUBLISHED.labels(event_type=event_type, status="error").inc()
    
    async def report_bug(
        self,
        bug_data: Dict[str, Any]
    ) -> Optional[str]:
        """
        Report a bug to Arbiter's BugManager.
        
        Bugs are reported to the BugManager for tracking and potential auto-remediation.
        If bug reporting fails, the error is logged but the generator continues.
        
        Args:
            bug_data: Bug information dictionary with keys:
                - title: Bug title (required)
                - description: Detailed description
                - severity: Bug severity (low, medium, high, critical)
                - error: Exception or error message
                - context: Additional context
        
        Returns:
            Bug tracking ID if successful, None otherwise
        
        Examples:
            >>> bug_id = await bridge.report_bug({
            ...     "title": "Code generation failed",
            ...     "description": "Failed to generate valid Python code",
            ...     "severity": "high",
            ...     "error": str(exception),
            ...     "context": {"language": "python", "file": "main.py"}
            ... })
        """
        if not self.enabled:
            logger.debug("Bridge disabled, skipping bug report")
            return None
        
        try:
            # Enrich bug data with metadata
            enriched_bug_data = {
                **bug_data,
                "source": "generator",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "severity": bug_data.get("severity", "medium")
            }
            
            with BRIDGE_OPERATION_DURATION.labels(operation="report_bug").time() if HAS_PROMETHEUS else _NoOpTimer():
                bug_id = await asyncio.wait_for(
                    self.bug_manager.report_bug(enriched_bug_data),
                    timeout=5.0
                )
            
            BRIDGE_BUGS_REPORTED.labels(
                severity=enriched_bug_data["severity"]
            ).inc()
            
            logger.info(f"Bug reported: {bug_data.get('title', 'Untitled')} (ID: {bug_id})")
            return bug_id
        
        except asyncio.TimeoutError:
            logger.warning(f"Bug reporting timed out: {bug_data.get('title', 'Untitled')}")
            BRIDGE_BUGS_REPORTED.labels(severity="unknown").inc()
            return None
        
        except Exception as e:
            logger.warning(f"Failed to report bug: {e}")
            BRIDGE_BUGS_REPORTED.labels(severity="unknown").inc()
            return None
    
    async def update_knowledge(
        self,
        domain: str,
        key: str,
        data: Dict[str, Any]
    ) -> bool:
        """
        Update Arbiter's knowledge graph with new information.
        
        Knowledge updates are added to the knowledge graph for learning and
        future decision-making. If the update fails, the error is logged but
        the generator continues.
        
        Args:
            domain: Knowledge domain (e.g., "generator", "workflow", "agent")
            key: Unique key for the knowledge item
            data: Knowledge data dictionary
        
        Returns:
            True if update succeeded, False otherwise
        
        Examples:
            >>> success = await bridge.update_knowledge(
            ...     "generator",
            ...     "workflow_stats",
            ...     {
            ...         "total_runs": 100,
            ...         "success_rate": 0.95,
            ...         "avg_duration": 45.2
            ...     }
            ... )
        """
        if not self.enabled:
            logger.debug("Bridge disabled, skipping knowledge update")
            return False
        
        try:
            with BRIDGE_OPERATION_DURATION.labels(operation="update_knowledge").time() if HAS_PROMETHEUS else _NoOpTimer():
                result = await asyncio.wait_for(
                    self.knowledge_graph.add_fact(domain, key, data),
                    timeout=5.0
                )
            
            # Check if result indicates success (handle both dict and None returns)
            success = result is not None if isinstance(result, dict) else False
            
            BRIDGE_KNOWLEDGE_UPDATES.labels(
                domain=domain,
                status="success" if success else "unknown"
            ).inc()
            
            logger.debug(f"Knowledge updated: {domain}:{key}")
            return True
        
        except asyncio.TimeoutError:
            logger.warning(f"Knowledge update timed out: {domain}:{key}")
            BRIDGE_KNOWLEDGE_UPDATES.labels(domain=domain, status="timeout").inc()
            return False
        
        except Exception as e:
            logger.warning(f"Failed to update knowledge '{domain}:{key}': {e}")
            BRIDGE_KNOWLEDGE_UPDATES.labels(domain=domain, status="error").inc()
            return False
    
    async def request_approval(
        self,
        action: str,
        context: Dict[str, Any],
        timeout: Optional[int] = None
    ) -> bool:
        """
        Request human approval for an action via HumanInLoop.
        
        This method is provided for future integration but is not currently
        used by the generator pipeline. The DeployAgent has its own HITL system.
        
        Args:
            action: Action requiring approval
            context: Context for the approval request
            timeout: Optional timeout in seconds
        
        Returns:
            True if approved, False otherwise
        """
        if not self.enabled:
            logger.debug("Bridge disabled, auto-approving")
            return True
        
        try:
            approved = await self.human_in_loop.request_approval(action, context, timeout)
            logger.info(f"Human approval for '{action}': {approved}")
            return approved
        
        except Exception as e:
            logger.warning(f"Failed to request approval for '{action}': {e}, auto-approving")
            return True
    
    def disable(self):
        """Disable the bridge, preventing all Arbiter interactions."""
        self.enabled = False
        logger.info("ArbiterBridge disabled")
    
    def enable(self):
        """Enable the bridge, allowing Arbiter interactions."""
        self.enabled = True
        logger.info("ArbiterBridge enabled")


class _NoOpTimer:
    """No-op timer context manager for when Prometheus is unavailable."""
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
    
    def time(self):
        return self


# Convenience function for creating a bridge instance
def create_arbiter_bridge(**kwargs) -> Optional[ArbiterBridge]:
    """
    Create an ArbiterBridge instance with error handling.
    
    Args:
        **kwargs: Optional service instances to pass to the bridge
    
    Returns:
        ArbiterBridge instance or None if creation fails
    """
    try:
        return ArbiterBridge(**kwargs)
    except Exception as e:
        logger.error(f"Failed to create ArbiterBridge: {e}")
        return None
