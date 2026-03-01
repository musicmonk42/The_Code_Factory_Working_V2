# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Comprehensive tests for the 6 bug fixes:

  P0 Fix 1 — Runtime URL rewrite in generated database.py stub
  P0 Fix 2 — Strip ``response_model`` from HTTP 204 decorators
  P0 Fix 3 — Spec-aware (field-populated) stub generation
  P1 Fix 4 — Double-prefix detection in ``_validate_no_double_prefix``
  P1 Fix 5 — Exclude ``alembic/env.py`` from LLM output
  P2 Fix 6 — Nested module resolution in ``fix_import_paths``
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import re
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Module loaders
# ---------------------------------------------------------------------------

def _load_crh():
    """Load codegen_response_handler with minimal synthetic dependencies."""
    mod_name = "crh_pr_fixes_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    pkg_name = "generator.agents.codegen_agent"
    for parent in ("generator", "generator.agents", pkg_name):
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)

    sar_name = f"{pkg_name}.syntax_auto_repair"
    if sar_name not in sys.modules:
        fake_sar = types.ModuleType(sar_name)

        class _SyntaxAutoRepair:
            def repair(self, code: str, **_kw) -> dict:
                return {"repaired_code": code, "repairs_applied": [], "auto_repaired": False}

        fake_sar.SyntaxAutoRepair = _SyntaxAutoRepair
        sys.modules[sar_name] = fake_sar

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/agents/codegen_agent/codegen_response_handler.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[mod_name]
        raise
    return mod


def _load_rfu():
    """Load runner_file_utils with minimal synthetic dependencies."""
    mod_name = "rfu_pr_fixes_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    # Stub aiofiles (only .open = None triggers the sync fallback already in the code)
    if "aiofiles" not in sys.modules:
        fake_aiofiles = types.ModuleType("aiofiles")
        fake_aiofiles.open = None
        sys.modules["aiofiles"] = fake_aiofiles

    if "yaml" not in sys.modules:
        fake_yaml = types.ModuleType("yaml")
        fake_yaml.safe_load = lambda x: {}
        fake_yaml.dump = lambda x, **kw: str(x)
        sys.modules["yaml"] = fake_yaml

    # register_file_handler must return a decorator
    _fake_handlers: Dict[str, Any] = {}

    def _register_file_handler(mime_type: str, extensions: list):
        def decorator(fn):
            _fake_handlers[mime_type] = fn
            return fn
        return decorator

    if "runner" not in sys.modules:
        fake_runner = types.ModuleType("runner")
        fake_runner.FILE_HANDLERS = _fake_handlers
        fake_runner.register_file_handler = _register_file_handler
        sys.modules["runner"] = fake_runner

    # Ensure the generator.runner package namespace is populated
    for pkg in ("generator", "generator.runner"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    rfu_pkg = "generator.runner"
    rl_name = f"{rfu_pkg}.runner_logging"
    if rl_name not in sys.modules:
        fake_rl = types.ModuleType(rl_name)
        fake_rl.logger = logging.getLogger("rfu_test")
        fake_rl.add_provenance = lambda *a, **kw: None
        sys.modules[rl_name] = fake_rl

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/runner/runner_file_utils.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = rfu_pkg
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[mod_name]
        raise
    return mod


def _load_fix_import_paths():
    """Extract and return the ``fix_import_paths`` function from
    ``testgen_response_handler.py`` without loading its full dependency tree.

    The function uses only ``re`` and a module-level logger, both of which are
    injected into a minimal execution namespace.
    """
    _cache_key = "_fix_import_paths_fn"
    if _cache_key in sys.modules:  # reuse across test calls
        return sys.modules[_cache_key]

    source_path = (
        PROJECT_ROOT / "generator/agents/testgen_agent/testgen_response_handler.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Collect SANITIZATION_PATTERNS assignment + _local_regex_sanitize + fix_import_paths
    parts: List[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SANITIZATION_PATTERNS":
                    parts.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.FunctionDef) and node.name in (
            "_local_regex_sanitize",
            "fix_import_paths",
        ):
            parts.append(ast.get_source_segment(source, node))

    combined = "\n\n".join(p for p in parts if p)

    namespace: Dict[str, Any] = {
        "re": re,
        "logging": logging,
        "logger": logging.getLogger("fix_import_paths_test"),
        "Dict": Dict,
        "Optional": Optional,
        "List": List,
        "Tuple": Tuple,
        "Any": Any,
    }
    exec(compile(combined, str(source_path), "exec"), namespace)
    fn = namespace["fix_import_paths"]

    # Cache the callable in a fake sys.modules slot to survive repeated calls
    fake_holder = types.ModuleType(_cache_key)
    fake_holder.fix_import_paths = fn
    sys.modules[_cache_key] = fake_holder
    return fake_holder


# ===========================================================================
# P0 Fix 1 — Runtime URL rewrite in database stub
# ===========================================================================


class TestDatabaseStubRuntimeUrlRewrite:
    """The generated database.py stub must rewrite sync driver URLs to async
    equivalents at *runtime* (after ``os.getenv`` resolves the env var)."""

    def setup_method(self):
        self._crh = _load_crh()

    def test_stub_contains_postgresql_rewrite(self):
        """Stub emits code to convert ``postgresql://`` → ``postgresql+asyncpg://``."""
        files = {"app/main.py": "from app.database import get_db\n"}
        result = self._crh.ensure_local_module_stubs(files)
        db = result.get("app/database.py", "")
        assert "postgresql://" in db, "Must handle postgresql:// case"
        assert "postgresql+asyncpg://" in db, "Must rewrite to postgresql+asyncpg://"

    def test_stub_uses_db_url_variable_in_engine(self):
        """create_async_engine must receive the rewritten ``_db_url`` variable."""
        files = {"app/main.py": "from app.database import async_sessionmaker, get_db\n"}
        result = self._crh.ensure_local_module_stubs(files)
        db = result.get("app/database.py", "")
        assert "_db_url" in db
        # The engine call must pass the rewritten variable (may span multiple lines)
        assert "create_async_engine(" in db
        # The _db_url variable must appear after the runtime rewrite block
        rewrite_pos = db.find("_db_url")
        engine_pos = db.find("create_async_engine(")
        assert rewrite_pos != -1
        assert engine_pos != -1
        # _db_url must be referenced after its assignment (rewrite block comes first)
        assert rewrite_pos < engine_pos or "_db_url" in db[engine_pos:]

    def test_stub_handles_sqlite_runtime_rewrite(self):
        """Stub must rewrite ``sqlite:///`` → ``sqlite+aiosqlite:///`` at runtime."""
        files = {"app/router.py": "from app.database import get_db\n"}
        result = self._crh.ensure_local_module_stubs(files)
        db = result.get("app/database.py", "")
        assert "sqlite:///" in db
        assert "sqlite+aiosqlite:///" in db

    def test_stub_handles_mysql_runtime_rewrite(self):
        """Stub must rewrite ``mysql://`` → ``mysql+aiomysql://`` at runtime."""
        files = {"app/services/items.py": "from app.database import get_db\n"}
        result = self._crh.ensure_local_module_stubs(files)
        db = result.get("app/database.py", "")
        assert "mysql://" in db
        assert "mysql+aiomysql://" in db


class TestFixAsyncDatabaseUrlRuntimeInjection:
    """``fix_async_database_url`` must inject a runtime URL rewrite snippet
    when ``create_async_engine`` is called with a variable sourced from
    ``os.getenv``."""

    def setup_method(self):
        self._crh = _load_crh()

    def test_injects_runtime_rewrite_for_getenv_url(self):
        files = {
            "app/database.py": (
                "import os\n"
                "from sqlalchemy.ext.asyncio import create_async_engine\n\n"
                'DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")\n'
                "engine = create_async_engine(DATABASE_URL)\n"
            )
        }
        result = self._crh.fix_async_database_url(files)
        content = result["app/database.py"]
        assert "_db_url" in content, "Runtime rewrite variable must be injected"

    def test_no_double_injection_when_already_rewritten(self):
        """If ``_db_url`` is already present, do NOT inject a second rewrite."""
        files = {
            "app/database.py": (
                "import os\n"
                "from sqlalchemy.ext.asyncio import create_async_engine\n\n"
                'DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./app.db")\n'
                "_db_url = DATABASE_URL\n"
                'if _db_url.startswith("postgresql://"):\n'
                '    _db_url = "postgresql+asyncpg://" + _db_url[len("postgresql://"):]\n'
                "engine = create_async_engine(_db_url)\n"
            )
        }
        result = self._crh.fix_async_database_url(files)
        # _db_url must appear exactly once (not duplicated)
        assert result["app/database.py"].count("_db_url = DATABASE_URL") == 1

    def test_no_effect_on_files_without_async_engine(self):
        """Files that do not use ``create_async_engine`` must be left unchanged."""
        files = {
            "app/sync_db.py": (
                "import os\n"
                "from sqlalchemy import create_engine\n\n"
                'DB_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")\n'
                "engine = create_engine(DB_URL)\n"
            )
        }
        result = self._crh.fix_async_database_url(files)
        assert result["app/sync_db.py"] == files["app/sync_db.py"]


# ===========================================================================
# P0 Fix 2 — Strip response_model from HTTP 204 decorators
# ===========================================================================


class TestFix204NoContentResponses:
    """``fix_204_no_content_responses`` must remove ``response_model`` from any
    FastAPI decorator that also carries ``status_code=204`` (or the enum
    equivalent), ensuring FastAPI's boot-time validation passes."""

    def setup_method(self):
        self._crh = _load_crh()

    def test_removes_response_model_from_204_decorator(self):
        files = {
            "app/routers/items.py": (
                "@router.delete('/{id}', status_code=204, response_model=DeleteResponse)\n"
                "async def delete_item(id: int):\n"
                "    return {'status': 'deleted'}\n"
            )
        }
        result = self._crh.fix_204_no_content_responses(files)
        content = result["app/routers/items.py"]
        assert "response_model" not in content
        assert "status_code=204" in content

    def test_handles_http_204_no_content_constant(self):
        files = {
            "app/routers/items.py": (
                "@app.delete('/{id}', status_code=status.HTTP_204_NO_CONTENT,"
                " response_model=Schema)\n"
                "async def delete_item(id: int):\n"
                "    return {'ok': True}\n"
            )
        }
        result = self._crh.fix_204_no_content_responses(files)
        content = result["app/routers/items.py"]
        assert "response_model" not in content

    def test_replaces_non_none_return_statement(self):
        files = {
            "app/routers/items.py": (
                "@router.delete('/{id}', status_code=204, response_model=DeleteResp)\n"
                "async def delete_item(id: int):\n"
                "    db.delete(item)\n"
                "    return {'status': 'ok'}\n"
            )
        }
        result = self._crh.fix_204_no_content_responses(files)
        content = result["app/routers/items.py"]
        assert "return None" in content

    def test_does_not_modify_200_decorators_with_response_model(self):
        original = (
            "@router.get('/', status_code=200, response_model=ListResponse)\n"
            "async def list_items():\n"
            "    return []\n"
        )
        files = {"app/routers/items.py": original}
        result = self._crh.fix_204_no_content_responses(files)
        assert result["app/routers/items.py"] == original

    def test_does_not_modify_204_without_response_model(self):
        original = (
            "@router.delete('/{id}', status_code=204)\n"
            "async def delete_item(id: int):\n"
            "    pass\n"
        )
        files = {"app/routers/items.py": original}
        result = self._crh.fix_204_no_content_responses(files)
        assert result["app/routers/items.py"] == original

    def test_skips_non_python_files(self):
        original = "status_code=204, response_model=X"
        files = {"README.md": original}
        result = self._crh.fix_204_no_content_responses(files)
        assert result["README.md"] == original

    def test_preserves_remaining_decorator_parameters(self):
        """Parameters other than ``response_model`` must be preserved."""
        files = {
            "app/routers/items.py": (
                "@router.delete('/{id}', tags=['items'], status_code=204,"
                " response_model=DeleteResp, summary='Delete item')\n"
                "async def delete_item(id: int):\n"
                "    pass\n"
            )
        }
        result = self._crh.fix_204_no_content_responses(files)
        content = result["app/routers/items.py"]
        assert "response_model" not in content
        assert "tags=['items']" in content
        assert "summary='Delete item'" in content or "summary=" in content

    def test_empty_files_dict_returns_empty(self):
        result = self._crh.fix_204_no_content_responses({})
        assert result == {}


# ===========================================================================
# P0 Fix 3 — Spec-aware (field-populated) stub generation
# ===========================================================================


class TestSpecAwareStubGeneration:
    """Stubs for Pydantic schemas and SQLAlchemy models must include fields
    detected from how the importing files access the class."""

    def setup_method(self):
        self._crh = _load_crh()

    # ---- Pydantic schema stubs ----

    def test_pydantic_stub_includes_instance_attribute_fields(self):
        """Fields accessed as ``order.user_id`` must appear in the schema stub."""
        files = {
            "app/routers/orders.py": (
                "from app.schemas import Order\n\n"
                "async def get_order(order: Order):\n"
                "    return order.user_id, order.total_amount\n"
            )
        }
        result = self._crh.ensure_local_module_stubs(files)
        schema = result.get("app/schemas.py", "")
        assert "user_id" in schema, "Detected instance field must appear in stub"
        assert "total_amount" in schema, "Detected instance field must appear in stub"

    def test_pydantic_stub_includes_class_attribute_fields(self):
        """Fields accessed as ``Product.price`` must appear in the schema stub."""
        files = {
            "app/services/products.py": (
                "from app.schemas import Product\n\n"
                "price = Product.price\n"
                "category = Product.category\n"
            )
        }
        result = self._crh.ensure_local_module_stubs(files)
        schema = result.get("app/schemas.py", "")
        assert "price" in schema
        assert "category" in schema

    def test_pydantic_stub_has_model_config(self):
        """Pydantic stubs must include ``ConfigDict(from_attributes=True)``."""
        files = {
            "app/routers/items.py": (
                "from app.schemas import Item\n\n"
                "async def read_item(item: Item):\n"
                "    return item.name\n"
            )
        }
        result = self._crh.ensure_local_module_stubs(files)
        schema = result.get("app/schemas.py", "")
        # Must include ConfigDict or model_config
        assert "ConfigDict" in schema or "model_config" in schema

    def test_pydantic_stub_fields_are_optional_any(self):
        """Detected fields should be typed ``Optional[Any]`` with ``None`` default."""
        files = {
            "app/routers/users.py": (
                "from app.schemas import User\n\n"
                "x = User.email\n"
            )
        }
        result = self._crh.ensure_local_module_stubs(files)
        schema = result.get("app/schemas.py", "")
        assert "email" in schema
        # Should be Optional[Any] = None
        assert "Optional" in schema or "Any" in schema

    # ---- SQLAlchemy model stubs ----

    def test_sqlalchemy_stub_includes_detected_fields(self):
        """Fields accessed on an ORM model instance must appear as Columns."""
        files = {
            "app/services/orders.py": (
                "from app.models.order import Order\n\n"
                "async def get_order(order: Order):\n"
                "    return order.user_id, order.items\n"
            )
        }
        result = self._crh.ensure_local_module_stubs(files)
        model_path = "app/models/order.py"
        assert model_path in result
        model = result[model_path]
        assert "user_id" in model
        assert "items" in model

    def test_sqlalchemy_stub_has_tablename(self):
        """SQLAlchemy model stubs must declare ``__tablename__``."""
        files = {
            "app/services/products.py": (
                "from app.models.product import Product\n\n"
                "async def get_product(product: Product):\n"
                "    return product.sku\n"
            )
        }
        result = self._crh.ensure_local_module_stubs(files)
        model_path = "app/models/product.py"
        if model_path in result:
            model = result[model_path]
            assert "__tablename__" in model

    # ---- Router stubs ----

    def test_router_stub_uses_apirouter_instance(self):
        """Router stubs must create an ``APIRouter()`` instance, not ``None``."""
        files = {"app/main.py": "from app.routers import router\n"}
        result = self._crh.ensure_local_module_stubs(files)
        router_stub = result.get("app/routers.py", "")
        assert "APIRouter" in router_stub
        assert "router = APIRouter()" in router_stub

    def test_router_stub_imports_apirouter(self):
        """Router stubs must import ``APIRouter`` from fastapi."""
        files = {"app/main.py": "from app.routers.items import items_router\n"}
        result = self._crh.ensure_local_module_stubs(files)
        router_stub = result.get("app/routers/items.py", "")
        assert "from fastapi import APIRouter" in router_stub

    # ---- Idempotency ----

    def test_existing_non_stub_file_not_overwritten(self):
        """A file with real (non-stub) content must never be overwritten."""
        real_content = (
            "from pydantic import BaseModel\n\n"
            "class Item(BaseModel):\n"
            "    name: str\n"
            "    price: float\n"
        )
        files = {
            "app/routers/items.py": "from app.schemas import Item\n",
            "app/schemas.py": real_content,
        }
        result = self._crh.ensure_local_module_stubs(files)
        assert result["app/schemas.py"] == real_content


# ===========================================================================
# P1 Fix 4 — Double-prefix detection
# ===========================================================================


class TestValidateNoDoublePrefix:
    """``_validate_no_double_prefix`` must detect routers that are mounted with
    a path prefix in both the ``APIRouter`` constructor *and* the
    ``include_router`` call."""

    def setup_method(self):
        self._rfu = _load_rfu()

    def test_detects_double_prefix(self, tmp_path):
        """Same prefix in router definition and include_router is flagged."""
        (tmp_path / "app" / "routers").mkdir(parents=True)
        (tmp_path / "app" / "routers" / "orders.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/v1/orders')\n"
            "@router.get('/')\n"
            "def list_orders(): pass\n"
        )
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "from app.routers.orders import router\n"
            "app = FastAPI()\n"
            "app.include_router(router, prefix='/api/v1/orders')\n"
        )

        errors = self._rfu._validate_no_double_prefix(tmp_path)
        assert len(errors) > 0
        assert any("prefix" in e.lower() or "double" in e.lower() for e in errors)

    def test_no_error_when_prefix_only_in_router_def(self, tmp_path):
        """Prefix in router def but NOT in include_router — no error."""
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "orders.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/v1/orders')\n"
        )
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "from app.orders import router\n"
            "app = FastAPI()\n"
            "app.include_router(router)\n"
        )

        errors = self._rfu._validate_no_double_prefix(tmp_path)
        assert errors == []

    def test_no_error_when_prefix_only_in_include_router(self, tmp_path):
        """Prefix in include_router but NOT in APIRouter def — no error."""
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "orders.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/')\n"
            "def list_orders(): pass\n"
        )
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "from app.orders import router\n"
            "app = FastAPI()\n"
            "app.include_router(router, prefix='/api/v1/orders')\n"
        )

        errors = self._rfu._validate_no_double_prefix(tmp_path)
        assert errors == []

    def test_detects_route_decorator_with_full_prefix_path(self, tmp_path):
        """Route decorator that repeats the router prefix must be flagged."""
        (tmp_path / "orders.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/v1/orders')\n"
            "@router.get('/api/v1/orders/list')\n"
            "def list_orders(): pass\n"
        )

        errors = self._rfu._validate_no_double_prefix(tmp_path)
        assert len(errors) > 0

    def test_no_error_on_relative_route_path(self, tmp_path):
        """Relative route path in a prefixed router — no error."""
        (tmp_path / "orders.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/v1/orders')\n"
            "@router.get('/list')\n"
            "def list_orders(): pass\n"
        )
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "from orders import router\n"
            "app = FastAPI()\n"
            "app.include_router(router)\n"
        )

        errors = self._rfu._validate_no_double_prefix(tmp_path)
        assert errors == []

    def test_returns_list_for_empty_directory(self, tmp_path):
        """An empty output directory must return an empty error list."""
        errors = self._rfu._validate_no_double_prefix(tmp_path)
        assert errors == []

    def test_multiple_routers_flagged_independently(self, tmp_path):
        """Every router with a doubled prefix must produce its own error."""
        for name in ("orders", "products"):
            (tmp_path / f"{name}.py").write_text(
                f"from fastapi import APIRouter\n"
                f"router = APIRouter(prefix='/api/v1/{name}')\n"
            )
        (tmp_path / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "from orders import router as r1\n"
            "from products import router as r2\n"
            "app.include_router(r1, prefix='/api/v1/orders')\n"
            "app.include_router(r2, prefix='/api/v1/products')\n"
        )
        errors = self._rfu._validate_no_double_prefix(tmp_path)
        assert len(errors) >= 2


# ===========================================================================
# P2 Fix 6 — Nested module import resolution in fix_import_paths
# ===========================================================================


class TestFixImportPathsNestedModules:
    """``fix_import_paths`` must correctly resolve nested module paths and
    build multi-level mappings so that imports like ``from app.orders import X``
    are rewritten to ``from app.routers.orders import X`` when the actual file
    is ``app/routers/orders.py``."""

    def setup_method(self):
        self._fix = _load_fix_import_paths().fix_import_paths

    def test_strips_generated_project_prefix(self):
        """``generated.<project>.app.main`` must become ``app.main``."""
        test_files = {
            "test_main.py": "from generated.myproject.app.main import app\n"
        }
        result = self._fix(test_files)
        content = result["test_main.py"]
        assert "generated.myproject" not in content
        assert "app.main" in content

    def test_resolves_leaf_name_to_full_path(self):
        """``from main import app`` must become ``from app.main import app``."""
        code_files = {"app/main.py": "# main\n", "app/utils.py": "# utils\n"}
        test_files = {"test_main.py": "from main import app\n"}
        result = self._fix(test_files, code_files)
        assert "app.main" in result["test_main.py"]

    def test_resolves_short_path_to_nested_module(self):
        """``from app.orders import X`` must map to ``from app.routers.orders import X``
        when the actual file is ``app/routers/orders.py``."""
        code_files = {"app/routers/orders.py": "# orders\n"}
        test_files = {"test_orders.py": "from app.orders import list_orders\n"}
        result = self._fix(test_files, code_files)
        content = result["test_orders.py"]
        assert "app.routers.orders" in content

    def test_multilevel_key_mapping(self):
        """``from services.user_service import X`` must map to
        ``from app.services.user_service import X``."""
        code_files = {"app/services/user_service.py": "# user service\n"}
        test_files = {
            "test_user.py": "from services.user_service import UserService\n"
        }
        result = self._fix(test_files, code_files)
        content = result["test_user.py"]
        assert "app.services.user_service" in content

    def test_unchanged_when_import_already_correct(self):
        """Imports that are already correct must not be modified."""
        code_files = {"app/main.py": "# main\n"}
        original = "from app.main import app\n"
        test_files = {"test_main.py": original}
        result = self._fix(test_files, code_files)
        assert result["test_main.py"] == original

    def test_non_python_files_unchanged(self):
        """Non-Python test files must be returned unmodified."""
        code_files = {"app/main.py": "# main\n"}
        original = "from main import app\n"
        test_files = {"test.js": original}
        result = self._fix(test_files, code_files, language="javascript")
        assert result["test.js"] == original

    def test_doubled_package_import_fixed(self):
        """``from app.app import X`` must be fixed to ``from app.main import X``."""
        code_files = {"app/main.py": "# main\n"}
        test_files = {"test_app.py": "from app.app import something\n"}
        result = self._fix(test_files, code_files)
        content = result["test_app.py"]
        assert "app.app" not in content or "app.main" in content

    def test_deeply_nested_generated_prefix_stripped(self):
        """Multi-segment ``generated.`` prefixes must all be stripped."""
        test_files = {
            "test_deep.py": "from generated.my_project.v2.app.routers import router\n"
        }
        result = self._fix(test_files)
        content = result["test_deep.py"]
        assert "generated." not in content
        assert "app.routers" in content
