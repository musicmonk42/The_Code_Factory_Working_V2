# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
self_fixing_engineer.refactor_agent
====================================

Public API for the Refactor Agent subsystem.

Exports
-------
- :class:`ConfigDBResolver` — resolves ``configdb://`` URIs to configuration values.
- :class:`ServiceRouter` — routes ``service://`` URIs to handler implementations.
- :mod:`integration_clients` — clients for all YAML-declared integration endpoints.
- :mod:`api` — FastAPI application (``/refactor``, ``/status``, ``/agents``, ``/health``).
- :mod:`cli` — command-line interface (``refactor``, ``selftest``, ``status``).
- :mod:`dashboard` — Streamlit dashboard.
"""

from __future__ import annotations

from self_fixing_engineer.refactor_agent.config_resolver import ConfigDBResolver
from self_fixing_engineer.refactor_agent.service_router import ServiceRouter

__all__ = [
    "ConfigDBResolver",
    "ServiceRouter",
]
