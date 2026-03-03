# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/utils/project_endpoint_analyzer.py
"""
ProjectEndpointAnalyzer — Cross-File FastAPI Router Prefix Resolution Engine

This module provides a production-grade, observability-aware static analysis
engine that resolves fully-qualified FastAPI endpoint paths across an entire
generated project, correctly handling ``include_router(router, prefix=...)``
patterns regardless of how the routers are organised across files.

Problem Solved
--------------
When FastAPI code uses ``app.include_router(router, prefix="/api/v1/auth")``,
the route decorators in the router file contain only the *sub-path* (e.g.
``@router.post('/login')``).  A per-file extractor therefore yields ``/login``,
not the fully-qualified ``/api/v1/auth/login`` that the spec requires.

:class:`ProjectEndpointAnalyzer` performs a two-phase cross-file scan:

1. **Prefix scan** — parses ``main.py`` / ``app/main.py`` to extract all
   ``include_router()`` calls and their ``prefix=`` values, and resolves
   each router variable to its source module via import statement analysis.

2. **Endpoint resolution** — for every router file, applies the correct
   prefix to each endpoint, returning fully-qualified paths.

Supported Import Patterns
--------------------------
All three import styles are resolved automatically::

    # 1. Aliased import
    from app.routers.auth import router as auth_router

    # 2. Direct import
    from app.routes import auth_router, patients_router

    # 3. Multi-import with parentheses
    from app.routes import (
        auth_router,
        patients_router,
        encounters_router,
    )

Router File Patterns
--------------------
Both common organisation patterns are fully supported:

**Multi-file** (one router per file)::

    app/routers/auth.py      → auth_router      → /api/v1/auth
    app/routers/patients.py  → patients_router  → /api/v1/patients

**Single-file** (all routers in one file, e.g. as LLMs often generate)::

    app/routes.py → auth_router, patients_router, encounters_router
                  → /api/v1/auth, /api/v1/patients, /api/v1/encounters

Architecture
------------
::

    ┌──────────────────────────┐
    │  generated_files dict    │  ← {path: source_code}
    └────────────┬─────────────┘
                 │
                 ▼
    ┌──────────────────────────┐
    │  Phase 1: main.py scan   │
    │  • include_router() map  │  ← router_var → prefix
    │  • import statement map  │  ← router_var → module_stem
    └────────────┬─────────────┘
                 │
                 ▼
    ┌──────────────────────────┐
    │  Phase 2: router files   │
    │  • multi-file: whole-    │  ← one prefix per file
    │    file prefix applied   │
    │  • single-file: per-     │  ← AST-walked; per-decorator
    │    decorator prefix      │    var lookup
    └────────────┬─────────────┘
                 │
                 ▼
    ┌──────────────────────────┐
    │  ResolvedEndpoint[]      │  ← Pydantic-validated results
    └──────────────────────────┘

Observability
-------------
- **Prometheus metrics**: resolution count and duration exported as
  ``project_endpoint_analysis_total`` and
  ``project_endpoint_analysis_duration_seconds``.
- **OpenTelemetry spans**: ``ProjectEndpointAnalyzer.get_endpoints`` emits a
  span with ``router_count``, ``file_count``, and ``endpoint_count``
  attributes, enabling distributed-trace correlation in codegen pipelines.
- **Structured logging**: all phases emit ``DEBUG`` / ``WARNING`` messages
  with file-name context for operational debugging.

Industry Standards Applied
--------------------------
- **Single Responsibility Principle** — analysis logic isolated in one class.
- **Visitor Pattern** (GoF) via ``ast.walk`` for clean AST traversal.
- **Defensive Programming** — graceful handling of unparseable source files.
- **Type Safety** — full PEP 484 type hints with ``from __future__ import
  annotations``.
- **Pydantic Validation** — ``ResolvedEndpoint`` model enforces output schema.
- **Observability** — RED metrics (Rate, Errors, Duration) via Prometheus.
- **SOC 2 Type II** / **ISO 27001 A.12.4.1**: structured audit-ready logging.
- **Input Size Guards** — configurable maximum source size prevents
  denial-of-service via excessively large generated files.
"""

from __future__ import annotations

import ast
import logging
import re
import time
from collections import Counter
from typing import Any, Dict, FrozenSet, List, Optional

# ---------------------------------------------------------------------------
# Pydantic — required for structured result validation
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field

from generator.agents.metrics_utils import get_or_create_metric

# ---------------------------------------------------------------------------
# Prometheus — conditional import with no-op stubs
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter as PCounter, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    PROMETHEUS_AVAILABLE = False
    PCounter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]


class _NoopMetric:
    """Lightweight no-op stub that silently accepts any Prometheus-style call."""

    def labels(self, *args: Any, **kwargs: Any) -> "_NoopMetric":
        return self

    def inc(self, *args: Any, **kwargs: Any) -> None:
        pass

    def observe(self, *args: Any, **kwargs: Any) -> None:
        pass


_NOOP = _NoopMetric()

_analysis_total: Any = _NOOP
_analysis_duration: Any = _NOOP

if PROMETHEUS_AVAILABLE:
    _analysis_total = get_or_create_metric(
        PCounter,
        "project_endpoint_analysis_total",
        "Total ProjectEndpointAnalyzer.get_endpoints() invocations",
        ["status"],
    )
    _analysis_duration = get_or_create_metric(
        Histogram,
        "project_endpoint_analysis_duration_seconds",
        "Duration of cross-file endpoint resolution operations in seconds",
    )

# ---------------------------------------------------------------------------
# OpenTelemetry — conditional import with NullTracer fallback
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace as _otel_trace

    tracer = _otel_trace.get_tracer(__name__)
    TRACING_AVAILABLE = True
except ImportError:  # pragma: no cover
    TRACING_AVAILABLE = False

    class _NullContext:
        """No-op context manager returned when OpenTelemetry is unavailable."""

        def __enter__(self) -> "_NullContext":
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        def set_attribute(self, key: str, value: Any) -> None:
            pass

    class _NullTracer:
        """No-op tracer mirroring the OpenTelemetry ``Tracer`` interface."""

        def start_as_current_span(
            self, name: str, *args: Any, **kwargs: Any
        ) -> "_NullContext":
            return _NullContext()

    tracer = _NullTracer()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & configuration
# ---------------------------------------------------------------------------

#: Default maximum source file size (bytes) accepted for AST parsing.
#: Files exceeding this limit are skipped gracefully with a warning.
DEFAULT_MAX_SOURCE_SIZE: int = 10 * 1024 * 1024  # 10 MB

#: HTTP methods recognised as FastAPI / Starlette route decorator names.
_HTTP_METHODS: FrozenSet[str] = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
)

# Regex: include_router(var_name, ..., prefix="/some/path", ...)
# Uses DOTALL so the call may span multiple lines.
_INCLUDE_ROUTER_RE = re.compile(
    r'include_router\s*\(\s*(\w+)\s*,\s*(?:[^)]*?\s)?prefix\s*=\s*["\']([^"\']+)["\']',
    re.DOTALL,
)

# Regex: aliased import — from some.module import name as alias
_IMPORT_ALIAS_RE = re.compile(
    r'from\s+(\S+)\s+import\s+\w+\s+as\s+(\w+)'
)

# Regex: direct / multi-name imports (with or without parentheses).
# Group 1+2: parenthesised  — from X import (a, b,)
# Group 3+4: flat           — from X import a, b
_IMPORT_DIRECT_RE = re.compile(
    r'from\s+(\S+)\s+import\s+\(([^)]+)\)|from\s+(\S+)\s+import\s+([^\n(]+)',
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------


class ResolvedEndpoint(BaseModel):
    """Validated representation of a fully-qualified FastAPI endpoint.

    Attributes:
        method: The HTTP method (upper-case, e.g. ``"POST"``).
        path: The fully-qualified route path including the ``include_router``
            prefix (e.g. ``"/api/v1/auth/login"``).

    Examples:
        >>> ep = ResolvedEndpoint(method="POST", path="/api/v1/auth/login")
        >>> ep.model_dump()
        {'method': 'POST', 'path': '/api/v1/auth/login'}
    """

    method: str = Field(..., description="HTTP method (upper-case)")
    path: str = Field(..., description="Fully-qualified route path")


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _module_stem(module_path: str) -> str:
    """Return the last dotted component of a dotted module name or file path.

    This is used to map both ``import from`` declarations
    (``app.routers.auth``) and file paths (``app/routers/auth.py``) to a
    common key for prefix look-up.

    Args:
        module_path: A dotted module path or a ``/``-separated file path
            (with or without a ``.py`` suffix).

    Returns:
        The last component of the module name, lower-cased.

    Examples:
        >>> _module_stem("app.routers.auth")
        'auth'
        >>> _module_stem("app/routes.py")
        'routes'
        >>> _module_stem("main")
        'main'
    """
    normalised = module_path.replace("/", ".").removesuffix(".py")
    return normalised.split(".")[-1]


def _extract_router_prefixes(main_content: str) -> Dict[str, str]:
    """Scan *main_content* for ``include_router`` calls and return a
    mapping from router variable name to its ``prefix=`` value.

    Only calls that carry an explicit string ``prefix=`` keyword argument
    are captured; calls without a prefix are intentionally ignored because
    they do not alter the endpoint paths.

    Args:
        main_content: Source code of the application entry-point file.

    Returns:
        ``{router_var_name: prefix_string}`` — for example
        ``{"auth_router": "/api/v1/auth"}``.

    Examples:
        >>> src = 'app.include_router(auth_router, prefix="/api/v1/auth")'
        >>> _extract_router_prefixes(src)
        {'auth_router': '/api/v1/auth'}
    """
    return {var: prefix for var, prefix in _INCLUDE_ROUTER_RE.findall(main_content)}


def _build_var_to_stem(main_content: str) -> Dict[str, str]:
    """Parse import statements in *main_content* and return a mapping from
    each imported router variable name to its source module stem.

    Three import patterns are handled:

    1. **Aliased** — ``from app.routers.auth import router as auth_router``
    2. **Direct** — ``from app.routes import auth_router, patients_router``
    3. **Parenthesised** — ``from app.routes import (\\n    auth_router,\\n)``

    For direct and parenthesised imports the *first* occurrence wins
    (``setdefault``), so that aliased imports (which are matched first and
    stored explicitly) are not overwritten.

    Args:
        main_content: Source code of the application entry-point file.

    Returns:
        ``{router_var_name: module_stem}`` — for example
        ``{"auth_router": "routes", "patients_router": "routes"}``.
    """
    var_to_stem: Dict[str, str] = {}

    # Pattern 1 — aliased: from app.routers.auth import router as auth_router
    for module, alias in _IMPORT_ALIAS_RE.findall(main_content):
        var_to_stem[alias] = _module_stem(module)

    # Pattern 2 & 3 — direct imports (parenthesised and flat)
    for m_paren, names_paren, m_direct, names_direct in _IMPORT_DIRECT_RE.findall(
        main_content
    ):
        if m_paren and names_paren:
            module, names_raw = m_paren, names_paren
        elif m_direct and names_direct:
            module, names_raw = m_direct, names_direct
        else:
            continue
        stem = _module_stem(module)
        for entry in re.split(r'[,\s]+', names_raw):
            entry = entry.strip().rstrip(",")
            if not entry or entry == "as":
                continue
            # Only store simple identifiers; skip keywords and aliased parts
            if re.match(r'^\w+$', entry):
                var_to_stem.setdefault(entry, stem)

    return var_to_stem


def _endpoints_from_ast_single_file(
    content: str,
    filename: str,
    var_to_prefix: Dict[str, str],
    max_source_size: int = DEFAULT_MAX_SOURCE_SIZE,
) -> List[Dict[str, str]]:
    """Walk the AST of *content* and return endpoints for the single-file
    router pattern, where multiple router variables with different prefixes
    co-exist in the same source file.

    Each ``function`` / ``async function`` decorated with
    ``<router_var>.<http_method>('/path')`` is collected.  The prefix for
    the router variable is looked up in *var_to_prefix* and prepended to
    the sub-path.  Functions decorated by variables not present in
    *var_to_prefix* are skipped silently.

    Args:
        content: Python source code of the router file.
        filename: File path used in log messages (not read from disk).
        var_to_prefix: Mapping of router variable name → ``include_router``
            prefix.  Only variables present in this map are considered.
        max_source_size: Maximum accepted source size in bytes.  Files
            exceeding this limit are skipped with a warning.

    Returns:
        List of ``{"method": str, "path": str}`` dicts with
        fully-qualified paths, each validated through :class:`ResolvedEndpoint`.
    """
    if max_source_size > 0 and len(content) > max_source_size:
        logger.warning(
            "Skipping single-file AST analysis of %s: source size %d bytes "
            "exceeds limit %d bytes",
            filename,
            len(content),
            max_source_size,
        )
        return []

    endpoints: List[Dict[str, str]] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        logger.debug(
            "Skipping single-file AST analysis of %s (SyntaxError): %s",
            filename,
            exc,
        )
        return endpoints

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
            ):
                continue
            dec_var = dec.func.value.id
            http_method = dec.func.attr.upper()
            if http_method not in _HTTP_METHODS:
                continue
            prefix = var_to_prefix.get(dec_var)
            if prefix is None:
                continue
            # Extract the path string literal from the first positional argument.
            if not dec.args:
                continue
            first_arg = dec.args[0]
            if not (
                isinstance(first_arg, ast.Constant)
                and isinstance(first_arg.value, str)
            ):
                continue
            path_arg: str = first_arg.value
            full_path = prefix.rstrip("/") + "/" + path_arg.lstrip("/")
            validated = ResolvedEndpoint(method=http_method, path=full_path)
            endpoints.append(validated.model_dump())

    logger.debug(
        "Single-file AST analysis of %s: resolved %d endpoint(s)",
        filename,
        len(endpoints),
    )
    return endpoints


# ---------------------------------------------------------------------------
# Main analyser class
# ---------------------------------------------------------------------------


class ProjectEndpointAnalyzer:
    """Resolve fully-qualified FastAPI endpoint paths across an entire project.

    Given a ``{relative_path: source_code}`` mapping (as produced by the
    code-generation pipeline), this class performs a two-phase cross-file
    analysis:

    **Phase 1 — main.py scan**
        Locates ``main.py`` or ``app/main.py`` and extracts:

        * All ``include_router(var, prefix=...)`` calls → ``router_var: prefix``
          mapping.
        * All import statements → ``router_var: module_stem`` mapping.

    **Phase 2 — router file resolution**
        For each Python file in the project, applies the correct prefix:

        * **Multi-file pattern** (one router per file): a single prefix applies
          to all endpoints in the file; uses
          :func:`generator.main.provenance.extract_endpoints_from_code`.
        * **Single-file pattern** (multiple routers in one file): uses
          :func:`_endpoints_from_ast_single_file` to walk the AST and assign
          the correct per-decorator prefix.

    Industry Standards Applied:
        - **Single Responsibility Principle** — cross-file analysis isolated.
        - **Defensive Programming** — unparseable files are skipped with
          structured log warnings rather than raising exceptions.
        - **Pydantic Validation** — every result is validated through
          :class:`ResolvedEndpoint` before being returned.
        - **Observability** — RED metrics via Prometheus and span attributes
          via OpenTelemetry.
        - **Input Size Guards** — configurable maximum source size.

    Args:
        generated_files: ``{relative_path: source_code}`` mapping of all
            files produced by the code-generation pipeline.
        max_source_size: Maximum source-file size in bytes for AST parsing.
            Defaults to :data:`DEFAULT_MAX_SOURCE_SIZE` (10 MB).

    Examples:
        >>> files = {
        ...     "app/main.py": (
        ...         "from app.routes import auth_router\\n"
        ...         "app.include_router(auth_router, prefix='/api/v1/auth')\\n"
        ...     ),
        ...     "app/routes.py": (
        ...         "auth_router = APIRouter()\\n"
        ...         "@auth_router.post('/login')\\ndef login(): pass\\n"
        ...     ),
        ... }
        >>> analyzer = ProjectEndpointAnalyzer(files)
        >>> analyzer.get_endpoints()
        [{'method': 'POST', 'path': '/api/v1/auth/login'}]
    """

    def __init__(
        self,
        generated_files: Dict[str, str],
        max_source_size: int = DEFAULT_MAX_SOURCE_SIZE,
    ) -> None:
        self._files = generated_files
        self._max_source_size = max_source_size

        # Phase 1: parse main.py
        self._main_content: str = self._find_main_content()
        self._router_prefix_map: Dict[str, str] = _extract_router_prefixes(
            self._main_content
        )
        self._var_to_stem: Dict[str, str] = _build_var_to_stem(self._main_content)

        # Build stem → prefix map for the multi-file pattern.
        # CRITICAL: only include stems with exactly ONE router variable mapped to
        # them.  When multiple router variables share the same stem (the
        # single-file pattern, e.g. app/routes.py), the stem is deliberately
        # excluded here so the per-decorator AST branch handles it correctly
        # instead of applying one prefix to the entire file.
        _stem_count: Counter[str] = Counter(
            stem
            for var, stem in self._var_to_stem.items()
            if var in self._router_prefix_map
        )
        self._stem_to_prefix: Dict[str, str] = {
            stem: self._router_prefix_map[var]
            for var, stem in self._var_to_stem.items()
            if var in self._router_prefix_map and _stem_count[stem] == 1
        }

        # Build var → prefix map for the single-file pattern (direct look-up
        # during per-decorator AST analysis).
        self._var_to_prefix: Dict[str, str] = {
            var: self._router_prefix_map[var]
            for var in self._var_to_stem
            if var in self._router_prefix_map
        }

        logger.debug(
            "ProjectEndpointAnalyzer initialised: %d router prefix(es), "
            "%d stem-to-prefix mapping(s), %d var-to-prefix mapping(s)",
            len(self._router_prefix_map),
            len(self._stem_to_prefix),
            len(self._var_to_prefix),
        )

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ProjectEndpointAnalyzer("
            f"files={len(self._files)}, "
            f"routers={len(self._router_prefix_map)})"
        )

    def __str__(self) -> str:
        return (
            f"<ProjectEndpointAnalyzer "
            f"files={len(self._files)} "
            f"routers={len(self._router_prefix_map)}>"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_endpoints(self) -> List[Dict[str, str]]:
        """Return all fully-qualified endpoints found across all router files.

        Performs the Phase 2 resolution pass: iterates every Python file in
        the project, determines whether it contributes endpoints under the
        multi-file or single-file pattern, and collects the fully-qualified
        ``{"method", "path"}`` dicts.

        Returns:
            A list of ``{"method": str, "path": str}`` dicts.  Each entry is
            validated through :class:`ResolvedEndpoint` before being
            returned.  Returns an empty list when no ``include_router()``
            calls with ``prefix=`` were found in main.py.

        Examples:
            >>> analyzer = ProjectEndpointAnalyzer({})
            >>> analyzer.get_endpoints()
            []
        """
        with tracer.start_as_current_span(
            "ProjectEndpointAnalyzer.get_endpoints"
        ) as span:
            span.set_attribute("file_count", len(self._files))
            span.set_attribute("router_count", len(self._router_prefix_map))

            start = time.monotonic()
            try:
                result = self._resolve_all_endpoints()
                elapsed = time.monotonic() - start
                _analysis_total.labels(status="success").inc()
                _analysis_duration.observe(elapsed)
                span.set_attribute("endpoint_count", len(result))
                logger.debug(
                    "ProjectEndpointAnalyzer.get_endpoints resolved %d "
                    "endpoint(s) in %.3fs",
                    len(result),
                    elapsed,
                )
                return result
            except Exception as exc:  # pragma: no cover
                elapsed = time.monotonic() - start
                _analysis_total.labels(status="error").inc()
                _analysis_duration.observe(elapsed)
                logger.error(
                    "ProjectEndpointAnalyzer.get_endpoints failed after "
                    "%.3fs: %s",
                    elapsed,
                    exc,
                    exc_info=True,
                )
                raise

    def get_router_prefix_map(self) -> Dict[str, str]:
        """Return the ``{router_var: prefix}`` map extracted from main.py.

        Intended for diagnostic use (e.g. in structured log output).

        Returns:
            A shallow copy of the internal ``{router_var: prefix}`` dict.
        """
        return dict(self._router_prefix_map)

    def get_stem_to_prefix_map(self) -> Dict[str, str]:
        """Return the ``{module_stem: prefix}`` map (multi-file pattern only).

        Stems that are shared by multiple router variables (single-file
        pattern) are intentionally absent from this map.

        Returns:
            A shallow copy of the internal ``{stem: prefix}`` dict.
        """
        return dict(self._stem_to_prefix)

    def get_var_to_prefix_map(self) -> Dict[str, str]:
        """Return the ``{router_var: prefix}`` map used for single-file analysis.

        Returns:
            A shallow copy of the internal ``{var: prefix}`` dict.
        """
        return dict(self._var_to_prefix)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_main_content(self) -> str:
        """Return the source code of main.py / app/main.py, or empty string.

        Prefers ``app/main.py`` over ``main.py`` to match the standard
        FastAPI project layout.

        Returns:
            Source code string, or ``""`` when neither candidate is present.
        """
        for candidate in ("app/main.py", "main.py"):
            if candidate in self._files:
                logger.debug("ProjectEndpointAnalyzer: using %s as entry-point", candidate)
                return self._files[candidate]
        logger.debug(
            "ProjectEndpointAnalyzer: no main.py / app/main.py found in %d file(s)",
            len(self._files),
        )
        return ""

    def _resolve_all_endpoints(self) -> List[Dict[str, str]]:
        """Core resolution logic: iterate all Python files and collect
        fully-qualified endpoints.

        Returns:
            List of ``{"method": str, "path": str}`` dicts.
        """
        if not self._router_prefix_map:
            logger.debug(
                "ProjectEndpointAnalyzer: no include_router() calls with "
                "prefix= found — skipping resolution"
            )
            return []

        # Lazy import to avoid a circular top-level dependency; provenance
        # imports ast_endpoint_extractor and this module is a sibling utility.
        from generator.main.provenance import (  # noqa: PLC0415
            extract_endpoints_from_code,
        )

        endpoints: List[Dict[str, str]] = []
        for filename, content in self._files.items():
            if not filename.endswith(".py"):
                continue
            file_endpoints = self._endpoints_for_file(
                filename, content, extract_endpoints_from_code
            )
            endpoints.extend(file_endpoints)

        return endpoints

    def _endpoints_for_file(
        self,
        filename: str,
        content: str,
        extract_fn: Any,
    ) -> List[Dict[str, str]]:
        """Return fully-qualified endpoints contributed by a single *filename*.

        Dispatches to the correct resolution strategy:

        * **Multi-file** — when the file's module stem maps to exactly one
          prefix in :attr:`_stem_to_prefix`.  Applies that prefix to every
          endpoint extracted by *extract_fn* (the standard per-file
          extractor).
        * **Single-file** — when the file's module stem does not have a
          one-to-one prefix mapping (i.e. multiple router variables from the
          same file).  Delegates to :func:`_endpoints_from_ast_single_file`
          for per-decorator, per-variable prefix application.

        Files that match neither pattern are skipped.

        Args:
            filename: Relative file path (used for module stem derivation).
            content: Source code of the file.
            extract_fn: Callable with the same signature as
                ``extract_endpoints_from_code`` — used for multi-file
                resolution so the caller can inject mocks in tests.

        Returns:
            List of ``{"method": str, "path": str}`` dicts.
        """
        file_stem = _module_stem(filename)
        file_prefix = self._stem_to_prefix.get(file_stem, "")

        if file_prefix:
            # Multi-file pattern: one prefix for the whole file.
            if (
                self._max_source_size > 0
                and len(content) > self._max_source_size
            ):
                logger.warning(
                    "Skipping multi-file resolution of %s: source size "
                    "%d bytes exceeds limit %d bytes",
                    filename,
                    len(content),
                    self._max_source_size,
                )
                return []
            raw = extract_fn(content, filename)
            result: List[Dict[str, str]] = []
            for ep in raw:
                raw_path = ep.get("path", "")
                full_path = file_prefix.rstrip("/") + "/" + raw_path.lstrip("/")
                validated = ResolvedEndpoint(method=ep["method"], path=full_path)
                result.append(validated.model_dump())
            logger.debug(
                "Multi-file resolution of %s (prefix=%r): %d endpoint(s)",
                filename,
                file_prefix,
                len(result),
            )
            return result

        # Single-file pattern: check whether any relevant router vars are
        # sourced from this file.
        local_routers = [
            var
            for var, stem in self._var_to_stem.items()
            if stem == file_stem and var in self._var_to_prefix
        ]
        if not local_routers:
            return []

        local_var_to_prefix = {var: self._var_to_prefix[var] for var in local_routers}
        return _endpoints_from_ast_single_file(
            content,
            filename,
            local_var_to_prefix,
            max_source_size=self._max_source_size,
        )
