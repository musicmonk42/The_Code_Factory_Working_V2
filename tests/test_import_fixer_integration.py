# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration test for ImportFixerEngine wired into the codegen pipeline.
Validates that missing imports are automatically fixed during code generation.
"""

import pytest
from pathlib import Path
from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine


class TestImportFixerIntegration:
    """Integration tests for ImportFixerEngine in the pipeline."""

    def test_import_fixer_engine_can_be_imported(self):
        """Verify ImportFixerEngine can be imported from the pipeline."""
        from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine
        
        fixer = ImportFixerEngine()
        assert fixer is not None
        assert hasattr(fixer, 'fix_code')

    def test_integration_point_exists_in_omnicore_service(self):
        """Verify the import auto-fix integration point exists in omnicore_service.py."""
        omnicore_file = Path("server/services/omnicore_service.py")
        assert omnicore_file.exists(), "omnicore_service.py should exist"
        
        content = omnicore_file.read_text()
        
        # Verify import statement is present
        assert "from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine" in content, \
            "ImportFixerEngine should be imported in omnicore_service.py"
        
        # Verify the fixer is instantiated
        assert "fixer = ImportFixerEngine()" in content, \
            "ImportFixerEngine should be instantiated"
        
        # Verify files are being fixed (check for the improved logic)
        assert "if not filename.endswith('.py')" in content or "filename.endswith('.py')" in content, \
            "Should check for Python files"
        assert "fixer.fix_code(content" in content, \
            "Should call fix_code on file content"
        
        # Verify logging is present
        assert "[CODEGEN] Auto-fixed imports" in content, \
            "Should log when imports are auto-fixed"
        
        # Verify industry-standard error handling
        assert "try:" in content and "except" in content, \
            "Should have proper error handling"
        
        # Verify summary logging for observability
        assert "Import auto-fix summary" in content or "files_fixed" in content, \
            "Should have summary logging for observability"

    def test_retry_filter_includes_import_errors(self):
        """Verify the retry filter includes import errors as retriable."""
        omnicore_file = Path("server/services/omnicore_service.py")
        assert omnicore_file.exists(), "omnicore_service.py should exist"
        
        content = omnicore_file.read_text()
        
        # Verify import errors are detected
        assert "import_errors = [e for e in validation_errors if 'does not import' in e.lower() or 'but does not import' in e.lower()]" in content, \
            "Should detect import errors in validation errors"
        
        # Verify import errors are added to errors_for_retry
        assert "errors_for_retry = syntax_errors + import_errors" in content, \
            "Import errors should be added to errors_for_retry"

    def test_previous_error_instruction_includes_import_guidance(self):
        """Verify the previous_error instruction includes import guidance."""
        omnicore_file = Path("server/services/omnicore_service.py")
        assert omnicore_file.exists(), "omnicore_service.py should exist"
        
        content = omnicore_file.read_text()
        
        # Verify import guidance is in the instruction
        assert "6. Ensure all modules used (e.g., time, os, json) are properly imported at the top of the file" in content, \
            "Instruction should include import guidance"

    def test_pipeline_logs_import_errors(self):
        """Verify the pipeline logs import errors separately."""
        omnicore_file = Path("server/services/omnicore_service.py")
        assert omnicore_file.exists(), "omnicore_service.py should exist"
        
        content = omnicore_file.read_text()
        
        # Verify import_errors are logged
        assert '"import_errors": import_errors' in content, \
            "Should log import_errors in the warning message"

    def test_error_type_detection_includes_import_error(self):
        """Verify error type detection includes ImportError."""
        omnicore_file = Path("server/services/omnicore_service.py")
        assert omnicore_file.exists(), "omnicore_service.py should exist"
        
        content = omnicore_file.read_text()
        
        # Verify ImportError type is set
        assert 'error_type = "ImportError"' in content, \
            "Should set error_type to ImportError when import errors are detected"

    def test_import_fixer_handles_production_scenario(self):
        """Test the exact production scenario from the logs."""
        fixer = ImportFixerEngine()
        
        # The exact code from production logs
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
        
        result = fixer.fix_code(code)
        
        # Should succeed and fix the missing import
        assert result["status"] == "success"
        assert "import time" in result["fixed_code"]
        assert len(result["fixes_applied"]) > 0
        assert any("time" in fix for fix in result["fixes_applied"])

    def test_import_fixer_preserves_correct_code(self):
        """Test that fixer doesn't break already-correct code."""
        fixer = ImportFixerEngine()
        
        code = """import time
from fastapi import FastAPI

app = FastAPI()

def get_timestamp():
    return time.time()
"""
        
        result = fixer.fix_code(code)
        
        # Should succeed without changes
        assert result["status"] == "success"
        # May have minor whitespace differences but should be functionally identical
        assert "import time" in result["fixed_code"]
        assert "from fastapi import FastAPI" in result["fixed_code"]


def _load_is_third_party_import_error():
    """Load _is_third_party_import_error from omnicore_service.py without
    triggering its heavy module-level imports, using source extraction."""
    import re as _re
    import importlib.util as _util

    src_path = Path("server/services/omnicore_service.py")
    source = src_path.read_text(encoding="utf-8")

    # Extract the standalone function definition (ends at the next top-level def/class)
    func_match = _re.search(
        r"(def _is_third_party_import_error\b.*?)(?=\n\nclass |\n\ndef )",
        source,
        _re.DOTALL,
    )
    assert func_match, "_is_third_party_import_error not found in omnicore_service.py"
    func_src = func_match.group(1)

    # Compile and execute in an isolated namespace that has access to `re`
    namespace = {"re": _re}
    exec(compile(func_src, "<omnicore_service>", "exec"), namespace)  # nosec B102
    return namespace["_is_third_party_import_error"]


class TestThirdPartyImportErrorGuard:
    """Behavioural tests for the _is_third_party_import_error() guard."""

    @pytest.fixture(scope="class")
    def fn(self):
        return _load_is_third_party_import_error()

    def test_third_party_module_returns_true(self, fn):
        """asyncstdlib is a third-party package — guard should return True."""
        assert fn("ModuleNotFoundError: No module named 'asyncstdlib'") is True

    def test_httpx_is_third_party(self, fn):
        """httpx is a third-party package — guard should return True."""
        assert fn("ModuleNotFoundError: No module named 'httpx'") is True

    def test_project_local_app_returns_false(self, fn):
        """app.services is project-local — guard should return False."""
        assert fn("ModuleNotFoundError: No module named 'app.services.auth'") is False

    def test_server_module_returns_false(self, fn):
        """server module is project-local — guard should return False."""
        assert fn("ModuleNotFoundError: No module named 'server.routes'") is False

    def test_non_import_error_returns_false(self, fn):
        """SyntaxError is not a module import error — guard should return False."""
        assert fn("SyntaxError: invalid syntax at line 42") is False

    def test_empty_string_returns_false(self, fn):
        """Empty error string should safely return False."""
        assert fn("") is False

    def test_importfixer_guard_code_present(self):
        """The guard is wired into the ImportFixer section of _run_full_pipeline."""
        content = Path("server/services/omnicore_service.py").read_text()
        assert "all(_is_third_party_import_error(e) for e in _last_val_errors)" in content, \
            "Guard should use all() with _last_val_errors"
        assert "Skipping ImportFixer" in content, \
            "Guard should log 'Skipping ImportFixer' message"
