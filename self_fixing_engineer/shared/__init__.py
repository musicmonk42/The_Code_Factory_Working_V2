# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
self_fixing_engineer.shared — Cross-module integration utilities.

Problem
-------
Several platform subsystems (simulation, arbiter) operated with isolated
plugin registries and disconnected event buses, preventing cross-module
plugin discovery and event routing.

Solution
--------
This package provides two production-quality bridge components:

:mod:`~self_fixing_engineer.shared.registry_bridge`
    Unified bridge between ``simulation/registry.py`` (:data:`SIM_REGISTRY`)
    and ``arbiter/arbiter_plugin_registry.py``, enabling cross-module plugin
    discovery with Prometheus observability.

:mod:`~self_fixing_engineer.shared.simulation_bridge`
    Async bridge helper that wires a live Arbiter instance to the simulation
    module's :class:`~self_fixing_engineer.simulation.simulation_module.ShardedMessageBus`,
    forwarding ``requests.simulation.*`` events to Arbiter and publishing
    Arbiter ``task_complete`` events back to the simulation bus.

Architecture
------------
::

    self_fixing_engineer/
    └── shared/
        ├── __init__.py          ← this file; convenience re-exports
        ├── registry_bridge.py   ← RegistryBridge + get_registry_bridge()
        └── simulation_bridge.py ← setup_simulation_bridge()

Usage
-----
::

    # Registry bridge
    from self_fixing_engineer.shared import get_registry_bridge
    bridge = get_registry_bridge()
    plugin = bridge.discover_plugin("monte_carlo_runner")

    # Simulation ↔ Arbiter event bridge
    from self_fixing_engineer.shared import setup_simulation_bridge
    await setup_simulation_bridge(arbiter_instance, simulation_bus)

See Also
--------
:mod:`self_fixing_engineer.arbiter.event_bus_bridge` — tripartite event
bridge (Mesh, Arbiter, Simulation) managed as an application-level singleton.
"""

from __future__ import annotations

from self_fixing_engineer.shared.registry_bridge import (
    RegistryBridge,
    get_registry_bridge,
)
from self_fixing_engineer.shared.simulation_bridge import setup_simulation_bridge

__all__ = [
    "RegistryBridge",
    "get_registry_bridge",
    "setup_simulation_bridge",
]
