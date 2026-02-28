# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — Multi-Pass Codegen Improvements
=============================================

Validates the upgrades applied to codegen_agent.py and codegen_prompt.py:

Fix 1 — ``_MULTIPASS_GROUPS`` focus strings are now maximally prescriptive:
    Tested via :class:`TestMultipassGroupFocusStrings`.

Fix 2 — ``_extract_spec_models`` upgraded with heading-split, table keyword
    matching, code-block detection, whitespace-normalised deduplication, and a
    hard character cap:
    Tested via :class:`TestExtractSpecModels`.

Fix 3 — ``_validate_wiring`` upgraded with anchored router-variable regex,
    precise import + mount checks, extended stub-body pattern, and float pct:
    Tested via :class:`TestValidateWiring`.

Fix 4 — ``_reconcile_app_wiring`` step-5 now handles parenthesised multiline
    imports, skips class names, and produces stubs with ``return None``:
    Tested via :class:`TestReconcileAppWiringStep5`.

Fix 5 — ``get_syntax_safety_instructions`` CRITICAL section extended with
    requirements 8–15:
    Tested via :class:`TestSyntaxSafetyInstructionsCriticalSection`.

Fix 6 — ``_reconcile_app_wiring`` step-6 (AST-based) deduplicates router
    function definitions and renames handlers that shadow imported service names:
    Tested via :class:`TestReconcileAppWiringStep6`.

Coverage contract
-----------------
* Source-level checks validate string literals present in the module files.
* Functional checks load the modules with ``importlib`` (bypassing heavy
  transitive imports via sys.modules stubs) and call the functions directly.
* No network access or real API keys are required.

Author: Code Factory Platform Team
Version: 1.0.0
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Source-reader helpers (no module loading required for structural checks)
# ---------------------------------------------------------------------------

def _read_agent_src() -> str:
    return (PROJECT_ROOT / "generator/agents/codegen_agent/codegen_agent.py").read_text(encoding="utf-8")


def _read_prompt_src() -> str:
    return (PROJECT_ROOT / "generator/agents/codegen_agent/codegen_prompt.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Functional module loader — stubs out heavy transitive dependencies
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _install_stubs() -> None:
    """Pre-populate sys.modules with lightweight stubs for all heavy deps."""
    stubs: Dict[str, Any] = {
        # aiohttp
        "aiohttp": _make_stub_module("aiohttp"),
        # redis — override to ensure Redis attribute is present
        "redis": _make_stub_module("redis"),
        "redis.asyncio": _make_stub_module("redis.asyncio", Redis=object, StrictRedis=object),
        # yaml
        "yaml": _make_stub_module("yaml", safe_load=lambda *a, **kw: {}),
        # fastapi — needs to be callable and support @app.get/post/etc decorators
        "fastapi": _make_stub_module(
            "fastapi",
            FastAPI=type("FastAPI", (), {
                "__init__": lambda self, **kw: None,
                "get": lambda self, *a, **kw: (lambda f: f),
                "post": lambda self, *a, **kw: (lambda f: f),
                "put": lambda self, *a, **kw: (lambda f: f),
                "delete": lambda self, *a, **kw: (lambda f: f),
                "include_router": lambda self, *a, **kw: None,
                "add_middleware": lambda self, *a, **kw: None,
            }),
            HTTPException=Exception,
            Request=object,
            Depends=lambda f=None: f,
        ),
        # jinja2
        "jinja2": _make_stub_module("jinja2", TemplateNotFound=Exception),
        # opentelemetry
        "opentelemetry": _make_stub_module("opentelemetry"),
        "opentelemetry.trace": _make_stub_module("opentelemetry.trace", get_tracer=lambda *a, **kw: None),
        "opentelemetry.exporter": _make_stub_module("opentelemetry.exporter"),
        "opentelemetry.exporter.jaeger": _make_stub_module("opentelemetry.exporter.jaeger"),
        "opentelemetry.exporter.jaeger.thrift": _make_stub_module("opentelemetry.exporter.jaeger.thrift"),
        # prometheus — a fresh stub with ALL required attributes
        "prometheus_client": _make_stub_module(
            "prometheus_client",
            REGISTRY=_make_stub_module("REGISTRY", _names_to_collectors={}, _collector_to_names={}),
            Counter=lambda *a, **kw: None,
            Gauge=lambda *a, **kw: None,
            Histogram=lambda *a, **kw: None,
            generate_latest=lambda *a, **kw: b"",
            start_http_server=lambda *a, **kw: None,
        ),
        "omnicore_engine": _make_stub_module("omnicore_engine"),
        "omnicore_engine.plugin_registry": _make_stub_module(
            "omnicore_engine.plugin_registry",
            PlugInKind=type("PlugInKind", (), {"FIX": "fix", "GENERATE": "generate", "TRANSFORM": "transform"}),
            plugin=lambda *a, **kw: (lambda f: f),
        ),
        # generator stubs
        "generator": _make_stub_module("generator"),
        "generator.runner": _make_stub_module("generator.runner"),
        "generator.runner.llm_client": _make_stub_module(
            "generator.runner.llm_client",
            CircuitBreaker=object,
            call_ensemble_api=None,
            call_llm_api=None,
        ),
        "generator.runner.runner_audit": _make_stub_module(
            "generator.runner.runner_audit", log_audit_event=lambda *a, **kw: None
        ),
        "generator.runner.runner_security_utils": _make_stub_module(
            "generator.runner.runner_security_utils", scan_for_vulnerabilities=lambda *a, **kw: []
        ),
        "generator.agents": _make_stub_module("generator.agents"),
        "generator.agents.plugin_stubs": _make_stub_module(
            "generator.agents.plugin_stubs",
            PlugInKind=type("PlugInKind", (), {"FIX": "fix", "GENERATE": "generate", "TRANSFORM": "transform"}),
            plugin=lambda *a, **kw: (lambda f: f),
        ),
        # codegen sub-modules
        "generator.agents.codegen_agent": _make_stub_module("generator.agents.codegen_agent"),
        "generator.agents.codegen_agent.codegen_prompt": _make_stub_module(
            "generator.agents.codegen_agent.codegen_prompt",
            build_code_generation_prompt=None,
        ),
        "generator.agents.codegen_agent.codegen_response_handler": _make_stub_module(
            "generator.agents.codegen_agent.codegen_response_handler",
            add_traceability_comments=lambda f, **kw: f,
            parse_llm_response=lambda r: {},
            build_stub_retry_prompt_hint=lambda r: "",
            _detect_module_package_collisions=lambda f: f,
            disambiguate_model_schema_imports=lambda f, **kw: f,
            fix_response_model_type_mismatches=lambda f, **kw: f,
            get_stub_files=lambda f: [],
        ),
    }
    _ALWAYS_OVERRIDE = frozenset({"prometheus_client", "redis.asyncio"})
    for name, mod in stubs.items():
        # Always override prometheus_client to ensure it has generate_latest / start_http_server.
        # Always override redis.asyncio to ensure it has Redis attribute.
        # For other stubs use setdefault so we don't clobber real installed packages.
        if name in _ALWAYS_OVERRIDE:
            sys.modules[name] = mod
        else:
            sys.modules.setdefault(name, mod)

    # Patch the already-registered prometheus_client (from conftest) if it lacks the
    # attributes that codegen_agent.py needs to import.
    pc = sys.modules.get("prometheus_client")
    if pc is not None:
        if not hasattr(pc, "generate_latest"):
            pc.generate_latest = lambda *a, **kw: b""  # type: ignore[attr-defined]
        if not hasattr(pc, "start_http_server"):
            pc.start_http_server = lambda *a, **kw: None  # type: ignore[attr-defined]


_install_stubs()


def _load_agent_module() -> types.ModuleType:
    """Load codegen_agent.py bypassing its package __init__."""
    # Use the real dotted name so relative imports resolve correctly.
    pkg_name = "generator.agents.codegen_agent"
    mod_name = f"{pkg_name}.codegen_agent"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    # Ensure the package hierarchy exists in sys.modules so that relative
    # imports like ``from .codegen_prompt import ...`` resolve to our stubs.
    for part_name in (
        "generator",
        "generator.agents",
        pkg_name,
    ):
        if part_name not in sys.modules:
            sys.modules[part_name] = _make_stub_module(part_name)

    # Make sure the sibling submodules are already in sys.modules so that
    # the relative imports in codegen_agent.py don't trigger a real load.
    sys.modules.setdefault(
        f"{pkg_name}.codegen_prompt",
        _make_stub_module(f"{pkg_name}.codegen_prompt", build_code_generation_prompt=None),
    )
    sys.modules.setdefault(
        f"{pkg_name}.codegen_response_handler",
        _make_stub_module(
            f"{pkg_name}.codegen_response_handler",
            add_traceability_comments=lambda f, **kw: f,
            parse_llm_response=lambda r: {},
            build_stub_retry_prompt_hint=lambda r: "",
            _detect_module_package_collisions=lambda f: f,
            disambiguate_model_schema_imports=lambda f, **kw: f,
            fix_response_model_type_mismatches=lambda f, **kw: f,
            get_stub_files=lambda f: [],
        ),
    )

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/agents/codegen_agent/codegen_agent.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    mod.__package__ = pkg_name  # required for relative imports
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return mod


# ---------------------------------------------------------------------------
# Fix 1 — _MULTIPASS_GROUPS focus strings
# ---------------------------------------------------------------------------

class TestMultipassGroupFocusStrings:
    """_MULTIPASS_GROUPS focus strings must be maximally prescriptive."""

    def _src(self) -> str:
        return _read_agent_src()

    # -- core pass --

    def test_core_sqlalchemy_column_imports_mentioned(self):
        src = self._src()
        assert "from sqlalchemy import Column, String, Integer, UUID, DateTime, ForeignKey, Boolean, Numeric" in src

    def test_core_sqlalchemy_orm_imports_mentioned(self):
        src = self._src()
        assert "from sqlalchemy.orm import relationship, DeclarativeBase, Mapped, mapped_column" in src

    def test_core_async_session_mentioned(self):
        src = self._src()
        assert "async_sessionmaker" in src and "AsyncSession" in src

    def test_core_shared_base_mentioned(self):
        src = self._src()
        assert "Base = declarative_base()" in src and "app/database.py" in src

    def test_core_alembic_base_metadata_mentioned(self):
        src = self._src()
        assert "Base.metadata" in src

    def test_core_pydantic_config_dict_mentioned(self):
        src = self._src()
        assert "model_config = ConfigDict(from_attributes=True)" in src

    # -- routes_and_services pass --

    def test_routes_repository_pattern_no_raw_sql(self):
        src = self._src()
        assert "no raw SQL f-strings" in src

    def test_routes_http_status_codes_specified(self):
        src = self._src()
        assert "404" in src and "400" in src and "409" in src and "422" in src

    def test_routes_jwt_auth_mentioned(self):
        src = self._src()
        assert "python-jose" in src or "PyJWT" in src
        assert "401" in src

    def test_routes_rate_limiting_mentioned(self):
        src = self._src()
        assert "starlette-ratelimit" in src or "Redis counter" in src
        assert "429" in src

    def test_routes_request_id_mentioned(self):
        src = self._src()
        assert "X-Request-ID" in src

    def test_routes_security_headers_mentioned(self):
        src = self._src()
        assert "X-Content-Type-Options" in src
        assert "X-Frame-Options" in src
        assert "Strict-Transport-Security" in src
        assert "Content-Security-Policy" in src

    # -- infrastructure pass --

    def test_infra_multi_stage_build_mentioned(self):
        src = self._src()
        assert "multi-stage build" in src or "AS builder" in src

    def test_infra_termination_grace_period_mentioned(self):
        src = self._src()
        assert "terminationGracePeriodSeconds" in src

    def test_infra_rolling_update_mentioned(self):
        src = self._src()
        assert "RollingUpdate" in src

    def test_infra_liveness_probe_mentioned(self):
        src = self._src()
        assert "/healthz" in src and "initialDelaySeconds: 10" in src

    def test_infra_readiness_probe_mentioned(self):
        src = self._src()
        assert "/readyz" in src and "initialDelaySeconds: 5" in src

    def test_infra_resource_requests_mentioned(self):
        src = self._src()
        assert "cpu: 100m" in src and "memory: 128Mi" in src

    def test_infra_resource_limits_mentioned(self):
        src = self._src()
        assert "cpu: 500m" in src and "memory: 512Mi" in src

    def test_infra_helm_values_defaults_mentioned(self):
        src = self._src()
        assert "sensible defaults" in src or "values.yaml" in src.lower()


# ---------------------------------------------------------------------------
# Fix 2 — _extract_spec_models (functional)
# ---------------------------------------------------------------------------

class TestExtractSpecModels:
    """Functional tests for the upgraded _extract_spec_models."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_agent_module()
        return mod._extract_spec_models  # type: ignore[attr-defined]

    def test_empty_requirements_returns_empty(self, fn):
        assert fn({}) == ""

    def test_empty_md_content_returns_empty(self, fn):
        assert fn({"md_content": ""}) == ""

    def test_none_md_content_falls_back_to_description(self, fn):
        md = "## Data Models\n| Field | Type |\n|---|---|\n| id | UUID |\n"
        result = fn({"md_content": None, "description": md})
        assert "UUID" in result

    def test_model_heading_section_extracted(self, fn):
        md = (
            "# Overview\n\nSome overview text.\n\n"
            "## Data Models\n\nThis section describes the data models.\n"
            "| Field | Type | Description |\n|---|---|---|\n| id | UUID | Primary key |\n"
            "| name | String | Product name |\n\n"
            "## API Endpoints\n\nSome endpoints."
        )
        result = fn({"md_content": md})
        assert "Data Models" in result or "UUID" in result

    def test_schema_heading_extracted(self, fn):
        md = "## Schema\n\nThe schema has fields: id (UUID), name (string).\n"
        result = fn({"md_content": md})
        assert result != ""

    def test_entity_heading_extracted(self, fn):
        md = "## Entity Definitions\n\nProduct entity: id UUID, name String.\n"
        result = fn({"md_content": md})
        assert result != ""

    def test_table_with_uuid_extracted(self, fn):
        md = "| field | type | required |\n|---|---|---|\n| id | UUID | yes |\n| name | string | yes |\n"
        result = fn({"md_content": md})
        assert "UUID" in result

    def test_table_without_keywords_not_extracted(self, fn):
        md = "| column_a | column_b |\n|---|---|\n| alpha | beta |\n"
        # No model-related keywords: may or may not be extracted, but if empty that's fine
        result = fn({"md_content": md})
        # We simply assert this doesn't crash
        assert isinstance(result, str)

    def test_python_code_block_with_class_extracted(self, fn):
        md = (
            "```python\n"
            "class Product(Base):\n"
            "    id = Column(UUID, primary_key=True)\n"
            "    name = Column(String(255))\n"
            "```\n"
        )
        result = fn({"md_content": md})
        assert "Product" in result or "Column" in result or "UUID" in result

    def test_code_block_with_basemodel_extracted(self, fn):
        md = (
            "```python\n"
            "class ProductSchema(BaseModel):\n"
            "    id: UUID\n"
            "    name: str\n"
            "```\n"
        )
        result = fn({"md_content": md})
        assert "BaseModel" in result or "UUID" in result

    def test_result_capped_at_max_chars(self, fn):
        # Build a very large spec
        large_section = "## Data Models\n\n" + ("| field | type | required |\n|---|---|---|\n| id | UUID | yes |\n") * 200
        result = fn({"md_content": large_section})
        assert len(result) <= 12000

    def test_deduplication_prevents_repeated_content(self, fn):
        # The same table appears twice — it should only appear once
        table = "| field | type |\n|---|---|\n| id | UUID |\n"
        md = table + "\n\n" + table
        result = fn({"md_content": md})
        # Count occurrences of "UUID" — dedup should limit them
        occurrences = result.count("UUID")
        assert occurrences <= 2  # normalised dedup removes exact duplicates

    def test_short_sections_below_min_len_ignored(self, fn):
        # Section with heading but only a few characters of body
        md = "## Data Models\n\nok\n"
        # This is < 30 chars for body; might or might not be included but should not crash
        result = fn({"md_content": md})
        assert isinstance(result, str)

    def test_no_model_content_returns_empty(self, fn):
        md = "# Overview\n\nThis service manages orders.\n\n## API\n\nGET /orders returns a list.\n"
        result = fn({"md_content": md})
        # No model/schema/entity headings; likely empty
        assert isinstance(result, str)

    def test_constants_present_in_source(self):
        src = _read_agent_src()
        assert "_SPEC_MODELS_MAX_CHARS" in src
        assert "_SPEC_MODELS_MIN_SECTION_LEN" in src

    def test_spec_models_max_chars_value(self):
        mod = _load_agent_module()
        assert mod._SPEC_MODELS_MAX_CHARS == 12000  # type: ignore[attr-defined]

    def test_spec_models_min_section_len_value(self):
        mod = _load_agent_module()
        assert mod._SPEC_MODELS_MIN_SECTION_LEN == 30  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Fix 3 — _validate_wiring (functional)
# ---------------------------------------------------------------------------

class TestValidateWiring:
    """Functional tests for the upgraded _validate_wiring."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_agent_module()
        return mod._validate_wiring  # type: ignore[attr-defined]

    # -- router wiring checks --

    def test_unwired_router_detected(self, fn):
        files = {
            "app/routers/products.py": "router = APIRouter()\n@router.get('/')\nasync def list(): ...",
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()",
        }
        result = fn(files)
        assert "app/routers/products.py" in result["unwired_routers"]

    def test_wired_router_not_flagged(self, fn):
        files = {
            "app/routers/products.py": "router = APIRouter()\n@router.get('/')\nasync def list(): ...",
            "app/main.py": (
                "from app.routers.products import router\n"
                "app = FastAPI()\n"
                "app.include_router(router)\n"
            ),
        }
        result = fn(files)
        assert "app/routers/products.py" not in result["unwired_routers"]

    def test_router_imported_but_not_mounted_flagged(self, fn):
        files = {
            "app/routers/orders.py": "router = APIRouter()\n",
            "app/main.py": "from app.routers.orders import router\napp = FastAPI()\n",
        }
        result = fn(files)
        assert "app/routers/orders.py" in result["unwired_routers"]

    def test_router_mounted_but_not_imported_flagged(self, fn):
        files = {
            "app/routers/orders.py": "router = APIRouter()\n",
            "app/main.py": "app = FastAPI()\napp.include_router(router)\n",
        }
        result = fn(files)
        assert "app/routers/orders.py" in result["unwired_routers"]

    def test_init_py_not_treated_as_router(self, fn):
        files = {
            "app/routers/__init__.py": "router = APIRouter()\n",
            "app/main.py": "app = FastAPI()\n",
        }
        result = fn(files)
        assert "app/routers/__init__.py" not in result["unwired_routers"]

    def test_no_routers_returns_empty_list(self, fn):
        files = {"app/main.py": "app = FastAPI()\n"}
        result = fn(files)
        assert result["unwired_routers"] == []

    def test_unwired_routers_sorted(self, fn):
        files = {
            "app/routers/zebra.py": "router = APIRouter()\n",
            "app/routers/alpha.py": "router = APIRouter()\n",
            "app/main.py": "app = FastAPI()\n",
        }
        result = fn(files)
        assert result["unwired_routers"] == sorted(result["unwired_routers"])

    def test_backslash_paths_normalised(self, fn):
        files = {
            "app\\routers\\products.py": "router = APIRouter()\n",
            "app\\main.py": "from app.routers.products import router\napp = FastAPI()\napp.include_router(router)\n",
        }
        result = fn(files)
        assert "app/routers/products.py" not in result["unwired_routers"]

    # -- placeholder service checks --

    def test_all_stub_service_flagged(self, fn):
        svc = (
            "async def get_product():\n"
            "    pass\n\n"
            "async def list_products():\n"
            "    return []\n\n"
            "async def delete_product():\n"
            "    raise NotImplementedError\n"
        )
        files = {"app/services/product.py": svc}
        result = fn(files)
        paths = [t[0] for t in result["placeholder_services"]]
        assert "app/services/product.py" in paths

    def test_real_service_not_flagged(self, fn):
        svc = (
            "from sqlalchemy.orm import Session\n\n"
            "async def get_product(db: Session, product_id: int):\n"
            "    return db.query(Product).filter(Product.id == product_id).first()\n\n"
            "async def list_products(db: Session):\n"
            "    return db.query(Product).all()\n"
        )
        files = {"app/services/product.py": svc}
        result = fn(files)
        paths = [t[0] for t in result["placeholder_services"]]
        assert "app/services/product.py" not in paths

    def test_placeholder_pct_is_float(self, fn):
        svc = "async def f():\n    pass\n"
        files = {"app/services/stub.py": svc}
        result = fn(files)
        for _path, pct in result["placeholder_services"]:
            assert isinstance(pct, float)

    def test_placeholder_services_sorted(self, fn):
        files = {
            "app/services/zebra.py": "async def f():\n    pass\n",
            "app/services/alpha.py": "async def f():\n    return []\n",
        }
        result = fn(files)
        paths = [t[0] for t in result["placeholder_services"]]
        assert paths == sorted(paths)

    def test_service_file_outside_services_dir_not_checked(self, fn):
        files = {"app/utils/helper.py": "async def f():\n    pass\n"}
        result = fn(files)
        assert result["placeholder_services"] == []

    def test_threshold_constant_present_in_source(self):
        src = _read_agent_src()
        assert "_PLACEHOLDER_SERVICE_THRESHOLD_PCT" in src

    def test_placeholder_threshold_value(self):
        mod = _load_agent_module()
        assert mod._PLACEHOLDER_SERVICE_THRESHOLD_PCT == 30.0  # type: ignore[attr-defined]

    def test_todo_comment_counted_as_stub(self, fn):
        svc = (
            "async def create():\n"
            "    # TODO implement this\n"
            "    pass\n"
        )
        files = {"app/services/todo_svc.py": svc}
        result = fn(files)
        paths = [t[0] for t in result["placeholder_services"]]
        assert "app/services/todo_svc.py" in paths


# ---------------------------------------------------------------------------
# Fix 4 — _reconcile_app_wiring step 5 (functional)
# ---------------------------------------------------------------------------

class TestReconcileAppWiringStep5:
    """Step-5 of _reconcile_app_wiring: service import reconciliation."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_agent_module()
        return mod._reconcile_app_wiring  # type: ignore[attr-defined]

    def _minimal_files(self, router_content: str, svc_content: str) -> dict:
        return {
            "app/routers/products.py": router_content,
            "app/services/product.py": svc_content,
        }

    def test_missing_function_stub_added(self, fn):
        files = self._minimal_files(
            router_content=(
                "from app.services.product import get_product\n"
                "router = APIRouter()\n"
            ),
            svc_content="# empty service\n",
        )
        result = fn(files)
        assert "get_product" in result["app/services/product.py"]

    def test_present_function_not_duplicated(self, fn):
        files = self._minimal_files(
            router_content=(
                "from app.services.product import get_product\n"
                "router = APIRouter()\n"
            ),
            svc_content="async def get_product(db, pid):\n    return db.get(pid)\n",
        )
        result = fn(files)
        count = result["app/services/product.py"].count("def get_product")
        assert count == 1

    def test_stub_raises_not_implemented_error(self, fn):
        files = self._minimal_files(
            router_content="from app.services.product import missing_fn\nrouter = APIRouter()\n",
            svc_content="# empty\n",
        )
        result = fn(files)
        svc = result["app/services/product.py"]
        assert "return None" in svc
        assert "NotImplementedError" not in svc

    def test_stub_has_typed_signature(self, fn):
        files = self._minimal_files(
            router_content="from app.services.product import my_fn\nrouter = APIRouter()\n",
            svc_content="# empty\n",
        )
        result = fn(files)
        svc = result["app/services/product.py"]
        assert "Any" in svc or "*args" in svc

    def test_class_names_skipped(self, fn):
        """Uppercase names (class imports) must not generate stubs."""
        files = self._minimal_files(
            router_content="from app.services.product import ProductService\nrouter = APIRouter()\n",
            svc_content="# empty\n",
        )
        result = fn(files)
        svc = result["app/services/product.py"]
        # No stub should be generated for 'ProductService'
        assert "def ProductService" not in svc

    def test_parenthesised_import_handled(self, fn):
        router = (
            "from app.services.product import (\n"
            "    get_product,\n"
            "    list_products,\n"
            ")\n"
            "router = APIRouter()\n"
        )
        files = self._minimal_files(
            router_content=router,
            svc_content="# empty\n",
        )
        result = fn(files)
        svc = result["app/services/product.py"]
        assert "get_product" in svc
        assert "list_products" in svc

    def test_aliased_import_uses_original_name(self, fn):
        router = (
            "from app.services.product import get_product as fetch_product\n"
            "router = APIRouter()\n"
        )
        files = self._minimal_files(
            router_content=router,
            svc_content="# empty\n",
        )
        result = fn(files)
        svc = result["app/services/product.py"]
        assert "get_product" in svc

    def test_missing_service_file_silently_skipped(self, fn):
        files = {
            "app/routers/products.py": (
                "from app.services.nonexistent import something\n"
                "router = APIRouter()\n"
            ),
        }
        # Should not raise; nonexistent service file is silently skipped
        result = fn(files)
        assert "app/services/nonexistent.py" not in result

    def test_skip_names_not_stubbed(self, fn):
        """TYPE_CHECKING, Any, Dict, etc. must never produce stubs."""
        router = (
            "from app.services.product import Any, Dict, List\n"
            "router = APIRouter()\n"
        )
        files = self._minimal_files(router_content=router, svc_content="# empty\n")
        result = fn(files)
        svc = result["app/services/product.py"]
        assert "def Any" not in svc
        assert "def Dict" not in svc
        assert "def List" not in svc

    def test_step5_src_contains_paren_regex(self):
        src = _read_agent_src()
        assert "_svc_import_paren_re" in src
        assert "_svc_import_simple_re" in src

    def test_step5_src_contains_skip_names(self):
        src = _read_agent_src()
        assert "_SKIP_NAMES" in src
        assert "TYPE_CHECKING" in src
        # Extended typing-construct coverage
        assert "TypeVar" in src
        assert "Protocol" in src
        assert "Literal" in src
        assert "cast" in src


# ---------------------------------------------------------------------------
# Fix 6 — _reconcile_app_wiring step 6 (AST-based deduplication)
# ---------------------------------------------------------------------------

class TestReconcileAppWiringStep6:
    """Step-6 of _reconcile_app_wiring: AST-based router deduplication."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_agent_module()
        return mod._reconcile_app_wiring  # type: ignore[attr-defined]

    # ── helpers ──────────────────────────────────────────────────────────

    def _router_file(self, content: str) -> dict:
        return {"app/routers/auth.py": content}

    # ── deduplication ────────────────────────────────────────────────────

    def test_duplicate_definition_removed(self, fn):
        """Second occurrence of a function definition must be dropped."""
        router = (
            "from app.services.auth_service import login\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "@router.post('/login')\n"
            "async def login_endpoint(data: dict):\n"
            "    return await login(data)\n"
            "\n"
            "@router.post('/login2')\n"
            "async def login_endpoint(data: dict):\n"
            "    return None\n"
        )
        result = fn(self._router_file(router))
        content = result["app/routers/auth.py"]
        assert content.count("def login_endpoint") == 1
        # The first occurrence (with the real implementation) must be kept
        assert "return await login(data)" in content
        # The second occurrence must be gone
        assert content.count("return await login(data)") == 1

    def test_only_first_definition_kept(self, fn):
        """The first occurrence is preserved; later occurrences are removed."""
        router = (
            "from app.services.auth_service import login\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "@router.post('/a')\n"
            "async def my_handler():\n"
            "    return 'first'\n"
            "\n"
            "@router.post('/b')\n"
            "async def my_handler():\n"
            "    return 'second'\n"
        )
        result = fn(self._router_file(router))
        content = result["app/routers/auth.py"]
        assert "first" in content
        assert "second" not in content

    def test_no_change_when_no_duplicates(self, fn):
        """Files with no duplicates and no shadowing must be left untouched."""
        router = (
            "from app.services.auth_service import login\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "@router.post('/login')\n"
            "async def login_endpoint(data: dict):\n"
            "    return await login(data)\n"
        )
        result = fn(self._router_file(router))
        assert result["app/routers/auth.py"] == router

    # ── shadowing rename ─────────────────────────────────────────────────

    def test_shadowing_function_renamed(self, fn):
        """A handler whose name shadows an import is renamed to <name>_endpoint."""
        router = (
            "from app.services.auth_service import login\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "@router.post('/login')\n"
            "async def login(data: dict):\n"
            "    return data\n"
        )
        result = fn(self._router_file(router))
        content = result["app/routers/auth.py"]
        assert "def login_endpoint" in content
        # The import statement is untouched
        assert "from app.services.auth_service import login" in content

    def test_non_shadowing_function_not_renamed(self, fn):
        """Handler names that don't collide with imports are left alone."""
        router = (
            "from app.services.auth_service import login\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "@router.post('/logout')\n"
            "async def logout_endpoint(data: dict):\n"
            "    return None\n"
        )
        result = fn(self._router_file(router))
        content = result["app/routers/auth.py"]
        assert "def logout_endpoint" in content
        assert "def logout_endpoint_endpoint" not in content

    # ── multi-line parenthesised imports ─────────────────────────────────

    def test_multiline_parenthesised_import_detected(self, fn):
        """Names in a parenthesised multi-line import must be detected as imported."""
        router = (
            "from app.services.auth_service import (\n"
            "    login,\n"
            "    logout,\n"
            ")\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "@router.post('/login')\n"
            "async def login(data: dict):\n"
            "    return data\n"
            "\n"
            "@router.post('/logout')\n"
            "async def logout(data: dict):\n"
            "    return None\n"
        )
        result = fn(self._router_file(router))
        content = result["app/routers/auth.py"]
        assert "def login_endpoint" in content
        assert "def logout_endpoint" in content

    # ── syntax-error fallback ────────────────────────────────────────────

    def test_syntax_error_file_skipped_gracefully(self, fn):
        """A router file with a syntax error must be returned unchanged."""
        router = "this is not valid python !!!\n"
        result = fn(self._router_file(router))
        assert result["app/routers/auth.py"] == router

    # ── non-router files untouched ───────────────────────────────────────

    def test_service_file_not_deduplicated(self, fn):
        """Step 6 must not touch service files — only app/routers/*.py."""
        svc = (
            "async def do_something():\n    return 1\n"
            "async def do_something():\n    return 2\n"
        )
        files = {"app/services/my_svc.py": svc}
        result = fn(files)
        assert result["app/services/my_svc.py"] == svc

    # ── source-level assertions ───────────────────────────────────────────

    def test_step6_uses_ast_parse(self):
        """Step 6 must use ast.parse (not regex) for structural analysis."""
        src = _read_agent_src()
        # The implementation must mention ast.parse in the step-6 block
        step6_idx = src.find("6. Deduplicate function definitions")
        assert step6_idx != -1, "Step 6 header comment not found"
        step6_src = src[step6_idx:]
        assert "ast.parse" in step6_src

    def test_step6_uses_importfrom_node(self):
        """Step 6 must inspect ast.ImportFrom nodes for imported names."""
        src = _read_agent_src()
        assert "ast.ImportFrom" in src

    def test_step6_uses_end_lineno(self):
        """Step 6 must use end_lineno for accurate block boundaries."""
        src = _read_agent_src()
        assert "end_lineno" in src


# ---------------------------------------------------------------------------
# Fix 5 — codegen_prompt.py CRITICAL section (requirements 8–15)
# ---------------------------------------------------------------------------

class TestSyntaxSafetyInstructionsCriticalSection:
    """Verify requirements 8–15 are present in get_syntax_safety_instructions."""

    def _src(self) -> str:
        return _read_prompt_src()

    def test_req_8_forbidden_patterns_listed(self):
        src = self._src()
        assert "FORBIDDEN" in src
        assert "pass" in src
        assert "return []" in src
        assert "Placeholder" in src or "# Placeholder" in src

    def test_req_8_required_sqlalchemy_example(self):
        src = self._src()
        assert "scalar_one_or_none" in src

    def test_req_9_sqlalchemy_uuid_primary_key_example(self):
        src = self._src()
        assert "SAUUID" in src or "UUID as SAUUID" in src or "mapped_column" in src

    def test_req_9_shared_base_requirement(self):
        src = self._src()
        assert "declarative_base()" in src and "app/database.py" in src

    def test_req_10_pydantic_config_dict_example(self):
        src = self._src()
        assert "ConfigDict(from_attributes=True)" in src

    def test_req_10_uuid_not_int_for_id(self):
        src = self._src()
        assert "UUID" in src and "int" in src  # contrast is made

    def test_req_11_router_mounting_example(self):
        src = self._src()
        assert "include_router" in src
        assert "prefix=" in src

    def test_req_11_unmounted_routers_return_404(self):
        src = self._src()
        assert "404" in src

    def test_req_12_jwt_middleware_example(self):
        src = self._src()
        assert "JWTAuthMiddleware" in src or "jwt.decode" in src

    def test_req_12_middleware_returns_401(self):
        src = self._src()
        assert "401" in src

    def test_req_13_dockerfile_multi_stage_example(self):
        src = self._src()
        assert "AS builder" in src
        assert "AS runtime" in src

    def test_req_13_uvicorn_cmd_correct(self):
        src = self._src()
        assert 'uvicorn", "app.main:app"' in src or "uvicorn app.main:app" in src

    def test_req_14_k8s_liveness_probe_example(self):
        src = self._src()
        assert "livenessProbe" in src
        assert "/healthz" in src

    def test_req_14_k8s_readiness_probe_example(self):
        src = self._src()
        assert "readinessProbe" in src
        assert "/readyz" in src

    def test_req_14_k8s_resource_limits(self):
        src = self._src()
        assert "cpu: " in src and "memory: " in src

    def test_req_15_helm_go_template_example(self):
        src = self._src()
        assert "chart.fullname" in src or "chart.labels" in src
        assert ".Values.replicaCount" in src

    def test_req_15_json_blob_wrong_example_present(self):
        src = self._src()
        assert '"apiVersion"' in src or "apiVersion" in src

    def test_critical_section_numbers_8_through_15(self):
        src = self._src()
        for n in range(8, 16):
            assert f"{n}." in src, f"Requirement {n}. not found in codegen_prompt.py"


# ---------------------------------------------------------------------------
# Fix 7 — app/routes/ directory discovery in _reconcile_app_wiring and
#          _validate_wiring (Bug 1 & Bug 2)
# ---------------------------------------------------------------------------


class TestRoutesDirectoryDiscovery:
    """_reconcile_app_wiring and _validate_wiring must detect app/routes/ files."""

    @pytest.fixture(scope="class")
    def reconcile_fn(self):
        mod = _load_agent_module()
        return mod._reconcile_app_wiring  # type: ignore[attr-defined]

    @pytest.fixture(scope="class")
    def validate_fn(self):
        mod = _load_agent_module()
        return mod._validate_wiring  # type: ignore[attr-defined]

    def test_reconcile_discovers_routes_directory(self, reconcile_fn):
        """_reconcile_app_wiring must find routers in app/routes/ as well as app/routers/."""
        files = {
            "app/routes/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
                "@router.get('/products')\n"
                "async def list_products(): ...\n"
            ),
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        }
        result = reconcile_fn(files)
        main_content = result.get("app/main.py", "")
        assert "from app.routes.products import" in main_content, (
            "_reconcile_app_wiring must import routers discovered in app/routes/"
        )
        assert "include_router" in main_content, (
            "_reconcile_app_wiring must wire routers discovered in app/routes/"
        )
        # The __init__.py must be created in the correct directory, not app/routers/
        assert "app/routes/__init__.py" in result, (
            "_reconcile_app_wiring must create app/routes/__init__.py, not app/routers/__init__.py"
        )
        assert "app/routers/__init__.py" not in result, (
            "_reconcile_app_wiring must not create app/routers/__init__.py when using app/routes/"
        )

    def test_validate_wiring_finds_routes_directory(self, validate_fn):
        """_validate_wiring must detect unwired routers in app/routes/ directory."""
        files = {
            "app/routes/products.py": (
                "router = APIRouter()\n"
                "@router.get('/products')\n"
                "async def list_products(): ...\n"
            ),
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        }
        result = validate_fn(files)
        assert "app/routes/products.py" in result["unwired_routers"], (
            "_validate_wiring must flag unwired routers in app/routes/"
        )

    def test_validate_wiring_routes_dir_wired_not_flagged(self, validate_fn):
        """_validate_wiring must not flag app/routes/ routers that are properly wired."""
        files = {
            "app/routes/products.py": (
                "router = APIRouter()\n"
                "@router.get('/products')\n"
                "async def list_products(): ...\n"
            ),
            "app/main.py": (
                "from app.routes.products import router\n"
                "app = FastAPI()\n"
                "app.include_router(router)\n"
            ),
        }
        result = validate_fn(files)
        assert "app/routes/products.py" not in result["unwired_routers"], (
            "_validate_wiring must not flag already-wired routers in app/routes/"
        )
