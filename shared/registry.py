# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unified generic Registry for the Code Factory platform.

Problem
-------
Two or more independent ``Registry`` class definitions existed in the codebase:

- ``generator/runner/__init__.py``          — primary definition
- ``generator/runner/summarize_utils.py``   — local fallback copy

Each copy had a different feature set (some lacked thread safety, some lacked
dict-style access), causing subtle inconsistencies when names were registered
in one module but looked up through another.

Solution
--------
This module provides a single, production-quality implementation with:

* **Thread-safe register / clear** — a ``threading.Lock`` serialises all
  mutations so concurrent workers do not corrupt the registry state.
* **Full typed API** — ``register``, ``get``, ``get_all``, ``clear``,
  ``__getitem__``, ``__setitem__``, ``__contains__``, ``__len__``,
  ``__iter__``.
* **Zero external dependencies at import time** — only stdlib modules.

Architecture
------------
::

    Thread A                     Thread B
       │                            │
       │ registry.register("fn", f) │ registry.get("fn")
       │         │                  │         │
       │    ┌────▼──────────────────▼────┐    │
       │    │   threading.Lock           │    │
       │    │   _items: Dict[str, Any]   │    │
       │    └────────────────────────────┘    │
       │                                      │

Usage
-----
::

    from shared.registry import Registry

    MY_REGISTRY = Registry()
    MY_REGISTRY.register("sha256", hashlib.sha256)
    fn = MY_REGISTRY.get("sha256")

    # Dict-style
    MY_REGISTRY["md5"] = hashlib.md5
    assert "md5" in MY_REGISTRY

Industry Standards Applied
--------------------------
* **PEP 484** — full type annotations on all public methods.
* **PEP 517 / 518** — zero mandatory runtime dependencies.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


class Registry:
    """Generic thread-safe registry mapping string names to arbitrary objects.

    Supports both method-based and dict-style access so that callers can adopt
    whichever style fits their context.

    Thread safety
    -------------
    A single ``threading.Lock`` is held during all mutations (``register``,
    ``clear``, ``__setitem__``) and during reads that must be consistent with
    those mutations (``get``, ``__getitem__``, ``__contains__``, ``__len__``).

    Parameters
    ----------
    name : str
        Optional human-readable name for the registry (used in ``__repr__``).

    Examples
    --------
    ::

        from shared.registry import Registry

        reg = Registry(name="hashers")
        reg.register("sha256", hashlib.sha256)
        assert reg.get("sha256") is hashlib.sha256
        assert "sha256" in reg
        assert len(reg) == 1

        for name in reg:
            print(name)
    """

    def __init__(self, name: str = "default") -> None:
        self._name: str = name
        self._lock: threading.Lock = threading.Lock()
        self._items: Dict[str, Any] = {}

    # ── Mutation API ──────────────────────────────────────────────────────────

    def register(self, name: str, item: Any) -> None:
        """Register *item* under *name*.

        Parameters
        ----------
        name : str
            Registry key.  Existing entries under the same key are silently
            overwritten.
        item : Any
            Value to store (typically a callable but any object is accepted).

        Returns
        -------
        None

        Examples
        --------
        ::

            reg = Registry()
            reg.register("add", lambda a, b: a + b)
        """
        with self._lock:
            self._items[name] = item
        logger.debug("Registry '%s': registered %r", self._name, name)

    def clear(self) -> None:
        """Remove all registered items.

        Returns
        -------
        None
        """
        with self._lock:
            self._items.clear()
        logger.debug("Registry '%s': cleared.", self._name)

    # ── Read API ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Any]:
        """Return the item registered under *name*, or ``None``.

        Parameters
        ----------
        name : str
            Registry key to look up.

        Returns
        -------
        Any | None
            The registered item, or ``None`` if *name* is not registered.

        Examples
        --------
        ::

            fn = reg.get("sha256")
            if fn is None:
                raise KeyError("sha256 not registered")
        """
        with self._lock:
            return self._items.get(name)

    def get_all(self) -> List[str]:
        """Return a snapshot list of all registered names.

        Returns
        -------
        list[str]
            Names in insertion order (CPython 3.7+).
        """
        with self._lock:
            return list(self._items.keys())

    # ── Dict-style access ─────────────────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        """Return the item for *key*.

        Raises
        ------
        KeyError
            If *key* is not registered.
        """
        with self._lock:
            return self._items[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """Register *value* under *key* (dict-style write)."""
        with self._lock:
            self._items[key] = value

    def __contains__(self, key: object) -> bool:
        """Return ``True`` if *key* is registered."""
        with self._lock:
            return key in self._items

    def __len__(self) -> int:
        """Return the number of registered items."""
        with self._lock:
            return len(self._items)

    def __iter__(self) -> Iterator[str]:
        """Iterate over registered names (snapshot at call time)."""
        with self._lock:
            keys = list(self._items.keys())
        return iter(keys)

    def __repr__(self) -> str:  # pragma: no cover
        with self._lock:
            count = len(self._items)
        return f"Registry(name={self._name!r}, items={count})"


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "Registry",
]
