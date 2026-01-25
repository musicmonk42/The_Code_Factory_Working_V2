# omnicore_engine/message_bus/__init__.py

"""
OmniCore Sharded Message Bus (OMSB)

The central communication layer for OmniCoreEngine. It provides:
1. Sharding and prioritized queuing.
2. Resilience via Circuit Breakers and Retries.
3. Integration with external systems (Kafka, Redis, etc.).
4. Security features (Encryption, Signing, Dedup).

Note: This package uses lazy imports to minimize import-time overhead and prevent
heavy initialization during pytest collection or package import.
"""

import logging
import sys
from typing import TYPE_CHECKING

# Initialize logger
logger = logging.getLogger(__name__)

# Export public API names for documentation and IDE support
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

# Type checking imports (only for static type analysis, not at runtime)
if TYPE_CHECKING:
    from .backpressure import BackpressureManager
    from .cache import MessageCache
    from .context import ContextPropagationMiddleware, ExecutionContext
    from .dead_letter_queue import DeadLetterQueue
    from .encryption import EncryptionStrategy, FernetEncryption
    from .guardian import MessageBusGuardian
    from .hash_ring import ConsistentHashRing
    from .message_types import Message, MessageSchema
    from .rate_limit import RateLimiter, RateLimitError
    from .resilience import CircuitBreaker, RetryPolicy
    from .sharded_message_bus import PluginMessageBusAdapter, ShardedMessageBus


# Lazy import mapping for __getattr__
_LAZY_IMPORTS = {
    # Core
    "ShardedMessageBus": (".sharded_message_bus", "ShardedMessageBus"),
    "PluginMessageBusAdapter": (".sharded_message_bus", "PluginMessageBusAdapter"),
    "Message": (".message_types", "Message"),
    "MessageSchema": (".message_types", "MessageSchema"),
    "ExecutionContext": (".context", "ExecutionContext"),
    "ContextPropagationMiddleware": (".context", "ContextPropagationMiddleware"),
    # Resilience & Guardians
    "RetryPolicy": (".resilience", "RetryPolicy"),
    "CircuitBreaker": (".resilience", "CircuitBreaker"),
    "MessageBusGuardian": (".guardian", "MessageBusGuardian"),
    "BackpressureManager": (".backpressure", "BackpressureManager"),
    "DeadLetterQueue": (".dead_letter_queue", "DeadLetterQueue"),
    # Utilities
    "ConsistentHashRing": (".hash_ring", "ConsistentHashRing"),
    "MessageCache": (".cache", "MessageCache"),
    "EncryptionStrategy": (".encryption", "EncryptionStrategy"),
    "FernetEncryption": (".encryption", "FernetEncryption"),
    "RateLimiter": (".rate_limit", "RateLimiter"),
    "RateLimitError": (".rate_limit", "RateLimitError"),
}


def __getattr__(name: str):
    """
    Lazy load module attributes on first access (PEP 562).
    
    This allows for fast import times during pytest collection while still
    providing the same API as if all modules were imported at package load time.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib
            module = importlib.import_module(module_path, package=__name__)
            attr = getattr(module, attr_name)
            # Cache in module globals for subsequent access
            globals()[name] = attr
            return attr
        except (ImportError, AttributeError) as e:
            logger.debug(f"Failed to import {name} from {module_path}: {e}")
            raise AttributeError(f"module '{__name__}' has no attribute '{name}'") from e
    
    # Handle special cases for integrations
    if name == "KafkaBridge":
        try:
            from .integrations.kafka_bridge import KafkaBridge
            globals()["KafkaBridge"] = KafkaBridge
            return KafkaBridge
        except ImportError:
            class _KafkaBridge:
                pass
            globals()["KafkaBridge"] = _KafkaBridge
            return _KafkaBridge
    
    if name == "KafkaBridgeConfig":
        try:
            from .integrations.kafka_bridge import KafkaBridgeConfig
            globals()["KafkaBridgeConfig"] = KafkaBridgeConfig
            return KafkaBridgeConfig
        except ImportError:
            class _KafkaBridgeConfig:
                pass
            globals()["KafkaBridgeConfig"] = _KafkaBridgeConfig
            return _KafkaBridgeConfig
    
    if name == "RedisBridge":
        try:
            from .integrations.redis_bridge import RedisBridge
            globals()["RedisBridge"] = RedisBridge
            return RedisBridge
        except ImportError:
            class _RedisBridge:
                pass
            globals()["RedisBridge"] = _RedisBridge
            return _RedisBridge
    
    if name == "RedisBridgeConfig":
        try:
            from .integrations.redis_bridge import RedisBridgeConfig
            globals()["RedisBridgeConfig"] = RedisBridgeConfig
            return RedisBridgeConfig
        except ImportError:
            class _RedisBridgeConfig:
                pass
            globals()["RedisBridgeConfig"] = _RedisBridgeConfig
            return _RedisBridgeConfig
    
    if name == "MessageFilter":
        try:
            from .filters import MessageFilter
            globals()["MessageFilter"] = MessageFilter
            return MessageFilter
        except ImportError:
            # Define a minimal mock if MessageFilter is missing
            class _MessageFilter:
                def __init__(self, condition):
                    self.condition = condition

                def apply(self, payload):
                    return self.condition(payload)
            globals()["MessageFilter"] = _MessageFilter
            return _MessageFilter
    
    if name == "message_bus_cli":
        try:
            from .cli import message_bus_cli
            globals()["message_bus_cli"] = message_bus_cli
            return message_bus_cli
        except ImportError:
            def _message_bus_cli():
                print("Error: message_bus_cli component not found.")
                logger.warning("message_bus_cli component not found.")
            globals()["message_bus_cli"] = _message_bus_cli
            return _message_bus_cli
    
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
