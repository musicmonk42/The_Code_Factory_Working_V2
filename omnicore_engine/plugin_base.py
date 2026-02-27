# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical plugin base classes and enums for the OmniCore Engine plugin system.

This module is the **single source of truth** for the foundational plugin
abstractions shared across every subsystem:

- :class:`PlugInKind` — unified enum covering every plugin category across
  OmniCore, Arbiter / SFE, Generator, and integration layers.
- :class:`PluginBase` — abstract base class defining the lifecycle contract
  (``initialize → start → (running) → stop``) that all class-based plugins
  must implement.

Design Principles
-----------------
* **Zero heavy imports at module level** — this module intentionally avoids
  importing Pydantic, prometheus_client, OpenTelemetry, asyncio event-loop
  internals, or any external library so that it can be imported safely during
  ``pytest --collect-only``, at decorator-evaluation time, and inside
  ``TYPE_CHECKING`` blocks without triggering side-effects.
* **Thread-safe** — all definitions are immutable (Enum members, ABC methods)
  and stateless.
* **Forward-compatible** — new plugin kinds can be added to :class:`PlugInKind`
  without changing any consuming module; existing ``str(kind)`` serialisations
  continue to round-trip correctly because ``PlugInKind`` inherits from ``str``.

Migration Guide
---------------
Instead of defining local ``PlugInKind`` or ``PluginBase`` fallbacks, import
directly from this module::

    from omnicore_engine.plugin_base import PlugInKind, PluginBase

If ``omnicore_engine`` is unavailable (e.g., standalone arbiter deployments),
wrap the import in a ``try / except ImportError`` and provide a minimal local
fallback — see ``arbiter_plugin_registry.py`` for the canonical pattern.

Static type checking with ``mypy --strict`` is recommended for maximum safety.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PlugInKind — unified plugin category taxonomy
# ---------------------------------------------------------------------------


class PlugInKind(str, Enum):
    """Unified, platform-wide plugin kind taxonomy.

    Every plugin registered anywhere in the platform — OmniCore, Arbiter,
    Generator, simulation layer, or integration plugins — is categorised by
    exactly one ``PlugInKind`` value.  Using a single source-of-truth enum
    prevents value mismatches and ensures consistent routing on the message bus.

    Categories are grouped by subsystem origin but are available everywhere.
    The ``str`` mixin means ``str(PlugInKind.FIX) == "fix"`` and JSON
    serialisation works without custom encoders.

    To add a new kind:
    1.  Add the member here under the appropriate section.
    2.  Run the existing test suite — no other file needs changing.
    3.  If arbiter tests mock ``omnicore_engine.plugin_base``, verify the mock
        is updated or uses a wildcard import.
    """

    # --- OmniCore core kinds ------------------------------------------------
    FIX = "fix"
    CHECK = "check"
    VALIDATION = "validation"
    EXECUTION = "execution"
    CORE_SERVICE = "core_service"
    SCENARIO = "scenario"
    CUSTOM = "custom"
    AGGREGATOR = "aggregator"
    AI_ASSISTANT = "ai_assistant"
    OPTIMIZATION = "optimization"
    MONITORING = "monitoring"
    GROWTH_MANAGER = "growth_manager"
    SIMULATION_RUNNER = "simulation_runner"
    EVOLUTION = "evolution"
    RL_ENVIRONMENT = "rl_environment"

    # --- Arbiter / SFE kinds ------------------------------------------------
    WORKFLOW = "workflow"
    VALIDATOR = "validator"
    REPORTER = "reporter"
    ANALYTICS = "analytics"
    STRATEGY = "strategy"
    TRANSFORMER = "transformer"

    # --- Integration / event-sink kinds -------------------------------------
    SINK = "sink"
    INTEGRATION = "integration"

    # --- Refactor Crew agent kinds ------------------------------------------
    # These correspond to the agents defined in refactor_agent.yaml /
    # crew_config.yaml and are used to categorise them in the plugin registry
    # so that the Arbiter and OmniCore can route tasks and telemetry correctly.
    REFACTOR_AGENT = "refactor_agent"
    CODE_HEALER = "code_healer"
    JUDGE_AGENT = "judge_agent"
    ETHICS_SENTINEL = "ethics_sentinel"
    ORACLE_AGENT = "oracle_agent"
    CI_CD_TRIGGER = "ci_cd_trigger"
    SIMULATION_ORCHESTRATOR = "simulation_orchestrator"
    HUMAN_IN_THE_LOOP_AGENT = "human_in_the_loop_agent"
    SWARM_AGENT = "swarm_agent"
    CREW_AGENT = "crew_agent"


# ---------------------------------------------------------------------------
# PluginBase — lifecycle contract for class-based plugins
# ---------------------------------------------------------------------------


class PluginBase(ABC):
    """Abstract base class for all class-based plugins across the platform.

    Any plugin that registers via the Arbiter
    :class:`~arbiter_plugin_registry.PluginRegistry` (class-based registration)
    **must** inherit from ``PluginBase`` and provide concrete implementations of
    every abstract method.

    Lifecycle
    ---------
    The host system guarantees that these methods are called in order::

        plugin.initialize()   # Allocate resources, open connections
        plugin.start()        # Begin processing / subscribe to topics
        # ... plugin is running ...
        plugin.stop()         # Graceful shutdown & resource cleanup

    ``initialize`` and ``start`` are separated so that all plugins can be
    initialised in parallel before any starts processing — important when
    plugins depend on one another.

    Health & Discovery
    ------------------
    * ``health_check()`` — invoked periodically by the monitoring subsystem
      (and by ``PluginRegistry.health_check_all``).  Should execute quickly
      (<1 s) and return ``False`` or raise to indicate an unhealthy state.
    * ``get_capabilities()`` — used by UIs and orchestration layers to
      dynamically discover the services offered by a plugin.  Return a
      machine-readable list of capability identifiers.

    Hot-Reload
    ----------
    ``on_reload()`` is called after the plugin module has been reimported.
    Override it to re-read configuration, re-establish connections, etc.
    The default implementation is a no-op.

    Thread Safety
    -------------
    All lifecycle methods are ``async`` and will be called from the
    ``PluginRegistry``'s event-loop context.  Avoid blocking I/O in these
    methods; use ``asyncio.to_thread`` for CPU-bound or synchronous work.

    Example
    -------
    ::

        from omnicore_engine.plugin_base import PluginBase

        class MyPlugin(PluginBase):
            async def initialize(self) -> None:
                self._conn = await open_database()

            async def start(self) -> None:
                self._task = asyncio.create_task(self._process_loop())

            async def stop(self) -> None:
                self._task.cancel()
                await self._conn.close()

            async def health_check(self) -> bool:
                return self._conn.is_alive()

            async def get_capabilities(self) -> list[str]:
                return ["data_ingestion", "anomaly_detection"]
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Allocate resources, open connections, perform one-time setup.

        Raising here will cause the ``PluginRegistry`` to mark the plugin as
        failed and (with retry/tenacity) attempt re-initialisation.
        """

    @abstractmethod
    async def start(self) -> None:
        """Begin active processing.

        Called only after ``initialize`` has completed successfully.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the plugin and release all resources.

        Implementations should be idempotent — calling ``stop`` on an
        already-stopped plugin must not raise.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` if the plugin is operating normally.

        Keep this method lightweight (< 1 second).  The monitoring subsystem
        calls it at a configurable interval; an ``asyncio.TimeoutError`` is
        treated as unhealthy.
        """

    @abstractmethod
    async def get_capabilities(self) -> list[str]:
        """Return a list of capability identifiers exposed by this plugin.

        Capability strings should be stable, machine-readable tokens
        (e.g. ``"code_analysis"``, ``"test_generation"``).  UIs and the
        ``DecisionOptimizer`` use these to route tasks.
        """

    def on_reload(self) -> None:
        """Called after the plugin's module has been hot-reloaded.

        Override to refresh cached configuration, re-establish connections,
        or perform any cleanup needed after a code update.  The default
        implementation is a no-op.
        """
        pass


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "PlugInKind",
    "PluginBase",
]

