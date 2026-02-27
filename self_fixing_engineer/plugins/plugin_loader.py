# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
PluginLoader — wires all integration plugins into the OmniCore / Arbiter
plugin registry so that list_plugins(), get_plugin_for_task(), and
discover() can find them.

Design principles
-----------------
* **Lazy registry acquisition** — the registry is resolved inside
  ``initialize_all_plugins()``, never at module import time.
* **Fault-isolated loading** — each plugin is wrapped in its own
  try/except so a missing optional dependency (e.g. ``aiormq`` for
  RabbitMQ) never prevents the other plugins from loading.
* **Class registration only** — the loader registers gateway *classes*
  (factories) with the registry.  Instantiation and lifecycle management
  (``startup`` / ``shutdown``) remain the responsibility of each plugin's
  own ``initialize()`` / ``shutdown()`` / ``app_lifecycle()`` functions.
* **Idempotent** — safe to call more than once; already-registered names
  are silently skipped by the registry.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft import of PlugInKind so this module is importable without the full
# omnicore / arbiter stack being present (e.g. in unit tests).
# ---------------------------------------------------------------------------
try:
    from self_fixing_engineer.arbiter.arbiter_plugin_registry import PlugInKind
except Exception:  # pragma: no cover
    try:
        from omnicore_engine.plugin_base import PlugInKind  # type: ignore[assignment]
    except Exception:
        from enum import Enum

        class PlugInKind(Enum):  # type: ignore[no-redef]
            SINK = "sink"
            INTEGRATION = "integration"


def _get_registry() -> Optional[Any]:
    """Return the active plugin registry, or *None* if unavailable."""
    try:
        from self_fixing_engineer.arbiter.arbiter_plugin_registry import get_registry
        return get_registry()
    except Exception:  # pragma: no cover
        return None


def _register(registry: Any, kind: Any, name: str, cls: Any, version: str = "1.0.0") -> None:
    """Register plugin *cls* with *registry* under *kind* / *name*.

    Parameters
    ----------
    registry:
        The active ``PluginRegistry`` instance, or *None* if the registry is
        unavailable (e.g. during unit tests without the full arbiter stack).
        When *None* the function is a no-op — plugins are still importable,
        but not discoverable via the registry.
    kind:
        A ``PlugInKind`` enum value that categorises the plugin
        (e.g. ``PlugInKind.SINK`` or ``PlugInKind.INTEGRATION``).
    name:
        The unique string name used to look up the plugin in the registry.
    cls:
        The plugin *class* (factory) to register.  An actual gateway instance
        is **not** passed here; instantiation and lifecycle management remain
        the responsibility of each plugin module's ``initialize()`` / ``shutdown()``
        / ``app_lifecycle()`` functions.
    version:
        Semantic version string for the plugin (default ``"1.0.0"``).
    """
    if registry is None:
        return
    try:
        registry.register_instance(kind=kind, name=name, instance=cls, version=version)
    except Exception as exc:
        logger.warning(
            "Failed to register plugin '%s' with registry: %s",
            name,
            exc,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Public lifecycle API
# ---------------------------------------------------------------------------

async def initialize_all_plugins() -> None:
    """Import every integration plugin class and register it with the registry.

    Plugin lifecycle (``startup`` / ``shutdown``) is *not* managed here —
    each plugin module owns that via its own ``initialize()`` and
    ``shutdown()`` / ``app_lifecycle()`` functions.

    Raises nothing — load failures are logged as warnings so a broken
    optional dependency never prevents the rest from being registered.
    """
    registry = _get_registry()

    # ---- Azure EventGrid (INTEGRATION) ----
    try:
        from self_fixing_engineer.plugins.azure_eventgrid_plugin.azure_eventgrid_plugin import (  # noqa: E501
            AzureEventGridAuditHook,
        )
        _register(registry, PlugInKind.INTEGRATION, "azure_eventgrid", AzureEventGridAuditHook)
        logger.info("azure_eventgrid plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("azure_eventgrid plugin could not be loaded: %s", exc, exc_info=True)

    # ---- DLT Backend (INTEGRATION) ----
    try:
        from self_fixing_engineer.plugins.dlt_backend.dlt_backend import CheckpointManager
        _register(registry, PlugInKind.INTEGRATION, "dlt_backend", CheckpointManager)
        logger.info("dlt_backend plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("dlt_backend plugin could not be loaded: %s", exc, exc_info=True)

    # ---- Kafka (SINK) ----
    try:
        from self_fixing_engineer.plugins.kafka.kafka_plugin import KafkaAuditPlugin
        _register(registry, PlugInKind.SINK, "kafka", KafkaAuditPlugin)
        logger.info("kafka plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("kafka plugin could not be loaded: %s", exc, exc_info=True)

    # ---- PagerDuty (INTEGRATION) ----
    try:
        from self_fixing_engineer.plugins.pagerduty_plugin.pagerduty_plugin import (
            PagerDutyGateway,
        )
        _register(registry, PlugInKind.INTEGRATION, "pagerduty", PagerDutyGateway)
        logger.info("pagerduty plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("pagerduty plugin could not be loaded: %s", exc, exc_info=True)

    # ---- Google Pub/Sub (SINK) ----
    try:
        from self_fixing_engineer.plugins.pubsub_plugin.pubsub_plugin import PubSubGateway
        _register(registry, PlugInKind.SINK, "pubsub", PubSubGateway)
        logger.info("pubsub plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("pubsub plugin could not be loaded: %s", exc, exc_info=True)

    # ---- RabbitMQ (SINK) ----
    try:
        from self_fixing_engineer.plugins.rabbitmq_plugin.rabbitmq_plugin import RabbitMQGateway
        _register(registry, PlugInKind.SINK, "rabbitmq", RabbitMQGateway)
        logger.info("rabbitmq plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("rabbitmq plugin could not be loaded: %s", exc, exc_info=True)

    # ---- SIEM (SINK) ----
    try:
        from self_fixing_engineer.plugins.siem_plugin.siem_plugin import SIEMGatewayManager
        _register(registry, PlugInKind.SINK, "siem", SIEMGatewayManager)
        logger.info("siem plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("siem plugin could not be loaded: %s", exc, exc_info=True)

    # ---- Slack (SINK) ----
    try:
        from self_fixing_engineer.plugins.slack_plugin.slack_plugin import SlackGatewayManager
        _register(registry, PlugInKind.SINK, "slack", SlackGatewayManager)
        logger.info("slack plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("slack plugin could not be loaded: %s", exc, exc_info=True)

    # ---- SNS (SINK) ----
    try:
        from self_fixing_engineer.plugins.sns_plugin.sns_plugin import SNSGatewayManager
        _register(registry, PlugInKind.SINK, "sns", SNSGatewayManager)
        logger.info("sns plugin registered.")
    except (Exception, SystemExit) as exc:
        logger.warning("sns plugin could not be loaded: %s", exc, exc_info=True)

    logger.info("PluginLoader: all integration plugins processed.")


async def shutdown_all_plugins() -> None:
    """No-op: gateway lifecycle is managed by each plugin module.

    Retained for API symmetry and forward-compatibility — callers that
    wire ``initialize_all_plugins`` / ``shutdown_all_plugins`` into a
    central application lifecycle do not need to change when a future
    plugin requires explicit shutdown work here.
    """
    logger.info("PluginLoader: shutdown complete.")
