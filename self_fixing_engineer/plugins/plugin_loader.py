# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
PluginLoader — wires all integration plugins into the OmniCore / Arbiter
plugin registry so that list_plugins(), get_plugin_for_task(), and
discover() can find them.

Each plugin is wrapped with a try/except so that a missing optional
dependency (e.g. `aiormq` for RabbitMQ) does not prevent the other
plugins from loading.
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry helpers (soft import so this module is importable standalone)
# ---------------------------------------------------------------------------
try:
    from self_fixing_engineer.arbiter.arbiter_plugin_registry import (
        PlugInKind,
        get_registry,
    )
    _registry = get_registry()
except Exception:  # pragma: no cover
    _registry = None  # type: ignore
    try:
        from omnicore_engine.plugin_base import PlugInKind
    except Exception:
        from enum import Enum

        class PlugInKind(Enum):  # type: ignore
            SINK = "sink"
            INTEGRATION = "integration"


# ---------------------------------------------------------------------------
# Track which gateway instances were initialised so we can shut them down.
# ---------------------------------------------------------------------------
_active_gateways: List = []


async def initialize_all_plugins() -> None:
    """Import every integration plugin and register it with the registry."""
    global _active_gateways
    _active_gateways = []

    # ---- Azure EventGrid ----
    try:
        from self_fixing_engineer.plugins.azure_eventgrid_plugin.azure_eventgrid_plugin import (
            AzureEventGridAuditHook,
        )
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.INTEGRATION,
                name="azure_eventgrid",
                instance=AzureEventGridAuditHook,
                version="1.0.0",
            )
        logger.info("azure_eventgrid plugin registered.")
    except Exception as exc:
        logger.warning(f"azure_eventgrid plugin could not be loaded: {exc}")

    # ---- DLT Backend ----
    try:
        from self_fixing_engineer.plugins.dlt_backend.dlt_backend import (
            CheckpointManager,
        )
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.INTEGRATION,
                name="dlt_backend",
                instance=CheckpointManager,
                version="1.0.0",
            )
        logger.info("dlt_backend plugin registered.")
    except Exception as exc:
        logger.warning(f"dlt_backend plugin could not be loaded: {exc}")

    # ---- Kafka ----
    try:
        from self_fixing_engineer.plugins.kafka.kafka_plugin import KafkaAuditPlugin
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.SINK,
                name="kafka",
                instance=KafkaAuditPlugin,
                version="1.0.0",
            )
        logger.info("kafka plugin registered.")
    except Exception as exc:
        logger.warning(f"kafka plugin could not be loaded: {exc}")

    # ---- PagerDuty ----
    try:
        from self_fixing_engineer.plugins.pagerduty_plugin.pagerduty_plugin import (
            PagerDutyGateway,
            initialize as pd_initialize,
            shutdown as pd_shutdown,
        )
        await pd_initialize()
        from self_fixing_engineer.plugins.pagerduty_plugin import pagerduty_plugin as _pd_mod
        if _pd_mod.pagerduty_gateway is not None:
            _active_gateways.append(_pd_mod.pagerduty_gateway)
            await _pd_mod.pagerduty_gateway.startup()
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.INTEGRATION,
                name="pagerduty",
                instance=PagerDutyGateway,
                version="1.0.0",
            )
        logger.info("pagerduty plugin registered.")
    except Exception as exc:
        logger.warning(f"pagerduty plugin could not be loaded: {exc}")

    # ---- Google Pub/Sub ----
    try:
        from self_fixing_engineer.plugins.pubsub_plugin.pubsub_plugin import (
            PubSubGateway,
            initialize as ps_initialize,
        )
        await ps_initialize()
        from self_fixing_engineer.plugins.pubsub_plugin import pubsub_plugin as _ps_mod
        if _ps_mod.pubsub_gateway is not None:
            _active_gateways.append(_ps_mod.pubsub_gateway)
            await _ps_mod.pubsub_gateway.startup()
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.SINK,
                name="pubsub",
                instance=PubSubGateway,
                version="1.0.0",
            )
        logger.info("pubsub plugin registered.")
    except Exception as exc:
        logger.warning(f"pubsub plugin could not be loaded: {exc}")

    # ---- RabbitMQ ----
    try:
        from self_fixing_engineer.plugins.rabbitmq_plugin.rabbitmq_plugin import (
            RabbitMQGateway,
            initialize as rmq_initialize,
        )
        await rmq_initialize()
        from self_fixing_engineer.plugins.rabbitmq_plugin import rabbitmq_plugin as _rmq_mod
        if _rmq_mod.rabbitmq_gateway is not None:
            _active_gateways.append(_rmq_mod.rabbitmq_gateway)
            await _rmq_mod.rabbitmq_gateway.startup()
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.SINK,
                name="rabbitmq",
                instance=RabbitMQGateway,
                version="1.0.0",
            )
        logger.info("rabbitmq plugin registered.")
    except Exception as exc:
        logger.warning(f"rabbitmq plugin could not be loaded: {exc}")

    # ---- SIEM ----
    try:
        from self_fixing_engineer.plugins.siem_plugin.siem_plugin import (
            SIEMGatewayManager,
        )
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.SINK,
                name="siem",
                instance=SIEMGatewayManager,
                version="1.0.0",
            )
        logger.info("siem plugin registered.")
    except Exception as exc:
        logger.warning(f"siem plugin could not be loaded: {exc}")

    # ---- Slack ----
    try:
        from self_fixing_engineer.plugins.slack_plugin.slack_plugin import (
            SlackGatewayManager,
        )
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.SINK,
                name="slack",
                instance=SlackGatewayManager,
                version="1.0.0",
            )
        logger.info("slack plugin registered.")
    except Exception as exc:
        logger.warning(f"slack plugin could not be loaded: {exc}")

    # ---- SNS ----
    try:
        from self_fixing_engineer.plugins.sns_plugin.sns_plugin import (
            SNSGatewayManager,
        )
        if _registry is not None:
            _registry.register_instance(
                kind=PlugInKind.SINK,
                name="sns",
                instance=SNSGatewayManager,
                version="1.0.0",
            )
        logger.info("sns plugin registered.")
    except Exception as exc:
        logger.warning(f"sns plugin could not be loaded: {exc}")

    logger.info("PluginLoader: all integration plugins processed.")


async def shutdown_all_plugins() -> None:
    """Gracefully shut down all gateway instances started by initialize_all_plugins."""
    for gateway in _active_gateways:
        try:
            await gateway.shutdown()
        except Exception as exc:
            logger.warning(f"Error shutting down gateway {gateway!r}: {exc}")
    _active_gateways.clear()
    logger.info("PluginLoader: all plugins shut down.")
