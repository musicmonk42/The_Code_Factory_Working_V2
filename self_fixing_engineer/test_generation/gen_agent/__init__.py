# test_generation/gen_agent/__init__.py
"""
Lightweight package initializer for gen_agent.

Enables imports like:
    from test_generation.gen_agent import agents
without eagerly importing the whole subpackage (and its optional deps).
"""

from __future__ import annotations
import importlib

__all__ = [
    "agents",
    "api",
    "cli",
    "graph",
    "io_utils",
    "runtime",
    "atco_signal",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + __all__)
