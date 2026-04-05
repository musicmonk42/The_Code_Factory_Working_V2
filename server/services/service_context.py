"""Shared state container for the OmniCore service layer.

This module defines ``ServiceContext``, a dataclass that holds the mutable
shared state previously scattered across ``OmniCoreService.__init__``.  By
extracting the state into a plain data object we decouple helper functions
from the god-class and make dependency injection straightforward.

No business logic lives here -- only data definitions and a thin async
factory that wires up sensible defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ServiceContext:
    """Immutable-ish bag of shared state for the OmniCore service layer.

    Attributes:
        llm_config: LLM provider configuration dict (provider name, model,
            API-key presence, etc.).  May be ``None`` when no config module
            is available.
        agents: Mapping of agent name to loaded agent module.  Populated
            lazily on first pipeline run.
        message_bus: ``ShardedMessageBus`` instance used for inter-module
            communication, or ``None`` if the bus failed to initialise.
        omnicore_engine: ``OmniCoreEngine`` instance, or ``None``.
        omnicore_components_available: Boolean flags keyed by component name
            (``message_bus``, ``plugin_registry``, ``metrics``, ``audit``).
        job_output_base: Root directory under which per-job output folders
            are created.
        kafka_producer: Kafka producer for event streaming, or ``None``.
    """

    llm_config: Optional[Dict[str, Any]] = None
    agents: Dict[str, Any] = field(default_factory=dict)
    agents_available: Dict[str, bool] = field(default_factory=dict)
    message_bus: Optional[Any] = None
    plugin_registry: Optional[Any] = None
    metrics_client: Optional[Any] = None
    audit_client: Optional[Any] = None
    omnicore_engine: Optional[Any] = None
    omnicore_components_available: Dict[str, bool] = field(
        default_factory=lambda: {
            "message_bus": False,
            "plugin_registry": False,
            "metrics": False,
            "audit": False,
        }
    )
    job_output_base: Path = field(default_factory=lambda: Path("./uploads"))
    kafka_producer: Optional[Any] = None
    llm_status: Dict[str, Any] = field(default_factory=lambda: {
        "provider": None,
        "configured": False,
        "validated": False,
        "error": None,
    })


async def create_service_context(
    llm_config: Optional[Dict[str, Any]] = None,
) -> ServiceContext:
    """Factory that builds a ``ServiceContext`` with sensible defaults.

    This is intentionally thin -- it does *not* perform heavy initialisation
    such as connecting to Kafka or starting the message bus.  Callers (i.e.
    ``OmniCoreService.__init__``) are responsible for wiring up the real
    components after construction.

    Args:
        llm_config: Optional LLM configuration dictionary.  When ``None``
            the context is created with an empty config (useful for tests).

    Returns:
        A new ``ServiceContext`` instance ready for further configuration.
    """
    job_output_base = Path("./uploads")
    job_output_base.mkdir(parents=True, exist_ok=True)

    ctx = ServiceContext(
        llm_config=llm_config,
        agents={},
        message_bus=None,
        omnicore_engine=None,
        omnicore_components_available={
            "message_bus": False,
            "plugin_registry": False,
            "metrics": False,
            "audit": False,
        },
        job_output_base=job_output_base,
        kafka_producer=None,
    )
    logger.debug("ServiceContext created (llm_config=%s)", "present" if llm_config else "None")
    return ctx


# -- Singleton accessor ---------------------------------------------------

_service_context_instance: Optional[ServiceContext] = None


def get_service_context() -> ServiceContext:
    """Return the shared ServiceContext singleton.

    During app startup, ``create_service_context`` populates this.  If called
    before startup (e.g. in tests), returns a default empty context.
    """
    global _service_context_instance  # noqa: PLW0603
    if _service_context_instance is None:
        _service_context_instance = ServiceContext()
    return _service_context_instance


def set_service_context(ctx: ServiceContext) -> None:
    """Set the shared ServiceContext singleton (called once at app startup)."""
    global _service_context_instance  # noqa: PLW0603
    _service_context_instance = ctx
