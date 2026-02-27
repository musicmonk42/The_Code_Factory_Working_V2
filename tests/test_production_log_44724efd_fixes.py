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


# ---------------------------------------------------------------------------
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
