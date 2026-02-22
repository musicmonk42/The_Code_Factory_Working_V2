# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
AST-Based Endpoint Extraction for FastAPI Applications

Walks the Python AST to find FastAPI/Starlette route decorators,
resolves APIRouter prefixes, and traces include_router() calls to
compute fully-qualified endpoint paths.

Industry Standards Applied:
- Visitor Pattern: Uses ast.NodeVisitor for clean tree traversal
- Single Responsibility Principle: Extraction logic isolated in one class
- Defensive Programming: Graceful handling of unparseable source
- Type Safety: Full type hints for better IDE support and runtime checking
"""

import ast
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# HTTP methods recognised by FastAPI / Starlette
_HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
)

# Names commonly used for FastAPI / APIRouter instances
_APP_NAMES: frozenset[str] = frozenset({"app", "application"})
_ROUTER_NAMES: frozenset[str] = frozenset({"router", "api_router"})


def _normalize_path(prefix: str, path: str) -> str:
    """
    Join a router prefix and a route path, ensuring exactly one leading
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
    """
    combined = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
    if not combined.startswith("/"):
        combined = f"/{combined}"
    # Collapse any double slashes that may remain
    while "//" in combined:
        combined = combined.replace("//", "/")
    return combined


def _resolve_string_node(node: ast.expr) -> Optional[str]:
    """
    Attempt to resolve an AST expression to a plain string.

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


class ASTEndpointExtractor:
    """
    Extract FastAPI endpoints from Python source code using AST analysis.

    This class walks the abstract syntax tree of a Python module to discover
    route registrations made via decorators (``@app.get``, ``@router.post``,
    etc.), resolves ``APIRouter(prefix=...)`` prefixes, and traces
    ``include_router(router, prefix=...)`` calls to produce fully-qualified
    endpoint paths.

    Industry Standards Applied:
    - Visitor Pattern for clean, extensible tree traversal
    - Defensive Programming with graceful error recovery
    - Immutable internal state per extraction run

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

    def __init__(self) -> None:
        # Per-run state — reset at the start of each extraction
        self._router_prefixes: Dict[str, str] = {}
        self._include_router_prefixes: Dict[str, str] = {}
        self._endpoints: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_source(
        self, source: str, filename: str = "<string>"
    ) -> List[Dict[str, Any]]:
        """
        Parse *source* as Python and return discovered FastAPI endpoints.

        Args:
            source: Python source code to analyse.
            filename: Optional filename used in error messages and the
                returned ``line_number`` context.

        Returns:
            A list of endpoint dicts, each containing:
            ``method``, ``path``, ``function_name``, ``line_number``.

        Examples:
            >>> ASTEndpointExtractor().extract_from_source(
            ...     '@app.get("/items")\\ndef list_items(): ...'
            ... )
            [{'method': 'GET', 'path': '/items', 'function_name': 'list_items', 'line_number': 2}]
        """
        self._reset()

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

    def extract_from_file(self, filepath: str) -> List[Dict[str, Any]]:
        """
        Read a Python file from disk and return discovered FastAPI endpoints.

        Args:
            filepath: Path to a ``.py`` file.

        Returns:
            A list of endpoint dicts (same schema as
            :meth:`extract_from_source`).

        Raises:
            FileNotFoundError: If *filepath* does not exist.
            PermissionError: If *filepath* cannot be read.
        """
        logger.debug("Reading source from %s", filepath)
        with open(filepath, "r", encoding="utf-8") as fh:
            source = fh.read()
        return self.extract_from_source(source, filename=filepath)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        """Clear per-run state."""
        self._router_prefixes = {}
        self._include_router_prefixes = {}
        self._endpoints = []

    # -- Pass 1: assignments & include_router ---------------------------

    def _collect_assignments_and_includes(self, tree: ast.Module) -> None:
        """
        Walk top-level statements to populate ``_router_prefixes`` and
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
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        """Check every decorator on *node* for route registrations."""
        for decorator in node.decorator_list:
            endpoint = self._parse_route_decorator(decorator, node)
            if endpoint is not None:
                self._endpoints.append(endpoint)

    def _parse_route_decorator(
        self,
        decorator: ast.expr,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> Optional[Dict[str, Any]]:
        """
        If *decorator* is a FastAPI route decorator, return an endpoint dict;
        otherwise return ``None``.
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

        return {
            "method": method_name.upper(),
            "path": full_path,
            "function_name": func_node.name,
            "line_number": func_node.lineno,
        }

    # -- Utility extractors ---------------------------------------------

    @staticmethod
    def _extract_route_path(call: Optional[ast.Call]) -> str:
        """
        Return the route path from a decorator call node.

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
