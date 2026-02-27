# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test for ImportFixerEngine missing imports auto-fix functionality.
Tests the exact scenario from production logs where LLM generates code 
with missing imports like time.time() without import time.
"""

import pytest
from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine


class TestImportFixerMissingImports:
    """Test ImportFixerEngine's ability to auto-fix missing imports."""

    def setup_method(self):
        """Setup test fixtures."""
        self.fixer = ImportFixerEngine()

    def test_missing_time_import_production_scenario(self):
        """Test the exact scenario from production logs: time.time() without import time."""
        code = """from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware import Middleware
from app.routes import router

app = FastAPI()

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        # Should have import time added
        assert "import time" in fixed_code
        # Original code should still be present
        assert "time.time()" in fixed_code
        assert "async def add_process_time_header" in fixed_code

    def test_missing_os_import(self):
        """Test fixing missing os module import."""
        code = """def get_env_var():
    return os.getenv('MY_VAR')
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        assert "import os" in fixed_code
        assert "os.getenv" in fixed_code

    def test_missing_json_import(self):
        """Test fixing missing json module import."""
        code = """def load_config():
    with open('config.json') as f:
        return json.load(f)
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        assert "import json" in fixed_code
        assert "json.load" in fixed_code

    def test_multiple_missing_imports(self):
        """Test fixing multiple missing imports at once."""
        code = """def process_data():
    data = json.loads(os.getenv('DATA'))
    timestamp = time.time()
    return data, timestamp
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        # All three imports should be added
        assert "import json" in fixed_code
        assert "import os" in fixed_code
        assert "import time" in fixed_code

    def test_idempotent_already_correct_code(self):
        """Test that running fixer on already-correct code produces identical output."""
        code = """import time
import os

def get_timestamp():
    return time.time()
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        # Should be essentially the same (maybe whitespace differences)
        assert "import time" in fixed_code
        assert "import os" in fixed_code
        assert "time.time()" in fixed_code

    def test_syntax_error_graceful_handling(self):
        """Test that syntax errors are handled gracefully by returning original code."""
        code = """def broken_function(:
    return time.time(
"""
        result = self.fixer.fix_code(code)
        
        # Should return error status but with original code
        assert result["status"] == "error"
        assert result["fixed_code"] == code
        assert "Syntax error" in result["message"]

    def test_fastapi_specific_imports(self):
        """Test detection of FastAPI-specific names that are used but not imported."""
        code = """from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root(request: Request) -> Response:
    return Response(content="Hello")
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        # Should detect Request and Response are not imported
        # and add them to the from fastapi import line
        assert "Request" in fixed_code
        assert "Response" in fixed_code
        # Should have been added to existing import
        assert "from fastapi import" in fixed_code

    def test_datetime_import(self):
        """Test fixing missing datetime module import."""
        code = """def get_now():
    return datetime.datetime.now()
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        assert "import datetime" in fixed_code

    def test_pathlib_import(self):
        """Test fixing missing pathlib module import."""
        code = """def get_path():
    return pathlib.Path(__file__)
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        assert "import pathlib" in fixed_code

    def test_preserves_existing_imports(self):
        """Test that existing imports are preserved."""
        code = """import sys
from typing import Dict

def get_version():
    return sys.version
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        # Existing imports should remain
        assert "import sys" in fixed_code
        assert "from typing import Dict" in fixed_code

    def test_no_duplicate_imports(self):
        """Test that imports are not duplicated if already present."""
        code = """import time

def get_timestamp():
    return time.time()
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed_code = result["fixed_code"]
        
        # Should only have one import time
        assert fixed_code.count("import time") == 1

    def test_sys_module(self):
        """Test fixing missing sys module import."""
        code = """def exit_program():
    sys.exit(0)
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        assert "import sys" in result["fixed_code"]

    def test_re_module(self):
        """Test fixing missing re module import."""
        code = """def match_pattern(text):
    return re.match(r'\\d+', text)
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        assert "import re" in result["fixed_code"]

    def test_empty_code(self):
        """Test that empty code is handled gracefully."""
        result = self.fixer.fix_code("")
        
        assert result["status"] == "success"
        assert result["fixed_code"] == ""
        assert len(result["fixes_applied"]) == 0

    def test_whitespace_only_code(self):
        """Test that whitespace-only code is handled gracefully."""
        result = self.fixer.fix_code("   \n  \n  ")
        
        assert result["status"] == "success"
        assert len(result["fixes_applied"]) == 0

    def test_invalid_input_type(self):
        """Test that non-string input is handled gracefully."""
        result = self.fixer.fix_code(None)
        
        assert result["status"] == "error"
        assert "Invalid input" in result["message"]

    def test_code_with_module_docstring(self):
        """Test that imports are inserted after module docstring."""
        code = '''"""Module docstring."""

def f():
    return time.time()
'''
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        
        # Docstring should come first
        assert fixed.index('"""Module docstring."""') < fixed.index('import time')
        # Import should come before function
        assert fixed.index('import time') < fixed.index('def f()')

    def test_code_with_multiline_docstring(self):
        """Test handling of multi-line module docstrings."""
        code = '''"""
Module docstring
with multiple lines.
"""

def f():
    return os.getcwd()
'''
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        assert "import os" in result["fixed_code"]
        # Docstring should still be present
        assert '"""' in result["fixed_code"]

    def test_complex_attribute_access(self):
        """Test detection of chained attribute access."""
        code = """def get_path():
    return os.path.join('a', 'b')
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        assert "import os" in result["fixed_code"]

    def test_multiple_fastapi_imports_at_once(self):
        """Test adding multiple FastAPI imports when none exist."""
        code = """app = FastAPI()

@app.get("/")
def root(request: Request, response: Response):
    raise HTTPException(status_code=404)
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        fixed = result["fixed_code"]
        
        # Should have a from fastapi import line with all three
        assert "from fastapi import" in fixed
        assert "FastAPI" in fixed
        assert "Request" in fixed
        assert "Response" in fixed
        assert "HTTPException" in fixed

    def test_dry_run_mode(self):
        """Test that dry_run mode doesn't modify code."""
        code = """def f():
    return time.time()
"""
        result = self.fixer.fix_code(code, dry_run=True)
        
        assert result["status"] == "success"
        assert result["fixed_code"] == code  # Original unchanged
        assert len(result["fixes_applied"]) > 0  # But fixes were detected
        assert "Dry run" in result["message"]

    def test_logging_module(self):
        """Test fixing missing logging module import."""
        code = """def log_message():
    logging.info('test')
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        assert "import logging" in result["fixed_code"]

    def test_uuid_module(self):
        """Test fixing missing uuid module import."""
        code = """def gen_id():
    return uuid.uuid4()
"""
        result = self.fixer.fix_code(code)
        
        assert result["status"] == "success"
        assert "import uuid" in result["fixed_code"]

    def test_missing_uuid_symbol_import(self):
        """Test fixing missing UUID symbol import from uuid module."""
        code = """def get_id(order_id: UUID) -> str:
    return str(order_id)
"""
        result = self.fixer.fix_code(code)

        assert result["status"] == "success"
        assert "from uuid import UUID" in result["fixed_code"]

    def test_missing_path_symbol_import(self):
        """Test fixing missing Path symbol import from pathlib module."""
        code = """def get_path() -> Path:
    return Path('/tmp')
"""
        result = self.fixer.fix_code(code)

        assert result["status"] == "success"
        assert "from pathlib import Path" in result["fixed_code"]

    def test_missing_decimal_symbol_import(self):
        """Test fixing missing Decimal symbol import from decimal module."""
        code = """def calculate_price(amount: Decimal) -> Decimal:
    return amount * Decimal('1.1')
"""
        result = self.fixer.fix_code(code)

        assert result["status"] == "success"
        assert "from decimal import Decimal" in result["fixed_code"]

    def test_extend_existing_uuid_import(self):
        """Test extending existing uuid import with UUID symbol."""
        code = """from uuid import uuid4

def get_id(order_id: UUID) -> str:
    return str(uuid4())
"""
        result = self.fixer.fix_code(code)

        assert result["status"] == "success"
        # Should extend existing import, not add a new one
        assert result["fixed_code"].count("from uuid import") == 1
        assert "UUID" in result["fixed_code"]
        assert "uuid4" in result["fixed_code"]

    def test_multiple_stdlib_symbols(self):
        """Test fixing multiple stdlib symbol imports from different modules."""
        code = """def process(id: UUID, path: Path, amount: Decimal):
    return str(id), str(path), float(amount)
"""
        result = self.fixer.fix_code(code)

        assert result["status"] == "success"
        assert "from uuid import UUID" in result["fixed_code"]
        assert "from pathlib import Path" in result["fixed_code"]
        assert "from decimal import Decimal" in result["fixed_code"]


class TestCrossRouterImportGuard:
    """ImportFixerEngine must not inject cross-router service imports."""

    def setup_method(self):
        self.fixer = ImportFixerEngine()

    def test_cross_router_service_import_is_skipped(self):
        """auth_service defined in app.routers.auth must not be imported into orders.py."""
        code = (
            "from fastapi import APIRouter, Depends\n"
            "router = APIRouter()\n"
            "\n"
            "async def list_orders(user=Depends(auth_service.get_current_user)):\n"
            "    return []\n"
        )
        project_symbol_map = {
            "auth_service": ("app.routers.auth", "auth_service"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/routers/orders.py",
            project_symbol_map=project_symbol_map,
        )
        fixed = result["fixed_code"]
        assert "from app.routers.auth import auth_service" not in fixed, (
            "cross-router import of auth_service must be suppressed"
        )

    def test_service_instance_import_skipped_for_same_package_siblings(self):
        """order_service defined in a sibling router module must not be injected."""
        code = (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "async def checkout():\n"
            "    return order_service.process()\n"
        )
        project_symbol_map = {
            "order_service": ("app.routers.orders", "order_service"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/routers/checkout.py",
            project_symbol_map=project_symbol_map,
        )
        fixed = result["fixed_code"]
        assert "from app.routers.orders import order_service" not in fixed, (
            "cross-sibling service instance import must be suppressed"
        )

    def test_non_service_cross_router_import_also_skipped(self):
        """Any cross-router import (both under app.routers) must be skipped."""
        code = (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "async def some_view():\n"
            "    return helper_fn()\n"
        )
        project_symbol_map = {
            "helper_fn": ("app.routers.utils", "helper_fn"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/routers/orders.py",
            project_symbol_map=project_symbol_map,
        )
        fixed = result["fixed_code"]
        assert "from app.routers.utils import helper_fn" not in fixed, (
            "any cross-router import must be suppressed"
        )

    def test_cross_service_import_is_allowed(self):
        """Importing from app.services (not app.routers) must still be allowed."""
        code = (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "\n"
            "async def create_order():\n"
            "    return order_service.create()\n"
        )
        project_symbol_map = {
            "order_service": ("app.services.orders", "order_service"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/routers/checkout.py",
            project_symbol_map=project_symbol_map,
        )
        fixed = result["fixed_code"]
        assert "from app.services.orders import order_service" in fixed, (
            "import from app.services (not app.routers) must be allowed"
        )

    def test_auth_service_is_listed_in_common_vars_for_sibling_routers(self):
        """auth_service is listed in _COMMON_FRAMEWORK_VARS.

        This gives belt-and-suspenders blocking for same-package router siblings
        in addition to the cross-router ``startswith("app.routers.")`` guard.
        The test exercises the same-package sibling path (``_self_pkg == _target_pkg``)
        by using a project_symbol_map that resolves auth_service to the SAME package
        as the file under test.
        """
        code = (
            "from fastapi import APIRouter, Depends\n"
            "router = APIRouter()\n"
            "\n"
            "@router.get('/me')\n"
            "async def me(user=Depends(auth_service.get_current_user)):\n"
            "    return user\n"
        )
        # map auth_service to a sibling router module — same app.routers package
        project_symbol_map = {
            "auth_service": ("app.routers.auth", "auth_service"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/routers/orders.py",
            project_symbol_map=project_symbol_map,
        )
        assert "from app.routers.auth import auth_service" not in result["fixed_code"], (
            "auth_service must not be imported cross-router "
            "(blocked by cross-router guard AND _COMMON_FRAMEWORK_VARS)"
        )

    def test_non_router_same_package_service_import_not_blocked(self):
        """A same-package *non-router* sibling's service var should not be suppressed.

        The guard only applies to the app.routers package.  Sibling modules in
        app.services (for example) are allowed to import service helpers from each
        other.
        """
        code = (
            "from app.services.base import BaseService\n"
            "\n"
            "class ProductService(BaseService):\n"
            "    def list(self):\n"
            "        return catalog_service.all()\n"
        )
        project_symbol_map = {
            "catalog_service": ("app.services.catalog", "catalog_service"),
        }
        result = self.fixer.fix_code(
            code,
            file_path="app/services/products.py",
            project_symbol_map=project_symbol_map,
        )
        fixed = result["fixed_code"]
        # app.services.products importing from app.services.catalog is allowed.
        assert "from app.services.catalog import catalog_service" in fixed, (
            "same-package service import outside app.routers must not be blocked"
        )
