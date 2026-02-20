# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Compatibility shim for legacy ``arbiter`` imports."""

import importlib

try:
    _arbiter_pkg = importlib.import_module("self_fixing_engineer.arbiter")
except ImportError as exc:
    raise ImportError(
        "Legacy 'arbiter' import requires 'self_fixing_engineer.arbiter' to be importable."
    ) from exc

__path__ = _arbiter_pkg.__path__
__all__ = getattr(_arbiter_pkg, "__all__", [])


def __getattr__(name):
    try:
        return getattr(_arbiter_pkg, name)
    except AttributeError as exc:
        raise AttributeError(
            f"module 'arbiter' has no attribute '{name}'"
        ) from exc
