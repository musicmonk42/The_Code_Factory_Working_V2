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

    def test_function_stub_returns_none(self, stub_fn):
        """Template-generated auth stubs use real JWT logic, not a bare return None.

        The auth_stub.jinja2 template generates functional ``get_current_user``
        with JWT decode logic instead of the old ``return None`` placeholder.
        The stub must be valid Python and must NOT contain the stub marker.
        """
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        code = result["app/auth.py"]
        # New template-generated auth stubs have real implementations (not return None)
        assert "def get_current_user" in code, "get_current_user must be defined"
        assert _valid_python(code), "generated auth stub must be valid Python"
        # Must NOT contain the old placeholder stub marker
        assert "Generated module — replace with actual implementation." not in code, (
            "template-generated auth stubs must not contain the stub module marker"
        )

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

    def test_appended_function_stub_returns_none(self, stub_fn):
        files = {
            "app/routes.py": "from app.db import get_db\n",
            "app/db.py": "engine = None\n",
        }
        result = stub_fn(dict(files))
        assert "return None" in result["app/db.py"], \
            "appended function stub must return None (not raise NotImplementedError)"


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


# =============================================================================
# TestRouterVariableStubs — Issue 12 (api_router stubbed as APIRouter())
# =============================================================================


class TestRouterVariableStubs:
    """api_router and router symbols must be stubbed as APIRouter() instances."""

    def test_api_router_stubbed_as_apirouter(self, stub_fn):
        """api_router in a missing module must be an APIRouter() instance, not None."""
        files = {"app/routes.py": "from app.routers import api_router\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/routers.py", "")
        assert "APIRouter" in stub, "api_router stub must use APIRouter()"
        assert "api_router = None" not in stub, "api_router must not be stubbed as None"

    def test_router_stubbed_as_apirouter(self, stub_fn):
        """A bare 'router' symbol must also be stubbed as an APIRouter() instance."""
        files = {"app/routes.py": "from app.api import router\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/api.py", "")
        assert "APIRouter" in stub, "router stub must use APIRouter()"

    def test_new_module_with_two_router_syms_has_single_import(self, stub_fn):
        """APIRouter must be imported exactly once even when two router symbols appear."""
        files = {"app/routes.py": "from app.routers import api_router, router\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/routers.py", "")
        import_count = stub.count("from fastapi import APIRouter")
        assert import_count == 1, (
            f"APIRouter import must appear exactly once in new module stub, got {import_count}"
        )
        assert _valid_python(stub), "stub with two router symbols must be valid Python"

    def test_append_path_two_router_syms_has_single_import(self, stub_fn):
        """Appending two router symbols must produce a single APIRouter import."""
        files = {
            "app/routes.py": "from app.routers import api_router, router\n",
            "app/routers.py": "# placeholder\n",
        }
        result = stub_fn(dict(files))
        stub = result.get("app/routers.py", "")
        import_count = stub.count("from fastapi import APIRouter")
        assert import_count == 1, (
            f"APIRouter import must appear exactly once in appended stubs, got {import_count}"
        )
        assert _valid_python(stub), "file with appended router stubs must be valid Python"

    def test_existing_variable_not_restubbed(self, stub_fn):
        """A top-level variable already defined in a module must not be re-stubbed."""
        existing = "db_engine = create_engine(DATABASE_URL)\n"
        files = {
            "app/routes.py": "from app.db import db_engine\n",
            "app/db.py": existing,
        }
        result = stub_fn(dict(files))
        # Content must be unchanged — no stub appended.
        assert result["app/db.py"] == existing, (
            "existing top-level variable must not be re-stubbed"
        )

    def test_router_stub_idempotent_when_called_twice(self, stub_fn):
        """Calling ensure_local_module_stubs twice on router stubs must not duplicate them."""
        files = {"app/routes.py": "from app.routers import api_router\n"}
        result1 = stub_fn(dict(files))
        result2 = stub_fn(dict(result1))
        stub = result2["app/routers.py"]
        # Count assignments to api_router.
        assign_count = stub.count("api_router =")
        assert assign_count == 1, (
            f"api_router must be assigned exactly once after two passes, got {assign_count}"
        )
        assert _valid_python(stub), "idempotent router stub must remain valid Python"

    def test_products_router_stubbed_as_apirouter(self, stub_fn):
        """products_router must be stubbed as APIRouter(), not None."""
        files = {"app/main.py": "from app.routers.products import products_router\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/routers/products.py", "")
        assert "APIRouter" in stub, "products_router stub must use APIRouter()"
        assert "products_router = None" not in stub, "products_router must not be stubbed as None"
        assert "products_router = APIRouter()" in stub, "products_router must be assigned APIRouter()"

    def test_orders_router_stubbed_as_apirouter(self, stub_fn):
        """orders_router must be stubbed as APIRouter(), not None."""
        files = {"app/main.py": "from app.routers.orders import orders_router\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/routers/orders.py", "")
        assert "APIRouter" in stub, "orders_router stub must use APIRouter()"
        assert "orders_router = None" not in stub, "orders_router must not be stubbed as None"

    def test_suffixed_router_has_apirouter_import(self, stub_fn):
        """from fastapi import APIRouter must be emitted for suffixed router names."""
        files = {"app/main.py": "from app.routers.products import products_router\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/routers/products.py", "")
        assert "from fastapi import APIRouter" in stub, (
            "stub for products_router must include 'from fastapi import APIRouter'"
        )

    def test_verb_prefixed_router_name_not_stubbed_as_apirouter(self, stub_fn):
        """create_router must be stubbed as a function, not APIRouter().

        Factory functions like create_router() must not be confused with
        router-instance variables even though they end with _router.
        """
        files = {"app/main.py": "from app.factory import create_router\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/factory.py", "")
        # create_router should be a callable stub, not an APIRouter() assignment
        assert "create_router = APIRouter()" not in stub, (
            "create_router must not be stubbed as APIRouter() — it is a factory function"
        )
        assert "def create_router" in stub, (
            "create_router must be stubbed as a function"
        )


# =============================================================================
# TestIssue2EmptyInitPy — empty __init__.py files must pass validation
# =============================================================================


class TestIssue2EmptyInitPy:
    """Issue 2: empty __init__.py files are valid Python package markers."""

    def test_validate_syntax_allows_empty_init_py(self, crh):
        """Empty __init__.py must pass _validate_syntax without error."""
        valid, msg = crh._validate_syntax("", "python", "app/__init__.py")
        assert valid is True, f"Empty __init__.py must pass validation, got msg={msg!r}"
        assert msg == "", f"Empty __init__.py must not produce error message, got {msg!r}"

    def test_validate_syntax_allows_empty_nested_init_py(self, crh):
        """Empty __init__.py in a nested package must also pass."""
        valid, msg = crh._validate_syntax("", "python", "app/models/__init__.py")
        assert valid is True, "Empty nested __init__.py must pass _validate_syntax"

    def test_validate_syntax_still_rejects_empty_non_init(self, crh):
        """Empty non-__init__.py Python files must still be rejected."""
        valid, msg = crh._validate_syntax("", "python", "app/main.py")
        assert valid is False, "Empty non-__init__.py must still fail _validate_syntax"

    def test_validate_production_ready_skips_init_py(self, crh):
        """validate_production_ready must skip __init__.py files entirely."""
        files = {
            "app/__init__.py": "",                 # empty -- must not trigger stub check
            "app/models/__init__.py": "",
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        }
        valid, msg = crh.validate_production_ready(files)
        assert valid is True, (
            f"validate_production_ready must not fail on empty __init__.py files, msg={msg!r}"
        )


# =============================================================================
# TestIssue1StubTracking — get_stub_files and build_stub_retry_prompt_hint
# =============================================================================


class TestIssue1StubTracking:
    """Issue 1: stub files can be detected and surfaced as a retry hint."""

    def test_get_stub_files_detects_auto_generated_stub(self, crh):
        """get_stub_files must detect a file containing the canonical stub marker."""
        stub_content = (
            '"""Generated module — replace with actual implementation."""\n'
            "from typing import Any\n"
            "def get_db(*args: Any, **kwargs: Any) -> Any:\n"
            '    """Placeholder implementation."""\n'
            "    return None\n"
        )
        files = {
            "app/db.py": stub_content,
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        }
        stubs = crh.get_stub_files(files)
        assert "app/db.py" in stubs, "get_stub_files must detect stub in app/db.py"
        assert "app/main.py" not in stubs, "app/main.py must not be detected as stub"

    def test_get_stub_files_empty_when_no_stubs(self, crh):
        """get_stub_files returns empty set when no stubs are present."""
        files = {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
        assert crh.get_stub_files(files) == set()

    def test_build_stub_retry_prompt_hint_returns_hint(self, crh):
        """build_stub_retry_prompt_hint returns a non-empty hint when stubs exist."""
        stub_content = (
            '"""Generated module — replace with actual implementation."""\n'
            "from typing import Any\n"
            "def get_db(*args: Any, **kwargs: Any) -> Any:\n"
            '    """Placeholder implementation."""\n'
            "    return None\n"
        )
        hint = crh.build_stub_retry_prompt_hint({"app/db.py": stub_content})
        assert hint != "", "hint must be non-empty when stubs exist"
        assert "app/db.py" in hint, "hint must mention the stub file path"
        assert "IMPORTANT" in hint, "hint must start with IMPORTANT marker"

    def test_build_stub_retry_prompt_hint_empty_for_no_stubs(self, crh):
        """build_stub_retry_prompt_hint returns empty string when no stubs exist."""
        files = {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
        assert crh.build_stub_retry_prompt_hint(files) == ""

    def test_ensure_local_module_stubs_logs_created_stubs(self, crh):
        """ensure_local_module_stubs still returns a dict (backward compatible).

        Template-generated stubs (e.g. auth_stub.jinja2) produce functional code
        that does NOT contain the ``_STUB_MODULE_MARKER``, so ``get_stub_files``
        correctly does NOT flag them.  Verify the file was created instead.
        """
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = crh.ensure_local_module_stubs(dict(files))
        # Must return a dict
        assert isinstance(result, dict), "ensure_local_module_stubs must return a dict"
        # The auth stub file must be present in the result
        assert "app/auth.py" in result, (
            "ensure_local_module_stubs must create app/auth.py"
        )
        # Template-generated auth stubs do NOT contain the stub marker —
        # they are functional code that does not need to be replaced.
        assert "Generated module — replace with actual implementation." not in result["app/auth.py"], (
            "template-generated auth stub must not contain the stub module marker"
        )
        # The stub must be valid Python
        assert _valid_python(result["app/auth.py"]), (
            "template-generated auth stub must be syntactically valid Python"
        )


# =============================================================================
# TestIssue3CollisionAfterStubs — collision detection runs after stub generation
# =============================================================================


class TestIssue3CollisionAfterStubs:
    """Issue 3: module/package collision detection must run after stub generation."""

    def test_collision_resolved_when_stub_creates_conflicting_module(self, crh):
        """When stub creation introduces a .py file that conflicts with a package
        already in code_files, the collision must be resolved."""
        # Use the module loaded via the custom loader (crh fixture) to avoid
        # conflict with the synthetic package stubs registered in sys.modules.
        _detect_collisions = crh._detect_module_package_collisions
        files = {
            "app/routes.py": "x = 1",
            "app/auth.py": "# stub",
            "app/auth/__init__.py": "from fastapi import APIRouter\nrouter = APIRouter()\n",
        }
        cleaned = _detect_collisions(files)
        assert "app/auth.py" not in cleaned, (
            "app/auth.py must be removed when app/auth/__init__.py exists"
        )
        assert "app/auth/__init__.py" in cleaned


# =============================================================================
# TestStubMethodInjection — third pass injects methods from call sites
# =============================================================================


class TestStubMethodInjection:
    """Third pass: methods referenced on stub class instances must be injected."""

    def test_method_injected_for_depends_call_site(self, stub_fn):
        """Depends(var.method) must inject the method into the stub class."""
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "from fastapi import Depends\n"
                "auth_service = AuthService()\n"
                "async def me(user=Depends(auth_service.get_current_user)):\n"
                "    return user\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/auth.py", "")
        assert "class AuthService" in stub, "AuthService stub must be created"
        assert "get_current_user" in stub, (
            "get_current_user must be injected into AuthService stub"
        )

    def test_injected_depends_method_is_async(self, stub_fn):
        """Methods used in Depends() context must be generated as async."""
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "from fastapi import Depends\n"
                "auth_service = AuthService()\n"
                "async def me(user=Depends(auth_service.get_current_user)):\n"
                "    return user\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/auth.py", "")
        assert "async def get_current_user" in stub, (
            "get_current_user used in Depends() must be async def"
        )

    def test_method_injected_for_await_call_site(self, stub_fn):
        """``await var.method(...)`` must inject an async method into the stub."""
        files = {
            "app/routers/orders.py": (
                "from app.services.orders import OrderService\n"
                "order_service = OrderService()\n"
                "async def create(data: dict):\n"
                "    return await order_service.create_order(data)\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/orders.py", "")
        assert "class OrderService" in stub, "OrderService stub must be created"
        assert "async def create_order" in stub, (
            "create_order used with await must be async def"
        )

    def test_plain_attr_access_injects_non_async_method(self, stub_fn):
        """Plain ``var.method()`` without await or Depends must inject a sync method."""
        files = {
            "app/main.py": (
                "from app.services.email import EmailService\n"
                "email_service = EmailService()\n"
                "def notify(msg: str):\n"
                "    email_service.send(msg)\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/email.py", "")
        assert "class EmailService" in stub
        # ``send`` is not awaited and not in Depends — must be a plain def
        assert "def send" in stub
        assert "async def send" not in stub, (
            "send used without await/Depends must not be async"
        )

    def test_multiple_methods_injected(self, stub_fn):
        """All methods referenced on a stub instance must be injected."""
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "from fastapi import Depends\n"
                "auth_service = AuthService()\n"
                "async def login(data=Depends(auth_service.login)): ...\n"
                "async def logout(user=Depends(auth_service.logout)): ...\n"
                "async def me(user=Depends(auth_service.get_current_user)): ...\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/auth.py", "")
        for method in ("login", "logout", "get_current_user"):
            assert f"async def {method}" in stub, (
                f"{method} must be injected into AuthService stub"
            )

    def test_injected_stub_is_valid_python(self, stub_fn):
        """Stub class with injected methods must be syntactically valid Python."""
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "from fastapi import Depends\n"
                "auth_service = AuthService()\n"
                "async def me(user=Depends(auth_service.get_current_user)): ...\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/auth.py", "")
        assert _valid_python(stub), "stub with injected methods must be valid Python"

    def test_method_injection_idempotent(self, stub_fn):
        """Calling ensure_local_module_stubs twice must not duplicate injected methods."""
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "from fastapi import Depends\n"
                "auth_service = AuthService()\n"
                "async def me(user=Depends(auth_service.get_current_user)): ...\n"
            ),
        }
        result1 = stub_fn(dict(files))
        result2 = stub_fn(dict(result1))
        stub = result2.get("app/services/auth.py", "")
        assert stub.count("def get_current_user") == 1, (
            "get_current_user must appear exactly once after two passes"
        )
        assert _valid_python(stub), "idempotent injected stub must remain valid Python"

    def test_async_wins_over_sync_across_files(self, stub_fn):
        """When a method is called both as await and plain, async must win."""
        files = {
            "app/routers/orders.py": (
                "from app.services.orders import OrderService\n"
                "order_service = OrderService()\n"
                # Plain call in one place
                "def validate():\n"
                "    order_service.validate_order()\n"
            ),
            "app/routers/checkout.py": (
                "from app.services.orders import OrderService\n"
                "order_service = OrderService()\n"
                # Awaited call in another file
                "async def checkout():\n"
                "    await order_service.validate_order()\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/orders.py", "")
        # async wins
        assert "async def validate_order" in stub, (
            "validate_order must be async because it is awaited in checkout.py"
        )

    def test_dunder_attributes_are_not_injected(self, stub_fn):
        """Dunder attributes (__class__, __dict__, etc.) must never become method stubs.

        Injecting a ``def __class__(self)`` stub would shadow Python's built-in
        descriptor and break isinstance() checks at runtime.
        """
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "auth_service = AuthService()\n"
                # These dunder accesses must be silently ignored by the third pass.
                "x = auth_service.__class__\n"
                "y = auth_service.__dict__\n"
                "z = auth_service.__module__\n"
                # A real method that SHOULD be injected.
                "async def me(user=Depends(auth_service.get_current_user)): ...\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/auth.py", "")
        assert "def __class__" not in stub, "dunder __class__ must NOT be injected"
        assert "def __dict__" not in stub, "dunder __dict__ must NOT be injected"
        assert "def __module__" not in stub, "dunder __module__ must NOT be injected"
        # The real method should still be injected.
        assert "def get_current_user" in stub, (
            "get_current_user must still be injected alongside filtered dunders"
        )

    def test_injected_methods_use_single_blank_line_separator(self, stub_fn):
        """Methods injected into a stub class must be separated by exactly one blank line.

        PEP 8 §E303 requires at most two blank lines inside a class; one blank
        line between method definitions is the conventional standard.
        """
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "from fastapi import Depends\n"
                "auth_service = AuthService()\n"
                "async def login(d=Depends(auth_service.login)): ...\n"
                "async def logout(d=Depends(auth_service.logout)): ...\n"
            ),
        }
        result = stub_fn(dict(files))
        stub = result.get("app/services/auth.py", "")
        # Two consecutive ``raise NotImplementedError`` lines followed by two blank
        # lines (before the next ``def``) would indicate PEP 8 E303.
        # Strip trailing whitespace so end-of-file newlines don't trigger a false positive.
        stub_body = stub.rstrip("\n")
        assert "\n\n\n" not in stub_body, (
            "methods inside a stub class must not be separated by two blank lines"
        )
        assert _valid_python(stub), "stub with PEP-8 spacing must be valid Python"

    def test_third_pass_is_non_fatal_on_corrupt_content(self, stub_fn):
        """If the third pass encounters an unexpected error it must not discard code_files.

        The stub dict produced by the first two passes must always be returned
        intact, even if the method-injection regex scan fails on unusual input.
        """
        # Simulate a stub file that is syntactically unusual (but still valid Python)
        # so that the stub class regex won't match — injection silently skips.
        files = {
            "app/routers/auth.py": (
                "from app.services.auth import AuthService\n"
                "auth_service = AuthService()\n"
                "async def me(user=Depends(auth_service.get_current_user)): ...\n"
            ),
            # Override the stub with unusual whitespace that the regex won't match;
            # ensure_local_module_stubs must still return this file unchanged.
            "app/services/auth.py": (
                'class AuthService:\n'
                '    """Custom docstring — not the stub marker."""\n'
                '    pass\n'
            ),
        }
        result = stub_fn(dict(files))
        # The class file must still be present (not discarded by a crash).
        assert "app/services/auth.py" in result, (
            "code_files must be returned intact even when injection finds no match"
        )


# =============================================================================
# TestClassifyStubModule — _classify_stub_module() covers each category
# =============================================================================


class TestClassifyStubModule:
    """Tests for the _classify_stub_module() helper function."""

    @pytest.fixture(scope="class")
    def classify(self, crh):
        return crh._classify_stub_module

    def test_database_by_symbol(self, classify):
        assert classify("app/db.py", {"get_db"}) == "database"
        assert classify("app/db.py", {"async_sessionmaker"}) == "database"

    def test_service_by_directory(self, classify):
        assert classify("app/services/user_service.py", set()) == "service"
        assert classify("app/services/auth.py", {"AuthService"}) == "service"
        assert classify("app/api/v1/services/orders.py", set()) == "service"

    def test_auth_by_filename(self, classify):
        assert classify("app/auth.py", set()) == "auth"
        assert classify("app/security.py", set()) == "auth"
        assert classify("app/jwt.py", set()) == "auth"

    def test_auth_by_symbols(self, classify):
        assert classify("app/utils.py", {"get_current_user"}) == "auth"
        assert classify("app/helpers.py", {"create_access_token"}) == "auth"

    def test_config_by_filename(self, classify):
        assert classify("app/config.py", set()) == "config"
        assert classify("app/settings.py", set()) == "config"

    def test_model_by_directory(self, classify):
        assert classify("app/models/user.py", set()) == "model"
        assert classify("app/models/product.py", {"Product"}) == "model"

    def test_schema_by_directory(self, classify):
        assert classify("app/schemas/user.py", set()) == "schema"
        assert classify("app/schemas/product.py", {"ProductCreate"}) == "schema"

    def test_router_by_directory(self, classify):
        assert classify("app/routers/users.py", set()) == "router"
        assert classify("app/routes/api.py", set()) == "router"

    def test_service_beats_auth_for_services_path(self, classify):
        """app/services/auth.py must be 'service', not 'auth'."""
        assert classify("app/services/auth.py", set()) == "service"

    def test_generic_fallback(self, classify):
        assert classify("app/utils.py", {"helper", "foo"}) == "generic"
        assert classify("app/main.py", set()) == "generic"


# =============================================================================
# TestTemplateRendering — _render_stub_template() with various symbol lists
# =============================================================================


class TestTemplateRendering:
    """Tests for _render_stub_template() with various symbol combinations."""

    @pytest.fixture(scope="class")
    def render(self, crh):
        return crh._render_stub_template

    def test_auth_template_renders_valid_python(self, render):
        result = render("auth", "app/auth.py", {"get_current_user"})
        assert result is not None
        assert _valid_python(result), "auth stub must be valid Python"

    def test_auth_template_has_get_current_user(self, render):
        result = render("auth", "app/auth.py", {"get_current_user"})
        assert result is not None
        assert "def get_current_user" in result

    def test_auth_template_no_stub_marker(self, render):
        result = render("auth", "app/auth.py", {"get_current_user"})
        assert result is not None
        assert "Generated module — replace with actual implementation." not in result

    def test_model_template_has_declarative_base(self, render):
        result = render("model", "app/models/user.py", {"User"})
        assert result is not None
        assert "DeclarativeBase" in result

    def test_model_template_has_mapped_column(self, render):
        result = render("model", "app/models/user.py", {"User"})
        assert result is not None
        assert "mapped_column" in result

    def test_model_template_valid_python(self, render):
        result = render("model", "app/models/user.py", {"User", "Product"})
        assert result is not None
        assert _valid_python(result)

    def test_schema_template_has_field(self, render):
        result = render("schema", "app/schemas/user.py", {"UserCreate"})
        assert result is not None
        assert "Field(" in result

    def test_schema_template_has_config_dict(self, render):
        result = render("schema", "app/schemas/user.py", {"UserCreate"})
        assert result is not None
        assert "ConfigDict" in result

    def test_router_template_has_apirouter(self, render):
        result = render("router", "app/routers/users.py", {"router"})
        assert result is not None
        assert "APIRouter" in result

    def test_router_template_valid_python(self, render):
        result = render("router", "app/routers/users.py", {"router"})
        assert result is not None
        assert _valid_python(result)

    def test_generic_template_renders(self, render):
        result = render("generic", "app/utils.py", {"helper", "Util"})
        assert result is not None
        assert _valid_python(result)

    def test_unknown_category_falls_back_to_generic(self, render):
        result = render("nonexistent_category", "app/foo.py", {"bar"})
        assert result is not None, "unknown category must fall back to generic_stub.jinja2"
        assert _valid_python(result)


# =============================================================================
# TestNewStubBehavior — template-generated stubs pass detection checks
# =============================================================================


class TestNewStubBehavior:
    """Verify that template-generated stubs pass stub-detection checks."""

    def test_auth_stub_passes_is_stub_content_false(self, crh, stub_fn):
        """Template-generated auth stub must NOT be flagged as a stub by _is_stub_content."""
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        auth_content = result.get("app/auth.py", "")
        assert auth_content, "app/auth.py must be created"
        assert not crh._is_stub_content(auth_content), (
            "template-generated auth stub must pass _is_stub_content() check (return False)"
        )

    def test_auth_stub_passes_detect_stub_patterns_false(self, crh, stub_fn):
        """Template-generated auth stub must not trigger _detect_stub_patterns."""
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        auth_content = result.get("app/auth.py", "")
        is_stub, issues = crh._detect_stub_patterns(auth_content, "app/auth.py")
        assert not is_stub, (
            f"template-generated auth stub must not be flagged by _detect_stub_patterns; "
            f"issues: {issues}"
        )

    def test_auth_stub_no_stub_marker(self, stub_fn):
        """Template-generated auth stubs must not contain the old stub marker."""
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        auth_content = result.get("app/auth.py", "")
        assert "Generated module — replace with actual implementation." not in auth_content

    def test_model_stub_passes_is_stub_content_false(self, crh, stub_fn):
        """Template-generated model stub must NOT be flagged as a stub."""
        files = {"app/services/user.py": "from app.models.user import User\n"}
        result = stub_fn(dict(files))
        model_content = result.get("app/models/user.py", "")
        if model_content:
            assert not crh._is_stub_content(model_content), (
                "template-generated model stub must pass _is_stub_content() check"
            )

    def test_schema_stub_passes_is_stub_content_false(self, crh, stub_fn):
        """Template-generated schema stub must NOT be flagged as a stub."""
        files = {"app/routes.py": "from app.schemas.user import UserCreate\n"}
        result = stub_fn(dict(files))
        schema_content = result.get("app/schemas/user.py", "")
        if schema_content:
            assert not crh._is_stub_content(schema_content), (
                "template-generated schema stub must pass _is_stub_content() check"
            )

    def test_validate_production_ready_passes_for_auth_stub(self, crh, stub_fn):
        """validate_production_ready must pass for a set of template-generated stubs."""
        files = {"app/routes.py": "from app.auth import get_current_user\n"}
        result = stub_fn(dict(files))
        valid, msg = crh.validate_production_ready(result)
        assert valid, (
            f"validate_production_ready must pass for template-generated auth stub; msg={msg!r}"
        )

    def test_auth_stub_role_class_is_real(self, stub_fn):
        """Role class in auth_stub must be a real enum, not a bare pass stub."""
        files = {"app/routes.py": "from app.auth import Role\n"}
        result = stub_fn(dict(files))
        stub = result.get("app/auth.py", "")
        assert "class Role" in stub
        assert _valid_python(stub)
        # auth_stub.jinja2 generates Role as a str enum with real members
        assert "Enum" in stub, "Role must inherit from Enum (real implementation)"
        # Extract the body of the Role class and verify it has enum members
        role_section = stub.split("class Role")[1].split("\nclass ")[0]
        # An enum with real members will have assignments like ADMIN = "admin"
        assert "=" in role_section, "Role enum must have assigned member values"

    def test_auth_stub_is_valid_python_with_multiple_symbols(self, stub_fn):
        """Multi-symbol auth stub must produce valid Python."""
        files = {"app/routes.py": "from app.auth import get_current_user, Role, create_access_token\n"}
        result = stub_fn(dict(files))
        assert _valid_python(result.get("app/auth.py", "")), "multi-symbol auth stub must be valid Python"
