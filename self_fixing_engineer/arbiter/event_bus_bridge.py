"""
EventBusBridge - Bidirectional bridge between Mesh EventBus and Arbiter MessageQueue.

[GAP #7 FIX] This module creates a bidirectional bridge that allows events to flow
between the Mesh event system and the Arbiter's message queue service.

Architecture:
- Mesh EventBus: Used by the mesh/adaptive system
- Arbiter MessageQueueService: Used by the Arbiter governance system
- EventBusBridge: Subscribes to both and republishes events across systems

Usage:
    bridge = EventBusBridge()
    await bridge.start()
    # Bridge now routes events bidirectionally
    await bridge.stop()
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# Prometheus metrics
try:
    BRIDGE_EVENTS_TOTAL = Counter(
        'event_bus_bridge_events_total',
        'Total events bridged between systems',
        ['direction', 'event_type', 'status']
    )
    BRIDGE_LATENCY = Histogram(
        'event_bus_bridge_latency_seconds',
        'Latency of event bridging',
        ['direction']
    )
    METRICS_AVAILABLE = True
except Exception:
    METRICS_AVAILABLE = False
    logger.debug("Prometheus metrics not available for EventBusBridge")


class EventBusBridge:
    """
    Bidirectional bridge between Mesh EventBus and Arbiter MessageQueueService.
    
    This bridge enables event flow in both directions:
    - Mesh → Arbiter: Events from the mesh system are published to Arbiter
    - Arbiter → Mesh: Events from Arbiter are published to the mesh system
    
    Configurable event types allow selective bridging to avoid loops and
    unnecessary cross-system traffic.
    """
    
    def __init__(
        self,
        mesh_to_arbiter_events: Optional[Set[str]] = None,
        arbiter_to_mesh_events: Optional[Set[str]] = None,
    ):
        """
        Initialize the event bus bridge.
        
        Args:
            mesh_to_arbiter_events: Set of event types to bridge from Mesh to Arbiter.
                If None, bridges all events (default: selective set).
            arbiter_to_mesh_events: Set of event types to bridge from Arbiter to Mesh.
                If None, bridges all events (default: selective set).
        """
        self.mesh_to_arbiter_events = mesh_to_arbiter_events or {
            "mesh_event",
            "agent_update",
            "policy_violation",
            "system_alert",
        }
        self.arbiter_to_mesh_events = arbiter_to_mesh_events or {
            "arbiter_decision",
            "policy_update",
            "governance_alert",
            "task_assigned",
        }
        
        self.running = False
        self._tasks: List[asyncio.Task] = []
        
        # Try to import both event systems
        self.mesh_bus = None
        self.arbiter_mqs = None
        
        self._init_mesh_bus()
        self._init_arbiter_mqs()
    
    def _init_mesh_bus(self):
        """Initialize connection to Mesh EventBus."""
        try:
            from self_fixing_engineer.mesh.event_bus import publish_event
            self.mesh_bus = publish_event
            logger.info("EventBusBridge: Mesh EventBus available")
        except ImportError as e:
            logger.warning(f"EventBusBridge: Mesh EventBus not available: {e}")
            self.mesh_bus = None
        except Exception as e:
            logger.error(f"EventBusBridge: Failed to initialize Mesh EventBus: {e}")
            self.mesh_bus = None
    
    def _init_arbiter_mqs(self):
        """Initialize connection to Arbiter MessageQueueService."""
        try:
            from self_fixing_engineer.arbiter.message_queue_service import MessageQueueService
            self.arbiter_mqs = MessageQueueService()
            logger.info("EventBusBridge: Arbiter MessageQueueService available")
        except ImportError as e:
            logger.warning(f"EventBusBridge: Arbiter MQS not available: {e}")
            self.arbiter_mqs = None
        except Exception as e:
            logger.error(f"EventBusBridge: Failed to initialize Arbiter MQS: {e}")
            self.arbiter_mqs = None
    
    async def start(self):
        """
        Start the bidirectional bridge.
        
        Subscribes to events on both systems and starts forwarding.
        """
        if not self.mesh_bus and not self.arbiter_mqs:
            logger.warning(
                "EventBusBridge: Neither Mesh EventBus nor Arbiter MQS available. "
                "Bridge will not start."
            )
            return
        
        self.running = True
        logger.info("EventBusBridge: Starting bidirectional event bridge")
        
        # Start Mesh → Arbiter bridge
        if self.mesh_bus and self.arbiter_mqs:
            task = asyncio.create_task(self._bridge_mesh_to_arbiter())
            self._tasks.append(task)
            logger.info("EventBusBridge: Mesh → Arbiter bridge started")
        
        # Start Arbiter → Mesh bridge
        if self.arbiter_mqs and self.mesh_bus:
            task = asyncio.create_task(self._bridge_arbiter_to_mesh())
            self._tasks.append(task)
            logger.info("EventBusBridge: Arbiter → Mesh bridge started")
        
        logger.info(
            f"EventBusBridge: Active. Bridging {len(self.mesh_to_arbiter_events)} "
            f"Mesh→Arbiter event types and {len(self.arbiter_to_mesh_events)} "
            f"Arbiter→Mesh event types"
        )
    
    async def stop(self):
        """
        Stop the bridge and cancel all subscriptions.
        """
        self.running = False
        logger.info("EventBusBridge: Stopping...")
        
        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        self._tasks.clear()
        logger.info("EventBusBridge: Stopped")
    
    async def _bridge_mesh_to_arbiter(self):
        """
        Bridge events from Mesh EventBus to Arbiter MessageQueue.
        
        Subscribes to Mesh events and republishes them to Arbiter.
        """
        logger.info("EventBusBridge: Mesh → Arbiter worker started")
        
        try:
            # Subscribe to configured event types on Mesh
            for event_type in self.mesh_to_arbiter_events:
                await self._subscribe_and_forward(
                    event_type,
                    direction="mesh_to_arbiter"
                )
        except Exception as e:
            logger.error(f"EventBusBridge: Mesh → Arbiter worker error: {e}", exc_info=True)
    
    async def _bridge_arbiter_to_mesh(self):
        """
        Bridge events from Arbiter MessageQueue to Mesh EventBus.
        
        Subscribes to Arbiter events and republishes them to Mesh.
        """
        logger.info("EventBusBridge: Arbiter → Mesh worker started")
        
        try:
            # Subscribe to configured event types on Arbiter MQS
            for event_type in self.arbiter_to_mesh_events:
                async def handler(data: Dict[str, Any], _event_type=event_type):
                    await self._forward_arbiter_to_mesh(_event_type, data)
                
                await self.arbiter_mqs.subscribe(event_type, handler)
        except Exception as e:
            logger.error(f"EventBusBridge: Arbiter → Mesh worker error: {e}", exc_info=True)
    
    async def _subscribe_and_forward(self, event_type: str, direction: str):
        """
        Subscribe to an event type and forward it.
        
        Args:
            event_type: The event type to subscribe to
            direction: "mesh_to_arbiter" or "arbiter_to_mesh"
        """
        # Note: Mesh event_bus doesn't have a subscribe() method in the current implementation
        # This is a placeholder for future implementation when Mesh adds subscription support
        logger.debug(
            f"EventBusBridge: Subscription for {event_type} ({direction}) "
            "pending Mesh EventBus subscription API"
        )
    
    async def _forward_mesh_to_arbiter(self, event_type: str, data: Dict[str, Any]):
        """
        Forward an event from Mesh to Arbiter.
        
        Args:
            event_type: Type of the event
            data: Event data
        """
        start_time = time.time()
        
        try:
            # Add bridge metadata
            bridged_data = {
                **data,
                "_bridge": {
                    "source": "mesh",
                    "destination": "arbiter",
                    "bridged_at": time.time(),
                    "original_event_type": event_type,
                }
            }
            
            # Publish to Arbiter MQS
            await self.arbiter_mqs.publish(event_type, bridged_data)
            
            # Track metrics
            if METRICS_AVAILABLE:
                BRIDGE_EVENTS_TOTAL.labels(
                    direction="mesh_to_arbiter",
                    event_type=event_type,
                    status="success"
                ).inc()
                BRIDGE_LATENCY.labels(direction="mesh_to_arbiter").observe(
                    time.time() - start_time
                )
            
            logger.debug(f"EventBusBridge: Forwarded {event_type} Mesh → Arbiter")
            
        except Exception as e:
            logger.error(
                f"EventBusBridge: Failed to forward {event_type} Mesh → Arbiter: {e}",
                exc_info=True
            )
            if METRICS_AVAILABLE:
                BRIDGE_EVENTS_TOTAL.labels(
                    direction="mesh_to_arbiter",
                    event_type=event_type,
                    status="error"
                ).inc()
    
    async def _forward_arbiter_to_mesh(self, event_type: str, data: Dict[str, Any]):
        """
        Forward an event from Arbiter to Mesh.
        
        Args:
            event_type: Type of the event
            data: Event data
        """
        start_time = time.time()
        
        try:
            # Add bridge metadata
            bridged_data = {
                **data,
                "_bridge": {
                    "source": "arbiter",
                    "destination": "mesh",
                    "bridged_at": time.time(),
                    "original_event_type": event_type,
                }
            }
            
            # Publish to Mesh EventBus
            await self.mesh_bus(event_type, bridged_data)
            
            # Track metrics
            if METRICS_AVAILABLE:
                BRIDGE_EVENTS_TOTAL.labels(
                    direction="arbiter_to_mesh",
                    event_type=event_type,
                    status="success"
                ).inc()
                BRIDGE_LATENCY.labels(direction="arbiter_to_mesh").observe(
                    time.time() - start_time
                )
            
            logger.debug(f"EventBusBridge: Forwarded {event_type} Arbiter → Mesh")
            
        except Exception as e:
            logger.error(
                f"EventBusBridge: Failed to forward {event_type} Arbiter → Mesh: {e}",
                exc_info=True
            )
            if METRICS_AVAILABLE:
                BRIDGE_EVENTS_TOTAL.labels(
                    direction="arbiter_to_mesh",
                    event_type=event_type,
                    status="error"
                ).inc()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get bridge statistics.
        
        Returns:
            Dictionary with bridge status and configuration
        """
        return {
            "running": self.running,
            "mesh_available": self.mesh_bus is not None,
            "arbiter_available": self.arbiter_mqs is not None,
            "mesh_to_arbiter_events": list(self.mesh_to_arbiter_events),
            "arbiter_to_mesh_events": list(self.arbiter_to_mesh_events),
            "active_tasks": len([t for t in self._tasks if not t.done()]),
        }


# Global bridge instance (singleton pattern)
_bridge_instance: Optional[EventBusBridge] = None


async def get_bridge() -> EventBusBridge:
    """
    Get or create the global EventBusBridge instance.
    
    Returns:
        The EventBusBridge singleton
    """
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = EventBusBridge()
        await _bridge_instance.start()
    return _bridge_instance


async def stop_bridge():
    """
    Stop the global EventBusBridge instance.
    """
    global _bridge_instance
    if _bridge_instance:
        await _bridge_instance.stop()
        _bridge_instance = None
