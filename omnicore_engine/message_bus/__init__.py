# omnicore_engine/message_bus/__init__.py

"""
OmniCore Sharded Message Bus (OMSB)

The central communication layer for OmniCoreEngine. It provides:
1. Sharding and prioritized queuing.
2. Resilience via Circuit Breakers and Retries.
3. Integration with external systems (Kafka, Redis, etc.).
4. Security features (Encryption, Signing, Dedup).
"""
import logging

from .backpressure import BackpressureManager
from .cache import MessageCache
from .context import ContextPropagationMiddleware, ExecutionContext
from .dead_letter_queue import DeadLetterQueue
from .encryption import EncryptionStrategy, FernetEncryption

# FIX: Corrected import source for MessageBusGuardian from 'resilience' to 'guardian'.
from .guardian import MessageBusGuardian

# Basic utilities
from .hash_ring import ConsistentHashRing
from .message_types import Message, MessageSchema
from .rate_limit import RateLimiter, RateLimitError
from .resilience import CircuitBreaker, RetryPolicy
from .sharded_message_bus import PluginMessageBusAdapter, ShardedMessageBus

# Integrations (Conditionally available)
try:
    from .integrations.kafka_bridge import KafkaBridge, KafkaBridgeConfig
except ImportError:

    class KafkaBridge:
        pass

    class KafkaBridgeConfig:
        pass


try:
    from .integrations.redis_bridge import RedisBridge, RedisBridgeConfig
except ImportError:

    class RedisBridge:
        pass

    class RedisBridgeConfig:
        pass


# Additional helper classes (assuming they are siblings or part of a core module)
try:
    from .filters import MessageFilter
except ImportError:
    # Define a minimal mock if MessageFilter is missing
    class MessageFilter:
        def __init__(self, condition):
            self.condition = condition

        def apply(self, payload):
            return self.condition(payload)


# Import CLI from second merge-conflicted version
try:
    from .cli import message_bus_cli
except ImportError:

    def message_bus_cli():
        print("Error: message_bus_cli component not found.")
        logger.warning("message_bus_cli component not found.")


# Initialize logger
logger = logging.getLogger(__name__)


# Export public API - Merged and corrected from both conflicting versions
__all__ = [
    # Core
    "ShardedMessageBus",
    "PluginMessageBusAdapter",
    "Message",
    "MessageSchema",
    "MessageFilter",
    "ExecutionContext",
    "ContextPropagationMiddleware",
    # Resilience & Guardians
    "RetryPolicy",
    "CircuitBreaker",
    "MessageBusGuardian",
    "BackpressureManager",
    "DeadLetterQueue",
    # Utilities
    "ConsistentHashRing",
    "MessageCache",
    "EncryptionStrategy",
    "FernetEncryption",
    "RateLimiter",
    "RateLimitError",
    # Integrations
    "KafkaBridge",
    "KafkaBridgeConfig",
    "RedisBridge",
    "RedisBridgeConfig",
    # CLI
    "message_bus_cli",
]
