# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Canonical base classes for plugin registries — Code Factory Platform.

Problem
-------
At least **six independent** ``PluginRegistry`` classes exist across the
codebase, every one implementing the same four-method contract
(``register``, ``unregister``, ``get``, ``list_plugins``) and the same
defensive patterns (``threading.Lock``, ``logging.getLogger``, Prometheus
metrics) from scratch:

- ``omnicore_engine/plugin_registry.py``        — audit logging, entrypoints
- ``self_fixing_engineer/arbiter/arbiter_plugin_registry.py`` — PlugInKind enums,
  dependency graph, ``register_with_omnicore()`` bridge
- ``self_fixing_engineer/arbiter/plugin_config.py`` — ImmutableDict / OTEL
- ``generator/agents/docgen_agent/docgen_agent.py`` — compliance plugins
- ``generator/agents/deploy_agent/deploy_agent.py`` — hot-reload via watchdog
- ``generator/agents/docgen_agent/docgen_response_validator.py`` — format plugins,
  hot-reload via watchdog

Two additional concepts appear in ≥ 2 registries without a shared home:

1. **Hot-reload** (``watchdog.FileSystemEventHandler`` + ``_scan_plugins``):
   duplicated in ``deploy_agent.py`` and ``docgen_response_validator.py``.

2. **Dependency & version validation** (``_validate_name``, ``_validate_version``,
   ``_satisfies_version``): duplicated in ``arbiter_plugin_registry.py`` and
   ``plugin_config.py``.

Solution
--------
This module provides **three production-quality building blocks** that every
registry can inherit, removing the duplicated boilerplate while preserving
each registry's domain-specific logic:

:class:`BasePluginRegistry`
    Abstract base class defining the canonical four-method interface, a
    shared :class:`threading.Lock`, and a scoped :class:`logging.Logger`.
    Optional Prometheus counters and an OpenTelemetry tracer are lazily
    resolved so the class carries zero mandatory runtime dependencies.

:class:`HotReloadableRegistryMixin`
    Extracted from ``deploy_agent.py`` and ``docgen_response_validator.py``.
    Provides the ``watchdog``-compatible ``on_modified`` handler and declares
    ``_scan_plugins`` / ``_load_plugin_file`` as the standard hooks.

:class:`DependencyAwareRegistryMixin`
    Extracted from ``arbiter_plugin_registry.py``.  Provides
    ``_validate_name``, ``_validate_version``, and ``_satisfies_version`` as
    reusable concrete helpers.  ``_validate_dependencies`` is intentionally
    left abstract because full graph validation requires knowledge of the
    host registry's plugin-kind enum and graph library.

Architecture
------------
::

    ┌─────────────────────────────────────────────────────────────────────┐
    │                    shared/plugin_registry_base.py                   │
    │                                                                     │
    │  ┌──────────────────────────────────────────────────────────────┐   │
    │  │  BasePluginRegistry (abc.ABC)                                │   │
    │  │                                                              │   │
    │  │  _lock : threading.Lock          # serialises mutations      │   │
    │  │  logger: logging.Logger          # scoped per subclass       │   │
    │  │  _tracer: OTel Tracer | NullTracer  # lazy, graceful         │   │
    │  │                                                              │   │
    │  │  register(name, plugin, **kw)  ─ NotImplementedError        │   │
    │  │  unregister(name)              ─ NotImplementedError        │   │
    │  │  get(name)                     ─ NotImplementedError        │   │
    │  │  list_plugins()                ─ NotImplementedError        │   │
    │  └───────────────┬──────────────────────────┬───────────────────┘   │
    │                  │                          │                       │
    │  ┌───────────────▼──────────────┐  ┌────────▼──────────────────┐   │
    │  │ HotReloadableRegistryMixin   │  │ DependencyAwareRegistryMixin│  │
    │  │                              │  │                             │  │
    │  │ on_modified(event)           │  │ _validate_name(name)        │  │
    │  │ _scan_plugins()  ─ hook      │  │ _validate_version(ver)      │  │
    │  │ _load_plugin_file(path)─hook │  │ _validate_dependencies(...) │  │
    │  └──────────────────────────────┘  │ _satisfies_version(c, req)  │  │
    │                                    └─────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────────────┘

    Typical MRO for a hot-reloadable registry:

        class MyRegistry(
            HotReloadableRegistryMixin,
            FileSystemEventHandler,
            BasePluginRegistry,
        ):
            ...

    Typical MRO for a dependency-aware registry:

        class MyRegistry(DependencyAwareRegistryMixin, BasePluginRegistry):
            ...

Observability
-------------
When ``prometheus_client`` is available the base class exposes three
Prometheus metrics (created via :func:`shared.noop_metrics.safe_metric` so
they are silently no-ops when the library is absent):

``plugin_registry_operations_total``
    Counter labelled ``{registry, operation, status}`` — incremented by the
    default :meth:`BasePluginRegistry._record_operation` helper.

``plugin_registry_operation_duration_seconds``
    Histogram labelled ``{registry, operation}`` — optional timing helper.

``plugin_registry_active_count``
    Gauge labelled ``{registry}`` — tracks the number of currently
    registered plugins.

Subclasses can call ``self._record_operation(op, status)`` to emit these
metrics automatically.  None of the abstract methods emit metrics by default
because only concrete implementations know the precise semantics.

Thread Safety
-------------
:class:`BasePluginRegistry` exposes a single ``_lock`` (:class:`threading.Lock`)
that subclasses should acquire around all mutations::

    with self._lock:
        self._plugins[name] = plugin

Registries that require finer-grained locking (e.g. per-kind
:class:`threading.RLock` maps in ``arbiter_plugin_registry``) supplement
``_lock`` with their own additional locks.

Usage
-----
::

    from shared.plugin_registry_base import (
        BasePluginRegistry,
        HotReloadableRegistryMixin,
        DependencyAwareRegistryMixin,
    )
    from watchdog.events import FileSystemEventHandler

    # ── Minimal registry ──────────────────────────────────────────────
    class SimpleRegistry(BasePluginRegistry):
        def __init__(self):
            super().__init__()
            self._store: dict = {}

        def register(self, name, plugin, **kw):
            with self._lock:
                self._store[name] = plugin
            self._record_operation("register", "success")
            self.logger.info("Registered plugin: %s", name)

        def unregister(self, name):
            with self._lock:
                existed = self._store.pop(name, None) is not None
            self._record_operation("unregister", "success" if existed else "not_found")
            return existed

        def get(self, name):
            return self._store.get(name)

        def list_plugins(self):
            return dict(self._store)

    # ── Hot-reloadable registry ───────────────────────────────────────
    class HotRegistry(HotReloadableRegistryMixin, FileSystemEventHandler, BasePluginRegistry):
        def __init__(self, plugin_dir: str):
            super().__init__()
            self._store: dict = {}
            self.plugin_dir = plugin_dir

        def _scan_plugins(self):
            self.logger.info("Scanning %s for plugins…", self.plugin_dir)
            # … load plugins from self.plugin_dir …

        # register / unregister / get / list_plugins as above

    # ── Dependency-aware registry ─────────────────────────────────────
    class DepRegistry(DependencyAwareRegistryMixin, BasePluginRegistry):
        def register(self, name, plugin, version="1.0.0", deps=None, **kw):
            self._validate_name(name)
            self._validate_version(version)
            with self._lock:
                self._store[name] = plugin
            self.logger.info("Registered %s @ %s", name, version)

Industry Standards Applied
--------------------------
* **PEP 484** — full type annotations on all public and protected APIs.
* **PEP 3119** — ``abc.ABC`` / ``@abc.abstractmethod`` for interface
  enforcement where appropriate.
* **PEP 517 / 518** — zero mandatory runtime dependencies at import time;
  ``prometheus_client`` and ``opentelemetry-api`` are resolved lazily.
* **OpenTelemetry Specification** — tracer obtained via
  ``opentelemetry.trace.get_tracer(__name__)`` when available; falls back to
  :class:`shared.noop_tracing.NullTracer` transparently.
* **Prometheus Data Model** — metrics follow the ``snake_case`` naming
  convention and carry informative ``HELP`` strings.
* **Twelve-Factor App** — no configuration baked in; behaviour adapts to
  the installed environment automatically.
* **Google Python Style Guide** — ``Args / Returns / Raises`` sections in
  docstrings; one blank line before the section header.

See Also
--------
:mod:`shared.noop_metrics`  — thread-safe Prometheus no-op factory used by
    :meth:`BasePluginRegistry._build_metrics`.
:mod:`shared.noop_tracing`  — OpenTelemetry null-tracer used as a fallback.
:mod:`omnicore_engine.plugin_registry`  — primary omnicore registry that
    inherits from :class:`BasePluginRegistry`.
:mod:`self_fixing_engineer.arbiter.arbiter_plugin_registry`  — arbiter
    registry that inherits from both :class:`BasePluginRegistry` and
    :class:`DependencyAwareRegistryMixin`.
"""

from __future__ import annotations

import abc
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion
from packaging.version import parse as _parse_version

from shared.noop_metrics import NOOP, safe_metric
from shared.noop_tracing import NullTracer

__all__ = [
    "BasePluginRegistry",
    "HotReloadableRegistryMixin",
    "DependencyAwareRegistryMixin",
]

__version__: str = "1.0.0"

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy OpenTelemetry tracer — resolved once, on first use.
# Falls back to NullTracer when opentelemetry-api is not installed.
# ---------------------------------------------------------------------------

_tracer_lock: threading.Lock = threading.Lock()
_module_tracer: Optional[Any] = None  # real tracer or NullTracer, set lazily


def _get_tracer() -> Any:
    """Return the module-level OpenTelemetry tracer, resolving it on first call.

    Thread-safe; uses a double-checked lock pattern to avoid repeated
    ``importlib`` probes after the first resolution.

    Returns:
        An ``opentelemetry.trace.Tracer`` instance when ``opentelemetry-api``
        is installed, otherwise a :class:`shared.noop_tracing.NullTracer`.
    """
    global _module_tracer
    if _module_tracer is not None:
        return _module_tracer
    with _tracer_lock:
        if _module_tracer is not None:  # re-check after acquiring lock
            return _module_tracer
        try:
            from opentelemetry import trace as _otel_trace  # type: ignore[import]

            _module_tracer = _otel_trace.get_tracer(__name__, __version__)
            logger.debug("OpenTelemetry tracer initialised for %s", __name__)
        except ImportError:
            logger.debug(
                "opentelemetry-api not installed; using NullTracer for %s", __name__
            )
            _module_tracer = NullTracer()
    return _module_tracer


# ---------------------------------------------------------------------------
# Module-level Prometheus metrics — created via safe_metric so they are
# silent no-ops when prometheus_client is absent.
# ---------------------------------------------------------------------------

_metrics_lock: threading.Lock = threading.Lock()
_metrics_initialised: bool = False

# Metric objects — populated by _ensure_metrics() on first registry instantiation.
_ops_counter: Any = NOOP
_ops_histogram: Any = NOOP
_active_gauge: Any = NOOP


def _ensure_metrics() -> None:
    """Initialise Prometheus metrics idempotently (double-checked lock).

    Called from :meth:`BasePluginRegistry.__init__` so metrics are available
    from the first operation of any concrete registry subclass.
    """
    global _metrics_initialised, _ops_counter, _ops_histogram, _active_gauge
    if _metrics_initialised:
        return
    with _metrics_lock:
        if _metrics_initialised:
            return
        try:
            from prometheus_client import Counter, Gauge, Histogram  # type: ignore[import]
        except ImportError:
            Counter = Gauge = Histogram = None  # type: ignore[assignment]

        _ops_counter = safe_metric(
            Counter,
            "plugin_registry_operations_total",
            "Total plugin registry operations partitioned by registry class, "
            "operation type, and outcome.",
            labelnames=["registry", "operation", "status"],
        )
        _ops_histogram = safe_metric(
            Histogram,
            "plugin_registry_operation_duration_seconds",
            "Latency of plugin registry operations in seconds.",
            labelnames=["registry", "operation"],
            buckets=[0.0001, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
        )
        _active_gauge = safe_metric(
            Gauge,
            "plugin_registry_active_count",
            "Number of plugins currently registered per registry class.",
            labelnames=["registry"],
        )
        _metrics_initialised = True
        logger.debug("Plugin registry Prometheus metrics initialised.")


# ---------------------------------------------------------------------------
# Base registry ABC
# ---------------------------------------------------------------------------


class BasePluginRegistry(abc.ABC):
    """Abstract base class for all plugin registries on the Code Factory platform.

    Provides the canonical four-method interface for plugin lifecycle
    management together with production-grade cross-cutting concerns:

    * **Thread safety** — a per-instance :class:`threading.Lock` (``_lock``)
      serialises all mutations; subclasses must acquire it around writes.
    * **Structured logging** — a :class:`logging.Logger` scoped to the
      concrete subclass name (``self.logger``), ready for PII-redaction
      filters to be attached at the application layer.
    * **Observability** — optional Prometheus counters/histograms/gauges and
      an OpenTelemetry tracer, both resolved lazily with graceful degradation
      to no-ops when the optional libraries are absent.
    * **Introspection hook** — :meth:`_record_operation` provides a single
      call-site for emitting metrics so concrete subclasses stay DRY.

    Subclass contract
    -----------------
    Concrete subclasses **must** override the four interface methods below.
    Python's ABC mechanism does **not** enforce identical parameter signatures
    — implementations may extend the signature with domain-specific parameters
    (e.g. a ``kind: PlugInKind`` first argument) provided they satisfy at
    minimum the documented contract.

    +-----------------+------------------------------------+------------------+
    | Method          | Contract                           | Default          |
    +=================+====================================+==================+
    | ``register``    | Store *plugin* under *name*        | NotImplementedError|
    | ``unregister``  | Remove *name*; return bool         | NotImplementedError|
    | ``get``         | Retrieve by *name* or ``None``     | NotImplementedError|
    | ``list_plugins``| Return ``{name: plugin}`` mapping  | NotImplementedError|
    +-----------------+------------------------------------+------------------+

    Thread-safety contract
    ----------------------
    All writes to the plugin store MUST be performed under ``self._lock``::

        with self._lock:
            self._plugins[name] = plugin

    All reads from the plugin store that require atomicity (e.g. check-then-act)
    MUST also be performed under ``self._lock``.  Simple ``dict.get`` calls on
    immutable snapshots may be performed without the lock if the subclass
    documents that decision explicitly.

    Observability contract
    ----------------------
    Subclasses are encouraged to call ``self._record_operation(op, status)``
    after each mutation so that the shared Prometheus metrics stay accurate::

        self._record_operation("register", "success")
        self._record_operation("unregister", "not_found")

    Valid *op* values (by convention): ``"register"``, ``"unregister"``,
    ``"get"``, ``"list"``.
    Valid *status* values: ``"success"``, ``"not_found"``, ``"error"``,
    ``"duplicate"``, ``"disabled"``.

    Examples
    --------
    Minimal concrete implementation::

        class SimpleRegistry(BasePluginRegistry):
            def __init__(self):
                super().__init__()
                self._store: Dict[str, Any] = {}

            def register(self, name: str, plugin: Any, **kwargs: Any) -> None:
                with self._lock:
                    self._store[name] = plugin
                self._record_operation("register", "success")
                self.logger.info("Registered plugin: %s", name)

            def unregister(self, name: str) -> bool:
                with self._lock:
                    existed = self._store.pop(name, None) is not None
                status = "success" if existed else "not_found"
                self._record_operation("unregister", status)
                return existed

            def get(self, name: str) -> Optional[Any]:
                return self._store.get(name)

            def list_plugins(self) -> Dict[str, Any]:
                with self._lock:
                    return dict(self._store)
    """

    def __init__(self) -> None:
        """Initialise shared infrastructure for the registry.

        Sets up the per-instance lock and logger, then lazily initialises the
        shared Prometheus metrics (idempotent — safe to call from multiple
        subclass constructors).
        """
        self._lock: threading.Lock = threading.Lock()
        self.logger: logging.Logger = logging.getLogger(
            f"{self.__class__.__module__}.{self.__class__.__name__}"
        )
        # Lazily initialise Prometheus metrics shared across all subclasses.
        _ensure_metrics()

    # ------------------------------------------------------------------
    # Plugin registry interface — subclasses must override all four.
    # ------------------------------------------------------------------

    def register(self, name: str, plugin: Any, **kwargs: Any) -> None:
        """Register *plugin* under *name*.

        Args:
            name:    Unique identifier for the plugin within this registry.
            plugin:  The plugin object, class, or callable to register.
            **kwargs: Additional domain-specific metadata (version, author,
                      kind, dependencies, etc.) interpreted by subclasses.

        Raises:
            NotImplementedError: Always — concrete subclasses must override.

        Note:
            Implementations should acquire ``self._lock`` around all writes
            and call ``self._record_operation("register", status)`` on exit.
        """
        raise NotImplementedError(
            f"{type(self).__qualname__} must implement register()"
        )

    def unregister(self, name: str) -> bool:
        """Unregister the plugin identified by *name*.

        Args:
            name: The name under which the plugin was registered.

        Returns:
            ``True`` if the plugin was found and removed.
            ``False`` if no plugin with *name* exists.

        Raises:
            NotImplementedError: Always — concrete subclasses must override.

        Note:
            Implementations should acquire ``self._lock`` around all writes
            and call ``self._record_operation("unregister", status)`` on exit.
        """
        raise NotImplementedError(
            f"{type(self).__qualname__} must implement unregister()"
        )

    def get(self, name: str) -> Optional[Any]:
        """Retrieve the plugin registered under *name*.

        Args:
            name: The name under which the plugin was registered.

        Returns:
            The plugin object, or ``None`` if not found.

        Raises:
            NotImplementedError: Always — concrete subclasses must override.
        """
        raise NotImplementedError(
            f"{type(self).__qualname__} must implement get()"
        )

    def list_plugins(self) -> Dict[str, Any]:
        """Return a snapshot of all registered plugins.

        Returns:
            A ``{name: plugin}`` dictionary.  The concrete return type may be
            richer (e.g. ``{kind: [name, …]}`` for multi-kind registries).

        Raises:
            NotImplementedError: Always — concrete subclasses must override.
        """
        raise NotImplementedError(
            f"{type(self).__qualname__} must implement list_plugins()"
        )

    # ------------------------------------------------------------------
    # Observability helpers — call from concrete implementations.
    # ------------------------------------------------------------------

    def _record_operation(
        self,
        operation: str,
        status: str,
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Emit Prometheus metrics for a registry operation.

        Designed to be called from concrete implementations after each
        ``register`` / ``unregister`` / ``get`` / ``list`` operation.
        All metric calls are guarded against failures so that an absent
        ``prometheus_client`` or a prometheus deregistration race never
        propagates to the caller.

        Args:
            operation:        One of ``"register"``, ``"unregister"``,
                              ``"get"``, ``"list"``.
            status:           One of ``"success"``, ``"not_found"``,
                              ``"error"``, ``"duplicate"``, ``"disabled"``.
            duration_seconds: Optional wall-clock duration of the operation.
                              When provided, records an observation on the
                              ``plugin_registry_operation_duration_seconds``
                              histogram.
        """
        registry_name = type(self).__qualname__
        try:
            _ops_counter.labels(
                registry=registry_name,
                operation=operation,
                status=status,
            ).inc()
        except Exception:  # pragma: no cover
            pass

        if duration_seconds is not None:
            try:
                _ops_histogram.labels(
                    registry=registry_name,
                    operation=operation,
                ).observe(duration_seconds)
            except Exception:  # pragma: no cover
                pass

    def _update_active_count(self, delta: int) -> None:
        """Adjust the ``plugin_registry_active_count`` gauge by *delta*.

        Call with ``delta=+1`` after a successful registration and
        ``delta=-1`` after a successful unregistration.  Silently swallows
        metric errors.

        Args:
            delta: Integer offset to apply to the gauge (typically +1 or -1).
        """
        try:
            _active_gauge.labels(registry=type(self).__qualname__).inc(delta)
        except Exception:  # pragma: no cover
            pass

    def _get_tracer(self) -> Any:
        """Return the module-level OpenTelemetry tracer (lazy, thread-safe).

        Returns:
            A real ``opentelemetry.trace.Tracer`` when ``opentelemetry-api``
            is installed, otherwise a :class:`~shared.noop_tracing.NullTracer`.
        """
        return _get_tracer()


# ---------------------------------------------------------------------------
# Hot-reload mixin
# ---------------------------------------------------------------------------


class HotReloadableRegistryMixin:
    """Mixin that adds filesystem-watching hot-reload to a registry.

    Designed to be combined with ``watchdog.events.FileSystemEventHandler``
    **and** :class:`BasePluginRegistry` in the MRO.  Subclasses provide
    concrete implementations of :meth:`_scan_plugins` and (optionally)
    :meth:`_load_plugin_file`.

    Problem this solves
    -------------------
    Identical ``on_modified`` logic (guard on ``is_directory``, filter on
    ``.py`` extension, delegate to ``_scan_plugins``) was duplicated in:

    - ``generator/agents/deploy_agent/deploy_agent.py``
    - ``generator/agents/docgen_agent/docgen_response_validator.py``

    This mixin provides the single canonical implementation.

    Recommended MRO
    ---------------
    ::

        class MyRegistry(
            HotReloadableRegistryMixin,
            FileSystemEventHandler,     # watchdog
            BasePluginRegistry,
        ):
            def __init__(self, plugin_dir: str) -> None:
                super().__init__()      # calls all MRO __init__s

            def _scan_plugins(self) -> None:
                # scan self.plugin_dir, call self._load_plugin_file(path)
                ...

    ``HotReloadableRegistryMixin`` must appear **before**
    ``FileSystemEventHandler`` in the MRO so that its ``on_modified``
    override takes precedence over the watchdog default (which is a no-op).

    Thread safety
    -------------
    ``on_modified`` is called from the watchdog observer thread.  If
    ``_scan_plugins`` mutates shared state it must acquire the registry lock
    (``self._lock`` from :class:`BasePluginRegistry`)::

        def _scan_plugins(self) -> None:
            with self._lock:
                self._plugins.clear()
                # … reload …

    Examples
    --------
    ::

        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        from shared.plugin_registry_base import (
            HotReloadableRegistryMixin,
            BasePluginRegistry,
        )

        class HotRegistry(
            HotReloadableRegistryMixin,
            FileSystemEventHandler,
            BasePluginRegistry,
        ):
            def __init__(self, plugin_dir: str) -> None:
                super().__init__()
                self._store: dict = {}
                self.plugin_dir = plugin_dir
                self._observer = Observer()
                self._observer.schedule(self, plugin_dir, recursive=True)
                self._observer.start()

            def _scan_plugins(self) -> None:
                import glob
                for path in glob.glob(f"{self.plugin_dir}/**/*.py", recursive=True):
                    self._load_plugin_file(path)

            def _load_plugin_file(self, plugin_file: str) -> None:
                # load and register plugin from file
                ...

            # Implement register / unregister / get / list_plugins …
    """

    def on_modified(self, event: Any) -> None:
        """Handle a watchdog filesystem-modification event.

        Ignores directory events and non-Python file modifications.  For all
        other ``.py`` file change events, delegates to :meth:`_scan_plugins`
        to perform the full plugin directory rescan.

        This single implementation replaces the identical handlers that
        previously existed independently in ``deploy_agent.py`` and
        ``docgen_response_validator.py``.

        Args:
            event: A ``watchdog.events.FileSystemEvent`` instance.  The
                   method accesses only ``event.is_directory`` and
                   ``event.src_path`` so it is compatible with any watchdog
                   event type.

        Note:
            Called from the watchdog observer *background thread*.
            ``_scan_plugins`` implementations must be thread-safe.
        """
        if getattr(event, "is_directory", False):
            return
        src_path: str = getattr(event, "src_path", "")
        if not src_path.endswith(".py"):
            return
        self._scan_plugins()

    def _scan_plugins(self) -> None:
        """Scan the plugin directory and (re-)load all plugins.

        Called automatically by :meth:`on_modified` on any ``.py`` file
        change, and may also be called at startup.

        Subclasses must provide a concrete implementation.  The typical
        pattern is to iterate over Python files in the plugin directory and
        call :meth:`_load_plugin_file` for each one.

        Raises:
            NotImplementedError: If the subclass does not provide an
                implementation.

        Note:
            Must be thread-safe (called from the watchdog observer thread).
            Acquire ``self._lock`` around any mutations to shared plugin state.
        """
        raise NotImplementedError(
            f"{type(self).__qualname__} must implement _scan_plugins()"
        )

    def _load_plugin_file(self, plugin_file: str) -> None:
        """Load a single plugin file and register any plugins found within it.

        Called by :meth:`_scan_plugins` for each candidate ``.py`` file.
        The typical implementation uses ``importlib.util.spec_from_file_location``
        to load the module, then inspects its members for subclasses of the
        domain's plugin base class and registers each one.

        Args:
            plugin_file: Absolute or relative path to the Python file to load.

        Raises:
            NotImplementedError: If the subclass relies on per-file loading
                but does not provide an implementation.
        """
        raise NotImplementedError(
            f"{type(self).__qualname__} must implement _load_plugin_file()"
        )


# ---------------------------------------------------------------------------
# Dependency-aware mixin
# ---------------------------------------------------------------------------


class DependencyAwareRegistryMixin:
    """Mixin that provides plugin-name, version, and dependency validation.

    Problem this solves
    -------------------
    ``_validate_name``, ``_validate_version``, and ``_satisfies_version``
    were duplicated — with minor wording differences — in:

    - ``self_fixing_engineer/arbiter/arbiter_plugin_registry.py``
    - ``self_fixing_engineer/arbiter/plugin_config.py``

    This mixin provides the single canonical implementation.

    Concrete helpers (no override required)
    ---------------------------------------
    :meth:`_validate_name`
        Rejects empty strings and names containing characters outside
        ``[a-zA-Z0-9_-]``.

    :meth:`_validate_version`
        Rejects version strings that are not valid PEP 440 three-component
        semantic versions (``MAJOR.MINOR.PATCH``).

    :meth:`_satisfies_version`
        Checks whether a *current* version string satisfies a PEP 440
        version-specifier expression such as ``">=1.2.0,<2.0.0"``.

    Abstract hook (must override if using dependency graphs)
    --------------------------------------------------------
    :meth:`_validate_dependencies`
        Full dependency-graph validation (existence checks, version
        constraints, circular-dependency detection) is deliberately left
        abstract because the implementation requires knowledge of the host
        registry's plugin-kind type and graph library.  The default
        implementation raises :exc:`NotImplementedError`.

        **Override example** (arbiter uses ``networkx.DiGraph``)::

            def _validate_dependencies(
                self,
                kind: PlugInKind,
                name: str,
                dependencies: List[Dict[str, str]],
            ) -> None:
                dep_graph = DiGraph()
                for dep in dependencies:
                    dep_node = f"{dep['kind']}:{dep['name']}"
                    if has_path(dep_graph, dep_node, f"{kind.value}:{name}"):
                        raise PluginDependencyError("Circular dependency")
                    existing = self.get_metadata(PlugInKind(dep['kind']), dep['name'])
                    if not existing:
                        raise PluginDependencyError(f"Dependency not found: {dep_node}")
                    if not self._satisfies_version(existing.version, dep.get('version', '>=0.0.0')):
                        raise PluginDependencyError("Version constraint not satisfied")

    Examples
    --------
    ::

        from shared.plugin_registry_base import DependencyAwareRegistryMixin

        class MyRegistry(DependencyAwareRegistryMixin, BasePluginRegistry):

            def register(self, name, plugin, version="1.0.0", deps=None, **kw):
                self._validate_name(name)
                self._validate_version(version)
                if deps:
                    self._validate_dependencies(None, name, deps)
                with self._lock:
                    self._store[name] = plugin

    Industry Standards Applied
    --------------------------
    * **PEP 440** — version parsing and specifier evaluation via the
      ``packaging`` library, which is the canonical Python packaging standard.
    * **Semantic Versioning** — ``_validate_version`` enforces exactly three
      release components (``MAJOR.MINOR.PATCH``) per semver.org.
    """

    def _validate_name(self, name: str) -> None:
        """Assert that *name* is a well-formed plugin identifier.

        A valid plugin name must be non-empty and contain only alphanumeric
        characters, underscores, or hyphens (``^[a-zA-Z0-9_-]+$``).  This
        constraint prevents injection attacks through plugin names and ensures
        safe use of names as filesystem paths, JSON keys, and metric labels.

        Args:
            name: The proposed plugin name to validate.

        Raises:
            ValueError: If *name* is empty or contains characters outside the
                allowed set, with a human-readable description of the rule.

        Examples:
            >>> reg._validate_name("my-plugin_v2")   # OK
            >>> reg._validate_name("")               # raises ValueError
            >>> reg._validate_name("bad name!")      # raises ValueError
        """
        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError(
                f"Invalid plugin name {name!r}: must be non-empty and contain "
                "only alphanumeric characters, underscores, or hyphens "
                "(pattern: ^[a-zA-Z0-9_-]+$)."
            )

    def _validate_version(self, version_str: str) -> None:
        """Assert that *version_str* is a three-component semantic version.

        Uses the ``packaging`` library (PEP 440) to parse the version and
        additionally enforces that it has exactly three release components
        (``MAJOR.MINOR.PATCH``) to match the platform's semantic-versioning
        convention.

        Args:
            version_str: The version string to validate (e.g. ``"1.2.3"``).

        Raises:
            ValueError: If *version_str* is not a valid PEP 440 version or
                does not have exactly three numeric components.

        Examples:
            >>> reg._validate_version("1.2.3")    # OK
            >>> reg._validate_version("1.0")      # raises ValueError (only 2 components)
            >>> reg._validate_version("not-a-ver")# raises ValueError
        """
        try:
            parsed = _parse_version(version_str)
            if len(parsed.release) != 3:
                raise InvalidVersion(
                    f"Version {version_str!r} must have exactly three "
                    "components (MAJOR.MINOR.PATCH)."
                )
        except InvalidVersion as exc:
            raise ValueError(
                f"Invalid version {version_str!r}: must follow semantic "
                "versioning (e.g. '1.2.3').  Original error: {exc}"
            ) from exc

    def _validate_dependencies(
        self,
        kind: Any,
        name: str,
        dependencies: List[Dict[str, str]],
    ) -> None:
        """Validate a plugin's declared dependency list.

        The base implementation raises :exc:`NotImplementedError` because
        full dependency resolution requires:

        * Knowledge of the registry's plugin-kind type (e.g. ``PlugInKind``
          enum vs. plain ``str``).
        * A directed-graph library (e.g. ``networkx``) for circular-dependency
          detection.
        * Access to the registry's metadata store (``self._meta``) to check
          whether each declared dependency actually exists and satisfies its
          version constraint.

        Registries that need dependency validation **must** provide a concrete
        override.  See the class-level docstring for a reference implementation.

        Args:
            kind:         The plugin-kind value for the plugin being registered.
                          Type is ``Any`` to accommodate both ``str`` and
                          domain-specific enum types.
            name:         The name of the plugin being registered.
            dependencies: Sequence of dependency descriptors.  Each descriptor
                          is a ``dict`` with at minimum ``"kind"`` and ``"name"``
                          keys, and an optional ``"version"`` specifier string.

        Raises:
            NotImplementedError: Always in the base implementation.  Subclasses
                that use this mixin without overriding this method will receive
                this error at runtime.
        """
        raise NotImplementedError(
            f"{type(self).__qualname__} uses DependencyAwareRegistryMixin but "
            "has not implemented _validate_dependencies().  Provide a concrete "
            "override, or do not pass dependencies to register()."
        )

    def _satisfies_version(self, current: str, required: str) -> bool:
        """Return ``True`` if *current* satisfies the *required* PEP 440 specifier.

        Delegates to :class:`packaging.specifiers.SpecifierSet` for standards-
        compliant version comparison.  Accepts any PEP 440 specifier expression,
        including compound specifiers such as ``">=1.0.0,<2.0.0"``.

        Args:
            current:  The installed/registered version string (e.g. ``"1.3.0"``).
            required: A PEP 440 version specifier expression
                      (e.g. ``">=1.2.0"``, ``"~=1.2"``, ``"!=1.2.3"``).

        Returns:
            ``True`` if *current* is within the range defined by *required*.

        Examples:
            >>> reg._satisfies_version("1.3.0", ">=1.2.0")     # True
            >>> reg._satisfies_version("1.1.0", ">=1.2.0")     # False
            >>> reg._satisfies_version("2.0.0", ">=1.0.0,<2.0.0")  # False

        Note:
            Pre-release versions are excluded from specifier matches by
            default (PEP 440 / ``packaging`` behaviour).  Pass
            ``prereleases=True`` explicitly if pre-releases must be accepted.
        """
        return SpecifierSet(required).contains(_parse_version(current))
