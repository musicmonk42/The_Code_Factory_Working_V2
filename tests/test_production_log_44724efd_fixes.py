# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for 4 production bug fixes identified in job log 44724efd.

Fix 1: ImportFixerEngine self-import guard
Fix 2: SIEM & SNS plugin encode() null guard
Fix 3: Endpoint coverage normalization with include_router() prefix
Fix 4: Contract validator schema package layout flexibility
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fix 1: ImportFixerEngine self-import guard
# ---------------------------------------------------------------------------

class TestImportFixerSelfImportGuard:
    """Verify that fix_code does not add self-imports."""

    def setup_method(self):
        from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine
        self.fixer = ImportFixerEngine()

    def test_self_import_is_skipped(self):
        """Symbol defined in the same file must NOT be re-imported."""
        code = """from fastapi import APIRouter

router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok"}
"""
        project_symbol_map = {
            "router": ("app.routers.health", "router"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/routers/health.py",
            project_symbol_map=project_symbol_map,
        )
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        # The self-import must NOT be added
        assert "from app.routers.health import router" not in fixed

    def test_cross_module_import_still_added(self):
        """Symbol from a different module should still be imported."""
        code = """from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
"""
        project_symbol_map = {
            "router": ("app.routers.health", "router"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/main.py",
            project_symbol_map=project_symbol_map,
        )
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        assert "from app.routers.health import router" in fixed

    def test_init_py_self_import_skipped(self):
        """__init__.py files: module path computed without trailing .__init__."""
        code = """from pydantic import BaseModel

class Item(BaseModel):
    name: str
"""
        project_symbol_map = {
            "Item": ("app.schemas", "Item"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/schemas/__init__.py",
            project_symbol_map=project_symbol_map,
        )
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        # "Item" is defined in app.schemas, so no self-import
        assert "from app.schemas import Item" not in fixed

    def test_no_file_path_no_skip(self):
        """Without a file_path, self-import guard is disabled and imports are added."""
        code = """from fastapi import APIRouter

router = APIRouter()
"""
        project_symbol_map = {
            "router": ("app.routers.health", "router"),
        }
        # Without file_path, the guard cannot compute _self_module.
        # "router" is already in imported_names indirectly via APIRouter(),
        # but the logic checks imported_names for the *name* "router".
        # Since router is assigned (not imported), it won't be in imported_names.
        # The guard should still NOT add self-import when file_path is None,
        # because _self_module will be None -> no filtering -> imports added.
        # This test just verifies no crash occurs.
        result = self.fixer.fix_code(
            code,
            file_path=None,
            project_symbol_map=project_symbol_map,
        )
        assert result["status"] == "success"

    def test_locally_defined_assignment_skipped(self):
        """Symbol defined via top-level assignment must NOT be re-imported."""
        code = """Base = declarative_base()
async_session = sessionmaker()
"""
        project_symbol_map = {
            "Base": ("app.database", "Base"),
            "async_session": ("app.database", "async_session"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/database.py",
            project_symbol_map=project_symbol_map,
        )
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        assert "from app.database import Base" not in fixed
        assert "from app.database import async_session" not in fixed

    def test_absolute_path_self_import_skipped(self):
        """Absolute file paths are normalised to skip self-imports correctly."""
        code = """Base = None
async_session = None
"""
        project_symbol_map = {
            "Base": ("app.database", "Base"),
            "async_session": ("app.database", "async_session"),
        }
        # Simulates a post-materialisation pass with an absolute path.
        result = self.fixer.fix_code(
            code,
            file_path="/app/generated/my_app/app/database.py",
            project_symbol_map=project_symbol_map,
        )
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        assert "from app.database import Base" not in fixed
        assert "from app.database import async_session" not in fixed

    def test_cross_sibling_router_import_skipped(self):
        """router defined in a sibling router file must NOT be imported."""
        code = """from fastapi import APIRouter

router = APIRouter()

@router.get("/auth/login")
async def login():
    return {}
"""
        project_symbol_map = {
            # Simulate: another router file registered `router` last
            "router": ("app.routers.product", "router"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/routers/auth.py",
            project_symbol_map=project_symbol_map,
        )
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        assert "from app.routers.product import router" not in fixed



# Fix 2: SIEM & SNS plugin encode() null guard
# ---------------------------------------------------------------------------

_SIEM_PLUGIN_PATH = Path(__file__).parent.parent / "self_fixing_engineer/plugins/siem_plugin/siem_plugin.py"
_SNS_PLUGIN_PATH = Path(__file__).parent.parent / "self_fixing_engineer/plugins/sns_plugin/sns_plugin.py"


class TestSiemPluginNullGuard:
    """Verify the SIEM plugin AuditJsonFormatter and WAL null guard are present."""

    def setup_method(self):
        self._content = _SIEM_PLUGIN_PATH.read_text()

    def test_audit_formatter_uses_conditional_encode(self):
        """AuditJsonFormatter uses conditional encode instead of chained .encode()."""
        import re
        # Must NOT have the old anti-pattern: SECRETS_MANAGER.get_secret(...).encode()
        assert not re.search(
            r'SECRETS_MANAGER\.get_secret\s*\([^)]*"SIEM_AUDIT_LOG_HMAC_KEY"[^)]*\)\s*\.encode\s*\(\)',
            self._content,
        ), "SIEM_AUDIT_LOG_HMAC_KEY get_secret() must not chain .encode() directly"
        # Must have the null-safe pattern
        assert "_hmac_key_value.encode() if _hmac_key_value is not None else None" in self._content

    def test_audit_formatter_null_guard_variable(self):
        """AuditJsonFormatter stores intermediate _hmac_key_value."""
        assert '_hmac_key_value = SECRETS_MANAGER.get_secret(' in self._content
        assert '"SIEM_AUDIT_LOG_HMAC_KEY"' in self._content

    def test_add_fields_guards_hmac(self):
        """add_fields guards HMAC computation with None check."""
        assert 'if self._hmac_key is not None:' in self._content

    def test_wal_hmac_uses_bytes_fallback(self):
        """PersistentWALQueue WAL HMAC uses b'' fallback instead of crashing."""
        assert '"SIEM_WAL_HMAC_KEY"' in self._content
        assert '_wal_hmac_value.encode() if _wal_hmac_value is not None else b""' in self._content


class TestSnsPluginNullGuard:
    """Verify the SNS plugin AuditJsonFormatter and WAL null guard are present."""

    def setup_method(self):
        self._content = _SNS_PLUGIN_PATH.read_text()

    def test_audit_formatter_uses_conditional_encode(self):
        """AuditJsonFormatter uses conditional encode instead of chained .encode()."""
        import re
        assert not re.search(
            r'SECRETS_MANAGER\.get_secret\s*\([^)]*"SNS_AUDIT_LOG_HMAC_KEY"[^)]*\)\s*\.encode\s*\(\)',
            self._content,
        ), "SNS_AUDIT_LOG_HMAC_KEY get_secret() must not chain .encode() directly"
        assert "_hmac_key_value.encode() if _hmac_key_value is not None else None" in self._content

    def test_audit_formatter_null_guard_variable(self):
        """AuditJsonFormatter stores intermediate _hmac_key_value."""
        assert '_hmac_key_value = SECRETS_MANAGER.get_secret(' in self._content
        assert '"SNS_AUDIT_LOG_HMAC_KEY"' in self._content

    def test_add_fields_guards_hmac(self):
        """add_fields guards HMAC computation with None check."""
        assert 'if self._hmac_key is not None:' in self._content

    def test_wal_hmac_uses_bytes_fallback(self):
        """PersistentWALQueue WAL HMAC uses b'' fallback instead of crashing."""
        assert '"SNS_WAL_HMAC_KEY"' in self._content
        assert '_wal_hmac_value.encode() if _wal_hmac_value is not None else b""' in self._content


# ---------------------------------------------------------------------------
# Fix 3: Endpoint coverage with include_router() prefix
# ---------------------------------------------------------------------------

_CODEGEN_AGENT_PATH = (
    Path(__file__).parent.parent / "generator/agents/codegen_agent/codegen_agent.py"
)


class TestExtractRoutesIncludeRouter:
    """_extract_routes_from_files applies include_router() prefixes from main.py."""

    def setup_method(self):
        self._content = _CODEGEN_AGENT_PATH.read_text()

    def test_second_pass_present(self):
        """Second pass to read include_router() prefixes is present."""
        assert "Second pass: apply include_router() prefixes from app/main.py" in self._content
        assert '_main_content = files.get("app/main.py", "")' in self._content

    def test_helper_functions_exist(self):
        """Helper functions for parsing router prefixes are defined."""
        assert "def _parse_router_instance_prefixes(" in self._content
        assert "def _parse_include_router_prefixes(" in self._content
        assert "def _extract_route_entries(" in self._content

    def test_include_prefixes_mapping_built(self):
        """_parse_include_router_prefixes handles include_router calls."""
        assert "_include_prefixes" in self._content
        assert '_func.attr == "include_router"' in self._content

    def test_additional_routes_added(self):
        """Additional routes from include_router prefixes are added to result."""
        assert "routes.add((_method, _prefixed))" in self._content
        assert "_include_prefixes" in self._content

    @staticmethod
    def _load_route_helpers() -> dict:
        """Compile and return the pure-stdlib helper functions from codegen_agent.py.

        Extracts only the four route-extraction functions (which depend solely on
        ``ast``, ``re``, and the standard typing module) and executes them in an
        isolated namespace, avoiding the heavy third-party imports that the full
        module requires.
        """
        import ast as _ast
        import re as _re
        from typing import Dict, FrozenSet, List, Set, Tuple

        source = _CODEGEN_AGENT_PATH.read_text()
        tree = _ast.parse(source)

        _TARGET_FUNCS = frozenset({
            "_parse_router_instance_prefixes",
            "_parse_include_router_prefixes",
            "_extract_route_entries",
            "_extract_routes_from_files",
        })
        _TARGET_CONSTS = frozenset({"_HTTP_ROUTE_METHODS", "_PATH_PARAM_WILDCARD"})

        # Collect only the nodes we need: the two module-level constants and the
        # four helper functions.  All other nodes (imports, classes, etc.) are
        # deliberately excluded so that no heavy import is executed.
        # Note: _HTTP_ROUTE_METHODS uses an annotated assignment (ast.AnnAssign).
        nodes = []
        for node in tree.body:
            if isinstance(node, _ast.Assign):
                names = {t.id for t in node.targets if isinstance(t, _ast.Name)}
                if names & _TARGET_CONSTS:
                    nodes.append(node)
            elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                if node.target.id in _TARGET_CONSTS:
                    nodes.append(node)
            elif isinstance(node, _ast.FunctionDef) and node.name in _TARGET_FUNCS:
                nodes.append(node)

        module = _ast.Module(body=nodes, type_ignores=[])
        _ast.fix_missing_locations(module)
        code = compile(module, str(_CODEGEN_AGENT_PATH), "exec")
        ns: dict = {
            "ast": _ast, "re": _re,
            "Dict": Dict, "FrozenSet": FrozenSet,
            "List": List, "Set": Set, "Tuple": Tuple,
        }
        exec(code, ns)  # noqa: S102  (trusted internal source)
        return ns

    def _run_extract(self, files):
        """Call the actual production _extract_routes_from_files via compiled source."""
        ns = self._load_route_helpers()
        return ns["_extract_routes_from_files"](files)

    def test_include_router_prefix_applied(self):
        """Routes without inline prefix get the include_router prefix from main.py."""
        router_code = """from fastapi import APIRouter

router = APIRouter()

@router.get("/products")
async def list_products():
    return []
"""
        main_code = """from fastapi import FastAPI

app = FastAPI()
app.include_router(router, prefix="/api/v1")
"""
        files = {
            "app/routers/products.py": router_code,
            "app/main.py": main_code,
        }
        routes = self._run_extract(files)
        assert ("GET", "/api/v1/products") in routes

    def test_combined_prefix(self):
        """Routes with both APIRouter prefix and include_router prefix are combined."""
        router_code = """from fastapi import APIRouter

router = APIRouter(prefix="/items")

@router.get("/list")
async def list_items():
    return []
"""
        main_code = """from fastapi import FastAPI

app = FastAPI()
app.include_router(router, prefix="/api/v1")
"""
        files = {
            "app/routers/items.py": router_code,
            "app/main.py": main_code,
        }
        routes = self._run_extract(files)
        assert ("GET", "/api/v1/items/list") in routes

    def test_no_include_router_unchanged(self):
        """When main.py has no include_router prefix, routes are unchanged."""
        router_code = """from fastapi import APIRouter

router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
"""
        main_code = """from fastapi import FastAPI
app = FastAPI()
app.include_router(router)
"""
        files = {
            "app/routers/health.py": router_code,
            "app/main.py": main_code,
        }
        routes = self._run_extract(files)
        assert ("GET", "/health") in routes

    def test_no_main_py_no_crash(self):
        """When main.py is absent, no crash occurs."""
        router_code = """from fastapi import APIRouter
router = APIRouter()

@router.get("/ping")
async def ping():
    return {}
"""
        files = {"app/routers/ping.py": router_code}
        routes = self._run_extract(files)
        assert ("GET", "/ping") in routes


# ---------------------------------------------------------------------------
# Fix 4: Contract validator schema package layout
# ---------------------------------------------------------------------------

class TestContractValidatorSchemaFlexibility:
    """check_output_structure and check_schema_validation accept schemas/ package."""

    def _make_output_dir(self, tmp_path, flat_schema=True, package_schema=False):
        """Create a minimal output directory for testing."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "reports").mkdir()
        (app_dir / "main.py").write_text("# main\n")
        (app_dir / "routes.py").write_text("# routes\n")
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        (tmp_path / "README.md").write_text("# README\n")

        if flat_schema:
            (app_dir / "schemas.py").write_text(
                "from pydantic import BaseModel, field_validator\n\nclass Item(BaseModel):\n    name: str\n\n    @field_validator('name')\n    def validate_name(cls, v): return v\n"
            )
        if package_schema:
            schemas_dir = app_dir / "schemas"
            schemas_dir.mkdir()
            (schemas_dir / "__init__.py").write_text("")
            (schemas_dir / "item.py").write_text(
                "from pydantic import BaseModel, field_validator\n\nclass Item(BaseModel):\n    name: str\n\n    @field_validator('name')\n    def validate_name(cls, v): return v\n"
            )
        return tmp_path

    def _make_validator(self, output_dir):
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from validate_contract_compliance import ContractValidator
        return ContractValidator(output_dir)

    def test_flat_schema_still_works(self, tmp_path):
        """Flat app/schemas.py layout passes check_output_structure."""
        output_dir = self._make_output_dir(tmp_path, flat_schema=True)
        checker = self._make_validator(output_dir)
        # Should not raise
        checker.check_output_structure()

    def test_package_schema_passes_structure_check(self, tmp_path):
        """Package app/schemas/ layout passes check_output_structure."""
        output_dir = self._make_output_dir(tmp_path, flat_schema=False, package_schema=True)
        checker = self._make_validator(output_dir)
        # Should not raise
        checker.check_output_structure()

    def test_missing_schemas_raises(self, tmp_path):
        """When neither schemas.py nor schemas/ exists, raises AssertionError."""
        output_dir = self._make_output_dir(tmp_path, flat_schema=False, package_schema=False)
        checker = self._make_validator(output_dir)
        with pytest.raises(AssertionError, match="schemas"):
            checker.check_output_structure()

    def test_flat_schema_validation_check(self, tmp_path):
        """check_schema_validation works with flat schemas.py."""
        output_dir = self._make_output_dir(tmp_path, flat_schema=True)
        checker = self._make_validator(output_dir)
        checker.check_schema_validation()

    def test_package_schema_validation_check(self, tmp_path):
        """check_schema_validation works with schemas/ package."""
        output_dir = self._make_output_dir(tmp_path, flat_schema=False, package_schema=True)
        checker = self._make_validator(output_dir)
        checker.check_schema_validation()

    def test_missing_schemas_validation_raises(self, tmp_path):
        """check_schema_validation raises when no schemas found."""
        output_dir = self._make_output_dir(tmp_path, flat_schema=False, package_schema=False)
        checker = self._make_validator(output_dir)
        with pytest.raises(AssertionError, match="schemas"):
            checker.check_schema_validation()


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fix 5 (Bug 3): Pydantic stub class inheritance
# ---------------------------------------------------------------------------

_CODEGEN_RESPONSE_HANDLER_PATH = (
    Path(__file__).parent.parent
    / "generator/agents/codegen_agent/codegen_response_handler.py"
)


def _load_stub_helpers() -> dict:
    """Compile and return stub-related helper functions from codegen_response_handler.py.

    Extracts only the constants and functions needed by ``ensure_local_module_stubs``
    and executes them in an isolated namespace, avoiding the heavy third-party imports
    that the full module requires (aiohttp, opentelemetry, redis, etc.).
    """
    import ast as _ast
    import re as _re
    from typing import Dict, Set

    source = _CODEGEN_RESPONSE_HANDLER_PATH.read_text()
    tree = _ast.parse(source)

    _TARGET_FUNCS = frozenset({
        "_is_router_variable",
        "_is_likely_variable",
        "_is_stub_content",
        "_classify_stub_module",
        "_pluralize_tablename",
        "_render_stub_template",
        "ensure_local_module_stubs",
    })
    _TARGET_CONSTS = frozenset({
        "_LOCAL_IMPORT_RE", "_APP_SUBMODULE_IMPORT_RE", "_PYDANTIC_CLASS_SUFFIXES",
        "_REAL_CONTENT_PATTERNS", "_VARIABLE_SUFFIXES",
        "_ROUTER_VARIABLE_PATTERNS", "_STUB_TABLENAME_IRREGULARS",
    })

    nodes = []
    for node in tree.body:
        if isinstance(node, _ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, _ast.Name)}
            if names & _TARGET_CONSTS:
                nodes.append(node)
        elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
            if node.target.id in _TARGET_CONSTS:
                nodes.append(node)
        elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            if node.name in _TARGET_FUNCS:
                nodes.append(node)

    module = _ast.Module(body=nodes, type_ignores=[])
    _ast.fix_missing_locations(module)
    code = compile(module, str(_CODEGEN_RESPONSE_HANDLER_PATH), "exec")
    import logging as _logging
    from pathlib import Path as _Path
    from typing import Any as _Any, List as _List, Optional as _Optional, Tuple as _Tuple
    ns: dict = {
        "ast": _ast, "re": _re,
        "Dict": Dict, "Set": Set,
        "logging": _logging,
        "logger": _logging.getLogger("codegen_response_handler"),
        "Path": _Path,
        "Any": _Any,
        "List": _List,
        "Optional": _Optional,
        "Tuple": _Tuple,
        # Disable Jinja2 in the isolated namespace so _render_stub_template
        # always returns None and the inline generation path is used.
        "HAS_JINJA2": False,
        "_STUB_TEMPLATE_ENV": None,
        "_STUB_TEMPLATES_DIR": _Path("/nonexistent/stubs"),
        "__file__": str(_CODEGEN_RESPONSE_HANDLER_PATH),
    }
    exec(code, ns)  # noqa: S102  (trusted internal source)
    return ns


class TestPydanticStubClassInheritance:
    """Verify ensure_local_module_stubs generates BaseModel stubs for schema/model paths."""

    def setup_method(self):
        ns = _load_stub_helpers()
        self.ensure_local_module_stubs = ns["ensure_local_module_stubs"]

    def test_schema_path_gets_basemodel(self):
        """Class stubs in app/schemas/common.py inherit from BaseModel."""
        # A router file imports ErrorResponse from app.schemas.common, but that
        # module is missing — ensure_local_module_stubs should create a stub
        # with BaseModel inheritance.
        code_files = {
            "app/routers/auth.py": (
                "from app.schemas.common import ErrorResponse, SuccessResponse\n\n"
                "def login(): pass\n"
            ),
        }
        result = self.ensure_local_module_stubs(code_files)
        assert "app/schemas/common.py" in result
        content = result["app/schemas/common.py"]
        assert "class ErrorResponse(BaseModel):" in content
        assert "class SuccessResponse(BaseModel):" in content
        assert "from pydantic import BaseModel" in content

    def test_response_suffix_gets_basemodel(self):
        """Class stubs whose names end in 'Response' get BaseModel regardless of path."""
        code_files = {
            "app/routers/items.py": (
                "from app.utils.helpers import ErrorResponse, SomeHelper\n\n"
                "def get(): pass\n"
            ),
        }
        result = self.ensure_local_module_stubs(code_files)
        assert "app/utils/helpers.py" in result
        content = result["app/utils/helpers.py"]
        assert "class ErrorResponse(BaseModel):" in content
        assert "from pydantic import BaseModel" in content
        assert "class SomeHelper:" in content

    def test_models_path_gets_basemodel(self):
        """Class stubs in app/models/user.py inherit from BaseModel."""
        code_files = {
            "app/routers/users.py": (
                "from app.models.user import UserCreate\n\n"
                "def create(): pass\n"
            ),
        }
        result = self.ensure_local_module_stubs(code_files)
        assert "app/models/user.py" in result
        content = result["app/models/user.py"]
        assert "class UserCreate(BaseModel):" in content
        assert "from pydantic import BaseModel" in content

    def test_non_schema_plain_class_unchanged(self):
        """Class stubs in non-schema paths without Pydantic suffixes stay as plain classes."""
        code_files = {
            "app/routers/utils.py": (
                "from app.utils.helpers import MyHelper\n\n"
                "def do(): pass\n"
            ),
        }
        result = self.ensure_local_module_stubs(code_files)
        assert "app/utils/helpers.py" in result
        content = result["app/utils/helpers.py"]
        assert "class MyHelper:" in content
        assert "BaseModel" not in content

    def test_existing_file_appended_with_basemodel(self):
        """Supplemental stubs appended to existing schema files use BaseModel."""
        existing = (
            "from pydantic import BaseModel\n\n"
            "class Existing(BaseModel):\n    pass\n"
        )
        code_files = {
            "app/schemas/common.py": existing,
            "app/routers/auth.py": (
                "from app.schemas.common import ErrorResponse\n\ndef login(): pass\n"
            ),
        }
        result = self.ensure_local_module_stubs(code_files)
        content = result["app/schemas/common.py"]
        assert "class ErrorResponse(BaseModel):" in content
        # BaseModel import must not be duplicated since it was already present
        assert content.count("from pydantic import BaseModel") == 1
