# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
RegistryBridge — Unified plugin-registry bridge for the SFE platform.

Problem
-------
Two separate plugin registries exist without a shared discovery mechanism:

* ``self_fixing_engineer/simulation/registry.py`` — :data:`SIM_REGISTRY`,
  a category-keyed dict of simulation runner / tool plugins.
* ``self_fixing_engineer/arbiter/arbiter_plugin_registry.py`` — the arbiter
  plugin registry, a class-based registry with dependency graphs and
  PlugInKind enum enforcement.

Code that needs to call a simulation plugin from the arbiter layer (or vice
versa) has no standard cross-module lookup path, leading to duplicated
imports and tight coupling.

Solution
--------
:class:`RegistryBridge` provides:

* **Lazy, fault-tolerant loading** of both registries — each is resolved on
  first access; ``ImportError`` and runtime errors are caught and logged
  without raising, so the bridge works in any partial deployment.
* **Unified discovery** via :meth:`discover_plugin` — searches simulation
  categories first, then the arbiter registry, returning the first match.
* **Reload / sync** — :meth:`sync` discards cached handles and re-resolves
  both registries (useful after hot-reload events).
* **Prometheus observability** — lookup hits/misses and sync events are
  tracked via :func:`shared.noop_metrics.safe_metric`.
* **Thread-safe singleton** — :func:`get_registry_bridge` uses
  double-checked locking so concurrent callers share a single instance.

Architecture
------------
::

    ┌─────────────────────────────────────────────────────────┐
    │                    RegistryBridge                       │
    │                                                         │
    │   simulation/registry.py          ──► _sim_registry     │
    │   arbiter/arbiter_plugin_registry ──► _arbiter_registry │
    │                                                         │
    │   discover_plugin(name) ─── sim categories             │
    │                        └─── arbiter.get_plugin(name)   │
    └─────────────────────────────────────────────────────────┘

Usage
-----
::

    from self_fixing_engineer.shared.registry_bridge import get_registry_bridge

    bridge = get_registry_bridge()
    plugin = bridge.discover_plugin("monte_carlo_runner")
    all_plugins = bridge.get_all_plugins()
    bridge.sync()  # force reload after hot-reload

See Also
--------
:mod:`self_fixing_engineer.shared.simulation_bridge` — event-bus bridge
between simulation and arbiter runtime systems.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Prometheus metrics — lazy, thread-safe, noop-safe
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter as _PCounter
except ImportError:  # pragma: no cover
    _PCounter = None  # type: ignore[assignment]

try:
    from shared.noop_metrics import safe_metric as _safe_metric  # type: ignore[import]
except ImportError:  # pragma: no cover
    def _safe_metric(factory: Any, name: str, doc: str, **kw: Any) -> Any:  # type: ignore[misc]
        class _Noop:
            def labels(self, *_: Any, **__: Any) -> "_Noop":
                return self
            def inc(self, *_: Any, **__: Any) -> None:
                pass
        return _Noop()

logger = logging.getLogger(__name__)

_REGISTRY_LOOKUPS = _safe_metric(
    _PCounter,
    "sfe_registry_bridge_lookups_total",
    "Total plugin lookups via RegistryBridge",
    labelnames=["source", "status"],
)
_REGISTRY_SYNCS = _safe_metric(
    _PCounter,
    "sfe_registry_bridge_syncs_total",
    "Total RegistryBridge sync() calls",
    labelnames=["status"],
)


class RegistryBridge:
    """Unified read-only bridge over simulation and arbiter plugin registries.

    Provides lazy loading, cross-registry plugin discovery, forced reload,
    and Prometheus metrics.  All methods are thread-safe.

    Attributes
    ----------
    _sim_registry : dict | None
        Cached result of ``simulation.registry.get_registry()``, or ``None``
        before first access or after :meth:`sync`.
    _arbiter_registry : Any | None
        Cached arbiter registry object, or ``None`` before first access or
        after :meth:`sync`.
    """

    def __init__(self) -> None:
        self._sim_registry: Optional[Dict[str, Any]] = None
        self._arbiter_registry: Optional[Any] = None
        self._lock: threading.Lock = threading.Lock()
        self._load_registries()

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _load_registries(self) -> None:
        """Resolve both registries, logging warnings on failure.

        Safe to call multiple times; caches are set only when resolution
        succeeds.
        """
        try:
            from self_fixing_engineer.simulation.registry import (  # type: ignore[import]
                get_registry as _get_sim,
            )

            self._sim_registry = _get_sim()
            logger.debug("RegistryBridge: Simulation registry loaded")
        except ImportError as exc:
            logger.warning("RegistryBridge: Simulation registry not available — %s", exc)
        except Exception:
            logger.error(
                "RegistryBridge: Failed to load simulation registry", exc_info=True
            )

        try:
            from self_fixing_engineer.arbiter.arbiter_plugin_registry import (  # type: ignore[import]
                get_registry as _get_arbiter,
            )

            self._arbiter_registry = _get_arbiter()
            logger.debug("RegistryBridge: Arbiter registry loaded")
        except ImportError as exc:
            logger.warning("RegistryBridge: Arbiter registry not available — %s", exc)
        except Exception:
            logger.error(
                "RegistryBridge: Failed to load arbiter registry", exc_info=True
            )

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_simulation_plugins(self) -> Dict[str, Any]:
        """Return all plugins from the simulation registry.

        Returns
        -------
        dict
            The category-keyed simulation plugin dict, or ``{}`` if unavailable.
        """
        with self._lock:
            if self._sim_registry is None:
                self._load_registries()
            return self._sim_registry or {}

    def get_arbiter_plugins(self) -> Any:
        """Return the arbiter plugin registry object.

        Returns
        -------
        Any | None
            The arbiter registry instance, or ``None`` if unavailable.
        """
        with self._lock:
            if self._arbiter_registry is None:
                self._load_registries()
            return self._arbiter_registry

    def get_all_plugins(self) -> Dict[str, Any]:
        """Return a unified snapshot of plugins from both registries.

        Returns
        -------
        dict
            ``{"simulation": <sim_registry>, "arbiter": <arbiter_registry>}``.
        """
        return {
            "simulation": self.get_simulation_plugins(),
            "arbiter": self.get_arbiter_plugins(),
        }

    def sync(self) -> None:
        """Discard cached registry handles and force a fresh resolution.

        Use after hot-reload events or when the plugin set may have changed
        at runtime.
        """
        with self._lock:
            self._sim_registry = None
            self._arbiter_registry = None
            try:
                self._load_registries()
                _REGISTRY_SYNCS.labels(status="success").inc()
                logger.info("RegistryBridge: Registries synchronised")
            except Exception:
                _REGISTRY_SYNCS.labels(status="error").inc()
                logger.error("RegistryBridge: Sync failed", exc_info=True)

    def discover_plugin(self, name: str) -> Optional[Any]:
        """Search for *name* across both registries, returning the first match.

        Search order:

        1. All categories in the simulation registry (``SIM_REGISTRY``).
        2. The arbiter registry via ``get_plugin(name)`` if that method exists.

        Parameters
        ----------
        name:
            Plugin identifier to search for.

        Returns
        -------
        Any | None
            The plugin object if found, ``None`` otherwise.
        """
        # Simulation registry — category-keyed dict of dicts
        sim_plugins = self.get_simulation_plugins()
        if isinstance(sim_plugins, dict):
            for _category, plugins in sim_plugins.items():
                if isinstance(plugins, dict) and name in plugins:
                    _REGISTRY_LOOKUPS.labels(source="simulation", status="hit").inc()
                    return plugins[name]

        # Arbiter registry — object with optional get_plugin()
        arbiter_registry = self.get_arbiter_plugins()
        if arbiter_registry is not None and hasattr(arbiter_registry, "get_plugin"):
            try:
                result = arbiter_registry.get_plugin(name)
                if result is not None:
                    _REGISTRY_LOOKUPS.labels(source="arbiter", status="hit").inc()
                    return result
            except Exception:
                logger.debug(
                    "RegistryBridge: arbiter.get_plugin(%r) raised", name, exc_info=True
                )

        _REGISTRY_LOOKUPS.labels(source="none", status="miss").inc()
        logger.debug("RegistryBridge: Plugin %r not found in either registry", name)
        return None


# ---------------------------------------------------------------------------
# Module-level singleton — double-checked locking
# ---------------------------------------------------------------------------

_bridge_instance: Optional[RegistryBridge] = None
_instance_lock: threading.Lock = threading.Lock()


def get_registry_bridge() -> RegistryBridge:
    """Return (or create) the global :class:`RegistryBridge` singleton.

    Uses double-checked locking to prevent duplicate instantiation under
    concurrent startup.

    Returns
    -------
    RegistryBridge
        The shared bridge instance.
    """
    global _bridge_instance
    if _bridge_instance is not None:
        return _bridge_instance
    with _instance_lock:
        if _bridge_instance is None:
            _bridge_instance = RegistryBridge()
    return _bridge_instance
