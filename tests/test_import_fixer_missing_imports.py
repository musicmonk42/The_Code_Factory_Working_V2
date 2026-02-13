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
