# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
DEPRECATED: ``arbiter_array_backend`` has been superseded by
:mod:`self_fixing_engineer.arbiter.persistent_array_store`.

Background
----------
The original ``ArrayBackend`` ABC and ``ConcreteArrayBackend`` implementation
in this file were misnamed: the module provided a **persistent data store**
(CRUD operations: ``append``, ``get``, ``update``, ``delete``, ``query`` backed
by JSON / SQLite / Redis / PostgreSQL) rather than the numerical-computing array
abstraction implied by the name.

A separate, unrelated ``ArrayBackend`` class in
``omnicore_engine/array_backend.py`` provides the genuine numerical-computing
abstraction (wrapping NumPy / CuPy / Dask / PyTorch / Quantum backends with
50+ math operations: ``sin``, ``cos``, ``fft``, ``linalg_svd``, etc.).  The
name collision between the two classes created confusion for readers and static
analysers alike.

Changes in the rename
---------------------
* Module renamed:      ``arbiter_array_backend``  → ``persistent_array_store``
* ABC renamed:         ``ArrayBackend``            → ``PersistentArrayStore``
* Concrete renamed:    ``ConcreteArrayBackend``    → ``ConcretePersistentArrayStore``
* ``array()`` / ``asnumpy()`` helpers removed from the concrete class — these
  were trivial ``np.array()`` wrappers that belonged in
  :mod:`omnicore_engine.array_backend`, not in a persistence layer.  Call
  sites should use ``numpy.array()`` directly.

Migration Guide
---------------
Replace imports of the old module::

    # OLD — triggers DeprecationWarning
    from self_fixing_engineer.arbiter.arbiter_array_backend import (
        ArrayBackend,
        ConcreteArrayBackend,
        ArrayBackendError,
        ArrayMeta,
        ArraySizeLimitError,
        StorageError,
    )

with imports from the canonical module::

    # NEW — preferred
    from self_fixing_engineer.arbiter.persistent_array_store import (
        PersistentArrayStore,          # replaces ArrayBackend
        ConcretePersistentArrayStore,  # replaces ConcreteArrayBackend
        ArrayBackendError,
        ArrayMeta,
        ArraySizeLimitError,
        StorageError,
    )

Old names (``ArrayBackend``, ``ConcreteArrayBackend``) remain available
as aliases in :mod:`~self_fixing_engineer.arbiter.persistent_array_store`
for codebases that cannot be migrated immediately.

Deprecation Timeline
--------------------
* **v1.x** — This shim emits a :exc:`DeprecationWarning` once per process.
  All existing imports continue to work without code changes.
* **v2.0** — This shim will be removed.  All imports must be updated to
  :mod:`~self_fixing_engineer.arbiter.persistent_array_store`.

See Also
--------
:mod:`self_fixing_engineer.arbiter.persistent_array_store`
    Canonical module — use this for all new code.
:mod:`omnicore_engine.array_backend`
    Genuine numerical-computing array abstraction (NumPy / CuPy / Dask /
    PyTorch / Quantum / Neuromorphic backends).
:mod:`omnicore_engine.scenario_plugin_manager`
    Reference example of the platform's backward-compatibility shim pattern.
"""

import warnings as _warnings

# ---------------------------------------------------------------------------
# Emit a single DeprecationWarning per process (simplefilter="once" prevents
# log noise when the same import appears in multiple hot-code paths).
# ---------------------------------------------------------------------------

_warnings.simplefilter("once", DeprecationWarning)
_warnings.warn(
    "arbiter_array_backend is deprecated and will be removed in v2.0.  "
    "Use self_fixing_engineer.arbiter.persistent_array_store instead.  "
    "Canonical class names: PersistentArrayStore / ConcretePersistentArrayStore.",
    DeprecationWarning,
    stacklevel=2,
)

# ---------------------------------------------------------------------------
# Re-export everything from the canonical module so that existing
# ``from arbiter_array_backend import X`` statements continue to work.
# ---------------------------------------------------------------------------

from self_fixing_engineer.arbiter.persistent_array_store import (  # noqa: E402, F401
    ArrayBackend,
    ArrayBackendError,
    ArrayMeta,
    ArraySizeLimitError,
    ConcreteArrayBackend,
    ConcretePersistentArrayStore,
    PermissionManager,
    PersistentArrayStore,
    StorageError,
)

# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # ── Canonical new names ───────────────────────────────────────────────
    "PersistentArrayStore",
    "ConcretePersistentArrayStore",
    # ── Backward-compatible aliases (deprecated) ──────────────────────────
    "ArrayBackend",
    "ConcreteArrayBackend",
    # ── Unchanged supporting types ────────────────────────────────────────
    "ArrayBackendError",
    "StorageError",
    "ArraySizeLimitError",
    "ArrayMeta",
    "PermissionManager",
]
