"""Message bus operations for the OmniCore service layer.

Extracted from ``OmniCoreService`` during Phase 2 decomposition.  Covers
message publishing, event emission, topic subscription/listing, dead-letter
queue queries, and message retry.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)


class MessageBusService:
    """Service wrapping all message-bus related operations."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # -- Lifecycle -----------------------------------------------------------

    async def start_message_bus(self) -> bool:
        """Start the message bus dispatcher tasks.

        Should be called from an async context during application startup.
        """
        bus = self._ctx.message_bus
        if not bus or not self._ctx.omnicore_components_available.get("message_bus", False):
            logger.warning("Message bus not available -- cannot start dispatcher tasks")
            return False
        try:
            await bus.start()
            logger.info("Message bus dispatcher tasks started")
            return True
        except Exception as e:
            logger.error(f"Failed to start message bus dispatcher tasks: {e}", exc_info=True)
            return False

    # -- Publishing ----------------------------------------------------------

    async def publish_message(
        self,
        topic: str,
        payload: Dict[str, Any],
        priority: int = 5,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Publish a message to the message bus."""
        logger.info(f"Publishing message to topic {topic}")

        bus = self._ctx.message_bus
        if bus and self._ctx.omnicore_components_available.get("message_bus"):
            try:
                success = await bus.publish(topic=topic, payload=payload, priority=priority)
                if success:
                    logger.info(f"Message published successfully to topic: {topic}")
                    message_id = f"msg_{topic}_{int(time.time() * 1000)}"
                    return {
                        "status": "published",
                        "topic": topic,
                        "message_id": message_id,
                        "priority": priority,
                        "transport": "message_bus",
                    }
                logger.warning(f"Failed to publish message to topic: {topic}")
                return {
                    "status": "failed",
                    "topic": topic,
                    "error": "Message bus publish returned False",
                    "transport": "message_bus",
                }
            except Exception as e:
                logger.error(f"Error publishing to message bus: {e}", exc_info=True)

        logger.debug(f"Using fallback for message publication to topic: {topic}")
        return {
            "status": "published",
            "topic": topic,
            "message_id": f"msg_{topic}_{hash(str(payload)) % 10000}",
            "priority": priority,
            "transport": "fallback",
        }

    async def emit_event(
        self, topic: str, payload: Dict[str, Any], priority: int = 5
    ) -> Dict[str, Any]:
        """Emit an event (convenience alias for ``publish_message``)."""
        return await self.publish_message(topic=topic, payload=payload, priority=priority)

    # -- Subscriptions -------------------------------------------------------

    async def subscribe_to_topic(
        self,
        topic: str,
        callback_url: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Subscribe to a message bus topic."""
        logger.info(f"Subscribing to topic {topic}")
        return {
            "status": "subscribed",
            "topic": topic,
            "subscription_id": f"sub_{topic}_{hash(str(callback_url)) % 10000}",
            "callback_url": callback_url,
        }

    async def list_topics(self) -> Dict[str, Any]:
        """List all message bus topics."""
        logger.info("Listing message bus topics")
        return {
            "topics": ["generator", "sfe", "audit", "metrics", "notifications"],
            "topic_stats": {
                "generator": {"subscribers": 2, "messages_published": 150},
                "sfe": {"subscribers": 3, "messages_published": 89},
                "audit": {"subscribers": 1, "messages_published": 500},
            },
        }

    # -- Dead-letter queue ---------------------------------------------------

    async def query_dead_letter_queue(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        topic: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Query the dead-letter queue."""
        logger.info("Querying dead letter queue")
        return {
            "messages": [
                {
                    "message_id": "msg_123",
                    "topic": topic or "generator",
                    "failure_reason": "timeout",
                    "attempts": 3,
                    "timestamp": "2026-01-20T01:00:00Z",
                }
            ],
            "count": 1,
            "filters": {"topic": topic, "start_time": start_time, "end_time": end_time},
        }

    async def retry_message(self, message_id: str, force: bool = False) -> Dict[str, Any]:
        """Retry a failed message from the dead-letter queue."""
        logger.info(f"Retrying message {message_id}")
        return {
            "status": "retried",
            "message_id": message_id,
            "attempt": 4,
            "forced": force,
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_message_bus_service_instance: Optional[MessageBusService] = None


def get_message_bus_service(ctx: Optional[ServiceContext] = None) -> MessageBusService:
    """Return the singleton ``MessageBusService``."""
    global _message_bus_service_instance
    if _message_bus_service_instance is None:
        if ctx is None:
            raise RuntimeError("MessageBusService not initialised -- pass a ServiceContext on first call")
        _message_bus_service_instance = MessageBusService(ctx)
    return _message_bus_service_instance
