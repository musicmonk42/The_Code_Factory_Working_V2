# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the provenance tracking module.
"""

import json
import tempfile
from pathlib import Path

import pytest

from generator.main.provenance import (
    PipelineStage,
    ProvenanceTracker,
    extract_endpoints_from_code,
    extract_endpoints_from_md,
    run_fail_fast_validation,
    validate_deployment_artifacts,
    validate_docker_compose,
    validate_dockerfile,
    validate_has_content,
    validate_spec_fidelity,
    validate_syntax,
)


class TestProvenanceTracker:
    """Test ProvenanceTracker class."""

    def test_init_with_job_id(self):
        tracker = ProvenanceTracker(job_id="test-123")
        assert tracker.job_id == "test-123"
        assert tracker.stages == []
        assert tracker.artifacts == {}

    def test_init_generates_job_id(self):
        tracker = ProvenanceTracker()
        assert tracker.job_id.startswith("job-")

    def test_compute_sha256(self):
        content = "Hello, World!"
        sha256 = ProvenanceTracker.compute_sha256(content)
        # Verify it's a valid SHA256 hex string (64 chars)
        assert len(sha256) == 64
        assert all(c in '0123456789abcdef' for c in sha256)
        # Verify consistency
        assert sha256 == ProvenanceTracker.compute_sha256(content)

    def test_record_stage(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage(
            PipelineStage.READ_MD,
            artifacts={"input.md": "# Test"},
            metadata={"source": "test"}
        )
        
        assert len(tracker.stages) == 1
        assert tracker.stages[0]["stage"] == "READ_MD"
        assert "input.md" in tracker.stages[0]["artifacts"]

    def test_record_error(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_error("STAGE1", "TestError", "Test message")
        
        assert len(tracker.errors) == 1
        assert tracker.errors[0]["error_type"] == "TestError"

    def test_artifact_change_detection(self):
        tracker = ProvenanceTracker(job_id="test")
        
        tracker.record_stage("S1", artifacts={"file.py": "v1"})
        tracker.record_stage("S2", artifacts={"file.py": "v1"})
        assert not tracker.check_artifact_changed("file.py")
        
        tracker.record_stage("S3", artifacts={"file.py": "v2"})
        assert tracker.check_artifact_changed("file.py")

    def test_get_overwrites(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage("S1", artifacts={"a.py": "v1", "b.py": "stable"})
        tracker.record_stage("S2", artifacts={"a.py": "v2", "b.py": "stable"})
        
        overwrites = tracker.get_artifact_overwrites()
        assert "a.py" in overwrites
        assert "b.py" not in overwrites

    def test_to_dict(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage("S1", artifacts={"test.py": "content"})
        
        data = tracker.to_dict()
        assert data["job_id"] == "test"
        assert "stages" in data
        assert "summary" in data

    def test_save_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = ProvenanceTracker(job_id="test")
            tracker.record_stage("S1", artifacts={"test.py": "content"})
            
            path = tracker.save_to_file(tmpdir)
            assert Path(path).exists()
            
            with open(path) as f:
                data = json.load(f)
            assert data["job_id"] == "test"


class TestValidateSyntax:
    """Test syntax validation."""

    def test_valid_syntax(self):
        code = "def hello(): return 'world'"
        result = validate_syntax(code, "test.py")
        assert result["valid"] is True

    def test_invalid_syntax(self):
        code = "def hello(:"
        result = validate_syntax(code, "test.py")
        assert result["valid"] is False
        assert result["error"] is not None


class TestValidateHasContent:
    """Test content validation."""

    def test_has_content(self):
        result = validate_has_content("def main(): pass", "main.py")
        assert result["valid"] is True

    def test_empty_content(self):
        result = validate_has_content("", "main.py")
        assert result["valid"] is False


class TestExtractEndpoints:
    """Test endpoint extraction."""

    def test_extract_fastapi_endpoints(self):
        code = '''
@app.get("/users")
def get_users(): pass

@app.post("/users")
def create_user(): pass
'''
        endpoints = extract_endpoints_from_code(code)
        assert len(endpoints) == 2
        assert any(e["path"] == "/users" and e["method"] == "GET" for e in endpoints)
        assert any(e["path"] == "/users" and e["method"] == "POST" for e in endpoints)


class TestRunFailFastValidation:
    """Test fail-fast validation."""

    def test_valid_files(self):
        files = {
            "main.py": "def main(): pass",
            "requirements.txt": "fastapi\nuvicorn"
        }
        result = run_fail_fast_validation(files)
        assert result["valid"] is True

    def test_missing_main(self):
        files = {"requirements.txt": "fastapi"}
        result = run_fail_fast_validation(files)
        assert result["valid"] is False
        assert any("main.py" in e for e in result["errors"])

    def test_syntax_error(self):
        files = {
            "main.py": "def broken(:",
            "requirements.txt": "fastapi"
        }
        result = run_fail_fast_validation(files)
        assert result["valid"] is False


class TestValidateDockerfile:
    """Test Dockerfile validation."""

    def test_valid_dockerfile(self):
        content = "FROM python:3.11\nCMD python main.py"
        result = validate_dockerfile(content)
        assert result["valid"] is True

    def test_missing_from(self):
        content = "CMD python main.py"
        result = validate_dockerfile(content)
        assert result["valid"] is False

    def test_empty(self):
        result = validate_dockerfile("")
        assert result["valid"] is False


class TestValidateDockerCompose:
    """Test docker-compose validation."""

    def test_valid_compose(self):
        content = "services:\n  app:\n    build: ."
        result = validate_docker_compose(content)
        assert result["valid"] is True

    def test_missing_services(self):
        content = "version: '3'"
        result = validate_docker_compose(content)
        assert result["valid"] is False


class TestExtractEndpointsFromMd:
    """Test MD spec endpoint extraction."""

    def test_extract_basic_endpoints(self):
        md = """
# API Spec
- GET /api/users
- POST /api/users
- DELETE /api/users/{id}
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 3
        assert any(e["method"] == "GET" and e["path"] == "/api/users" for e in endpoints)
        assert any(e["method"] == "POST" and e["path"] == "/api/users" for e in endpoints)
        assert any(e["method"] == "DELETE" for e in endpoints)

    def test_extract_table_format(self):
        md = """
| Method | Path | Description |
|--------|------|-------------|
| GET | /api/items | Get all items |
| POST | /api/items | Create item |
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 2
        assert any(e["method"] == "GET" and e["path"] == "/api/items" for e in endpoints)

    def test_extract_backtick_format(self):
        md = """
The API has the following endpoints:
`GET /api/products`
`POST /api/products`
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 2

    def test_empty_md(self):
        endpoints = extract_endpoints_from_md("")
        assert endpoints == []

    def test_no_duplicates(self):
        md = """
- GET /api/users
- GET /api/users
- **GET** /api/users
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 1


class TestValidateSpecFidelity:
    """Test spec fidelity validation."""

    def test_all_endpoints_present(self):
        md = """
- GET /api/users
- POST /api/users
"""
        files = {
            "main.py": '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/users")
def get_users(): pass

@app.post("/api/users")
def create_user(): pass
''',
            "requirements.txt": "fastapi"
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True
        assert len(result["missing_endpoints"]) == 0

    def test_missing_endpoints(self):
        md = """
- GET /api/users
- POST /api/users
- DELETE /api/users/{id}
"""
        files = {
            "main.py": '''
@app.get("/api/users")
def get_users(): pass
''',
            "requirements.txt": "fastapi"
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is False
        assert len(result["missing_endpoints"]) == 2

    def test_no_endpoints_in_spec(self):
        md = "# Simple README\nNo API endpoints here."
        files = {"main.py": "print('hello')", "requirements.txt": ""}
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True  # No endpoints required = pass

    def test_writes_error_file(self):
        md = "- GET /api/missing"
        files = {"main.py": "pass", "requirements.txt": ""}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_spec_fidelity(md, files, output_dir=tmpdir)
            assert result["valid"] is False
            error_path = Path(tmpdir) / "error.txt"
            assert error_path.exists()

    def test_api_version_prefix_normalization(self):
        """Spec with /api/v1/ prefix matches code implementing routes without prefix."""
        md = """
| POST | /api/v1/orders | Create order |
| GET | /api/v1/products | List products |
"""
        files = {
            "app/main.py": '''
from fastapi import FastAPI
app = FastAPI()

@app.post("/orders")
def create_order(): pass

@app.get("/products")
def list_products(): pass
''',
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Expected valid=True when code implements /orders for spec /api/v1/orders, "
            f"missing: {result['missing_endpoints']}"
        )

    def test_api_version_prefix_in_both_spec_and_code(self):
        """Both spec and code using /api/v1/ prefix still passes validation."""
        md = "POST /api/v1/orders\nGET /api/v1/products\n"
        files = {
            "app/main.py": '''
@app.post("/api/v1/orders")
def create_order(): pass

@app.get("/api/v1/products")
def list_products(): pass
''',
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True

    def test_router_wiring_disconnected_warning(self):
        """Router files exist but main.py has no include_router() — should warn."""
        md = "# API\nGET /api/products"
        files = {
            "app/routers/products.py": (
                "@router.get('/api/products')\ndef list_products(): pass\n"
            ),
            "app/main.py": (
                "from fastapi import FastAPI\napp = FastAPI()\n"
            ),
        }
        result = validate_spec_fidelity(md, files)

        assert result["router_wiring_check"] is not None
        assert result["router_wiring_check"]["status"] == "disconnected"
        assert any("include_router" in w for w in result["warnings"])

    def test_router_wiring_connected_no_warning(self):
        """Router files exist and main.py includes them — no wiring warning."""
        md = "# API\nGET /api/products"
        files = {
            "app/routers/products.py": (
                "@router.get('/api/products')\ndef list_products(): pass\n"
            ),
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers import products_router\n"
                "app = FastAPI()\n"
                "app.include_router(products_router)\n"
            ),
        }
        result = validate_spec_fidelity(md, files)

        assert result["router_wiring_check"] is not None
        assert result["router_wiring_check"]["status"] == "connected"
        assert not any("include_router" in w for w in result["warnings"])

    def test_include_router_prefix_reconciliation_resolves_full_paths(self):
        """Endpoints declared in router files are matched against spec when the
        router is wired via ``include_router(..., prefix=...)`` in main.py.

        Background
        ----------
        A FastAPI router file typically contains route decorators using only
        the *sub-path*, e.g. ``@router.get("/stats")``.  The prefix
        ``/api/v1/orders`` is supplied by the ``include_router`` call in
        ``app/main.py``.  Per-file endpoint extraction therefore yields
        ``/stats``, not the full ``/api/v1/orders/stats`` that the spec
        requires.

        The reconciliation pass must detect the ``include_router`` prefix and
        reconstruct the fully-qualified path so that spec validation passes.
        """
        md = "- GET /api/v1/orders/stats\n- GET /api/v1/orders/{id}\n"
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers.orders import router as orders_router\n"
                "app = FastAPI()\n"
                "app.include_router(orders_router, prefix=\"/api/v1/orders\")\n"
            ),
            "app/routers/orders.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.get('/stats')\n"
                "def orders_stats(): pass\n\n"
                "@router.get('/{id}')\n"
                "def get_order(id: int): pass\n"
            ),
        }
        result = validate_spec_fidelity(md, files)

        assert result["valid"] is True, (
            f"Expected valid=True after prefix reconciliation, "
            f"missing: {result['missing_endpoints']}"
        )
        assert result["missing_endpoints"] == [], (
            "No endpoints should be missing after include_router prefix is resolved"
        )

    def test_include_router_prefix_double_prefix_not_duplicated(self):
        """When a route already carries the full path (including API version),
        the reconciliation pass must not double-count it so validation still
        passes even if the route path and the include_router prefix overlap."""
        md = "GET /api/v1/version\n"
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers.system import router as system_router\n"
                "app = FastAPI()\n"
                "@app.get('/api/v1/version')\n"
                "def version(): return {'version': '1.0'}\n"
                "app.include_router(system_router, prefix=\"/api/v1/system\")\n"
            ),
            "app/routers/system.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.get('/health')\n"
                "def health(): pass\n"
            ),
        }
        result = validate_spec_fidelity(md, files)

        # /api/v1/version is directly in main.py, so it must be found.
        assert result["valid"] is True, (
            f"GET /api/v1/version in main.py must be found; "
            f"missing: {result['missing_endpoints']}"
        )


class TestValidateDeploymentArtifacts:
    """Test deployment validation."""

    def test_valid_deployment(self):
        files = {
            "Dockerfile": "FROM python:3.11\nCMD python main.py",
            "docker-compose.yml": "services:\n  app:\n    build: ."
        }
        result = validate_deployment_artifacts(files)
        assert result["valid"] is True

    def test_invalid_dockerfile(self):
        files = {"Dockerfile": "# no FROM"}
        result = validate_deployment_artifacts(files)
        assert result["valid"] is False


class TestExtractOutputDir:
    """Test extract_output_dir_from_md function."""

    def test_extracts_simple_output_dir(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
# Project Spec
output_dir: generated/hello_generator

## API Endpoints
"""
        result = extract_output_dir_from_md(md_content)
        assert result == "generated/hello_generator"

    def test_extracts_output_dir_with_quotes(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: "my_project"
"""
        result = extract_output_dir_from_md(md_content)
        assert result == "my_project"

    def test_rejects_path_traversal(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: ../../../etc/passwd
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""

    def test_rejects_absolute_paths(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: /absolute/path
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""

    def test_rejects_windows_absolute_paths(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: C:/windows/path
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""

    def test_returns_empty_when_not_found(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
# Project without output_dir
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""


class TestValidateReadmeCompleteness:
    """Test validate_readme_completeness function."""

    def test_valid_complete_readme(self):
        from generator.main.provenance import validate_readme_completeness
        
        readme = """
# My Project

This is a comprehensive README for my project with detailed instructions.

## Setup

1. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\\Scripts\\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Run the Server

Start the development server:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at http://localhost:8000

## Testing

Run the test suite:
```bash
pytest tests/ -v --cov=app
```

## API Examples

### Health Check
```bash
curl http://localhost:8000/health
```

### Get Users
```bash
curl http://localhost:8000/api/users
```
"""
        result = validate_readme_completeness(readme)
        assert result["valid"] is True
        assert result["length"] > 500
        assert "setup" in result["sections_found"]
        assert "run" in result["sections_found"]
        assert "test" in result["sections_found"]
        assert "examples" in result["sections_found"]
        assert "venv" in result["commands_found"]
        assert "pip" in result["commands_found"]
        assert "uvicorn" in result["commands_found"]
        assert "pytest" in result["commands_found"]

    def test_incomplete_readme_too_short(self):
        from generator.main.provenance import validate_readme_completeness
        
        readme = "# Short README"
        result = validate_readme_completeness(readme)
        assert result["valid"] is False
        assert "too short" in str(result["errors"])

    def test_incomplete_readme_missing_sections(self):
        from generator.main.provenance import validate_readme_completeness
        
        # Create a README that's long enough but missing required sections
        readme = "# Project\n\n" + ("This is filler content to meet the minimum length requirement. " * 20)
        result = validate_readme_completeness(readme)
        assert result["valid"] is False
        assert any("setup" in err.lower() for err in result["errors"])

    def test_incomplete_readme_missing_commands(self):
        from generator.main.provenance import validate_readme_completeness
        
        # Create README with sections but no commands
        readme_parts = [
            "# Project\n\n",
            "## Setup\n",
            "Some setup instructions. " * 10,
            "\n\n## Run\n",
            "Run the app. " * 10,
            "\n\n## Testing\n",
            "Test the app. " * 10,
            "\n\n## Examples\n",
            "Some examples. " * 10,
        ]
        readme = "".join(readme_parts)
        result = validate_readme_completeness(readme)
        assert result["valid"] is False
        # Should be missing venv, pip, uvicorn, pytest commands
        assert any("venv" in err.lower() for err in result["errors"])


# ==============================================================================
# Tests for Fix 5: README markdown wrapper detection
# ==============================================================================

def test_validate_readme_with_markdown_wrapper():
    """
    Test that README content wrapped in ```markdown fences is detected and flagged.
    This addresses the docs/README.md wrapper bug.
    """
    from generator.main.provenance import validate_readme_completeness
    
    wrapped_readme = """```markdown
# My Project

A great project for testing.

## Installation

Run `pip install -r requirements.txt`

## Running

Run `python -m uvicorn app:main`

## Testing

Run `pytest`

## Examples

Use `curl http://localhost:8000/api/endpoint`
```"""
    
    result = validate_readme_completeness(wrapped_readme, language="python")
    # Should have an error about markdown fence wrapper
    assert any("markdown code fence" in err.lower() or "wrapped" in err.lower() 
               for err in result["errors"]), \
        f"Should detect markdown wrapper. Errors: {result['errors']}"


def test_validate_readme_with_md_wrapper():
    """
    Test that README content wrapped in ```md fences is also detected.
    """
    from generator.main.provenance import validate_readme_completeness
    
    wrapped_readme = """```md
# My Project

A great project.

## Setup

Run `python -m venv venv` and `pip install -r requirements.txt`

## Run

Run `uvicorn app:main`

## Test

Run `pytest`

## Examples

Use `curl http://localhost:8000`
```"""
    
    result = validate_readme_completeness(wrapped_readme, language="python")
    # Should have an error about markdown fence wrapper
    assert any("markdown code fence" in err.lower() or "wrapped" in err.lower() 
               for err in result["errors"]), \
        f"Should detect md wrapper. Errors: {result['errors']}"


def test_validate_readme_unwrapped_valid():
    """
    Test that a properly unwrapped README passes validation.
    """
    from generator.main.provenance import validate_readme_completeness
    
    good_readme = """# My Project

A great project for testing and production use.

## Installation

First, create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Running the Server

Start the server with:

```bash
uvicorn app:main --reload
```

## Testing

Run the test suite:

```bash
pytest
```

## API Examples

Test the API with curl:

```bash
curl http://localhost:8000/api/users
```
"""
    
    result = validate_readme_completeness(good_readme, language="python")
    # Should pass validation (no wrapper error)
    wrapper_errors = [err for err in result["errors"] 
                     if "markdown code fence" in err.lower() or "wrapped" in err.lower()]
    assert len(wrapper_errors) == 0, \
        f"Should not have wrapper errors for unwrapped README. Errors: {wrapper_errors}"


def test_validate_readme_auto_extract_from_wrapper():
    """
    Test that content can be auto-extracted from markdown wrapper for validation.
    """
    from generator.main.provenance import validate_readme_completeness
    
    # Valid content wrapped in markdown fence
    wrapped_readme = """```markdown
# My Project

A comprehensive README with all required sections.

## Installation

Set up your environment:

```bash
python -m venv venv
pip install -r requirements.txt
```

## Running

Start the application:

```bash
uvicorn app:main
```

## Testing

Run tests:

```bash
pytest
```

## Examples

Test endpoints:

```bash
curl http://localhost:8000/health
```
```"""
    
    result = validate_readme_completeness(wrapped_readme, language="python")
    
    # Should have an error about the wrapper
    assert any("markdown code fence" in err.lower() for err in result["errors"])
    
    # But should also have a warning about auto-extraction
    assert any("auto-extract" in warn.lower() for warn in result.get("warnings", [])), \
        f"Should have auto-extraction warning. Warnings: {result.get('warnings', [])}"


class TestReadmeTestMode:
    """Test README_TEST_MODE environment variable support."""

    def test_readme_test_mode_relaxed_length(self):
        """Test that README_TEST_MODE relaxes minimum length requirement."""
        from unittest.mock import patch
        from generator.main.provenance import validate_readme_completeness
        
        # Short README that would fail strict mode (< 500 chars) but should pass test mode (>= 200 chars)
        short_readme = """# Test Project

## Introduction

This is a minimal README with basic information about the project that is long enough for test mode validation but not for production strict mode validation requirements.
"""
        
        # Test with README_TEST_MODE enabled
        with patch.dict('os.environ', {'README_TEST_MODE': '1'}):
            result = validate_readme_completeness(short_readme)
            
            # Should not fail on length in test mode (>= 200 chars)
            length_errors = [err for err in result["errors"] if "too short" in err.lower()]
            assert len(length_errors) == 0, f"Should not fail on length in test mode. Errors: {result['errors']}"
        
        # Test without README_TEST_MODE (strict mode)
        with patch.dict('os.environ', {}):
            result = validate_readme_completeness(short_readme)
            
            # Should fail on length in strict mode (< 500 chars)
            length_errors = [err for err in result["errors"] if "too short" in err.lower()]
            assert len(length_errors) > 0, "Should fail on length in strict mode"

    def test_readme_test_mode_relaxed_sections(self):
        """Test that README_TEST_MODE makes sections optional."""
        from unittest.mock import patch
        from generator.main.provenance import validate_readme_completeness
        
        # README with no sections (just title and content) - deliberately avoids
        # any keywords that match section patterns (setup, run, test, examples, etc.)
        minimal_readme = """# My Application

This is a brief description of the application. The project provides various features for processing and analyzing data according to business requirements.

The codebase is organized into modules that handle specific concerns. Each component is designed to work independently and can be integrated with other parts of the system.

Further information about configuration and deployment can be found in the team's internal documentation and knowledge base.
""" * 2  # Repeat to meet minimum length
        
        # Test with README_TEST_MODE enabled
        with patch.dict('os.environ', {'README_TEST_MODE': '1'}):
            result = validate_readme_completeness(minimal_readme)
            
            # Should not fail on missing sections in test mode
            section_errors = [err for err in result["errors"] if "Missing required section" in err]
            assert len(section_errors) == 0, f"Should not fail on missing sections in test mode. Errors: {result['errors']}"
        
        # Test without README_TEST_MODE (strict mode) - explicitly remove the key
        import os
        env_copy = os.environ.copy()
        env_copy.pop('README_TEST_MODE', None)  # Remove the key entirely
        
        with patch.dict('os.environ', env_copy, clear=True):
            result = validate_readme_completeness(minimal_readme)
            
            # Should fail on missing sections in strict mode
            section_errors = [err for err in result["errors"] if "Missing required section" in err]
            assert len(section_errors) > 0, f"Should fail on missing sections in strict mode. Got {len(section_errors)} errors"

    def test_readme_test_mode_relaxed_commands(self):
        """Test that README_TEST_MODE makes commands optional."""
        from unittest.mock import patch
        from generator.main.provenance import validate_readme_completeness
        
        # README with sections but no commands
        readme_no_commands = """# Test Project

## Setup

You need to set up the project by following these instructions and preparing your environment appropriately.

## Run

To run the project, execute the application using the appropriate method for your system and configuration.

## Test

Testing can be performed by running the test suite with your preferred testing framework and verifying results.

## Examples

Here are some examples of how to use the project and its features for various use cases and scenarios.
""" * 2  # Repeat to meet minimum length
        
        # Test with README_TEST_MODE enabled
        with patch.dict('os.environ', {'README_TEST_MODE': '1'}):
            result = validate_readme_completeness(readme_no_commands)
            
            # Should not fail on missing commands in test mode
            command_errors = [err for err in result["errors"] if "Missing required command" in err]
            assert len(command_errors) == 0, f"Should not fail on missing commands in test mode. Errors: {result['errors']}"
        
        # Test without README_TEST_MODE (strict mode)
        with patch.dict('os.environ', {}):
            result = validate_readme_completeness(readme_no_commands)
            
            # Should fail on missing commands in strict mode
            command_errors = [err for err in result["errors"] if "Missing required command" in err]
            assert len(command_errors) > 0, "Should fail on missing commands in strict mode"

    def test_readme_test_mode_complete_validation(self):
        """Test that a minimal README passes all validations in test mode."""
        from unittest.mock import patch
        from generator.main.provenance import validate_readme_completeness
        
        # Truly minimal README for test mode
        minimal_readme = """# Test Project

This is a minimal README with just enough content to pass the relaxed test mode validation requirements. It has sufficient length for test mode (over 200 characters) but lacks the detailed sections and commands that would be required in production strict mode.
"""
        
        # Test with README_TEST_MODE enabled
        with patch.dict('os.environ', {'README_TEST_MODE': '1'}):
            result = validate_readme_completeness(minimal_readme)
            
            # Should pass all validations in test mode
            assert result["valid"] is True, f"Minimal README should pass in test mode. Errors: {result['errors']}"
            assert len(result["errors"]) == 0
        
        # Test without README_TEST_MODE (strict mode)
        with patch.dict('os.environ', {}):
            result = validate_readme_completeness(minimal_readme)
            
            # Should fail in strict mode
            assert result["valid"] is False, "Minimal README should fail in strict mode"
            assert len(result["errors"]) > 0


class TestRunFailFastValidationInitPy:
    """Tests for the __init__.py check added to run_fail_fast_validation."""

    def test_run_fail_fast_validation_warns_missing_init_py(self, tmp_path):
        """A subdirectory with .py files but no __init__.py produces a warning."""
        generated_files = {
            "main.py": "print('hello')",
            "requirements.txt": "flask",
            "app/routes.py": "# routes",
        }
        result = run_fail_fast_validation(
            generated_files, output_dir=str(tmp_path), target_language="python"
        )
        warnings = result.get("warnings", [])
        assert any("app" in w and "__init__.py" in w for w in warnings), (
            f"Expected a warning about missing __init__.py in 'app' dir, got: {warnings}"
        )

    def test_run_fail_fast_validation_no_warning_when_init_py_present(self, tmp_path):
        """No warning when all subdirectories contain __init__.py."""
        generated_files = {
            "main.py": "print('hello')",
            "requirements.txt": "flask",
            "app/__init__.py": "",
            "app/routes.py": "# routes",
        }
        result = run_fail_fast_validation(
            generated_files, output_dir=str(tmp_path), target_language="python"
        )
        warnings = result.get("warnings", [])
        init_py_warnings = [w for w in warnings if "__init__.py" in w]
        assert len(init_py_warnings) == 0, (
            f"Should not warn when __init__.py is present, got: {init_py_warnings}"
        )

    def test_run_fail_fast_validation_root_py_files_not_flagged(self, tmp_path):
        """Root-level .py files (no subdirectory) are not flagged."""
        generated_files = {
            "main.py": "print('hello')",
            "requirements.txt": "flask",
            "utils.py": "# utils",
        }
        result = run_fail_fast_validation(
            generated_files, output_dir=str(tmp_path), target_language="python"
        )
        warnings = result.get("warnings", [])
        init_py_warnings = [w for w in warnings if "__init__.py" in w]
        assert len(init_py_warnings) == 0


# ==============================================================================
# Tests for extract_file_structure_from_md
# ==============================================================================


class TestExtractFileStructureFromMd:
    """Tests for extract_file_structure_from_md — the two-pass spec structure parser."""

    def test_empty_string_returns_empty_structure(self):
        from generator.main.provenance import extract_file_structure_from_md

        result = extract_file_structure_from_md("")
        assert result == {"directories": [], "files": [], "modules": []}

    def test_whitespace_only_returns_empty_structure(self):
        from generator.main.provenance import extract_file_structure_from_md

        result = extract_file_structure_from_md("   \n\t  ")
        assert result == {"directories": [], "files": [], "modules": []}

    def test_raises_type_error_on_non_string(self):
        from generator.main.provenance import extract_file_structure_from_md

        with pytest.raises(TypeError, match="str"):
            extract_file_structure_from_md(None)  # type: ignore[arg-type]

    def test_raises_type_error_on_list(self):
        from generator.main.provenance import extract_file_structure_from_md

        with pytest.raises(TypeError, match="str"):
            extract_file_structure_from_md([])  # type: ignore[arg-type]

    def test_unicode_tree_block_extracts_nested_dirs(self):
        """Full tree block: all directories including siblings must be detected."""
        from generator.main.provenance import extract_file_structure_from_md

        md = """
```
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── products.py
│   │   └── orders.py
│   ├── services/
│   │   └── __init__.py
│   ├── middleware/
│   │   └── auth.py
│   └── utils/
│       └── helpers.py
├── alembic/
│   └── env.py
├── tests/
│   ├── test_products.py
│   └── test_orders.py
└── alembic.ini
```
"""
        result = extract_file_structure_from_md(md)

        # Top-level directories must be detected
        for expected_dir in ["app", "alembic", "tests"]:
            assert expected_dir in result["directories"], (
                f"Expected top-level directory '{expected_dir}' in {result['directories']}"
            )

        # Nested app sub-packages must be detected
        for expected_dir in [
            "app/routers",
            "app/services",
            "app/middleware",
            "app/utils",
        ]:
            assert expected_dir in result["directories"], (
                f"Expected nested directory '{expected_dir}' in {result['directories']}"
            )

        # File paths must be reconstructed correctly
        assert "app/routers/products.py" in result["files"]
        assert "app/routers/orders.py" in result["files"]
        assert "alembic/env.py" in result["files"]
        assert "tests/test_products.py" in result["files"]

        # Python module paths must be derived from files
        assert "app.routers.products" in result["modules"]
        assert "app.routers.orders" in result["modules"]

    def test_backtick_inline_file_reference(self):
        """Backtick-quoted multi-part file paths must be captured."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "Auth logic lives in `app/routers/auth.py`."
        result = extract_file_structure_from_md(md)

        assert "app/routers/auth.py" in result["files"]
        assert "app/routers" in result["directories"]
        assert "app" in result["directories"]
        assert "app.routers.auth" in result["modules"]

    def test_backtick_inline_directory_reference(self):
        """Backtick-quoted directory paths (trailing /) must be captured."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "Place schemas in `app/schemas/` and models in `app/models/`."
        result = extract_file_structure_from_md(md)

        assert "app/schemas" in result["directories"]
        assert "app/models" in result["directories"]

    def test_trailing_slash_directory_reference(self):
        """Bare directory references with trailing / in prose must be captured."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "Routes in app/routers/ and business logic in app/services/."
        result = extract_file_structure_from_md(md)

        assert "app/routers" in result["directories"]
        assert "app/services" in result["directories"]

    def test_glob_pattern_extracts_directory(self):
        """Glob patterns like app/routers/*.py must yield the directory."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "All routers: app/routers/*.py.  All tests: tests/test_*.py."
        result = extract_file_structure_from_md(md)

        assert "app/routers" in result["directories"]
        assert "tests" in result["directories"]
        # Ensure no partial filename segment is treated as a directory
        assert "tests/test_" not in result["directories"]

    def test_ancestor_directories_are_recorded(self):
        """When a deep path is detected, all ancestor directories must be added."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "`app/routers/v1/products.py`"
        result = extract_file_structure_from_md(md)

        assert "app" in result["directories"]
        assert "app/routers" in result["directories"]
        assert "app/routers/v1" in result["directories"]

    def test_no_duplicate_directories(self):
        """The same directory must not appear twice even if referenced multiple times."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "app/routers/ and app/routers/ again, plus `app/routers/auth.py`."
        result = extract_file_structure_from_md(md)

        assert result["directories"].count("app/routers") == 1

    def test_no_duplicate_files(self):
        """The same file must not appear twice."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "`app/main.py` is the entrypoint. See `app/main.py` for details."
        result = extract_file_structure_from_md(md)

        assert result["files"].count("app/main.py") == 1

    def test_deterministic_output(self):
        """Calling the function twice with the same input must return equal results."""
        from generator.main.provenance import extract_file_structure_from_md

        md = """
```
├── app/
│   ├── main.py
│   └── routers/
│       └── auth.py
└── tests/
    └── test_auth.py
```
"""
        assert extract_file_structure_from_md(md) == extract_file_structure_from_md(md)

    def test_modules_only_for_python_files(self):
        """Only .py files should generate module entries."""
        from generator.main.provenance import extract_file_structure_from_md

        md = "`app/config.yml` and `app/main.py`."
        result = extract_file_structure_from_md(md)

        assert "app.main" in result["modules"]
        assert "app.config" not in result["modules"]

    def test_structure_validation_in_validate_spec_fidelity(self):
        """validate_spec_fidelity must include a 'structure_validation' key."""
        from generator.main.provenance import validate_spec_fidelity

        md = "app/routers/ and app/services/."
        result = validate_spec_fidelity(md, {})

        assert "structure_validation" in result
        sv = result["structure_validation"]
        assert "expected_directories" in sv
        assert "missing_directories" in sv
        assert "passed" in sv
        # Without an output_dir there are no missing dirs (nothing to check on disk)
        assert sv["passed"] is True

    def test_structure_validation_detects_missing_dirs(self):
        """When output_dir is provided and a spec dir is absent, it is flagged."""
        from generator.main.provenance import validate_spec_fidelity
        import tempfile, os

        md = "Use app/routers/ for routing and app/services/ for business logic."
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create only app/routers — leave app/services absent
            os.makedirs(os.path.join(tmpdir, "app", "routers"), exist_ok=True)
            result = validate_spec_fidelity(md, {}, output_dir=tmpdir)

        sv = result["structure_validation"]
        assert "app/services" in sv["missing_directories"]
        assert "app/routers" not in sv["missing_directories"]
        assert sv["passed"] is False


class TestStructureValidationDirectoryFilter:
    """Verify that file-like entries are excluded from the directory check."""

    def test_env_example_not_reported_as_missing_directory(self):
        """
        .env.example has a file extension and must be filtered out of the
        expected-directories list before the on-disk check is performed.
        The spec validator must not flag it as a missing directory.
        """
        import tempfile, os
        from generator.main.provenance import validate_spec_fidelity

        # Spec mentions .env.example and a real directory
        md = "Project layout:\n- `.env.example`\n- `app/`\n- `app/main.py`\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "app"), exist_ok=True)
            result = validate_spec_fidelity(md, {}, output_dir=tmpdir)

        sv = result["structure_validation"]
        assert ".env.example" not in sv["missing_directories"], (
            ".env.example is a file, not a directory — it must be filtered out"
        )

    def test_alembic_ini_not_reported_as_missing_directory(self):
        """
        alembic.ini has a .ini extension and must not appear in missing directories.
        """
        import tempfile
        from generator.main.provenance import validate_spec_fidelity

        md = "Config files: `alembic.ini`, `alembic/`\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            import os
            os.makedirs(os.path.join(tmpdir, "alembic"), exist_ok=True)
            result = validate_spec_fidelity(md, {}, output_dir=tmpdir)

        sv = result["structure_validation"]
        assert "alembic.ini" not in sv["missing_directories"], (
            "alembic.ini is a file, not a directory — it must be filtered out"
        )

    def test_plain_directory_still_checked(self):
        """
        Regular directories (no extension) must still be included in the check.
        A genuinely missing directory must still be reported.
        """
        import tempfile
        from generator.main.provenance import validate_spec_fidelity

        md = "Use `app/routers/` and `app/services/` directories.\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            import os
            # Only create app/routers — leave app/services absent
            os.makedirs(os.path.join(tmpdir, "app", "routers"), exist_ok=True)
            result = validate_spec_fidelity(md, {}, output_dir=tmpdir)

        sv = result["structure_validation"]
        assert "app/services" in sv["missing_directories"]
        assert "app/routers" not in sv["missing_directories"]


class TestNormalizePathParameterVariants:
    """validate_spec_fidelity must match routes differing only in path-parameter names."""

    def test_different_param_names_match(self):
        """Spec /api/v1/products/{id} must match code /products/{product_id}."""
        md = "- GET /api/v1/products/{id}\n"
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n\n"
                "@app.get('/products/{product_id}')\n"
                "def get_product(product_id: int): pass\n"
            ),
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"GET /api/v1/products/{{id}} should match /products/{{product_id}}; "
            f"missing: {result['missing_endpoints']}"
        )
        assert result["missing_endpoints"] == []

    def test_nested_path_param_variants_match(self):
        """Spec /api/v1/orders/{order_id}/items/{item_id} matches equivalent code path."""
        md = "- GET /api/v1/orders/{order_id}/items/{item_id}\n"
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n\n"
                "@app.get('/orders/{oid}/items/{iid}')\n"
                "def get_item(oid: int, iid: int): pass\n"
            ),
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Nested path params with different names must still match; "
            f"missing: {result['missing_endpoints']}"
        )

    def test_path_with_trailing_whitespace_normalized(self):
        """Paths with leading/trailing whitespace in spec/code must still match."""
        md = "| GET | /api/v1/orders  | List orders |\n"
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n\n"
                "@app.get('/orders')\n"
                "def list_orders(): pass\n"
            ),
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Trailing whitespace in paths must be stripped; "
            f"missing: {result['missing_endpoints']}"
        )

    def test_api_v2_prefix_also_stripped(self):
        """/api/v2/ prefix is stripped just like /api/v1/."""
        md = "- POST /api/v2/users\n"
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n\n"
                "@app.post('/users')\n"
                "def create_user(): pass\n"
            ),
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"/api/v2/ prefix must be stripped during normalisation; "
            f"missing: {result['missing_endpoints']}"
        )

    def test_router_prefix_plus_param_name_difference(self):
        """Router prefix (/api/v1/products) + route param {product_id} must match
        spec /api/v1/products/{id} even when parameter names differ."""
        md = "- GET /api/v1/products/{id}\n"
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers.products import router as products_router\n"
                "app = FastAPI()\n"
                "app.include_router(products_router, prefix=\"/api/v1/products\")\n"
            ),
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.get('/{product_id}')\n"
                "def get_product(product_id: int): pass\n"
            ),
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Router prefix + differing param name must match spec; "
            f"missing: {result['missing_endpoints']}"
        )


class TestImportPatternFixes:
    """Tests for the expanded import pattern matching in validate_spec_fidelity.

    Covers the P0 fix for _IMPORT_AS_RE being too restrictive.
    """

    def test_direct_import_no_alias(self):
        """Direct import without 'as' alias is correctly mapped to prefix."""
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routes import auth_router, patients_router\n"
                "app = FastAPI()\n"
                "app.include_router(auth_router, prefix=\"/api/v1/auth\")\n"
                "app.include_router(patients_router, prefix=\"/api/v1/patients\")\n"
            ),
            "app/routes.py": (
                "from fastapi import APIRouter\n"
                "auth_router = APIRouter()\n"
                "patients_router = APIRouter()\n\n"
                "@auth_router.post('/login')\n"
                "def login(): pass\n\n"
                "@patients_router.get('/')\n"
                "def get_patients(): pass\n"
            ),
        }
        md = "- POST /api/v1/auth/login\n- GET /api/v1/patients\n"
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Direct import without alias must be resolved; "
            f"missing: {result['missing_endpoints']}"
        )
        assert result["missing_endpoints"] == []

    def test_multi_import_parentheses(self):
        """Multi-import with parentheses is correctly resolved."""
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routes import (\n"
                "    auth_router,\n"
                "    patients_router,\n"
                ")\n"
                "app = FastAPI()\n"
                "app.include_router(auth_router, prefix=\"/api/v1/auth\")\n"
                "app.include_router(patients_router, prefix=\"/api/v1/patients\")\n"
            ),
            "app/routes.py": (
                "from fastapi import APIRouter\n"
                "auth_router = APIRouter()\n"
                "patients_router = APIRouter()\n\n"
                "@auth_router.post('/login')\n"
                "def login(): pass\n\n"
                "@patients_router.get('/')\n"
                "def get_patients(): pass\n"
            ),
        }
        md = "- POST /api/v1/auth/login\n- GET /api/v1/patients\n"
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Multi-import with parentheses must be resolved; "
            f"missing: {result['missing_endpoints']}"
        )
        assert result["missing_endpoints"] == []

    def test_single_file_all_routers(self):
        """All routers in a single file are each assigned the correct prefix."""
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routes import auth_router, patients_router, encounters_router\n"
                "app = FastAPI()\n"
                "app.include_router(auth_router, prefix=\"/api/v1/auth\")\n"
                "app.include_router(patients_router, prefix=\"/api/v1/patients\")\n"
                "app.include_router(encounters_router, prefix=\"/api/v1/encounters\")\n"
            ),
            "app/routes.py": (
                "from fastapi import APIRouter\n"
                "auth_router = APIRouter()\n"
                "patients_router = APIRouter()\n"
                "encounters_router = APIRouter()\n\n"
                "@auth_router.post('/login')\n"
                "def login(): pass\n\n"
                "@patients_router.get('/')\n"
                "def list_patients(): pass\n\n"
                "@encounters_router.post('/')\n"
                "def create_encounter(): pass\n"
            ),
        }
        md = (
            "- POST /api/v1/auth/login\n"
            "- GET /api/v1/patients\n"
            "- POST /api/v1/encounters\n"
        )
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Single-file router pattern must be resolved; "
            f"missing: {result['missing_endpoints']}"
        )
        assert result["missing_endpoints"] == []

    def test_aliased_import_still_works(self):
        """Existing aliased import pattern (from X import Y as Z) still works."""
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers.auth import router as auth_router\n"
                "app = FastAPI()\n"
                "app.include_router(auth_router, prefix=\"/api/v1/auth\")\n"
            ),
            "app/routers/auth.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.post('/login')\n"
                "def login(): pass\n"
            ),
        }
        md = "- POST /api/v1/auth/login\n"
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True, (
            f"Aliased import must still work after fix; "
            f"missing: {result['missing_endpoints']}"
        )
        assert result["missing_endpoints"] == []
