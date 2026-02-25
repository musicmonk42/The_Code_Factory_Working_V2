# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — ensure_local_module_stubs (Issues 2 & 3)
======================================================

Validates the ``ensure_local_module_stubs()`` function added to
``generator/agents/codegen_agent/codegen_response_handler.py``.

Tests are structured in three classes:

* :class:`TestMissingModule`   — Issue 2: entire module file absent
* :class:`TestMissingSymbol`   — Issue 3: module exists but symbol absent
* :class:`TestEdgeCases`       — boundary conditions (star-imports, aliases,
                                  idempotency, non-Python files, …)

The module is loaded by manually bootstrapping the parent package hierarchy
and injecting a lightweight stub for ``syntax_auto_repair`` so that the
relative import chain does not drag in ``redis``/``aiohttp``/``tenacity``.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path
from typing import Dict

import pytest

# ---------------------------------------------------------------------------
# Module loader — bootstraps the package hierarchy to allow relative imports
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent


def _load_crh():
    """Load codegen_response_handler, bootstrapping just enough package
    context for the relative import to resolve without pulling in redis/aiohttp.

    Returns ``(module, synthetic_names)`` where *synthetic_names* is the set
    of ``sys.modules`` keys that were added by this function so the caller can
    clean them up without disturbing real packages registered by other tests.
    """
    mod_name = "crh_under_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name], set()

    pkg_name = "generator.agents.codegen_agent"
    synthetic: set = set()

    # Register synthetic parent packages only when the real ones are absent.
    for parent in ("generator", "generator.agents", pkg_name):
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)
            synthetic.add(parent)

    # Inject a stub for syntax_auto_repair before loading the handler.
    sar_name = f"{pkg_name}.syntax_auto_repair"
    if sar_name not in sys.modules:
        fake_sar = types.ModuleType(sar_name)

        class _SyntaxAutoRepair:  # minimal stub
            def repair(self, code: str, **_kw) -> dict:
                return {"repaired_code": code, "repairs_applied": [], "auto_repaired": False}

        fake_sar.SyntaxAutoRepair = _SyntaxAutoRepair
        sys.modules[sar_name] = fake_sar
        synthetic.add(sar_name)

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/agents/codegen_agent/codegen_response_handler.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name  # enable relative imports
    sys.modules[mod_name] = mod
    synthetic.add(mod_name)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        for name in synthetic:
            sys.modules.pop(name, None)
        raise
    return mod, synthetic


@pytest.fixture(scope="module")
def crh(request):
    """Load the codegen_response_handler module and register a finalizer that
    removes only the synthetic ``sys.modules`` entries it added, so that
    later test modules can still import the real ``generator`` package."""
    mod, synthetic = _load_crh()
    def _cleanup():
        for name in synthetic:
            sys.modules.pop(name, None)
    request.addfinalizer(_cleanup)
    return mod


@pytest.fixture(scope="module")
def stub_fn(crh):
    return crh.ensure_local_module_stubs


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _valid_python(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _defined_names(code: str):
    tree = ast.parse(code)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


# =============================================================================
# TestMissingModule — Issue 2
# =============================================================================


class TestMissingModule:
    """Issue 2: entire module file is absent from code_files."""

    def test_creates_stub_file_for_missing_module(self, stub_fn):
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        assert "app/auth.py" in result, "stub file must be created for missing app.auth"

    def test_stub_is_valid_python(self, stub_fn):
        files = {"app/routes.py": "from app.auth import get_current_user, Role\n"}
        result = stub_fn(dict(files))
        assert _valid_python(result["app/auth.py"]), "generated stub must be valid Python"

    def test_function_stub_raises_not_implemented(self, stub_fn):
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        code = result["app/auth.py"]
        assert "NotImplementedError" in code, "function stub must raise NotImplementedError"

    def test_class_stub_generated_for_uppercase_name(self, stub_fn):
        files = {"app/routes.py": "from app.auth import Role\n"}
        result = stub_fn(dict(files))
        assert "class Role" in result["app/auth.py"], "uppercase name must become a class stub"

    def test_function_stub_generated_for_lowercase_name(self, stub_fn):
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        assert "def get_current_user" in result["app/auth.py"], \
            "lowercase name must become a function stub"

    def test_multiple_symbols_all_stubbed(self, stub_fn):
        files = {"app/routes.py": "from app.auth import get_current_user, Role, create_access_token\n"}
        result = stub_fn(dict(files))
        stub = result["app/auth.py"]
        assert "def get_current_user" in stub
        assert "class Role" in stub
        assert "def create_access_token" in stub

    def test_stub_has_typing_any_import(self, stub_fn):
        files = {"app/routes.py": "from app.auth import helper\n"}
        result = stub_fn(dict(files))
        assert "from typing import Any" in result["app/auth.py"], \
            "stub file must import Any for type annotations"

    def test_stub_does_not_import_optional(self, stub_fn):
        """Optional is unused in stubs — importing it would be dead code."""
        files = {"app/routes.py": "from app.auth import helper\n"}
        result = stub_fn(dict(files))
        # Inspect only the header import line
        first_lines = result["app/auth.py"].split("\n")[:6]
        import_line = next((l for l in first_lines if l.startswith("from typing")), "")
        assert "Optional" not in import_line, \
            "stub header must not import unused Optional"

    def test_stub_function_uses_typed_args(self, stub_fn):
        """Function stubs must declare *args: Any, **kwargs: Any -> Any."""
        files = {"app/routes.py": "from app.auth import my_func\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/auth.py", "")
        assert "*args: Any" in stub or "**kwargs: Any" in stub, \
            "function stub must use Any type annotation for args"

    def test_symbols_deduped_across_multiple_importing_files(self, stub_fn):
        """If two files both import Role, only one class stub is generated."""
        files = {
            "app/routes.py": "from app.auth import Role\n",
            "app/middleware.py": "from app.auth import Role\n",
        }
        result = stub_fn(dict(files))
        stub = result["app/auth.py"]
        assert stub.count("class Role") == 1, "Role class must appear exactly once"

    def test_symbols_merged_across_multiple_importing_files(self, stub_fn):
        """Symbols from multiple importing files are all present in one stub."""
        files = {
            "app/routes.py": "from app.auth import Role\n",
            "app/middleware.py": "from app.auth import get_current_user\n",
        }
        result = stub_fn(dict(files))
        stub = result["app/auth.py"]
        assert "class Role" in stub
        assert "def get_current_user" in stub


# =============================================================================
# TestMissingSymbol — Issue 3
# =============================================================================


class TestMissingSymbol:
    """Issue 3: module exists but required symbol is absent."""

    def test_appends_stub_for_missing_class(self, stub_fn):
        files = {
            "app/routes.py": "from app.schemas import User\n",
            "app/schemas.py": "from pydantic import BaseModel\n\nclass UserBase(BaseModel):\n    pass\n",
        }
        result = stub_fn(dict(files))
        assert "class User" in result["app/schemas.py"], "User class stub must be appended"

    def test_does_not_duplicate_existing_symbol(self, stub_fn):
        files = {
            "app/routes.py": "from app.schemas import User\n",
            "app/schemas.py": "class User:\n    pass\n",
        }
        result = stub_fn(dict(files))
        assert result["app/schemas.py"].count("class User") == 1, \
            "existing User class must not be duplicated"

    def test_multiple_missing_symbols_all_appended(self, stub_fn):
        files = {
            "app/routes.py": "from app.schemas import User, Product, Order, AuditLog\n",
            "app/schemas.py": "from pydantic import BaseModel\n\nclass UserBase(BaseModel):\n    pass\n",
        }
        result = stub_fn(dict(files))
        schemas = result["app/schemas.py"]
        for sym in ("User", "Product", "Order", "AuditLog"):
            assert f"class {sym}" in schemas, f"{sym} stub must be appended to schemas.py"

    def test_existing_content_preserved(self, stub_fn):
        original = "from pydantic import BaseModel\n\nclass UserBase(BaseModel):\n    name: str\n"
        files = {
            "app/routes.py": "from app.schemas import User\n",
            "app/schemas.py": original,
        }
        result = stub_fn(dict(files))
        assert result["app/schemas.py"].startswith(original), \
            "original file content must be preserved before appended stubs"

    def test_appended_stub_is_valid_python(self, stub_fn):
        files = {
            "app/routes.py": "from app.schemas import Product\n",
            "app/schemas.py": "class UserBase:\n    pass\n",
        }
        result = stub_fn(dict(files))
        assert _valid_python(result["app/schemas.py"]), \
            "file with appended stub must remain valid Python"

    def test_function_stub_appended_for_lowercase_symbol(self, stub_fn):
        files = {
            "app/routes.py": "from app.db import get_db\n",
            "app/db.py": "engine = None\n",
        }
        result = stub_fn(dict(files))
        assert "def get_db" in result["app/db.py"], \
            "missing lowercase symbol must be appended as a function stub"

    def test_appended_function_stub_uses_typed_args(self, stub_fn):
        """Appended function stubs must use *args: Any, **kwargs: Any -> Any."""
        files = {
            "app/routes.py": "from app.db import get_db\n",
            "app/db.py": "engine = None\n",
        }
        result = stub_fn(dict(files))
        stub_section = result["app/db.py"]
        assert "Any" in stub_section, \
            "appended function stub must use Any type annotation"

    def test_appended_function_raises_not_implemented(self, stub_fn):
        files = {
            "app/routes.py": "from app.db import get_db\n",
            "app/db.py": "engine = None\n",
        }
        result = stub_fn(dict(files))
        assert "NotImplementedError" in result["app/db.py"], \
            "appended function stub must raise NotImplementedError"


# =============================================================================
# TestEdgeCases — boundary conditions
# =============================================================================


class TestEdgeCases:
    """Boundary conditions and negative tests."""

    def test_star_import_ignored(self, stub_fn):
        """Star imports must not cause any stub generation."""
        files = {"app/routes.py": "from app.auth import *\n"}
        result = stub_fn(dict(files))
        assert "app/auth.py" not in result, "star import must not trigger stub generation"

    def test_alias_import_stubs_original_name(self, stub_fn):
        """'from app.auth import Role as R' must stub Role, not R."""
        files = {"app/routes.py": "from app.auth import Role as R\n"}
        result = stub_fn(dict(files))
        assert "class Role" in result.get("app/auth.py", ""), \
            "aliased import must stub the original name (Role), not the alias (R)"

    def test_stdlib_import_ignored(self, stub_fn):
        """Standard-library imports must not be treated as local modules."""
        files = {"app/main.py": "from datetime import datetime\n"}
        original_keys = set(files.keys())
        result = stub_fn(dict(files))
        assert set(result.keys()) == original_keys, \
            "stdlib import must not add any files"

    def test_third_party_import_ignored(self, stub_fn):
        files = {"app/main.py": "from fastapi import FastAPI, Depends\n"}
        result = stub_fn(dict(files))
        assert "fastapi.py" not in result, \
            "third-party import must not trigger stub generation"

    def test_idempotent_when_called_twice(self, stub_fn):
        """Calling ensure_local_module_stubs twice must not add duplicate definitions."""
        files = {
            "app/routes.py": "from app.schemas import User\n",
            "app/schemas.py": "class UserBase:\n    pass\n",
        }
        result1 = stub_fn(dict(files))
        # Pass result1 as the new input — the stub for User is already in schemas.py
        result2 = stub_fn(dict(result1))
        # Use AST to count exact class definitions (avoids substring false-positive
        # from "class UserBase" which contains the substring "class User").
        tree = ast.parse(result2["app/schemas.py"])
        user_classes = [
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "User"
        ]
        assert len(user_classes) == 1, "second call must not duplicate the User stub"

    def test_empty_code_files_returns_unchanged(self, stub_fn):
        result = stub_fn({})
        assert result == {}, "empty input must produce empty output"

    def test_returns_same_dict_object(self, stub_fn):
        """ensure_local_module_stubs must return the same dict object (in-place contract)."""
        files: Dict[str, str] = {"app/routes.py": "from app.auth import helper\n"}
        result = stub_fn(files)
        assert result is files, "must return the same dict object (in-place modification)"

    def test_multiline_parenthesised_import_produces_no_stubs(self, stub_fn):
        """Multi-line parenthesised imports start with '(' on the first line.
        The '(' token is not a valid Python identifier so it is filtered out,
        no symbols are collected, and no stub file is created."""
        files = {
            "app/routes.py": (
                "from app.auth import (\n"
                "    get_current_user,\n"
                "    Role,\n"
                ")\n"
            )
        }
        result = stub_fn(dict(files))
        assert "app/auth.py" not in result, \
            "multi-line parenthesised imports must not trigger stub generation"

    def test_deeply_nested_module_resolved(self, stub_fn):
        """from app.api.v1.users import UserService — deeply-nested module path."""
        files = {"app/routes.py": "from app.api.v1.users import UserService\n"}
        result = stub_fn(dict(files))
        assert "app/api/v1/users.py" in result, \
            "deeply-nested module path must be resolved correctly"
        assert "class UserService" in result["app/api/v1/users.py"]
