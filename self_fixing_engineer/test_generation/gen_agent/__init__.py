# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_generation/gen_agent/__init__.py
"""
Lightweight package initializer for gen_agent.

Enables imports like:
    from test_generation.gen_agent import agents
without eagerly importing the whole subpackage (and its optional deps).
"""

from __future__ import annotations

# Change from lazy imports to explicit imports for pytest compatibility
from . import agents, api, cli, graph, io_utils, runtime, atco_signal

__all__ = [
    "agents",
    "api",
    "cli",
    "graph",
    "io_utils",
    "runtime",
    "atco_signal",
]
