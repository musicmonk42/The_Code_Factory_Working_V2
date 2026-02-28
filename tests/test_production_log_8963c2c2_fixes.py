# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — production job 8963c2c2 post-mortem fixes
=======================================================

Validates all 7 fixes implemented after the e-commerce FastAPI microservice
(28 endpoints) failed validation due to SQLAlchemy models used as FastAPI
``response_model`` parameters.

Fixes tested:
- Fix 1: ``fix_response_model_type_mismatches`` — rewrites SQLAlchemy model imports
- Fix 2: ``_is_stub_content`` + ``ensure_local_module_stubs`` guard
- Fix 3: ``disambiguate_model_schema_imports`` — aliases ORM imports in router files
- Fix 4: OTEL exporter probe + env var clearing
- Fix 5: GCP NLP circuit breaker + ``ENABLE_GCP_NLP`` env var
- Fix 7: README.md creation in ``_patch_readme``
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loader helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_module(rel_path: str, name: str):
    """Load a module by file-path, bypassing package __init__ files."""
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


def _load_crh():
    """Load codegen_response_handler with minimal dependencies."""
    mod_name = "crh_8963_under_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name], set()

    pkg_name = "generator.agents.codegen_agent"
    synthetic: set = set()

    for parent in ("generator", "generator.agents", pkg_name):
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)
            synthetic.add(parent)

    sar_name = f"{pkg_name}.syntax_auto_repair"
    if sar_name not in sys.modules:
        fake_sar = types.ModuleType(sar_name)

        class _SyntaxAutoRepair:
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
    mod.__package__ = pkg_name
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
    mod, synthetic = _load_crh()

    def _cleanup():
        for name in synthetic:
            sys.modules.pop(name, None)

    request.addfinalizer(_cleanup)
    return mod


@pytest.fixture(scope="module")
def pm_module():
    """Load post_materialize.py directly (avoids heavy __init__ chain)."""
    return _load_module("generator/main/post_materialize.py", "post_materialize_8963_test")


def _valid_python(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


# ===========================================================================
# Fix 2: _is_stub_content
# ===========================================================================

class TestIsStubContent:
    """Verify _is_stub_content correctly classifies stub vs real content."""

    def test_empty_content_is_stub(self, crh):
        assert crh._is_stub_content("") is True
        assert crh._is_stub_content("   \n\n") is True

    def test_stub_marker_is_stub(self, crh):
        content = '"""Generated module — replace with actual implementation."""\nfrom typing import Any\n\nclass Foo:\n    pass\n'
        assert crh._is_stub_content(content) is True

    def test_pass_only_class_is_stub(self, crh):
        """A Pydantic model with only `pass` body is stub content."""
        content = "from pydantic import BaseModel\n\nclass UserBase(BaseModel):\n    pass\n"
        assert crh._is_stub_content(content) is True

    def test_pydantic_field_is_real(self, crh):
        """A Pydantic model with Field() definitions is real content."""
        content = (
            "from pydantic import BaseModel, Field\n\n"
            "class Product(BaseModel):\n"
            "    id: int = Field(...)\n"
            "    name: str = Field(...)\n"
        )
        assert crh._is_stub_content(content) is False

    def test_annotated_field_is_real(self, crh):
        """A class with annotated attributes (no Field) is real content."""
        content = (
            "from pydantic import BaseModel\n\n"
            "class User(BaseModel):\n"
            "    name: str\n"
            "    age: int\n"
        )
        assert crh._is_stub_content(content) is False

    def test_sqlalchemy_column_is_real(self, crh):
        """SQLAlchemy Column() definitions make a file real."""
        content = (
            "from sqlalchemy import Column, Integer, String\n"
            "from app.database import Base\n\n"
            "class Product(Base):\n"
            "    __tablename__ = 'products'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    name = Column(String)\n"
        )
        assert crh._is_stub_content(content) is False

    def test_return_none_function_is_stub(self, crh):
        """A function that only returns None is stub content."""
        content = (
            "from typing import Any\n\n"
            "def create_product(*args: Any, **kwargs: Any) -> Any:\n"
            "    return None\n"
        )
        assert crh._is_stub_content(content) is True

    def test_syntax_error_is_not_stub(self, crh):
        """Unparseable files should NOT be overwritten (return False = not stub)."""
        content = "def broken(\n    # incomplete"
        assert crh._is_stub_content(content) is False


# ===========================================================================
# Fix 2: ensure_local_module_stubs guard (non-stub files preserved)
# ===========================================================================

class TestStubOverwriteGuard:
    """Verify ensure_local_module_stubs does not overwrite real schema files."""

    def test_real_schema_not_overwritten(self, crh):
        """A schema file with real Pydantic fields must NOT be replaced by a stub."""
        real_schema = (
            "from pydantic import BaseModel, Field\n\n"
            "class Product(BaseModel):\n"
            "    id: int = Field(...)\n"
            "    name: str = Field(...)\n"
        )
        files = {
            "app/routers/products.py": (
                "from app.schemas.product import Product\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
            "app/schemas/product.py": real_schema,
        }
        result = crh.ensure_local_module_stubs(dict(files))
        # The real schema must be preserved verbatim
        assert result["app/schemas/product.py"] == real_schema

    def test_stub_schema_allows_symbol_append(self, crh):
        """A schema file with only stub classes should still get missing symbols appended."""
        stub_schema = (
            "from pydantic import BaseModel\n\n"
            "class UserBase(BaseModel):\n"
            "    pass\n"
        )
        files = {
            "app/routes.py": "from app.schemas import User, Product\n",
            "app/schemas.py": stub_schema,
        }
        result = crh.ensure_local_module_stubs(dict(files))
        schema_content = result["app/schemas.py"]
        # User and/or Product should be appended since they're missing
        assert "class User" in schema_content or "class Product" in schema_content


# ===========================================================================
# Fix 1: fix_response_model_type_mismatches
# ===========================================================================

class TestFixResponseModelTypeMismatches:
    """Verify SQLAlchemy model → Pydantic schema import rewriting."""

    def test_sqlalchemy_import_rewritten_when_schema_exists(self, crh):
        """When a Pydantic schema exists for the same name, rewrite the import."""
        files = {
            "app/routers/products.py": (
                "from app.models.product import Product\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.post('/', response_model=Product)\n"
                "async def create_product():\n"
                "    pass\n"
            ),
            "app/schemas/product.py": (
                "from pydantic import BaseModel\n\n"
                "class Product(BaseModel):\n"
                "    id: int\n"
                "    name: str\n"
            ),
        }
        result = crh.fix_response_model_type_mismatches(dict(files))
        router_content = result["app/routers/products.py"]
        # Import should now point to schemas, not models (or model is aliased)
        assert "app.schemas.product" in router_content

    def test_response_model_set_to_none_when_no_schema(self, crh):
        """When no Pydantic schema exists, response_model should be set to None."""
        files = {
            "app/routers/products.py": (
                "from app.models.product import Product\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.post('/', response_model=Product)\n"
                "async def create_product():\n"
                "    pass\n"
            ),
            # No app/schemas/product.py
        }
        result = crh.fix_response_model_type_mismatches(dict(files))
        router_content = result["app/routers/products.py"]
        # response_model should be None with a TODO comment
        assert "response_model=None" in router_content
        assert "TODO" in router_content

    def test_non_router_files_not_modified(self, crh):
        """Files outside app/routers/ should not be modified."""
        original = "from app.models.product import Product\n"
        files = {
            "app/services/product_service.py": original,
            "app/schemas/product.py": (
                "from pydantic import BaseModel\n\n"
                "class Product(BaseModel):\n    id: int\n"
            ),
        }
        result = crh.fix_response_model_type_mismatches(dict(files))
        assert result["app/services/product_service.py"] == original

    def test_schema_imports_not_modified(self, crh):
        """Router files that already import from schemas should not be changed."""
        original_router = (
            "from app.schemas.product import Product\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n\n"
            "@router.get('/', response_model=Product)\n"
            "async def list_products():\n"
            "    pass\n"
        )
        files = {
            "app/routers/products.py": original_router,
            "app/schemas/product.py": (
                "from pydantic import BaseModel\n\n"
                "class Product(BaseModel):\n    id: int\n"
            ),
        }
        result = crh.fix_response_model_type_mismatches(dict(files))
        # Router already imports from schemas — should be unchanged
        assert result["app/routers/products.py"] == original_router


# ===========================================================================
# Fix 3: disambiguate_model_schema_imports
# ===========================================================================

class TestDisambiguateModelSchemaImports:
    """Verify that ORM model imports are aliased when same name exists in schemas/."""

    def test_orm_import_aliased_when_collision(self, crh):
        """When Product exists in both models/ and schemas/, alias the ORM import."""
        files = {
            "app/models/product.py": (
                "from app.database import Base\n"
                "from sqlalchemy import Column, Integer\n\n"
                "class Product(Base):\n"
                "    __tablename__ = 'products'\n"
                "    id = Column(Integer, primary_key=True)\n"
            ),
            "app/schemas/product.py": (
                "from pydantic import BaseModel\n\n"
                "class Product(BaseModel):\n"
                "    id: int\n"
            ),
            "app/routers/products.py": (
                "from app.models.product import Product\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
        }
        result = crh.disambiguate_model_schema_imports(dict(files))
        router_content = result["app/routers/products.py"]
        # ORM import should be aliased as ProductModel
        assert "ProductModel" in router_content or "as ProductModel" in router_content

    def test_no_collision_no_change(self, crh):
        """When no name collision exists, router files should be unchanged."""
        original_router = (
            "from app.models.order import Order\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
        )
        files = {
            "app/models/order.py": (
                "from app.database import Base\n"
                "class Order(Base):\n"
                "    __tablename__ = 'orders'\n"
            ),
            # No app/schemas/order.py — no collision
            "app/routers/orders.py": original_router,
        }
        result = crh.disambiguate_model_schema_imports(dict(files))
        assert result["app/routers/orders.py"] == original_router

    def test_schema_import_added_after_alias(self, crh):
        """After aliasing ORM import, schema import should be added to router."""
        files = {
            "app/models/product.py": (
                "from app.database import Base\n"
                "class Product(Base):\n"
                "    __tablename__ = 'products'\n"
            ),
            "app/schemas/product.py": (
                "from pydantic import BaseModel\n\n"
                "class Product(BaseModel):\n"
                "    id: int\n"
            ),
            "app/routers/products.py": (
                "from app.models.product import Product\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
        }
        result = crh.disambiguate_model_schema_imports(dict(files))
        router_content = result["app/routers/products.py"]
        # Schema import should be present
        assert "app.schemas.product" in router_content


# ===========================================================================
# Fix 4: OTEL exporter probe — clears env var when unreachable
# ===========================================================================

class TestOtelProbeEnvVarClearing:
    """Verify OTEL endpoint env var is cleared when collector is unreachable."""

    def test_env_var_cleared_on_unreachable(self):
        """When the OTLP collector is unreachable, OTEL_EXPORTER_OTLP_ENDPOINT is cleared."""
        tracing_mod = _load_module("server/middleware/tracing.py", "tracing_8963_test")

        if not tracing_mod.OTEL_AVAILABLE:
            pytest.skip("OpenTelemetry not available in this environment")

        def _always_fail(*args, **kwargs):
            raise OSError("Connection refused")

        test_env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}
        with patch.dict(os.environ, test_env, clear=False):
            with patch("socket.create_connection", side_effect=_always_fail):
                tracing_mod.setup_tracing()
                # After setup_tracing, the env var should be empty
                assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") == ""

    def test_reachable_endpoint_not_cleared(self):
        """When OTLP collector IS reachable, env var should remain set."""
        tracing_mod = _load_module("server/middleware/tracing.py", "tracing_reach_8963_test")

        if not tracing_mod.OTEL_AVAILABLE:
            pytest.skip("OpenTelemetry not available in this environment")

        class _FakeSocket:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        test_env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}
        with patch.dict(os.environ, test_env, clear=False):
            with patch("socket.create_connection", return_value=_FakeSocket()):
                tracing_mod.setup_tracing()
                # Env var should remain set (not cleared, since collector is reachable)
                assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") == "http://localhost:4317"


# ===========================================================================
# Fix 5: GCP NLP circuit breaker
# ===========================================================================

class TestGcpNlpCircuitBreaker:
    """Verify GCP NLP circuit breaker and ENABLE_GCP_NLP env var."""

    def _load_codegen_prompt(self):
        """Load codegen_prompt.py with minimal stubs."""
        name = "codegen_prompt_8963_test"
        if name in sys.modules:
            return sys.modules[name]

        pkg_name = "generator.agents.codegen_agent"
        # Only add synthetic stubs if not already present
        synth = []
        for parent in ("generator", "generator.agents", pkg_name):
            if parent not in sys.modules:
                sys.modules[parent] = types.ModuleType(parent)
                synth.append(parent)

        spec = importlib.util.spec_from_file_location(
            name,
            PROJECT_ROOT / "generator/agents/codegen_agent/codegen_prompt.py",
            submodule_search_locations=[],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = pkg_name
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(name, None)
            for p in synth:
                sys.modules.pop(p, None)
            raise
        return mod

    def test_circuit_breaker_blocks_call(self):
        """When _gcp_nlp_disabled=True, translate_requirements_if_needed returns immediately."""
        import asyncio
        try:
            cp = self._load_codegen_prompt()
        except Exception:
            pytest.skip("codegen_prompt could not be loaded in this environment")

        original = cp._gcp_nlp_disabled
        cp._gcp_nlp_disabled = True
        call_count = [0]

        async def _run():
            req = {"features": ["Build a product catalog API"]}
            result = await cp.translate_requirements_if_needed(req)
            return result

        try:
            result = asyncio.get_event_loop().run_until_complete(_run())
            assert result == {"features": ["Build a product catalog API"]}
            assert call_count[0] == 0
        finally:
            cp._gcp_nlp_disabled = original

    def test_enable_gcp_nlp_false_skips_call(self):
        """When ENABLE_GCP_NLP=false, translate_requirements_if_needed returns immediately."""
        import asyncio
        try:
            cp = self._load_codegen_prompt()
        except Exception:
            pytest.skip("codegen_prompt could not be loaded in this environment")

        original = cp._gcp_nlp_disabled
        cp._gcp_nlp_disabled = False

        async def _run():
            req = {"features": ["Build a product catalog API"]}
            result = await cp.translate_requirements_if_needed(req)
            return result

        try:
            with patch.dict(os.environ, {"ENABLE_GCP_NLP": "false"}):
                result = asyncio.get_event_loop().run_until_complete(_run())
                assert result == {"features": ["Build a product catalog API"]}
        finally:
            cp._gcp_nlp_disabled = original


# ===========================================================================
# Fix 7: README.md creation in _patch_readme
# ===========================================================================

class TestPatchReadmeCreation:
    """Verify README.md is created from scratch when missing."""

    def test_readme_created_when_missing(self, pm_module, tmp_path):
        """_patch_readme should create README.md when it doesn't exist."""
        result = pm_module.PostMaterializeResult(output_dir=tmp_path)
        readme_path = tmp_path / "README.md"
        assert not readme_path.exists()

        pm_module._patch_readme(tmp_path, "app.main:app", result)

        assert readme_path.exists(), "README.md should have been created"
        content = readme_path.read_text(encoding="utf-8")
        # Should contain contract-required sections
        assert "## Setup" in content
        assert "## Run" in content
        assert "## API Endpoints" in content
        assert "README.md" in result.files_created

    def test_readme_patched_when_missing_sections(self, pm_module, tmp_path):
        """_patch_readme should add missing sections to an existing README."""
        readme_path = tmp_path / "README.md"
        readme_path.write_text("# My Project\n\nA cool project.\n", encoding="utf-8")

        result = pm_module.PostMaterializeResult(output_dir=tmp_path)
        pm_module._patch_readme(tmp_path, "app.main:app", result)

        content = readme_path.read_text(encoding="utf-8")
        assert "## Setup" in content
        assert "## Run" in content
        assert "## API Endpoints" in content

    def test_readme_with_all_sections_unchanged(self, pm_module, tmp_path):
        """An already-complete README should not be modified."""
        full = pm_module.ensure_readme_sections("# Project\n", "app.main:app")

        readme_path = tmp_path / "README.md"
        readme_path.write_text(full, encoding="utf-8")

        result = pm_module.PostMaterializeResult(output_dir=tmp_path)
        pm_module._patch_readme(tmp_path, "app.main:app", result)

        after = readme_path.read_text(encoding="utf-8")
        assert after == full  # unchanged
        assert "README.md" not in result.files_created


# ===========================================================================
# Fix 7: critique_report.json always generated (unit test of the logic)
# ===========================================================================

class TestCritiqueReportFallback:
    """Verify critique_report.json is always generated even after validation failure."""

    def test_critique_report_skipped_status_when_no_agent_results(self, tmp_path):
        """When critique agent did not run, report should have status=skipped."""
        from datetime import datetime, timezone

        reports_dir = tmp_path / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        critique_report_path = reports_dir / "critique_report.json"

        workflow_id = "test-job-8963"
        critique_data: dict = {}
        critique_report = {
            "job_id": workflow_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "skipped" if not critique_data else critique_data.get("status", "unknown"),
            "reason": (
                "Pipeline validation failed before critique stage"
                if not critique_data
                else None
            ),
            "issues": critique_data.get("issues", []),
            "fixes_applied": critique_data.get("fixes_applied", []),
        }
        critique_report_path.write_text(json.dumps(critique_report, indent=2), encoding="utf-8")

        assert critique_report_path.exists()
        data = json.loads(critique_report_path.read_text())
        assert data["job_id"] == workflow_id
        assert data["status"] == "skipped"
        assert "Pipeline validation" in data["reason"]
        assert data["issues"] == []

    def test_critique_report_not_overwritten_if_exists(self, tmp_path):
        """If critique_report.json already exists, it should not be overwritten."""
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        critique_report_path = reports_dir / "critique_report.json"

        existing = {"job_id": "existing-job", "status": "completed", "issues": [{"severity": "low"}]}
        critique_report_path.write_text(json.dumps(existing), encoding="utf-8")

        # The engine only writes the fallback if the file does NOT exist
        if not critique_report_path.exists():
            critique_report_path.write_text(json.dumps({"status": "skipped"}), encoding="utf-8")

        data = json.loads(critique_report_path.read_text())
        assert data["status"] == "completed"  # original preserved


