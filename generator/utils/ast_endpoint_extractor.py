# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
ast_endpoint_extractor.py — AST-Based Endpoint Extraction for FastAPI Applications

This module provides a production-grade, observability-aware static analysis
engine that walks the Python AST to discover FastAPI / Starlette route
registrations.  It resolves ``APIRouter`` prefixes, traces
``include_router()`` call chains, and produces fully-qualified endpoint
metadata suitable for code-generation pipelines and OpenAPI enrichment.

Key Features
------------
- **Visitor-Pattern AST Traversal:** Two-pass analysis (assignments → routes)
  ensures router prefixes are resolved before endpoint paths are computed.
- **Pydantic-Validated Results:** Each discovered endpoint is validated
  through an ``EndpointInfo`` model before being returned, guaranteeing
  schema conformance across downstream consumers.
- **Prometheus Metrics:** Extraction count and duration are exported as
  ``ast_endpoint_extraction_total`` and
  ``ast_endpoint_extraction_duration_seconds`` for SRE dashboards.
- **OpenTelemetry Tracing:** Every public method emits a span, enabling
  distributed-trace correlation in code-generation workflows.
- **Security Hardening:** Input size limits prevent denial-of-service via
  excessively large source files, and path-traversal validation protects
  the ``extract_from_file`` entry-point.
- **Graceful Degradation:** Prometheus and OpenTelemetry are conditionally
  imported; the module operates fully without either dependency installed.

Industry Standards Applied
--------------------------
- **Visitor Pattern** (GoF) for clean, extensible tree traversal.
- **Single Responsibility Principle** — extraction logic isolated in one class.
- **Defensive Programming** — graceful handling of unparseable source.
- **Type Safety** — full type hints for IDE support and runtime checking.
- **Observability** — RED metrics (Rate, Errors, Duration) via Prometheus.
- **Structured Validation** — Pydantic ``BaseModel`` for output contracts.

Architecture
------------
::

    ┌───────────────────┐
    │  Python source    │
    └────────┬──────────┘
             │  ast.parse()
             ▼
    ┌───────────────────┐
    │  Pass 1: collect  │  ← APIRouter / include_router prefixes
    └────────┬──────────┘
             ▼
    ┌───────────────────┐
    │  Pass 2: routes   │  ← decorated function definitions
    └────────┬──────────┘
             ▼
    ┌───────────────────┐
    │  EndpointInfo[]   │  ← validated Pydantic models → List[dict]
    └───────────────────┘
"""

from __future__ import annotations

import ast
import logging
import os
import time
from typing import Any, Dict, FrozenSet, List, Optional, Set, Union

# ---------------------------------------------------------------------------
# Pydantic — required for structured result validation
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field
from generator.agents.metrics_utils import get_or_create_metric

# ---------------------------------------------------------------------------
# Prometheus — conditional import with no-op stubs
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]

# Idempotent metric registration
_extraction_total: Any = None
_extraction_duration: Any = None

if PROMETHEUS_AVAILABLE:
    _extraction_total = get_or_create_metric(
        Counter,
        "ast_endpoint_extraction_total",
        "Total AST endpoint extraction operations",
        ["status"],
    )
    _extraction_duration = get_or_create_metric(
        Histogram,
        "ast_endpoint_extraction_duration_seconds",
        "Duration of AST endpoint extraction operations in seconds",
    )
class _NoopMetric:
    """Lightweight no-op stub that silently accepts any Prometheus-style call."""

    def labels(self, *args: Any, **kwargs: Any) -> "_NoopMetric":
        return self

    def inc(self, *args: Any, **kwargs: Any) -> None:
        pass

    def observe(self, *args: Any, **kwargs: Any) -> None:
        pass


_NOOP = _NoopMetric()

if _extraction_total is None:
    _extraction_total = _NOOP
if _extraction_duration is None:
    _extraction_duration = _NOOP

# ---------------------------------------------------------------------------
# OpenTelemetry — conditional import with NullTracer fallback
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False

    class NullContext:
        """No-op context manager returned when OpenTelemetry is unavailable."""

        def __enter__(self) -> "NullContext":
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

        def set_attribute(self, key: str, value: Any) -> None:
            pass

    class NullTracer:
        """No-op tracer that mirrors the OpenTelemetry ``Tracer`` interface."""

        def start_as_current_span(
            self, name: str, *args: Any, **kwargs: Any
        ) -> NullContext:
            return NullContext()

    tracer = NullTracer()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & configuration
# ---------------------------------------------------------------------------

#: Default maximum source size (bytes) accepted for parsing.
DEFAULT_MAX_SOURCE_SIZE: int = 10 * 1024 * 1024  # 10 MB

# HTTP methods recognised by FastAPI / Starlette
_HTTP_METHODS: FrozenSet[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
)

# Names commonly used for FastAPI / APIRouter instances
_APP_NAMES: FrozenSet[str] = frozenset({"app", "application"})
_ROUTER_NAMES: FrozenSet[str] = frozenset({"router", "api_router"})

# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------


class EndpointInfo(BaseModel):
    """Structured representation of a discovered FastAPI endpoint.

    This model validates and normalises the metadata extracted from route
    decorators.  The extractor constructs ``EndpointInfo`` instances
    internally and converts them to plain dicts for backward-compatible
    return values.

    Attributes:
        method: The HTTP method (upper-case, e.g. ``"GET"``).
        path: The fully-qualified route path (e.g. ``"/api/v1/users"``).
        function_name: The Python function or coroutine name.
        line_number: The 1-based line number in the source file.

    Examples:
        >>> info = EndpointInfo(
        ...     method="GET",
        ...     path="/health",
        ...     function_name="health_check",
        ...     line_number=42,
        ... )
        >>> info.model_dump()
        {'method': 'GET', 'path': '/health', 'function_name': 'health_check', 'line_number': 42}
    """

    method: str = Field(..., description="HTTP method (upper-case)")
    path: str = Field(..., description="Fully-qualified route path")
    function_name: str = Field(..., description="Handler function name")
    line_number: int = Field(..., ge=1, description="Source line number")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _normalize_path(prefix: str, path: str) -> str:
    """Join a router prefix and a route path, ensuring exactly one leading
    slash and no duplicate slashes.

    Args:
        prefix: The router or include_router prefix (may be empty).
        path: The route path from the decorator.

    Returns:
        A normalised absolute path string.

    Examples:
        >>> _normalize_path("/api/v1", "/users")
        '/api/v1/users'
        >>> _normalize_path("/api/v1/", "/users")
        '/api/v1/users'
        >>> _normalize_path("", "/users")
        '/users'
        >>> _normalize_path("", "")
        '/'
    """
    if not prefix and not path:
        return "/"
    combined = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
    if not combined.startswith("/"):
        combined = f"/{combined}"
    # Collapse any double slashes that may remain
    while "//" in combined:
        combined = combined.replace("//", "/")
    return combined


def _resolve_string_node(node: ast.expr) -> Optional[str]:
    """Attempt to resolve an AST expression to a plain string.

    Handles:
    - String constants  (``ast.Constant``)
    - String concatenation via ``BinOp`` with ``Add``
    - f-strings are *not* resolved (return ``None``)

    Args:
        node: The AST expression node.

    Returns:
        The resolved string, or ``None`` if it cannot be statically determined.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_string_node(node.left)
        right = _resolve_string_node(node.right)
        if left is not None and right is not None:
            return left + right

    return None


def _validate_filepath(filepath: str) -> None:
    """Validate that *filepath* does not contain path-traversal sequences.

    Args:
        filepath: The file path to validate.

    Raises:
        ValueError: If the path contains ``..`` components or null bytes.
    """
    if "\x00" in filepath:
        raise ValueError("Filepath must not contain null bytes")
    normalized = os.path.normpath(filepath)
    if ".." in normalized.split(os.sep):
        raise ValueError(
            f"Path traversal detected in filepath: {filepath!r}"
        )


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------


class ASTEndpointExtractor:
    """Extract FastAPI endpoints from Python source code using AST analysis.

    This class walks the abstract syntax tree of a Python module to discover
    route registrations made via decorators (``@app.get``, ``@router.post``,
    etc.), resolves ``APIRouter(prefix=...)`` prefixes, and traces
    ``include_router(router, prefix=...)`` calls to produce fully-qualified
    endpoint paths.

    The extractor is **stateless across calls** — internal bookkeeping is
    reset at the start of each extraction so a single instance can be reused
    safely.

    Industry Standards Applied:
        - **Visitor Pattern** for clean, extensible tree traversal.
        - **Defensive Programming** with graceful error recovery.
        - **Pydantic Validation** for output schema enforcement.
        - **Observability** via Prometheus metrics and OpenTelemetry spans.

    Args:
        max_source_size: Maximum source-code size in bytes that will be
            accepted for parsing.  Defaults to ``DEFAULT_MAX_SOURCE_SIZE``
            (10 MB).  Set to ``0`` to disable the limit.

    Examples:
        >>> extractor = ASTEndpointExtractor()
        >>> endpoints = extractor.extract_from_source('''
        ... from fastapi import FastAPI
        ... app = FastAPI()
        ... @app.get("/health")
        ... def health():
        ...     return {"status": "ok"}
        ... ''')
        >>> endpoints[0]["method"]
        'GET'
        >>> endpoints[0]["path"]
        '/health'
    """

    def __init__(self, max_source_size: int = DEFAULT_MAX_SOURCE_SIZE) -> None:
        self._max_source_size: int = max_source_size
        # Per-run state — reset at the start of each extraction
        self._router_prefixes: Dict[str, str] = {}
        self._include_router_prefixes: Dict[str, str] = {}
        self._endpoints: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ASTEndpointExtractor(max_source_size={self._max_source_size!r})"
        )

    def __str__(self) -> str:
        return (
            f"<ASTEndpointExtractor max_source_size="
            f"{self._max_source_size} bytes>"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_source(
        self, source: str, filename: str = "<string>"
    ) -> List[Dict[str, Any]]:
        """Parse *source* as Python and return discovered FastAPI endpoints.

        The method validates input size, parses the source into an AST, and
        performs a two-pass analysis to resolve router prefixes before
        extracting route metadata.  Each result is validated through the
        ``EndpointInfo`` Pydantic model before being returned as a plain
        dict for backward compatibility.

        Args:
            source: Python source code to analyse.
            filename: Optional filename used in error messages and the
                returned ``line_number`` context.

        Returns:
            A list of endpoint dicts, each containing keys:
            ``method``, ``path``, ``function_name``, ``line_number``.

        Raises:
            ValueError: If *source* exceeds the configured maximum size.

        Examples:
            >>> ASTEndpointExtractor().extract_from_source(
            ...     '@app.get("/items")\\ndef list_items(): ...'
            ... )
            [{'method': 'GET', 'path': '/items', 'function_name': 'list_items', 'line_number': 2}]
        """
        with tracer.start_as_current_span(
            "ASTEndpointExtractor.extract_from_source"
        ) as span:
            span.set_attribute("filename", filename)
            span.set_attribute("source_size", len(source))

            start = time.monotonic()
            try:
                result = self._do_extract_from_source(source, filename)
                elapsed = time.monotonic() - start
                _extraction_total.labels(status="success").inc()
                _extraction_duration.observe(elapsed)
                span.set_attribute("endpoint_count", len(result))
                return result
            except Exception:
                elapsed = time.monotonic() - start
                _extraction_total.labels(status="error").inc()
                _extraction_duration.observe(elapsed)
                raise

    def extract_from_file(self, filepath: str) -> List[Dict[str, Any]]:
        """Read a Python file from disk and return discovered FastAPI endpoints.

        The filepath is validated against path-traversal attacks before
        reading.  The file contents are then delegated to
        :meth:`extract_from_source` for AST analysis.

        Args:
            filepath: Path to a ``.py`` file.

        Returns:
            A list of endpoint dicts (same schema as
            :meth:`extract_from_source`).

        Raises:
            FileNotFoundError: If *filepath* does not exist.
            PermissionError: If *filepath* cannot be read.
            ValueError: If *filepath* contains path-traversal sequences or
                the file contents exceed the configured maximum size.
        """
        with tracer.start_as_current_span(
            "ASTEndpointExtractor.extract_from_file"
        ) as span:
            span.set_attribute("filepath", filepath)

            _validate_filepath(filepath)
            logger.debug("Reading source from %s", filepath)
            with open(filepath, "r", encoding="utf-8") as fh:
                source = fh.read()
            return self.extract_from_source(source, filename=filepath)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_extract_from_source(
        self, source: str, filename: str
    ) -> List[Dict[str, Any]]:
        """Core extraction logic, separated from observability wrapper."""
        self._reset()

        # --- Input validation ---
        if self._max_source_size > 0 and len(source) > self._max_source_size:
            raise ValueError(
                f"Source size ({len(source)} bytes) exceeds maximum "
                f"allowed size ({self._max_source_size} bytes) for "
                f"{filename!r}"
            )

        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as exc:
            logger.warning(
                "Failed to parse %s: %s. Returning empty endpoint list.",
                filename,
                exc,
            )
            return []

        # Two-pass approach:
        # 1. Collect variable assignments (FastAPI / APIRouter instances and
        #    include_router calls) so prefixes are known.
        # 2. Walk decorated functions to extract routes.
        self._collect_assignments_and_includes(tree)
        self._collect_routes(tree)

        logger.debug(
            "Extracted %d endpoint(s) from %s", len(self._endpoints), filename
        )
        return list(self._endpoints)

    def _reset(self) -> None:
        """Clear per-run state."""
        self._router_prefixes = {}
        self._include_router_prefixes = {}
        self._endpoints = []

    # -- Pass 1: assignments & include_router ---------------------------

    def _collect_assignments_and_includes(self, tree: ast.Module) -> None:
        """Walk top-level statements to populate ``_router_prefixes`` and
        ``_include_router_prefixes``.
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                self._handle_assignment(node)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                self._handle_call_expr(node.value)

    def _handle_assignment(self, node: ast.Assign) -> None:
        """Detect ``router = APIRouter(prefix=...)`` patterns."""
        if not isinstance(node.value, ast.Call):
            return

        call = node.value
        func_name = self._call_name(call)
        if func_name not in ("APIRouter", "FastAPI"):
            return

        # Determine the variable name(s) being assigned
        for target in node.targets:
            var_name = self._target_name(target)
            if var_name is None:
                continue

            prefix = self._extract_keyword_string(call, "prefix") or ""
            self._router_prefixes[var_name] = prefix
            logger.debug(
                "Found %s assignment: %s (prefix=%r)", func_name, var_name, prefix
            )

    def _handle_call_expr(self, call: ast.Call) -> None:
        """Detect ``app.include_router(router, prefix=...)`` patterns."""
        if not isinstance(call.func, ast.Attribute):
            return
        if call.func.attr != "include_router":
            return

        # First positional arg should be the router variable
        if not call.args:
            return

        router_name = self._target_name(call.args[0])
        if router_name is None:
            return

        include_prefix = self._extract_keyword_string(call, "prefix") or ""
        # Merge with router's own prefix if known
        router_own_prefix = self._router_prefixes.get(router_name, "")
        combined = _normalize_path(include_prefix, router_own_prefix)
        self._include_router_prefixes[router_name] = combined

        logger.debug(
            "Found include_router for %s (combined prefix=%r)",
            router_name,
            combined,
        )

    # -- Pass 2: decorated functions ------------------------------------

    def _collect_routes(self, tree: ast.Module) -> None:
        """Walk decorated function definitions and extract route metadata."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._process_function(node)

    def _process_function(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    ) -> None:
        """Check every decorator on *node* for route registrations."""
        for decorator in node.decorator_list:
            endpoint = self._parse_route_decorator(decorator, node)
            if endpoint is not None:
                self._endpoints.append(endpoint)

    def _parse_route_decorator(
        self,
        decorator: ast.expr,
        func_node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> Optional[Dict[str, Any]]:
        """If *decorator* is a FastAPI route decorator, return an endpoint
        dict; otherwise return ``None``.

        The result is validated through ``EndpointInfo`` before being
        converted to a plain dict.
        """
        # Decorators like @app.get("/path") are Call nodes whose func is
        # an Attribute node.
        if isinstance(decorator, ast.Call):
            call = decorator
            if not isinstance(call.func, ast.Attribute):
                return None
            method_name = call.func.attr
            obj_node = call.func.value
        elif isinstance(decorator, ast.Attribute):
            # Bare decorator without call, e.g. @app.get (unlikely but safe)
            method_name = decorator.attr
            obj_node = decorator.value
            call = None
        else:
            return None

        if method_name not in _HTTP_METHODS:
            return None

        var_name = self._target_name(obj_node)
        if var_name is None:
            return None

        # Ensure the variable is a known FastAPI/router instance (or one of
        # the conventional names)
        known_names: Set[str] = (
            _APP_NAMES | _ROUTER_NAMES | set(self._router_prefixes.keys())
        )
        if var_name not in known_names:
            return None

        # Extract path from first positional argument or `path` keyword
        path = self._extract_route_path(call)

        # Compute full path by prepending any applicable prefix
        prefix = self._include_router_prefixes.get(
            var_name, self._router_prefixes.get(var_name, "")
        )
        full_path = _normalize_path(prefix, path)

        info = EndpointInfo(
            method=method_name.upper(),
            path=full_path,
            function_name=func_node.name,
            line_number=func_node.lineno,
        )
        return info.model_dump()

    # -- Utility extractors ---------------------------------------------

    @staticmethod
    def _extract_route_path(call: Optional[ast.Call]) -> str:
        """Return the route path from a decorator call node.

        Checks the first positional argument and the ``path`` keyword.
        Falls back to ``"/"`` when the path cannot be determined.
        """
        if call is None:
            return "/"

        # First positional argument
        if call.args:
            resolved = _resolve_string_node(call.args[0])
            if resolved is not None:
                return resolved

        # ``path`` keyword argument
        path_str = ASTEndpointExtractor._extract_keyword_string(call, "path")
        if path_str is not None:
            return path_str

        return "/"

    @staticmethod
    def _extract_keyword_string(
        call: ast.Call, keyword: str
    ) -> Optional[str]:
        """Return the string value of *keyword* in *call*, or ``None``."""
        for kw in call.keywords:
            if kw.arg == keyword:
                return _resolve_string_node(kw.value)
        return None

    @staticmethod
    def _call_name(call: ast.Call) -> Optional[str]:
        """Return the simple name of a Call's function (``Name`` nodes only)."""
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    @staticmethod
    def _target_name(node: ast.expr) -> Optional[str]:
        """Return the identifier string if *node* is a simple ``Name``."""
        if isinstance(node, ast.Name):
            return node.id
        return None
