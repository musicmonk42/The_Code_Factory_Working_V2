# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for generator/utils/project_endpoint_analyzer.py

Validates the cross-file FastAPI router-prefix resolution engine at every
layer of the stack:

* **Unit tests** for each pure helper function (``_module_stem``,
  ``_extract_router_prefixes``, ``_build_var_to_stem``,
  ``_endpoints_from_ast_single_file``).
* **Integration tests** for :class:`ProjectEndpointAnalyzer` covering:
  - Multi-file router pattern (one router per file)
  - Single-file router pattern (all routers in one file)
  - All three import styles (aliased, direct, parenthesised)
  - Edge cases (no include_router, syntax errors, missing main.py, deep
    module paths, overlapping variable names)
* **Observability tests** verifying that the Prometheus metrics counters
  are incremented correctly on success.
* **Public-API contract tests** for ``get_router_prefix_map()``,
  ``get_stem_to_prefix_map()``, and ``get_var_to_prefix_map()``.
* **Repr / str tests** for operational tooling.

Test data design
----------------
All test fixtures use minimal but representative FastAPI source snippets
that mirror real generated code patterns.  Router sources are kept
self-contained so tests have no external file-system or network
dependencies.
"""

from __future__ import annotations

from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

from generator.utils.project_endpoint_analyzer import (
    DEFAULT_MAX_SOURCE_SIZE,
    ProjectEndpointAnalyzer,
    ResolvedEndpoint,
    _build_var_to_stem,
    _endpoints_from_ast_single_file,
    _extract_router_prefixes,
    _module_stem,
)


# ---------------------------------------------------------------------------
# Shared test-fixture helpers
# ---------------------------------------------------------------------------


def _auth_routes_source() -> str:
    """Minimal single-file source with two router variables."""
    return (
        "from fastapi import APIRouter\n"
        "auth_router = APIRouter()\n"
        "patients_router = APIRouter()\n\n"
        "@auth_router.post('/login')\n"
        "def login(): pass\n\n"
        "@patients_router.get('/')\n"
        "def list_patients(): pass\n"
    )


def _multi_file_main(*, auth_prefix: str = "/api/v1/auth") -> str:
    """main.py that uses the aliased import + include_router pattern."""
    return (
        "from fastapi import FastAPI\n"
        "from app.routers.auth import router as auth_router\n"
        "app = FastAPI()\n"
        f'app.include_router(auth_router, prefix="{auth_prefix}")\n'
    )


def _single_file_main(
    *,
    auth_prefix: str = "/api/v1/auth",
    patients_prefix: str = "/api/v1/patients",
) -> str:
    """main.py that imports two routers from a single routes.py."""
    return (
        "from fastapi import FastAPI\n"
        "from app.routes import auth_router, patients_router\n"
        "app = FastAPI()\n"
        f'app.include_router(auth_router, prefix="{auth_prefix}")\n'
        f'app.include_router(patients_router, prefix="{patients_prefix}")\n'
    )


# ---------------------------------------------------------------------------
# Unit tests: _module_stem
# ---------------------------------------------------------------------------


class TestModuleStem:
    """_module_stem() converts module paths and file paths to a stem."""

    def test_dotted_module_path(self):
        """Returns the final component of a dotted module path."""
        assert _module_stem("app.routers.auth") == "auth"

    def test_file_path_with_py_suffix(self):
        """Strips the .py extension from a file path."""
        assert _module_stem("app/routes.py") == "routes"

    def test_single_component_module(self):
        """Single-component modules are returned unchanged."""
        assert _module_stem("main") == "main"

    def test_deeply_nested_module(self):
        """Only the final component is returned for deeply nested paths."""
        assert _module_stem("a.b.c.d.e.f") == "f"

    def test_file_path_without_suffix(self):
        """File paths without .py extension still work."""
        assert _module_stem("app/routers/auth") == "auth"


# ---------------------------------------------------------------------------
# Unit tests: _extract_router_prefixes
# ---------------------------------------------------------------------------


class TestExtractRouterPrefixes:
    """_extract_router_prefixes() parses include_router() calls from source."""

    def test_single_router(self):
        """Extracts one router variable and its prefix."""
        content = 'app.include_router(auth_router, prefix="/api/v1/auth")\n'
        assert _extract_router_prefixes(content) == {"auth_router": "/api/v1/auth"}

    def test_multiple_routers(self):
        """Extracts multiple router variables from the same source."""
        content = (
            'app.include_router(auth_router, prefix="/api/v1/auth")\n'
            'app.include_router(patients_router, prefix="/api/v1/patients")\n'
        )
        assert _extract_router_prefixes(content) == {
            "auth_router": "/api/v1/auth",
            "patients_router": "/api/v1/patients",
        }

    def test_no_include_router_returns_empty_dict(self):
        """Returns empty dict when there are no include_router() calls."""
        assert _extract_router_prefixes("app = FastAPI()\n") == {}

    def test_include_router_without_prefix_is_ignored(self):
        """include_router() calls without prefix= are not captured."""
        content = "app.include_router(auth_router)\n"
        assert _extract_router_prefixes(content) == {}

    def test_include_router_with_surrounding_kwargs(self):
        """Prefix= is found even when other keyword arguments are present."""
        content = (
            'app.include_router(orders_router, tags=["orders"], '
            'prefix="/api/v1/orders", dependencies=[])\n'
        )
        assert _extract_router_prefixes(content) == {"orders_router": "/api/v1/orders"}

    def test_multiline_include_router_call(self):
        """Multi-line include_router() calls are handled via DOTALL flag."""
        content = (
            "app.include_router(\n"
            "    auth_router,\n"
            '    prefix="/api/v1/auth",\n'
            ")\n"
        )
        assert _extract_router_prefixes(content) == {"auth_router": "/api/v1/auth"}

    def test_single_quoted_prefix(self):
        """Single-quoted prefix strings are extracted correctly."""
        content = "app.include_router(auth_router, prefix='/api/v1/auth')\n"
        assert _extract_router_prefixes(content) == {"auth_router": "/api/v1/auth"}


# ---------------------------------------------------------------------------
# Unit tests: _build_var_to_stem
# ---------------------------------------------------------------------------


class TestBuildVarToStem:
    """_build_var_to_stem() resolves router variables to their module stems."""

    def test_aliased_import(self):
        """from X import Y as Z → Z maps to stem of X."""
        content = "from app.routers.auth import router as auth_router\n"
        result, full_mod = _build_var_to_stem(content)
        assert result.get("auth_router") == "auth"
        assert full_mod.get("auth_router") == "app.routers.auth"

    def test_direct_import_single_name(self):
        """from X import Y → Y maps to stem of X."""
        content = "from app.routes import auth_router\n"
        result, full_mod = _build_var_to_stem(content)
        assert result.get("auth_router") == "routes"
        assert full_mod.get("auth_router") == "app.routes"

    def test_direct_import_multiple_names(self):
        """from X import Y, Z → both Y and Z map to stem of X."""
        content = "from app.routes import auth_router, patients_router\n"
        result, full_mod = _build_var_to_stem(content)
        assert result.get("auth_router") == "routes"
        assert result.get("patients_router") == "routes"

    def test_parenthesised_import(self):
        """Multi-import with parentheses maps all names to the module stem."""
        content = (
            "from app.routes import (\n"
            "    auth_router,\n"
            "    patients_router,\n"
            "    encounters_router,\n"
            ")\n"
        )
        result, _ = _build_var_to_stem(content)
        assert result.get("auth_router") == "routes"
        assert result.get("patients_router") == "routes"
        assert result.get("encounters_router") == "routes"

    def test_aliased_import_not_overwritten_by_direct(self):
        """Aliased imports take precedence; direct imports use setdefault."""
        content = (
            "from app.routers.auth import router as auth_router\n"
            "from app.routes import auth_router\n"  # should not overwrite
        )
        result, full_mod = _build_var_to_stem(content)
        # The aliased import (processed first) should win.
        assert result.get("auth_router") == "auth"
        assert full_mod.get("auth_router") == "app.routers.auth"

    def test_mixed_import_styles(self):
        """Aliased and direct imports co-exist in the same file."""
        content = (
            "from app.routers.auth import router as auth_router\n"
            "from app.routes import patients_router\n"
        )
        result, _ = _build_var_to_stem(content)
        assert result.get("auth_router") == "auth"
        assert result.get("patients_router") == "routes"

    def test_deep_module_path(self):
        """Stems are derived from deeply nested module paths."""
        content = "from a.b.c.d.routers.auth import router as auth_router\n"
        result, full_mod = _build_var_to_stem(content)
        assert result.get("auth_router") == "auth"
        assert full_mod.get("auth_router") == "a.b.c.d.routers.auth"


# ---------------------------------------------------------------------------
# Unit tests: _endpoints_from_ast_single_file
# ---------------------------------------------------------------------------


class TestEndpointsFromAstSingleFile:
    """_endpoints_from_ast_single_file() extracts per-decorator endpoints."""

    def test_single_router_variable(self):
        """Decorates a function with one router variable → correct path."""
        source = (
            "from fastapi import APIRouter\n"
            "auth_router = APIRouter()\n\n"
            "@auth_router.post('/login')\n"
            "def login(): pass\n"
        )
        result = _endpoints_from_ast_single_file(
            source, "app/routes.py", {"auth_router": "/api/v1/auth"}
        )
        assert len(result) == 1
        assert result[0] == {"method": "POST", "path": "/api/v1/auth/login"}

    def test_two_router_variables_separate_prefixes(self):
        """Two routers in one file each receive their own prefix."""
        source = _auth_routes_source()
        var_to_prefix = {
            "auth_router": "/api/v1/auth",
            "patients_router": "/api/v1/patients",
        }
        result = _endpoints_from_ast_single_file(source, "app/routes.py", var_to_prefix)
        methods_paths = {(r["method"], r["path"]) for r in result}
        assert ("POST", "/api/v1/auth/login") in methods_paths
        assert ("GET", "/api/v1/patients/") in methods_paths

    def test_unknown_router_variable_skipped(self):
        """Decorators using a variable not in var_to_prefix are ignored."""
        source = (
            "@unknown_router.get('/items')\n"
            "def list_items(): pass\n"
        )
        result = _endpoints_from_ast_single_file(source, "routes.py", {})
        assert result == []

    def test_non_http_method_decorator_skipped(self):
        """Decorators whose attribute name is not an HTTP method are skipped."""
        source = (
            "auth_router = APIRouter()\n\n"
            "@auth_router.middleware('http')\n"
            "def mw(): pass\n"
        )
        result = _endpoints_from_ast_single_file(
            source, "routes.py", {"auth_router": "/api/v1/auth"}
        )
        assert result == []

    def test_syntax_error_returns_empty_list(self):
        """A file with a syntax error is skipped gracefully."""
        result = _endpoints_from_ast_single_file(
            "def broken(:\n", "routes.py", {"auth_router": "/api/v1/auth"}
        )
        assert result == []

    def test_source_size_limit_enforced(self):
        """Sources exceeding max_source_size are skipped with a warning."""
        source = "auth_router = APIRouter()\n" * 10
        result = _endpoints_from_ast_single_file(
            source, "routes.py", {"auth_router": "/api/v1/auth"}, max_source_size=5
        )
        assert result == []

    def test_async_function_decorator_resolved(self):
        """Async route handlers are resolved in the same way as sync ones."""
        source = (
            "auth_router = APIRouter()\n\n"
            "@auth_router.get('/me')\n"
            "async def get_me(): pass\n"
        )
        result = _endpoints_from_ast_single_file(
            source, "routes.py", {"auth_router": "/api/v1/auth"}
        )
        assert result == [{"method": "GET", "path": "/api/v1/auth/me"}]

    def test_result_dicts_pass_pydantic_validation(self):
        """Every returned dict satisfies the ResolvedEndpoint schema."""
        source = (
            "auth_router = APIRouter()\n\n"
            "@auth_router.post('/login')\n"
            "def login(): pass\n"
        )
        result = _endpoints_from_ast_single_file(
            source, "routes.py", {"auth_router": "/api/v1/auth"}
        )
        for item in result:
            ep = ResolvedEndpoint(**item)  # raises ValidationError if invalid
            assert ep.method == "POST"
            assert ep.path == "/api/v1/auth/login"


# ---------------------------------------------------------------------------
# Integration tests: multi-file pattern
# ---------------------------------------------------------------------------


class TestProjectEndpointAnalyzerMultiFile:
    """Multi-file router pattern: one router variable per file."""

    def test_basic_aliased_import(self):
        """Standard aliased import + include_router resolves all endpoints."""
        files: Dict[str, str] = {
            "app/main.py": _multi_file_main(),
            "app/routers/auth.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.post('/login')\n"
                "def login(): pass\n\n"
                "@router.get('/me')\n"
                "def me(): pass\n"
            ),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}
        assert ("POST", "/api/v1/auth/login") in eps
        assert ("GET", "/api/v1/auth/me") in eps

    def test_multiple_router_files(self):
        """Multiple router files each receive their own prefix."""
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers.auth import router as auth_router\n"
                "from app.routers.patients import router as patients_router\n"
                "app = FastAPI()\n"
                'app.include_router(auth_router, prefix="/api/v1/auth")\n'
                'app.include_router(patients_router, prefix="/api/v1/patients")\n'
            ),
            "app/routers/auth.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.post('/login')\n"
                "def login(): pass\n"
            ),
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.get('/')\n"
                "def list_patients(): pass\n\n"
                "@router.post('/')\n"
                "def create_patient(): pass\n"
            ),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}
        assert ("POST", "/api/v1/auth/login") in eps
        assert ("GET", "/api/v1/patients/") in eps
        assert ("POST", "/api/v1/patients/") in eps

    def test_no_include_router_returns_empty(self):
        """When main.py has no include_router() calls, falls back to per-file local extraction.

        The fallback scans router files and returns endpoints without prefix resolution
        so that gap-fill passes are not fooled into thinking zero endpoints exist.
        """
        files = {
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "app/routers/auth.py": "@router.post('/login')\ndef login(): pass\n",
        }
        endpoints = ProjectEndpointAnalyzer(files).get_endpoints()
        # The fallback returns the local path without a prefix.
        local_paths = {(e["method"], e["path"]) for e in endpoints}
        assert ("POST", "/login") in local_paths

    def test_non_python_files_are_skipped(self):
        """Non-.py files do not contribute endpoints and do not raise."""
        files = {
            "app/main.py": _multi_file_main(),
            "app/routers/auth.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.post('/login')\n"
                "def login(): pass\n"
            ),
            "README.md": "# My App",
            "Dockerfile": "FROM python:3.12",
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}
        assert ("POST", "/api/v1/auth/login") in eps

    def test_empty_files_mapping_returns_empty(self):
        """Empty ``generated_files`` dict returns an empty list without error."""
        assert ProjectEndpointAnalyzer({}).get_endpoints() == []

    def test_nested_routers_directory_with_aliased_import(self):
        """Routers in app/routers/*.py with aliased imports should resolve correctly.

        This test specifically covers the case where multiple router files share
        the same local variable name (``router``) but are imported under distinct
        aliases in main.py.  The direct file→router mapping (via
        ``_file_to_var``) must resolve each file to its correct prefix even
        when the stem-based lookup would be ambiguous.
        """
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers.auth import router as auth_router\n"
                "from app.routers.patients import router as patients_router\n"
                "app = FastAPI()\n"
                'app.include_router(auth_router, prefix="/api/v1/auth")\n'
                'app.include_router(patients_router, prefix="/api/v1/patients")\n'
            ),
            "app/routers/auth.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.post('/login')\n"
                "def login(): pass\n\n"
                "@router.get('/me')\n"
                "def me(): pass\n"
            ),
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.get('/')\n"
                "def list_patients(): pass\n\n"
                "@router.get('/{id}')\n"
                "def get_patient(id: int): pass\n"
            ),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}

        # Auth endpoints
        assert ("POST", "/api/v1/auth/login") in eps
        assert ("GET", "/api/v1/auth/me") in eps

        # Patient endpoints
        assert ("GET", "/api/v1/patients/") in eps
        assert ("GET", "/api/v1/patients/{id}") in eps


# ---------------------------------------------------------------------------
# Integration tests: single-file pattern
# ---------------------------------------------------------------------------


class TestProjectEndpointAnalyzerSingleFile:
    """Single-file router pattern: all routers in app/routes.py."""

    def test_two_routers_in_one_file_direct_import(self):
        """Direct import of two routers → each assigned its own prefix."""
        files = {
            "app/main.py": _single_file_main(),
            "app/routes.py": _auth_routes_source(),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}
        assert ("POST", "/api/v1/auth/login") in eps
        assert ("GET", "/api/v1/patients/") in eps

    def test_three_routers_in_one_file(self):
        """Three routers in one file each receive distinct prefixes."""
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routes import "
                "auth_router, patients_router, encounters_router\n"
                "app = FastAPI()\n"
                'app.include_router(auth_router, prefix="/api/v1/auth")\n'
                'app.include_router(patients_router, prefix="/api/v1/patients")\n'
                'app.include_router(encounters_router, prefix="/api/v1/encounters")\n'
            ),
            "app/routes.py": (
                "from fastapi import APIRouter\n"
                "auth_router = APIRouter()\n"
                "patients_router = APIRouter()\n"
                "encounters_router = APIRouter()\n\n"
                "@auth_router.post('/login')\n"
                "def login(): pass\n\n"
                "@patients_router.get('/')\n"
                "def list_patients(): pass\n\n"
                "@encounters_router.post('/')\n"
                "def create_encounter(): pass\n"
            ),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}
        assert ("POST", "/api/v1/auth/login") in eps
        assert ("GET", "/api/v1/patients/") in eps
        assert ("POST", "/api/v1/encounters/") in eps

    def test_parenthesised_import_single_file(self):
        """Parenthesised multi-import is resolved for the single-file pattern."""
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routes import (\n"
                "    auth_router,\n"
                "    patients_router,\n"
                ")\n"
                "app = FastAPI()\n"
                'app.include_router(auth_router, prefix="/api/v1/auth")\n'
                'app.include_router(patients_router, prefix="/api/v1/patients")\n'
            ),
            "app/routes.py": _auth_routes_source(),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}
        assert ("POST", "/api/v1/auth/login") in eps
        assert ("GET", "/api/v1/patients/") in eps

    def test_single_file_no_cross_contamination(self):
        """Endpoints in the single-file are not assigned the wrong prefix."""
        files = {
            "app/main.py": _single_file_main(),
            "app/routes.py": _auth_routes_source(),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = analyzer.get_endpoints()
        # auth_router endpoints must NOT carry the patients prefix
        auth_paths = [e["path"] for e in eps if e["path"].startswith("/api/v1/auth")]
        for p in auth_paths:
            assert "/api/v1/patients" not in p


# ---------------------------------------------------------------------------
# Integration tests: edge cases
# ---------------------------------------------------------------------------


class TestProjectEndpointAnalyzerEdgeCases:
    """Edge cases for robustness and graceful degradation."""

    def test_main_py_fallback_used_when_app_main_absent(self):
        """Falls back to main.py when app/main.py is not present."""
        files = {
            "main.py": (
                "from fastapi import FastAPI\n"
                "from routes import auth_router\n"
                "app = FastAPI()\n"
                'app.include_router(auth_router, prefix="/api/v1/auth")\n'
            ),
            "routes.py": (
                "from fastapi import APIRouter\n"
                "auth_router = APIRouter()\n\n"
                "@auth_router.post('/login')\n"
                "def login(): pass\n"
            ),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = {(e["method"], e["path"]) for e in analyzer.get_endpoints()}
        assert ("POST", "/api/v1/auth/login") in eps

    def test_syntax_error_in_router_file_skipped_gracefully(self):
        """Router files with syntax errors are skipped without raising."""
        files = {
            "app/main.py": (
                "from app.routes import auth_router\n"
                'app.include_router(auth_router, prefix="/api/v1/auth")\n'
            ),
            "app/routes.py": "def broken(:\n",  # syntax error
        }
        analyzer = ProjectEndpointAnalyzer(files)
        eps = analyzer.get_endpoints()
        assert isinstance(eps, list)
        # No endpoints should be extracted from the broken file.
        assert eps == []

    def test_include_router_without_prefix_not_captured(self):
        """include_router() calls without prefix= are not processed."""
        files = {
            "app/main.py": (
                "from app.routes import auth_router\n"
                "app.include_router(auth_router)\n"  # no prefix=
            ),
            "app/routes.py": (
                "auth_router = APIRouter()\n\n"
                "@auth_router.post('/login')\ndef login(): pass\n"
            ),
        }
        assert ProjectEndpointAnalyzer(files).get_endpoints() == []

    def test_source_exceeding_max_size_skipped_in_multi_file(self):
        """Multi-file resolution skips sources exceeding max_source_size."""
        router_source = "@router.post('/login')\ndef login(): pass\n"
        files = {
            "app/main.py": _multi_file_main(),
            "app/routers/auth.py": router_source,
        }
        # Force max_source_size to 1 byte to trigger the guard.
        analyzer = ProjectEndpointAnalyzer(files, max_source_size=1)
        eps = analyzer.get_endpoints()
        assert eps == []

    def test_no_main_file_returns_empty(self):
        """No main.py / app/main.py → no router prefixes → falls back to per-file local extraction."""
        files = {
            "app/routers/auth.py": (
                "@router.post('/login')\ndef login(): pass\n"
            )
        }
        endpoints = ProjectEndpointAnalyzer(files).get_endpoints()
        # The fallback returns the local path without a prefix.
        local_paths = {(e["method"], e["path"]) for e in endpoints}
        assert ("POST", "/login") in local_paths

    def test_all_http_methods_resolved(self):
        """All HTTP methods are recognised as route decorators."""
        router_source = (
            "r = APIRouter()\n"
            "@r.get('/')\ndef list_(): pass\n"
            "@r.post('/')\ndef create(): pass\n"
            "@r.put('/{id}')\ndef update(id: int): pass\n"
            "@r.delete('/{id}')\ndef delete(id: int): pass\n"
            "@r.patch('/{id}')\ndef patch(id: int): pass\n"
        )
        files = {
            "app/main.py": (
                "from app.routes import r\n"
                'app.include_router(r, prefix="/api/v1/items")\n'
            ),
            "app/routes.py": router_source,
        }
        analyzer = ProjectEndpointAnalyzer(files)
        methods = {e["method"] for e in analyzer.get_endpoints()}
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods
        assert "DELETE" in methods
        assert "PATCH" in methods


# ---------------------------------------------------------------------------
# Public API contract tests
# ---------------------------------------------------------------------------


class TestProjectEndpointAnalyzerPublicAPI:
    """Verify the diagnostic accessor methods return correct data."""

    def _make_analyzer(self) -> ProjectEndpointAnalyzer:
        files = {
            "app/main.py": (
                "from app.routers.auth import router as auth_router\n"
                'app.include_router(auth_router, prefix="/api/v1/auth")\n'
            ),
        }
        return ProjectEndpointAnalyzer(files)

    def test_get_router_prefix_map_is_copy(self):
        """get_router_prefix_map() returns a copy, not the internal dict."""
        analyzer = self._make_analyzer()
        m1 = analyzer.get_router_prefix_map()
        m2 = analyzer.get_router_prefix_map()
        assert m1 == m2
        m1["mutated"] = "X"
        assert "mutated" not in analyzer.get_router_prefix_map()

    def test_get_router_prefix_map_content(self):
        """get_router_prefix_map() returns expected {var: prefix} mapping."""
        analyzer = self._make_analyzer()
        assert analyzer.get_router_prefix_map() == {"auth_router": "/api/v1/auth"}

    def test_get_stem_to_prefix_map_multi_file(self):
        """get_stem_to_prefix_map() returns {stem: prefix} for multi-file."""
        analyzer = self._make_analyzer()
        assert analyzer.get_stem_to_prefix_map() == {"auth": "/api/v1/auth"}

    def test_get_stem_to_prefix_map_excludes_single_file_stems(self):
        """Stems shared by multiple router variables are absent from stem map."""
        files = {
            "app/main.py": _single_file_main(),
        }
        analyzer = ProjectEndpointAnalyzer(files)
        # "routes" stem has two vars → should NOT appear in stem_to_prefix
        assert "routes" not in analyzer.get_stem_to_prefix_map()

    def test_get_var_to_prefix_map_content(self):
        """get_var_to_prefix_map() returns {var: prefix} for all known vars."""
        files = {"app/main.py": _single_file_main()}
        analyzer = ProjectEndpointAnalyzer(files)
        vmap = analyzer.get_var_to_prefix_map()
        assert vmap.get("auth_router") == "/api/v1/auth"
        assert vmap.get("patients_router") == "/api/v1/patients"

    def test_repr_contains_file_count(self):
        """__repr__ includes the number of files."""
        analyzer = ProjectEndpointAnalyzer({"app/main.py": "app = FastAPI()"})
        assert "files=1" in repr(analyzer)

    def test_str_contains_angle_brackets(self):
        """__str__ produces a human-readable summary starting with <."""
        analyzer = ProjectEndpointAnalyzer({})
        assert str(analyzer).startswith("<ProjectEndpointAnalyzer")


# ---------------------------------------------------------------------------
# Observability tests
# ---------------------------------------------------------------------------


class TestProjectEndpointAnalyzerObservability:
    """Verify Prometheus metrics are incremented on get_endpoints() calls."""

    def test_success_counter_incremented(self):
        """_analysis_total counter is incremented with status='success'."""
        import generator.utils.project_endpoint_analyzer as module

        mock_counter = MagicMock()
        mock_counter.labels.return_value = mock_counter

        with patch.object(module, "_analysis_total", mock_counter):
            analyzer = ProjectEndpointAnalyzer({})
            analyzer.get_endpoints()

        mock_counter.labels.assert_called_once_with(status="success")
        mock_counter.inc.assert_called_once()

    def test_duration_histogram_observed(self):
        """_analysis_duration histogram is observed on every call."""
        import generator.utils.project_endpoint_analyzer as module

        mock_histogram = MagicMock()

        with patch.object(module, "_analysis_duration", mock_histogram):
            ProjectEndpointAnalyzer({}).get_endpoints()

        mock_histogram.observe.assert_called_once()
        elapsed = mock_histogram.observe.call_args[0][0]
        assert elapsed >= 0.0

    def test_multiple_calls_each_increment_counter(self):
        """Each call to get_endpoints() independently increments the counter."""
        import generator.utils.project_endpoint_analyzer as module

        mock_counter = MagicMock()
        mock_counter.labels.return_value = mock_counter

        with patch.object(module, "_analysis_total", mock_counter):
            analyzer = ProjectEndpointAnalyzer({})
            analyzer.get_endpoints()
            analyzer.get_endpoints()

        assert mock_counter.inc.call_count == 2


# ---------------------------------------------------------------------------
# Inject-mock tests for extract_fn (internal _endpoints_for_file)
# ---------------------------------------------------------------------------


class TestProjectEndpointAnalyzerExtractFnInjection:
    """Validate that the multi-file branch correctly delegates to extract_fn."""

    def test_extract_fn_result_is_prefixed(self):
        """Paths returned by extract_fn are prepended with the router prefix."""
        files = {
            "app/main.py": _multi_file_main(auth_prefix="/api/v1/auth"),
            "app/routers/auth.py": "router = APIRouter()\n",
        }
        analyzer = ProjectEndpointAnalyzer(files)

        # Replace the lazily-imported extract_endpoints_from_code with a mock.
        fake_extract = MagicMock(
            return_value=[{"method": "POST", "path": "/login", "function_name": "login", "line_number": 1}]
        )

        with patch(
            "generator.main.provenance.extract_endpoints_from_code",
            fake_extract,
        ):
            eps = analyzer.get_endpoints()

        assert any(e["path"] == "/api/v1/auth/login" for e in eps)

    def test_extract_fn_called_with_correct_filename(self):
        """extract_fn receives the router file's path as the filename argument."""
        files = {
            "app/main.py": _multi_file_main(),
            "app/routers/auth.py": "router = APIRouter()\n",
        }
        analyzer = ProjectEndpointAnalyzer(files)
        fake_extract = MagicMock(return_value=[])

        with patch(
            "generator.main.provenance.extract_endpoints_from_code",
            fake_extract,
        ):
            analyzer.get_endpoints()

        call_args = [call[0] for call in fake_extract.call_args_list]
        filenames_used = [args[1] for args in call_args]
        assert "app/routers/auth.py" in filenames_used
