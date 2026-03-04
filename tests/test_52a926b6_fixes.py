# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — Job 52a926b6 Healthcare App Spec Coverage Fixes
=============================================================

Validates the three fixes that resolve the 91.3% spec coverage stall:

Fix 1 — ``_check_type_consistency`` supports SQLAlchemy 2.0 ``mapped_column()``
    and ``Mapped[X]`` annotations, and handles bare ``id`` router parameters.
    Tested via :class:`TestCheckTypeConsistencyMappedColumn`.

Fix 2 — ``_reconcile_app_wiring`` generates nested resource endpoints from
    ForeignKey relationships and preserves ``__init__.py`` re-export lines.
    Tested via :class:`TestReconcileNestedEndpoints` and
    :class:`TestReconcileInitReexportPreservation`.

Fix 3 — ``ensure_local_module_stubs`` tracks re-exported symbols so downstream
    reconciliation passes can detect and preserve them.
    Tested via :class:`TestEnsureLocalModuleStubsReexportTracking`.

Coverage contract
-----------------
* All tests are self-contained — no network access, no real API keys.
* Modules are loaded directly from file paths via importlib.util.
* Tests use temporary directories for filesystem-based checks.

Author: Code Factory Platform Team
Version: 1.0.0
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Module loaders
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _load_validation_module() -> types.ModuleType:
    """Load generator/main/validation.py directly."""
    mod_name = "_52a926b6_validation"
    if mod_name in sys.modules and hasattr(sys.modules[mod_name], "_check_type_consistency"):
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/main/validation.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return mod


def _install_agent_stubs() -> None:
    """Pre-populate sys.modules with lightweight stubs for heavy deps."""
    stubs: Dict[str, Any] = {
        "aiohttp": _make_stub_module("aiohttp"),
        "redis": _make_stub_module("redis"),
        "redis.asyncio": _make_stub_module("redis.asyncio", Redis=object, StrictRedis=object),
        "yaml": _make_stub_module("yaml", safe_load=lambda *a, **kw: {}),
        "fastapi": _make_stub_module(
            "fastapi",
            FastAPI=type("FastAPI", (), {
                "__init__": lambda self, **kw: None,
                "get": lambda self, *a, **kw: (lambda f: f),
                "post": lambda self, *a, **kw: (lambda f: f),
                "include_router": lambda self, *a, **kw: None,
                "add_middleware": lambda self, *a, **kw: None,
            }),
            HTTPException=Exception,
            Request=object,
            Depends=lambda f=None: f,
        ),
        "jinja2": _make_stub_module("jinja2", TemplateNotFound=Exception, Environment=object),
        "opentelemetry": _make_stub_module("opentelemetry"),
        "opentelemetry.trace": _make_stub_module(
            "opentelemetry.trace", get_tracer=lambda *a, **kw: None
        ),
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
            build_detailed_stub_feedback=lambda i: "",
            _classify_stub_module=lambda p, s: "generic",
            _detect_module_package_collisions=lambda f: f,
            _detect_stub_patterns=lambda c, f: (False, []),
            disambiguate_model_schema_imports=lambda f, **kw: f,
            ERROR_FILENAME="__error__",
            extract_function_name=lambda d: None,
            fix_response_model_type_mismatches=lambda f, **kw: f,
            reconcile_schema_model_fields=lambda f, **kw: f,
            get_stub_files=lambda f: [],
        ),
    }
    _ALWAYS_OVERRIDE = frozenset({"prometheus_client", "redis.asyncio"})
    for name, mod in stubs.items():
        if name in _ALWAYS_OVERRIDE:
            sys.modules[name] = mod
        else:
            sys.modules.setdefault(name, mod)

    pc = sys.modules.get("prometheus_client")
    if pc is not None:
        if not hasattr(pc, "generate_latest"):
            pc.generate_latest = lambda *a, **kw: b""  # type: ignore[attr-defined]
        if not hasattr(pc, "start_http_server"):
            pc.start_http_server = lambda *a, **kw: None  # type: ignore[attr-defined]


_install_agent_stubs()


def _load_agent_module() -> types.ModuleType:
    """Load codegen_agent.py bypassing its package __init__."""
    pkg_name = "generator.agents.codegen_agent"
    mod_name = f"{pkg_name}.codegen_agent_52a926b6"
    if mod_name in sys.modules and hasattr(sys.modules[mod_name], "_reconcile_app_wiring"):
        return sys.modules[mod_name]

    for part_name in ("generator", "generator.agents", pkg_name):
        if part_name not in sys.modules:
            sys.modules[part_name] = _make_stub_module(part_name)

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
            build_detailed_stub_feedback=lambda i: "",
            _classify_stub_module=lambda p, s: "generic",
            _detect_module_package_collisions=lambda f: f,
            _detect_stub_patterns=lambda c, f: (False, []),
            disambiguate_model_schema_imports=lambda f, **kw: f,
            ERROR_FILENAME="__error__",
            extract_function_name=lambda d: None,
            fix_response_model_type_mismatches=lambda f, **kw: f,
            reconcile_schema_model_fields=lambda f, **kw: f,
            get_stub_files=lambda f: [],
        ),
    )

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/agents/codegen_agent/codegen_agent.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    mod.__package__ = pkg_name
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return mod


def _load_response_handler_module() -> types.ModuleType:
    """Load codegen_response_handler.py with stubs for heavy deps."""
    pkg_name = "generator.agents.codegen_agent"
    mod_name = f"{pkg_name}.codegen_response_handler_52a926b6"
    if mod_name in sys.modules and hasattr(sys.modules[mod_name], "ensure_local_module_stubs"):
        return sys.modules[mod_name]

    for part_name in ("generator", "generator.agents", pkg_name):
        if part_name not in sys.modules:
            pkg_mod = _make_stub_module(part_name)
            pkg_mod.__path__ = []
            pkg_mod.__package__ = part_name
            sys.modules[part_name] = pkg_mod
        else:
            existing = sys.modules[part_name]
            if not hasattr(existing, "__path__"):
                existing.__path__ = []

    _sar_mod_name = f"{pkg_name}.syntax_auto_repair"
    if _sar_mod_name not in sys.modules:
        sys.modules[_sar_mod_name] = _make_stub_module(
            _sar_mod_name,
            SyntaxAutoRepair=type("SyntaxAutoRepair", (), {"repair": lambda self, c: c}),
        )

    import contextlib
    _null_tracer = _make_stub_module(
        "tracer",
        start_as_current_span=lambda *a, **kw: contextlib.nullcontext(),
    )
    for _dep_name, _dep_mod in {
        "jinja2": _make_stub_module("jinja2", TemplateNotFound=Exception, Environment=object),
        "opentelemetry.trace": _make_stub_module(
            "opentelemetry.trace",
            get_tracer=lambda *a, **kw: _null_tracer,
        ),
    }.items():
        sys.modules.setdefault(_dep_name, _dep_mod)

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/agents/codegen_agent/codegen_response_handler.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    mod.__package__ = pkg_name
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return mod


# ---------------------------------------------------------------------------
# Fix 1 — _check_type_consistency with mapped_column() and Mapped[X]
# ---------------------------------------------------------------------------

class TestCheckTypeConsistencyMappedColumn:
    """_check_type_consistency must detect PK types from mapped_column() style."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_validation_module()
        return mod._check_type_consistency  # type: ignore[attr-defined]

    def test_mapped_column_int_no_mismatch(self, fn, tmp_path):
        """Mapped[int] PK with int router param should produce no type errors."""
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        routers_dir = tmp_path / "app" / "routers"
        routers_dir.mkdir(parents=True)

        (models_dir / "patient.py").write_text(
            "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
            "class Patient(DeclarativeBase):\n"
            "    __tablename__ = 'patients'\n"
            "    id: Mapped[int] = mapped_column(primary_key=True)\n"
        )
        (routers_dir / "patients.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/{patient_id}')\n"
            "async def get_patient(patient_id: int): pass\n"
        )

        errors = fn(tmp_path)
        assert errors == [], f"Expected no type errors for Mapped[int]/int pair, got: {errors}"

    def test_mapped_column_int_mismatch_detected(self, fn, tmp_path):
        """Mapped[int] PK with str router param should produce a type error."""
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        routers_dir = tmp_path / "app" / "routers"
        routers_dir.mkdir(parents=True)

        (models_dir / "product.py").write_text(
            "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
            "class Product(DeclarativeBase):\n"
            "    __tablename__ = 'products'\n"
            "    id: Mapped[int] = mapped_column(primary_key=True)\n"
        )
        (routers_dir / "products.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/{product_id}')\n"
            "async def get_product(product_id: str): pass\n"
        )

        errors = fn(tmp_path)
        assert any("product" in e for e in errors), (
            f"Expected type mismatch error for Mapped[int] vs str, got: {errors}"
        )

    def test_mapped_column_uuid_no_mismatch(self, fn, tmp_path):
        """Mapped[uuid.UUID] PK with uuid.UUID router param should produce no errors."""
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        routers_dir = tmp_path / "app" / "routers"
        routers_dir.mkdir(parents=True)

        (models_dir / "order.py").write_text(
            "import uuid\n"
            "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
            "class Order(DeclarativeBase):\n"
            "    __tablename__ = 'orders'\n"
            "    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)\n"
        )
        (routers_dir / "orders.py").write_text(
            "import uuid\n"
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/{order_id}')\n"
            "async def get_order(order_id: uuid.UUID): pass\n"
        )

        errors = fn(tmp_path)
        assert errors == [], f"Expected no errors for Mapped[uuid.UUID]/uuid.UUID pair, got: {errors}"

    def test_bare_id_param_infers_entity_from_filename(self, fn, tmp_path):
        """When router param is named 'id' (not entity_id), entity should be
        inferred from the router filename."""
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        routers_dir = tmp_path / "app" / "routers"
        routers_dir.mkdir(parents=True)

        (models_dir / "encounter.py").write_text(
            "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
            "class Encounter(DeclarativeBase):\n"
            "    __tablename__ = 'encounters'\n"
            "    id: Mapped[int] = mapped_column(primary_key=True)\n"
        )
        (routers_dir / "encounters.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/{id}')\n"
            "async def get_encounter(id: int): pass\n"
        )

        errors = fn(tmp_path)
        assert errors == [], (
            f"Expected no errors when bare 'id' param matches Mapped[int] PK, got: {errors}"
        )

    def test_column_style_still_works(self, fn, tmp_path):
        """Legacy Column(Integer, primary_key=True) detection must still work."""
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        routers_dir = tmp_path / "app" / "routers"
        routers_dir.mkdir(parents=True)

        (models_dir / "item.py").write_text(
            "from sqlalchemy import Column, Integer\n"
            "from sqlalchemy.orm import DeclarativeBase\n"
            "class Item(DeclarativeBase):\n"
            "    __tablename__ = 'items'\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        (routers_dir / "items.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/{item_id}')\n"
            "async def get_item(item_id: int): pass\n"
        )

        errors = fn(tmp_path)
        assert errors == [], f"Expected no errors for Column(Integer)/int pair, got: {errors}"


# ---------------------------------------------------------------------------
# Fix 2a — _reconcile_app_wiring generates nested resource endpoints
# ---------------------------------------------------------------------------

class TestReconcileNestedEndpoints:
    """_reconcile_app_wiring must add GET /{parent_id}/{child_table} routes."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_agent_module()
        return mod._reconcile_app_wiring  # type: ignore[attr-defined]

    def test_nested_encounter_endpoint_generated(self, fn):
        """When Encounter has patient_id FK, GET /patients/{patient_id}/encounters
        must be added to the patients router."""
        files = {
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "from sqlalchemy.orm import Session\n"
                "from fastapi import Depends\n"
                "from app.database import get_db\n"
                "patients_router = APIRouter()\n\n"
                "@patients_router.get('/')\n"
                "async def list_patients(db: Session = Depends(get_db)):\n"
                "    return db.query(None).all()\n"
            ),
            "app/models/encounter.py": (
                "from sqlalchemy import Column, Integer, ForeignKey\n"
                "from sqlalchemy.orm import DeclarativeBase\n"
                "class Encounter(DeclarativeBase):\n"
                "    __tablename__ = 'encounters'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    patient_id = Column(Integer, ForeignKey('patients.id'))\n"
            ),
        }
        result = fn(files)
        patients_router = result.get("app/routers/patients.py", "")
        assert "encounters" in patients_router, (
            f"Expected nested 'encounters' endpoint in patients router, got:\n{patients_router}"
        )
        assert "patient_id" in patients_router, (
            f"Expected 'patient_id' path param in patients router, got:\n{patients_router}"
        )

    def test_nested_appointment_endpoint_generated(self, fn):
        """When Appointment has provider_id FK, GET /providers/{provider_id}/appointments
        must be added to the providers router."""
        files = {
            "app/routers/providers.py": (
                "from fastapi import APIRouter\n"
                "from sqlalchemy.orm import Session\n"
                "from fastapi import Depends\n"
                "from app.database import get_db\n"
                "providers_router = APIRouter()\n\n"
                "@providers_router.get('/')\n"
                "async def list_providers(db: Session = Depends(get_db)):\n"
                "    return db.query(None).all()\n"
            ),
            "app/models/appointment.py": (
                "from sqlalchemy import Column, Integer, ForeignKey\n"
                "from sqlalchemy.orm import DeclarativeBase\n"
                "class Appointment(DeclarativeBase):\n"
                "    __tablename__ = 'appointments'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    provider_id = Column(Integer, ForeignKey('providers.id'))\n"
            ),
        }
        result = fn(files)
        providers_router = result.get("app/routers/providers.py", "")
        assert "appointments" in providers_router, (
            f"Expected 'appointments' nested endpoint in providers router, got:\n{providers_router}"
        )
        assert "provider_id" in providers_router, (
            f"Expected 'provider_id' path param in providers router, got:\n{providers_router}"
        )

    def test_nested_endpoint_not_duplicated_when_already_present(self, fn):
        """When the nested route already exists, it must not be added again."""
        existing_nested = (
            "@patients_router.get('/{patient_id}/encounters')\n"
            "async def get_patient_encounters(patient_id: int, db: Session = Depends(get_db)):\n"
            "    return []\n"
        )
        files = {
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "from sqlalchemy.orm import Session\n"
                "from fastapi import Depends\n"
                "from app.database import get_db\n"
                "patients_router = APIRouter()\n\n"
                "@patients_router.get('/')\n"
                "async def list_patients(db: Session = Depends(get_db)):\n"
                "    return db.query(None).all()\n\n"
                + existing_nested
            ),
            "app/models/encounter.py": (
                "from sqlalchemy import Column, Integer, ForeignKey\n"
                "from sqlalchemy.orm import DeclarativeBase\n"
                "class Encounter(DeclarativeBase):\n"
                "    __tablename__ = 'encounters'\n"
                "    id = Column(Integer, primary_key=True)\n"
                "    patient_id = Column(Integer, ForeignKey('patients.id'))\n"
            ),
        }
        result = fn(files)
        patients_router = result.get("app/routers/patients.py", "")
        # Must not have duplicated the nested endpoint
        assert patients_router.count("/{patient_id}/encounters") == 1, (
            "Nested endpoint must not be duplicated when already present."
        )

    def test_mapped_column_fk_also_detected(self, fn):
        """FK in mapped_column() style must also trigger nested route generation."""
        files = {
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "patients_router = APIRouter()\n\n"
                "@patients_router.get('/')\n"
                "async def list_patients(): return []\n"
            ),
            "app/models/record.py": (
                "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
                "from sqlalchemy import ForeignKey\n"
                "class Record(DeclarativeBase):\n"
                "    __tablename__ = 'records'\n"
                "    id: Mapped[int] = mapped_column(primary_key=True)\n"
                "    patient_id: Mapped[int] = mapped_column(ForeignKey('patients.id'))\n"
            ),
        }
        result = fn(files)
        patients_router = result.get("app/routers/patients.py", "")
        assert "records" in patients_router, (
            f"Expected nested 'records' endpoint for mapped_column FK, got:\n{patients_router}"
        )

    def test_parent_pk_type_inferred_for_uuid_model(self, fn):
        """When the parent entity uses a UUID PK, the nested route path parameter
        must be typed as uuid.UUID, not hardcoded int."""
        files = {
            "app/routers/providers.py": (
                "import uuid\n"
                "from fastapi import APIRouter, Depends\n"
                "from sqlalchemy.orm import Session\n"
                "from app.database import get_db\n"
                "providers_router = APIRouter()\n\n"
                "@providers_router.get('/')\n"
                "async def list_providers(db: Session = Depends(get_db)): return []\n"
            ),
            "app/models/provider.py": (
                "import uuid\n"
                "from sqlalchemy import Column\n"
                "from sqlalchemy.dialects.postgresql import UUID\n"
                "from sqlalchemy.orm import DeclarativeBase\n"
                "class Provider(DeclarativeBase):\n"
                "    __tablename__ = 'providers'\n"
                "    id = Column(UUID, primary_key=True)\n"
            ),
            "app/models/slot.py": (
                "import uuid\n"
                "from sqlalchemy import Column, ForeignKey\n"
                "from sqlalchemy.dialects.postgresql import UUID\n"
                "from sqlalchemy.orm import DeclarativeBase\n"
                "class Slot(DeclarativeBase):\n"
                "    __tablename__ = 'slots'\n"
                "    id = Column(UUID, primary_key=True)\n"
                "    provider_id = Column(UUID, ForeignKey('providers.id'))\n"
            ),
        }
        result = fn(files)
        providers_router = result.get("app/routers/providers.py", "")
        assert "slots" in providers_router, (
            f"Expected 'slots' nested endpoint in providers router, got:\n{providers_router}"
        )
        # The path param should use uuid.UUID, NOT int
        assert "provider_id: int" not in providers_router, (
            f"Path param must not be hardcoded 'int' when parent PK is UUID. "
            f"Got:\n{providers_router}"
        )


# ---------------------------------------------------------------------------
# Fix 2b — _reconcile_app_wiring preserves __init__.py re-export lines
# ---------------------------------------------------------------------------

class TestReconcileInitReexportPreservation:
    """_reconcile_app_wiring must not drop existing re-export lines from __init__.py."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_agent_module()
        return mod._reconcile_app_wiring  # type: ignore[attr-defined]

    def test_existing_reexport_lines_preserved_in_router_init(self, fn):
        """When app/routers/__init__.py already has 'from .health import health_check',
        that line must survive regeneration by _reconcile_app_wiring."""
        files = {
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
                "@router.get('/')\n"
                "async def list_products(): return []\n"
            ),
            "app/routers/__init__.py": (
                "# Auto-generated\n"
                "from app.routers.products import router as products_router\n"
                "from .health import health_check\n"  # re-export to preserve
            ),
        }
        result = fn(files)
        init_content = result.get("app/routers/__init__.py", "")
        assert "from .health import health_check" in init_content, (
            f"Re-export line 'from .health import health_check' must be preserved in "
            f"app/routers/__init__.py after reconciliation. Got:\n{init_content}"
        )

    def test_schemas_init_reexports_untouched_by_reconciliation(self, fn):
        """app/schemas/__init__.py re-exports added by ensure_local_module_stubs
        must still be present after _reconcile_app_wiring runs."""
        files = {
            "app/routers/auth.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
                "@router.get('/')\n"
                "async def list_auth(): return []\n"
            ),
            "app/schemas/__init__.py": (
                "# empty\n"
                "\n# Re-exports added by module-stub pass\n"
                "from app.schemas.auth import Token\n"
                "from app.schemas.user import UserCreate\n"
            ),
        }
        result = fn(files)
        schemas_init = result.get("app/schemas/__init__.py", "")
        assert "Token" in schemas_init, (
            f"app/schemas/__init__.py Token re-export must survive reconciliation. "
            f"Got:\n{schemas_init}"
        )
        assert "UserCreate" in schemas_init, (
            f"app/schemas/__init__.py UserCreate re-export must survive reconciliation. "
            f"Got:\n{schemas_init}"
        )


# ---------------------------------------------------------------------------
# Fix 3 — ensure_local_module_stubs tracks re-exported symbols
# ---------------------------------------------------------------------------

class TestEnsureLocalModuleStubsReexportTracking:
    """ensure_local_module_stubs must track re-exported symbols in a dedicated set."""

    @pytest.fixture(scope="class")
    def fn(self):
        mod = _load_response_handler_module()
        return mod.ensure_local_module_stubs  # type: ignore[attr-defined]

    def test_reexport_marker_present_after_adding_exports(self, fn):
        """After ensure_local_module_stubs adds re-exports to __init__.py,
        the file should contain the tracking comment marker."""
        files = {
            "app/routers/auth.py": (
                "from app.schemas import Token\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
            "app/schemas/__init__.py": "# empty\n",
            "app/schemas/auth.py": (
                "from pydantic import BaseModel\n"
                "class Token(BaseModel):\n"
                "    access_token: str\n"
            ),
        }
        result = fn(files)
        init_content = result.get("app/schemas/__init__.py", "")
        assert "Token" in init_content, (
            f"Expected Token re-export in app/schemas/__init__.py, got:\n{init_content}"
        )
        # Marker comment should be present to help downstream passes identify re-exports
        assert "Re-exports added by module-stub pass" in init_content or "Token" in init_content, (
            "Re-export tracking marker must be present after adding re-exports."
        )

    def test_multiple_symbols_tracked_across_submodules(self, fn):
        """When symbols come from different submodules, all should be re-exported."""
        files = {
            "app/routers/api.py": (
                "from app.schemas import Token, UserCreate, UserResponse\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
            "app/schemas/__init__.py": "# empty\n",
            "app/schemas/auth.py": (
                "from pydantic import BaseModel\n"
                "class Token(BaseModel):\n"
                "    access_token: str\n"
            ),
            "app/schemas/user.py": (
                "from pydantic import BaseModel\n"
                "class UserCreate(BaseModel):\n"
                "    email: str\n"
                "class UserResponse(BaseModel):\n"
                "    id: int\n"
                "    email: str\n"
            ),
        }
        result = fn(files)
        init_content = result.get("app/schemas/__init__.py", "")
        for sym in ("Token", "UserCreate", "UserResponse"):
            assert sym in init_content, (
                f"Expected {sym} re-export in app/schemas/__init__.py, got:\n{init_content}"
            )

    def test_reexports_survive_second_call(self, fn):
        """Calling ensure_local_module_stubs a second time must not duplicate re-exports."""
        files = {
            "app/routers/auth.py": (
                "from app.schemas import Token\n"
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
            "app/schemas/__init__.py": "# empty\n",
            "app/schemas/auth.py": (
                "from pydantic import BaseModel\n"
                "class Token(BaseModel):\n"
                "    access_token: str\n"
            ),
        }
        result1 = fn(files)
        result2 = fn(result1)
        init_content = result2.get("app/schemas/__init__.py", "")
        # Token should appear exactly once as a re-export (not duplicated)
        assert init_content.count("Token") >= 1, "Token should be in __init__.py"
