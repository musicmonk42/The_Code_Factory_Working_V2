# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Service for interacting with the OmniCore Engine module.

This service provides a mockable interface to the omnicore_engine module for
job coordination, plugin management, and inter-module communication.

This module implements proper agent integration with:
- Configuration-based LLM provider selection
- Graceful degradation when agents unavailable
- Proper error handling and logging
- Environment variable support for API keys
- Industry-standard observability (metrics, tracing, structured logging)
"""

import aiofiles
import ast
import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
import yaml
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from jinja2 import Template
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False
    Template = None

from server.utils.agent_loader import get_agent_loader
from server.storage import jobs_db
from server.schemas.jobs import JobStatus, JobStage
from server.services.sfe_utils import transform_pipeline_issues_to_frontend_errors

# Import shared Presidio placeholders constant
try:
    from generator.runner.runner_security_utils import PRESIDIO_PLACEHOLDERS
except ImportError:
    # Fallback if import fails
    PRESIDIO_PLACEHOLDERS = ['<ORGANIZATION>', '<URL>', '<PERSON>', '<API_KEY>']

# Import flexible requirements parser for code generation
try:
    from generator.agents.codegen_agent.codegen_prompt import _parse_requirements_flexible
except ImportError:
    _parse_requirements_flexible = None

# Import existing materializer for writing LLM multi-file output to disk
# This replaces the manual file-writing loop and prevents the JSON-bundle-in-main.py bug
try:
    from generator.runner.runner_file_utils import (
        materialize_file_map as _materialize_file_map,
        validate_generated_project as _validate_generated_project,
        write_validation_error as _write_validation_error,
    )
    _MATERIALIZER_AVAILABLE = True
except ImportError:
    _MATERIALIZER_AVAILABLE = False

# Import existing provenance tracker and spec validator
try:
    from generator.main.provenance import (
        ProvenanceTracker,
        validate_spec_fidelity as _validate_spec_fidelity,
        run_fail_fast_validation as _run_fail_fast_validation,
        extract_required_files_from_md as _extract_required_files_from_md,
        extract_output_dir_from_md as _extract_output_dir_from_md,
        validate_readme_completeness as _validate_readme_completeness,
        extract_file_structure_from_md as _extract_file_structure_from_md,
    )
    _PROVENANCE_AVAILABLE = True
except ImportError:
    _PROVENANCE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Observability imports with graceful degradation
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
    tracer = trace.get_tracer(__name__)
except ImportError:
    TRACING_AVAILABLE = False
    logger.warning("OpenTelemetry not available, tracing disabled")
    
try:
    from prometheus_client import Counter, Histogram, Gauge
    from prometheus_client.registry import REGISTRY
    METRICS_AVAILABLE = True
    
    # Helper functions for safe metric registration (idempotent pattern)
    def _get_or_create_counter(name: str, description: str, labelnames: list = None):
        """Create a Counter or return existing one with same name."""
        labelnames = tuple(labelnames or [])  # Convert list to tuple
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector is not None:
                return collector
        except (AttributeError, KeyError):
            pass
        try:
            return Counter(name, description, labelnames=labelnames)
        except ValueError as e:
            if "Duplicated timeseries" in str(e):
                existing = REGISTRY._names_to_collectors.get(name)
                if existing is not None:
                    return existing
            raise
    
    def _get_or_create_histogram(name: str, description: str, labelnames: list = None):
        """Create a Histogram or return existing one with same name."""
        labelnames = tuple(labelnames or [])  # Convert list to tuple
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector is not None:
                return collector
        except (AttributeError, KeyError):
            pass
        try:
            return Histogram(name, description, labelnames=labelnames)
        except ValueError as e:
            if "Duplicated timeseries" in str(e):
                existing = REGISTRY._names_to_collectors.get(name)
                if existing is not None:
                    return existing
            raise
    
    # Define metrics for code generation observability using safe registration
    codegen_requests_total = _get_or_create_counter(
        'codegen_requests_total',
        'Total number of code generation requests',
        ['job_id', 'language', 'status']
    )
    codegen_files_generated = _get_or_create_counter(
        'codegen_files_generated_total',
        'Total number of files generated',
        ['job_id', 'language']
    )
    codegen_duration_seconds = _get_or_create_histogram(
        'codegen_duration_seconds',
        'Code generation duration in seconds',
        ['job_id', 'language']
    )
    codegen_file_size_bytes = _get_or_create_histogram(
        'codegen_file_size_bytes',
        'Size of generated files in bytes',
        ['job_id', 'file_type']
    )
    codegen_errors_total = _get_or_create_counter(
        'codegen_errors_total',
        'Total number of code generation errors',
        ['job_id', 'error_type']
    )
    
    # Deployment-specific metrics for observability
    deployment_requests_total = _get_or_create_counter(
        'deployment_requests_total',
        'Total number of deployment requests',
        ['job_id', 'target', 'status']
    )
    deployment_duration_seconds = _get_or_create_histogram(
        'deployment_duration_seconds',
        'Deployment generation duration in seconds',
        ['job_id', 'target']
    )
    deployment_validation_total = _get_or_create_counter(
        'deployment_validation_total',
        'Total number of deployment validations',
        ['job_id', 'status', 'validation_type']
    )
    deployment_files_generated = _get_or_create_counter(
        'deployment_files_generated_total',
        'Total number of deployment files generated',
        ['job_id', 'target', 'file_type']
    )
except ImportError:
    METRICS_AVAILABLE = False
    logger.warning("Prometheus client not available, metrics disabled")

# Import configuration and helper functions
try:
    from server.config import (
        detect_available_llm_provider,
        get_agent_config,
        get_default_model_for_provider,
        get_llm_config,
    )
    CONFIG_AVAILABLE = True
except ImportError:
    logger.warning("server.config not available, using default configuration")
    CONFIG_AVAILABLE = False
    # Provide fallback implementations
    def detect_available_llm_provider():
        return None
    def get_default_model_for_provider(provider):
        return "gpt-4o"

# In-memory storage for clarification sessions
_clarification_sessions = {}

# Constants for configurable timeouts
DEFAULT_TESTGEN_TIMEOUT = int(os.getenv("TESTGEN_TIMEOUT_SECONDS", "600"))
DEFAULT_TESTGEN_LLM_TIMEOUT = int(os.getenv("TESTGEN_LLM_TIMEOUT_SECONDS", "360"))
# Add a pipeline-specific timeout that caps testgen when running in full pipeline mode
DEFAULT_TESTGEN_PIPELINE_TIMEOUT = int(os.getenv("TESTGEN_PIPELINE_TIMEOUT_SECONDS", "120"))
DEFAULT_DEPLOY_TIMEOUT = int(os.getenv("DEPLOY_TIMEOUT_SECONDS", "90"))
DEFAULT_DOCGEN_TIMEOUT = int(os.getenv("DOCGEN_TIMEOUT_SECONDS", "300"))
DEFAULT_CRITIQUE_TIMEOUT = int(os.getenv("CRITIQUE_TIMEOUT_SECONDS", "90"))
DEFAULT_SFE_ANALYSIS_TIMEOUT = int(os.getenv("SFE_ANALYSIS_TIMEOUT_SECONDS", "600"))
# Maximum number of files to analyze in depth during SFE analysis (prevents timeout)
MAX_SFE_FILES_TO_ANALYZE = int(os.getenv("MAX_SFE_FILES_TO_ANALYZE", "50"))

# Per-step pipeline timeouts — codegen timeout triggers a FAILED job (critical path).
# Other step timeouts (testgen, critique, sfe_analysis, docgen) return an error status
# and allow the pipeline to continue gracefully (non-critical path).
# All values are configurable via environment variables for operator tuning.
PIPELINE_STEP_TIMEOUTS: Dict[str, int] = {
    "codegen": int(os.environ.get("PIPELINE_CODEGEN_TIMEOUT_SECONDS", "600")),
    "testgen": int(os.environ.get("PIPELINE_TESTGEN_TIMEOUT_SECONDS", "300")),
    "deploy": int(os.environ.get("PIPELINE_DEPLOY_TIMEOUT_SECONDS", "300")),
    "docgen": int(os.environ.get("PIPELINE_DOCGEN_TIMEOUT_SECONDS", "300")),
    # Support both CRITIQUE_PIPELINE_TIMEOUT_SECONDS and PIPELINE_CRITIQUE_TIMEOUT_SECONDS
    # for backwards compatibility; default to 300s (5 minutes)
    "critique": int(
        os.environ.get(
            "CRITIQUE_PIPELINE_TIMEOUT_SECONDS",
            os.environ.get("PIPELINE_CRITIQUE_TIMEOUT_SECONDS", "300"),
        )
    ),
    "sfe_analysis": int(os.environ.get("PIPELINE_SFE_TIMEOUT_SECONDS", "600")),
}

# ============================================================================
# INDUSTRY STANDARD: Named Constants for Configuration and Limits
# Following OWASP and industry best practices for secure, maintainable code
# ============================================================================

# File Size Limits (Industry Standard: Prevent DoS and memory exhaustion)
MAX_REPORT_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB - reasonable for JSON reports
MAX_CONFIG_FILE_SIZE_BYTES = 5 * 1024 * 1024   # 5 MB - reasonable for config files

# Helm Chart Validation Constants
HELM_REQUIRED_FIELDS = {"apiVersion", "name"}  # Minimum required fields per Helm spec
HELM_DEFAULT_API_VERSION = "v2"  # Helm 3 uses apiVersion v2
HELM_DEFAULT_CHART_TYPE = "application"

# Spec-Derived File Patterns for Categorization
DEPLOYMENT_FILE_PATTERNS = {
    'Dockerfile', 'docker-compose.yml', 'compose.yml', 
    'Chart.yaml', 'deployment.yaml', 'service.yaml',
    'Jenkinsfile', 'Makefile', '.gitlab-ci.yml'
}
FRONTEND_FILE_PATTERNS = {
    'index.html', 'style.css', 'app.js', 'bundle.js',
    'main.tsx', 'App.tsx', 'index.jsx', 'App.jsx'
}
CONFIG_FILE_PATTERNS = {'conf.py', 'config.yaml', 'settings.py'}

# Error Type Constants (Industry Standard: Structured error identification)
ERROR_TYPE_IMPORT = "import_error"
ERROR_TYPE_SETTINGS_INIT = "settings_initialization_failed"
ERROR_TYPE_VALIDATION = "validation_error"
ERROR_TYPE_IO = "io_error"
ERROR_TYPE_PARSE = "parse_error"
ERROR_TYPE_TIMEOUT = "timeout_error"

# Cache/Report Source Constants
SOURCE_CACHE = "sfe_analysis_report"
SOURCE_DIRECT = "direct_sfe"
SOURCE_PLACEHOLDER = "placeholder"

# Constants for clarification session cleanup
CLARIFICATION_SESSION_TTL_SECONDS = int(os.getenv("CLARIFICATION_SESSION_TTL_SECONDS", "3600"))  # 1 hour default


# Custom exception for security violations
class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


# Constants for file parsing and validation
MIN_YAML_DOC_LENGTH = 10  # Minimum characters for a valid YAML document
HELM_FILE_HEADER_CHECK_LENGTH = 50  # Check first N chars for Helm filenames

# Pre-compiled regex for extracting the Kubernetes resource kind from YAML documents.
# Used in both the primary K8s YAML processing pass and the retry loop.
_K8S_KIND_RE = re.compile(r'kind:\s*(\w+)', re.IGNORECASE)

# Constants for README generation
MAX_FILES_IN_README = 10  # Maximum files to list in README
MAX_DEPENDENCIES_IN_README = 5  # Maximum dependencies to list in README
MIN_README_LENGTH = 500  # Minimum length for a complete README (characters)

# Minimum fraction of spec-required endpoints that must be present before the
# pipeline continues past the codegen step.  If the fraction *missing* exceeds
# this threshold a codegen retry is triggered (subject to max_codegen_retries).
SPEC_FIDELITY_MISSING_ENDPOINT_THRESHOLD = 0.50

# Language detection and file extension mappings
LANGUAGE_FILE_EXTENSIONS = {
    "python": ["*.py"],
    "typescript": ["*.ts", "*.tsx"],
    "javascript": ["*.js", "*.jsx"],
    "java": ["*.java"],
    "go": ["*.go"],
    "rust": ["*.rs"],
}

TEST_FILE_PATTERNS = {
    "python": lambda f: f.name.startswith("test_") or f.name.endswith("_test.py"),
    "typescript": lambda f: f.name.endswith(".test.ts") or f.name.endswith(".spec.ts") or f.name.endswith(".test.tsx") or f.name.endswith(".spec.tsx"),
    "javascript": lambda f: f.name.endswith(".test.js") or f.name.endswith(".spec.js") or f.name.endswith(".test.jsx") or f.name.endswith(".spec.jsx"),
    "java": lambda f: f.name.endswith("Test.java") or f.name.endswith("Tests.java"),
    "go": lambda f: f.name.endswith("_test.go"),
    "rust": lambda f: f.name.startswith("test_") or "tests" in str(f.parent),
}


def _pre_materialization_import_check(files: Dict[str, str]) -> List[str]:
    """Perform an in-memory import validity check before writing files to disk.

    For each .py file in the file map:
    1. AST-parses it to verify syntax.
    2. Collects all ``from X import Y`` statements where X starts with ``app.``.
    3. For each such import, verifies that the target module exists in the file
       map and that the imported symbol is defined there.
    4. Also checks for used names in decorators that may cause NameError at
       import time.

    Args:
        files: Dict mapping file paths (e.g. ``"app/routers/audit.py"``) to
               source content strings.

    Returns:
        List of error strings, one per unresolvable import or symbol.
        Empty list means all imports are resolvable.
    """
    errors: List[str] = []

    # Pre-build a map of module_path -> set of top-level symbol names
    module_symbols: Dict[str, Set[str]] = {}
    for filepath, content in files.items():
        if not filepath.endswith('.py') or not isinstance(content, str):
            continue
        mod = filepath.replace('\\', '/').replace('/', '.').removesuffix('.py')
        if mod.endswith('.__init__'):
            mod = mod[:-9]
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        syms: Set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                syms.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        syms.add(t.id)
        module_symbols[mod] = syms

    for filepath, content in files.items():
        if not filepath.endswith('.py') or not isinstance(content, str):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            errors.append(f"{filepath}: SyntaxError: {e}")
            continue

        # Check project-local from...import statements
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ''
            if not module.startswith('app.') and module != 'app':
                continue
            target_syms = module_symbols.get(module)
            if target_syms is None:
                errors.append(
                    f"{filepath}: module '{module}' not found in generated files"
                )
                continue
            for alias in node.names:
                sym = alias.name
                if sym != '*' and sym not in target_syms:
                    errors.append(
                        f"{filepath}: '{sym}' imported from '{module}' but not defined there"
                    )

    return errors


def _detect_project_language(code_path: Path) -> str:
    """
    Detect the primary programming language of a project by counting files.
    
    Args:
        code_path: Path to the project directory
        
    Returns:
        Language name (e.g., "python", "typescript", "javascript")
        Defaults to "python" if unable to determine
    """
    if not code_path.exists():
        logger.warning(f"Code path {code_path} does not exist, defaulting to python")
        return "python"
    
    # Count files by extension
    file_counts = {}
    for language, patterns in LANGUAGE_FILE_EXTENSIONS.items():
        count = 0
        for pattern in patterns:
            count += len(list(code_path.rglob(pattern)))
        file_counts[language] = count
    
    # Find the language with the most files
    if not file_counts or max(file_counts.values()) == 0:
        logger.info(f"No recognized source files found in {code_path}, defaulting to python")
        return "python"
    
    detected_language = max(file_counts, key=file_counts.get)
    logger.info(
        f"Detected project language: {detected_language} "
        f"(file counts: {file_counts})"
    )
    return detected_language


def _is_test_file(file_path: Path, language: str) -> bool:
    """
    Check if a file is a test file based on language-specific patterns.
    
    Args:
        file_path: Path to the file
        language: Programming language
        
    Returns:
        True if the file is a test file, False otherwise
    """
    pattern_func = TEST_FILE_PATTERNS.get(language)
    if pattern_func:
        return pattern_func(file_path)
    # Default fallback for unknown languages
    return file_path.name.startswith("test_") or "test" in file_path.name.lower()


def _ensure_python_package_structure(output_dir: Path) -> None:
    """
    Ensure all directories with .py files have __init__.py for proper package imports.
    
    This function is critical for generated Python code that uses relative imports
    (e.g., `from .routes import router`). Without __init__.py files, Python raises
    ImportError when trying to import from packages.
    
    Args:
        output_dir: Root directory to scan for Python files
    """
    if not output_dir.exists():
        logger.warning(f"Output directory {output_dir} does not exist, skipping __init__.py creation")
        return
    
    skip_dirs = {'__pycache__', '.git', '.pytest_cache', '.mypy_cache', 
                 'node_modules', '.venv', 'venv', 'env', 'reports'}
    
    # Track directories where we create __init__.py files
    created_count = 0
    dirs_with_python = set()
    
    # First pass: collect all directories that contain Python files
    if any(output_dir.glob('*.py')):
        dirs_with_python.add(output_dir)
    
    for subdir in output_dir.rglob('*'):
        if subdir.is_dir() and subdir.name not in skip_dirs:
            has_python_files = any(subdir.glob('*.py'))
            if has_python_files:
                dirs_with_python.add(subdir)
    
    # Second pass: for each directory with Python files, ensure all parent dirs
    # up to output_dir also have __init__.py (for proper package hierarchy)
    all_package_dirs = set()
    for pydir in dirs_with_python:
        current = pydir
        while current != output_dir and current.parent != output_dir.parent:
            all_package_dirs.add(current)
            current = current.parent
        all_package_dirs.add(output_dir)  # Include root
    
    # Create __init__.py in all package directories
    for pkg_dir in sorted(all_package_dirs):
        if pkg_dir.name not in skip_dirs:
            init_file = pkg_dir / '__init__.py'
            if not init_file.exists():
                init_file.write_text('# Auto-generated for package imports\n')
                logger.debug(f"Created __init__.py in {pkg_dir}")
                created_count += 1
    
    if created_count > 0:
        logger.info(f"Created {created_count} __init__.py files in {output_dir}")


def _load_readme_from_disk(job_dir: Path) -> Optional[str]:
    """
    Load README content from a job directory.
    
    Args:
        job_dir: Path to the job directory
        
    Returns:
        README content as string, or None if not found
    """
    if not job_dir.exists():
        return None
    
    # Priority order for README files
    readme_patterns = ["README.md", "readme.md", "README.txt", "readme.txt"]
    
    # Try exact filename matches first
    for pattern in readme_patterns:
        readme_path = job_dir / pattern
        if readme_path.exists() and readme_path.is_file():
            try:
                return readme_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Error reading {readme_path}: {e}")
                continue
    
    # Fallback: find any .md file
    try:
        for f in job_dir.glob("*.md"):
            if f.is_file():
                return f.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error scanning for .md files in {job_dir}: {e}")
    
    return None


def _extract_project_name_from_path_or_payload(
    payload: Dict[str, Any],
    default: Optional[str] = None
) -> Optional[str]:
    """
    Extract project name from payload in a consistent way.

    This function implements the correct logic to determine project name:
    1. Try package_name or package field from payload
    2. Extract last component from output_dir path
    3. Return default (None by default — callers must decide the fallback)

    Args:
        payload: Job payload containing output_dir, package_name, etc.
        default: Value to return when no name can be determined (default: None).
                 Callers that previously relied on the old "generated_project"
                 fallback should pass that value explicitly; new callers should
                 handle None and avoid creating a spurious subdirectory.

    Returns:
        Project name as a string (just the name, not a path), or ``default``

    Example:
        >>> _extract_project_name_from_path_or_payload({"output_dir": "generated/my_app"})
        'my_app'
        >>> _extract_project_name_from_path_or_payload({"package_name": "user_service"})
        'user_service'
        >>> _extract_project_name_from_path_or_payload({}) is None
        True
    """
    project_name = None

    # Priority 1: Check for package_name or package field
    project_name = payload.get("package_name") or payload.get("package")

    # Priority 2: Extract from output_dir (take last path component)
    if not project_name:
        output_dir = payload.get("output_dir", "").strip()
        if output_dir:
            # Handle both forward and backward slashes, remove leading/trailing slashes
            path_parts = output_dir.replace("\\", "/").strip("/").split("/")
            # Filter out empty parts and take the last non-empty one
            path_parts = [p for p in path_parts if p]
            if path_parts:
                project_name = path_parts[-1]

    # Priority 3: Use caller-supplied default (may be None)
    if not project_name:
        project_name = default

    return project_name


def _generate_fallback_readme(project_name: str = "generated_project", 
                                language: str = "python",
                                output_path: Optional[str] = None) -> str:
    """
    Generate a deterministic fallback README when DocGen fails or times out.
    
    This function creates a complete README directly from project metadata
    without requiring LLM generation. It uses Jinja2 templating if available,
    otherwise falls back to string formatting.
    
    Args:
        project_name: Name of the generated project
        language: Programming language of the project
        output_path: Path to the generated project (for scanning files/deps)
        
    Returns:
        Complete README content as a string
    """
    # Scan project for additional metadata if path provided
    endpoints = []
    dependencies = []
    file_list = []
    
    if output_path:
        output_path_obj = Path(output_path)
        if output_path_obj.exists():
            # Scan for Python files
            py_files = list(output_path_obj.rglob("*.py"))
            file_list = [str(f.relative_to(output_path_obj)) for f in py_files[:MAX_FILES_IN_README]]
            
            # Try to extract endpoints from main.py or app/main.py
            for main_file in [output_path_obj / "main.py", output_path_obj / "app" / "main.py"]:
                if main_file.exists():
                    try:
                        content = main_file.read_text(encoding="utf-8")
                        # Simple regex to find FastAPI route decorators
                        endpoint_patterns = [
                            r'@app\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                            r'@router\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                        ]
                        for pattern in endpoint_patterns:
                            matches = re.findall(pattern, content)
                            for method, path in matches:
                                endpoints.append(f"{method.upper()} {path}")
                    except Exception as e:
                        logger.debug(f"Could not extract endpoints from {main_file}: {e}")
            
            # Try to read requirements.txt
            req_file = output_path_obj / "requirements.txt"
            if req_file.exists():
                try:
                    deps_content = req_file.read_text(encoding="utf-8")
                    # Extract package names (ignore versions and comments)
                    for line in deps_content.split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            pkg = line.split('==')[0].split('>=')[0].split('<=')[0].strip()
                            if pkg:
                                dependencies.append(pkg)
                except Exception as e:
                    logger.debug(f"Could not read requirements.txt: {e}")
    
    # Use Jinja2 template if available
    if JINJA2_AVAILABLE and Template:
        template_str = """# {{ project_name }}

A {{ language }} application generated by The Code Factory.

## Description

This project was automatically generated and includes a complete application structure with:
- RESTful API endpoints{% if endpoints %}
- {{ endpoints|length }} defined routes{% endif %}
- Dependency management
- Testing infrastructure
- Deployment configuration (Docker, Kubernetes, Helm)

## Installation

1. Clone the repository or extract the generated archive

2. Install dependencies:
```bash
pip install -r requirements.txt
```

{% if dependencies %}
### Dependencies

The project includes the following key dependencies:
{% for dep in dependencies %}
- {{ dep }}
{% endfor %}
{% endif %}

## Running the Application

Start the development server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or if using the app/ structure:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The API will be available at: `http://localhost:8000`

### Health Check

Test the health check endpoint:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "healthy"}
```

{% if endpoints %}
## API Endpoints

The following endpoints are available:

{% for endpoint in endpoints %}
- {{ endpoint }}
{% endfor %}

For complete API documentation, visit: `http://localhost:8000/docs`
{% endif %}

## Testing

Run the test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=. --cov-report=html
```

## Deployment

### Docker

Build the Docker image:

```bash
docker build -t {{ project_name }}:latest .
```

Run the container:

```bash
docker run -p 8000:8000 {{ project_name }}:latest
```

### Kubernetes

Deploy to Kubernetes:

```bash
kubectl apply -f k8s/
```

### Helm

Install using Helm:

```bash
helm install {{ project_name }} ./helm
```

## Project Structure

{% if file_list %}
Key files:
{% for file in file_list %}
- {{ file }}
{% endfor %}
{% endif %}

## License

See LICENSE file for details.

## Support

For issues or questions, please refer to the project documentation or contact the development team.
"""
        template = Template(template_str)
        return template.render(
            project_name=project_name,
            language=language,
            endpoints=endpoints,
            dependencies=dependencies[:MAX_DEPENDENCIES_IN_README],  # Limit dependencies shown
            file_list=file_list  # Already limited when created
        )
    
    # Fallback: Simple string formatting without Jinja2
    readme = f"""# {project_name}

A {language} application generated by The Code Factory.

## Description

This project was automatically generated and includes a complete application structure with:
- RESTful API endpoints
- Dependency management
- Testing infrastructure
- Deployment configuration (Docker, Kubernetes, Helm)

## Installation

1. Clone the repository or extract the generated archive

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running the Application

Start the development server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or if using the app/ structure:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The API will be available at: `http://localhost:8000`

### Health Check

Test the health check endpoint:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{{"status": "healthy"}}
```

## API Endpoints

For complete API documentation, visit: `http://localhost:8000/docs`

## Testing

Run the test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=. --cov-report=html
```

## Deployment

### Docker

Build the Docker image:

```bash
docker build -t {project_name}:latest .
```

Run the container:

```bash
docker run -p 8000:8000 {project_name}:latest
```

### Kubernetes

Deploy to Kubernetes:

```bash
kubectl apply -f k8s/
```

### Helm

Install using Helm:

```bash
helm install {project_name} ./helm
```

## License

See LICENSE file for details.

## Support

For issues or questions, please refer to the project documentation or contact the development team.
"""
    return readme


def _generate_fallback_frontend_files(
    output_path: str, 
    missing_files: Set[str],
    project_name: str = "Generated App"
) -> Dict[str, bool]:
    """
    Generate fallback frontend files when codegen didn't produce them.
    
    This function creates basic frontend file templates when the spec requires
    frontend files but they weren't generated. Uses industry-standard templates.
    
    Args:
        output_path: Path to the generated project directory
        missing_files: Set of missing frontend file names
        project_name: Name of the project for template content
        
    Returns:
        Dictionary mapping file names to success/failure status
    """
    results = {}
    output_dir = Path(output_path)
    
    # Template for index.html - Bootstrap-based responsive template
    INDEX_HTML_TEMPLATE = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{project_name}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container">
            <a class="navbar-brand" href="#">{project_name}</a>
        </div>
    </nav>
    
    <main class="container mt-4">
        <h1>Welcome to {project_name}</h1>
        <p class="lead">This frontend was auto-generated to meet spec requirements.</p>
        
        <div id="app-content">
            <!-- Dynamic content will be loaded here -->
        </div>
    </main>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="app.js"></script>
</body>
</html>'''

    # Template for style.css
    STYLE_CSS_TEMPLATE = '''/* Generated CSS for the application */
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
}

main {
    flex: 1;
}

.navbar-brand {
    font-weight: bold;
}

#app-content {
    padding: 20px 0;
}

/* Responsive adjustments */
@media (max-width: 768px) {
    .container {
        padding: 0 15px;
    }
}'''

    # Template for app.js
    APP_JS_TEMPLATE = '''// Generated JavaScript for the application
document.addEventListener("DOMContentLoaded", function() {
    console.log("Application initialized");
    
    // API base URL - adjust based on your backend
    const API_BASE = window.location.origin;
    
    // Example: Load data from API
    async function loadData() {
        try {
            const response = await fetch(`${API_BASE}/api/health`);
            if (response.ok) {
                const data = await response.json();
                console.log("API health check:", data);
            }
        } catch (error) {
            console.log("API not available or running on different port");
        }
    }
    
    loadData();
});'''

    TEMPLATES = {
        'index.html': INDEX_HTML_TEMPLATE,
        'style.css': STYLE_CSS_TEMPLATE,
        'app.js': APP_JS_TEMPLATE,
    }
    
    # Determine where to place frontend files
    # Check for existing structure
    possible_dirs = [
        output_dir / "templates",
        output_dir / "static",
        output_dir / "public",
        output_dir / "app" / "templates",
        output_dir / "app" / "static",
        output_dir,  # Fallback to root
    ]
    
    # Find or create target directory
    target_dir = output_dir  # Default
    for pdir in possible_dirs:
        if pdir.exists():
            target_dir = pdir
            break
    
    # For index.html, prefer templates directory
    html_target = target_dir
    css_js_target = target_dir
    
    # If templates exists, put HTML there, CSS/JS in static
    templates_dir = output_dir / "templates"
    static_dir = output_dir / "static"
    
    if templates_dir.exists():
        html_target = templates_dir
    else:
        # Create templates if we're generating index.html
        if 'index.html' in missing_files:
            templates_dir.mkdir(parents=True, exist_ok=True)
            html_target = templates_dir
    
    if static_dir.exists():
        css_js_target = static_dir
    elif 'style.css' in missing_files or 'app.js' in missing_files:
        static_dir.mkdir(parents=True, exist_ok=True)
        css_js_target = static_dir
    
    # Generate missing files
    for filename in missing_files:
        if filename not in TEMPLATES:
            results[filename] = False
            continue
        
        try:
            if filename == 'index.html':
                file_path = html_target / filename
            else:
                file_path = css_js_target / filename
            
            file_path.write_text(TEMPLATES[filename], encoding='utf-8')
            results[filename] = True
            logger.info(f"Generated fallback frontend file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to generate fallback {filename}: {e}")
            results[filename] = False
    
    return results


def _create_placeholder_critique_report(job_id: str, message: str) -> Dict[str, Any]:
    """
    Create a placeholder critique report structure.
    
    This helper function creates a standardized placeholder report when
    critique is skipped, fails, or is not requested. This ensures that
    reports/critique_report.json always exists with a valid structure
    that conforms to the expected report schema.
    
    Industry Standards Applied:
    - Input validation: Ensures parameters are valid
    - Schema compliance: Report structure matches successful critique reports
    - Defensive programming: Returns valid report even with invalid inputs
    - ISO 8601 timestamps: Industry standard for date/time representation
    
    Args:
        job_id: The job identifier (should not be empty)
        message: The reason the critique was not performed (descriptive text)
        
    Returns:
        Dictionary containing the placeholder report structure with all
        required fields populated with appropriate default values.
        
    Example:
        >>> report = _create_placeholder_critique_report("job-123", "Critique not requested")
        >>> assert report["job_id"] == "job-123"
        >>> assert report["status"] == "skipped"
        >>> assert "timestamp" in report
    
    Raises:
        ValueError: If job_id is empty or None (defensive programming)
    """
    # Input validation - Industry Standard: Fail fast with clear errors
    if not job_id:
        raise ValueError("job_id cannot be empty or None")
    
    if not isinstance(job_id, str):
        raise TypeError(f"job_id must be a string, got {type(job_id)}")
    
    # Allow empty message but log warning
    if not message:
        logger.warning(f"Creating placeholder report for job {job_id} with empty message")
        message = "No message provided"
    
    # Create report with standardized structure
    report = {
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "skipped",
        "message": message,
        "issues_found": 0,
        "issues_fixed": 0,
        "coverage": {
            "total_lines": 0,
            "covered_lines": 0,
            "percentage": 0.0
        },
        "test_results": {
            "total": 0,
            "passed": 0,
            "failed": 0
        },
        "issues": [],
        "fixes_applied": [],
        "scan_types": []
    }
    
    logger.debug(f"Created placeholder critique report for job {job_id}: {message}")
    
    return report


# ============================================================================
# INDUSTRY STANDARD: Input Validation Utilities
# Following defensive programming and contract-based design principles
# ============================================================================

def _validate_report_structure(report: Any, report_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Validate SFE analysis report structure with comprehensive checks.
    
    Industry Standard: Defense-in-depth validation following OWASP guidelines.
    Validates both structure and data types before trusting cached data.
    
    Args:
        report: Parsed JSON report data (can be any type)
        report_path: Path to report file (for error messages)
        
    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
        If valid, error_message is None. If invalid, error_message explains why.
        
    Validation Rules:
        1. Report must be a dictionary
        2. Must contain 'all_defects' or 'issues' key
        3. Issues must be a list
        4. Each issue should be a dictionary (warning if not)
        
    Examples:
        >>> report = {"all_defects": [{"type": "error", "file": "test.py"}]}
        >>> _validate_report_structure(report, Path("report.json"))
        (True, None)
        
        >>> report = "not a dict"
        >>> _validate_report_structure(report, Path("report.json"))
        (False, "Invalid report format: expected dict, got str")
    """
    # Validation Rule 1: Must be a dictionary
    if not isinstance(report, dict):
        return False, f"Invalid report format: expected dict, got {type(report).__name__}"
    
    # Validation Rule 2: Must contain issues data
    if "all_defects" not in report and "issues" not in report:
        return False, "Report missing required key: 'all_defects' or 'issues'"
    
    # Get issues list (prefer all_defects, fallback to issues)
    issues = report.get("all_defects", report.get("issues", []))
    
    # Validation Rule 3: Issues must be a list
    if not isinstance(issues, list):
        return False, f"Invalid issues format: expected list, got {type(issues).__name__}"
    
    # Validation Rule 4: Warn if issues contain non-dict items (but don't fail)
    if issues:
        non_dict_count = sum(1 for item in issues if not isinstance(item, dict))
        if non_dict_count > 0:
            logger.warning(
                f"Report contains {non_dict_count} non-dict issues",
                extra={
                    "report_path": str(report_path),
                    "total_issues": len(issues),
                    "non_dict_count": non_dict_count
                }
            )
    
    return True, None


def _validate_helm_chart_structure(chart_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate Helm chart data structure against Helm specification.
    
    Industry Standard: Schema validation following Helm chart specification.
    Ensures generated charts are valid before writing to disk.
    
    Args:
        chart_data: Parsed Helm chart dictionary
        
    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
        
    Validation Rules:
        1. Must be a dictionary
        2. Must contain required fields: apiVersion, name
        3. apiVersion should be 'v2' for Helm 3
        4. name should be a non-empty string
        
    Reference:
        https://helm.sh/docs/topics/charts/#the-chartyaml-file
    """
    if not isinstance(chart_data, dict):
        return False, f"Chart must be a dict, got {type(chart_data).__name__}"
    
    # Check required fields
    missing_fields = HELM_REQUIRED_FIELDS - chart_data.keys()
    if missing_fields:
        return False, f"Missing required fields: {missing_fields}"
    
    # Validate apiVersion
    api_version = chart_data.get("apiVersion")
    if not isinstance(api_version, str) or not api_version:
        return False, "apiVersion must be a non-empty string"
    
    # Validate name
    name = chart_data.get("name")
    if not isinstance(name, str) or not name:
        return False, "name must be a non-empty string"
    
    return True, None


def _load_sfe_analysis_report(
    report_path: Path,
    job_id: str,
    max_file_size: int = MAX_REPORT_FILE_SIZE_BYTES
) -> Optional[Dict[str, Any]]:
    """
    Load and validate SFE analysis report with comprehensive error handling.
    
    Industry Standard: DRY principle - centralized report loading logic
    with defense-in-depth validation. Eliminates code duplication between
    omnicore_service and sfe_service.
    
    Args:
        report_path: Path to sfe_analysis_report.json
        job_id: Job identifier for logging context
        max_file_size: Maximum allowed file size in bytes
        
    Returns:
        Dictionary containing report data with keys:
        - issues: List of detected issues/defects
        - count: Number of issues
        - source: "sfe_analysis_report"
        - cached: True
        Returns None if report doesn't exist or is invalid.
        
    Validation Steps:
        1. Check file exists and is a regular file
        2. Validate file size (prevent DoS)
        3. Parse JSON with error handling
        4. Validate report structure
        5. Extract and validate issues list
        
    Side Effects:
        Logs warnings for validation failures (non-fatal)
        
    Examples:
        >>> report = _load_sfe_analysis_report(Path("report.json"), "job-123")
        >>> if report:
        ...     print(f"Found {report['count']} issues")
    """
    # Validation Step 1: File existence
    if not report_path.exists() or not report_path.is_file():
        return None
    
    try:
        # Validation Step 2: File size check (DoS prevention)
        file_size = report_path.stat().st_size
        if file_size > max_file_size:
            logger.warning(
                f"[SFE] Analysis report file too large ({file_size} bytes), skipping cache",
                extra={
                    "job_id": job_id,
                    "file_size": file_size,
                    "max_size": max_file_size,
                    "report_path": str(report_path)
                }
            )
            return None
        
        # Validation Step 3: Parse JSON
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        
        # Validation Step 4: Validate report structure
        is_valid, error_msg = _validate_report_structure(report, report_path)
        if not is_valid:
            raise ValueError(error_msg)
        
        # Validation Step 5: Extract issues
        issues = report.get("all_defects", report.get("issues", []))
        
        logger.info(
            f"[SFE] Loaded {len(issues)} issues from cached analysis report",
            extra={
                "job_id": job_id,
                "issue_count": len(issues),
                "report_age_seconds": (datetime.now(timezone.utc).timestamp() - 
                                      report_path.stat().st_mtime),
                "source": "cache"
            }
        )
        
        return {
            "issues": issues,
            "count": len(issues),
            "source": SOURCE_CACHE,
            "cached": True,
        }
        
    except json.JSONDecodeError as e:
        logger.warning(
            f"[SFE] Invalid JSON in analysis report: {e}",
            extra={
                "job_id": job_id,
                "report_path": str(report_path),
                "error": str(e)
            }
        )
    except (IOError, OSError) as e:
        logger.warning(
            f"[SFE] Failed to read analysis report: {type(e).__name__}: {e}",
            extra={
                "job_id": job_id,
                "report_path": str(report_path),
                "error_type": type(e).__name__
            }
        )
    except ValueError as e:
        logger.warning(
            f"[SFE] Invalid report structure: {e}",
            extra={
                "job_id": job_id,
                "report_path": str(report_path),
                "error": str(e)
            }
        )
    except Exception as e:
        logger.warning(
            f"[SFE] Unexpected error loading report: {type(e).__name__}: {e}",
            extra={
                "job_id": job_id,
                "report_path": str(report_path),
                "error_type": type(e).__name__
            },
            exc_info=True
        )
    
    return None


def _invalidate_sfe_analysis_cache(job_path: Path, job_id: str) -> None:
    """Delete the cached SFE analysis report so the next detect_errors re-analyzes."""
    report_path = job_path / "reports" / "sfe_analysis_report.json"
    if report_path.exists():
        try:
            os.remove(report_path)
            logger.info(
                f"[SFE] Invalidated cached analysis report for job {job_id}",
                extra={"job_id": job_id, "report_path": str(report_path)},
            )
        except OSError as e:
            logger.warning(
                f"[SFE] Could not delete cached analysis report for job {job_id}: {e}",
                extra={"job_id": job_id},
            )


def _fix_double_nesting(output_dir: Path) -> None:
    """Flatten a nested 'generated/' subdirectory inside output_dir.

    After materialization the LLM-generated code may place files under
    ``output_dir/generated/`` which causes a double-nesting pattern such as
    ``<job>/generated/my_app/generated/…``.  This helper detects that situation
    and moves the contents of the inner ``generated/`` directory up one level,
    then removes the now-empty directory.
    """
    nested_generated = output_dir / "generated"
    if nested_generated.is_dir():
        logger.warning(
            f"Double-nesting detected at {nested_generated}, flattening..."
        )
        for item in os.listdir(nested_generated):
            src = nested_generated / item
            dst = output_dir / item
            if not dst.exists():
                shutil.move(str(src), str(dst))
            else:
                logger.warning(
                    f"Double-nesting flatten: skipping '{item}' — destination already exists at {dst}"
                )
        try:
            if not os.listdir(nested_generated):
                os.rmdir(nested_generated)
        except OSError:
            pass


def _is_third_party_import_error(error_str: str) -> bool:
    """Return True if *error_str* describes a missing third-party package.

    A third-party import error matches ``ModuleNotFoundError: No module named 'X'``
    where ``X`` is NOT a project-local module (i.e., does not start with ``app``,
    ``tests``, or ``server``).

    Args:
        error_str: A validation error message string.

    Returns:
        True if the error is caused by a missing third-party package.
    """
    match = re.search(r"No module named '([^']+)'", error_str)
    if not match:
        return False
    module_top = match.group(1).split(".")[0]
    # Project-local prefixes that should NOT be treated as third-party
    _LOCAL_PREFIXES = {"app", "tests", "test", "server", "generator", "self_fixing_engineer"}
    return module_top not in _LOCAL_PREFIXES


class OmniCoreService:
    """
    Service for interacting with the OmniCore Engine.

    This service acts as an abstraction layer for OmniCore operations,
    coordinating between generator and SFE modules via the message bus.
    The implementation includes proper agent integration with configuration-based
    LLM provider selection and graceful degradation.
    """

    def __init__(self):
        """Initialize the OmniCoreService with agent availability checks."""
        logger.info("OmniCoreService initializing...")
        
        # Load configuration
        self.agent_config = get_agent_config() if CONFIG_AVAILABLE else None
        self.llm_config = get_llm_config() if CONFIG_AVAILABLE else None
        
        # Track agent availability
        self.agents_available = {
            "codegen": False,
            "testgen": False,
            "deploy": False,
            "docgen": False,
            "critique": False,
            "clarifier": False,
        }
        
        # FIX: Track jobs currently in pipeline to prevent concurrent runs
        self._jobs_in_pipeline: set = set()
        
        # Track LLM provider status
        self._llm_status = {
            "provider": None,
            "configured": False,
            "validated": False,
            "error": None,
        }
        
        # Initialize core OmniCore components
        self._message_bus = None
        self._plugin_registry = None
        self._metrics_client = None
        self._audit_client = None
        self._omnicore_components_available = {
            "message_bus": False,
            "plugin_registry": False,
            "metrics": False,
            "audit": False,
        }
        
        # Initialize storage path (following GeneratorService pattern)
        # Use centralized config if available, otherwise fallback to default
        self.storage_path = self.agent_config.upload_dir if self.agent_config else Path("./uploads")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Storage path initialized: {self.storage_path}")
        
        # Initialize Kafka producer if configured
        self.kafka_producer = None
        self._init_kafka_producer()
        
        # Validate LLM provider configuration
        self._validate_llm_configuration()
        
        # DON'T call _load_agents() here to avoid circular imports
        self._agents_loaded = False  # Track if agents have been loaded
        self._agent_loading_error: Optional[str] = None  # Capture any agent loading error
        
        # Initialize OmniCore integrations
        self._init_omnicore_components()
        
        # Log that agents will be loaded on-demand
        logger.info("OmniCore initialized - agents will be loaded on demand")
        
        # Log system state and what triggers agent execution
        self._log_system_ready_state()
    
    def _validate_llm_configuration(self):
        """
        Validate LLM provider configuration and log status.
        
        This helps diagnose issues where agents load but fail silently
        due to missing or invalid API keys.
        """
        provider = None
        api_key_configured = False
        
        if self.llm_config:
            provider = self.llm_config.default_llm_provider
            api_key_configured = self.llm_config.is_provider_configured(provider)
            
            if not api_key_configured:
                # Try auto-detection
                auto_provider = detect_available_llm_provider()
                if auto_provider:
                    provider = auto_provider
                    api_key_configured = True
                    logger.info(f"Auto-detected LLM provider: {auto_provider}")
        else:
            # Check environment directly
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                provider = auto_provider
                api_key_configured = True
        
        # Use explicit status when no provider is configured
        if api_key_configured:
            self._llm_status["provider"] = provider
        else:
            # Keep the intended provider for diagnostics, but indicate it's not configured
            self._llm_status["provider"] = provider or "none"
        
        self._llm_status["configured"] = api_key_configured
        
        if api_key_configured:
            logger.info(f"✓ LLM provider '{provider}' is configured with API key")
        else:
            intended_provider = provider or "openai (default)"
            logger.warning(
                f"⚠ LLM provider '{intended_provider}' API key NOT configured. "
                "Agents will load but may fail when executing jobs."
            )
            logger.warning(
                "To configure an LLM provider, set one of the following environment variables:\n"
                "  - OPENAI_API_KEY for OpenAI (GPT-4)\n"
                "  - ANTHROPIC_API_KEY for Anthropic (Claude)\n"
                "  - XAI_API_KEY or GROK_API_KEY for xAI (Grok)\n"
                "  - GOOGLE_API_KEY for Google (Gemini)\n"
                "  - OLLAMA_HOST for Ollama (local LLM)"
            )
            self._llm_status["error"] = "API key not configured"
    
    def _log_system_ready_state(self):
        """
        Log the system's ready state and clarify what triggers agent execution.
        
        This helps users understand that the system is idle and waiting for input.
        """
        # Build LLM status message
        if self._llm_status["configured"]:
            llm_msg = f"LLM Provider: {self._llm_status['provider']} (configured)"
        else:
            llm_msg = f"LLM Provider: {self._llm_status['provider']} (NOT CONFIGURED - jobs will fail)"
        
        # Build agent status message
        available_agents = [k for k, v in self.agents_available.items() if v]
        agents_msg = ', '.join(available_agents) if available_agents else 'None'
        
        # Log as a single structured message for better log readability
        status_message = (
            "\n"
            "============================================================\n"
            "SYSTEM STATUS: Ready and waiting for input\n"
            "============================================================\n"
            f"  {llm_msg}\n"
            f"  Available Agents: {agents_msg}\n"
            "\n"
            "IMPORTANT: Agents are now PASSIVE and waiting for jobs.\n"
            "No code will be generated until you submit a job request.\n"
            "\n"
            "To trigger code generation, use one of these methods:\n"
            "  1. POST /api/jobs/ - Create a new job\n"
            "  2. POST /api/generator/upload - Upload a README file\n"
            "  3. POST /api/omnicore/route - Route a job directly\n"
            "\n"
            "Monitor job status at: GET /api/jobs/{job_id}/progress\n"
            "============================================================"
        )
        
        if self._llm_status["configured"]:
            logger.info(status_message)
        else:
            logger.warning(status_message)
    
    def _init_kafka_producer(self):
        """Initialize Kafka producer if configured."""
        try:
            kafka_enabled = os.getenv("KAFKA_ENABLED", "false").lower() == "true"
            if kafka_enabled:
                bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
                # Import and initialize only if enabled
                try:
                    from aiokafka import AIOKafkaProducer
                    # Note: Actual connection happens in async context via start()
                    # For now, we just store the configuration
                    self.kafka_producer = {
                        "bootstrap_servers": bootstrap_servers,
                        "enabled": True,
                    }
                    logger.info(f"Kafka producer configured with servers: {bootstrap_servers}")
                except ImportError:
                    logger.warning("aiokafka not installed - Kafka producer unavailable")
                    self.kafka_producer = None
            else:
                logger.info("Kafka disabled - SFE dispatch will use HTTP fallback")
                self.kafka_producer = None
        except Exception as e:
            logger.warning(f"Failed to initialize Kafka producer: {e}")
            self.kafka_producer = None
    
    def _load_agents(self):
        """
        Attempt to load all agent modules and track availability.
        
        This method tries to import each agent and marks it as available
        if the import succeeds. Failures are logged but don't prevent
        service initialization unless strict_mode is enabled.
        """
        # Try loading codegen agent
        try:
            from generator.agents.codegen_agent.codegen_agent import generate_code
            self._codegen_func = generate_code
            self.agents_available["codegen"] = True
            logger.info("✓ Codegen agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Codegen agent unavailable: {e}")
            self._codegen_func = None
        except Exception as e:
            logger.error(f"Unexpected error loading codegen agent: {e}", exc_info=True)
            self._codegen_func = None
        
        # Try loading testgen agent
        try:
            from generator.agents.testgen_agent.testgen_agent import TestgenAgent, Policy
            self._testgen_class = TestgenAgent
            self._testgen_policy_class = Policy
            self.agents_available["testgen"] = True
            logger.info("✓ Testgen agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Testgen agent unavailable: {e}")
            self._testgen_class = None
            self._testgen_policy_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading testgen agent: {e}", exc_info=True)
            self._testgen_class = None
            self._testgen_policy_class = None
        
        # Try loading deploy agent
        try:
            from generator.agents.deploy_agent.deploy_agent import DeployAgent
            self._deploy_class = DeployAgent
            self.agents_available["deploy"] = True
            logger.info("✓ Deploy agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Deploy agent unavailable: {e}")
            self._deploy_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading deploy agent: {e}", exc_info=True)
            self._deploy_class = None
        
        # Try loading docgen agent
        try:
            from generator.agents.docgen_agent.docgen_agent import DocgenAgent
            self._docgen_class = DocgenAgent
            self.agents_available["docgen"] = True
            logger.info("✓ Docgen agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Docgen agent unavailable: {e}")
            self._docgen_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading docgen agent: {e}", exc_info=True)
            self._docgen_class = None
        
        # Try loading critique agent
        try:
            from generator.agents.critique_agent.critique_agent import CritiqueAgent
            self._critique_class = CritiqueAgent
            self.agents_available["critique"] = True
            logger.info("✓ Critique agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Critique agent unavailable: {e}")
            self._critique_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading critique agent: {e}", exc_info=True)
            self._critique_class = None
        
        # Try loading clarifier (prefer LLM-based if configured)
        use_llm_clarifier = (
            self.agent_config and 
            self.agent_config.use_llm_clarifier and
            self.llm_config and
            self.llm_config.get_available_providers()
        )
        
        if use_llm_clarifier:
            try:
                from generator.clarifier.clarifier_llm import GrokLLM
                self._clarifier_llm_class = GrokLLM
                self.agents_available["clarifier"] = True
                logger.info("✓ LLM-based clarifier loaded successfully")
            except ImportError as e:
                logger.warning(f"LLM clarifier unavailable, will use rule-based: {e}")
                self._clarifier_llm_class = None
                # Rule-based clarifier is always available as fallback
                self.agents_available["clarifier"] = True
            except Exception as e:
                logger.error(f"Unexpected error loading LLM clarifier: {e}", exc_info=True)
                self._clarifier_llm_class = None
                self.agents_available["clarifier"] = True
        else:
            logger.info("Using rule-based clarifier (LLM clarifier not configured)")
            self._clarifier_llm_class = None
            self.agents_available["clarifier"] = True
    
    def _ensure_agents_loaded(self):
        """Lazy-load agents on first use to avoid circular imports."""
        if not self._agents_loaded:
            logger.info("Loading agents on demand...")
            try:
                self._load_agents()
                self._agents_loaded = True
            except Exception as exc:
                self._agent_loading_error = str(exc)
                logger.error("Agent loading failed: %s", exc, exc_info=True)
                return
            
            # Log initialization status after loading
            available = [k for k, v in self.agents_available.items() if v]
            unavailable = [k for k, v in self.agents_available.items() if not v]
            
            if available:
                logger.info(f"Agents loaded. Available: {', '.join(available)}")
            if unavailable:
                logger.warning(f"Some agents unavailable: {', '.join(unavailable)}")
                if self.agent_config and self.agent_config.strict_mode:
                    raise RuntimeError(
                        f"STRICT_MODE: Required agents are unavailable: {', '.join(unavailable)}. "
                        f"Install required dependencies or disable strict mode."
                    )
    
    def _build_llm_config(self) -> Dict[str, Any]:
        """
        Build LLM configuration dict for agents from our config.
        Auto-detects available LLM provider if default is not configured.
        
        Returns:
            Configuration dictionary compatible with agent requirements
        """
        if not self.llm_config:
            # Fallback configuration when config module not available
            # Try to auto-detect from environment
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                logger.info(f"Auto-detected LLM provider: {auto_provider}")
                return {
                    "backend": auto_provider,
                    "model": {auto_provider: get_default_model_for_provider(auto_provider)},
                    "ensemble_enabled": False,
                }
            else:
                logger.warning("No LLM provider configured or auto-detected")
                return {
                    "backend": "openai",
                    "model": {"openai": "gpt-4o"},
                    "ensemble_enabled": False,
                }
        
        provider = self.llm_config.default_llm_provider
        
        # Auto-detect if the default provider is not configured
        if not self.llm_config.is_provider_configured(provider):
            logger.warning(
                f"Default LLM provider '{provider}' is not configured. "
                "Attempting auto-detection..."
            )
            
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                logger.info(f"Auto-detected LLM provider: {auto_provider}")
                provider = auto_provider
                # Update model to match auto-detected provider
                model = self.llm_config.get_provider_model(provider)
            else:
                logger.error(
                    "No LLM provider configured. Please set API keys in environment variables:\n"
                    "  - OPENAI_API_KEY for OpenAI\n"
                    "  - ANTHROPIC_API_KEY for Anthropic/Claude\n"
                    "  - XAI_API_KEY or GROK_API_KEY for xAI/Grok\n"
                    "  - GOOGLE_API_KEY for Google/Gemini\n"
                    "  - OLLAMA_HOST for Ollama (local)"
                )
                # Use default provider anyway, might be mocked
                model = self.llm_config.get_provider_model(provider)
        else:
            model = self.llm_config.get_provider_model(provider)
            logger.info(f"Using configured LLM provider: {provider} with model: {model}")
        
        api_key = self.llm_config.get_provider_api_key(provider)
        
        # Set environment variable for the agent to use
        if api_key:
            # For xAI/Grok, set both XAI_API_KEY and GROK_API_KEY
            if provider == "grok":
                os.environ["XAI_API_KEY"] = api_key
                os.environ["GROK_API_KEY"] = api_key
            else:
                env_var = f"{provider.upper()}_API_KEY"
                os.environ[env_var] = api_key
        
        # For Ollama, set the host
        if provider == "ollama" and self.llm_config.ollama_host:
            os.environ["OLLAMA_HOST"] = self.llm_config.ollama_host
        
        config = {
            "backend": provider,
            "model": {provider: model},
            "ensemble_enabled": self.llm_config.enable_ensemble_mode,
            "timeout": self.llm_config.llm_timeout,
            "max_retries": self.llm_config.llm_max_retries,
            "temperature": self.llm_config.llm_temperature,
        }
        
        # Add OpenAI base URL if configured
        if provider == "openai" and self.llm_config.openai_base_url:
            config["openai_base_url"] = self.llm_config.openai_base_url
        
        # Add Ollama host if configured
        if provider == "ollama" and self.llm_config.ollama_host:
            config["ollama_host"] = self.llm_config.ollama_host
        
        return config
    
    def _init_omnicore_components(self):
        """
        Initialize OmniCore Engine components with graceful degradation.
        
        Attempts to initialize:
        - ShardedMessageBus for inter-module communication
        - PluginRegistry for plugin management
        - Metrics client for monitoring
        - Audit client for compliance logging
        
        All components are optional and the service will operate in degraded mode
        if any component is unavailable.
        """
        # Initialize Message Bus
        try:
            # Skip during pytest collection to avoid event loop requirements
            if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("PYTEST_COLLECTING"):
                logger.info("Skipping message bus initialization during pytest collection")
                self._message_bus = None
                return
                
            from omnicore_engine.message_bus.sharded_message_bus import ShardedMessageBus
            self._message_bus = ShardedMessageBus()
            self._omnicore_components_available["message_bus"] = True
            logger.info("✓ Message bus initialized successfully")
        except ImportError as e:
            logger.warning(f"Message bus not available (import failed): {e}")
        except Exception as e:
            logger.warning(f"Message bus initialization failed: {e}", exc_info=True)
        
        # Initialize Plugin Registry
        try:
            from omnicore_engine.plugin_registry import PLUGIN_REGISTRY
            self._plugin_registry = PLUGIN_REGISTRY
            self._omnicore_components_available["plugin_registry"] = True
            logger.info("✓ Plugin registry connected successfully")
        except ImportError as e:
            logger.warning(f"Plugin registry not available: {e}")
        except Exception as e:
            logger.warning(f"Plugin registry connection failed: {e}", exc_info=True)
        
        # Initialize Metrics Client
        try:
            from omnicore_engine import metrics
            self._metrics_client = metrics
            self._omnicore_components_available["metrics"] = True
            logger.info("✓ Metrics client connected successfully")
        except ImportError as e:
            logger.warning(f"Metrics client not available: {e}")
        except Exception as e:
            logger.warning(f"Metrics client connection failed: {e}", exc_info=True)
        
        # Initialize Audit Client
        try:
            from omnicore_engine.audit import ExplainAudit
            self._audit_client = ExplainAudit()
            self._omnicore_components_available["audit"] = True
            logger.info("✓ Audit client initialized successfully")
        except ImportError as e:
            logger.warning(f"Audit client not available: {e}")
        except Exception as e:
            logger.warning(f"Audit client initialization failed: {e}", exc_info=True)
        
        # Log component availability summary
        available_components = [k for k, v in self._omnicore_components_available.items() if v]
        unavailable_components = [k for k, v in self._omnicore_components_available.items() if not v]
        
        if available_components:
            logger.info(f"OmniCore components available: {', '.join(available_components)}")
        if unavailable_components:
            logger.info(f"OmniCore components unavailable (using fallback): {', '.join(unavailable_components)}")
            # Clarify that fallback mode doesn't block task execution
            logger.info(
                "Note: Fallback mode is active for unavailable components. "
                "Task execution will proceed normally - only logging/audit features may be limited."
            )
    
    def _check_agent_available(self, agent_name: str) -> Tuple[bool, Optional[str]]:
        """
        Check if an agent is available and return error message if not.
        
        Args:
            agent_name: Name of the agent to check
        
        Returns:
            Tuple of (is_available, error_message)
        """
        if not self.agents_available.get(agent_name, False):
            error_msg = (
                f"{agent_name.capitalize()} agent is not available. "
                "Check that dependencies are installed"
            )
            if not self.llm_config or not self.llm_config.get_available_providers():
                error_msg += " and LLM provider is configured (set API keys in .env)"
            return False, error_msg
        return True, None
    
    def get_llm_status(self) -> Dict[str, Any]:
        """
        Get the current LLM provider status.
        
        Returns:
            Dictionary with LLM provider status information
        """
        return {
            "provider": self._llm_status.get("provider", "unknown"),
            "configured": self._llm_status.get("configured", False),
            "validated": self._llm_status.get("validated", False),
            "error": self._llm_status.get("error"),
            "available_providers": (
                self.llm_config.get_available_providers() if self.llm_config else []
            ),
        }
    
    def get_system_status(self) -> Dict[str, Any]:
        """
        Get comprehensive system status including agents and LLM.
        
        Returns:
            Dictionary with full system status
        """
        return {
            "state": "ready_idle",
            "message": "System is ready and waiting for job requests",
            "llm_status": self.get_llm_status(),
            "agents": {
                "available": [k for k, v in self.agents_available.items() if v],
                "unavailable": [k for k, v in self.agents_available.items() if not v],
            },
            "components": {
                "available": [k for k, v in self._omnicore_components_available.items() if v],
                "unavailable": [k for k, v in self._omnicore_components_available.items() if not v],
            },
            "instructions": {
                "to_generate_code": "POST /api/jobs/ with requirements",
                "to_upload_readme": "POST /api/generator/upload",
                "to_check_status": "GET /api/jobs/{job_id}/progress",
            },
        }
    
    async def start_message_bus(self) -> bool:
        """
        Explicitly start the message bus dispatcher tasks.
        
        This method should be called from an async context during application
        startup to ensure the message bus is fully operational before WebSocket
        connections attempt to subscribe to events.
        
        Returns:
            bool: True if message bus was started successfully, False otherwise
        """
        if not self._message_bus or not self._omnicore_components_available.get("message_bus", False):
            logger.warning("Message bus not available - cannot start dispatcher tasks")
            return False
        
        try:
            await self._message_bus.start()
            logger.info("✓ Message bus dispatcher tasks started")
            return True
        except Exception as e:
            logger.error(f"Failed to start message bus dispatcher tasks: {e}", exc_info=True)
            return False
    
    async def start_periodic_audit_flush(self):
        """
        Start periodic audit flush task from async context.
        
        HIGH: Call this from application startup to enable periodic audit log flushing.
        """
        if self._audit_client and self._omnicore_components_available.get("audit"):
            try:
                await self._audit_client.start_periodic_flush()
                logger.info("✓ Periodic audit flush initialized via OmniCore service")
                return True
            except Exception as e:
                logger.warning(f"Failed to start periodic audit flush: {e}", exc_info=True)
                return False
        else:
            logger.debug("Audit client not available, skipping periodic flush initialization")
            return False

    async def route_job(
        self,
        job_id: str,
        source_module: str,
        target_module: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Route a job from one module to another via the message bus.

        Args:
            job_id: Unique job identifier
            source_module: Source module (e.g., 'generator')
            target_module: Target module (e.g., 'sfe')
            payload: Job data to route

        Returns:
            Routing result

        Example integration:
            >>> # from omnicore_engine.message_bus import publish_message
            >>> # await publish_message(topic=target_module, payload=payload)
        """
        # Log intent parsing event when job is received
        logger.info(f"Intent Parsed: Job {job_id} received from {source_module} targeting {target_module}")
        logger.info(f"Job Received: {job_id} with action: {payload.get('action', 'unknown')}")
        
        logger.info(f"Routing job {job_id} from {source_module} to {target_module}")

        # Generator runs in the same process — always dispatch directly
        # to ensure synchronous results are returned to the caller.
        # This prevents the fire-and-forget message bus issue where messages
        # are published but no subscriber processes them, causing jobs to fail
        # immediately with "OmniCore service unavailable" error.
        if target_module == "generator":
            action = payload.get("action")
            logger.info(f"Using direct dispatch for generator job {job_id} action: {action}")
            try:
                result = await self._dispatch_generator_action(job_id, action, payload)
                return {
                    "job_id": job_id,
                    "routed": True,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch",
                    "data": result,
                }
            except Exception as e:
                logger.error(f"Direct dispatch failed for generator job {job_id}: {e}", exc_info=True)
                return {
                    "job_id": job_id,
                    "routed": False,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch",
                    "error": str(e),
                    "data": {"status": "error", "message": str(e)},
                }

        # Audit log queries must always use direct dispatch because the message bus
        # is fire-and-forget with no response channel, so the result would be lost.
        # Note: generator targets are already handled above and never reach this point.
        if payload.get("action") == "query_audit_logs":
            logger.info(f"Using direct dispatch for audit query job {job_id} targeting {target_module}")
            try:
                result = await self._dispatch_sfe_action(job_id, "query_audit_logs", payload)
                return {
                    "job_id": job_id,
                    "routed": True,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch",
                    "data": result,
                }
            except Exception as e:
                logger.error(f"Direct dispatch failed for audit query job {job_id}: {e}", exc_info=True)
                return {
                    "job_id": job_id,
                    "routed": False,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch",
                    "error": str(e),
                    "data": {"status": "error", "message": str(e)},
                }

        # Use message bus if available for inter-module communication (PRIORITY 1)
        if self._message_bus and self._omnicore_components_available["message_bus"]:
            try:
                # Construct topic for target module
                topic = f"{target_module}.job_request"
                
                # Enrich payload with metadata
                enriched_payload = {
                    **payload,
                    "job_id": job_id,
                    "source_module": source_module,
                    "target_module": target_module,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                
                # Use publish for all non-generator targets (fire-and-forget via message bus)
                priority = payload.get("priority", 5)
                success = await self._message_bus.publish(
                    topic=topic,
                    payload=enriched_payload,
                    priority=priority,
                )
                
                if success:
                    logger.info(f"Job {job_id} published to message bus topic: {topic}")
                    
                    # Log to audit if available
                    if self._audit_client and self._omnicore_components_available["audit"]:
                        try:
                            await self._audit_client.add_entry_async(
                                kind="job_routed",
                                name=f"job_{job_id}",
                                detail={
                                    "source": source_module,
                                    "target": target_module,
                                    "topic": topic,
                                    "priority": priority,
                                },
                                sim_id=None,
                                agent_id=None,
                                error=None,
                                context=None,
                                custom_attributes=None,
                                rationale=f"Routing job {job_id} from {source_module} to {target_module}",
                                simulation_outcomes=None,
                                tenant_id=None,
                                explanation_id=None,
                            )
                        except Exception as audit_error:
                            logger.warning(f"Audit logging failed: {audit_error}")
                    
                    return {
                        "job_id": job_id,
                        "routed": True,
                        "source": source_module,
                        "target": target_module,
                        "topic": topic,
                        "message_bus": "ShardedMessageBus",
                        "transport": "message_bus",
                    }
                else:
                    logger.warning(f"Failed to publish job {job_id} to message bus")
                    
            except Exception as e:
                logger.error(f"Message bus routing error: {e}", exc_info=True)
                # Fall through to direct dispatch fallback

        # Fallback: Direct dispatch when message bus not available
        logger.info(f"Using direct dispatch for job {job_id} (message bus not available)")
        
        # If target is generator, dispatch to actual generator agents
        if target_module == "generator":
            action = payload.get("action")
            logger.info(f"Task Dispatched: Job {job_id} dispatching generator action: {action}")
            
            try:
                result = await self._dispatch_generator_action(job_id, action, payload)
                # CRITICAL FIX: Check actual result status before logging success
                # Don't log "finished successfully" if the job actually failed
                result_status = result.get("status", "unknown")
                if result_status in ["completed", "success", "acknowledged"]:
                    logger.info(f"Task Completed: Job {job_id} action {action} finished successfully")
                elif result_status in ["failed", "error"]:
                    logger.error(f"Task Failed: Job {job_id} action {action} failed: {result.get('message', 'Unknown error')}")
                else:
                    logger.warning(f"Task Status: Job {job_id} action {action} finished with status: {result_status}")
                
                return {
                    "job_id": job_id,
                    "routed": True,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch_fallback",
                    "data": result,
                }
            except Exception as e:
                logger.error(f"Task Failed: Job {job_id} action {action} failed: {e}", exc_info=True)
                return {
                    "job_id": job_id,
                    "routed": False,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch_fallback",
                    "error": str(e),
                    "data": {"status": "error", "message": str(e)},
                }

        # If target is SFE, dispatch to Self-Fixing Engineer components
        elif target_module == "sfe":
            action = payload.get("action")
            logger.info(f"Task Dispatched: Job {job_id} dispatching SFE action: {action}")
            
            try:
                result = await self._dispatch_sfe_action(job_id, action, payload)
                result_status = result.get("status", "unknown")
                if result_status in ["completed", "success"]:
                    logger.info(f"Task Completed: Job {job_id} SFE action {action} finished successfully")
                elif result_status in ["failed", "error"]:
                    logger.error(f"Task Failed: Job {job_id} SFE action {action} failed: {result.get('message', 'Unknown error')}")
                
                return {
                    "job_id": job_id,
                    "routed": True,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch_fallback",
                    "data": result,
                }
            except Exception as e:
                logger.error(f"Task Failed: Job {job_id} SFE action {action} failed: {e}", exc_info=True)
                return {
                    "job_id": job_id,
                    "routed": False,
                    "source": source_module,
                    "target": target_module,
                    "transport": "direct_dispatch_fallback",
                    "error": str(e),
                    "data": {"status": "error", "message": str(e)},
                }

        # For unknown targets, return fallback
        return {
            "job_id": job_id,
            "routed": True,
            "source": source_module,
            "target": target_module,
            "transport": "direct_dispatch_fallback",
            "note": "Message bus not available, job queued for direct processing",
        }
    
    async def _dispatch_generator_action(
        self, job_id: str, action: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dispatch to actual generator agents based on action.
        
        Implements circuit breaker pattern for agent availability checking.
        Returns structured errors that can be retried by upstream callers.
        
        Args:
            job_id: Job identifier
            action: Action to perform (run_codegen, run_testgen, etc.)
            payload: Action-specific parameters
            
        Returns:
            Result from the generator agent or structured error response
            
        Raises:
            No exceptions - all errors returned as structured responses
        """
        import asyncio
        
        # Check if agents are loaded before attempting to dispatch
        # Implements fail-fast pattern for better system responsiveness
        if not self._agents_loaded:
            # Check if the loader has actually finished but we haven't synced yet
            # This fixes the race condition where loader completes before _ensure_agents_loaded is called
            loader = get_agent_loader()
            if loader and not loader.is_loading():
                # Loader finished but we haven't synced - do it now
                logger.info(
                    f"Agent loader finished, syncing state for job {job_id}",
                    extra={"job_id": job_id, "action": action}
                )
            else:
                # Loader still running, attempt lazy loading
                logger.info(
                    "Agents not yet loaded, attempting lazy loading",
                    extra={"job_id": job_id, "action": action}
                )
            
            # Call _ensure_agents_loaded in both cases
            self._ensure_agents_loaded()
            
        # Re-check after potential sync or lazy loading
        if not self._agents_loaded:
            # Return structured retryable error following industry-standard error response format
            error_detail = f": {self._agent_loading_error}" if self._agent_loading_error else ""
            error_response = {
                "status": "error",
                "job_id": job_id,
                "action": action,
                "message": f"Code generation agents are still loading{error_detail}. Please retry in a few seconds.",
                "retry": True,
                "error_code": "AGENTS_NOT_READY",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.warning(
                "Agent dispatch failed - agents not ready",
                extra={
                    "job_id": job_id,
                    "action": action,
                    "error_code": "AGENTS_NOT_READY",
                    "retryable": True
                }
            )
            return error_response
        
        # Log successful agent dispatch
        logger.debug(
            "Dispatching action to generator agents",
            extra={"job_id": job_id, "action": action, "agents_loaded": True}
        )
        
        if action == "run_codegen":
            return await self._run_codegen(job_id, payload)
        elif action == "run_testgen":
            return await self._run_testgen(job_id, payload)
        elif action == "run_deploy":
            return await self._run_deploy(job_id, payload)
        elif action == "run_docgen":
            return await self._run_docgen(job_id, payload)
        elif action == "run_critique":
            return await self._run_critique(job_id, payload)
        elif action == "clarify_requirements":
            return await self._run_clarifier(job_id, payload)
        elif action == "get_clarification_feedback":
            return self._get_clarification_feedback(job_id, payload)
        elif action == "submit_clarification_response":
            return self._submit_clarification_response(job_id, payload)
        elif action == "run_full_pipeline":
            return await self._run_full_pipeline(job_id, payload)
        elif action == "configure_llm":
            return await self._configure_llm(payload)
        elif action == "query_audit_logs":
            return await self._read_audit_logs_from_files(
                log_paths=["logs/generator_audit.jsonl", "generator/audit_log/"],
                payload=payload,
            )
        elif action in ["create_job", "get_status", "get_llm_status"]:
            # These are status/query actions that don't need actual agent execution
            return {"status": "acknowledged", "action": action}
        else:
            logger.warning(f"Unknown generator action: {action}")
            return {"status": "error", "message": f"Unknown action: {action}"}
    
    async def _dispatch_sfe_action(self, job_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch action to Self-Fixing Engineer components directly."""
        
        if action == "analyze_code":
            code_path = payload.get("code_path", "")
            # Resolve the actual output path from job metadata or standard locations
            code_path = self._resolve_job_output_path(job_id, code_path)
            
            if not code_path or not Path(code_path).exists():
                return {
                    "status": "error",
                    "message": f"Code path not found for job {job_id}: {code_path}",
                    "issues_found": 0,
                    "issues": [],
                }
            
            try:
                from self_fixing_engineer.arbiter.codebase_analyzer import CodebaseAnalyzer
                
                code_path_obj = Path(code_path)
                # Don't ignore tests when analyzing generated output
                # Wrap analysis with configurable timeout to prevent long-running scans
                try:
                    async with asyncio.timeout(DEFAULT_SFE_ANALYSIS_TIMEOUT):
                        async with CodebaseAnalyzer(
                            root_dir=str(code_path_obj),
                            ignore_patterns=["__pycache__", ".git", "*.pyc", "*.egg-info"]
                        ) as analyzer:
                            summary = await analyzer.scan_codebase(str(code_path_obj))
                            
                            defects = summary.get("defects", [])
                            # Filter out defects for non-existent files
                            valid_defects = []
                            for defect in defects:
                                defect_file = defect.get("file", "")
                                if defect_file:
                                    defect_path = Path(defect_file)
                                    if not defect_path.is_absolute():
                                        defect_path = code_path_obj / defect_path
                                    if defect_path.exists():
                                        valid_defects.append(defect)
                                    else:
                                        logger.debug(f"Skipping defect for non-existent file: {defect_file}")
                            
                            return {
                                "status": "completed",
                                "job_id": job_id,
                                "code_path": code_path,
                                "issues_found": len(valid_defects),
                                "issues": valid_defects,
                                "total_files": summary.get("files", 0),
                                "source": "direct_sfe",
                            }
                except asyncio.TimeoutError:
                    logger.warning(f"SFE analysis timed out after {DEFAULT_SFE_ANALYSIS_TIMEOUT}s for job {job_id}")
                    return {
                        "status": "error",
                        "message": f"Code analysis timed out after {DEFAULT_SFE_ANALYSIS_TIMEOUT} seconds",
                        "timeout": True,
                        "job_id": job_id,
                    }
            except ImportError:
                return {"status": "error", "message": "CodebaseAnalyzer not available"}
            except Exception as e:
                logger.error(f"Error in analyze_code for job {job_id}: {e}", exc_info=True)
                return {"status": "error", "message": str(e)}
        
        elif action == "detect_errors":
            code_path = self._resolve_job_output_path(job_id, payload.get("code_path", ""))
            
            if not code_path or not Path(code_path).exists():
                return {
                    "status": "error",
                    "message": f"Code path not found for job {job_id}",
                    "errors": [],
                }
            
            # BUG FIX 3: Check for existing SFE analysis report first
            # Industry Standard: DRY principle - use centralized function
            report_path = Path(code_path) / "reports" / "sfe_analysis_report.json"
            cached_report = _load_sfe_analysis_report(report_path, job_id)
            
            if cached_report:
                # Transform cached pipeline issues to frontend error format
                errors = transform_pipeline_issues_to_frontend_errors(
                    cached_report["issues"], job_id
                )
                
                # Return cached data with appropriate structure for detect_errors
                return {
                    "status": "completed",
                    "job_id": job_id,
                    "code_path": code_path,
                    "errors": errors,
                    "error_count": len(errors),
                    "source": cached_report["source"],
                    "cached": True,
                }
            
            try:
                from self_fixing_engineer.arbiter.bug_manager import BugManager
                
                # BUG FIX 1: BugManager requires a settings argument
                # Industry Standard: Explicit settings initialization with proper fallback chain
                # Following Dependency Injection pattern with graceful degradation
                settings = None
                settings_source = "unknown"
                
                try:
                    from self_fixing_engineer.arbiter.policy.config import ArbiterConfig
                    settings = ArbiterConfig()
                    settings_source = "ArbiterConfig"
                    logger.debug(
                        "[SFE] Initialized BugManager with ArbiterConfig",
                        extra={"job_id": job_id, "settings_source": settings_source}
                    )
                except (ImportError, ModuleNotFoundError) as e:
                    # Expected import error - module may not be available in all environments
                    logger.debug(
                        f"[SFE] ArbiterConfig not available: {type(e).__name__}",
                        extra={"job_id": job_id, "error": str(e)}
                    )
                except Exception as e:
                    # Unexpected initialization error - log with full context
                    logger.warning(
                        f"[SFE] ArbiterConfig initialization failed: {type(e).__name__}: {e}",
                        extra={"job_id": job_id, "error_type": type(e).__name__},
                        exc_info=True
                    )
                
                # Fallback to minimal settings if ArbiterConfig unavailable
                if settings is None:
                    try:
                        from self_fixing_engineer.arbiter.bug_manager.bug_manager import Settings
                        settings = Settings()
                        settings_source = "Settings(default)"
                        logger.info(
                            "[SFE] Using fallback Settings for BugManager",
                            extra={"job_id": job_id, "settings_source": settings_source}
                        )
                    except Exception as e:
                        logger.error(
                            f"[SFE] Critical: Failed to initialize fallback Settings: {type(e).__name__}: {e}",
                            extra={"job_id": job_id, "error_type": type(e).__name__},
                            exc_info=True
                        )
                        return {
                            "status": "error",
                            "message": f"Failed to initialize BugManager settings: {type(e).__name__}",
                            "error_type": "settings_initialization_failed",
                            "job_id": job_id,
                        }
                
                # Initialize BugManager with validated settings
                bug_manager = BugManager(settings=settings)
                errors = await bug_manager.detect_errors(code_path)
                
                # Industry Standard: Return structured response with metadata
                return {
                    "status": "completed",
                    "job_id": job_id,
                    "code_path": code_path,
                    "errors": errors,
                    "error_count": len(errors),
                    "source": "direct_sfe",
                    "settings_source": settings_source,  # Metadata for observability
                }
            except ImportError as e:
                logger.error(
                    f"[SFE] BugManager module not available: {e}",
                    extra={"job_id": job_id, "error_type": "import_error"},
                    exc_info=True
                )
                return {
                    "status": "error",
                    "message": "BugManager module not available",
                    "error_type": "import_error",
                    "job_id": job_id,
                }
            except Exception as e:
                logger.error(
                    f"[SFE] Error in detect_errors for job {job_id}: {type(e).__name__}: {e}",
                    extra={
                        "job_id": job_id,
                        "error_type": type(e).__name__,
                        "code_path": code_path,
                    },
                    exc_info=True
                )
                return {
                    "status": "error",
                    "message": str(e),
                    "error_type": type(e).__name__,
                    "job_id": job_id,
                }
        
        elif action == "detect_bugs":
            # BUG FIX 2: Add handler for detect_bugs action
            # Similar to analyze_code but returns bugs array format
            code_path = payload.get("code_path", "")
            code_path = self._resolve_job_output_path(job_id, code_path)
            scan_depth = payload.get("scan_depth", "full")
            
            if not code_path or not Path(code_path).exists():
                return {
                    "status": "error",
                    "message": f"Code path not found for job {job_id}: {code_path}",
                    "bugs": [],
                }
            
            try:
                from self_fixing_engineer.arbiter.codebase_analyzer import CodebaseAnalyzer
                
                code_path_obj = Path(code_path)
                try:
                    async with asyncio.timeout(DEFAULT_SFE_ANALYSIS_TIMEOUT):
                        async with CodebaseAnalyzer(
                            root_dir=str(code_path_obj),
                            ignore_patterns=["__pycache__", ".git", "*.pyc", "*.egg-info"]
                        ) as analyzer:
                            summary = await analyzer.scan_codebase(str(code_path_obj))
                            
                            defects = summary.get("defects", [])
                            # Filter based on scan depth and include_potential settings
                            bugs = []
                            for defect in defects:
                                # Filter out defects for non-existent files
                                defect_file = defect.get("file", "")
                                if defect_file:
                                    defect_path = Path(defect_file)
                                    if not defect_path.is_absolute():
                                        defect_path = code_path_obj / defect_path
                                    if not defect_path.exists():
                                        continue
                                
                                # Filter by severity if scan_depth is not "full"
                                severity = defect.get("severity", "medium").lower()
                                if scan_depth == "critical" and severity not in ["critical"]:
                                    continue
                                elif scan_depth == "high" and severity not in ["critical", "high"]:
                                    continue
                                
                                bugs.append(defect)
                            
                            return {
                                "status": "completed",
                                "job_id": job_id,
                                "code_path": code_path,
                                "bugs_found": len(bugs),
                                "bugs": bugs,
                                "scan_depth": scan_depth,
                                "source": "direct_sfe",
                            }
                except asyncio.TimeoutError:
                    logger.warning(f"Bug detection timed out after {DEFAULT_SFE_ANALYSIS_TIMEOUT}s for job {job_id}")
                    return {
                        "status": "error",
                        "message": f"Bug detection timed out after {DEFAULT_SFE_ANALYSIS_TIMEOUT} seconds",
                        "timeout": True,
                        "job_id": job_id,
                    }
            except ImportError:
                return {"status": "error", "message": "CodebaseAnalyzer not available"}
            except Exception as e:
                logger.error(f"Error in detect_bugs for job {job_id}: {e}", exc_info=True)
                return {"status": "error", "message": str(e)}
        
        elif action == "fix_imports":
            # BUG FIX 2: Add handler for fix_imports action
            code_path = payload.get("code_path", "")
            code_path = self._resolve_job_output_path(job_id, code_path)
            auto_install = payload.get("auto_install", False)
            fix_style = payload.get("fix_style", True)
            
            if not code_path or not Path(code_path).exists():
                return {
                    "status": "error",
                    "message": f"Code path not found for job {job_id}: {code_path}",
                    "fixes": [],
                }
            
            try:
                # Try to use self_healing_import_fixer if available
                try:
                    from self_fixing_engineer.self_healing_import_fixer import ImportFixer
                    
                    fixer = ImportFixer(root_dir=code_path)
                    fixes = await fixer.fix_imports(auto_install=auto_install, fix_style=fix_style)
                    
                    return {
                        "status": "completed",
                        "job_id": job_id,
                        "code_path": code_path,
                        "fixes_applied": len(fixes),
                        "fixes": fixes,
                        "source": "import_fixer",
                    }
                except ImportError:
                    # Fallback: return placeholder result
                    logger.info("ImportFixer not available, returning placeholder result")
                    return {
                        "status": "completed",
                        "job_id": job_id,
                        "code_path": code_path,
                        "fixes_applied": 0,
                        "fixes": [],
                        "message": "Import fixer module not available",
                        "source": "placeholder",
                    }
            except Exception as e:
                logger.error(f"Error in fix_imports for job {job_id}: {e}", exc_info=True)
                return {"status": "error", "message": str(e)}
        
        elif action == "get_learning_insights":
            # BUG FIX 2: Add handler for get_learning_insights action
            # Return aggregated learning insights
            target_job_id = payload.get("job_id", job_id)
            
            try:
                # Try to gather learning insights from various sources
                insights = {
                    "job_id": target_job_id,
                    "patterns_learned": [],
                    "common_issues": [],
                    "suggestions": [],
                }
                
                # Check if there's an SFE analysis report
                job_path = self._resolve_job_output_path(target_job_id, "")
                if job_path:
                    report_path = Path(job_path) / "reports" / "sfe_analysis_report.json"
                    if report_path.exists():
                        with open(report_path) as f:
                            report = json.load(f)
                        
                        # Extract patterns from the report
                        issues = report.get("all_defects", [])
                        if issues:
                            # Group by type for patterns
                            issue_types = {}
                            for issue in issues:
                                issue_type = issue.get("type", "unknown")
                                if issue_type not in issue_types:
                                    issue_types[issue_type] = 0
                                issue_types[issue_type] += 1
                            
                            insights["common_issues"] = [
                                {"type": k, "count": v}
                                for k, v in sorted(issue_types.items(), key=lambda x: x[1], reverse=True)
                            ]
                            
                            # Generate suggestions based on common issues
                            if issue_types:
                                top_issue = max(issue_types.items(), key=lambda x: x[1])
                                insights["suggestions"].append(
                                    f"Consider reviewing {top_issue[0]} issues ({top_issue[1]} occurrences)"
                                )
                
                return {
                    "status": "completed",
                    "job_id": target_job_id,
                    "insights": insights,
                    "source": "direct_sfe",
                }
            except Exception as e:
                logger.error(f"Error in get_learning_insights for job {job_id}: {e}", exc_info=True)
                return {"status": "error", "message": str(e)}
        
        elif action == "query_audit_logs":
            module = payload.get("module", "")
            # Map logical module name to its audit log file(s).
            # Primary paths are where modules actually write; canonical paths are kept as fallbacks.
            if module == "guardrails":
                log_paths = ["simulation/results/audit_trail.log"]
            elif module == "simulation":
                log_paths = ["simulation/results/audit_trail.log"]
            elif module == "arbiter":
                log_paths = ["sfe_bug_manager_audit.log",
                             "self_fixing_engineer/arbiter/audit/audit_trail.jsonl",
                             "logs/arbiter_audit.jsonl"]
            elif module == "testgen":
                log_paths = ["atco_artifacts/atco_audit.log",
                             "logs/testgen_audit.jsonl"]
            else:
                log_paths = ["sfe_bug_manager_audit.log",
                             "atco_artifacts/atco_audit.log",
                             "simulation/results/audit_trail.log",
                             "logs/arbiter_audit.jsonl",
                             "logs/testgen_audit.jsonl"]
            return await self._read_audit_logs_from_files(log_paths=log_paths, payload=payload)

        elif action in ["propose_fix", "analyze_bug", "deep_analyze"]:
            # These actions need more complex handling - for now return a not implemented response
            logger.info(f"SFE action {action} not yet implemented in direct dispatch")
            return {
                "status": "error",
                "message": f"SFE action {action} not yet implemented in direct dispatch",
            }
        
        elif action == "control_arbiter":
            # Control the Arbiter AI system
            command = payload.get("command", "status")
            config = payload.get("config", {})
            
            try:
                from server.services.sfe_service import SFEService
                sfe_service = SFEService()
                result = await sfe_service.control_arbiter(command, job_id, config)
                return {
                    "status": "completed",
                    "job_id": job_id,
                    "command": command,
                    "result": result,
                    "source": "direct_sfe",
                }
            except ImportError:
                return {
                    "status": "error",
                    "message": "SFEService not available for control_arbiter",
                }
            except Exception as e:
                logger.error(f"Error in control_arbiter for job {job_id}: {e}", exc_info=True)
                return {"status": "error", "message": str(e)}
        
        return {
            "status": "error",
            "message": f"Unknown SFE action: {action}",
        }

    async def _read_audit_logs_from_files(
        self,
        log_paths: List[str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Read and filter JSONL audit log entries from one or more file paths.

        Each path may be a ``.jsonl`` file or a directory (in which case all
        ``*.jsonl`` files inside it are read).  Entries are filtered according
        to the standard audit query payload fields:
        ``start_time``, ``end_time``, ``event_type``, ``job_id``, ``module``,
        and ``limit``.

        Returns ``{"logs": [...]}`` in the same shape that
        ``_query_via_omnicore()`` in audit.py expects.
        """
        start_time: Optional[str] = payload.get("start_time")
        end_time: Optional[str] = payload.get("end_time")
        event_type: Optional[str] = payload.get("event_type")
        filter_job_id: Optional[str] = payload.get("job_id")
        filter_module: Optional[str] = payload.get("module")
        limit: int = int(payload.get("limit", 100))

        logs: List[Dict[str, Any]] = []

        for path_str in log_paths:
            path = Path(path_str)
            # Collect the actual files to read
            files_to_read: List[Path] = []
            if path.is_dir():
                files_to_read = sorted(path.glob("*.jsonl"))
            elif path.is_file():
                files_to_read = [path]
            # If neither exists, skip silently

            for log_file in files_to_read:
                try:
                    async with aiofiles.open(log_file, "r", encoding="utf-8") as fh:
                        async for raw_line in fh:
                            line = raw_line.strip()
                            if not line:
                                continue
                            try:
                                entry: Dict[str, Any] = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            # Apply filters
                            ts = entry.get("timestamp") or entry.get("ts") or ""
                            if start_time and ts and ts < start_time:
                                continue
                            if end_time and ts and ts > end_time:
                                continue
                            if event_type:
                                etype = entry.get("event_type") or entry.get("event") or ""
                                if event_type not in etype:
                                    continue
                            if filter_job_id:
                                ejid = str(entry.get("job_id") or "")
                                if filter_job_id not in ejid:
                                    continue
                            if filter_module:
                                # Only reject when the entry explicitly declares a
                                # different module; entries without a module field are
                                # assumed to belong to the file's owning module.
                                emod = entry.get("module")
                                if emod is not None and filter_module not in emod:
                                    continue

                            logs.append(entry)
                            if len(logs) >= limit:
                                break
                    if len(logs) >= limit:
                        break
                except OSError as exc:
                    logger.debug("Could not read audit log file %s: %s", log_file, exc)

            if len(logs) >= limit:
                break

        return {"logs": logs[:limit]}

    def _resolve_job_output_path(self, job_id: str, hint_path: str = "") -> Optional[str]:
        """
        Resolve the actual output path for a job, checking multiple standard locations.
        
        This method implements a multi-tier fallback strategy to locate generated job files:
        1. Uses hint_path if provided and exists
        2. Checks job metadata for output_path, code_path, or generated_path
        3. Searches standard upload locations (generated/, output/, base dir)
        4. Within standard locations, prioritizes project subdirectories over root
        
        Args:
            job_id: Unique job identifier
            hint_path: Optional path hint to check first (e.g., from user input)
            
        Returns:
            Absolute path string to the job output directory, or None if not found
            
        Examples:
            >>> service._resolve_job_output_path("job-123", "/custom/path")
            '/custom/path'  # if exists
            >>> service._resolve_job_output_path("job-456")
            './uploads/job-456/generated/my_project'  # found in standard location
        """
        
        # 1. Check hint path first
        if hint_path and Path(hint_path).exists():
            return hint_path
        
        # 2. Check job metadata
        job = jobs_db.get(job_id)
        if job and job.metadata:
            for key in ("output_path", "code_path", "generated_path"):
                path = job.metadata.get(key)
                if path and Path(path).exists():
                    return path
        
        # 3. Check standard locations
        standard_locations = [
            Path(f"./uploads/{job_id}/generated"),
            Path(f"./uploads/{job_id}/output"),
            Path(f"./uploads/{job_id}"),
        ]
        
        for loc in standard_locations:
            if loc.exists() and loc.is_dir():
                # Look for project subdirectories
                subdirs = [d for d in loc.iterdir() if d.is_dir() and not d.name.startswith('.')]
                if subdirs:
                    # Return first project subdir (sorted alphabetically for determinism)
                    # In typical generator output, there's only one project directory
                    # If multiple exist, alphabetical sort provides consistent selection
                    return str(sorted(subdirs, key=lambda p: p.name)[0])
                # If no subdirs but has Python files, use this dir
                py_files = list(loc.glob("*.py"))
                if py_files:
                    return str(loc)
        
        return None
    
    def _get_default_helm_chart(self) -> Dict[str, Any]:
        """
        Get default Helm Chart.yaml structure following Helm v2+ specification.
        
        Industry Standard: Provide valid, minimal Helm chart metadata that conforms
        to Helm chart schema. Used as fallback when LLM-generated content is invalid,
        unparseable, or contains security risks.
        
        Returns:
            Dict with standard Helm chart metadata conforming to Chart.yaml v2 schema
            
        Reference:
            https://helm.sh/docs/topics/charts/#the-chartyaml-file
            
        Security Note:
            This is a trusted, static configuration - safe to use as fallback
            without additional validation.
        """
        return {
            "apiVersion": "v2",  # Helm 3 uses apiVersion v2
            "name": "app",
            "description": "A Helm chart for Kubernetes",
            "type": "application",
            "version": "0.1.0",  # Chart version (semver)
            "appVersion": "1.0.0"  # Version of the application being deployed
        }
    
    async def _write_default_helm_chart(
        self, 
        chart_file: Path, 
        repo_path: Path, 
        generated_files: List[str]
    ) -> None:
        """
        Write default Helm Chart.yaml to disk with proper error handling.
        
        Industry Standard: Atomic file write operation with comprehensive logging
        for observability and debugging.
        
        Args:
            chart_file: Absolute path where Chart.yaml should be written
            repo_path: Repository root path for computing relative paths in logs
            generated_files: Mutable list to append generated file path to
            
        Raises:
            IOError: If file write fails (propagated to caller for handling)
            
        Side Effects:
            - Creates Chart.yaml file on disk
            - Appends relative file path to generated_files list
            - Logs operation outcome
            
        Security:
            - Uses known-good default chart structure (no user input)
            - YAML serialization is safe (no code execution risk)
            - File path validated by caller
        """
        try:
            default_chart = self._get_default_helm_chart()
            async with aiofiles.open(chart_file, "w", encoding="utf-8") as f:
                await f.write(yaml.dump(default_chart, default_flow_style=False))
            
            relative_path = str(chart_file.relative_to(repo_path))
            generated_files.append(relative_path)
            
            logger.info(
                "[DEPLOY] Generated default Helm Chart.yaml",
                extra={
                    "file": relative_path,
                    "chart_name": default_chart["name"],
                    "chart_version": default_chart["version"],
                    "fallback": True
                }
            )
        except Exception as e:
            logger.error(
                f"[DEPLOY] Failed to write default Helm chart: {type(e).__name__}: {e}",
                extra={
                    "file": str(chart_file),
                    "error_type": type(e).__name__
                },
                exc_info=True
            )
            raise  # Re-raise to let caller handle
    
    def _unwrap_nested_json_content(self, content: str, job_id: str) -> Optional[Dict[str, str]]:
        """
        Helper to recursively unwrap nested JSON strings in file content.
        
        If content is a JSON string representing a file map, parse and return it.
        Handles nested {"files": {...}} structures and validates all values are strings.
        
        Args:
            content: File content that might be a JSON string
            job_id: Job ID for logging
            
        Returns:
            Dict of filename -> content if content is a valid file map JSON, else None
        """
        stripped = content.strip()
        if not (stripped.startswith('{') and stripped.endswith('}')):
            return None
            
        try:
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict) or len(parsed) == 0:
                return None
                
            # Unwrap "files" key if present
            inner = parsed
            if "files" in inner and isinstance(inner["files"], dict):
                inner = inner["files"]
            
            # Check if all values are strings (valid file map)
            # OR if values are themselves JSON strings that can be unwrapped
            file_map = {}
            for key, value in inner.items():
                if isinstance(value, str):
                    # Check if this string value is itself a nested JSON file map
                    nested = self._unwrap_nested_json_content(value, job_id)
                    if nested:
                        # Recursively unwrapped - prefix keys with parent key
                        for nested_key, nested_content in nested.items():
                            combined_key = f"{key}/{nested_key}" if key else nested_key
                            file_map[combined_key] = nested_content
                    else:
                        # Regular string content
                        file_map[key] = value
                elif isinstance(value, dict):
                    # Value is a dict - treat as nested file map
                    for nested_key, nested_content in value.items():
                        if isinstance(nested_content, str):
                            combined_key = f"{key}/{nested_key}"
                            file_map[combined_key] = nested_content
                else:
                    # Invalid value type
                    return None
            
            if file_map:
                logger.info(
                    f"[CODEGEN] Unwrapped nested JSON content: {len(file_map)} files",
                    extra={"job_id": job_id, "files": list(file_map.keys())}
                )
                return file_map
                
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"Content is not valid JSON: {e}")
            
        return None
    
    async def _run_codegen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute code generation agent."""
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('codegen', False) or self._codegen_func is None:
            # FIX: Log exactly WHY agent is unavailable with more details
            error_msg = "Codegen agent not available"
            logger.error(
                f"[CODEGEN] Agent unavailable for job {job_id}",
                extra={
                    "job_id": job_id,
                    "error": error_msg,
                    "agents_loaded": self._agents_loaded,
                    "codegen_available": self.agents_available.get('codegen', False),
                    "codegen_func_exists": self._codegen_func is not None,
                    "available_agents": [k for k, v in self.agents_available.items() if v],
                    "unavailable_agents": [k for k, v in self.agents_available.items() if not v],
                }
            )
            return {
                "status": "error",
                "message": f"Codegen agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        # Check if LLM provider is configured
        llm_available = False
        llm_provider = None
        
        if self.llm_config:
            provider = self.llm_config.default_llm_provider
            if self.llm_config.is_provider_configured(provider):
                llm_available = True
                llm_provider = provider
            else:
                # Try auto-detection
                auto_provider = detect_available_llm_provider()
                if auto_provider:
                    llm_available = True
                    llm_provider = auto_provider
        else:
            # Check environment variables directly
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                llm_available = True
                llm_provider = auto_provider
        
        if not llm_available:
            logger.error(
                f"No LLM provider configured for code generation job {job_id}. "
                "Please set one of the following environment variables:\n"
                "  - OPENAI_API_KEY for OpenAI\n"
                "  - ANTHROPIC_API_KEY for Anthropic/Claude\n"
                "  - XAI_API_KEY or GROK_API_KEY for xAI/Grok\n"
                "  - GOOGLE_API_KEY for Google/Gemini\n"
                "  - OLLAMA_HOST for Ollama (local)"
            )
            return {
                "status": "error",
                "message": (
                    "No LLM provider configured. Code generation requires an LLM API key. "
                    "Please set OPENAI_API_KEY, ANTHROPIC_API_KEY, XAI_API_KEY, GOOGLE_API_KEY, "
                    "or OLLAMA_HOST environment variable."
                ),
                "error_type": "LLMNotConfigured",
                "configuration_help": {
                    "openai": "Set OPENAI_API_KEY environment variable",
                    "anthropic": "Set ANTHROPIC_API_KEY environment variable",
                    "grok": "Set XAI_API_KEY or GROK_API_KEY environment variable",
                    "google": "Set GOOGLE_API_KEY environment variable",
                    "ollama": "Set OLLAMA_HOST environment variable (e.g., http://localhost:11434)",
                },
            }
        
        logger.info(f"Using LLM provider '{llm_provider}' for job {job_id}")
        
        # Start timing for metrics
        import time
        start_time = time.time()
        
        # Helper function to execute the codegen logic
        async def _execute_codegen(span=None):
            try:
                requirements = payload.get("requirements", "")
                language = payload.get("language", "python")
                framework = payload.get("framework")
                
                # Retrieve job to access metadata including frontend flags
                job = jobs_db.get(job_id)
                include_frontend = False
                frontend_type = None
                
                if job and job.metadata:
                    # Extract stack metadata if available
                    stack_metadata = job.metadata.get("stack_metadata")
                    if stack_metadata and isinstance(stack_metadata, dict):
                        include_frontend = stack_metadata.get("include_frontend", False)
                        frontend_type = stack_metadata.get("frontend_type")
                        logger.info(
                            f"[CODEGEN] Full-stack generation detected from job metadata: "
                            f"include_frontend={include_frontend}, frontend_type={frontend_type}"
                        )
                    # Also check direct metadata keys (fallback)
                    elif job.metadata.get("include_frontend"):
                        include_frontend = job.metadata.get("include_frontend", False)
                        frontend_type = job.metadata.get("frontend_type")
                        logger.info(
                            f"[CODEGEN] Frontend flags found in job metadata: "
                            f"include_frontend={include_frontend}, frontend_type={frontend_type}"
                        )
                
                # Debug logging - only log metadata, not content to avoid PII exposure
                logger.info(f"[CODEGEN] Processing requirements for job {job_id}: length={len(requirements)} bytes")
                
                # Input validation - industry standard security check
                if not requirements or not isinstance(requirements, str):
                    raise ValueError("Requirements must be a non-empty string")
                if len(requirements) > 100000:  # 100KB limit
                    raise ValueError("Requirements exceed maximum length of 100KB")
                if not language or not isinstance(language, str):
                    raise ValueError("Language must be a non-empty string")
                
                # Build requirements dict
                requirements_dict = {
                    "description": requirements,
                    "target_language": language,
                    "framework": framework,
                    "include_frontend": include_frontend,
                    "frontend_type": frontend_type,
                    "md_content": requirements,  # For codegen agent's content-based frontend detection safety net
                }
                
                # Inject project_type from payload so the codegen prompt builder
                # receives it (resolved by spec processing in _run_full_pipeline).
                project_type = payload.get("project_type")
                if project_type:
                    requirements_dict["project_type"] = project_type
                    logger.info(f"[CODEGEN] Injecting project_type={project_type!r} into requirements for job {job_id}")

                # Inject previous_error from payload so build_code_generation_prompt can
                # include it in the prompt, giving the LLM context about what failed.
                # This also changes the prompt content and therefore the LLM cache key,
                # busting any cached bad response from prior attempts.
                previous_error_from_payload = payload.get("previous_error")
                if previous_error_from_payload:
                    requirements_dict["previous_error"] = previous_error_from_payload
                    logger.info(
                        f"[CODEGEN] Injecting previous_error into requirements for job {job_id}: "
                        f"error_type={previous_error_from_payload.get('error_type')}"
                    )
                    # Propagate already_generated_files so the codegen agent's multi-pass
                    # logic can skip regenerating files that already exist on disk.
                    _already_gen = previous_error_from_payload.get("already_generated_files", [])
                    if _already_gen:
                        requirements_dict["already_generated_files"] = _already_gen
                        logger.info(
                            f"[CODEGEN] Propagating {len(_already_gen)} already-generated files "
                            f"to codegen agent for additive retry (job {job_id})"
                        )
                
                # Parse requirements to extract structured features for the prompt builder
                fallback_features = [requirements] if requirements else ["No specific features provided"]
                if _parse_requirements_flexible is not None:
                    try:
                        parsed = _parse_requirements_flexible(requirements)
                        requirements_dict.update(parsed)
                        logger.info(f"[CODEGEN] Extracted {len(requirements_dict.get('features', []))} features from requirements")
                    except Exception as e:
                        logger.warning(f"[CODEGEN] Failed to parse requirements flexibly: {e}")
                        # Ensure at minimum a features key exists with the raw content
                        if 'features' not in requirements_dict:
                            requirements_dict['features'] = fallback_features
                else:
                    # Fallback if import failed - ensure features key exists
                    if 'features' not in requirements_dict:
                        requirements_dict['features'] = fallback_features
                
                # Add span attributes for observability
                if span:
                    span.set_attribute("job.id", job_id)
                    span.set_attribute("job.language", language)
                    span.set_attribute("job.framework", framework or "none")
                    span.set_attribute("job.requirements_length", len(requirements))
                
                # Build configuration from our LLM config
                config = self._build_llm_config()
                
                state_summary = f"Generating code for job {job_id}"
                
                logger.info(
                    f"Starting code generation - job_id={job_id}, language={language}, "
                    f"framework={framework or 'none'}, requirements_length={len(requirements)}"
                )
                
                # Call the actual generator
                logger.info(f"Calling codegen agent for job {job_id}")
                result = await self._codegen_func(
                    requirements=requirements_dict,
                    state_summary=state_summary,
                    config_path_or_dict=config,
                )
                
                # Validate result structure - industry standard
                # If agent returned a JSON string instead of a file map dict,
                # parse it into a dict so materialize_file_map can process it.
                if isinstance(result, str):
                    logger.warning(
                        "[CODEGEN] Agent returned string instead of dict, attempting JSON parse",
                        extra={"job_id": job_id, "result_length": len(result)}
                    )
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict):
                            # Handle nested {"files": {...}} wrapper
                            if "files" in parsed and isinstance(parsed["files"], dict):
                                parsed = parsed["files"]
                            # Validate that values are strings (valid file content)
                            non_str = {k for k, v in parsed.items() if not isinstance(v, (str, dict))}
                            if non_str:
                                raise TypeError(
                                    f"Parsed JSON contains non-string values for keys: {non_str}"
                                )
                            result = parsed
                            logger.info(
                                f"[CODEGEN] Parsed JSON string into file map with {len(result)} entries",
                                extra={"job_id": job_id, "files": list(result.keys())}
                            )
                        else:
                            raise TypeError(f"Parsed JSON is not a dict, got {type(parsed).__name__}")
                    except (json.JSONDecodeError, TypeError) as parse_err:
                        logger.error(
                            f"[CODEGEN] Invalid result type: {type(result).__name__}, JSON parse failed: {parse_err}",
                            extra={"job_id": job_id, "result": str(result)[:200]}
                        )
                        raise TypeError(f"Code generation must return dict, got {type(result).__name__}")
                elif not isinstance(result, dict):
                    logger.error(
                        f"[CODEGEN] Invalid result type: {type(result).__name__}",
                        extra={"job_id": job_id, "result": str(result)[:200]}
                    )
                    raise TypeError(f"Code generation must return dict, got {type(result).__name__}")
                
                # FIX: Check if result is empty (no files generated)
                if len(result) == 0:
                    logger.error(
                        "[CODEGEN] Empty result - no files generated",
                        extra={"job_id": job_id}
                    )
                    return {
                        "status": "error",
                        "message": "Code generation returned zero files",
                        "job_id": job_id,
                    }
                
                # FIX: Check if result is an error response (single error.txt file)
                if "error.txt" in result and len(result) == 1:
                    error_content = result["error.txt"]
                    
                    # Enhanced error message with actionable suggestions
                    error_msg = error_content
                    suggestions = []
                    
                    # Detect specific error patterns and provide guidance
                    if "did not contain recognizable code" in error_content.lower():
                        suggestions.append("The AI provided an explanation instead of code.")
                        # Check for any Presidio placeholders in the error content
                        if any(placeholder in error_content for placeholder in PRESIDIO_PLACEHOLDERS):
                            suggestions.append("ISSUE DETECTED: Requirements were corrupted by PII redaction (Presidio over-redaction).")
                            suggestions.append("FIX: Ensure technical terms and URLs in requirements are not being redacted.")
                        suggestions.append("Try providing more specific, detailed requirements.")
                        suggestions.append("Include example code structure or API endpoints.")
                        suggestions.append("Avoid placeholder text (e.g., '<ORGANIZATION>' or '<URL>').")
                    elif "requirements" in error_content.lower() and "provide" in error_content.lower():
                        suggestions.append("Requirements may be incomplete or ambiguous.")
                        suggestions.append("Provide specific technical details (e.g., 'Python with FastAPI' instead of 'API').")
                        suggestions.append("Include concrete examples of desired functionality.")
                    
                    if suggestions:
                        error_msg = f"{error_content}\n\nSuggestions:\n" + "\n".join(f"  • {s}" for s in suggestions)
                    
                    logger.error(
                        "[CODEGEN] Generation failed with error",
                        extra={
                            "job_id": job_id,
                            "error": error_content[:500],
                            "suggestions": suggestions,
                            "has_presidio_placeholders": any(p in error_content for p in PRESIDIO_PLACEHOLDERS)
                        }
                    )
                    return {
                        "status": "error",
                        "message": error_msg,
                        "error_details": error_content,
                        "suggestions": suggestions,
                        "job_id": job_id,
                    }
                
                # FIX: Log what we actually received from agent
                logger.info(
                    f"[CODEGEN] Received {len(result)} files from agent",
                    extra={"job_id": job_id, "files": list(result.keys())}
                )
                
                # Auto-fix missing imports before materialization (Industry standard: fail-safe design)
                # This prevents common LLM errors like using time.time() without import time
                # Reference: Production incident job c296ae46-fafa-4adf-a81c-be1dbfe01f1c
                try:
                    from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine
                    
                    fixer = ImportFixerEngine()
                    # Build project symbol map for resolving project-local imports (Fix 1)
                    _proj_sym_map = fixer.build_project_symbol_map(result)
                    fixed_count = 0
                    error_count = 0
                    total_fixes = 0
                    
                    # Process each Python file in the result
                    for filename, content in list(result.items()):
                        # Only process Python files with string content
                        if not filename.endswith('.py') or not isinstance(content, str):
                            continue
                        
                        # Skip empty files
                        if not content.strip():
                            continue
                        
                        try:
                            fix_result = fixer.fix_code(content, file_path=filename, project_symbol_map=_proj_sym_map)
                            
                            if fix_result["status"] == "error":
                                # Log the error but continue processing other files
                                error_count += 1
                                logger.warning(
                                    f"[CODEGEN] Failed to auto-fix imports in {filename}: {fix_result['message']}",
                                    extra={"job_id": job_id, "source_file": filename, "error": fix_result["message"]}
                                )
                                continue
                            
                            # Check if any fixes were applied
                            if fix_result["fixed_code"] != content and fix_result["fixes_applied"]:
                                result[filename] = fix_result["fixed_code"]
                                fixed_count += 1
                                total_fixes += len(fix_result["fixes_applied"])
                                fixes_applied = fix_result["fixes_applied"]
                                
                                logger.info(
                                    f"[CODEGEN] Auto-fixed imports in {filename}: {', '.join(fixes_applied)}",
                                    extra={
                                        "job_id": job_id,
                                        "source_file": filename,
                                        "fixes": fixes_applied,
                                        "fix_count": len(fixes_applied)
                                    }
                                )
                        except Exception as file_err:
                            # Handle per-file errors without breaking the entire batch
                            error_count += 1
                            logger.warning(
                                f"[CODEGEN] Exception while fixing imports in {filename}: {file_err}",
                                exc_info=True,
                                extra={"job_id": job_id, "source_file": filename, "error": str(file_err)}
                            )
                    
                    # Summary logging for observability
                    if fixed_count > 0:
                        logger.info(
                            f"[CODEGEN] Import auto-fix summary: {fixed_count} file(s) fixed with {total_fixes} total fix(es)",
                            extra={
                                "job_id": job_id,
                                "files_fixed": fixed_count,
                                "total_fixes": total_fixes,
                                "errors": error_count
                            }
                        )
                    elif error_count > 0:
                        logger.warning(
                            f"[CODEGEN] Import auto-fix completed with {error_count} error(s), no files fixed",
                            extra={"job_id": job_id, "error_count": error_count}
                        )
                    else:
                        logger.debug(
                            "[CODEGEN] Import auto-fix completed: no missing imports detected",
                            extra={"job_id": job_id}
                        )
                        
                except ImportError as import_err:
                    # ImportFixerEngine module not available - log but continue
                    logger.warning(
                        f"[CODEGEN] Import auto-fix unavailable: {import_err}",
                        extra={"job_id": job_id, "error": str(import_err)}
                    )
                except Exception as e:
                    # Unexpected error in import fixing system - log with full context but continue
                    logger.error(
                        f"[CODEGEN] Import auto-fix system error: {e}",
                        exc_info=True,
                        extra={"job_id": job_id, "error": str(e), "error_type": type(e).__name__}
                    )
                
                # FIX: Detect collapsed multi-file output bundled as a single JSON string.
                # When the codegen handler's fallback wraps the entire JSON blob into a
                # single key (e.g. {"main.py": '{"files": {"app/main.py": ...}}'}),
                # try to unwrap it so the materializer receives the real file map.
                if len(result) == 1:
                    sole_key = next(iter(result))
                    sole_value = result[sole_key]
                    if isinstance(sole_value, str):
                        # Strip leading "json" prefix (LLM sometimes prepends it)
                        _sv = sole_value.strip()
                        if len(_sv) > 4 and _sv[:4].lower() == "json":
                            _sv = _sv[4:].lstrip()
                        if _sv.startswith("{"):
                            try:
                                inner = json.loads(_sv)
                                if isinstance(inner, dict):
                                    # Unwrap nested {"files": {...}} if present
                                    if "files" in inner and isinstance(inner["files"], dict):
                                        inner = inner["files"]
                                    # Only unwrap if the inner dict looks like a file map
                                    # (multiple entries or keys contain path separators / extensions)
                                    if len(inner) > 1 or any(
                                        "/" in k or "." in k for k in inner.keys()
                                    ):
                                        logger.info(
                                            f"[CODEGEN] Unwrapped collapsed JSON file map from '{sole_key}': "
                                            f"{len(inner)} files",
                                            extra={"job_id": job_id, "files": list(inner.keys())}
                                        )
                                        result = inner
                            except (json.JSONDecodeError, TypeError):
                                pass  # Not valid JSON, proceed with original result
                
                # Create output directory with security validation
                # Prevent path traversal attacks - industry standard security
                base_uploads_dir = Path("./uploads").resolve()
                # Propagate output_dir from payload/frontmatter if specified
                custom_output_dir = payload.get("output_dir", "").strip()
                if custom_output_dir:
                    # Sanitize: reject path traversal attempts
                    if ".." in custom_output_dir or custom_output_dir.startswith("/"):
                        logger.warning(
                            f"[CODEGEN] Rejecting suspicious output_dir: {custom_output_dir}",
                            extra={"job_id": job_id}
                        )
                        custom_output_dir = ""
                
                # FIX: Strip "generated/" prefix to avoid double-nesting
                # If README specifies "output_dir: generated/hello_generator", we should not create
                # "job-id/generated/generated/hello_generator" but rather "job-id/generated/hello_generator"
                if custom_output_dir:
                    # Remove "generated/" or "generated" prefix if present
                    if custom_output_dir.startswith("generated/"):
                        custom_output_dir = custom_output_dir[len("generated/"):]
                        logger.info(
                            f"[CODEGEN] Stripped 'generated/' prefix from output_dir: now {custom_output_dir}",
                            extra={"job_id": job_id}
                        )
                    elif custom_output_dir == "generated":
                        custom_output_dir = ""
                        logger.info(
                            "[CODEGEN] Stripped 'generated' from output_dir (would be redundant)",
                            extra={"job_id": job_id}
                        )
                
                if custom_output_dir:
                    output_path = (base_uploads_dir / job_id / "generated" / custom_output_dir).resolve()
                else:
                    output_path = (base_uploads_dir / job_id / "generated").resolve()
                
                # Ensure output path is within uploads directory
                if not str(output_path).startswith(str(base_uploads_dir)):
                    raise SecurityError("Invalid job_id: path traversal attempt detected")
                
                output_path.mkdir(parents=True, exist_ok=True)
                logger.info(
                    f"Created output directory - job_id={job_id}, path={output_path}",
                    extra={"job_id": job_id, "output_path": str(output_path)}
                )
                
                # Save generated files using the canonical materializer from runner_file_utils.
                # This replaces the manual loop and prevents the JSON-bundle-in-main.py bug
                # because materialize_file_map handles {"files": {...}} unwrapping, JSON string
                # parsing, path traversal prevention, and content type validation.
                generated_files = []
                total_bytes_written = 0
                files_failed = []
                _pre_mat_errors: List[str] = []
                
                if isinstance(result, dict):
                    if _MATERIALIZER_AVAILABLE:
                        try:
                            # FIX #1: Strip "generated/" and custom_output_dir prefixes from file_map keys
                            # to prevent double-nesting (e.g., generated/hello_generator/generated/app/main.py)
                            cleaned_file_map = {}
                            for original_path, content in result.items():
                                cleaned_path = original_path
                                
                                # Remove "generated/" prefix if present (use while to handle multiple levels)
                                while cleaned_path.startswith("generated/"):
                                    cleaned_path = cleaned_path[len("generated/"):]
                                    logger.debug(
                                        f"[CODEGEN] Stripped 'generated/' prefix: {original_path} -> {cleaned_path}",
                                        extra={"job_id": job_id}
                                    )
                                
                                # Remove custom_output_dir prefix if present (avoid double-nesting)
                                if custom_output_dir and cleaned_path.startswith(f"{custom_output_dir}/"):
                                    cleaned_path = cleaned_path[len(custom_output_dir) + 1:]
                                    logger.debug(
                                        f"[CODEGEN] Stripped custom_output_dir prefix: {original_path} -> {cleaned_path}",
                                        extra={"job_id": job_id, "custom_output_dir": custom_output_dir}
                                    )
                                
                                cleaned_file_map[cleaned_path] = content
                            
                            # Fix 4: Pre-materialization import check (in-memory, before writing to disk)
                            # Catches NameErrors like 'AuditLogSchema' not imported before they hit disk
                            try:
                                _pre_mat_errors = _pre_materialization_import_check(cleaned_file_map)
                                if _pre_mat_errors:
                                    logger.warning(
                                        f"[CODEGEN] Pre-materialization import check found {len(_pre_mat_errors)} error(s) for job {job_id}",
                                        extra={"job_id": job_id, "pre_mat_import_errors": _pre_mat_errors}
                                    )
                            except Exception as _pmc_err:
                                _pre_mat_errors = []
                                logger.warning(
                                    f"[CODEGEN] Pre-materialization import check failed (non-fatal): {_pmc_err}",
                                    extra={"job_id": job_id}
                                )

                            mat_result = await _materialize_file_map(
                                cleaned_file_map, output_path
                            )
                            if mat_result.get("success"):
                                for fname in mat_result.get("files_written", []):
                                    full_path = str((output_path / fname).resolve())
                                    generated_files.append(full_path)
                                    if METRICS_AVAILABLE:
                                        file_ext = Path(fname).suffix.lstrip('.') or 'unknown'
                                        codegen_files_generated.labels(
                                            job_id=job_id,
                                            language=language
                                        ).inc()
                                total_bytes_written = mat_result.get("total_bytes_written", 0)
                                
                                # FIX Issue 1: Enforce output layout after materialization
                                # Ensure all generated files are under the project subdirectory
                                # Build payload dict for helper function
                                helper_payload = {
                                    "package_name": requirements_dict.get("package_name") if requirements_dict else None,
                                    "package": requirements_dict.get("package") if requirements_dict else None,
                                    "output_dir": custom_output_dir
                                }
                                project_name = _extract_project_name_from_path_or_payload(helper_payload) or "generated_project"
                                
                                logger.info(
                                    f"[CODEGEN] Using project name: {project_name}",
                                    extra={
                                        "job_id": job_id,
                                        "project_name": project_name,
                                        "custom_output_dir": custom_output_dir or "none"
                                    }
                                )
                                
                                # If output_path already ends with the project name, use parent
                                if output_path.name == project_name:
                                    # Files are already in the right place
                                    logger.debug(f"Output path already ends with project name: {project_name}")
                                else:
                                    # Files might be at the wrong level - enforce layout
                                    try:
                                        from generator.runner.runner_file_utils import _enforce_output_layout
                                        layout_result = _enforce_output_layout(output_path, project_name)
                                        if not layout_result.get("success"):
                                            logger.warning(
                                                f"[CODEGEN] Output layout enforcement had errors: {layout_result.get('errors')}",
                                                extra={"job_id": job_id}
                                            )
                                        elif layout_result.get("files_moved"):
                                            logger.info(
                                                f"[CODEGEN] Enforced output layout: moved {len(layout_result['files_moved'])} items to {project_name}/",
                                                extra={
                                                    "job_id": job_id,
                                                    "files_moved": layout_result["files_moved"],
                                                    "project_name": project_name
                                                }
                                            )
                                            # Update output_path to point to the project subdirectory
                                            output_path = output_path / project_name
                                    except ImportError:
                                        logger.warning("[CODEGEN] _enforce_output_layout not available, skipping layout enforcement")
                                
                                # FIX Bug 1: Create __init__.py files for Python packages after materialization
                                # This must happen BEFORE testgen, critique, and SFE stages run
                                # Without __init__.py, Python cannot import from packages using relative imports
                                if language.lower() == "python":
                                    try:
                                        _ensure_python_package_structure(output_path)
                                        logger.info(
                                            f"[CODEGEN] Ensured Python package structure in {output_path}",
                                            extra={"job_id": job_id}
                                        )
                                        
                                        # FIX Issue 1: Sanitize Pydantic schemas to use V2 field_validator
                                        # Find and sanitize schemas.py or models.py files
                                        for schema_file in ["schemas.py", "models.py"]:
                                            schema_path = output_path / "app" / schema_file
                                            if not schema_path.exists():
                                                # Try at root level
                                                schema_path = output_path / schema_file
                                            
                                            if schema_path.exists():
                                                try:
                                                    schema_content = schema_path.read_text()
                                                    sanitized_content = self._sanitize_pydantic_schema(schema_content)
                                                    if sanitized_content != schema_content:
                                                        schema_path.write_text(sanitized_content)
                                                        logger.info(
                                                            f"[CODEGEN] Sanitized {schema_file} to use Pydantic V2 field_validator",
                                                            extra={"job_id": job_id, "file": str(schema_path)}
                                                        )
                                                except Exception as sanitize_err:
                                                    logger.warning(
                                                        f"[CODEGEN] Failed to sanitize {schema_file}: {sanitize_err}",
                                                        extra={"job_id": job_id}, exc_info=True
                                                    )
                                    except Exception as init_err:
                                        logger.warning(
                                            f"[CODEGEN] Failed to create __init__.py files: {init_err}",
                                            extra={"job_id": job_id}, exc_info=True
                                        )
                            else:
                                for err in mat_result.get("errors", []):
                                    files_failed.append({"filename": "(materializer)", "error": err})
                            for skipped in mat_result.get("files_skipped", []):
                                files_failed.append({
                                    "filename": skipped.get("path", "unknown"),
                                    "error": skipped.get("reason", "unknown")
                                })
                        except Exception as mat_err:
                            logger.error(
                                f"[CODEGEN] Materialization failed: {mat_err}",
                                extra={"job_id": job_id}, exc_info=True
                            )
                            files_failed.append({"filename": "(all)", "error": str(mat_err)})
                    else:
                        # Fallback: write files directly (legacy path when materializer unavailable)
                        # Unpack nested {"files": {...}} structures or JSON string bundles
                        # to prevent the JSON-bundle-in-main.py bug.
                        file_map = result
                        files_key_unwrapped = False
                        if "files" in file_map and isinstance(file_map["files"], dict):
                            logger.info(
                                "[CODEGEN] Fallback: unwrapping nested 'files' key",
                                extra={"job_id": job_id}
                            )
                            file_map = file_map["files"]
                            files_key_unwrapped = True

                        for filename, content in file_map.items():
                            try:
                                if not filename or '..' in filename or filename.startswith('/'):
                                    raise SecurityError(f"Invalid filename: {filename}")
                                # Handle content that is a dict (nested file map under a single key)
                                if isinstance(content, dict):
                                    # If a value is a dict, treat it as a nested file map
                                    for sub_name, sub_content in content.items():
                                        sub_path_str = f"{filename}/{sub_name}"
                                        if not isinstance(sub_content, str):
                                            files_failed.append({"filename": sub_path_str, "error": f"nested content must be string, got {type(sub_content).__name__}"})
                                            continue
                                        if '..' in sub_path_str or sub_path_str.startswith('/'):
                                            raise SecurityError(f"Invalid filename: {sub_path_str}")
                                        if len(sub_content) > 10 * 1024 * 1024:
                                            raise ValueError(f"File {sub_path_str} exceeds 10MB size limit")
                                        if not sub_content or not sub_content.strip():
                                            files_failed.append({"filename": sub_path_str, "error": "content_empty_or_whitespace"})
                                            continue
                                        sub_file_path = (output_path / sub_path_str).resolve()
                                        if not str(sub_file_path).startswith(str(output_path)):
                                            raise SecurityError(f"Path traversal attempt in filename: {sub_path_str}")
                                        sub_file_path.parent.mkdir(parents=True, exist_ok=True)
                                        sub_file_path.write_text(sub_content, encoding='utf-8')
                                        if sub_file_path.exists() and sub_file_path.stat().st_size > 0:
                                            generated_files.append(str(sub_file_path))
                                            total_bytes_written += len(sub_content.encode('utf-8'))
                                    continue
                                if not isinstance(content, str):
                                    raise TypeError(f"File content must be string, got {type(content).__name__}")
                                
                                # Use the helper to recursively unwrap nested JSON strings
                                unwrapped = self._unwrap_nested_json_content(content, job_id)
                                if unwrapped:
                                    # Content was a nested JSON file map - write each file
                                    logger.info(
                                        f"[CODEGEN] Fallback: unpacking JSON bundle from '{filename}'",
                                        extra={"job_id": job_id, "inner_files": list(unwrapped.keys())}
                                    )
                                    for inner_name, inner_content in unwrapped.items():
                                        if not inner_name or '..' in inner_name or inner_name.startswith('/'):
                                            raise SecurityError(f"Invalid filename: {inner_name}")
                                        if len(inner_content) > 10 * 1024 * 1024:
                                            raise ValueError(f"File {inner_name} exceeds 10MB size limit")
                                        if not inner_content or not inner_content.strip():
                                            files_failed.append({"filename": inner_name, "error": "content_empty_or_whitespace"})
                                            continue
                                        inner_path = (output_path / inner_name).resolve()
                                        if not str(inner_path).startswith(str(output_path)):
                                            raise SecurityError(f"Path traversal attempt in filename: {inner_name}")
                                        inner_path.parent.mkdir(parents=True, exist_ok=True)
                                        inner_path.write_text(inner_content, encoding='utf-8')
                                        if inner_path.exists() and inner_path.stat().st_size > 0:
                                            generated_files.append(str(inner_path))
                                            total_bytes_written += len(inner_content.encode('utf-8'))
                                    continue
                                if len(content) > 10 * 1024 * 1024:
                                    raise ValueError(f"File {filename} exceeds 10MB size limit")
                                if not content or not content.strip():
                                    files_failed.append({"filename": filename, "error": "content_empty_or_whitespace"})
                                    continue
                                file_path = (output_path / filename).resolve()
                                if not str(file_path).startswith(str(output_path)):
                                    raise SecurityError(f"Path traversal attempt in filename: {filename}")
                                file_path.parent.mkdir(parents=True, exist_ok=True)
                                file_path.write_text(content, encoding='utf-8')
                                if file_path.exists() and file_path.stat().st_size > 0:
                                    generated_files.append(str(file_path))
                                    total_bytes_written += len(content.encode('utf-8'))
                                    if METRICS_AVAILABLE:
                                        file_ext = file_path.suffix.lstrip('.') or 'unknown'
                                        codegen_files_generated.labels(
                                            job_id=job_id, language=language
                                        ).inc()
                                        codegen_file_size_bytes.labels(
                                            job_id=job_id, file_type=file_ext
                                        ).observe(len(content.encode('utf-8')))
                                else:
                                    files_failed.append({"filename": filename, "error": "file_empty_after_write"})
                            except SecurityError:
                                raise
                            except Exception as write_error:
                                files_failed.append({"filename": filename, "error": str(write_error)})
                        
                        # FIX Bug 1: Create __init__.py files for Python packages after fallback writing
                        # This must happen for both materializer and fallback paths
                        if language.lower() == "python":
                            try:
                                _ensure_python_package_structure(output_path)
                                logger.info(
                                    f"[CODEGEN] Fallback: Ensured Python package structure in {output_path}",
                                    extra={"job_id": job_id}
                                )
                            except Exception as init_err:
                                logger.warning(
                                    f"[CODEGEN] Fallback: Failed to create __init__.py files: {init_err}",
                                    extra={"job_id": job_id}, exc_info=True
                                )
                    # Apply post-materialization fixups: required dirs, schemas.py,
                    # README patching, Sphinx placeholder — mirrors the engine.py MATERIALIZE stage.
                    try:
                        from generator.main.post_materialize import post_materialize as _post_materialize
                        _pm_spec_structure = payload.get("spec_structure")
                        pm_result = _post_materialize(output_path, spec_structure=_pm_spec_structure)
                        if pm_result.files_created:
                            logger.info(
                                f"[CODEGEN] post_materialize created "
                                f"{len(pm_result.files_created)} stub file(s): "
                                f"{pm_result.files_created}",
                                extra={"job_id": job_id, "files_created": pm_result.files_created}
                            )
                        for warn in pm_result.warnings:
                            logger.warning(
                                f"[CODEGEN] post_materialize warning: {warn}",
                                extra={"job_id": job_id}
                            )
                    except Exception as pm_err:
                        logger.warning(
                            f"[CODEGEN] post_materialize failed: {pm_err}",
                            extra={"job_id": job_id}
                        )

                    # Fix any double-nesting that the LLM may have introduced
                    # (e.g. output_path/generated/… → output_path/…)
                    _fix_double_nesting(output_path)

                    # Apply pydantic v1→v2 fixes recursively to ALL Python files
                    # in the output directory (including nested subdirectories like hello_generator/).
                    # This ensures fixes aren't missed when the LLM generates nested structures.
                    if language.lower() in ("python", "py"):
                        try:
                            from generator.agents.codegen_agent.codegen_response_handler import (
                                auto_fix_pydantic_v1_imports,
                            )
                            py_files_on_disk: dict = {}
                            for py_path in Path(output_path).rglob("*.py"):
                                rel = str(py_path.relative_to(output_path))
                                try:
                                    py_files_on_disk[rel] = py_path.read_text(encoding="utf-8")
                                except Exception:
                                    pass
                            # Also include requirements.txt for pydantic pin upgrades
                            req_path = Path(output_path) / "requirements.txt"
                            if req_path.exists():
                                try:
                                    py_files_on_disk["requirements.txt"] = req_path.read_text(encoding="utf-8")
                                except Exception:
                                    pass
                            if py_files_on_disk:
                                fixed = auto_fix_pydantic_v1_imports(py_files_on_disk)
                                for rel_name, fixed_content in fixed.items():
                                    fixed_path = Path(output_path) / rel_name
                                    if fixed_content != py_files_on_disk.get(rel_name):
                                        try:
                                            fixed_path.parent.mkdir(parents=True, exist_ok=True)
                                            fixed_path.write_text(fixed_content, encoding="utf-8")
                                        except Exception as write_err:
                                            logger.warning(
                                                f"[CODEGEN] Could not write pydantic fix for {rel_name}: {write_err}"
                                            )
                        except ImportError:
                            pass
                        except Exception as pydantic_fix_err:
                            logger.warning(
                                f"[CODEGEN] Recursive pydantic fix failed: {pydantic_fix_err}",
                                extra={"job_id": job_id}
                            )
                else:
                    logger.warning(
                        f"Code generation returned non-dict result - type={type(result).__name__}",
                        extra={
                            "job_id": job_id,
                            "result_type": type(result).__name__,
                            "status": "warning"
                        }
                    )
                
                # FIX: Check if any files were successfully written
                if len(generated_files) == 0:
                    logger.error(
                        "[CODEGEN] Failed to write any code files to disk",
                        extra={
                            "job_id": job_id,
                            "files_failed": files_failed,
                            "status": "error"
                        }
                    )
                    return {
                        "status": "error",
                        "message": "Failed to write any code files to disk",
                        "files_failed": files_failed,
                        "job_id": job_id,
                    }
                
                # Calculate duration and record metrics
                duration = time.time() - start_time
                if METRICS_AVAILABLE:
                    codegen_duration_seconds.labels(
                        job_id=job_id,
                        language=language
                    ).observe(duration)
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language,
                        status="success" if not files_failed else "partial_success"
                    ).inc()
                
                # Update tracing span
                if span:
                    span.set_attribute("files.generated", len(generated_files))
                    span.set_attribute("files.failed", len(files_failed))
                    span.set_attribute("bytes.written", total_bytes_written)
                    span.set_attribute("duration.seconds", duration)
                    span.set_status(Status(StatusCode.OK))
                
                # Comprehensive completion log
                logger.info(
                    f"Code generation completed - job_id={job_id}, files_generated={len(generated_files)}, "
                    f"files_failed={len(files_failed)}, total_bytes={total_bytes_written}, "
                    f"duration={duration:.2f}s, output_path={output_path}",
                    extra={
                        "job_id": job_id,
                        "files_generated": len(generated_files),
                        "files_failed": len(files_failed),
                        "total_bytes": total_bytes_written,
                        "duration_seconds": duration,
                        "output_path": str(output_path),
                        "status": "completed"
                    }
                )
                
                # Validate frontend files were generated if requested
                if include_frontend:
                    frontend_dirs = ["templates", "static", "app/templates", "app/static", "public", "src/components"]
                    frontend_files_found = any(
                        any(frontend_dir in str(Path(f).parent) for frontend_dir in frontend_dirs)
                        for f in generated_files
                    )
                    
                    if not frontend_files_found:
                        # Simplify file paths for logging (helper to avoid complex inline logic)
                        def get_relative_path(file_path_str, base_path):
                            try:
                                file_path = Path(file_path_str)
                                return str(file_path.relative_to(base_path)) if file_path.is_relative_to(base_path) else file_path.name
                            except (ValueError, OSError):
                                return Path(file_path_str).name
                        
                        sample_files = [get_relative_path(f, output_path) for f in generated_files[:10]]
                        
                        logger.warning(
                            f"[CODEGEN] Frontend generation was requested but no frontend files (templates/, static/, etc.) were found. "
                            f"include_frontend={include_frontend}, frontend_type={frontend_type}",
                            extra={
                                "job_id": job_id,
                                "include_frontend": include_frontend,
                                "frontend_type": frontend_type,
                                "generated_files": sample_files,
                                "files_count": len(generated_files)
                            }
                        )
                    else:
                        frontend_file_count = sum(
                            1 for f in generated_files
                            if any(frontend_dir in str(Path(f).parent) for frontend_dir in frontend_dirs)
                        )
                        logger.info(
                            f"[CODEGEN] Frontend files validated: {frontend_file_count} files in frontend directories",
                            extra={
                                "job_id": job_id,
                                "include_frontend": include_frontend,
                                "frontend_type": frontend_type,
                                "frontend_files_count": frontend_file_count
                            }
                        )
                
                # Build detailed result dict with file information
                result_dict = {
                    "status": "completed",
                    "generated_files": generated_files,  # Full paths
                    "file_names": [Path(f).name for f in generated_files],  # Just filenames for UI
                    "output_path": str(output_path),
                    "files_count": len(generated_files),
                    "total_bytes_written": total_bytes_written,
                    "duration_seconds": round(duration, 2),
                    "pre_mat_import_errors": _pre_mat_errors,
                }
                
                # Include failures in response if any
                if files_failed:
                    result_dict["files_failed"] = files_failed
                    result_dict["files_failed_count"] = len(files_failed)
                    result_dict["warning"] = f"{len(files_failed)} file(s) failed to write"
                    logger.warning(
                        f"[CODEGEN] Partial success - {len(generated_files)} succeeded, {len(files_failed)} failed",
                        extra={
                            "job_id": job_id,
                            "succeeded": len(generated_files),
                            "failed": len(files_failed),
                            "failed_files": files_failed
                        }
                    )
                
                # FIX: Update job.output_files immediately after writing files
                # This ensures files appear in UI without waiting for pipeline completion
                if job_id in jobs_db:
                    job = jobs_db[job_id]
                    # Store relative paths from uploads/{job_id}/ directory
                    try:
                        # Use upload_dir from config if available, otherwise default to ./uploads
                        upload_dir = self.agent_config.upload_dir if self.agent_config else Path("./uploads")
                        job_base = upload_dir / job_id
                        relative_files = []
                        for file_path_str in generated_files:
                            file_path = Path(file_path_str)
                            if file_path.exists():
                                # [FIX] Add error handling for path resolution
                                try:
                                    rel_path = str(file_path.resolve().relative_to(job_base.resolve()))
                                    relative_files.append(rel_path)
                                except ValueError as e:
                                    logger.warning(f"[CODEGEN] File {file_path} is outside job_base {job_base}, using filename only. Error: {e}")
                                    relative_files.append(file_path.name)
                        job.output_files = relative_files
                        job.updated_at = datetime.now(timezone.utc)
                        logger.info(
                            f"Updated job {job_id} with {len(relative_files)} output files",
                            extra={"job_id": job_id, "files_count": len(relative_files)}
                        )
                        
                        # Code generation complete - keep job in running state for pipeline
                        # Only mark as COMPLETED in _finalize_successful_job after all stages
                        if len(generated_files) > 0:
                            logger.info(
                                f"✓ Job {job_id} code generation completed, continuing pipeline",
                                extra={
                                    "job_id": job_id,
                                    "files_generated": len(generated_files),
                                    "stage": "codegen"
                                }
                            )
                    except Exception as update_error:
                        logger.warning(
                            f"Failed to update job.output_files for {job_id}: {update_error}",
                            extra={"job_id": job_id, "error": str(update_error)}
                        )
                
                return result_dict
                
            except SecurityError as sec_error:
                # Security errors are critical - comprehensive logging
                duration = time.time() - start_time
                logger.critical(
                    f"Security violation in code generation - job_id={job_id}, error={sec_error}",
                    extra={
                        "job_id": job_id,
                        "error_type": "security_violation",
                        "error_message": str(sec_error),
                        "duration_seconds": duration,
                        "status": "security_error"
                    },
                    exc_info=True
                )
                if METRICS_AVAILABLE:
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language,
                        status="security_error"
                    ).inc()
                if span:
                    span.set_status(Status(StatusCode.ERROR, str(sec_error)))
                    span.record_exception(sec_error)
                
                return {
                    "status": "error",
                    "message": "Security violation detected",
                    "error_type": "SecurityError",
                    "error_details": str(sec_error),
                }
                
            except ValueError as val_error:
                # Validation errors - user input issues
                duration = time.time() - start_time
                logger.warning(
                    f"Validation error in code generation - job_id={job_id}, error={val_error}",
                    extra={
                        "job_id": job_id,
                        "error_type": "validation_error",
                        "error_message": str(val_error),
                        "duration_seconds": duration,
                        "status": "validation_error"
                    }
                )
                if METRICS_AVAILABLE:
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language if 'language' in locals() else 'unknown',
                        status="validation_error"
                    ).inc()
                if span:
                    span.set_status(Status(StatusCode.ERROR, str(val_error)))
                
                return {
                    "status": "error",
                    "message": str(val_error),
                    "error_type": "ValidationError",
                }
                
            except Exception as e:
                # Unexpected errors - comprehensive logging
                duration = time.time() - start_time
                error_type = type(e).__name__
                logger.error(
                    f"Unexpected error in code generation - job_id={job_id}, error={error_type}: {e}",
                    extra={
                        "job_id": job_id,
                        "error_type": error_type,
                        "error_message": str(e),
                        "duration_seconds": duration,
                        "status": "error"
                    },
                    exc_info=True
                )
                if METRICS_AVAILABLE:
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language if 'language' in locals() else 'unknown',
                        status="error"
                    ).inc()
                    codegen_errors_total.labels(
                        job_id=job_id,
                        error_type=error_type
                    ).inc()
                if span:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                
                return {
                    "status": "error",
                    "message": str(e),
                    "error_type": error_type,
                }
        
        # Execute with or without tracing
        if TRACING_AVAILABLE:
            with tracer.start_as_current_span("codegen_execution") as span:
                return await _execute_codegen(span)
        else:
            return await _execute_codegen()
    
    async def _run_testgen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute test generation agent with timeout."""
        logger.info(f"[TESTGEN] Starting test generation for job {job_id}")
        
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('testgen', False) or self._testgen_class is None:
            error_msg = "Testgen agent not available"
            logger.error(f"[TESTGEN] Testgen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Testgen agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            # Wrap test generation with configurable timeout
            async with asyncio.timeout(DEFAULT_TESTGEN_TIMEOUT):
                code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
                coverage_target = float(payload.get("coverage_target", 80.0))
                
                # Create testgen agent with correct repo path
                repo_path = Path(f"./uploads/{job_id}").resolve()  # Resolve to absolute
                agent = self._testgen_class(str(repo_path))
                
                # Initialize the agent's codebase asynchronously if method exists
                if hasattr(agent, '_async_init'):
                    await agent._async_init()
                
                # Set up policy for test generation
                # Issue 9 fix: quality_threshold is stored in range [0, 100] by TestGenPolicy,
                # so pass coverage_target directly (e.g. 80.0), NOT divided by 100 (which
                # would produce 0.8 ≈ 0.8% and make the quality gate effectively disabled).
                policy = self._testgen_policy_class(
                    quality_threshold=coverage_target,
                    max_refinements=2,
                    primary_metric="coverage",
                )
                
                # Get language from payload or detect it
                language = payload.get("language")
                if not language:
                    # Detect language if not provided
                    language = _detect_project_language(Path(code_path))
                
                logger.info(f"[TESTGEN] Target language: {language}")
                
                # Check if testgen supports this language
                if language != "python":
                    # Testgen currently only supports Python - skip gracefully
                    logger.info(
                        f"[TESTGEN] Testgen currently only supports Python - skipping gracefully for {language} project (not a failure)"
                    )
                    return {
                        "status": "skipped",
                        "message": f"Testgen currently only supports Python. Language detected: {language}",
                        "language": language,
                        "job_id": job_id,
                    }
                
                # Find code files to test
                code_files = []
                code_dir = Path(code_path).resolve()  # Resolve to absolute path
                
                logger.info(f"[TESTGEN] Resolved repo_path: {repo_path}")
                logger.info(f"[TESTGEN] Resolved code_dir: {code_dir}")
                
                # Get file patterns for the language
                file_patterns = LANGUAGE_FILE_EXTENSIONS.get(language, ["*.py"])
                
                if code_dir.exists():
                    # Convert absolute paths to relative paths from repo_path
                    # This prevents path duplication when testgen agent prepends repo_path
                    for pattern in file_patterns:
                        for f in code_dir.rglob(pattern):
                            if not _is_test_file(f, language):
                                try:
                                    # Get absolute path and convert to relative
                                    abs_file_path = f.resolve()
                                    rel_path = abs_file_path.relative_to(repo_path)
                                    code_files.append(str(rel_path))
                                    logger.debug(f"[TESTGEN] Added file: {abs_file_path} -> {rel_path}")
                                except ValueError as e:
                                    # File is outside repo_path
                                    logger.warning(
                                        f"[TESTGEN] File {f} is outside repo_path {repo_path}, skipping. Error: {e}"
                                    )
                                    continue
                
                if not code_files:
                    logger.error(f"[TESTGEN] No code files found in {code_path} for job {job_id}")
                    return {
                        "status": "error",
                        "message": f"No code files found in {code_path}",
                    }
                
                logger.info(
                    f"[TESTGEN] Running testgen agent for job {job_id} with {len(code_files)} code files"
                )
                logger.info(f"[TESTGEN] Code files (relative to repo_path): {code_files}")
                
                # Apply inner LLM timeout to fail fast if LLM is unresponsive
                try:
                    async with asyncio.timeout(DEFAULT_TESTGEN_LLM_TIMEOUT):
                        result = await agent.generate_tests(
                            target_files=code_files,
                            language=language,
                            policy=policy
                        )
                except asyncio.TimeoutError:
                    logger.warning(f"[TESTGEN] Job {job_id} inner LLM call timed out after {DEFAULT_TESTGEN_LLM_TIMEOUT}s")
                    return {
                        "status": "error",
                        "message": f"Test generation LLM call timed out after {DEFAULT_TESTGEN_LLM_TIMEOUT} seconds",
                        "timeout": True,
                    }
                
                logger.info(f"[TESTGEN] Test generation completed for job {job_id}")
                
                # Extract generated tests from result
                generated_tests = result.get("generated_tests", {})
                logger.info(f"[TESTGEN] Extracted {len(generated_tests)} test files from result")
                
                if not generated_tests:
                    logger.warning(f"Testgen agent returned no tests for job {job_id}")
                    return {
                        "status": "completed",
                        "generated_files": [],
                        "job_id": job_id,
                        "result": result,
                        "warning": "No test files were generated",
                    }
                
                # Write generated tests to files
                # FIX Issue 3: Write tests into project subdirectory, not repo root
                # Extract project_name from payload - extract only the last path component.
                # The testgen payload uses "code_path" (not "output_dir"), so check both.
                # Strip each candidate individually before the fallback chain to avoid a
                # whitespace-only string (e.g. "   ") being treated as truthy.
                _raw_path = (payload.get("output_dir") or payload.get("code_path") or "").strip()
                project_name = _raw_path
                if project_name:
                    # Extract last component from path (e.g., "my_app" from "generated/my_app")
                    path_parts = project_name.replace("\\", "/").strip("/").split("/")
                    if path_parts:
                        project_name = path_parts[-1]
                
                if not project_name:
                    # Fallback: try to get from package_name if available
                    project_name = payload.get("package_name") or payload.get("package")
                    if not project_name:
                        logger.warning(
                            "[TESTGEN] Could not determine project name from payload; "
                            "tests will be written alongside the generated code root.",
                            extra={"job_id": job_id}
                        )
                
                # Tests should go into generated/<project_name>/tests, not generated/tests.
                # When project_name is None (couldn't be determined) fall back to the
                # code_path directory itself so tests land next to the generated source.
                if project_name:
                    project_dir = repo_path / "generated" / project_name
                    if not project_dir.exists():
                        # Fallback: if project_dir doesn't exist, try to find it
                        # This handles cases where code was generated directly in repo_path/generated
                        alt_project_dir = repo_path / project_name
                        if alt_project_dir.exists():
                            project_dir = alt_project_dir
                        else:
                            # Create the expected structure
                            project_dir.mkdir(parents=True, exist_ok=True)
                            logger.info(f"[TESTGEN] Created project directory: {project_dir}")
                else:
                    # No project name determined — write tests into the code_path root
                    project_dir = Path(code_path).resolve()
                    project_dir.mkdir(parents=True, exist_ok=True)
                
                generated_files = []
                tests_dir = project_dir / "tests"
                tests_dir.mkdir(parents=True, exist_ok=True)
                
                logger.info(f"[TESTGEN] Writing tests to: {tests_dir} (project_name={project_name})")
                
                # Create __init__.py in tests directory
                init_file = tests_dir / "__init__.py"
                async with aiofiles.open(init_file, "w", encoding="utf-8") as f:
                    await f.write('"""Test suite for generated code."""\n')
                generated_files.append(str(init_file.relative_to(repo_path)))
                
                for test_file_path, test_content in generated_tests.items():
                    # Ensure test file path is relative and clean
                    test_path = Path(test_file_path)
                    
                    # If path is absolute or contains "..", use just the filename
                    if test_path.is_absolute() or ".." in str(test_path):
                        test_path = Path(test_path.name)
                    
                    # Construct full path in tests directory
                    full_test_path = tests_dir / test_path.name
                    
                    # Write the test file
                    logger.info(f"[TESTGEN] Writing test file: {full_test_path}")
                    async with aiofiles.open(full_test_path, "w", encoding="utf-8") as f:
                        await f.write(test_content)
                    
                    try:
                        generated_files.append(str(full_test_path.relative_to(repo_path)))
                    except ValueError as e:
                        logger.warning(f"[TESTGEN] File {full_test_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                        generated_files.append(str(full_test_path))
                
                logger.info(f"[TESTGEN] Wrote {len(generated_files)} test files to disk")
                
                return {
                    "status": "completed",
                    "job_id": job_id,
                    "generated_files": generated_files,
                    "tests_count": len(generated_tests),
                    "result": result,
                }
        
        except asyncio.TimeoutError:
            logger.warning(f"[TESTGEN] Job {job_id} LLM call timed out after {DEFAULT_TESTGEN_TIMEOUT}s - skipping tests")
            return {
                "status": "error",
                "message": f"Test generation timed out after {DEFAULT_TESTGEN_TIMEOUT} seconds - skipping tests",
                "timeout": True,
            }
        except Exception as e:
            logger.error(
                f"[TESTGEN] Error running testgen agent for job {job_id}: {str(e)}",
                exc_info=True
            )
            return {
                "status": "error",
                "message": str(e),
            }
    
    async def _run_deploy(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute deployment configuration generation with timeout."""
        logger.info(f"[DEPLOY] Starting deployment for job {job_id} with payload: {payload}")
        
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('deploy', False) or self._deploy_class is None:
            error_msg = "Deploy agent not available"
            logger.error(f"[DEPLOY] Deploy agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Deploy agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            # Wrap deploy generation with configurable timeout
            async with asyncio.timeout(DEFAULT_DEPLOY_TIMEOUT):
                code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
                platform = payload.get("platform", "docker")
                include_ci_cd = payload.get("include_ci_cd", False)
                
                repo_path = Path(code_path)
                if not repo_path.exists():
                    # Create the directory if it doesn't exist
                    repo_path.mkdir(parents=True, exist_ok=True)
                    logger.warning(f"Code path {code_path} did not exist, created directory. This may indicate an upstream issue.")
                
                # Initialize deploy agent
                logger.info(f"Initializing deploy agent for job {job_id} with platform: {platform}")
                agent = self._deploy_class(repo_path=str(repo_path))
                
                # Initialize the agent's database
                await agent._init_db()
                
                # Prepare requirements for deployment
                # FIX 1: Get list of generated files from payload or scan code_path
                generated_files = payload.get("generated_files", [])
                if not generated_files and repo_path.exists():
                    try:
                        # Collect source files, excluding common non-source directories
                        # Industry standard: filter out build artifacts, dependencies, VCS
                        exclude_dirs = {'.git', '.svn', 'node_modules', '__pycache__', '.pytest_cache', 
                                       'dist', 'build', '.venv', 'venv', '.mypy_cache', '.ruff_cache',
                                       '.tox', 'htmlcov', '.eggs', '*.egg-info'}
                        
                        for file_path in repo_path.rglob("*"):
                            if file_path.is_file():
                                # Skip if any parent directory is in exclude list
                                if any(part in exclude_dirs for part in file_path.parts):
                                    continue
                                # Skip hidden files (except specific configs)
                                if any(part.startswith('.') and part not in {'.env.example', '.dockerignore'} 
                                      for part in file_path.parts):
                                    continue
                                # Store relative path from repo_path
                                rel_path = str(file_path.relative_to(repo_path))
                                generated_files.append(rel_path)
                        logger.info(f"[DEPLOY] Found {len(generated_files)} source files in {code_path}")
                    except Exception as e:
                        logger.warning(f"[DEPLOY] Failed to collect files from {code_path}: {e}")
                
                requirements = {
                    "pipeline_steps": ["generate", "validate"],
                    "platform": platform,
                    "include_ci_cd": include_ci_cd,
                    "files": generated_files,  # FIX 1: Pass actual file list
                    "code_path": code_path,
                }
                
                # Run the deployment generation
                logger.info(f"[DEPLOY] Running deploy agent for job {job_id} with target={platform}, files={len(generated_files)}")
                deploy_result = await agent.run_deployment(target=platform, requirements=requirements)
                logger.info(f"[DEPLOY] Deploy agent returned result with keys: {list(deploy_result.keys())}")
                
                # Extract generated config
                configs = deploy_result.get("configs", {})
                logger.info(f"[DEPLOY] Extracted configs: {list(configs.keys())}")
                
                if not configs:
                    logger.warning(f"Deploy agent returned no configurations for job {job_id}")
                    generated_files = []
                    if platform in ("docker", "dockerfile"):
                        # FIX Issue 3: Fix output directory double-nesting for fallback files
                        # Use repo_path directly as it already points to the correct directory
                        output_dir = repo_path
                        
                        # Default Dockerfile
                        default_dockerfile = (
                            "FROM python:3.11-slim\n"
                            "WORKDIR /app\n"
                            "COPY requirements.txt .\n"
                            "RUN pip install --no-cache-dir -r requirements.txt\n"
                            "COPY . /app\n"
                            'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
                        )
                        file_path = output_dir / "Dockerfile"
                        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                            await f.write(default_dockerfile)
                        generated_files.append("Dockerfile")
                        
                        # Default docker-compose.yml
                        default_compose = (
                            "version: '3.8'\n\n"
                            "services:\n"
                            "  app:\n"
                            "    build:\n"
                            "      context: .\n"
                            "      dockerfile: Dockerfile\n"
                            "    ports:\n"
                            '      - "8000:8000"\n'
                            "    environment:\n"
                            "      - ENVIRONMENT=production\n"
                            "      - LOG_LEVEL=info\n"
                            "    restart: unless-stopped\n"
                            "    healthcheck:\n"
                            '      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]\n'
                            "      interval: 30s\n"
                            "      timeout: 10s\n"
                            "      retries: 3\n"
                            "      start_period: 40s\n"
                        )
                        compose_path = output_dir / "docker-compose.yml"
                        async with aiofiles.open(compose_path, "w", encoding="utf-8") as f:
                            await f.write(default_compose)
                        generated_files.append("docker-compose.yml")
                        
                        # Default .dockerignore
                        default_dockerignore = (
                            "__pycache__\n*.pyc\n*.pyo\n.git\n.gitignore\n"
                            ".env\n.venv\nvenv\nnode_modules\n"
                            ".pytest_cache\n.mypy_cache\n*.egg-info\n"
                            "dist\nbuild\n.coverage\nhtmlcov\n"
                        )
                        dockerignore_path = output_dir / ".dockerignore"
                        async with aiofiles.open(dockerignore_path, "w", encoding="utf-8") as f:
                            await f.write(default_dockerignore)
                        generated_files.append(".dockerignore")
                        
                        logger.info(
                            f"[DEPLOY] Generated default deployment fallback for job {job_id}: {generated_files}"
                        )
                    return {
                        "status": "completed",
                        "generated_files": generated_files,
                        "platform": platform,
                        "run_id": deploy_result.get("run_id"),
                        "warning": "No configuration files were generated by agent; default fallback used",
                    }
                
                generated_files = []
                
                # FIX Issue 3: Fix output directory double-nesting
                # The repo_path (code_path from payload) already points to the correct directory
                # (e.g., ./uploads/{job_id}/generated/hello_generator)
                # We should write deployment files directly to this directory, not create
                # another generated/hello_generator subdirectory inside it.
                output_dir = repo_path
                logger.info(f"[DEPLOY] Writing deployment configs to: {output_dir}")
                
                for target, config_content in configs.items():
                    # FIX: Determine filename and subdirectory based on target
                    # Kubernetes and Helm files should go into subdirectories
                    if target == "docker" or target == "dockerfile":
                        filename = "Dockerfile"
                        target_dir = output_dir
                    elif target == "kubernetes" or target == "k8s":
                        # FIX Bug 3 & 4: Kubernetes files go into k8s/ subdirectory with improved YAML splitting
                        target_dir = output_dir / "k8s"
                        target_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Parse YAML content to create separate files (deployment.yaml, service.yaml)
                        # The LLM typically generates multi-document YAML separated by "---"
                        yaml_docs = config_content.split("---")
                        doc_count = 0
                        
                        for idx, doc in enumerate(yaml_docs):
                            doc = doc.strip()
                            
                            # FIX Issue 4: Strip markdown code fences and non-YAML leading
                            # content from each document. The enrichment pipeline wraps YAML
                            # in markdown fences (```yaml ... ```) that would corrupt files.
                            doc_lines = [
                                line for line in doc.splitlines()
                                if not re.match(r'^```', line.strip())
                            ]
                            # Discard leading non-YAML lines (markdown headers, prose) before
                            # the first line that looks like YAML (apiVersion, kind, metadata, ---)
                            yaml_start = 0
                            for i, line in enumerate(doc_lines):
                                if line.strip() == '---' or re.match(
                                    r'^\s*(apiVersion|kind|metadata)\s*:', line
                                ):
                                    yaml_start = i
                                    break
                            else:
                                # No YAML start marker found; the empty doc will be caught
                                # by the length check below and skipped with a warning.
                                logger.warning(
                                    f"[DEPLOY] No YAML start marker found in K8s document {idx}; "
                                    "likely non-YAML content (markdown preamble only) — skipping"
                                )
                                yaml_start = len(doc_lines)
                            doc = '\n'.join(doc_lines[yaml_start:]).strip()
                            
                            # FIX Bug 3: Handle edge cases in YAML splitting
                            # Skip empty documents or those that are too short to be valid
                            if not doc or len(doc) < MIN_YAML_DOC_LENGTH:
                                logger.debug(f"Skipping empty/short K8s YAML document {idx}")
                                continue
                            
                            # Determine filename based on document kind
                            # Use case-insensitive search for better compatibility

                            # Try to extract the kind field more robustly
                            kind_match = _K8S_KIND_RE.search(doc)
                            kind = kind_match.group(1) if kind_match else None
                            
                            if kind:
                                # Map kind to filename
                                kind_to_filename = {
                                    "Deployment": "deployment.yaml",
                                    "Service": "service.yaml",
                                    "Ingress": "ingress.yaml",
                                    "ConfigMap": "configmap.yaml",
                                    "Secret": "secret.yaml",
                                    "HorizontalPodAutoscaler": "hpa.yaml",
                                    "PersistentVolumeClaim": "pvc.yaml",
                                    "ServiceAccount": "serviceaccount.yaml",
                                    "Role": "role.yaml",
                                    "RoleBinding": "rolebinding.yaml",
                                    "NetworkPolicy": "networkpolicy.yaml",
                                }
                                doc_filename = kind_to_filename.get(kind, f"{kind.lower()}.yaml")
                            else:
                                # Fallback: use index-based name if kind is not found
                                doc_filename = f"resource-{doc_count}.yaml"
                                logger.warning(f"Could not determine kind for K8s document {idx}, using fallback filename")
                            
                            # Validate document is a well-formed YAML dict before writing
                            try:
                                parsed_doc = yaml.safe_load(doc)
                                if not isinstance(parsed_doc, dict):
                                    logger.error(
                                        f"[DEPLOY] K8s document {idx} is not a valid dictionary "
                                        f"(got {type(parsed_doc).__name__}) — skipping"
                                    )
                                    continue
                            except Exception as yaml_exc:
                                logger.error(
                                    f"[DEPLOY] K8s document {idx} has invalid YAML: {yaml_exc} — skipping"
                                )
                                continue

                            # Write the document to file
                            file_path = target_dir / doc_filename
                            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                                await f.write(doc)
                            
                            doc_count += 1
                            
                            try:
                                rel_path = str(file_path.resolve().relative_to(repo_path.resolve()))
                                generated_files.append(rel_path)
                            except ValueError as e:
                                logger.warning(f"[DEPLOY] File {file_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                                generated_files.append(str(file_path))
                            logger.info(f"Generated kubernetes file: {file_path} (kind: {kind or 'unknown'})")
                        
                        if doc_count == 0:
                            logger.warning(f"[DEPLOY] No valid Kubernetes documents found in content for target {target}")
                            # Issue 7 fix: retry the deploy agent for kubernetes targets when
                            # YAML validation produces zero valid documents.  The retry prompt
                            # includes explicit constraints to prevent the LLM from returning
                            # prose, JSON, or markdown-fenced content instead of raw YAML.
                            for _k8s_retry_attempt in range(2):
                                try:
                                    _retry_requirements = dict(requirements)
                                    _retry_requirements["instructions"] = (
                                        "The previous Kubernetes YAML output was invalid or contained "
                                        "no parseable documents.  Generate ONLY raw Kubernetes YAML "
                                        "documents separated by '---'.  Every document MUST begin with "
                                        "'apiVersion:' and include 'kind:', 'metadata:', and 'spec:' "
                                        "fields.  Do NOT include prose, explanations, markdown fences, "
                                        "or JSON — only YAML."
                                    )
                                    logger.info(
                                        f"[DEPLOY] Kubernetes YAML retry {_k8s_retry_attempt + 1}/2 for job {job_id}"
                                    )
                                    _retry_result = await agent.run_deployment(
                                        target=target, requirements=_retry_requirements
                                    )
                                    _retry_configs = _retry_result.get("configs", {})
                                    _retry_content = (
                                        _retry_configs.get(target)
                                        or _retry_configs.get("kubernetes")
                                        or ""
                                    )
                                    if _retry_content:
                                        _retry_doc_count = 0
                                        for _ridx, _rdoc in enumerate(_retry_content.split("---")):
                                            _rdoc = _rdoc.strip()
                                            if not _rdoc or len(_rdoc) < MIN_YAML_DOC_LENGTH:
                                                continue
                                            try:
                                                _parsed = yaml.safe_load(_rdoc)
                                                if isinstance(_parsed, dict):
                                                    _kind_match = _K8S_KIND_RE.search(_rdoc)
                                                    _kind_name = (
                                                        _kind_match.group(1) if _kind_match else None
                                                    )
                                                    _fn = {
                                                        "Deployment": "deployment.yaml",
                                                        "Service":    "service.yaml",
                                                    }.get(_kind_name or "", f"resource-{_ridx}.yaml")
                                                    _fp = target_dir / _fn
                                                    async with aiofiles.open(
                                                        _fp, "w", encoding="utf-8"
                                                    ) as f:
                                                        await f.write(_rdoc)
                                                    _retry_doc_count += 1
                                                    doc_count += 1
                                                    logger.info(
                                                        f"[DEPLOY] K8s retry wrote: {_fp} (kind={_kind_name})"
                                                    )
                                            except Exception:
                                                pass
                                        if _retry_doc_count > 0:
                                            logger.info(
                                                f"[DEPLOY] K8s retry {_k8s_retry_attempt + 1} produced "
                                                f"{_retry_doc_count} valid document(s)"
                                            )
                                            break
                                except Exception as _k8s_retry_err:
                                    logger.warning(
                                        f"[DEPLOY] K8s retry {_k8s_retry_attempt + 1} failed: {_k8s_retry_err}"
                                    )
                            if doc_count == 0:
                                logger.warning(
                                    "[DEPLOY] All K8s retries exhausted — "
                                    "falling back to default deployment/service manifests"
                                )
                        
                        # Ensure service.yaml exists — it is required by the deploy validator.
                        # If the LLM did not include a Service resource, generate a sensible default.
                        service_yaml_path = target_dir / "service.yaml"
                        if not service_yaml_path.exists():
                            _app_name = output_dir.name or "app"
                            default_service_yaml = (
                                "---\n"
                                "apiVersion: v1\n"
                                "kind: Service\n"
                                "metadata:\n"
                                f"  name: {_app_name}-service\n"
                                "spec:\n"
                                "  selector:\n"
                                f"    app: {_app_name}\n"
                                "  ports:\n"
                                "  - protocol: TCP\n"
                                "    port: 80\n"
                                "    targetPort: 8000\n"
                                "  type: ClusterIP\n"
                            )
                            try:
                                async with aiofiles.open(service_yaml_path, "w", encoding="utf-8") as f:
                                    await f.write(default_service_yaml)
                                try:
                                    generated_files.append(str(service_yaml_path.relative_to(repo_path)))
                                except ValueError:
                                    generated_files.append(str(service_yaml_path))
                                logger.info("[DEPLOY] Generated default k8s/service.yaml (LLM did not include a Service resource)")
                            except Exception as svc_err:
                                logger.warning(f"[DEPLOY] Could not write default service.yaml: {svc_err}")
                        
                        # Ensure deployment.yaml exists — required for deployment completeness validation.
                        # If the LLM did not include a Deployment resource, generate a sensible default.
                        deployment_yaml_path = target_dir / "deployment.yaml"
                        if not deployment_yaml_path.exists():
                            _app_name = output_dir.name or "app"
                            default_deployment_yaml = (
                                "---\n"
                                "apiVersion: apps/v1\n"
                                "kind: Deployment\n"
                                "metadata:\n"
                                f"  name: {_app_name}\n"
                                "spec:\n"
                                "  replicas: 1\n"
                                "  selector:\n"
                                "    matchLabels:\n"
                                f"      app: {_app_name}\n"
                                "  template:\n"
                                "    metadata:\n"
                                "      labels:\n"
                                f"        app: {_app_name}\n"
                                "    spec:\n"
                                "      containers:\n"
                                f"      - name: {_app_name}\n"
                                f"        image: {_app_name}:latest\n"
                                "        ports:\n"
                                "        - containerPort: 8000\n"
                            )
                            try:
                                async with aiofiles.open(deployment_yaml_path, "w", encoding="utf-8") as f:
                                    await f.write(default_deployment_yaml)
                                try:
                                    generated_files.append(str(deployment_yaml_path.relative_to(repo_path)))
                                except ValueError:
                                    generated_files.append(str(deployment_yaml_path))
                                logger.info("[DEPLOY] Generated default k8s/deployment.yaml (LLM did not include a Deployment resource)")
                            except Exception as dep_err:
                                logger.warning(f"[DEPLOY] Could not write default deployment.yaml: {dep_err}")
                        
                        continue  # Skip the default file writing below
                    elif target == "helm":
                        # FIX Bug 3 & 4: Helm files go into helm/ subdirectory with proper chart structure
                        target_dir = output_dir / "helm"
                        target_dir.mkdir(parents=True, exist_ok=True)
                        templates_dir = target_dir / "templates"
                        templates_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Parse Helm chart content from LLM
                        # The HelmHandler returns structured data with Chart.yaml, values.yaml, templates
                        try:
                            # Try to parse as JSON first (structured response)
                            helm_data = json.loads(config_content)
                            
                            # Write Chart.yaml
                            if "Chart.yaml" in helm_data:
                                chart_file = target_dir / "Chart.yaml"
                                async with aiofiles.open(chart_file, "w", encoding="utf-8") as f:
                                    if isinstance(helm_data["Chart.yaml"], dict):
                                        await f.write(yaml.dump(helm_data["Chart.yaml"], default_flow_style=False))
                                    else:
                                        await f.write(str(helm_data["Chart.yaml"]))
                                generated_files.append(str(chart_file.relative_to(repo_path)))
                                logger.info(f"Generated helm file: {chart_file}")
                            
                            # Write values.yaml
                            if "values.yaml" in helm_data:
                                values_file = target_dir / "values.yaml"
                                async with aiofiles.open(values_file, "w", encoding="utf-8") as f:
                                    if isinstance(helm_data["values.yaml"], dict):
                                        await f.write(yaml.dump(helm_data["values.yaml"], default_flow_style=False))
                                    else:
                                        await f.write(str(helm_data["values.yaml"]))
                                generated_files.append(str(values_file.relative_to(repo_path)))
                                logger.info(f"Generated helm file: {values_file}")
                            
                            # Write template files
                            if "templates" in helm_data and isinstance(helm_data["templates"], dict):
                                for template_name, template_content in helm_data["templates"].items():
                                    # Ensure template name is just the filename
                                    if "/" in template_name:
                                        template_name = template_name.split("/")[-1]
                                    template_file = templates_dir / template_name
                                    template_str = str(template_content)
                                    # Issue 8 fix: validate that Helm template files use Go template
                                    # syntax ({{ ... }}).  A template without any {{ }} expressions
                                    # is likely a raw JSON blob or static YAML and won't work with
                                    # `helm install`.  Log a warning so operators can investigate.
                                    if (
                                        template_name.endswith(".yaml")
                                        and template_name != "_helpers.tpl"
                                        and "{{" not in template_str
                                    ):
                                        logger.warning(
                                            f"[DEPLOY] Helm template '{template_name}' does not contain "
                                            "Go template expressions ({{ .Values.* }}). "
                                            "The LLM may have generated raw JSON/YAML instead of a Helm template."
                                        )
                                    async with aiofiles.open(template_file, "w", encoding="utf-8") as f:
                                        await f.write(template_str)
                                    generated_files.append(str(template_file.relative_to(repo_path)))
                                    logger.info(f"Generated helm template: {template_file}")
                        except (json.JSONDecodeError, ValueError):
                            # Fallback: treat as raw YAML content for Chart.yaml
                            # Split by common delimiters or file markers
                            logger.warning("[DEPLOY] Helm content not in expected JSON format, using fallback parsing")
                            
                            # Try to split by file markers (# Chart.yaml, # values.yaml, etc.)
                            if "# Chart.yaml" in config_content or "# values.yaml" in config_content:
                                # Parse structured YAML with file markers
                                sections = re.split(r'#\s*(Chart\.yaml|values\.yaml|templates/[\w\-]+\.yaml)', config_content)
                                
                                current_file = None
                                for i, section in enumerate(sections):
                                    if i % 2 == 1:  # File name
                                        current_file = section.strip()
                                    elif current_file and section.strip():  # File content
                                        if current_file.startswith("templates/"):
                                            # Template file
                                            template_name = current_file.replace("templates/", "")
                                            template_file = templates_dir / template_name
                                            async with aiofiles.open(template_file, "w", encoding="utf-8") as f:
                                                await f.write(section.strip())
                                            generated_files.append(str(template_file.relative_to(repo_path)))
                                            logger.info(f"Generated helm template: {template_file}")
                                        elif current_file == "Chart.yaml":
                                            chart_file = target_dir / "Chart.yaml"
                                            async with aiofiles.open(chart_file, "w", encoding="utf-8") as f:
                                                await f.write(section.strip())
                                            generated_files.append(str(chart_file.relative_to(repo_path)))
                                            logger.info(f"Generated helm file: {chart_file}")
                                        elif current_file == "values.yaml":
                                            values_file = target_dir / "values.yaml"
                                            async with aiofiles.open(values_file, "w", encoding="utf-8") as f:
                                                await f.write(section.strip())
                                            generated_files.append(str(values_file.relative_to(repo_path)))
                                            logger.info(f"Generated helm file: {values_file}")
                            else:
                                # Final fallback: validate content is valid YAML before writing
                                # BUG FIX 5: Industry Standard - Defense in depth approach
                                # Don't write invalid markdown/prose as Chart.yaml
                                # Security: Validate structure to prevent injection attacks
                                chart_file = target_dir / "Chart.yaml"
                                
                                logger.debug(
                                    "[DEPLOY] Helm content not in structured format, attempting YAML validation",
                                    extra={"content_length": len(config_content)}
                                )
                                
                                try:
                                    # Use safe_load_all to handle multi-document YAML (separated by ---)
                                    # Take only the first valid dict document (e.g. Chart.yaml)
                                    parsed_yaml = None
                                    try:
                                        for _doc in yaml.safe_load_all(config_content):
                                            if isinstance(_doc, dict):
                                                parsed_yaml = _doc
                                                break
                                    except yaml.YAMLError:
                                        pass

                                    # Validate structure
                                    if not isinstance(parsed_yaml, dict):
                                        logger.warning(
                                            f"[DEPLOY] Helm content is not a YAML dict (got {type(parsed_yaml).__name__}), using default chart",
                                            extra={
                                                "actual_type": type(parsed_yaml).__name__,
                                                "fallback": "default_chart"
                                            }
                                        )
                                        await self._write_default_helm_chart(chart_file, repo_path, generated_files)
                                    else:
                                        # Provide defaults for missing required Helm chart fields
                                        # so that valid raw YAML from the LLM is not discarded
                                        helm_app_name = output_dir.name or "app"
                                        if "apiVersion" not in parsed_yaml:
                                            parsed_yaml["apiVersion"] = "v2"
                                        if "name" not in parsed_yaml:
                                            parsed_yaml["name"] = helm_app_name
                                        required_keys = {"apiVersion", "name"}
                                        has_required = required_keys.issubset(parsed_yaml.keys())
                                        
                                        if has_required:
                                            # Check if this is a structured format (Chart.yaml, values.yaml, templates as keys)
                                            # This happens when the LLM returns JSON that was round-tripped through YAML
                                            structured_keys = {"Chart.yaml", "values.yaml", "templates"}
                                            is_structured = bool(structured_keys & set(parsed_yaml.keys()))
                                            
                                            if is_structured:
                                                # Handle as structured format: write each file separately
                                                logger.info("[DEPLOY] Helm YAML contains structured keys, writing individual files")
                                                if "Chart.yaml" in parsed_yaml:
                                                    _chart_data = parsed_yaml["Chart.yaml"]
                                                    async with aiofiles.open(chart_file, "w", encoding="utf-8") as f:
                                                        if isinstance(_chart_data, dict):
                                                            await f.write(yaml.dump(_chart_data, default_flow_style=False))
                                                        else:
                                                            await f.write(str(_chart_data))
                                                    generated_files.append(str(chart_file.relative_to(repo_path)))
                                                    logger.info("[DEPLOY] Generated helm Chart.yaml from structured YAML")
                                                else:
                                                    # Use apiVersion/name from top-level as Chart.yaml
                                                    chart_meta = {k: v for k, v in parsed_yaml.items() if k not in structured_keys}
                                                    async with aiofiles.open(chart_file, "w", encoding="utf-8") as f:
                                                        await f.write(yaml.dump(chart_meta, default_flow_style=False))
                                                    generated_files.append(str(chart_file.relative_to(repo_path)))
                                                if "values.yaml" in parsed_yaml:
                                                    values_file = target_dir / "values.yaml"
                                                    _values_data = parsed_yaml["values.yaml"]
                                                    async with aiofiles.open(values_file, "w", encoding="utf-8") as f:
                                                        if isinstance(_values_data, dict):
                                                            await f.write(yaml.dump(_values_data, default_flow_style=False))
                                                        else:
                                                            await f.write(str(_values_data))
                                                    generated_files.append(str(values_file.relative_to(repo_path)))
                                                    logger.info("[DEPLOY] Generated helm values.yaml from structured YAML")
                                                if "templates" in parsed_yaml and isinstance(parsed_yaml["templates"], dict):
                                                    for tmpl_name, tmpl_content in parsed_yaml["templates"].items():
                                                        if "/" in tmpl_name:
                                                            tmpl_name = tmpl_name.split("/")[-1]
                                                        tmpl_file = templates_dir / tmpl_name
                                                        async with aiofiles.open(tmpl_file, "w", encoding="utf-8") as f:
                                                            await f.write(str(tmpl_content))
                                                        generated_files.append(str(tmpl_file.relative_to(repo_path)))
                                                        logger.info(f"[DEPLOY] Generated helm template: {tmpl_file}")
                                            else:
                                                # Content appears to be a valid Helm chart, write it
                                                async with aiofiles.open(chart_file, "w", encoding="utf-8") as f:
                                                    await f.write(yaml.dump(parsed_yaml, default_flow_style=False))
                                                generated_files.append(str(chart_file.relative_to(repo_path)))
                                                logger.info(
                                                    "[DEPLOY] Generated helm Chart.yaml (validated)",
                                                    extra={
                                                        "chart_name": parsed_yaml.get("name"),
                                                        "api_version": parsed_yaml.get("apiVersion")
                                                    }
                                                )
                                        else:
                                            # Missing required fields, use default
                                            logger.warning(
                                                f"[DEPLOY] Helm content missing required fields {required_keys}, using default chart",
                                                extra={
                                                    "present_keys": list(parsed_yaml.keys()),
                                                    "missing_keys": list(required_keys - parsed_yaml.keys()),
                                                    "fallback": "default_chart"
                                                }
                                            )
                                            await self._write_default_helm_chart(chart_file, repo_path, generated_files)
                                            
                                except yaml.YAMLError as e:
                                    # Invalid YAML syntax, use default chart
                                    logger.warning(
                                        f"[DEPLOY] Helm content has invalid YAML syntax: {type(e).__name__}, using default chart",
                                        extra={
                                            "error": str(e),
                                            "error_type": type(e).__name__,
                                            "fallback": "default_chart"
                                        }
                                    )
                                    await self._write_default_helm_chart(chart_file, repo_path, generated_files)
                                except Exception as e:
                                    # Unexpected error, use default chart
                                    logger.error(
                                        f"[DEPLOY] Unexpected error validating Helm content: {type(e).__name__}: {e}, using default chart",
                                        extra={
                                            "error": str(e),
                                            "error_type": type(e).__name__,
                                            "fallback": "default_chart"
                                        },
                                        exc_info=True
                                    )
                                    await self._write_default_helm_chart(chart_file, repo_path, generated_files)
                                
                                # Create minimal values.yaml
                                values_file = target_dir / "values.yaml"
                                async with aiofiles.open(values_file, "w", encoding="utf-8") as f:
                                    await f.write("# Helm chart values\nreplicaCount: 1\n")
                                generated_files.append(str(values_file.relative_to(repo_path)))
                        
                        continue  # Skip the default file writing below
                    elif target == "docker-compose":
                        filename = "docker-compose.yml"
                        target_dir = output_dir
                    elif target == "terraform":
                        filename = "main.tf"
                        target_dir = output_dir
                    else:
                        filename = f"{target}.config"
                        target_dir = output_dir
                    
                    # Sanitize Dockerfile content: strip markdown/images/mermaid tokens
                    if filename == "Dockerfile":
                        config_content = self._sanitize_dockerfile_content(config_content)
                    
                    file_path = target_dir / filename
                    
                    # Write the file
                    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                        await f.write(config_content)
                    
                    # [FIX] Add error handling for path resolution
                    try:
                        generated_files.append(str(file_path.resolve().relative_to(repo_path.resolve())))
                    except ValueError as e:
                        logger.warning(f"[DEPLOY] File {file_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                        generated_files.append(str(file_path))
                    logger.info(f"Generated deployment file: {file_path}")
                
                # Generate standard .dockerignore if a Dockerfile was produced
                dockerfile_path = output_dir / "Dockerfile"
                if dockerfile_path.exists():
                    dockerignore_path = output_dir / ".dockerignore"
                    if not dockerignore_path.exists():
                        dockerignore_content = (
                            "__pycache__\n*.pyc\n*.pyo\n.git\n.gitignore\n"
                            ".env\n.venv\nvenv\nnode_modules\n"
                            ".pytest_cache\n.mypy_cache\n*.egg-info\n"
                            "dist\nbuild\n.coverage\nhtmlcov\n"
                        )
                        async with aiofiles.open(dockerignore_path, "w", encoding="utf-8") as f:
                            await f.write(dockerignore_content)
                        generated_files.append(".dockerignore")
                        logger.info(f"Generated .dockerignore: {dockerignore_path}")
                
                # Write deploy_metadata.json
                deploy_meta_path = output_dir / "deploy_metadata.json"
                deploy_meta = {
                    "platform": platform,
                    "run_id": deploy_result.get("run_id"),
                    "generated_files": generated_files,
                    "validations": deploy_result.get("validations", {}),
                }
                async with aiofiles.open(deploy_meta_path, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(deploy_meta, indent=2))
                generated_files.append("deploy_metadata.json")
                
                result = {
                    "status": "completed",
                    "generated_files": generated_files,
                    "platform": platform,
                    "run_id": deploy_result.get("run_id"),
                    "validations": deploy_result.get("validations", {}),
                }
                
                logger.info(f"Deploy agent completed for job {job_id}, generated {len(generated_files)} files")
                return result
        
        except asyncio.TimeoutError:
            logger.warning(f"[DEPLOY] Job {job_id} timed out after 90s - skipping deployment configs")
            return {
                "status": "error",
                "message": "Deployment generation timed out after 90 seconds",
                "timeout": True,
            }
        except Exception as e:
            logger.error(f"Error running deploy agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_deploy_all(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute deployment for ALL targets (docker, kubernetes, helm) sequentially.
        
        This method runs all deployment targets as required stages following industry
        best practices for observability, error handling, and security.
        
        Compliance:
            - SOC 2 Type II: Comprehensive logging and error handling
            - ISO 27001 A.12.4.1: Event logging for security monitoring
            - NIST SP 800-53 AU-2: Auditable events tracking
        
        Args:
            job_id: The job identifier (validated for path traversal)
            payload: Deployment configuration containing:
                - code_path: Path to generated code directory
                - include_ci_cd: Boolean flag for CI/CD configs
                
        Returns:
            Dict containing:
                - status: "completed" or "error"
                - results: Dict mapping each target to its result
                - generated_files: List of all generated files across all targets
                - failed_targets: List of targets that failed (if any)
                - duration_seconds: Total execution time
                
        Raises:
            ValueError: If job_id or payload is invalid
            SecurityError: If path traversal is detected
        """
        # Input validation
        if not job_id or not isinstance(job_id, str):
            raise ValueError("job_id must be a non-empty string")
        
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dictionary")
        
        # Path traversal protection (matching platform security patterns)
        if ".." in job_id or "/" in job_id or "\\" in job_id:
            logger.error(f"[DEPLOY_ALL] Path traversal attempt detected in job_id: {job_id}")
            raise SecurityError("Invalid job_id: path traversal attempt detected")
        
        # Start timing and tracing
        start_time = time.time()
        
        # Use OpenTelemetry tracing if available
        span_context = (
            tracer.start_as_current_span("deploy.deploy_all") 
            if TRACING_AVAILABLE 
            else None
        )
        
        try:
            if span_context:
                with span_context as span:
                    span.set_attribute("job_id", job_id)
                    span.set_attribute("targets_count", 3)
                    result = await self._execute_deploy_all_targets(
                        job_id, payload, start_time
                    )
                    span.set_status(Status(StatusCode.OK))
                    return result
            else:
                return await self._execute_deploy_all_targets(
                    job_id, payload, start_time
                )
                
        except Exception as e:
            logger.error(
                f"[DEPLOY_ALL] Critical error in deploy_all for job {job_id}: {e}",
                exc_info=True,
                extra={"job_id": job_id, "error_type": type(e).__name__}
            )
            
            # Record metrics if available
            if METRICS_AVAILABLE:
                deployment_requests_total.labels(
                    job_id=job_id,
                    target="all",
                    status="error"
                ).inc()
            
            raise
    
    async def _execute_deploy_all_targets(
        self, 
        job_id: str, 
        payload: Dict[str, Any],
        start_time: float
    ) -> Dict[str, Any]:
        """Internal method to execute all deployment targets with full observability.
        
        Separated for cleaner tracing and error handling.
        
        Args:
            job_id: The validated job identifier
            payload: Validated deployment configuration
            start_time: Start timestamp for duration tracking
            
        Returns:
            Dict with deployment results and metadata
        """
        logger.info(
            "[DEPLOY_ALL] Starting deployment for all targets",
            extra={
                "job_id": job_id,
                "targets": ["docker", "kubernetes", "helm"],
                "include_ci_cd": payload.get("include_ci_cd", False)
            }
        )
        
        # Define the required deployment targets
        targets = ["docker", "kubernetes", "helm"]
        results = {}
        all_generated_files = []
        failed_targets = []
        
        # Extract and validate code_path
        code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
        include_ci_cd = payload.get("include_ci_cd", False)
        
        # Additional path validation
        code_path_obj = Path(code_path)
        if not code_path_obj.is_absolute():
            code_path_obj = Path.cwd() / code_path
        
        # Run each target sequentially with individual error handling
        for target_idx, target in enumerate(targets):
            target_start = time.time()
            
            logger.info(
                f"[DEPLOY_ALL] Processing target {target_idx + 1}/{len(targets)}: {target}",
                extra={"job_id": job_id, "target": target, "sequence": f"{target_idx + 1}/{len(targets)}"}
            )
            
            # FIX 1: Get list of generated files from code_path for deploy_all
            generated_files = []
            if code_path_obj.exists():
                try:
                    # Collect source files, excluding common non-source directories
                    # Industry standard: filter out build artifacts, dependencies, VCS
                    exclude_dirs = {'.git', '.svn', 'node_modules', '__pycache__', '.pytest_cache', 
                                   'dist', 'build', '.venv', 'venv', '.mypy_cache', '.ruff_cache',
                                   '.tox', 'htmlcov', '.eggs', '*.egg-info'}
                    
                    for file_path in code_path_obj.rglob("*"):
                        if file_path.is_file():
                            # Skip if any parent directory is in exclude list
                            if any(part in exclude_dirs for part in file_path.parts):
                                continue
                            # Skip hidden files (except specific configs)
                            if any(part.startswith('.') and part not in {'.env.example', '.dockerignore'} 
                                  for part in file_path.parts):
                                continue
                            # Store relative path from code_path
                            rel_path = str(file_path.relative_to(code_path_obj))
                            generated_files.append(rel_path)
                    logger.info(f"[DEPLOY_ALL] Found {len(generated_files)} source files in {code_path} for target {target}")
                except Exception as e:
                    logger.warning(f"[DEPLOY_ALL] Failed to collect files from {code_path}: {e}")
            
            target_payload = {
                "code_path": code_path,
                "platform": target,
                "include_ci_cd": include_ci_cd,
                "generated_files": generated_files,  # FIX 1: Pass files list
            }
            
            try:
                target_result = await self._run_deploy(job_id, target_payload)
                results[target] = target_result
                
                # FIX Bug 5: Track failures and validate deployment artifacts
                if target_result.get("status") == "failed":
                    failed_targets.append(target)
                    logger.error(
                        f"[DEPLOY_ALL] Target {target} failed",
                        extra={
                            "job_id": job_id,
                            "target": target,
                            "error": target_result.get("error", "Unknown error")
                        }
                    )
                else:
                    # Track generated files
                    target_files = target_result.get("generated_files", [])
                    all_generated_files.extend(target_files)
                    
                    # FIX Issue 5: Check if target produced 0 files and mark as failed
                    if len(target_files) == 0:
                        failed_targets.append(target)
                        logger.warning(
                            f"[DEPLOY_ALL] Target '{target}' completed but produced 0 files - marking as failed",
                            extra={"job_id": job_id, "target": target}
                        )
                    else:
                        logger.info(
                            f"[DEPLOY_ALL] Target {target} completed with {len(target_files)} files",
                            extra={"job_id": job_id, "target": target, "duration": time.time() - target_start}
                        )
                    
            except Exception as e:
                # FIX Bug 5: Track exceptions as failures
                failed_targets.append(target)
                logger.exception(
                    f"[DEPLOY_ALL] Target {target} raised exception: {e}",
                    extra={"job_id": job_id, "target": target}
                )
                results[target] = {
                    "status": "failed",
                    "error": str(e),
                    "target": target
                }
        
        # FIX Bug 5: Check if any critical targets failed
        # For now, all three targets (docker, kubernetes, helm) are considered required
        if failed_targets:
            error_msg = f"Deployment failed for targets: {', '.join(failed_targets)}"
            logger.error(
                f"[DEPLOY_ALL] {error_msg}",
                extra={
                    "job_id": job_id,
                    "failed_targets": failed_targets,
                    "successful_targets": [t for t in targets if t not in failed_targets]
                }
            )
            # Return failure status but include partial results
            return {
                "status": "failed",
                "error": error_msg,
                "failed_targets": failed_targets,
                "results": results,
                "generated_files": all_generated_files,
                "duration": time.time() - start_time
            }
        
        logger.info(
            "[DEPLOY_ALL] All deployment targets completed successfully",
            extra={
                "job_id": job_id,
                "targets": targets,
                "total_files": len(all_generated_files),
                "duration": time.time() - start_time
            }
        )
        
        return {
            "status": "completed",
            "message": "All deployment targets completed successfully",
            "results": results,
            "generated_files": all_generated_files,
            "failed_targets": [],
            "completed_targets": targets,
        }
    
    async def _validate_deployment_completeness(self, job_id: str, code_path: str) -> Dict[str, Any]:
        """Validate that all required deployment files exist and are valid.
        
        This method performs comprehensive validation of deployment artifacts,
        ensuring compliance with security and quality standards.
        
        Compliance:
            - SOC 2 Type II: Validation of deployment configurations
            - CIS Benchmarks: Security validation for containers and Kubernetes
            - OWASP: Secure configuration validation
        
        Uses the DeploymentCompletenessValidator to check:
            - All required deployment files exist
            - No unsubstituted placeholders remain
            - YAML files have valid syntax
            - Dockerfiles have required instructions
            - Deployment configs match actual generated code
        
        Args:
            job_id: The job identifier (for logging and metrics)
            code_path: Path to the generated code directory (must exist)
            
        Returns:
            Dict containing validation results:
                - status: "passed", "failed", or "error"
                - errors: List of detailed validation errors
                - warnings: List of non-fatal warnings
                - missing_files: List of required files not found
                - invalid_files: List of files with validation issues
                
        Raises:
            ImportError: If validator cannot be imported
            OSError: If code_path doesn't exist or is inaccessible
        """
        validation_start = time.time()
        
        logger.info(
            "[DEPLOY_VALIDATION] Starting deployment completeness validation",
            extra={"job_id": job_id, "code_path": code_path}
        )
        
        # Import the validator with graceful error handling
        try:
            from generator.agents.deploy_agent.deploy_validator import DeploymentCompletenessValidator
        except ImportError as e:
            logger.error(
                "[DEPLOY_VALIDATION] Failed to import DeploymentCompletenessValidator",
                exc_info=True,
                extra={"job_id": job_id, "error": str(e)}
            )
            
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status="error",
                    validation_type="import_error"
                ).inc()
            
            return {
                "status": "error",
                "errors": [f"Failed to import validator: {str(e)}"],
            }
        
        # Validate code_path exists and is accessible
        code_path_obj = Path(code_path) if code_path else None
        if not code_path_obj or not code_path_obj.exists():
            error_msg = f"Code path does not exist: {code_path}"
            logger.error(
                "[DEPLOY_VALIDATION] Invalid code path",
                extra={"job_id": job_id, "code_path": code_path}
            )
            
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status="error",
                    validation_type="invalid_path"
                ).inc()
            
            return {
                "status": "error",
                "errors": [error_msg],
            }
        
        # Change to the code path directory for validation
        # Store original CWD for restoration in finally block
        original_cwd = os.getcwd()
        
        try:
            os.chdir(code_path)
            logger.debug(
                "[DEPLOY_VALIDATION] Changed working directory",
                extra={"job_id": job_id, "new_cwd": code_path}
            )
            
            # Create validator instance
            validator = DeploymentCompletenessValidator()
            
            # Validate all deployment types
            validation_result = await validator.validate(
                config_content="",  # Not used for file-based validation
                target_type="all"   # Validate all deployment types (docker, kubernetes, helm)
            )
            
            # Calculate validation duration
            validation_duration = time.time() - validation_start
            
            # Enhanced logging with structured data
            logger.info(
                f"[DEPLOY_VALIDATION] Validation completed: {validation_result.get('status')}",
                extra={
                    "job_id": job_id,
                    "status": validation_result.get('status'),
                    "duration_seconds": round(validation_duration, 3),
                    "missing_files_count": len(validation_result.get('missing_files', [])),
                    "invalid_files_count": len(validation_result.get('invalid_files', [])),
                    "placeholder_issues_count": len(validation_result.get('placeholder_issues', [])),
                    "warnings_count": len(validation_result.get('warnings', []))
                }
            )
            
            # Record validation metrics
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status=validation_result.get('status', 'unknown'),
                    validation_type="completeness"
                ).inc()
            
            return validation_result
            
        except Exception as e:
            validation_duration = time.time() - validation_start
            logger.error(
                "[DEPLOY_VALIDATION] Validation exception occurred",
                exc_info=True,
                extra={
                    "job_id": job_id,
                    "error_type": type(e).__name__,
                    "duration_seconds": round(validation_duration, 3)
                }
            )
            
            # Record error metrics
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status="exception",
                    validation_type="completeness"
                ).inc()
            
            return {
                "status": "error",
                "errors": [f"Validation error: {str(e)}"],
                "error_type": type(e).__name__,
            }
            
        finally:
            # Always restore original working directory
            # Critical for preventing side effects in other operations
            try:
                os.chdir(original_cwd)
                logger.debug(
                    "[DEPLOY_VALIDATION] Restored working directory",
                    extra={"job_id": job_id, "restored_cwd": original_cwd}
                )
            except Exception as restore_error:
                logger.error(
                    "[DEPLOY_VALIDATION] Failed to restore working directory",
                    exc_info=True,
                    extra={
                        "job_id": job_id,
                        "original_cwd": original_cwd,
                        "error": str(restore_error)
                    }
                )
    
    @staticmethod
    def _sanitize_dockerfile_content(content: str) -> str:
        """Sanitize Dockerfile content from LLM responses.

        Strips markdown fences, image/badge lines, mermaid blocks, and
        ensures the first non-comment non-blank line starts with FROM.
        If no FROM is found, prepends a default FROM instruction.
        """
        if not content or not isinstance(content, str):
            return content

        # Strip markdown fences (```dockerfile ... ```)
        content = re.sub(
            r'^```(?:dockerfile|docker|Dockerfile)?\s*\n', '', content, flags=re.IGNORECASE
        )
        content = re.sub(r'\n```\s*$', '', content)

        lines = content.splitlines()
        cleaned: List[str] = []
        for line in lines:
            stripped = line.strip()
            # Remove markdown image/badge lines: ![...](...)
            if stripped.startswith('!['):
                continue
            # Remove mermaid/markdown tokens
            if stripped.startswith('```'):
                continue
            # Remove lines starting with '!' (invalid Dockerfile token)
            if stripped.startswith('!'):
                continue
            cleaned.append(line)

        # Ensure first non-comment non-blank line starts with FROM
        has_from = False
        for line in cleaned:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.upper().startswith('FROM'):
                has_from = True
            break

        if not has_from:
            cleaned.insert(0, 'FROM python:3.11-slim')

        return '\n'.join(cleaned)

    @staticmethod
    def _sanitize_pydantic_schema(content: str) -> str:
        """Sanitize Python schema content to use Pydantic V2 field_validator.

        Converts deprecated Pydantic V1 @validator decorator to V2 @field_validator.
        This handles cases where LLM generates old V1 patterns despite instructions.
        
        Args:
            content: Python schema file content
            
        Returns:
            Sanitized content with V2 validators
        """
        if not content or not isinstance(content, str):
            return content

        # Check if file uses @validator (Pydantic V1)
        if '@validator' not in content:
            return content  # Already using V2 or no validators
            
        logger.warning("Detected Pydantic V1 @validator usage, converting to V2 @field_validator")
        
        # Replace import statement
        # from pydantic import ... validator ... -> ... field_validator ...
        # Match validator not preceded by field_ to avoid double replacement
        content = re.sub(
            r'from pydantic import (.*)(?<!field_)validator(.*)',
            r'from pydantic import \1field_validator\2',
            content
        )
        
        # Replace @validator decorator with @field_validator
        # Only match @validator, not @field_validator
        content = re.sub(
            r'@validator\(',
            r'@field_validator(',
            content
        )
        
        # Second pass: Convert pre=True to mode='before'
        # Handle cases where pre=True has additional arguments after it
        # Pattern: @field_validator('field', pre=True, other_arg=value) -> @field_validator('field', mode='before', other_arg=value)
        content = re.sub(
            r"@field_validator\(([^,\)]+),\s*pre=True\s*,",
            r"@field_validator(\1, mode='before',",
            content
        )
        
        # Handle case where pre=True is the last/only argument after field name
        content = re.sub(
            r"@field_validator\(([^,\)]+),\s*pre=True\s*\)",
            r"@field_validator(\1, mode='before')",
            content
        )
        
        return content

    async def _run_docgen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute documentation generation with timeout."""
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('docgen', False) or self._docgen_class is None:
            error_msg = "Docgen agent not available"
            logger.warning(f"Docgen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Docgen agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            # Wrap docgen with configurable timeout
            async with asyncio.timeout(DEFAULT_DOCGEN_TIMEOUT):
                code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
                doc_type = payload.get("doc_type", "api")
                format = payload.get("format", "markdown")
                
                repo_path = Path(code_path)
                if not repo_path.exists():
                    logger.warning(f"Code path {code_path} does not exist for job {job_id}")
                    return {
                        "status": "error",
                        "message": f"Code path {code_path} does not exist",
                    }
                
                logger.info(f"Running docgen agent for job {job_id} with doc_type: {doc_type}, format: {format}")
                
                # Initialize docgen agent
                agent = self._docgen_class(repo_path=str(repo_path))
                
                # Gather target files from code_path
                target_files = []
                for file_path in repo_path.rglob("*.py"):
                    if not any(part.startswith('.') for part in file_path.parts):
                        # [FIX] Add error handling for path resolution
                        try:
                            target_files.append(str(file_path.resolve().relative_to(repo_path.resolve())))
                        except ValueError as e:
                            logger.warning(f"[DOCGEN] File {file_path} is outside repo_path {repo_path}, skipping. Error: {e}")
                            continue
                
                if not target_files:
                    logger.warning(f"No Python files found in {code_path} for documentation generation")
                    target_files = ["README.md"]  # Fallback to generating a README
                
                # Run documentation generation
                result_data = await agent.generate_documentation(
                    target_files=target_files,
                    doc_type=doc_type,
                    instructions=payload.get("instructions"),
                    stream=False,
                )
                
                # Extract generated documentation
                generated_docs = []
                docs_output = result_data.get("documentation", "")
                
                # Write documentation to file
                output_dir = repo_path / "docs"
                output_dir.mkdir(parents=True, exist_ok=True)
                
                # Determine filename based on doc_type
                if doc_type.lower() in ["api", "api_reference"]:
                    doc_filename = "API.md"
                elif doc_type.lower() in ["readme", "user"]:
                    doc_filename = "README.md"
                elif doc_type.lower() in ["developer", "dev"]:
                    doc_filename = "DEVELOPER.md"
                else:
                    doc_filename = f"{doc_type}.md"
                
                doc_path = output_dir / doc_filename
                
                # ✅ INDUSTRY STANDARD: Robust serialization with comprehensive type handling
                # Supports multiple response formats from documentation agents:
                # - Structured dict with 'content' or 'markdown' fields
                # - Raw string content
                # - Complex nested structures (serialized as JSON with metadata)
                
                start_write_time = time.time()
                output_strategy = "unknown"
                content_to_write = ""
                
                try:
                    if isinstance(docs_output, dict):
                        # Structured response - extract content intelligently
                        if 'content' in docs_output:
                            # Primary content field (standard convention)
                            content_to_write = docs_output['content']
                            output_strategy = "dict_content_field"
                            
                            # Validate content is string
                            if not isinstance(content_to_write, str):
                                logger.warning(
                                    "Documentation content field is not a string, converting",
                                    extra={
                                        "job_id": job_id,
                                        "content_type": type(content_to_write).__name__,
                                        "doc_type": doc_type
                                    }
                                )
                                content_to_write = str(content_to_write)
                                
                        elif 'markdown' in docs_output:
                            # Alternative markdown field (some agents use this)
                            content_to_write = docs_output['markdown']
                            output_strategy = "dict_markdown_field"
                            
                            if not isinstance(content_to_write, str):
                                logger.warning(
                                    "Documentation markdown field is not a string, converting",
                                    extra={
                                        "job_id": job_id,
                                        "markdown_type": type(content_to_write).__name__,
                                        "doc_type": doc_type
                                    }
                                )
                                content_to_write = str(content_to_write)
                                
                        elif 'text' in docs_output:
                            # Some agents may use 'text' field
                            content_to_write = str(docs_output['text'])
                            output_strategy = "dict_text_field"
                            
                        else:
                            # Unstructured dict - serialize as formatted JSON with metadata
                            output_strategy = "dict_json_serialization"
                            
                            # Add metadata header for clarity
                            metadata = {
                                "generated_by": "docgen_agent",
                                "job_id": job_id,
                                "doc_type": doc_type,
                                "timestamp": time.time(),
                                "note": "Content was returned as unstructured dictionary"
                            }
                            
                            serialized_output = {
                                "metadata": metadata,
                                "content": docs_output
                            }
                            
                            content_to_write = json.dumps(serialized_output, indent=2, ensure_ascii=False)
                            
                            logger.info(
                                "Serializing unstructured dict to JSON",
                                extra={
                                    "job_id": job_id,
                                    "dict_keys": list(docs_output.keys()),
                                    "doc_type": doc_type
                                }
                            )
                    else:
                        # Direct string or other type - convert to string
                        output_strategy = "direct_string"
                        content_to_write = str(docs_output)
                    
                    # Validate we have content to write
                    if not content_to_write:
                        logger.error(
                            "Documentation output is empty after processing",
                            extra={
                                "job_id": job_id,
                                "output_type": type(docs_output).__name__,
                                "output_strategy": output_strategy,
                                "doc_type": doc_type
                            }
                        )
                        raise ValueError("Documentation content is empty - refusing to write empty file")
                    
                    # Write with proper encoding
                    async with aiofiles.open(doc_path, "w", encoding="utf-8") as f:
                        await f.write(content_to_write)
                    
                    # Verify file was written successfully
                    if not doc_path.exists():
                        raise IOError(f"File {doc_path} was not created successfully")
                    
                    file_size = doc_path.stat().st_size
                    
                    # Comprehensive logging for observability
                    write_duration_ms = round((time.time() - start_write_time) * 1000, 2)
                    
                    logger.info(
                        "Documentation written successfully",
                        extra={
                            "job_id": job_id,
                            "doc_type": doc_type,
                            "doc_path": str(doc_path),
                            "output_type": type(docs_output).__name__,
                            "output_strategy": output_strategy,
                            "file_size_bytes": file_size,
                            "content_length": len(content_to_write),
                            "write_duration_ms": write_duration_ms,
                            "has_content": bool(content_to_write)
                        }
                    )
                    
                except Exception as e:
                    logger.error(
                        "Failed to write documentation file",
                        extra={
                            "job_id": job_id,
                            "doc_type": doc_type,
                            "doc_path": str(doc_path),
                            "output_type": type(docs_output).__name__,
                            "output_strategy": output_strategy,
                            "error": str(e),
                            "error_type": type(e).__name__
                        },
                        exc_info=True
                    )
                    raise
                
                # [FIX] Add error handling for path resolution
                try:
                    generated_docs.append(str(doc_path.resolve().relative_to(repo_path.resolve())))
                except ValueError as e:
                    logger.warning(f"[DOCGEN] Doc path {doc_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                    generated_docs.append(str(doc_path))
                logger.info(f"Generated documentation file: {doc_path}")
                
                # Issue 5c: Run sphinx-build to create docs/_build/html/ so that
                # final validation checks can locate the generated HTML documentation.
                sphinx_html_dir = output_dir / "_build" / "html"
                # Ensure a minimal conf.py exists so sphinx-build doesn't exit with code 2
                conf_py = output_dir / "conf.py"
                if not conf_py.exists():
                    try:
                        conf_py.write_text(
                            "# Minimal Sphinx conf.py - auto-generated\n"
                            "project = 'Generated Project'\n"
                            "extensions = []\n"
                            "html_theme = 'alabaster'\n",
                            encoding="utf-8",
                        )
                        logger.info("[DOCGEN] Created minimal docs/conf.py for job %s", job_id)
                    except OSError as conf_err:
                        logger.warning("[DOCGEN] Could not create conf.py: %s", conf_err)
                # Ensure a minimal index.rst exists; without it sphinx-build finds
                # 0 source files and exits with code 2.
                index_rst = output_dir / "index.rst"
                if not index_rst.exists():
                    try:
                        # Collect any .rst files already in the docs directory to
                        # include in the toctree (excluding index.rst itself).
                        rst_entries = []
                        for _rst_file in sorted(output_dir.glob("*.rst")):
                            if _rst_file.name != "index.rst":
                                rst_entries.append(f"   {_rst_file.stem}")
                        # Only add README.rst to the toctree — Sphinx reads .rst by default.
                        # README.md requires the myst-parser extension which may not be
                        # present; omit it to avoid a "document not found" build error.
                        _readme_rst = output_dir.parent / "README.rst"
                        if _readme_rst.exists() and "README" not in rst_entries:
                            rst_entries.append("   README")
                        toctree_body = "\n".join(rst_entries) if rst_entries else ""
                        index_rst.write_text(
                            "Welcome to Project Documentation\n"
                            "=================================\n\n"
                            ".. toctree::\n"
                            "   :maxdepth: 2\n"
                            "   :caption: Contents:\n\n"
                            f"{toctree_body}\n\n"
                            "Indices and tables\n"
                            "==================\n\n"
                            "* :ref:`genindex`\n"
                            "* :ref:`modindex`\n"
                            "* :ref:`search`\n",
                            encoding="utf-8",
                        )
                        logger.info("[DOCGEN] Created minimal docs/index.rst for job %s", job_id)
                    except OSError as idx_err:
                        logger.warning("[DOCGEN] Could not create index.rst: %s", idx_err)
                try:
                    sphinx_result = await asyncio.create_subprocess_exec(
                        "sphinx-build",
                        "-b", "html",
                        "-E",           # rebuild without cached environment
                        "-q",           # quiet mode
                        str(output_dir),
                        str(sphinx_html_dir),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    sphinx_stdout, sphinx_stderr = await asyncio.wait_for(
                        sphinx_result.communicate(), timeout=60
                    )
                    if sphinx_result.returncode == 0:
                        logger.info(
                            f"[DOCGEN] sphinx-build succeeded for job {job_id}. "
                            f"HTML docs at: {sphinx_html_dir}",
                            extra={"job_id": job_id}
                        )
                        # Record sphinx build output path
                        if sphinx_html_dir.exists():
                            generated_docs.append(str(sphinx_html_dir.relative_to(repo_path)))
                    else:
                        logger.warning(
                            f"[DOCGEN] sphinx-build exited with code {sphinx_result.returncode} for job {job_id}. "
                            f"stderr: {sphinx_stderr.decode('utf-8', errors='replace')[:500]}",
                            extra={"job_id": job_id}
                        )
                except FileNotFoundError:
                    logger.info(
                        "[DOCGEN] sphinx-build not found in PATH — skipping Sphinx HTML build. "
                        "Install sphinx (pip install sphinx) to enable HTML documentation generation.",
                        extra={"job_id": job_id}
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[DOCGEN] sphinx-build timed out after 60 seconds — skipping.",
                        extra={"job_id": job_id}
                    )
                except Exception as sphinx_err:
                    logger.warning(
                        f"[DOCGEN] sphinx-build failed (non-fatal): {sphinx_err}",
                        extra={"job_id": job_id}
                    )

                result = {
                    "status": "completed",
                    "generated_docs": generated_docs,
                    "doc_type": doc_type,
                    "format": format,
                    "file_count": len(target_files),
                }
                
                logger.info(f"Docgen agent completed for job {job_id}, generated {len(generated_docs)} files")
                return result
        
        except asyncio.TimeoutError:
            logger.warning(f"[DOCGEN] Job {job_id} timed out after 90s - skipping documentation")
            return {
                "status": "error",
                "message": "Documentation generation timed out after 90 seconds",
                "timeout": True,
            }
        except Exception as e:
            logger.error(f"Error running docgen agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_critique(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute critique/security scanning with timeout."""
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('critique', False) or self._critique_class is None:
            error_msg = "Critique agent not available"
            logger.warning(f"Critique agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Critique agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            # Wrap critique with configurable timeout
            async with asyncio.timeout(DEFAULT_CRITIQUE_TIMEOUT):
                code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
                scan_types = payload.get("scan_types", ["security", "quality"])
                auto_fix = payload.get("auto_fix", False)
                
                repo_path = Path(code_path)
                if not repo_path.exists():
                    logger.warning(f"Code path {code_path} does not exist for job {job_id}")
                    return {
                        "status": "error",
                        "message": f"Code path {code_path} does not exist",
                    }
                
                logger.info(f"Running critique agent for job {job_id} with scan_types: {scan_types}, auto_fix: {auto_fix}")
                
                # Detect language
                detected_language = _detect_project_language(repo_path)
                logger.info(f"[CRITIQUE] Detected language: {detected_language}")
                
                # Get file patterns for the detected language
                file_patterns = LANGUAGE_FILE_EXTENSIONS.get(detected_language, ["*.py"])
                
                # Initialize critique agent
                agent = self._critique_class(repo_path=str(repo_path))
                
                # Gather code files from code_path (non-test files only)
                code_files = {}
                test_files = {}
                for pattern in file_patterns:
                    for file_path in repo_path.rglob(pattern):
                        if not any(part.startswith('.') for part in file_path.parts):
                            # [FIX] Add error handling for path resolution
                            try:
                                rel_path = str(file_path.resolve().relative_to(repo_path.resolve()))
                            except ValueError as e:
                                logger.warning(f"[CRITIQUE] File {file_path} is outside repo_path {repo_path}, skipping. Error: {e}")
                                continue
                            try:
                                content = file_path.read_text(encoding="utf-8")
                            except Exception as e:
                                logger.warning(f"Failed to read file {file_path}: {e}")
                                continue
                            if "/tests/" in rel_path or rel_path.startswith("tests/"):
                                test_files[rel_path] = content
                            else:
                                code_files[rel_path] = content
                
                if not code_files:
                    logger.warning(f"No source files found in {code_path} for critique (language: {detected_language})")
                    return {
                        "status": "completed",
                        "issues_found": 0,
                        "issues_fixed": 0,
                        "scan_types": scan_types,
                        "warning": f"No code files found to critique (language: {detected_language})",
                    }
                
                logger.info(
                    f"[CRITIQUE] Job {job_id} gathered {len(code_files)} code files and "
                    f"{len(test_files)} test files for critique"
                )

                # Run critique
                critique_result = await agent.run(
                    code_files=code_files,
                    test_files=test_files,  # FIX: Pass test files instead of empty dict
                    requirements={
                        "scan_types": scan_types, 
                        "auto_fix": auto_fix,
                        "test_failures": payload.get("test_results"),
                        "validation_failures": payload.get("validation_results"),
                        "stages_completed": payload.get("stages_completed", []),
                        "stages_failed": payload.get("stages_failed", []),
                    },
                )
                
                # Extract results with type checking
                issues_found = len(critique_result.get("issues", []))
                
                # FIX: Handle both list and boolean return types for fixes_applied
                # Some code paths in critique_agent return boolean, others return list
                fixes_applied_raw = critique_result.get("fixes_applied", [])
                if isinstance(fixes_applied_raw, bool):
                    # Boolean indicates whether fixes were applied (True/False)
                    issues_fixed = 1 if fixes_applied_raw else 0
                elif isinstance(fixes_applied_raw, list):
                    # List contains the actual fixes that were applied
                    issues_fixed = len(fixes_applied_raw)
                else:
                    # Defensive fallback for unexpected types
                    logger.warning(
                        f"Unexpected type for fixes_applied: {type(fixes_applied_raw)}. Defaulting to 0."
                    )
                    issues_fixed = 0
                
                # FIX Problem 1C: Write fixed code files back to disk
                if auto_fix and issues_fixed > 0 and "code_files" in critique_result:
                    logger.info(f"[CRITIQUE] Job {job_id} writing {len(critique_result['code_files'])} fixed files back to disk")
                    fixed_files = critique_result["code_files"]
                    for file_path, file_content in fixed_files.items():
                        try:
                            # Ensure the file path is relative to repo_path
                            if Path(file_path).is_absolute():
                                full_path = Path(file_path)
                            else:
                                full_path = repo_path / file_path
                            
                            # Ensure parent directories exist
                            full_path.parent.mkdir(parents=True, exist_ok=True)
                            
                            # Write the fixed file
                            async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                                await f.write(file_content)
                            
                            logger.info(f"[CRITIQUE] Job {job_id} wrote fixed file: {full_path}")
                        except Exception as write_err:
                            logger.error(
                                f"[CRITIQUE] Job {job_id} failed to write fixed file {file_path}: {write_err}",
                                exc_info=True
                            )

                # Invalidate cached SFE analysis report so next detect_errors re-analyzes
                _invalidate_sfe_analysis_cache(repo_path, job_id)

                # Write critique report
                output_dir = repo_path / "reports"
                output_dir.mkdir(parents=True, exist_ok=True)
                
                # Verify directory was created successfully
                if not output_dir.exists():
                    logger.error(f"Failed to create reports directory: {output_dir}")
                    raise RuntimeError(f"Could not create reports directory: {output_dir}")
                
                report_path = output_dir / "critique_report.json"
                
                # FIX: Enhance critique report to include coverage and test results
                # This ensures the report complies with the contract requirements
                enhanced_report = {
                    "job_id": job_id,
                    "timestamp": critique_result.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    "coverage": critique_result.get("coverage", {
                        "total_lines": 0,
                        "covered_lines": 0,
                        "percentage": 0.0
                    }),
                    "test_results": critique_result.get("test_results") or payload.get("test_results", {
                        "total": 0,
                        "passed": 0,
                        "failed": 0
                    }),
                    "issues": critique_result.get("issues", []),
                    "fixes_applied": critique_result.get("fixes_applied", []),
                    "scan_types": scan_types,
                    "status": critique_result.get("status", "completed"),
                }
                
                # Add original critique_result fields that don't conflict
                for key, value in critique_result.items():
                    if key not in enhanced_report:
                        enhanced_report[key] = value
                
                # Ensure enhanced_report is serializable
                try:
                    json_str = json.dumps(enhanced_report, indent=2, default=str)
                except (TypeError, ValueError) as e:
                    logger.error(f"Critique result is not JSON serializable: {e}")
                    # Create a minimal valid report
                    json_str = json.dumps({
                        "job_id": job_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "error",
                        "message": "Failed to serialize critique results",
                        "issues_found": len(critique_result.get("issues", [])),
                        "coverage": {"total_lines": 0, "covered_lines": 0, "percentage": 0.0},
                        "test_results": {"total": 0, "passed": 0, "failed": 0},
                        "issues": [],
                        "fixes_applied": [],
                        "error": str(e)
                    }, indent=2)
                
                async with aiofiles.open(report_path, "w", encoding="utf-8") as f:
                    await f.write(json_str)
                
                # Verify file was written successfully
                if not report_path.exists():
                    logger.error(f"Critique report file was not created: {report_path}")
                else:
                    file_size = report_path.stat().st_size
                    logger.info(f"Generated critique report: {report_path} ({file_size} bytes)")
                
                # [FIX] Add error handling for path resolution
                try:
                    report_path_str = str(report_path.resolve().relative_to(repo_path.resolve()))
                except ValueError as e:
                    logger.warning(f"[CRITIQUE] Report path {report_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                    report_path_str = str(report_path)
                
                result = {
                    "status": "completed",
                    "issues_found": issues_found,
                    "issues_fixed": issues_fixed,
                    "scan_types": scan_types,
                    "report_path": report_path_str,
                    "file_count": len(code_files),
                }
                
                logger.info(f"Critique agent completed for job {job_id}, found {issues_found} issues")
                return result
        
        except asyncio.TimeoutError:
            logger.warning(f"[CRITIQUE] Job {job_id} timed out after 90s - skipping critique")
            return {
                "status": "error",
                "message": "Code critique timed out after 90 seconds",
                "timeout": True,
            }
        except Exception as e:
            logger.error(f"Error running critique agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_sfe_analysis(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Self-Fixing Engineer analysis with CodebaseAnalyzer and BugManager."""
        logger.info(f"[SFE_ANALYSIS] Starting analysis for job {job_id}")
        
        try:
            # Lazy import SFE components to avoid circular dependencies
            from self_fixing_engineer.arbiter.codebase_analyzer import CodebaseAnalyzer
        except ImportError as e:
            logger.warning(f"[SFE_ANALYSIS] CodebaseAnalyzer not available for job {job_id}: {e}")
            return {
                "status": "skipped",
                "message": f"CodebaseAnalyzer not available: {e}",
                "job_id": job_id,
            }
        
        # Attempt to reuse a shared PostgresClient so CodebaseAnalyzer does not open a
        # second connection to the same Railway PostgreSQL instance (Bug 7 fix).
        # The client is cached on the service instance after the first successful connection.
        # If the cached pool has since been closed (e.g. idle timeout), reset it so the
        # code below creates a fresh one.
        _shared_db_client = getattr(self, "_sfe_db_client", None)
        if _shared_db_client is not None:
            _pool = getattr(_shared_db_client, "_pool", None)
            if _pool is None or (hasattr(_pool, "is_closed") and _pool.is_closed()):
                logger.info("[SFE_ANALYSIS] Cached PostgresClient pool is closed — reconnecting.")
                self._sfe_db_client = None
                _shared_db_client = None

        if _shared_db_client is None:
            try:
                from self_fixing_engineer.arbiter.models.postgres_client import PostgresClient as _PGClient
                _db_url = os.environ.get("DATABASE_URL")
                if _db_url:
                    _shared_db_client = _PGClient(_db_url)
                    await _shared_db_client.connect()
                    self._sfe_db_client = _shared_db_client
                    logger.info("[SFE_ANALYSIS] Shared PostgresClient connected for SFE analysis.")
            except Exception as _db_err:
                logger.warning(
                    "[SFE_ANALYSIS] Could not create shared PostgresClient (%s); "
                    "CodebaseAnalyzer will create its own connection.",
                    _db_err,
                )
                _shared_db_client = None

        try:
            # Wrap analysis with configurable timeout
            async with asyncio.timeout(DEFAULT_SFE_ANALYSIS_TIMEOUT):
                code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
                code_path_obj = Path(code_path)
                
                if not code_path_obj.exists():
                    logger.warning(f"[SFE_ANALYSIS] Code path {code_path} does not exist for job {job_id}")
                    return {
                        "status": "error",
                        "message": f"Code path {code_path} does not exist",
                    }
                
                logger.info(f"[SFE_ANALYSIS] Running CodebaseAnalyzer for job {job_id} on {code_path}")
                
                # Use CodebaseAnalyzer as async context manager
                # Don't ignore tests - we want to analyze both code AND test files
                # Pass shared db client to avoid opening a duplicate PostgreSQL connection (Bug 7).
                async with CodebaseAnalyzer(
                    root_dir=str(code_path_obj),
                    ignore_patterns=["__pycache__", ".git", "*.pyc", "*.egg-info"],
                    external_db_client=_shared_db_client,
                ) as analyzer:
                    # First, do a quick scan to get overall summary
                    summary = await analyzer.scan_codebase(str(code_path_obj))
                    
                    # Then, perform deeper analysis by discovering and analyzing each Python file
                    logger.info(f"[SFE_ANALYSIS] Discovering Python files for deeper analysis in {code_path}")
                    py_files = await analyzer.discover_files_async()
                    logger.info(f"[SFE_ANALYSIS] Found {len(py_files)} Python files to analyze")
                    
                    # Collect issues from both scan and deeper analysis
                    all_issues = []
                    
                    # Get defects from initial scan
                    defects = summary.get("defects", [])
                    all_issues.extend(defects)
                    
                    # Perform deeper analysis on each file (limit to avoid timeout)
                    max_files_to_analyze = MAX_SFE_FILES_TO_ANALYZE
                    files_analyzed = 0
                    for py_file in py_files[:max_files_to_analyze]:
                        try:
                            file_issues = await analyzer.analyze_and_propose(py_file)
                            files_analyzed += 1
                            if file_issues:
                                all_issues.extend(file_issues)
                        except Exception as e:
                            logger.warning(f"[SFE_ANALYSIS] Error analyzing {py_file}: {e}")
                            continue
                    
                    logger.info(
                        f"[SFE_ANALYSIS] Completed deeper analysis on {files_analyzed}/{len(py_files)} files"
                    )
                    
                    # Filter out defects for non-existent files and deduplicate
                    valid_defects = []
                    seen_issues = set()
                    for defect in all_issues:
                        defect_file = defect.get("file", "")
                        if defect_file:
                            defect_path = Path(defect_file)
                            if not defect_path.is_absolute():
                                defect_path = code_path_obj / defect_path
                            
                            # Create unique key for deduplication
                            issue_key = (
                                str(defect_path),
                                defect.get("line", 0),
                                defect.get("type", ""),
                                defect.get("message", "")
                            )
                            
                            if defect_path.exists() and issue_key not in seen_issues:
                                valid_defects.append(defect)
                                seen_issues.add(issue_key)
                            elif not defect_path.exists():
                                logger.debug(f"[SFE_ANALYSIS] Skipping defect for non-existent file: {defect_file}")
                    
                    issues_found = len(valid_defects)
                    logger.info(f"[SFE_ANALYSIS] Found {issues_found} total issues for job {job_id}")
                    
                    # Filter for critical and high severity issues
                    critical_high_issues = [
                        d for d in valid_defects 
                        if d.get("severity", "").lower() in ["critical", "high"]
                    ]
                    
                    issues_fixed = 0
                    remediation_results = []

                    # ------------------------------------------------------------------
                    # SFE auto-fix: apply fixes for safe, auto-fixable categories.
                    # Gated by SFE_AUTO_FIX_ENABLED env var (default: true).
                    # Auto-fixable categories are those with a high success rate and
                    # low risk of introducing regressions (style/import issues).
                    # ------------------------------------------------------------------
                    _SFE_AUTO_FIX_FALSY = frozenset(("false", "0", "no", "off", "disabled"))
                    _sfe_auto_fix_enabled = (
                        os.environ.get("SFE_AUTO_FIX_ENABLED", "true").lower()
                        not in _SFE_AUTO_FIX_FALSY
                    )
                    _AUTO_FIXABLE_TYPES = frozenset(
                        {"unused_import", "missing_all", "import_order", "type_annotation"}
                    )
                    if _sfe_auto_fix_enabled and valid_defects:
                        _auto_fixable = [
                            d for d in valid_defects
                            if d.get("type", "").lower() in _AUTO_FIXABLE_TYPES
                            or any(
                                kw in d.get("message", "").lower()
                                for kw in ("unused import", "missing __all__")
                            )
                        ]
                        if _auto_fixable:
                            logger.info(
                                f"[SFE_ANALYSIS] Attempting auto-fix for {len(_auto_fixable)} "
                                f"auto-fixable issues (job {job_id})"
                            )
                            try:
                                from server.services.sfe_service import SFEService
                                _sfe_svc = SFEService()
                                # Populate errors cache so propose_fix can look up issues
                                _sfe_svc._populate_errors_cache(_auto_fixable, job_id)
                                for _issue in _auto_fixable:
                                    _err_id = _issue.get("error_id")
                                    if not _err_id:
                                        continue
                                    try:
                                        _fix_proposal = await _sfe_svc.propose_fix(_err_id)
                                        if _fix_proposal.get("confidence", 0.0) >= 0.7:
                                            _fix_id = _fix_proposal.get("fix_id")
                                            if _fix_id:
                                                _apply_result = await _sfe_svc.apply_fix(_fix_id)
                                                if _apply_result.get("applied"):
                                                    issues_fixed += 1
                                                    remediation_results.append({
                                                        "status": "fixed",
                                                        "error_id": _err_id,
                                                        "fix_id": _fix_id,
                                                        "files_modified": _apply_result.get(
                                                            "files_modified", []
                                                        ),
                                                    })
                                    except Exception as _fix_err:
                                        logger.debug(
                                            f"[SFE_ANALYSIS] Auto-fix skipped for {_err_id}: {_fix_err}"
                                        )
                                if issues_fixed:
                                    logger.info(
                                        f"[SFE_ANALYSIS] Auto-fixed {issues_fixed} issues for job {job_id}"
                                    )
                            except Exception as _sfe_auto_err:
                                logger.warning(
                                    f"[SFE_ANALYSIS] SFE auto-fix step failed (non-fatal): {_sfe_auto_err}"
                                )

                    # If critical/high severity issues found, attempt auto-remediation using BugManager
                    # Note: BugManager might not have detect_errors method, so we handle gracefully
                    if critical_high_issues:
                        logger.info(
                            f"[SFE_ANALYSIS] Found {len(critical_high_issues)} critical/high severity issues, "
                            f"attempting auto-remediation for job {job_id}"
                        )
                        
                        try:
                            from self_fixing_engineer.arbiter.bug_manager import BugManager
                            
                            # BugManager requires Settings - try to create with defaults
                            try:
                                from self_fixing_engineer.arbiter.bug_manager.utils import Settings
                                settings = Settings()
                                bug_manager = BugManager(settings)
                                
                                # Check if BugManager has an async initialization method
                                # Some versions may require explicit initialization
                                if hasattr(bug_manager, '_initialize') and asyncio.iscoroutinefunction(bug_manager._initialize):
                                    await bug_manager._initialize()
                                
                                # BugManager uses report() method, not detect_errors
                                # For now, just log that we would attempt remediation
                                logger.info(
                                    f"[SFE_ANALYSIS] BugManager initialized for job {job_id}, "
                                    f"would attempt remediation for {len(critical_high_issues)} issues"
                                )
                                
                                # In the future, could iterate through critical_high_issues
                                # and call bug_manager.report() for each one
                                # For now, just mark them as detected
                                for issue in critical_high_issues[:5]:  # Limit to first 5 to avoid overwhelming
                                    remediation_results.append({
                                        "status": "detected",
                                        "issue": issue,
                                        "message": "Issue detected, remediation would be applied here"
                                    })
                                
                            except ImportError as settings_err:
                                logger.warning(
                                    f"[SFE_ANALYSIS] Could not import Settings for BugManager: {settings_err}",
                                    extra={"job_id": job_id}
                                )
                        except ImportError as bug_mgr_import_err:
                            logger.warning(
                                f"[SFE_ANALYSIS] BugManager not available: {bug_mgr_import_err}",
                                extra={"job_id": job_id}
                            )
                        except Exception as bug_mgr_err:
                            logger.warning(
                                f"[SFE_ANALYSIS] BugManager error for job {job_id}: {bug_mgr_err}",
                                exc_info=True
                            )
                    
                    # Write JSON report
                    reports_dir = code_path_obj / "reports"
                    reports_dir.mkdir(parents=True, exist_ok=True)
                    report_path = reports_dir / "sfe_analysis_report.json"
                    
                    report_data = {
                        "job_id": job_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "code_path": str(code_path_obj),
                        "files_analyzed": files_analyzed,
                        "total_python_files": len(py_files),
                        "issues_found": issues_found,
                        "issues_fixed": issues_fixed,
                        "critical_high_count": len(critical_high_issues),
                        "all_defects": valid_defects,
                        "critical_high_defects": critical_high_issues,
                        "remediation_results": remediation_results,
                        "summary": summary,
                        "source": "CodebaseAnalyzer",
                    }
                    
                    async with aiofiles.open(report_path, "w", encoding="utf-8") as f:
                        await f.write(json.dumps(report_data, indent=2))
                    
                    logger.info(
                        f"[SFE_ANALYSIS] Wrote analysis report to {report_path}",
                        extra={
                            "job_id": job_id,
                            "report_path": str(report_path),
                            "file_size": report_path.stat().st_size
                        }
                    )
                    
                    # Structured logging
                    logger.info(
                        f"[PIPELINE] Job {job_id} completed step: sfe_analysis - "
                        f"analyzed {files_analyzed} files, found {issues_found} issues, fixed {issues_fixed}",
                        extra={
                            "job_id": job_id,
                            "stage": "sfe_analysis",
                            "files_analyzed": files_analyzed,
                            "total_python_files": len(py_files),
                            "issues_found": issues_found,
                            "issues_fixed": issues_fixed,
                            "critical_high_count": len(critical_high_issues)
                        }
                    )
                    
                    return {
                        "status": "completed",
                        "job_id": job_id,
                        "code_path": str(code_path_obj),
                        "files_analyzed": files_analyzed,
                        "total_python_files": len(py_files),
                        "issues_found": issues_found,
                        "issues_fixed": issues_fixed,
                        "critical_high_count": len(critical_high_issues),
                        "report_path": str(report_path),
                        "total_files": summary.get("files", 0),
                    }
        
        except asyncio.TimeoutError:
            logger.warning(f"[SFE_ANALYSIS] Analysis timed out after {DEFAULT_SFE_ANALYSIS_TIMEOUT}s for job {job_id}")
            return {
                "status": "error",
                "message": f"SFE analysis timed out after {DEFAULT_SFE_ANALYSIS_TIMEOUT} seconds",
                "timeout": True,
                "job_id": job_id,
            }
        except ImportError as e:
            logger.warning(f"[SFE_ANALYSIS] Import error for job {job_id}: {e}")
            return {
                "status": "skipped",
                "message": f"SFE components not available: {e}",
                "job_id": job_id,
            }
        except Exception as e:
            logger.error(f"[SFE_ANALYSIS] Error for job {job_id}: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_clarifier(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute requirements clarification using LLM-based or rule-based approach.
        
        Uses the Clarifier class which auto-detects available LLM providers
        (OpenAI, Anthropic, xAI, Google, Ollama) via the central runner/llm_client.py.
        Falls back to rule-based clarification if no LLM is available.
        
        Args:
            job_id: Job identifier
            payload: Parameters including readme_content, ambiguities, channel
        
        Returns:
            Dict with status and clarification questions
        """
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        try:
            readme_content = payload.get("readme_content", "")
            channel = payload.get("channel", "cli")  # Default to CLI if not specified
            
            logger.info(f"Running clarifier for job {job_id} with channel: {channel}")
            
            if not readme_content:
                return {
                    "status": "error",
                    "message": "No README content provided for clarification",
                }
            
            # Try LLM-based clarification first (with auto-detection)
            if self.agents_available.get("clarifier"):
                logger.info(f"Running LLM-based clarifier for job {job_id}")
                try:
                    from generator.clarifier.clarifier import Clarifier
                    from generator.clarifier.clarifier_user_prompt import get_channel
                    
                    # Create clarifier instance with auto-detection
                    clarifier = await Clarifier.create()
                    
                    # Override interaction channel if specified
                    try:
                        target_lang = getattr(getattr(clarifier, 'config', None), 'TARGET_LANGUAGE', 'en')
                        clarifier.interaction = get_channel(
                            channel_type=channel,
                            target_language=target_lang
                        )
                        logger.info(f"Set clarifier channel to: {channel}")
                    except Exception as channel_error:
                        logger.warning(
                            f"Could not set channel to {channel}: {channel_error}. "
                            f"Using default channel.",
                            exc_info=True
                        )
                    
                    # Check if LLM is actually available (not just rule-based fallback)
                    has_llm = hasattr(clarifier, 'llm') and clarifier.llm is not None
                    
                    if has_llm:
                        # Try to detect ambiguities using LLM
                        try:
                            detected_ambiguities = await clarifier.detect_ambiguities(readme_content)
                            # Generate questions based on detected ambiguities
                            # Bug 3 Fix: Pass readme_content for context
                            questions = await clarifier.generate_questions(detected_ambiguities, readme_content)
                            
                            logger.info(
                                f"LLM-based clarifier generated {len(questions)} questions for job {job_id}",
                                extra={"method": "llm", "questions_count": len(questions), "channel": channel}
                            )
                            
                            # Store session
                            _clarification_sessions[job_id] = {
                                "job_id": job_id,
                                "requirements": readme_content,
                                "questions": questions,
                                "answers": {},
                                "status": "in_progress",
                                "created_at": datetime.now().isoformat(),
                                "method": "llm",
                                "channel": channel,
                            }
                            
                            return {
                                "status": "clarification_initiated",
                                "job_id": job_id,
                                "clarifications": questions,
                                "confidence": 0.65,
                                "questions_count": len(questions),
                                "method": "llm",
                                "channel": channel,
                            }
                        except Exception as llm_error:
                            logger.warning(
                                f"LLM-based clarification failed: {llm_error}. "
                                "Falling back to rule-based.",
                                exc_info=True
                            )
                    else:
                        logger.info("No LLM configured, using rule-based clarification")
                    
                except ImportError as e:
                    logger.warning(f"Could not import Clarifier module: {e}. Using rule-based.")
                except Exception as e:
                    logger.warning(
                        f"Error initializing clarifier: {e}. Falling back to rule-based.",
                        exc_info=True
                    )
            
            # Fallback to rule-based clarification
            logger.info(f"Running rule-based clarifier for job {job_id}")
            questions = self._generate_clarification_questions(readme_content)
            
            # Store session
            _clarification_sessions[job_id] = {
                "job_id": job_id,
                "requirements": readme_content,
                "questions": questions,
                "answers": {},
                "status": "in_progress",
                "created_at": datetime.now().isoformat(),
                "method": "rule_based",
                "channel": channel,
            }
            
            result = {
                "status": "clarification_initiated",
                "job_id": job_id,
                "clarifications": questions,
                "confidence": 0.65,  # Low confidence indicates need for clarification
                "questions_count": len(questions),
                "method": "rule_based",
                "channel": channel,
            }
            
            logger.info(f"Clarifier completed for job {job_id} with {len(questions)} questions")
            return result
            
        except Exception as e:
            logger.error(f"Error running clarifier: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    def _generate_clarification_questions(self, requirements: str) -> List[Dict[str, str]]:
        """
        Generate clarification questions based on requirements content.
        This is a rule-based approach. In production, this would use LLM.
        Returns list of dicts with 'id', 'question', and 'category' keys.
        """
        questions = []
        req_lower = requirements.lower()
        question_counter = 1
        
        # Database questions - expanded keywords for Bug 4
        if any(word in req_lower for word in ['database', 'data', 'store', 'save', 'persist', 'storage', 'db']):
            # Expanded DB detection to include more variants
            if not any(db in req_lower for db in ['mysql', 'postgres', 'postgresql', 'mongodb', 'sqlite', 'redis', 'dynamodb', 'firestore', 'cassandra', 'mariadb']):
                questions.append({
                    "id": f"q{question_counter}",
                    "question": "What type of database would you like to use? (e.g., PostgreSQL, MongoDB, MySQL)",
                    "category": "database"
                })
                question_counter += 1
        
        # Authentication questions - expanded keywords
        if any(word in req_lower for word in ['user', 'login', 'auth', 'account', 'sign', 'authentication', 'credential']):
            if not any(auth in req_lower for auth in ['jwt', 'oauth', 'session', 'token', 'saml', 'auth0', 'cognito', 'firebase auth']):
                questions.append({
                    "id": f"q{question_counter}",
                    "question": "What authentication method should be used? (e.g., JWT, OAuth 2.0, session-based)",
                    "category": "authentication"
                })
                question_counter += 1
        
        # API questions - expanded keywords
        if any(word in req_lower for word in ['api', 'endpoint', 'rest', 'graphql', 'service']):
            if 'rest' not in req_lower and 'graphql' not in req_lower and 'grpc' not in req_lower:
                questions.append({
                    "id": f"q{question_counter}",
                    "question": "Should the API be RESTful or GraphQL?",
                    "category": "api"
                })
                question_counter += 1
        
        # Frontend questions - expanded keywords
        if any(word in req_lower for word in ['web', 'frontend', 'ui', 'interface', 'dashboard', 'client', 'browser']):
            if not any(fw in req_lower for fw in ['react', 'vue', 'angular', 'svelte', 'next', 'nextjs', 'nuxt', 'gatsby']):
                questions.append({
                    "id": f"q{question_counter}",
                    "question": "What frontend framework would you prefer? (e.g., React, Vue.js, Angular)",
                    "category": "frontend"
                })
                question_counter += 1
        
        # Deployment questions - expanded keywords
        if any(word in req_lower for word in ['deploy', 'host', 'production', 'server', 'cloud', 'infrastructure']):
            if not any(platform in req_lower for platform in ['docker', 'kubernetes', 'k8s', 'aws', 'azure', 'gcp', 'heroku', 'vercel', 'netlify']):
                questions.append({
                    "id": f"q{question_counter}",
                    "question": "What deployment platform will you use? (e.g., Docker, Kubernetes, AWS, Heroku)",
                    "category": "deployment"
                })
                question_counter += 1
        
        # Testing questions
        if 'test' in req_lower:
            if not any(test_type in req_lower for test_type in ['unit', 'integration', 'e2e', 'end-to-end', 'pytest', 'jest', 'mocha']):
                questions.append({
                    "id": f"q{question_counter}",
                    "question": "What types of tests should be included? (e.g., unit tests, integration tests, e2e tests)",
                    "category": "testing"
                })
                question_counter += 1
        
        # Performance questions
        if any(word in req_lower for word in ['performance', 'scale', 'load', 'concurrent']):
            questions.append({
                "id": f"q{question_counter}",
                "question": "What are your expected performance requirements? (e.g., number of concurrent users, response time SLAs)",
                "category": "performance"
            })
            question_counter += 1
        
        # Security questions
        if any(word in req_lower for word in ['secure', 'security', 'encrypt', 'protect']):
            if 'encrypt' not in req_lower:
                questions.append({
                    "id": f"q{question_counter}",
                    "question": "What security measures are required? (e.g., data encryption at rest/in transit, HTTPS, rate limiting)",
                    "category": "security"
                })
                question_counter += 1
        
        # Bug 2 Fix: Remove generic fallback - return empty list if no ambiguities detected
        # This allows the pipeline to proceed without unnecessary clarification
        
        return questions[:5]  # Limit to 5 questions max
    
    async def cleanup_expired_clarification_sessions(self, max_age_seconds: int = CLARIFICATION_SESSION_TTL_SECONDS) -> int:
        """
        Clean up clarification sessions older than max_age_seconds.
        
        Should be called periodically (e.g., every 10 minutes) to prevent memory exhaustion.
        
        Args:
            max_age_seconds: Maximum age in seconds before a session is considered expired
        
        Returns:
            Number of sessions cleaned up
        """
        now = datetime.now(timezone.utc)
        expired = []
        
        for job_id, session in _clarification_sessions.items():
            try:
                created_at_str = session.get("created_at", "")
                # Parse ISO format datetime (may or may not have timezone)
                if created_at_str:
                    try:
                        created_at = datetime.fromisoformat(created_at_str)
                        # If no timezone, assume UTC
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        
                        if (now - created_at).total_seconds() > max_age_seconds:
                            expired.append(job_id)
                    except (ValueError, TypeError):
                        # Invalid timestamp format - mark for cleanup
                        logger.warning(f"Invalid timestamp in session {job_id}: {created_at_str}")
                        expired.append(job_id)
                else:
                    # No timestamp - mark for cleanup
                    expired.append(job_id)
            except Exception as e:
                # Catch any unexpected errors when processing session
                logger.error(f"Error processing session {job_id}: {e}")
                expired.append(job_id)  # Mark for cleanup on error
        
        for job_id in expired:
            del _clarification_sessions[job_id]
            logger.info(f"Cleaned up expired clarification session for job {job_id}")
        
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired clarification sessions")
        
        return len(expired)
    
    async def start_periodic_session_cleanup(
        self,
        interval_seconds: int = 600,  # 10 minutes default
        max_age_seconds: int = CLARIFICATION_SESSION_TTL_SECONDS
    ) -> None:
        """
        Start a background task to periodically clean up expired clarification sessions.
        
        Args:
            interval_seconds: How often to run cleanup (default: 10 minutes)
            max_age_seconds: Maximum session age before cleanup (default: 1 hour)
        """
        logger.info(
            f"Starting periodic clarification session cleanup "
            f"(interval: {interval_seconds}s, max_age: {max_age_seconds}s)"
        )
        
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                cleaned = await self.cleanup_expired_clarification_sessions(max_age_seconds)
                if cleaned > 0:
                    logger.info(f"Periodic cleanup: removed {cleaned} expired sessions")
            except asyncio.CancelledError:
                logger.info("Periodic cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}", exc_info=True)
                # Continue running despite errors
    
    async def _run_full_pipeline(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute full generation pipeline."""
        # FIX: Check if job is already in pipeline
        if job_id in self._jobs_in_pipeline:
            logger.warning(
                f"[PIPELINE] Job {job_id} is already running in pipeline. Skipping duplicate request.",
                extra={"job_id": job_id}
            )
            return {
                "status": "skipped",
                "message": "Pipeline already running for this job",
                "job_id": job_id,
            }
        
        # Add job to in-progress set
        self._jobs_in_pipeline.add(job_id)
        logger.info(f"[PIPELINE] Starting pipeline for job {job_id}")
        
        # Initialize detected_language before try/finally so the finally block
        # always has a valid value even if codegen fails before assignment.
        detected_language: str = payload.get("language", "python") or "python"
        # Initialize codegen_result before try/finally so the finally block
        # always has a valid reference even if the pipeline pauses for clarification
        # before the codegen step is reached.
        codegen_result = None
        
        try:
            # Ensure agents are loaded before use
            self._ensure_agents_loaded()
            
            # ==========================================================================
            # [NEW] Spec-Driven Generation Integration
            # ==========================================================================
            # Process README through spec block parser and question loop if needed
            # This provides structured, validated specifications that override text extraction
            try:
                from generator.main.spec_integration import SpecDrivenPipeline
                
                if payload.get("readme_content"):
                    spec_pipeline = SpecDrivenPipeline(job_id=job_id)
                    
                    # Check if we should run question loop (only in interactive scenarios)
                    # For API/batch mode, run non-interactive
                    interactive = payload.get("interactive_spec", False)
                    
                    logger.info(
                        f"[PIPELINE] Processing spec for job {job_id}, interactive={interactive}",
                        extra={"job_id": job_id, "interactive": interactive}
                    )
                    
                    spec_lock = await spec_pipeline.process_requirements(
                        readme_content=payload["readme_content"],
                        interactive=interactive,
                        output_path=None  # Will be derived from spec
                    )
                    
                    # Inject spec_lock into payload for downstream use
                    payload["spec_lock"] = {
                        "project_type": spec_lock.project_type,
                        "package_name": spec_lock.package_name,
                        "module_name": spec_lock.module_name,
                        "output_dir": spec_lock.output_dir,
                        "interfaces": spec_lock.interfaces,
                        "dependencies": spec_lock.dependencies,
                        "nonfunctional": spec_lock.nonfunctional,
                        "adapters": spec_lock.adapters,
                        "acceptance_checks": spec_lock.acceptance_checks,
                    }
                    
                    # Override output_dir with spec value
                    if spec_lock.output_dir and not payload.get("output_dir"):
                        payload["output_dir"] = spec_lock.output_dir
                        logger.info(
                            f"[PIPELINE] Using spec output_dir: {spec_lock.output_dir}",
                            extra={"job_id": job_id, "output_dir": spec_lock.output_dir}
                        )
                    
                    logger.info(
                        f"[PIPELINE] Spec processing complete: "
                        f"type={spec_lock.project_type}, package={spec_lock.package_name}",
                        extra={
                            "job_id": job_id,
                            "project_type": spec_lock.project_type,
                            "package_name": spec_lock.package_name,
                        }
                    )
                    
                    # Inject spec-resolved fields into payload so codegen agent
                    # receives project_type and package_name from the prompt builder.
                    if spec_lock.project_type:
                        payload["project_type"] = spec_lock.project_type
                    if spec_lock.package_name or spec_lock.module_name:
                        payload["package_name"] = spec_lock.package_name or spec_lock.module_name
            except ImportError:
                logger.debug("[PIPELINE] Spec integration not available, using legacy flow")
            except Exception as e:
                logger.warning(
                    f"[PIPELINE] Spec processing failed, continuing with legacy flow: {e}",
                    exc_info=True
                )
            # ==========================================================================
            
            # Extract output_dir from README if not already set (legacy fallback)
            if not payload.get("output_dir") and payload.get("readme_content") and _PROVENANCE_AVAILABLE:
                extracted_output_dir = _extract_output_dir_from_md(payload["readme_content"])
                if extracted_output_dir:
                    payload["output_dir"] = extracted_output_dir
                    logger.info(
                        f"[PIPELINE] Extracted output_dir from README: {extracted_output_dir}",
                        extra={"job_id": job_id, "output_dir": extracted_output_dir}
                    )
            
            # Run pipeline stages sequentially
            stages_completed = []
            
            # Initialize result tracking for critique context
            testgen_result = None
            val_result = None
            
            # 1. Clarify (optional)
            if payload.get("skip_clarification", False):
                # Skip clarification when resuming after clarification is already completed
                logger.info(f"[PIPELINE] Skipping clarification for job {job_id} (already completed)")
            elif payload.get("readme_content"):
                logger.info(f"[PIPELINE] Job {job_id} starting step: clarify")
                clarify_result = await self._run_clarifier(job_id, payload)
                if clarify_result.get("status") == "clarification_initiated":
                    # Pause pipeline for clarifications
                    questions = clarify_result.get("clarifications", [])
                    # Update job status in database
                    job = jobs_db.get(job_id)
                    if job:
                        job.status = JobStatus.NEEDS_CLARIFICATION
                        job.current_stage = JobStage.GENERATOR_CLARIFICATION
                        job.updated_at = datetime.now(timezone.utc)
                        job.metadata["clarification_questions"] = questions
                        job.metadata["clarification_status"] = "pending_response"
                    logger.info(f"[PIPELINE] Job {job_id} awaiting clarification responses; pausing pipeline.")
                    # Remove from in-progress tracking to allow resumption
                    self._jobs_in_pipeline.discard(job_id)
                    return {"status": "clarification_initiated", "clarifications": questions, "job_id": job_id}
                elif clarify_result.get("status") != "error":
                    stages_completed.append("clarify")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: clarify")
            
            # 2. Codegen with retry logic
            # Transform payload for codegen - it needs 'requirements' not 'readme_content'
            # Preserve all original payload fields that might be needed
            raw_md = payload.get("readme_content", payload.get("requirements", ""))

            # Extract spec structure from README before codegen so the prompt
            # can be built with spec-derived directory requirements.
            spec_structure = None
            if raw_md and _PROVENANCE_AVAILABLE:
                try:
                    spec_structure = _extract_file_structure_from_md(raw_md)
                    logger.info(
                        "[PIPELINE] Extracted spec structure for job %s: %d dirs, %d files",
                        job_id,
                        len(spec_structure.get("directories", [])),
                        len(spec_structure.get("files", [])),
                        extra={"job_id": job_id},
                    )
                except Exception as _spec_struct_err:
                    logger.warning(
                        "[PIPELINE] Failed to extract spec structure for job %s: %s",
                        job_id,
                        _spec_struct_err,
                        extra={"job_id": job_id},
                    )

            codegen_payload = {
                **payload,  # Preserve all original fields
                "requirements": raw_md,
                "md_content": raw_md,  # Ensure codegen agent always has raw spec under md_content
                "spec_structure": spec_structure,  # Pass spec-derived structure requirements
            }
            # Remove readme_content from codegen payload as it's now in requirements
            codegen_payload.pop("readme_content", None)

            # Ensure requirements is populated before codegen
            # This handles the case where clarification was skipped and readme_content is empty
            if not codegen_payload.get("requirements") or len(codegen_payload.get("requirements", "").strip()) == 0:
                logger.warning(
                    f"[PIPELINE] Requirements is empty for job {job_id}. "
                    f"Attempting to load README from job directory."
                )
                # Try to read README from job directory
                job_dir = Path(self.storage_path) / job_id
                requirements = _load_readme_from_disk(job_dir)
                
                if requirements:
                    codegen_payload["requirements"] = requirements
                    logger.info(
                        f"[PIPELINE] Loaded requirements from job directory "
                        f"({len(requirements)} bytes) for job {job_id}"
                    )
                else:
                    error_msg = f"No requirements found: README file is missing from job directory {job_dir}"
                    logger.error(
                        f"[PIPELINE] {error_msg}",
                        extra={"job_id": job_id, "job_dir": str(job_dir)}
                    )
                    raise ValueError(error_msg)

            # FIX Failure 4: Pre-codegen spec check for frontend files
            # Extract required files from the MD content and check if any are frontend files
            # If so, enable include_frontend BEFORE codegen runs
            md_content_for_spec_check = codegen_payload.get("requirements", "")
            if md_content_for_spec_check and _PROVENANCE_AVAILABLE:
                try:
                    target_language = payload.get("language", "python").lower()
                    spec_required_files = set(_extract_required_files_from_md(md_content_for_spec_check, target_language=target_language))
                    spec_frontend_files = spec_required_files & FRONTEND_FILE_PATTERNS
                    
                    if spec_frontend_files:
                        logger.info(
                            f"[PIPELINE] Job {job_id} pre-codegen spec check: detected frontend files "
                            f"in spec: {spec_frontend_files}. Enabling frontend generation.",
                            extra={"job_id": job_id, "spec_frontend_files": list(spec_frontend_files)}
                        )
                        # Update job metadata to enable frontend
                        job = jobs_db.get(job_id)
                        if job:
                            job.metadata["include_frontend"] = True
                            if not job.metadata.get("frontend_type"):
                                # Default to jinja_templates for Python, vanilla_js for others
                                if target_language in ("python", "py"):
                                    job.metadata["frontend_type"] = "jinja_templates"
                                else:
                                    job.metadata["frontend_type"] = "vanilla_js"
                            logger.info(
                                f"[PIPELINE] Job {job_id} updated job metadata: include_frontend=True, "
                                f"frontend_type={job.metadata.get('frontend_type')}",
                                extra={"job_id": job_id}
                            )
                except Exception as spec_check_err:
                    logger.warning(
                        f"[PIPELINE] Job {job_id} pre-codegen spec check error: {spec_check_err}",
                        extra={"job_id": job_id}
                    )

            # Retry configuration
            max_codegen_retries = 2  # Total attempts = 1 initial + 2 retries = 3
            codegen_attempt = 0
            codegen_result = None
            previous_error = None

            while codegen_attempt <= max_codegen_retries:
                codegen_attempt += 1
                attempt_label = f"attempt {codegen_attempt}/{max_codegen_retries + 1}"

                logger.info(f"[PIPELINE] Job {job_id} starting step: codegen ({attempt_label})")

                # Add previous_error and retry_attempt to payload if retrying.
                # Including these in the payload changes the prompt content, which changes
                # the LLM cache key (sha256(prompt+model+provider)), busting any cached
                # bad response from prior attempts.
                if previous_error:
                    codegen_payload["previous_error"] = previous_error
                    codegen_payload["retry_attempt"] = codegen_attempt
                    logger.info(
                        f"[PIPELINE] Job {job_id} retrying codegen with error feedback: {previous_error.get('error_type')}",
                        extra={"job_id": job_id, "attempt": codegen_attempt, "previous_error": previous_error}
                    )

                codegen_timeout = PIPELINE_STEP_TIMEOUTS["codegen"]
                try:
                    codegen_result = await asyncio.wait_for(
                        self._run_codegen(job_id, codegen_payload),
                        timeout=codegen_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"[PIPELINE] Step 'codegen' timed out after {codegen_timeout}s for job {job_id}",
                        extra={"job_id": job_id, "timeout": codegen_timeout}
                    )
                    await self._finalize_failed_job(
                        job_id,
                        error=f"Code generation timed out after {codegen_timeout}s",
                    )
                    return {
                        "status": "failed",
                        "message": f"Code generation timed out after {codegen_timeout}s",
                        "stages_completed": stages_completed,
                        "job_id": job_id,
                    }

                if codegen_result.get("status") == "completed":
                    # Codegen succeeded - now validate before committing to success
                    output_path_for_validation = codegen_result.get("output_path")
                    
                    # Quick syntax validation to catch errors before exiting retry loop
                    # This allows us to retry codegen if validation fails
                    validation_passed = True
                    # Track the most recent validation errors for the ImportFixer guard below.
                    # Reset on each codegen attempt so stale errors don't carry over.
                    _last_val_errors: List[str] = []
                    if output_path_for_validation and _MATERIALIZER_AVAILABLE:
                        # Get target language early for all validation logic
                        target_lang = payload.get("language", "python").lower()
                        
                        try:
                            # Get required files list
                            md_content = payload.get("readme_content", payload.get("requirements", ""))
                            required_files = ["requirements.txt"]
                            spec_files = []
                            if md_content:
                                try:
                                    spec_files = _extract_required_files_from_md(md_content, target_language=target_lang) or []
                                    if spec_files:
                                        existing = set(required_files)
                                        required_files.extend(sf for sf in spec_files if sf not in existing)
                                except Exception:
                                    pass  # Ignore extraction errors
                            
                            # Run validation
                            val_result = await _validate_generated_project(
                                output_dir=output_path_for_validation,
                                required_files=required_files,
                                check_python_syntax=(target_lang in ("python", "py")),
                                language=target_lang,
                            )
                            
                            if not val_result.get("valid", True):
                                validation_errors = val_result.get('errors', [])
                                # Capture all validation errors for the ImportFixer guard.
                                # The guard uses these to decide whether to skip the fixer
                                # when all failures are from missing third-party packages.
                                _last_val_errors = validation_errors
                                
                                # Check for retriable errors (syntax errors, missing files, stub markers, or import errors)
                                syntax_errors = [e for e in validation_errors if 'syntax' in e.lower() or 'SyntaxError' in e]
                                missing_files = [e for e in validation_errors if 'missing' in e.lower() and 'required' in e.lower()]
                                import_errors = [e for e in validation_errors if 'does not import' in e.lower() or 'but does not import' in e.lower()]
                                stub_errors = [e for e in validation_errors if 'stub marker' in e.lower() or 'stub class' in e.lower()]
                                
                                # Inject pre-materialization import errors (Fix 4) into import_errors
                                _pme = codegen_result.get("pre_mat_import_errors", [])
                                if _pme:
                                    import_errors = import_errors + _pme
                                    logger.info(
                                        f"[PIPELINE] Job {job_id} injecting {len(_pme)} pre-materialization import error(s) into import_errors",
                                        extra={"job_id": job_id, "pre_mat_import_errors": _pme}
                                    )
                                
                                errors_for_retry = syntax_errors + import_errors + stub_errors
                                if missing_files:
                                    error_txt_path = Path(output_path_for_validation) / "error.txt"
                                    if error_txt_path.exists():
                                        logger.info(
                                            f"[PIPELINE] Job {job_id} has missing required files and error.txt",
                                            extra={"job_id": job_id, "missing_files": missing_files}
                                        )
                                        errors_for_retry.extend(missing_files)
                                
                                if errors_for_retry and codegen_attempt <= max_codegen_retries:
                                    # We have retriable errors and retries left - set up for retry
                                    validation_passed = False
                                    
                                    # Determine error type for better messaging
                                    if import_errors:
                                        error_type = "ImportError"
                                    elif syntax_errors:
                                        error_type = "SyntaxError"
                                    elif stub_errors:
                                        error_type = "StubError"
                                    else:
                                        error_type = "ValidationError"
                                    
                                    # Build instruction based on error type
                                    if error_type == "StubError":
                                        # Parse stub error messages to extract the specific file paths
                                        # and symbol names so the LLM receives actionable feedback.
                                        #
                                        # Two message formats are produced by validate_generated_project:
                                        #   "Stub marker '<text>' found in critical file <path>.py"
                                        #   "Stub class '<Name>' in <path>.py (body is only 'pass')"
                                        _stub_class_re = re.compile(
                                            r"[Ss]tub\s+class\s+'(\w+)'\s+in\s+([\w/.\-]+\.py)"
                                        )
                                        _stub_marker_re = re.compile(
                                            r"[Ss]tub\s+marker\s+'[^']+'\s+found\s+in\s+critical\s+file\s+([\w/.\-]+\.py)"
                                        )
                                        stub_by_file: dict = {}
                                        for err in stub_errors:
                                            class_match = _stub_class_re.search(err)
                                            marker_match = _stub_marker_re.search(err)
                                            if class_match:
                                                file_path = class_match.group(2)
                                                class_name = class_match.group(1)
                                                stub_by_file.setdefault(file_path, [])
                                                stub_by_file[file_path].append(class_name)
                                            elif marker_match:
                                                file_path = marker_match.group(1)
                                                stub_by_file.setdefault(file_path, [])
                                        stub_detail_lines = [
                                            "CRITICAL: The following files must have COMPLETE implementations, not stubs:"
                                        ]
                                        if stub_by_file:
                                            for stub_file_path, symbol_names in sorted(stub_by_file.items()):
                                                if symbol_names:
                                                    stub_detail_lines.append(
                                                        f"- {stub_file_path}: {', '.join(sorted(set(symbol_names)))}"
                                                    )
                                                else:
                                                    stub_detail_lines.append(f"- {stub_file_path}")
                                        else:
                                            # Fallback: include raw error details when parsing yields no results
                                            stub_detail_lines.extend(f"- {e}" for e in stub_errors[:5])
                                        instruction = (
                                            "\n".join(stub_detail_lines) + "\n"
                                            "Replace every raise NotImplementedError / pass stub with real business logic."
                                        )
                                    elif error_type == "ImportError":
                                        # Include the specific file paths and error messages so the
                                        # LLM can fix the exact import problems rather than regenerating
                                        # blindly (which caused errors to grow from 1 to 6 across retries).
                                        _import_detail_lines = [
                                            "The previous code generation had import errors. "
                                            "Fix EACH of the following specific issues:"
                                        ]
                                        for _ie in import_errors[:10]:
                                            _import_detail_lines.append(f"- {_ie}")
                                        instruction = (
                                            "\n".join(_import_detail_lines)
                                            + "\n\nFor each error, add the missing import statement "
                                            "at the top of the affected file using "
                                            "`from <module> import <symbol>`."
                                        )
                                    else:
                                        instruction = (
                                            "The previous code generation had validation errors. "
                                            "Please fix these errors and regenerate the code. "
                                            "Pay special attention to:\n"
                                            "1. String literals must be properly terminated with matching quotes\n"
                                            "2. All control structures (if, for, def, class, etc.) must end with a colon (:)\n"
                                            "3. Check for stray backslashes at line endings\n"
                                            "4. Ensure all brackets, parentheses, and braces are properly matched\n"
                                            "5. Include commas between function arguments and list/dict elements\n"
                                            "6. Ensure all modules used (e.g., time, os, json) are properly imported at the top of the file"
                                        )

                                    previous_error = {
                                        "error_type": error_type,
                                        "details": "\n".join(errors_for_retry[:3]),
                                        "instruction": instruction,
                                    }
                                    
                                    logger.warning(
                                        f"[PIPELINE] Job {job_id} validation failed, will retry codegen",
                                        extra={
                                            "job_id": job_id,
                                            "syntax_errors": syntax_errors,
                                            "import_errors": import_errors,
                                            "missing_files": missing_files,
                                            "attempt": codegen_attempt
                                        }
                                    )
                                    
                                    # Clean up failed output
                                    try:
                                        shutil.rmtree(output_path_for_validation)
                                        logger.info(f"[PIPELINE] Job {job_id} cleaned up failed output directory for retry")
                                    except Exception as cleanup_err:
                                        logger.warning(f"[PIPELINE] Job {job_id} cleanup error: {cleanup_err}")
                                    
                                    # Remove codegen from stages_completed since we're retrying
                                    if "codegen" in stages_completed:
                                        stages_completed.remove("codegen")
                                    
                                    # Continue to next attempt
                                    continue

                            # If validation passed, still check pre-materialization import errors (Fix 4)
                            _pme_passed = codegen_result.get("pre_mat_import_errors", [])
                            if _pme_passed and codegen_attempt <= max_codegen_retries and validation_passed:
                                validation_passed = False
                                previous_error = {
                                    "error_type": "ImportError",
                                    "details": "\n".join(_pme_passed[:3]),
                                    "instruction": (
                                        "The previous code generation had import errors in the following files:\n"
                                        + "\n".join(f"- {e}" for e in _pme_passed[:10])
                                        + "\n\nFor each error, add the missing import statement "
                                        "at the top of the affected file using "
                                        "`from <module> import <symbol>`."
                                    ),
                                }
                                logger.warning(
                                    f"[PIPELINE] Job {job_id} has pre-materialization import errors, retrying",
                                    extra={"job_id": job_id, "pre_mat_import_errors": _pme_passed, "attempt": codegen_attempt}
                                )
                                try:
                                    shutil.rmtree(output_path_for_validation)
                                except Exception:
                                    pass
                                if "codegen" in stages_completed:
                                    stages_completed.remove("codegen")
                                continue
                            # This catches cases where codegen "succeeds" but only generates a fraction of files
                            _CODEGEN_MIN_FILE_RATIO = 0.30
                            if spec_files and codegen_attempt <= max_codegen_retries and validation_passed:
                                output_dir_path = Path(output_path_for_validation)
                                if output_dir_path.exists():
                                    # Collect all generated file names once to avoid O(n*m) rglob calls
                                    generated_file_names = {
                                        f.name for f in output_dir_path.rglob("*") if f.is_file()
                                    }
                                    generated_count = len(generated_file_names)
                                    spec_required_count = len(spec_files)
                                    if spec_required_count > 0 and generated_count < _CODEGEN_MIN_FILE_RATIO * spec_required_count:
                                        validation_passed = False
                                        missing_spec_files = [
                                            sf for sf in spec_files
                                            if sf not in generated_file_names
                                        ]
                                        logger.warning(
                                            f"[PIPELINE] Job {job_id} generated only {generated_count} files "
                                            f"but spec requires ~{spec_required_count} ({attempt_label}). "
                                            f"Retrying with missing-file feedback.",
                                            extra={"job_id": job_id, "attempt": codegen_attempt,
                                                   "generated_count": generated_count,
                                                   "spec_required_count": spec_required_count}
                                        )
                                        # Collect already-generated file paths (relative) to pass to retry
                                        already_generated_files = [
                                            str(f.relative_to(output_dir_path))
                                            for f in output_dir_path.rglob("*") if f.is_file()
                                        ]
                                        previous_error = {
                                            "error_type": "InsufficientOutput",
                                            "details": (
                                                f"Only {generated_count} of ~{spec_required_count} required files were generated."
                                            ),
                                            "instruction": (
                                                "Previous generation was incomplete. "
                                                f"Missing required files: {missing_spec_files[:20]}. "
                                                "The following files were already generated and should be skipped; "
                                                f"focus only on generating the missing files: {already_generated_files[:30]}. "
                                                "Please generate ALL remaining specified files and endpoints."
                                            ),
                                            "already_generated_files": already_generated_files,
                                        }
                                        # Additive retry: keep existing files on disk so the retry
                                        # only needs to generate the missing ones (new files are merged in).
                                        logger.info(
                                            f"[PIPELINE] Job {job_id} keeping {len(already_generated_files)} "
                                            f"existing files for additive retry"
                                        )
                                        if "codegen" in stages_completed:
                                            stages_completed.remove("codegen")
                                        continue

                            # Issue 3 fix: spec fidelity check triggers codegen retry for ANY
                            # missing endpoints (not only when >50% are missing).  The previous
                            # threshold meant that 46% missing (13/28) never triggered a retry.
                            if (
                                md_content
                                and _PROVENANCE_AVAILABLE
                                and codegen_attempt <= max_codegen_retries
                                and validation_passed
                            ):
                                try:
                                    gen_dir_sf = Path(output_path_for_validation)
                                    gen_files_sf = {}
                                    for py_file in gen_dir_sf.rglob("*.py"):
                                        rel = str(py_file.relative_to(gen_dir_sf))
                                        gen_files_sf[rel] = py_file.read_text(encoding="utf-8")

                                    sf_result = _validate_spec_fidelity(
                                        md_content, gen_files_sf, output_path_for_validation
                                    )
                                    missing_eps = sf_result.get("missing_endpoints", [])
                                    required_eps = sf_result.get("required_endpoints", [])
                                    if not required_eps:
                                        # Fallback: count errors whose text indicates a missing
                                        # endpoint (format from _validate_spec_fidelity error list).
                                        required_eps = [
                                            e for e in sf_result.get("errors", [])
                                            if "Missing required endpoint" in e
                                        ] or missing_eps

                                    required_count = len(required_eps)
                                    missing_count = len(missing_eps)
                                    # Issue 3: trigger retry for ANY missing endpoints, not just >50%.
                                    if required_count > 0 and missing_count > 0:
                                        validation_passed = False
                                        # Normalise each missing endpoint to a human-readable
                                        # "METHOD /path" string regardless of whether the
                                        # spec-fidelity validator returned dicts or plain strings.
                                        def _ep_label(ep: Any) -> str:
                                            if isinstance(ep, dict):
                                                return (
                                                    f"{ep.get('method', '?').upper()} "
                                                    f"{ep.get('path', ep.get('url', '?'))}"
                                                )
                                            return str(ep)
                                        missing_ep_labels = [
                                            _ep_label(ep) for ep in missing_eps[:20]
                                        ]
                                        extra_note = (
                                            f" (and {missing_count - 20} more)"
                                            if missing_count > 20
                                            else ""
                                        )
                                        logger.warning(
                                            f"[PIPELINE] Job {job_id} spec fidelity retry triggered "
                                            f"({missing_count}/{required_count} endpoints missing, "
                                            f"{attempt_label}). Retrying codegen.",
                                            extra={
                                                "job_id": job_id,
                                                "attempt": codegen_attempt,
                                                "missing_count": missing_count,
                                                "required_count": required_count,
                                            },
                                        )
                                        # Collect already-generated file paths (relative) for additive retry
                                        _sf_dir = Path(output_path_for_validation)
                                        sf_already_generated = [
                                            str(f.relative_to(_sf_dir))
                                            for f in _sf_dir.rglob("*") if f.is_file()
                                        ] if _sf_dir.exists() else []
                                        previous_error = {
                                            "error_type": "SpecFidelityFailure",
                                            "details": (
                                                f"Only {required_count - missing_count} of "
                                                f"{required_count} required endpoints were generated."
                                            ),
                                            "instruction": (
                                                "The previous generation was incomplete. "
                                                f"The following required endpoints are missing: "
                                                f"{missing_ep_labels}{extra_note}. "
                                                "The following files were already generated and MUST NOT be "
                                                f"regenerated: {sf_already_generated[:30]}. "
                                                "Please generate ONLY the files needed to implement the missing endpoints."
                                            ),
                                            "already_generated_files": sf_already_generated,
                                        }
                                        # Additive retry: keep existing files on disk so the retry
                                        # only needs to generate implementations for missing endpoints.
                                        logger.info(
                                            f"[PIPELINE] Job {job_id} keeping {len(sf_already_generated)} "
                                            f"existing files for additive spec fidelity retry"
                                        )
                                        if "codegen" in stages_completed:
                                            stages_completed.remove("codegen")
                                        continue
                                except Exception as sf_err:
                                    logger.warning(
                                        f"[PIPELINE] Job {job_id} spec fidelity retry check error: {sf_err}"
                                    )
                        except Exception as val_err:
                            logger.warning(f"[PIPELINE] Job {job_id} validation check error: {val_err}")
                            # On validation error, assume success and break (fail-open for safety)
                    
                    if validation_passed:
                        # Validation passed or was skipped - codegen is successful
                        if "codegen" not in stages_completed:
                            stages_completed.append("codegen")
                        logger.info(f"[PIPELINE] Job {job_id} completed step: codegen ({attempt_label})")
                        
                        # Guard: skip ImportFixer if the validation errors (captured above) are purely
                        # from missing third-party packages. The fixer can only add project-local imports;
                        # it cannot remove hallucinated third-party imports and may create circular imports.
                        if _last_val_errors and all(_is_third_party_import_error(e) for e in _last_val_errors):
                            logger.info(
                                "[CODEGEN] Skipping ImportFixer for job %s — errors are from missing "
                                "third-party packages, not fixable by import fixer",
                                job_id,
                                extra={"job_id": job_id, "validation_errors": _last_val_errors}
                            )
                        else:
                            try:
                                from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine
                                
                                source_dir = Path(output_path_for_validation)
                                if source_dir.exists():
                                    # Collect Python source files, excluding test directories/files
                                    _test_dir_names = {"test", "tests", "__tests__"}
                                    source_files = [
                                        f for f in source_dir.rglob("*.py")
                                        if not any(part in _test_dir_names for part in f.parts)
                                        and not f.name.lower().startswith("test_")
                                    ]
                                    
                                    if source_files:
                                        logger.info(
                                            f"[CODEGEN] Running ImportFixerEngine on {len(source_files)} source files for job {job_id}"
                                        )
                                        
                                        fixer = ImportFixerEngine()
                                        # Build project symbol map for on-disk source files
                                        _src_file_map = {
                                            str(f.relative_to(source_dir)): f.read_text(encoding="utf-8")
                                            for f in source_files
                                            if f.is_file()
                                        }
                                        _proj_sym_map_src = fixer.build_project_symbol_map(_src_file_map)
                                        fixed_count = 0
                                        error_count = 0
                                        total_fixes = 0
                                        
                                        for src_file in source_files:
                                            try:
                                                src_content = src_file.read_text(encoding="utf-8")
                                                if not src_content.strip():
                                                    continue
                                                
                                                fix_result = fixer.fix_code(src_content, file_path=str(src_file), project_symbol_map=_proj_sym_map_src)
                                                
                                                if fix_result["status"] == "error":
                                                    error_count += 1
                                                    logger.warning(
                                                        f"[CODEGEN] Failed to auto-fix imports in source file {src_file.name}: {fix_result['message']}",
                                                        extra={"job_id": job_id, "source_file": str(src_file), "error": fix_result["message"]}
                                                    )
                                                    continue
                                                
                                                if fix_result["fixed_code"] != src_content and fix_result["fixes_applied"]:
                                                    src_file.write_text(fix_result["fixed_code"], encoding="utf-8")
                                                    fixed_count += 1
                                                    total_fixes += len(fix_result["fixes_applied"])
                                                    fixes_applied = fix_result["fixes_applied"]
                                                    
                                                    logger.info(
                                                        f"[CODEGEN] Auto-fixed imports in source file {src_file.name}: {', '.join(fixes_applied)}",
                                                        extra={
                                                            "job_id": job_id,
                                                            "source_file": str(src_file),
                                                            "fixes": fixes_applied,
                                                            "fix_count": len(fixes_applied)
                                                        }
                                                    )
                                            except Exception as file_err:
                                                error_count += 1
                                                logger.warning(
                                                    f"[CODEGEN] Exception while fixing imports in source file {src_file.name}: {file_err}",
                                                    exc_info=True,
                                                    extra={"job_id": job_id, "source_file": str(src_file), "error": str(file_err)}
                                                )
                                        
                                        if fixed_count > 0:
                                            logger.info(
                                                f"[CODEGEN] Import auto-fix summary for source files: {fixed_count} file(s) fixed with {total_fixes} total fix(es)",
                                                extra={
                                                    "job_id": job_id,
                                                    "files_fixed": fixed_count,
                                                    "total_fixes": total_fixes,
                                                    "errors": error_count
                                                }
                                            )
                                        elif error_count > 0:
                                            logger.warning(
                                                f"[CODEGEN] Import auto-fix for source files completed with {error_count} error(s), no files fixed",
                                                extra={"job_id": job_id, "error_count": error_count}
                                            )
                                        else:
                                            logger.debug(
                                                "[CODEGEN] Import auto-fix for source files completed: no missing imports detected",
                                                extra={"job_id": job_id}
                                            )
                            
                            except ImportError as import_err:
                                logger.warning(
                                    f"[CODEGEN] ImportFixerEngine unavailable for source files: {import_err}",
                                    extra={"job_id": job_id, "error": str(import_err)}
                                )
                            except Exception as fixer_err:
                                logger.error(
                                    f"[CODEGEN] Import auto-fix system error for source files: {fixer_err}",
                                    exc_info=True,
                                    extra={"job_id": job_id, "error": str(fixer_err), "error_type": type(fixer_err).__name__}
                                )
                        
                        break  # Success, exit retry loop
                else:
                    error_msg = codegen_result.get('message', 'Unknown error')
                    logger.warning(
                        f"[PIPELINE] Job {job_id} codegen {attempt_label} failed: {error_msg}",
                        extra={"job_id": job_id, "attempt": codegen_attempt, "error": error_msg}
                    )

                    # If this was the last attempt, fail the pipeline
                    if codegen_attempt > max_codegen_retries:
                        logger.error(
                            f"[PIPELINE] Job {job_id} failed step: codegen after {max_codegen_retries + 1} attempts",
                            extra={"job_id": job_id, "total_attempts": codegen_attempt}
                        )
                        return {
                            "status": "failed",
                            "message": f"Code generation failed after {codegen_attempt} attempts",
                            "stages_completed": stages_completed,
                            "last_error": error_msg,
                        }

                    # Set previous_error so the next attempt gets error context in the prompt
                    # (this also changes the prompt content, busting the LLM cache key)
                    _class_config_hint = ""
                    if "class Config:" in error_msg or "class Config" in error_msg:
                        _class_config_hint = (
                            "\n\nCRITICAL: Do NOT use 'class Config:' inside Pydantic models. "
                            "Instead use:\n"
                            "    model_config = ConfigDict(extra='forbid', from_attributes=True)\n"
                            "and add 'from pydantic import ConfigDict' to imports."
                        )
                    previous_error = {
                        "error_type": codegen_result.get("error_type", "CodegenError"),
                        "details": error_msg,
                        "attempt": codegen_attempt,
                        "instruction": (
                            "The previous code generation attempt failed. "
                            "Please fix the following error and regenerate the code:\n"
                            + _class_config_hint
                        ),
                    }

            # 2b. Post-codegen validation stages with retry on syntax errors
            output_path_for_validation = codegen_result.get("output_path")
            md_content = payload.get("readme_content", payload.get("requirements", ""))

            # FIX: Ensure requirements.txt exists with fallback before validation
            # This prevents pipeline failures when LLM omits requirements.txt or generates it empty
            if output_path_for_validation:
                requirements_path = Path(output_path_for_validation) / "requirements.txt"
                was_missing = not requirements_path.exists()
                # Short-circuit: stat() is only called when was_missing is False (file exists)
                if was_missing or requirements_path.stat().st_size == 0:
                    fallback_requirements = (
                        "# Auto-generated fallback requirements\n"
                        "# Generated by pipeline when requirements.txt was missing or empty\n"
                        "fastapi>=0.104.0\n"
                        "uvicorn[standard]>=0.24.0\n"
                        "pydantic>=2.5.0\n"
                        "pydantic-settings>=2.1.0\n"
                        "pytest>=7.4.0\n"
                        "httpx>=0.25.0\n"
                    )
                    try:
                        requirements_path.write_text(fallback_requirements, encoding="utf-8")
                        logger.info(
                            f"[PIPELINE] Job {job_id} auto-generated fallback requirements.txt "
                            f"({'was missing' if was_missing else 'was empty'})",
                            extra={"job_id": job_id, "requirements_path": str(requirements_path)}
                        )
                        logger.debug(
                            f"[PIPELINE] Job {job_id} fallback requirements.txt content:\n{fallback_requirements}",
                            extra={"job_id": job_id}
                        )
                    except Exception as req_err:
                        logger.warning(
                            f"[PIPELINE] Job {job_id} failed to write fallback requirements.txt: {req_err}",
                            extra={"job_id": job_id}
                        )

            # Extract spec-required files from the MD content so that
            # validation catches missing files like app/routes.py when the
            # spec references them.
            # Get target language for ecosystem filtering
            target_lang = payload.get("language", "python").lower()
            
            # Language-aware default required files
            LANGUAGE_REQUIRED_FILES = {
                "python": ["main.py"],
                "py": ["main.py"],
                "typescript": [],
                "ts": [],
                "javascript": [],
                "js": [],
                "java": [],
                "go": [],
                "rust": [],
            }
            required_files = list(LANGUAGE_REQUIRED_FILES.get(target_lang, ["main.py"]))

            # Adjust required_files based on actual output structure (app/ layout detection)
            # (Only applies to Python projects)
            if target_lang in ("python", "py") and output_path_for_validation:
                output_path_obj = Path(output_path_for_validation)
                app_dir = output_path_obj / "app"
                if app_dir.is_dir():
                    # Remove root main.py requirement for app-structured projects
                    if "main.py" in required_files:
                        required_files.remove("main.py")
                    # Explicitly add app/ subdirectory files to required_files
                    # The validator does NOT auto-detect these, so we must add them here
                    required_files.extend(["app/main.py", "app/routes.py", "app/schemas.py"])
                    logger.info(
                        f"[PIPELINE] Job {job_id} detected app/ layout, adjusted required files: {required_files}",
                        extra={"job_id": job_id, "required_files": required_files}
                    )

            if md_content and _PROVENANCE_AVAILABLE:
                try:
                    spec_files = _extract_required_files_from_md(md_content, target_language=target_lang)
                    if spec_files:
                        existing = set(required_files)
                        required_files.extend(sf for sf in spec_files if sf not in existing)
                        logger.info(
                            f"[PIPELINE] Job {job_id} spec-derived required files: {required_files}",
                            extra={"job_id": job_id}
                        )
                except Exception as parse_err:
                    logger.warning(f"[PIPELINE] Job {job_id} failed to extract required files from spec: {parse_err}")

            # Validate generated project (syntax + JSON-bundle detection)
            if output_path_for_validation and _MATERIALIZER_AVAILABLE:
                try:
                    val_result = await _validate_generated_project(
                        output_dir=output_path_for_validation,
                        required_files=required_files,
                        check_python_syntax=(target_lang in ("python", "py")),
                        language=target_lang,
                    )
                    if not val_result.get("valid", True):
                        validation_errors = val_result.get('errors', [])
                        validation_warnings = val_result.get('warnings', [])

                        # NOTE: Retry logic for syntax errors has moved into the codegen retry loop above
                        # This section now only handles final validation failures after all retries exhausted
                        
                        logger.error(
                            f"[PIPELINE] Job {job_id} validation failed after all retries",
                            extra={"job_id": job_id, "validation_errors": validation_errors}
                        )

                        # Store validation info in job metadata
                        if job_id in jobs_db:
                            job = jobs_db[job_id]
                            job.metadata["validation_errors"] = validation_errors
                            job.metadata["validation_warnings"] = validation_warnings

                        await _write_validation_error(output_path_for_validation, val_result)

                        if validation_errors:
                            # Classify errors: only actual syntax errors are hard failures
                            # that make code un-importable.  The validator emits syntax errors
                            # in the format "Python syntax error in <file>: line N: <msg>".
                            # Stub class warnings ("Stub class '...' in ...") and K8s manifest
                            # warnings ("K8s Deployment manifest missing ...") are soft failures
                            # that should be logged but must not prevent downstream stages.
                            _HARD_ERROR_MARKERS = (
                                "python syntax error in",  # from validate_generated_project
                                "syntaxerror",             # from syntax_auto_repair / AST parse
                                "unterminated string",     # specific Python syntax error msg
                                "invalid syntax",          # Python parse error msg
                            )
                            _SOFT_ERROR_MARKERS = (
                                "stub class '",            # "Stub class 'X' in file.py"
                                "stub marker '",           # "Stub marker '...' found in critical file"
                                "k8s ",                    # K8s manifest field warnings
                                "spec.selector",           # K8s missing field warnings
                                "missing 'spec.",          # K8s missing spec fields
                            )
                            _hard_errors = [
                                e for e in validation_errors
                                if any(marker in e.lower() for marker in _HARD_ERROR_MARKERS)
                            ]
                            _soft_errors = [e for e in validation_errors if e not in _hard_errors]

                            if _hard_errors:
                                # HARD FAIL: Actual syntax errors make code un-importable; block downstream.
                                logger.error(
                                    f"[PIPELINE] Job {job_id} HARD FAIL - syntax errors in validation: {_hard_errors}",
                                    extra={"job_id": job_id, "hard_errors": _hard_errors, "soft_errors": _soft_errors}
                                )
                                # Track that validate failed and testgen was implicitly skipped
                                # so generator.py reports the correct failing stage ("validate", not "testgen")
                                stages_completed.append("testgen:skipped")
                                await self._finalize_failed_job(
                                    job_id, error=f"Validation failed: {_hard_errors}"
                                )
                                return {
                                    "status": "failed",
                                    "message": f"Validation failed: {_hard_errors}",
                                    "stages_completed": stages_completed,
                                    "output_path": output_path_for_validation,
                                }
                            else:
                                # Soft failures only (stub class warnings, K8s field warnings, etc.)
                                # Log and store in metadata but allow downstream stages to proceed.
                                logger.warning(
                                    f"[PIPELINE] Job {job_id} validation soft failures (non-blocking): {_soft_errors}",
                                    extra={"job_id": job_id, "soft_errors": _soft_errors}
                                )
                                stages_completed.append("validate:warnings")
                        else:
                            # Only warnings, not errors - log and continue
                            logger.warning(
                                f"[PIPELINE] Job {job_id} validation warnings (non-fatal): {validation_warnings}",
                                extra={"job_id": job_id}
                            )
                            stages_completed.append("validate")
                    else:
                        stages_completed.append("validate")
                        logger.info(f"[PIPELINE] Job {job_id} completed step: validate")
                except Exception as val_err:
                    logger.warning(f"[PIPELINE] Job {job_id} validation step error: {val_err}")
            else:
                # Validate step was not run (no output_path or materializer unavailable)
                stages_completed.append("validate:skipped")
            
            # 2c. Spec fidelity check (uses existing provenance.validate_spec_fidelity)
            # This is a GATE: if >SPEC_FIDELITY_MISSING_ENDPOINT_THRESHOLD of required
            # endpoints are missing after the final codegen attempt, fail the job.
            if output_path_for_validation and _PROVENANCE_AVAILABLE:
                try:
                    if md_content:
                        # Read generated files for spec validation
                        gen_dir = Path(output_path_for_validation)
                        gen_files = {}
                        for py_file in gen_dir.glob("**/*.py"):
                            rel = str(py_file.relative_to(gen_dir))
                            gen_files[rel] = py_file.read_text(encoding="utf-8")
                        
                        spec_result = _validate_spec_fidelity(
                            md_content, gen_files, output_path_for_validation
                        )
                        if spec_result.get("valid", True):
                            stages_completed.append("spec_validate")
                            logger.info(f"[PIPELINE] Job {job_id} completed step: spec_validate")
                        else:
                            missing_eps = spec_result.get("missing_endpoints", [])
                            required_eps = spec_result.get("required_endpoints", [])
                            # Fallback: derive required_eps count from error messages when the
                            # validator doesn't populate required_endpoints directly.
                            # NOTE: This assumes the validator uses "Missing required endpoint"
                            # in its error messages (see generator/main/provenance.py).
                            # If the validator changes its message format, update this filter.
                            if not required_eps:
                                required_eps = [
                                    e for e in spec_result.get("errors", [])
                                    if "Missing required endpoint" in e
                                ] or missing_eps
                            missing_count = len(missing_eps)
                            required_count = len(required_eps)
                            if (
                                required_count > 0
                                and missing_count / required_count > SPEC_FIDELITY_MISSING_ENDPOINT_THRESHOLD
                            ):
                                missing_ep_labels = [
                                    f"{ep.get('method', '?')} {ep.get('path', '?')}"
                                    if isinstance(ep, dict)
                                    else str(ep)
                                    for ep in missing_eps[:20]
                                ]
                                extra_note = (
                                    f" (and {missing_count - 20} more)"
                                    if missing_count > 20
                                    else ""
                                )
                                fail_msg = (
                                    f"Spec fidelity check failed: {missing_count}/{required_count} "
                                    f"required endpoints are missing "
                                    f"(>{int(SPEC_FIDELITY_MISSING_ENDPOINT_THRESHOLD * 100)}% threshold). "
                                    f"Missing: {missing_ep_labels}{extra_note}"
                                )
                                logger.error(
                                    f"[PIPELINE] Job {job_id} HARD FAIL — {fail_msg}",
                                    extra={
                                        "job_id": job_id,
                                        "missing_count": missing_count,
                                        "required_count": required_count,
                                        "missing_endpoints": missing_ep_labels,
                                    },
                                )
                                stages_completed.append("spec_validate:failed")
                                await self._finalize_failed_job(job_id, error=fail_msg)
                                return {
                                    "status": "failed",
                                    "message": fail_msg,
                                    "stages_completed": stages_completed,
                                    "missing_endpoints": missing_ep_labels,
                                    "output_path": output_path_for_validation,
                                }
                            else:
                                logger.warning(
                                    f"[PIPELINE] Job {job_id} spec fidelity check found issues: "
                                    f"{spec_result.get('errors', [])}",
                                    extra={"job_id": job_id}
                                )
                except Exception as spec_err:
                    logger.warning(f"[PIPELINE] Job {job_id} spec validation error: {spec_err}")
            
            # 2d. README completeness validation
            if output_path_for_validation and _PROVENANCE_AVAILABLE:
                try:
                    gen_dir = Path(output_path_for_validation)
                    readme_path = gen_dir / "README.md"
                    
                    if readme_path.exists():
                        readme_content = readme_path.read_text(encoding="utf-8")
                        readme_result = _validate_readme_completeness(readme_content, language=target_lang)
                        
                        if readme_result.get("valid", True):
                            logger.info(
                                f"[PIPELINE] Job {job_id} README validation passed - "
                                f"length: {readme_result['length']}, "
                                f"sections: {readme_result['sections_found']}, "
                                f"commands: {readme_result['commands_found']}",
                                extra={"job_id": job_id, "readme_validation": readme_result}
                            )
                        else:
                            logger.warning(
                                f"[PIPELINE] Job {job_id} README validation found issues: "
                                f"{readme_result.get('errors', [])}",
                                extra={"job_id": job_id, "readme_validation": readme_result}
                            )
                    else:
                        logger.warning(
                            f"[PIPELINE] Job {job_id} README.md not found at {readme_path}",
                            extra={"job_id": job_id}
                        )
                except Exception as readme_err:
                    logger.warning(f"[PIPELINE] Job {job_id} README validation error: {readme_err}")
            
            # FIX Issue 4: README Regeneration
            # If README is incomplete or missing, regenerate it
            if output_path_for_validation:
                try:
                    gen_dir = Path(output_path_for_validation)
                    readme_path = gen_dir / "README.md"
                    should_regenerate = False
                    
                    if not readme_path.exists():
                        logger.warning(f"[PIPELINE] Job {job_id} README.md missing, will generate")
                        should_regenerate = True
                    else:
                        # Check if existing README is incomplete
                        # This check runs regardless of _PROVENANCE_AVAILABLE
                        readme_content = readme_path.read_text(encoding="utf-8")
                        
                        if _PROVENANCE_AVAILABLE:
                            readme_result = _validate_readme_completeness(readme_content, language=target_lang)
                            
                            if not readme_result.get("valid", True):
                                logger.warning(
                                    f"[PIPELINE] Job {job_id} README incomplete, will regenerate. "
                                    f"Errors: {readme_result.get('errors', [])}",
                                    extra={"job_id": job_id, "readme_errors": readme_result.get('errors', [])}
                                )
                                should_regenerate = True
                        else:
                            # Fallback: Simple length check if validation not available
                            if len(readme_content) < MIN_README_LENGTH:
                                logger.warning(
                                    f"[PIPELINE] Job {job_id} README too short ({len(readme_content)} chars), will regenerate"
                                )
                                should_regenerate = True
                    
                    if should_regenerate:
                        # Generate comprehensive README
                        project_name = _extract_project_name_from_path_or_payload(payload) or "generated_project"
                        
                        comprehensive_readme = _generate_fallback_readme(
                            project_name=project_name,
                            language="python",
                            output_path=str(gen_dir)
                        )
                        
                        # Write the new README
                        readme_path.write_text(comprehensive_readme, encoding="utf-8")
                        logger.info(
                            f"[PIPELINE] Job {job_id} generated comprehensive README with all required sections",
                            extra={"job_id": job_id, "readme_path": str(readme_path), "length": len(comprehensive_readme)}
                        )
                        
                        # Validate the new README
                        if _PROVENANCE_AVAILABLE:
                            new_readme_result = _validate_readme_completeness(comprehensive_readme, language=target_lang)
                            if new_readme_result.get("valid", True):
                                logger.info(
                                    f"[PIPELINE] Job {job_id} regenerated README validated successfully - "
                                    f"sections: {new_readme_result['sections_found']}, "
                                    f"commands: {new_readme_result['commands_found']}",
                                    extra={"job_id": job_id, "readme_validation": new_readme_result}
                                )
                            else:
                                logger.warning(
                                    f"[PIPELINE] Job {job_id} regenerated README still has issues: {new_readme_result.get('errors', [])}",
                                    extra={"job_id": job_id}
                                )
                except Exception as readme_regen_err:
                    logger.error(
                        f"[PIPELINE] Job {job_id} README regeneration error: {readme_regen_err}",
                        exc_info=True
                    )
            
            # 2e. Write provenance metadata
            if output_path_for_validation and _PROVENANCE_AVAILABLE:
                try:
                    tracker = ProvenanceTracker(job_id=job_id)
                    if md_content:
                        tracker.record_stage("READ_MD", artifacts={"md_input": md_content})
                    tracker.record_stage("CODEGEN", metadata={
                        "files_generated": codegen_result.get("files_count", 0),
                        "output_path": output_path_for_validation,
                    })
                    tracker.record_stage("MATERIALIZE", metadata={
                        "materializer_used": _MATERIALIZER_AVAILABLE,
                    })
                    tracker.save_to_file(output_path_for_validation)
                    logger.info(f"[PIPELINE] Job {job_id} provenance written")
                except Exception as prov_err:
                    logger.warning(f"[PIPELINE] Job {job_id} provenance error: {prov_err}")
            
            # FIX Issue A: Detect language once for all downstream stages
            # Move language detection outside testgen-only block so it's available for
            # deploy, docgen, and critique stages even if tests are disabled
            output_path = codegen_result.get("output_path")
            detected_language = payload.get("language")
            if detected_language:
                logger.info(f"[PIPELINE] Job {job_id} using explicit language from payload: {detected_language}")
            elif output_path:
                # Auto-detect project language from generated files
                code_path = Path(output_path)
                detected_language = _detect_project_language(code_path)
                logger.info(f"[PIPELINE] Job {job_id} detected language: {detected_language}")
            else:
                # Fallback to Python if no output path available
                detected_language = "python"
                logger.warning(f"[PIPELINE] Job {job_id} no output path for language detection, defaulting to python")
            
            # 2f. Post-codegen spec completeness check
            # Check spec-derived required files against generated files to ensure completeness
            if _PROVENANCE_AVAILABLE and _extract_required_files_from_md and md_content and output_path:
                try:
                    spec_required = set(_extract_required_files_from_md(md_content, target_language=detected_language))
                    code_path = Path(output_path)
                    
                    # Get all generated files recursively
                    generated_files = set()
                    if code_path.exists():
                        for file_path in code_path.rglob("*"):
                            if file_path.is_file():
                                # Get relative path from output_path
                                rel_path = str(file_path.relative_to(code_path))
                                generated_files.add(rel_path)
                                # Also add just the filename for matching
                                generated_files.add(file_path.name)
                    
                    missing_from_spec = spec_required - generated_files
                    
                    if missing_from_spec:
                        # Categorize missing files using module-level constants
                        missing_deploy_files = missing_from_spec & DEPLOYMENT_FILE_PATTERNS
                        missing_frontend_files = missing_from_spec & FRONTEND_FILE_PATTERNS
                        missing_config_files = missing_from_spec & CONFIG_FILE_PATTERNS
                        
                        if missing_deploy_files:
                            logger.info(
                                f"[PIPELINE] Job {job_id} spec requires deployment files not yet generated: "
                                f"{missing_deploy_files}. Ensuring deploy step is enabled."
                            )
                            # Override include_deployment to ensure it runs
                            if not payload.get("include_deployment", True):
                                payload["include_deployment"] = True
                                logger.info(f"[PIPELINE] Job {job_id} enabled deployment due to spec-derived requirements")
                        
                        if missing_frontend_files:
                            logger.info(
                                f"[PIPELINE] Job {job_id} spec requires frontend files not yet generated: "
                                f"{missing_frontend_files}. Generating fallback frontend files."
                            )
                            # FIX Failure 4: Generate fallback frontend files when spec requires them
                            if output_path:
                                try:
                                    # Use output_dir basename as project name, falling back to job_id
                                    output_dir = payload.get("output_dir", "")
                                    if output_dir:
                                        dir_name = Path(output_dir).name
                                        # Handle edge case where Path().name returns '.' or empty
                                        project_name = dir_name if dir_name and dir_name != '.' else f"Project-{job_id[:8]}"
                                    else:
                                        project_name = f"Project-{job_id[:8]}"
                                    
                                    frontend_results = _generate_fallback_frontend_files(
                                        output_path=output_path,
                                        missing_files=missing_frontend_files,
                                        project_name=project_name
                                    )
                                    generated_files = [f for f, success in frontend_results.items() if success]
                                    failed_files = [f for f, success in frontend_results.items() if not success]
                                    
                                    if generated_files:
                                        logger.info(
                                            f"[PIPELINE] Job {job_id} generated fallback frontend files: {generated_files}",
                                            extra={"job_id": job_id, "generated_files": generated_files}
                                        )
                                    if failed_files:
                                        logger.warning(
                                            f"[PIPELINE] Job {job_id} failed to generate some frontend files: {failed_files}",
                                            extra={"job_id": job_id, "failed_files": failed_files}
                                        )
                                except Exception as fe_err:
                                    logger.warning(
                                        f"[PIPELINE] Job {job_id} error generating fallback frontend: {fe_err}",
                                        extra={"job_id": job_id}
                                    )
                        
                        if missing_config_files:
                            logger.info(
                                f"[PIPELINE] Job {job_id} spec requires config files not yet generated: "
                                f"{missing_config_files}."
                            )
                        
                        logger.info(
                            f"[PIPELINE] Job {job_id} spec completeness check: "
                            f"{len(missing_from_spec)} files from spec not yet generated. "
                            f"Deploy/docgen steps will attempt to generate these."
                        )
                    else:
                        logger.info(
                            f"[PIPELINE] Job {job_id} spec completeness check: "
                            f"All {len(spec_required)} spec-derived files generated successfully"
                        )
                except Exception as spec_check_err:
                    logger.warning(
                        f"[PIPELINE] Job {job_id} spec completeness check error: {spec_check_err}",
                        exc_info=True
                    )
            
            # PARALLELIZATION: Start deploy task early since it only depends on codegen output
            # Deploy will run in parallel with testgen/critique/SFE to reduce total pipeline time
            deploy_task = None
            include_deployment = payload.get("include_deployment", True)
            
            # Collect spec-derived deployment requirements if available
            spec_derived_deploy_files = []
            if _PROVENANCE_AVAILABLE and _extract_required_files_from_md and md_content:
                try:
                    spec_files = set(_extract_required_files_from_md(md_content, target_language=detected_language))
                    spec_derived_deploy_files = list(spec_files & DEPLOYMENT_FILE_PATTERNS)
                except Exception:
                    pass
            
            if include_deployment:
                async def run_deploy_parallel():
                    """Run deploy stage in parallel with other stages."""
                    try:
                        deploy_payload = {
                            "code_path": codegen_result.get("output_path"),
                            "include_ci_cd": True,
                            "output_dir": payload.get("output_dir", ""),
                            "generated_files": codegen_result.get("file_names", []),
                            "language": detected_language,
                            "spec_required_files": spec_derived_deploy_files,  # Pass spec-derived requirements
                        }
                        logger.info(
                            f"[PIPELINE] Job {job_id} starting deploy in parallel with testgen "
                            f"with {len(deploy_payload.get('generated_files', []))} files"
                            + (f" (spec requires: {spec_derived_deploy_files})" if spec_derived_deploy_files else "")
                        )
                        result = await self._run_deploy_all(job_id, deploy_payload)
                        logger.info(f"[PIPELINE] Job {job_id} parallel deploy completed with status: {result.get('status')}")
                        return result
                    except Exception as e:
                        logger.error(f"[PIPELINE] Job {job_id} parallel deploy exception: {e}", exc_info=True)
                        return {"status": "error", "message": str(e)}
                
                deploy_task = asyncio.create_task(run_deploy_parallel())
                logger.info(f"[PIPELINE] Job {job_id} deploy task created and running in background")
            
            # 3. Testgen (if requested)
            # RESILIENCE FIX: Pipeline continues even if testgen fails
            # Industry Standard: Fail-safe pipeline design - individual stage failures
            # should not abort the entire workflow. This ensures maximum output delivery
            # even when optional stages encounter errors.
            if payload.get("include_tests", True):
                try:
                    # Check if codegen actually produced valid source files
                    if output_path:
                        code_path = Path(output_path)
                        
                        # Get file patterns for the detected language
                        file_patterns = LANGUAGE_FILE_EXTENSIONS.get(detected_language, ["*.py"])
                        
                        # Find source files using language-specific patterns
                        source_files = []
                        if code_path.exists():
                            for pattern in file_patterns:
                                for f in code_path.rglob(pattern):
                                    if not _is_test_file(f, detected_language):
                                        source_files.append(f)
                        
                        if not source_files:
                            logger.warning(
                                f"[PIPELINE] Job {job_id} skipping testgen - no source files found in {output_path}",
                                extra={
                                    "job_id": job_id,
                                    "output_path": str(output_path),
                                    "detected_language": detected_language,
                                    "files_in_directory": [f.name for f in code_path.iterdir()] if code_path.exists() else []
                                }
                            )
                        else:
                            # Check if LLM provider is configured for intelligent test generation
                            # detect_available_llm_provider() is imported at line 158 from runner.llm_client
                            llm_provider_configured = False
                            try:
                                if self.llm_config and self.llm_config.default_llm_provider:
                                    llm_provider_configured = True
                                elif detect_available_llm_provider():
                                    llm_provider_configured = True
                            except Exception:
                                pass
                            
                            testgen_payload = {
                                "code_path": output_path,
                                "test_type": "unit",
                                "coverage_target": 80.0,
                                "use_llm": llm_provider_configured,  # Enable LLM-based generation when provider available
                                "llm_timeout": DEFAULT_TESTGEN_PIPELINE_TIMEOUT if llm_provider_configured else 30,  # Use pipeline timeout for full pipeline mode
                                "language": detected_language,  # Pass detected language
                            }
                            logger.info(
                                f"[PIPELINE] Job {job_id} starting step: testgen with {len(source_files)} source files "
                                f"(language: {detected_language}, LLM-based: {llm_provider_configured})"
                            )
                            _testgen_timeout = PIPELINE_STEP_TIMEOUTS["testgen"]
                            try:
                                testgen_result = await asyncio.wait_for(
                                    self._run_testgen(job_id, testgen_payload),
                                    timeout=_testgen_timeout,
                                )
                            except asyncio.TimeoutError:
                                logger.error(
                                    f"[PIPELINE] Step 'testgen' timed out after {_testgen_timeout}s for job {job_id}",
                                    extra={"job_id": job_id, "timeout": _testgen_timeout}
                                )
                                testgen_result = {"status": "error", "message": f"testgen timed out after {_testgen_timeout}s"}
                            
                            # Check for skipped status first (non-Python projects)
                            if testgen_result.get("status") == "skipped":
                                stages_completed.append("testgen:skipped")
                                logger.info(
                                    f"[PIPELINE] Job {job_id} testgen skipped (likely non-Python project). "
                                    f"Continuing pipeline without test execution.",
                                    extra={
                                        "job_id": job_id,
                                        "reason": testgen_result.get("message", "Not applicable for this project type")
                                    }
                                )
                            elif testgen_result.get("status") == "completed":
                                # BUG FIX: Check if tests actually passed, not just if testgen completed
                                # Even if testgen "completed", tests may have failed
                                test_execution_failed = False
                                if payload.get("include_tests", True):
                                    # Extract test results from testgen_result
                                    result_data = testgen_result.get("result", {})
                                    final_validation_report = result_data.get("final_validation_report", {})
                                    
                                    # Check coverage validation results for test failures
                                    coverage_report = final_validation_report.get("coverage", {})
                                    test_results = coverage_report.get("test_results", {})
                                    fail_count = test_results.get("failed", 0) or test_results.get("fail_count", 0)
                                    
                                    # Also check top-level test results if available
                                    if fail_count == 0 and "test_results" in result_data:
                                        top_level_results = result_data.get("test_results", {})
                                        fail_count = top_level_results.get("failed", 0) or top_level_results.get("fail_count", 0)
                                    
                                    if fail_count > 0:
                                        # Test execution failed - use specific marker
                                        test_execution_failed = True
                                        logger.error(
                                            f"[PIPELINE] Job {job_id} testgen completed but {fail_count} test(s) failed. "
                                            f"Marking stage as execution_failed but continuing pipeline.",
                                            extra={
                                                "job_id": job_id,
                                                "fail_count": fail_count,
                                                "failure_type": "test_execution",
                                            }
                                        )
                                        stages_completed.append("testgen:execution_failed")
                                
                                # Only mark as successful if tests passed (or weren't checked)
                                if not test_execution_failed:
                                    stages_completed.append("testgen")
                                    logger.info(f"[PIPELINE] Job {job_id} completed step: testgen")
                                    
                                    # Run ImportFixerEngine on test files after testgen
                                    # This fixes missing imports in generated test files (e.g., missing "import pytest")
                                    try:
                                        from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine
                                        
                                        # Find test files in the tests/ directory
                                        tests_dir = Path(output_path) / "tests"
                                        if tests_dir.exists():
                                            test_files = list(tests_dir.rglob("*.py"))
                                            
                                            if test_files:
                                                logger.info(
                                                    f"[TESTGEN] Running ImportFixerEngine on {len(test_files)} test files for job {job_id}"
                                                )
                                                
                                                fixer = ImportFixerEngine()
                                                fixed_count = 0
                                                error_count = 0
                                                total_fixes = 0
                                                
                                                for test_file in test_files:
                                                    try:
                                                        # Read the test file
                                                        content = test_file.read_text(encoding="utf-8")
                                                        
                                                        # Skip empty files
                                                        if not content.strip():
                                                            continue
                                                        
                                                        # Fix imports
                                                        fix_result = fixer.fix_code(content, file_path=str(test_file))
                                                        
                                                        if fix_result["status"] == "error":
                                                            error_count += 1
                                                            logger.warning(
                                                                f"[TESTGEN] Failed to auto-fix imports in test file {test_file.name}: {fix_result['message']}",
                                                                extra={"job_id": job_id, "source_file": str(test_file), "error": fix_result["message"]}
                                                            )
                                                            continue
                                                        
                                                        # Check if any fixes were applied
                                                        if fix_result["fixed_code"] != content and fix_result["fixes_applied"]:
                                                            # Write fixed content back to disk
                                                            test_file.write_text(fix_result["fixed_code"], encoding="utf-8")
                                                            fixed_count += 1
                                                            total_fixes += len(fix_result["fixes_applied"])
                                                            fixes_applied = fix_result["fixes_applied"]
                                                            
                                                            logger.info(
                                                                f"[TESTGEN] Auto-fixed imports in test file {test_file.name}: {', '.join(fixes_applied)}",
                                                                extra={
                                                                    "job_id": job_id,
                                                                    "source_file": str(test_file),
                                                                    "fixes": fixes_applied,
                                                                    "fix_count": len(fixes_applied)
                                                                }
                                                            )
                                                    except Exception as file_err:
                                                        error_count += 1
                                                        logger.warning(
                                                            f"[TESTGEN] Exception while fixing imports in test file {test_file.name}: {file_err}",
                                                            exc_info=True,
                                                            extra={"job_id": job_id, "source_file": str(test_file), "error": str(file_err)}
                                                        )
                                                
                                                # Summary logging
                                                if fixed_count > 0:
                                                    logger.info(
                                                        f"[TESTGEN] Import auto-fix summary for test files: {fixed_count} file(s) fixed with {total_fixes} total fix(es)",
                                                        extra={
                                                            "job_id": job_id,
                                                            "files_fixed": fixed_count,
                                                            "total_fixes": total_fixes,
                                                            "errors": error_count
                                                        }
                                                    )
                                                elif error_count > 0:
                                                    logger.warning(
                                                        f"[TESTGEN] Import auto-fix for test files completed with {error_count} error(s), no files fixed",
                                                        extra={"job_id": job_id, "error_count": error_count}
                                                    )
                                                else:
                                                    logger.debug(
                                                        "[TESTGEN] Import auto-fix for test files completed: no missing imports detected",
                                                        extra={"job_id": job_id}
                                                    )
                                            else:
                                                logger.debug(f"[TESTGEN] No test files found in {tests_dir} for import fixing")
                                        else:
                                            logger.debug(f"[TESTGEN] No tests/ directory found at {tests_dir}")
                                    
                                    except ImportError as import_err:
                                        logger.warning(
                                            f"[TESTGEN] ImportFixerEngine unavailable for test files: {import_err}",
                                            extra={"job_id": job_id, "error": str(import_err)}
                                        )
                                    except Exception as fixer_err:
                                        logger.error(
                                            f"[TESTGEN] Import auto-fix system error for test files: {fixer_err}",
                                            exc_info=True,
                                            extra={"job_id": job_id, "error": str(fixer_err), "error_type": type(fixer_err).__name__}
                                        )
                            elif testgen_result.get("status") == "error":
                                # Test generation failed - use specific marker
                                testgen_error = testgen_result.get('message', 'Unknown error')
                                logger.error(
                                    f"[PIPELINE] Job {job_id} failed step: testgen - {testgen_error}",
                                    extra={
                                        "job_id": job_id,
                                        "error": testgen_error,
                                        "failure_type": "generation_error",
                                    }
                                )
                                stages_completed.append("testgen:error")
                                logger.warning(f"[PIPELINE] Job {job_id} continuing pipeline despite testgen failure")
                    else:
                        logger.warning(
                            f"[PIPELINE] Job {job_id} skipping testgen - no output path from codegen",
                            extra={"job_id": job_id}
                        )
                except Exception as e:
                    # Industry Standard: Comprehensive error logging with context
                    logger.error(
                        f"[PIPELINE] Job {job_id} testgen exception: {e}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "stage": "testgen",
                            "error_type": type(e).__name__,
                            "output_path": output_path if 'output_path' in locals() else None,
                            "failure_type": "exception",
                        }
                    )
                    stages_completed.append("testgen:exception")
                    logger.warning(
                        f"[PIPELINE] Job {job_id} continuing pipeline despite testgen exception",
                        extra={"job_id": job_id, "remaining_stages": ["deploy", "docgen", "critique"]}
                    )
            
            # 4. Critique (if requested)
            # PIPELINE REORDER: Critique now runs AFTER testgen but BEFORE deploy/docgen
            # This allows auto-fix to repair issues before deployment
            # FIX: Default to True since critique is a core pipeline feature for quality
            critique_result = {}  # Initialize for later reference
            if payload.get("run_critique", True):
                # Enrich critique with test and validation results for better context
                # NOTE: Only testgen can have failed at this point since critique runs before deploy/docgen
                # Deploy and docgen failures will be detected in later pipeline runs
                stages_failed = []
                if payload.get("include_tests", True) and "testgen" not in stages_completed:
                    stages_failed.append("testgen")
                
                critique_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "scan_types": ["security", "quality"],
                    "auto_fix": True,  # FIX: Enable auto-fix to apply fixes before deploy/docgen
                    # Feed test results so critique can suggest fixes
                    "test_results": testgen_result,
                    "validation_results": val_result,
                    "stages_completed": stages_completed,
                    "stages_failed": stages_failed,
                    "output_dir": payload.get("output_dir", ""),
                    "language": detected_language,
                }
                logger.info(f"[PIPELINE] Job {job_id} starting step: critique")
                # Dynamic timeout: at least 180 s, plus 5 s per generated file, but
                # never less than the operator-configured base timeout.
                _base_critique_timeout = PIPELINE_STEP_TIMEOUTS["critique"]
                _output_path_obj = Path(codegen_result.get("output_path", "."))
                _files_dict = codegen_result.get("files", {})
                if _files_dict:
                    _critique_file_count = len(_files_dict)
                elif _output_path_obj.exists():
                    logger.debug(
                        f"[PIPELINE] Job {job_id} critique: codegen 'files' dict absent, "
                        f"falling back to filesystem count in {_output_path_obj}"
                    )
                    _critique_file_count = sum(
                        1 for _ in _output_path_obj.rglob("*") if _.is_file()
                    )
                else:
                    _critique_file_count = 0
                _critique_timeout = max(
                    _base_critique_timeout,
                    180,
                    30 + 5 * _critique_file_count,
                )
                logger.debug(
                    f"[PIPELINE] Job {job_id} critique timeout: {_critique_timeout}s "
                    f"(base={_base_critique_timeout}s, files={_critique_file_count})"
                )
                try:
                    critique_result = await asyncio.wait_for(
                        self._run_critique(job_id, critique_payload),
                        timeout=_critique_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"[PIPELINE] Step 'critique' timed out after {_critique_timeout}s for job {job_id}",
                        extra={"job_id": job_id, "timeout": _critique_timeout}
                    )
                    critique_result = {"status": "error", "message": f"critique timed out after {_critique_timeout}s"}
                if critique_result.get("status") == "completed":
                    stages_completed.append("critique")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: critique - found {critique_result.get('issues_found', 0)} issues, fixed {critique_result.get('issues_fixed', 0)}")
                    
                    # 4a. Re-run tests if fixes were applied
                    if critique_result.get("issues_fixed", 0) > 0 and payload.get("include_tests", True):
                        logger.info(f"[PIPELINE] Job {job_id} re-running tests after critique fixes")
                        try:
                            testgen_rerun_payload = {
                                "code_path": codegen_result.get("output_path"),
                                "language": detected_language,
                                "run_tests": True,
                                "generate_tests": False,  # Do not regenerate, just run existing tests
                            }
                            testgen_rerun_result = await self._run_testgen(job_id, testgen_rerun_payload)
                            
                            if testgen_rerun_result.get("status") == "completed":
                                # Check if tests now pass
                                test_results = testgen_rerun_result.get("test_results", {})
                                fail_count = test_results.get("failed", 0)
                                pass_count = test_results.get("passed", 0)
                                
                                if fail_count == 0:
                                    logger.info(f"[PIPELINE] Job {job_id} tests PASSED after critique fixes ({pass_count} passed)")
                                    stages_completed.append("testgen:rerun_passed")
                                else:
                                    logger.warning(f"[PIPELINE] Job {job_id} tests still failing after critique fixes ({fail_count} failed, {pass_count} passed)")
                                    stages_completed.append("testgen:rerun_failed")
                            else:
                                logger.warning(f"[PIPELINE] Job {job_id} test re-run failed: {testgen_rerun_result.get('message', 'Unknown error')}")
                        except Exception as rerun_err:
                            logger.error(f"[PIPELINE] Job {job_id} test re-run exception: {rerun_err}", exc_info=True)
                    
                elif critique_result.get("status") == "error":
                    logger.warning(f"[PIPELINE] Job {job_id} failed step: critique - {critique_result.get('message', 'Unknown error')} (continuing pipeline)")
                    # Generate placeholder critique report if critique failed
                    output_path = codegen_result.get("output_path")
                    if output_path:
                        try:
                            output_path_obj = Path(output_path)
                            reports_dir = output_path_obj / "reports"
                            reports_dir.mkdir(parents=True, exist_ok=True)
                            report_path = reports_dir / "critique_report.json"
                            
                            # Only create placeholder if report doesn't exist
                            if not report_path.exists():
                                placeholder_report = _create_placeholder_critique_report(
                                    job_id, "Critique stage failed or was skipped"
                                )
                                report_path.write_text(json.dumps(placeholder_report, indent=2), encoding="utf-8")
                                logger.info(f"[PIPELINE] Job {job_id} generated placeholder critique report at {report_path}")
                        except Exception as e:
                            logger.error(f"[PIPELINE] Job {job_id} failed to generate placeholder critique report: {e}")
            else:
                # Generate placeholder critique report if critique was not requested
                logger.info(f"[PIPELINE] Job {job_id} skipping critique step (run_critique={payload.get('run_critique', True)})")
                output_path = codegen_result.get("output_path")
                if output_path:
                    try:
                        output_path_obj = Path(output_path)
                        reports_dir = output_path_obj / "reports"
                        reports_dir.mkdir(parents=True, exist_ok=True)
                        report_path = reports_dir / "critique_report.json"
                        
                        # Only create placeholder if report doesn't exist
                        if not report_path.exists():
                            placeholder_report = _create_placeholder_critique_report(
                                job_id, "Critique stage was not requested"
                            )
                            report_path.write_text(json.dumps(placeholder_report, indent=2), encoding="utf-8")
                            logger.info(f"[PIPELINE] Job {job_id} generated placeholder critique report (critique not requested) at {report_path}")
                    except Exception as e:
                        logger.error(f"[PIPELINE] Job {job_id} failed to generate placeholder critique report: {e}")
            
            # 5. SFE Analysis (Self-Fixing Engineer)
            # Run deeper AST-level analysis after critique but before deploy
            # This provides more comprehensive defect detection than critique
            sfe_result = None  # Initialize as None for proper None-check later
            if payload.get("run_sfe_analysis", True):
                try:
                    sfe_payload = {
                        "code_path": codegen_result.get("output_path"),
                        "language": detected_language,
                    }
                    logger.info(f"[PIPELINE] Job {job_id} starting step: sfe_analysis")
                    _sfe_timeout = PIPELINE_STEP_TIMEOUTS["sfe_analysis"]
                    try:
                        sfe_result = await asyncio.wait_for(
                            self._run_sfe_analysis(job_id, sfe_payload),
                            timeout=_sfe_timeout,
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            f"[PIPELINE] Step 'sfe_analysis' timed out after {_sfe_timeout}s for job {job_id}",
                            extra={"job_id": job_id, "timeout": _sfe_timeout}
                        )
                        sfe_result = {"status": "error", "message": f"sfe_analysis timed out after {_sfe_timeout}s"}
                    
                    if sfe_result.get("status") == "completed":
                        stages_completed.append("sfe_analysis")
                        logger.info(
                            f"[PIPELINE] Job {job_id} completed step: sfe_analysis - "
                            f"found {sfe_result.get('issues_found', 0)} issues, "
                            f"fixed {sfe_result.get('issues_fixed', 0)}"
                        )
                    elif sfe_result.get("status") == "skipped":
                        stages_completed.append("sfe_analysis:skipped")
                        logger.info(
                            f"[PIPELINE] Job {job_id} skipped SFE analysis: {sfe_result.get('message', 'Components not available')}"
                        )
                    elif sfe_result.get("status") == "error":
                        stages_completed.append("sfe_analysis:error")
                        logger.warning(
                            f"[PIPELINE] Job {job_id} SFE analysis error: {sfe_result.get('message', 'Unknown error')} "
                            f"(continuing pipeline)"
                        )
                except Exception as sfe_err:
                    logger.error(
                        f"[PIPELINE] Job {job_id} SFE analysis exception: {sfe_err}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "stage": "sfe_analysis",
                            "error_type": type(sfe_err).__name__,
                        }
                    )
                    stages_completed.append("sfe_analysis:exception")
                    logger.warning(f"[PIPELINE] Job {job_id} continuing pipeline despite SFE analysis exception")
            else:
                logger.info(f"[PIPELINE] Job {job_id} skipping SFE analysis step (run_sfe_analysis={payload.get('run_sfe_analysis', True)})")
            
            # 6. Await Deploy task (started in parallel earlier)
            # PARALLELIZATION: Deploy was started in parallel with testgen to reduce total pipeline time
            # Now we wait for it to complete before proceeding to docgen
            deploy_result = {}
            if deploy_task:
                logger.info(f"[PIPELINE] Job {job_id} awaiting parallel deploy task completion")
                try:
                    deploy_result = await deploy_task
                    
                    if deploy_result.get("status") == "completed":
                        stages_completed.append("deploy")
                        logger.info(
                            f"[PIPELINE] Job {job_id} completed parallel deploy - "
                            f"targets: {deploy_result.get('completed_targets', [])} - "
                            f"files: {deploy_result.get('generated_files', [])}"
                        )
                        
                        # Run deployment completeness validation
                        logger.info(f"[PIPELINE] Job {job_id} starting deployment validation")
                        try:
                            validation_result = await self._validate_deployment_completeness(
                                job_id, 
                                codegen_result.get("output_path")
                            )
                            
                            if validation_result.get("status") == "failed":
                                logger.error(
                                    f"[PIPELINE] Job {job_id} deployment validation failed - "
                                    f"errors: {validation_result.get('errors', [])} - continuing pipeline"
                                )
                                stages_completed.append("deploy:validation_failed")
                            else:
                                logger.info(f"[PIPELINE] Job {job_id} deployment validation passed")
                                
                        except Exception as e:
                            logger.error(f"[PIPELINE] Job {job_id} deployment validation error: {e}", exc_info=True)
                            # Continue pipeline on validation errors (non-fatal)
                            logger.warning(f"[PIPELINE] Job {job_id} continuing despite validation error")
                            
                    elif deploy_result.get("status") == "error":
                        deploy_error = deploy_result.get('message', 'Unknown error')
                        logger.error(
                            f"[PIPELINE] Job {job_id} parallel deploy failed - {deploy_error}",
                            extra={
                                "job_id": job_id,
                                "error": deploy_error,
                                "failure_type": "generation_error",
                            }
                        )
                        stages_completed.append("deploy:error")
                        logger.warning(f"[PIPELINE] Job {job_id} continuing pipeline despite deploy failure")
                except Exception as e:
                    # Industry Standard: Comprehensive error logging with structured context
                    logger.error(
                        f"[PIPELINE] Job {job_id} parallel deploy await exception: {e}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "stage": "deploy",
                            "error_type": type(e).__name__,
                            "code_path": codegen_result.get("output_path") if codegen_result else None,
                            "failure_type": "exception",
                        }
                    )
                    stages_completed.append("deploy:exception")
                    logger.warning(
                        f"[PIPELINE] Job {job_id} continuing pipeline despite parallel deploy exception",
                        extra={"job_id": job_id, "remaining_stages": ["docgen"]}
                    )
            else:
                logger.info(f"[PIPELINE] Job {job_id} skipping deploy step (include_deployment={include_deployment})")
            
            # 7. Docgen (if requested)
            # RESILIENCE FIX: Pipeline continues even if docgen fails
            # Industry Standard: Documentation generation failure shouldn't prevent
            # code critique, ensuring comprehensive quality analysis
            # FIX: Default to True since documentation is a core pipeline feature
            if payload.get("include_docs", True):
                try:
                    docgen_payload = {
                        "code_path": codegen_result.get("output_path"),
                        "doc_type": payload.get("doc_type", "readme"),  # Honour job-requested doc_type; defaults to README
                        "format": payload.get("doc_format", "markdown"),  # Honour job-requested format; defaults to markdown
                        "output_dir": payload.get("output_dir", ""),  # FIX: Propagate output_dir for consistency
                        "language": detected_language,  # FIX Issue A: Propagate detected language
                    }
                    logger.info(f"[PIPELINE] Job {job_id} starting step: docgen")
                    _docgen_timeout = PIPELINE_STEP_TIMEOUTS["docgen"]
                    try:
                        docgen_result = await asyncio.wait_for(
                            self._run_docgen(job_id, docgen_payload),
                            timeout=_docgen_timeout,
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            f"[PIPELINE] Step 'docgen' timed out after {_docgen_timeout}s for job {job_id}",
                            extra={"job_id": job_id, "timeout": _docgen_timeout}
                        )
                        docgen_result = {"status": "error", "message": f"docgen timed out after {_docgen_timeout}s"}
                    if docgen_result.get("status") == "completed":
                        stages_completed.append("docgen")
                        logger.info(f"[PIPELINE] Job {job_id} completed step: docgen")
                    elif docgen_result.get("status") == "error":
                        logger.error(
                            f"[PIPELINE] Job {job_id} failed step: docgen - {docgen_result.get('message', 'Unknown error')}",
                            extra={
                                "job_id": job_id,
                                "error": docgen_result.get('message'),
                                "failure_type": "generation_error",
                            }
                        )
                        stages_completed.append("docgen:error")
                        logger.warning(f"[PIPELINE] Job {job_id} continuing pipeline despite docgen failure")
                        
                        # Generate fallback README when docgen fails
                        try:
                            output_path = codegen_result.get("output_path")
                            if output_path:
                                output_path_obj = Path(output_path)
                                project_name = _extract_project_name_from_path_or_payload(payload) or "generated_project"
                                
                                # Generate fallback README content
                                fallback_readme = _generate_fallback_readme(
                                    project_name=project_name,
                                    language="python",
                                    output_path=str(output_path_obj)
                                )
                                
                                # Ensure all required README sections are present
                                from generator.main.post_materialize import ensure_readme_sections
                                fallback_readme = ensure_readme_sections(fallback_readme, entry_point="app.main:app")
                                
                                # Write README to the project directory
                                readme_path = output_path_obj / "README.md"
                                readme_path.write_text(fallback_readme, encoding="utf-8")
                                logger.info(
                                    f"[PIPELINE] Job {job_id} generated fallback README at {readme_path}",
                                    extra={"job_id": job_id, "readme_path": str(readme_path)}
                                )
                        except Exception as fallback_err:
                            logger.error(
                                f"[PIPELINE] Job {job_id} fallback README generation failed: {fallback_err}",
                                exc_info=True
                            )
                except Exception as e:
                    # Industry Standard: Structured error logging with full context
                    logger.error(
                        f"[PIPELINE] Job {job_id} docgen exception: {e}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "stage": "docgen",
                            "error_type": type(e).__name__,
                            "code_path": codegen_result.get("output_path") if codegen_result else None,
                            "failure_type": "exception",
                        }
                    )
                    stages_completed.append("docgen:exception")
                    logger.warning(
                        f"[PIPELINE] Job {job_id} continuing pipeline despite docgen exception",
                        extra={"job_id": job_id, "remaining_stages": ["critique"]}
                    )
                    
                    # Generate fallback README when docgen has exception (timeout, etc.)
                    try:
                        output_path = codegen_result.get("output_path")
                        if output_path:
                            output_path_obj = Path(output_path)
                            project_name = _extract_project_name_from_path_or_payload(payload) or "generated_project"
                            
                            # Generate fallback README content
                            fallback_readme = _generate_fallback_readme(
                                project_name=project_name,
                                language="python",
                                output_path=str(output_path_obj)
                            )
                            
                            # Ensure all required README sections are present
                            from generator.main.post_materialize import ensure_readme_sections
                            fallback_readme = ensure_readme_sections(fallback_readme, entry_point="app.main:app")
                            
                            # Write README to the project directory
                            readme_path = output_path_obj / "README.md"
                            readme_path.write_text(fallback_readme, encoding="utf-8")
                            logger.info(
                                f"[PIPELINE] Job {job_id} generated fallback README after exception at {readme_path}",
                                extra={"job_id": job_id, "readme_path": str(readme_path)}
                            )
                    except Exception as fallback_err:
                        logger.error(
                            f"[PIPELINE] Job {job_id} fallback README generation after exception failed: {fallback_err}",
                            exc_info=True
                        )
            
            logger.info(f"[PIPELINE] Pipeline completed successfully for job {job_id}")
            
            output_path = codegen_result.get("output_path")
            
            # FIX Bug 5: Validate deployment artifacts and raise errors for missing required files
            validation_warnings = []
            validation_errors = []
            
            if output_path:
                output_path_obj = Path(output_path)
                
                # Check for required directories based on stages completed
                # Only validate artifacts that were actually requested/generated
                if "deploy" in stages_completed:
                    # Note: Only check for artifacts based on what targets were run
                    # The deploy_all runs docker, kubernetes, and helm
                    
                    # Check for Docker files (always generated)
                    if not (output_path_obj / "Dockerfile").exists():
                        validation_errors.append("Dockerfile not found despite deploy stage completing")
                    
                    # Check for Kubernetes directory and files
                    k8s_dir = output_path_obj / "k8s"
                    if not k8s_dir.exists():
                        validation_errors.append("k8s/ directory not found despite deploy stage completing")
                    else:
                        if not (k8s_dir / "deployment.yaml").exists():
                            validation_errors.append("k8s/deployment.yaml not found")
                        if not (k8s_dir / "service.yaml").exists():
                            validation_errors.append("k8s/service.yaml not found")
                    
                    # Check for Helm directory and files
                    helm_dir = output_path_obj / "helm"
                    if not helm_dir.exists():
                        validation_errors.append("helm/ directory not found despite deploy stage completing")
                    else:
                        if not (helm_dir / "Chart.yaml").exists():
                            validation_errors.append("helm/Chart.yaml not found")
                        if not (helm_dir / "values.yaml").exists():
                            validation_errors.append("helm/values.yaml not found")
                        templates_dir = helm_dir / "templates"
                        if not templates_dir.exists():
                            validation_warnings.append("helm/templates/ directory not found")
                        elif not any(templates_dir.glob("*.yaml")):
                            validation_warnings.append("helm/templates/ directory is empty")
                
                if "docgen" in stages_completed:
                    docs_dir = output_path_obj / "docs"
                    if not docs_dir.exists():
                        validation_warnings.append("docs/ directory not found despite docgen stage completing")
                
                if "critique" in stages_completed:
                    reports_dir = output_path_obj / "reports"
                    if not reports_dir.exists():
                        validation_warnings.append("reports/ directory not found despite critique stage completing")
                    else:
                        if not (reports_dir / "critique_report.json").exists():
                            validation_warnings.append("reports/critique_report.json not found")
                
                # Log warnings if any
                if validation_warnings:
                    logger.warning(
                        f"[PIPELINE] Validation warnings for job {job_id}",
                        extra={
                            "job_id": job_id,
                            "warnings": validation_warnings,
                            "stages_completed": stages_completed
                        }
                    )
                
                # FIX Bug 5: Raise errors for missing deployment artifacts
                if validation_errors:
                    error_msg = f"[PIPELINE] Critical validation errors for job {job_id}: {', '.join(validation_errors)}"
                    logger.error(
                        error_msg,
                        extra={
                            "job_id": job_id,
                            "errors": validation_errors,
                            "stages_completed": stages_completed
                        }
                    )
                    # Update job status to failed
                    if job_id in jobs_db:
                        job = jobs_db[job_id]
                        job.status = JobStatus.FAILED
                        job.error = error_msg
                        job.result = {
                            "error": "Deployment artifacts validation failed",
                            "missing_artifacts": validation_errors,
                            "stages_completed": stages_completed
                        }
                    # Don't raise exception here - just mark job as failed and continue
                    # This allows cleanup to proceed normally
                else:
                    logger.info(f"[PIPELINE] All expected files and directories validated for job {job_id}")

            # Store stages_completed in job metadata for the single finalizer in generator.py
            if job_id in jobs_db:
                job = jobs_db[job_id]
                job.metadata["stages_completed"] = stages_completed
                job.metadata["output_path"] = output_path
                if validation_warnings:
                    job.metadata["validation_warnings"] = validation_warnings
                # Store SFE analysis results for dispatch to SFE (only if analysis ran)
                if sfe_result is not None:
                    job.metadata["sfe_analysis"] = sfe_result

            # Build sfe_feedback summary from SFE results for the pipeline return value
            sfe_feedback: Dict[str, Any] = {}
            if sfe_result and sfe_result.get("status") == "completed":
                _all_defects = sfe_result.get("all_defects", [])
                _issue_counts: Dict[str, int] = {}
                for _d in _all_defects:
                    _sev = _d.get("severity", "unknown").lower()
                    _issue_counts[_sev] = _issue_counts.get(_sev, 0) + 1

                # Collect top actionable recommendations (critical/high issues first)
                _top_issues = [
                    _d for _d in _all_defects
                    if _d.get("severity", "").lower() in ("critical", "high")
                ][:10]

                sfe_feedback = {
                    "issues_found": sfe_result.get("issues_found", 0),
                    "issues_fixed": sfe_result.get("issues_fixed", 0),
                    "issue_counts_by_severity": _issue_counts,
                    "critical_high_count": sfe_result.get("critical_high_count", 0),
                    "top_actionable_recommendations": [
                        {
                            "file": _d.get("file", ""),
                            "line": _d.get("line"),
                            "severity": _d.get("severity", ""),
                            "message": _d.get("message", ""),
                        }
                        for _d in _top_issues
                    ],
                    "files_analyzed": sfe_result.get("files_analyzed", 0),
                }
                if sfe_feedback["critical_high_count"] > 0:
                    logger.warning(
                        "[PIPELINE] SFE found %d critical/high severity issues for job %s — "
                        "review sfe_feedback in pipeline result for details.",
                        sfe_feedback["critical_high_count"],
                        job_id,
                    )

            # Persist the feedback summary in job metadata so it's available to
            # downstream consumers (e.g., the finalizer and SFE dispatch step).
            if sfe_feedback and job_id in jobs_db:
                jobs_db[job_id].metadata["sfe_feedback"] = sfe_feedback

            # NOTE: Do NOT call _finalize_successful_job here.
            # Finalization is handled by finalize_job_success() in generator.py
            # to avoid double-finalization and inconsistent state.

            return {
                "status": "completed",
                "stages_completed": stages_completed,
                "output_path": output_path,
                "validation_warnings": validation_warnings,
                "sfe_analysis": sfe_result if sfe_result is not None else {},  # Include SFE results in pipeline return
                "sfe_feedback": sfe_feedback,  # Summarised SFE findings for callers
            }
            
        except Exception as e:
            logger.error(f"[PIPELINE] Job {job_id} FAILED with exception: {str(e)}", exc_info=True)
            
            # Finalize failed job
            await self._finalize_failed_job(job_id, error=str(e))
            
            return {
                "status": "failed",
                "message": str(e),
                "error_type": type(e).__name__,
            }
        finally:
            # ==========================================================================
            # [NEW] Post-Generation Contract Validation
            # ==========================================================================
            # Validate generated code against spec_lock and contract requirements
            # This runs even if pipeline failed to generate validation reports
            try:
                from generator.main.spec_integration import SpecDrivenPipeline
                
                output_path = codegen_result.get("output_path") if codegen_result else None
                spec_lock_data = payload.get("spec_lock")
                
                if output_path and Path(output_path).exists():
                    logger.info(
                        f"[PIPELINE] Running post-generation validation for job {job_id}",
                        extra={"job_id": job_id, "output_path": output_path}
                    )
                    
                    # Create spec lock object if we have data
                    spec_lock = None
                    if spec_lock_data:
                        from generator.intent_parser.question_loop import SpecLock
                        try:
                            spec_lock = SpecLock(**spec_lock_data)
                        except Exception as e:
                            logger.warning(f"[PIPELINE] Failed to recreate SpecLock: {e}")
                    
                    spec_pipeline = SpecDrivenPipeline(job_id=job_id)
                    validation_report = spec_pipeline.validate_output(
                        output_dir=Path(output_path),
                        spec_lock=spec_lock,
                        language=detected_language
                    )
                    
                    # Save validation report
                    try:
                        reports_dir = Path(output_path) / "reports"
                        reports_dir.mkdir(exist_ok=True)
                        
                        validation_path = reports_dir / "validation_report.json"
                        with open(validation_path, "w") as f:
                            json.dump(validation_report.to_dict(), f, indent=2)
                        
                        validation_text_path = reports_dir / "validation_report.txt"
                        with open(validation_text_path, "w") as f:
                            f.write(validation_report.to_text())
                        
                        logger.info(
                            f"[PIPELINE] Saved validation report to {validation_path}",
                            extra={"job_id": job_id, "valid": validation_report.is_valid()}
                        )
                    except Exception as e:
                        logger.warning(f"[PIPELINE] Failed to save validation report: {e}")
                    
                    # Log validation results
                    if not validation_report.is_valid():
                        logger.warning(
                            f"[PIPELINE] Job {job_id} validation FAILED with {len(validation_report.errors)} errors",
                            extra={
                                "job_id": job_id,
                                "errors": validation_report.errors,
                                "checks_failed": validation_report.checks_failed,
                            }
                        )
                        # Note: We don't fail the job, but log the validation issues
                        # The validation report is available for review
                    else:
                        logger.info(
                            f"[PIPELINE] Job {job_id} validation PASSED",
                            extra={"job_id": job_id, "checks_passed": len(validation_report.checks_passed)}
                        )
                else:
                    logger.debug("[PIPELINE] Skipping validation (no output path or spec_lock)")
                    
            except ImportError:
                logger.debug("[PIPELINE] Validation integration not available")
            except Exception as e:
                logger.warning(
                    f"[PIPELINE] Post-generation validation failed: {e}",
                    exc_info=True
                )
            # ==========================================================================
            
            # FIX: Always remove job from in-progress set
            self._jobs_in_pipeline.discard(job_id)
            logger.debug(f"[PIPELINE] Removed job {job_id} from in-progress set")
    
    async def _finalize_successful_job(
        self, 
        job_id: str, 
        output_path: Optional[str], 
        stages_completed: List[str]
    ) -> None:
        """
        Critical: Update job status to SUCCESS and persist outputs.
        
        This method finalizes a successfully completed job by:
        - Updating job status to COMPLETED
        - Setting completion timestamp
        - Discovering and cataloging output artifacts
        - Creating downloadable ZIP archive
        - Triggering dispatch to Self-Fixing Engineer
        
        Args:
            job_id: Unique job identifier
            output_path: Path to generated output directory
            stages_completed: List of successfully completed pipeline stages
        """
        try:
            if job_id not in jobs_db:
                logger.error(f"✗ Cannot finalize job {job_id}: not found in jobs_db")
                return
            
            job = jobs_db[job_id]
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.metadata.update({
                "stages_completed": stages_completed,
                "output_path": output_path,
            })
            
            # Discover and catalog output artifacts
            if output_path:
                output_dir = Path(output_path)
                if output_dir.exists():
                    # FIX Issue 3: Final enforcement of output layout after all stages complete
                    # This catches any stray files written outside the project subdirectory
                    # Extract project name from job metadata
                    project_name = None
                    if job and job.metadata:
                        project_name = _extract_project_name_from_path_or_payload(
                            job.metadata, default=None
                        )
                    
                    try:
                        if _MATERIALIZER_AVAILABLE:
                            from generator.runner.runner_file_utils import _enforce_output_layout
                            # Only enforce layout when we have an explicit project name AND
                            # the output directory is not already that project subdirectory.
                            # Skipping when project_name is None prevents a spurious
                            # "generated_project/" subdirectory from being created inside an
                            # already-correctly-named directory (e.g. "my_app/generated_project/").
                            layout_result = None
                            if not project_name:
                                logger.debug(
                                    f"[FINALIZE] Could not determine project name for job {job_id}; "
                                    "skipping layout enforcement to avoid creating a spurious subdirectory"
                                )
                            elif output_dir.name == project_name:
                                logger.debug(
                                    f"[FINALIZE] Output path already ends with project name '{project_name}'; "
                                    "skipping layout enforcement to avoid double-nesting"
                                )
                            else:
                                layout_result = _enforce_output_layout(output_dir, project_name)
                            
                            if layout_result is not None and layout_result.get("success"):
                                if layout_result.get("files_moved"):
                                    logger.info(
                                        f"[FINALIZE] Layout enforcement moved {len(layout_result['files_moved'])} items "
                                        f"into {project_name}/ subdirectory",
                                        extra={
                                            "job_id": job_id,
                                            "files_moved": layout_result["files_moved"]
                                        }
                                    )
                                else:
                                    logger.debug(f"[FINALIZE] Layout already correct for job {job_id}")
                            elif layout_result is not None:
                                logger.warning(
                                    f"[FINALIZE] Layout enforcement had errors for job {job_id}: "
                                    f"{layout_result.get('errors', [])}",
                                    extra={"job_id": job_id, "errors": layout_result.get("errors")}
                                )
                        else:
                            logger.debug("[FINALIZE] _enforce_output_layout not available, skipping final layout check")
                    except Exception as layout_err:
                        # Don't fail job finalization if layout enforcement fails
                        logger.warning(f"[FINALIZE] Layout enforcement error for job {job_id}: {layout_err}")
                    
                    artifacts = list(output_dir.rglob('*'))
                    # Exclude existing _output.zip files to avoid nested zips
                    artifact_files = [f for f in artifacts if f.is_file() and not f.name.endswith('_output.zip')]
                    
                    # Generate artifact manifest
                    job.output_files = [f.name for f in artifact_files]
                    
                    # Create downloadable ZIP (in background)
                    zip_path = output_dir.parent / f"{job_id}_output.zip"
                    await self._create_artifact_zip(artifact_files, zip_path, output_dir)
                    
                    logger.info(
                        f"✓ Job {job_id} finalized: status=COMPLETED, files={len(artifact_files)}, "
                        f"stages={', '.join(stages_completed)}"
                    )
                else:
                    logger.warning(f"⚠ Job {job_id} output path {output_path} does not exist")
            
            # Trigger dispatch to Self-Fixing Engineer (non-blocking)
            try:
                # Extract validation context from job metadata
                validation_context = {
                    "validation_errors": job.metadata.get("validation_errors", []),
                    "validation_warnings": job.metadata.get("validation_warnings", []),
                    "stages_completed": stages_completed,
                    "sfe_analysis": job.metadata.get("sfe_analysis", {}),  # Include SFE results
                }
                await self._dispatch_to_sfe(job_id, output_path, validation_context)
            except Exception as dispatch_error:
                # Don't fail job finalization if dispatch fails
                logger.warning(f"⚠ SFE dispatch failed for job {job_id}: {dispatch_error}")
                
        except Exception as e:
            logger.error(f"✗ Failed to finalize successful job {job_id}: {e}", exc_info=True)
            # Don't raise - job is still successful even if finalization has issues
    
    async def _finalize_failed_job(self, job_id: str, error: str) -> None:
        """
        Update job status to FAILED and record error details.
        
        Args:
            job_id: Unique job identifier
            error: Error message describing the failure
        """
        try:
            if job_id not in jobs_db:
                logger.error(f"✗ Cannot finalize failed job {job_id}: not found in jobs_db")
                return
            
            job = jobs_db[job_id]
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            job.metadata.update({
                "error": error,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            })
            
            logger.info(f"✓ Job {job_id} finalized with FAILED status: {error}")
            
        except Exception as e:
            logger.error(f"✗ Failed to finalize failed job {job_id}: {e}", exc_info=True)
    
    async def _create_artifact_zip(
        self, 
        files: List[Path], 
        zip_path: Path,
        base_dir: Path
    ) -> None:
        """
        Bundle all outputs into single downloadable archive.
        
        Args:
            files: List of file paths to include in archive
            zip_path: Path where ZIP file should be created
            base_dir: Base directory for computing relative paths
        """
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in files:
                    try:
                        # [FIX] Add error handling for path resolution in zip archive
                        # Use relative path within archive
                        try:
                            arcname = file_path.resolve().relative_to(base_dir.resolve())
                        except ValueError as e:
                            logger.warning(f"[DOWNLOAD] File {file_path} is outside base_dir {base_dir}, using filename only. Error: {e}")
                            arcname = file_path.name
                        zf.write(file_path, arcname=arcname)
                    except Exception as file_error:
                        logger.warning(f"⚠ Failed to add {file_path} to archive: {file_error}")
            
            logger.info(f"✓ Created artifact archive at {zip_path} with {len(files)} files")
            
        except Exception as e:
            logger.error(f"✗ Failed to create artifact ZIP: {e}", exc_info=True)
            # Don't raise - ZIP creation failure shouldn't fail the job
    
    async def _dispatch_to_sfe(
        self, 
        job_id: str, 
        output_path: Optional[str],
        validation_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Dispatch completed job to Self-Fixing Engineer with fallback.
        
        Tries Kafka first, falls back to direct HTTP if Kafka unavailable.
        
        Args:
            job_id: Unique job identifier
            output_path: Path to generated outputs
            validation_context: Optional validation context with errors, warnings, stages
        """
        try:
            # Import here to avoid circular dependencies
            from server.config import get_server_config
            
            config = get_server_config()
            
            # Try Kafka dispatch if enabled
            if config.kafka_enabled:
                try:
                    # Check if Kafka producer is available
                    if hasattr(self, 'kafka_producer') and self.kafka_producer:
                        sfe_payload = {
                            "job_id": job_id,
                            "output_path": output_path,
                            "validation_context": validation_context or {},
                        }
                        await self.kafka_producer.send(
                            topic="sfe_jobs",
                            value=sfe_payload
                        )
                        logger.info(f"✓ Dispatched job {job_id} to SFE via Kafka")
                        return
                except Exception as kafka_error:
                    logger.warning(f"⚠ Kafka dispatch failed: {kafka_error}, trying fallback")
            
            # Fallback: Direct notification (if SFE URL configured)
            sfe_url = os.getenv("SFE_URL")
            if sfe_url:
                import httpx
                
                sfe_payload = {
                    "job_id": job_id,
                    "source": "omnicore",
                    "output_path": output_path,
                    "validation_context": validation_context or {},
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        f"{sfe_url}/api/jobs",
                        json=sfe_payload
                    )
                    response.raise_for_status()  # Raise exception for 4xx/5xx responses
                logger.info(f"✓ Dispatched job {job_id} to SFE via HTTP fallback (status: {response.status_code})")
            else:
                logger.info(f"ℹ SFE dispatch skipped for job {job_id} (no Kafka or SFE_URL configured)")
                
        except Exception as e:
            logger.warning(f"⚠ Failed to dispatch job {job_id} to SFE: {e}")
            # Don't raise - dispatch failure shouldn't fail the job
    
    async def _configure_llm(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Configure LLM provider."""
        try:
            provider = payload.get("provider", "openai")
            api_key = payload.get("api_key")
            model = payload.get("model")
            
            # Store configuration in environment or config file
            import os
            if api_key:
                env_var = f"{provider.upper()}_API_KEY"
                os.environ[env_var] = api_key
                logger.info(f"Configured API key for {provider}")
            
            return {
                "status": "configured",
                "provider": provider,
                "model": model or "default",
            }
            
        except Exception as e:
            logger.error(f"Error configuring LLM: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
            }

    async def get_plugin_status(self) -> Dict[str, Any]:
        """
        Get status of registered plugins.

        Returns:
            Plugin registry status including active plugins and their metadata

        Example integration:
            >>> # from omnicore_engine import get_plugin_registry
            >>> # registry = get_plugin_registry()
            >>> # plugins = registry.list_plugins()
        """
        logger.debug("Fetching plugin status")

        # Use actual plugin registry if available
        if self._plugin_registry and self._omnicore_components_available["plugin_registry"]:
            try:
                # Get all plugins from registry
                all_plugins = []
                plugin_details = []
                
                # Iterate through plugin kinds
                for kind, plugins_by_name in self._plugin_registry._plugins.items():
                    for name, plugin in plugins_by_name.items():
                        all_plugins.append(name)
                        plugin_details.append({
                            "name": name,
                            "kind": kind,
                            "version": getattr(plugin.meta, "version", "unknown") if hasattr(plugin, "meta") else "unknown",
                            "safe": getattr(plugin.meta, "safe", False) if hasattr(plugin, "meta") else False,
                        })
                
                logger.info(f"Retrieved {len(all_plugins)} plugins from registry")
                
                return {
                    "total_plugins": len(all_plugins),
                    "active_plugins": all_plugins[:10],  # Show first 10
                    "plugin_details": plugin_details,
                    "plugin_registry": "omnicore_engine.plugin_registry.PLUGIN_REGISTRY",
                    "source": "actual",
                }
            except Exception as e:
                logger.error(f"Error querying plugin registry: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock data
        logger.debug("Using fallback plugin status (registry not available)")
        return {
            "total_plugins": 3,
            "active_plugins": ["scenario_plugin", "audit_plugin", "metrics_plugin"],
            "plugin_registry": "omnicore_engine.plugin_registry",
            "source": "fallback",
        }

    async def get_job_metrics(self, job_id: str) -> Dict[str, Any]:
        """
        Get metrics for a specific job.

        Args:
            job_id: Unique job identifier

        Returns:
            Job metrics including processing time, resource usage

        Example integration:
            >>> # from omnicore_engine.metrics import get_job_metrics
            >>> # metrics = await get_job_metrics(job_id)
        """
        logger.debug(f"Fetching metrics for job {job_id}")

        # Use actual metrics client if available
        if self._metrics_client and self._omnicore_components_available["metrics"]:
            try:
                # Try to get actual metrics from Prometheus/InfluxDB
                metrics_data = {
                    "job_id": job_id,
                    "source": "actual",
                }
                
                # Try to get message bus metrics
                try:
                    if hasattr(self._metrics_client, "MESSAGE_BUS_DISPATCH_DURATION"):
                        dispatch_metric = self._metrics_client.MESSAGE_BUS_DISPATCH_DURATION
                        if hasattr(dispatch_metric, "_samples"):
                            # Get recent samples
                            metrics_data["dispatch_latency_samples"] = len(dispatch_metric._samples())
                except Exception:
                    pass
                
                # Try to get API metrics
                try:
                    if hasattr(self._metrics_client, "API_REQUESTS_TOTAL"):
                        requests_metric = self._metrics_client.API_REQUESTS_TOTAL
                        if hasattr(requests_metric, "_value"):
                            metrics_data["api_requests"] = requests_metric._value.get()
                except Exception:
                    pass
                
                logger.info(f"Retrieved actual metrics for job {job_id}")
                return metrics_data
                
            except Exception as e:
                logger.error(f"Error querying metrics: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock metrics
        logger.debug(f"Using fallback metrics for job {job_id} (metrics client not available)")
        return {
            "job_id": job_id,
            "processing_time": 125.5,
            "cpu_usage": 45.2,
            "memory_usage": 512.3,
            "metrics_module": "omnicore_engine.metrics",
            "source": "fallback",
        }

    async def get_audit_trail(
        self, job_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get audit trail for a job.

        Args:
            job_id: Unique job identifier
            limit: Maximum number of audit entries

        Returns:
            List of audit entries with timestamps and actions

        Example integration:
            >>> # from omnicore_engine.audit import get_audit_trail
            >>> # trail = await get_audit_trail(job_id, limit)
        """
        logger.debug(f"Fetching audit trail for job {job_id}")

        # Use actual audit client if available
        if self._audit_client and self._omnicore_components_available["audit"]:
            try:
                # Try to get audit entries from the database
                if hasattr(self._audit_client, "db") and self._audit_client.db:
                    # Query the audit records table
                    try:
                        from sqlalchemy import select, desc
                        from omnicore_engine.database import AuditRecord
                        
                        async with self._audit_client.db.async_session() as session:
                            # Query for audit records matching the job_id
                            stmt = (
                                select(AuditRecord)
                                .where(AuditRecord.name.like(f"%{job_id}%"))
                                .order_by(desc(AuditRecord.timestamp))
                                .limit(limit)
                            )
                            result = await session.execute(stmt)
                            records = result.scalars().all()
                            
                            audit_entries = []
                            for record in records:
                                audit_entries.append({
                                    "timestamp": record.timestamp.isoformat() if hasattr(record.timestamp, "isoformat") else str(record.timestamp),
                                    "action": record.kind,
                                    "name": record.name,
                                    "job_id": job_id,
                                    "module": "omnicore_engine.audit",
                                    "detail": record.detail if hasattr(record, "detail") else {},
                                })
                            
                            logger.info(f"Retrieved {len(audit_entries)} audit entries for job {job_id}")
                            
                            if audit_entries:
                                return audit_entries
                            
                    except ImportError as import_err:
                        logger.debug(f"Could not import audit database models: {import_err}")
                    except Exception as db_err:
                        logger.warning(f"Database query failed: {db_err}")
                
                # If no database entries found or database unavailable, check in-memory buffer
                if hasattr(self._audit_client, "buffer") and self._audit_client.buffer:
                    matching_entries = []
                    for entry in self._audit_client.buffer:
                        if isinstance(entry, dict) and job_id in entry.get("name", ""):
                            matching_entries.append({
                                "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                                "action": entry.get("kind", "unknown"),
                                "name": entry.get("name", ""),
                                "job_id": job_id,
                                "module": "omnicore_engine.audit",
                                "detail": entry.get("detail", {}),
                            })
                    
                    if matching_entries:
                        logger.info(f"Retrieved {len(matching_entries)} buffered audit entries for job {job_id}")
                        return matching_entries[:limit]
                
            except Exception as e:
                logger.error(f"Error querying audit trail: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock audit entry
        logger.debug(f"Using fallback audit trail for job {job_id} (audit client not available)")
        return [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "job_created",
                "job_id": job_id,
                "module": "omnicore_engine",
                "source": "fallback",
            }
        ]

    async def get_system_health(self) -> Dict[str, Any]:
        """
        Get overall system health from OmniCore perspective.

        Returns:
            System health status with component availability

        Example integration:
            >>> # from omnicore_engine.core import get_system_health
            >>> # health = await get_system_health()
        """
        logger.debug("Fetching system health")

        # Build health status from actual component checks
        health_status = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }
        
        # Check message bus health
        if self._message_bus and self._omnicore_components_available["message_bus"]:
            try:
                # Check if message bus is operational
                queue_sizes = []
                for queue in self._message_bus.queues:
                    queue_sizes.append(queue.qsize())
                
                health_status["components"]["message_bus"] = {
                    "status": "operational",
                    "shards": len(self._message_bus.queues),
                    "total_queued": sum(queue_sizes),
                }
            except Exception as e:
                health_status["components"]["message_bus"] = {
                    "status": "degraded",
                    "error": str(e),
                }
                health_status["status"] = "degraded"
        else:
            health_status["components"]["message_bus"] = {
                "status": "unavailable",
            }
        
        # Check plugin registry health
        if self._plugin_registry and self._omnicore_components_available["plugin_registry"]:
            try:
                plugin_count = sum(len(plugins) for plugins in self._plugin_registry._plugins.values())
                health_status["components"]["plugin_registry"] = {
                    "status": "operational",
                    "total_plugins": plugin_count,
                }
            except Exception as e:
                health_status["components"]["plugin_registry"] = {
                    "status": "degraded",
                    "error": str(e),
                }
                health_status["status"] = "degraded"
        else:
            health_status["components"]["plugin_registry"] = {
                "status": "unavailable",
            }
        
        # Check metrics health
        if self._metrics_client and self._omnicore_components_available["metrics"]:
            health_status["components"]["metrics"] = {
                "status": "operational",
            }
        else:
            health_status["components"]["metrics"] = {
                "status": "unavailable",
            }
        
        # Check audit health
        if self._audit_client and self._omnicore_components_available["audit"]:
            try:
                buffer_size = len(self._audit_client.buffer) if hasattr(self._audit_client, "buffer") else 0
                health_status["components"]["audit"] = {
                    "status": "operational",
                    "buffer_size": buffer_size,
                }
            except Exception as e:
                health_status["components"]["audit"] = {
                    "status": "degraded",
                    "error": str(e),
                }
                health_status["status"] = "degraded"
        else:
            health_status["components"]["audit"] = {
                "status": "unavailable",
            }
        
        # Overall status determination
        component_statuses = [c["status"] for c in health_status["components"].values()]
        if all(status == "operational" for status in component_statuses):
            health_status["status"] = "healthy"
        elif any(status == "operational" for status in component_statuses):
            health_status["status"] = "degraded"
        else:
            health_status["status"] = "critical"
        
        return health_status

    async def trigger_workflow(
        self, workflow_name: str, job_id: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Trigger a workflow in OmniCore.

        Args:
            workflow_name: Name of the workflow to trigger
            job_id: Associated job identifier
            params: Workflow parameters

        Returns:
            Workflow execution result

        Example integration:
            >>> # from omnicore_engine.core import trigger_workflow
            >>> # result = await trigger_workflow(name, params)
        """
        logger.info(f"Triggering workflow {workflow_name} for job {job_id}")

        # Placeholder: Trigger actual workflow
        return {
            "workflow_name": workflow_name,
            "job_id": job_id,
            "status": "started",
            "workflow_engine": "omnicore_engine.core",
        }

    async def publish_message(
        self, topic: str, payload: Dict[str, Any], priority: int = 5, ttl: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Publish message to message bus.

        Args:
            topic: Message topic/channel
            payload: Message payload
            priority: Message priority (1-10)
            ttl: Time-to-live in seconds

        Returns:
            Publication result with message_id and status
        """
        logger.info(f"Publishing message to topic {topic}")

        # Use actual message bus if available
        if self._message_bus and self._omnicore_components_available["message_bus"]:
            try:
                # Publish to message bus
                success = await self._message_bus.publish(
                    topic=topic,
                    payload=payload,
                    priority=priority,
                )
                
                if success:
                    logger.info(f"Message published successfully to topic: {topic}")
                    
                    # Generate message ID based on topic and timestamp
                    import time
                    message_id = f"msg_{topic}_{int(time.time() * 1000)}"
                    
                    return {
                        "status": "published",
                        "topic": topic,
                        "message_id": message_id,
                        "priority": priority,
                        "transport": "message_bus",
                    }
                else:
                    logger.warning(f"Failed to publish message to topic: {topic}")
                    return {
                        "status": "failed",
                        "topic": topic,
                        "error": "Message bus publish returned False",
                        "transport": "message_bus",
                    }
                    
            except Exception as e:
                logger.error(f"Error publishing to message bus: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock publication result
        logger.debug(f"Using fallback for message publication to topic: {topic}")
        return {
            "status": "published",
            "topic": topic,
            "message_id": f"msg_{topic}_{hash(str(payload)) % 10000}",
            "priority": priority,
            "transport": "fallback",
        }

    async def emit_event(
        self, topic: str, payload: Dict[str, Any], priority: int = 5
    ) -> Dict[str, Any]:
        """
        Emit an event to the message bus.
        
        This is a convenience alias for publish_message() used by job lifecycle
        event handlers. It delegates to publish_message which handles both
        real message bus publishing and fallback behavior.
        
        Args:
            topic: Event topic/channel (e.g., "job.created", "job.updated")
            payload: Event payload data
            priority: Message priority (1-10, default 5)
        
        Returns:
            Publication result with message_id and status
        """
        return await self.publish_message(topic=topic, payload=payload, priority=priority)

    async def subscribe_to_topic(
        self, topic: str, callback_url: Optional[str] = None, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Subscribe to message bus topic.

        Args:
            topic: Topic to subscribe to
            callback_url: Optional webhook URL
            filters: Message filters

        Returns:
            Subscription result
        """
        logger.info(f"Subscribing to topic {topic}")

        return {
            "status": "subscribed",
            "topic": topic,
            "subscription_id": f"sub_{topic}_{hash(str(callback_url)) % 10000}",
            "callback_url": callback_url,
        }

    async def list_topics(self) -> Dict[str, Any]:
        """
        List all message bus topics.

        Returns:
            Topics and their statistics
        """
        logger.info("Listing message bus topics")

        return {
            "topics": ["generator", "sfe", "audit", "metrics", "notifications"],
            "topic_stats": {
                "generator": {"subscribers": 2, "messages_published": 150},
                "sfe": {"subscribers": 3, "messages_published": 89},
                "audit": {"subscribers": 1, "messages_published": 500},
            },
        }

    async def reload_plugin(self, plugin_id: str, force: bool = False) -> Dict[str, Any]:
        """
        Hot-reload a plugin.

        Args:
            plugin_id: Plugin identifier
            force: Force reload even if errors

        Returns:
            Reload result
        """
        logger.info(f"Reloading plugin {plugin_id}")

        # Placeholder: Actual plugin reload
        # from omnicore_engine.plugin_registry import reload_plugin
        # result = await reload_plugin(plugin_id, force=force)

        return {
            "status": "reloaded",
            "plugin_id": plugin_id,
            "version": "1.0.0",
            "forced": force,
        }

    async def browse_marketplace(
        self, category: Optional[str] = None, search: Optional[str] = None, sort: str = "popularity", limit: int = 20
    ) -> Dict[str, Any]:
        """
        Browse plugin marketplace.

        Args:
            category: Filter by category
            search: Search term
            sort: Sort by field
            limit: Max results

        Returns:
            Plugin listings
        """
        logger.info("Browsing plugin marketplace")

        return {
            "plugins": [
                {
                    "plugin_id": "security_scanner",
                    "name": "Security Scanner",
                    "version": "2.1.0",
                    "category": "security",
                    "downloads": 1500,
                    "rating": 4.8,
                },
                {
                    "plugin_id": "performance_optimizer",
                    "name": "Performance Optimizer",
                    "version": "1.5.0",
                    "category": "optimization",
                    "downloads": 980,
                    "rating": 4.6,
                },
            ],
            "total": 2,
            "filters": {"category": category, "search": search, "sort": sort},
        }

    async def install_plugin(
        self, plugin_name: str, version: Optional[str] = None, source: str = "marketplace", config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Install a plugin.

        Args:
            plugin_name: Plugin name
            version: Specific version
            source: Installation source
            config: Plugin configuration

        Returns:
            Installation result
        """
        logger.info(f"Installing plugin {plugin_name}")

        return {
            "status": "installed",
            "plugin_name": plugin_name,
            "version": version or "latest",
            "source": source,
        }

    async def query_database(
        self, query_type: str, filters: Optional[Dict[str, Any]] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """
        Query OmniCore database.

        Args:
            query_type: Query type (jobs, audit, metrics)
            filters: Query filters
            limit: Max results

        Returns:
            Query results
        """
        logger.info(f"Querying database: {query_type}")

        # Placeholder: Actual database query
        # from omnicore_engine.database import query_state
        # results = await query_state(query_type, filters, limit)

        return {
            "query_type": query_type,
            "results": [{"id": "example", "data": {}}],
            "count": 1,
            "filters": filters,
        }

    async def export_database(
        self, export_type: str = "full", format: str = "json", include_audit: bool = True
    ) -> Dict[str, Any]:
        """
        Export database state.

        Args:
            export_type: Export type (full, incremental)
            format: Export format (json, csv, sql)
            include_audit: Include audit logs

        Returns:
            Export result with download path
        """
        logger.info(f"Exporting database: {export_type}")

        return {
            "status": "exported",
            "export_type": export_type,
            "format": format,
            "export_path": f"/exports/omnicore_export_{export_type}.{format}",
            "size_bytes": 1024000,
        }

    async def get_circuit_breakers(self) -> Dict[str, Any]:
        """
        Get status of all circuit breakers.

        Returns:
            Circuit breaker statuses
        """
        logger.info("Fetching circuit breaker statuses")

        return {
            "circuit_breakers": [
                {
                    "name": "generator_service",
                    "state": "closed",
                    "failure_count": 0,
                    "last_failure_time": None,
                },
                {
                    "name": "sfe_service",
                    "state": "closed",
                    "failure_count": 0,
                    "last_failure_time": None,
                },
            ],
            "total": 2,
        }

    async def reset_circuit_breaker(self, name: str) -> Dict[str, Any]:
        """
        Reset a circuit breaker.

        Args:
            name: Circuit breaker name

        Returns:
            Reset result
        """
        logger.info(f"Resetting circuit breaker {name}")

        return {
            "status": "reset",
            "name": name,
            "state": "closed",
            "failure_count": 0,
        }

    async def configure_rate_limit(
        self, endpoint: str, requests_per_second: float, burst_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Configure rate limits.

        Args:
            endpoint: Endpoint to limit
            requests_per_second: Requests per second
            burst_size: Burst capacity

        Returns:
            Configuration result
        """
        logger.info(f"Configuring rate limit for {endpoint}")

        return {
            "status": "configured",
            "endpoint": endpoint,
            "requests_per_second": requests_per_second,
            "burst_size": burst_size or int(requests_per_second * 2),
        }

    async def query_dead_letter_queue(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        topic: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Query dead letter queue.

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            topic: Filter by topic
            limit: Max results

        Returns:
            Failed messages
        """
        logger.info("Querying dead letter queue")

        return {
            "messages": [
                {
                    "message_id": "msg_123",
                    "topic": topic or "generator",
                    "failure_reason": "timeout",
                    "attempts": 3,
                    "timestamp": "2026-01-20T01:00:00Z",
                }
            ],
            "count": 1,
            "filters": {"topic": topic, "start_time": start_time, "end_time": end_time},
        }

    async def retry_message(self, message_id: str, force: bool = False) -> Dict[str, Any]:
        """
        Retry failed message from dead letter queue.

        Args:
            message_id: Message ID to retry
            force: Force retry even if max attempts reached

        Returns:
            Retry result
        """
        logger.info(f"Retrying message {message_id}")

        return {
            "status": "retried",
            "message_id": message_id,
            "attempt": 4,
            "forced": force,
        }
    
    def _get_clarification_feedback(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Get feedback from clarification session."""
        session = _clarification_sessions.get(job_id)
        
        if not session:
            return {
                "status": "not_found",
                "message": f"No clarification session found for job {job_id}",
            }
        
        # If all questions answered, generate clarified requirements
        if len(session["answers"]) == len(session["questions"]):
            return self._generate_clarified_requirements(session)
        
        return {
            "status": "in_progress",
            "job_id": job_id,
            "total_questions": len(session["questions"]),
            "answered_questions": len(session["answers"]),
            "answers": session["answers"],
        }
    
    def _submit_clarification_response(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submit answer to clarification question."""
        session = _clarification_sessions.get(job_id)
        
        if not session:
            return {
                "status": "error",
                "message": f"No clarification session found for job {job_id}",
            }
        
        question_id = payload.get("question_id", "")
        response = payload.get("response", "")
        
        # Bug 5 Fix: Allow question_id without response (for skip/empty answers)
        if not question_id:
            return {
                "status": "error",
                "message": "question_id is required",
            }
        
        # Store the answer - use "[SKIPPED]" marker for empty/skip responses
        if not response or response.strip() == "":
            session["answers"][question_id] = "[SKIPPED]"
            logger.info(f"Question {question_id} skipped for job {job_id}")
        else:
            session["answers"][question_id] = response
            logger.info(f"Stored answer for {job_id}, question {question_id}")
        
        session["updated_at"] = datetime.now().isoformat()
        
        # Check if all questions answered (including skipped ones)
        if len(session["answers"]) >= len(session["questions"]):
            session["status"] = "completed"
            return {
                "status": "completed",
                "job_id": job_id,
                "message": "All questions answered",
                "clarified_requirements": self._generate_clarified_requirements(session),
            }
        
        return {
            "status": "answer_recorded",
            "job_id": job_id,
            "remaining_questions": len(session["questions"]) - len(session["answers"]),
        }
    
    def _generate_clarified_requirements(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """Generate clarified requirements from answers."""
        requirements = {
            "original_requirements": session["requirements"],
            "clarified_requirements": {},
        }
        
        # Map answers to clarified requirements
        for question_id, answer in session["answers"].items():
            # Extract question index
            q_idx = int(question_id.replace("q", "")) - 1
            if q_idx < len(session["questions"]):
                question = session["questions"][q_idx]
                
                # Handle both dict format (new rule-based) and string format (legacy/LLM)
                if isinstance(question, dict):
                    q_text = question.get("question", "")
                    q_category = question.get("category", "")
                    
                    # Use category if available, otherwise fall back to text matching
                    if q_category:
                        requirements["clarified_requirements"][q_category] = answer
                    else:
                        q_lower = q_text.lower()
                        self._categorize_answer(requirements, q_lower, answer)
                else:
                    # Legacy string format
                    q_lower = str(question).lower()
                    self._categorize_answer(requirements, q_lower, answer)
                    requirements["clarified_requirements"][f"answer_{q_idx + 1}"] = answer
        
        requirements["confidence"] = 0.95  # High confidence after clarification
        requirements["status"] = "clarified"
        
        return requirements
    
    def _categorize_answer(self, requirements: Dict[str, Any], q_lower: str, answer: str) -> None:
        """Helper method to categorize answers based on question text."""
        if "database" in q_lower:
            requirements["clarified_requirements"]["database"] = answer
        elif "auth" in q_lower or "login" in q_lower:
            requirements["clarified_requirements"]["authentication"] = answer
        elif "api" in q_lower:
            requirements["clarified_requirements"]["api_type"] = answer
        elif "frontend" in q_lower or "framework" in q_lower:
            requirements["clarified_requirements"]["frontend_framework"] = answer
        elif "deploy" in q_lower or "platform" in q_lower:
            requirements["clarified_requirements"]["deployment_platform"] = answer
        elif "test" in q_lower:
            requirements["clarified_requirements"]["testing_strategy"] = answer
        elif "performance" in q_lower:
            requirements["clarified_requirements"]["performance_requirements"] = answer
        elif "security" in q_lower:
            requirements["clarified_requirements"]["security_requirements"] = answer
        elif "language" in q_lower:
            requirements["clarified_requirements"]["programming_language"] = answer
        elif "user" in q_lower:
            requirements["clarified_requirements"]["target_users"] = answer
        elif "integration" in q_lower:
            requirements["clarified_requirements"]["third_party_integrations"] = answer


# Module-level singleton for OmniCoreService
_instance: Optional["OmniCoreService"] = None
_instance_lock = threading.Lock()
_async_instance_lock: Optional[asyncio.Lock] = None
_async_lock_creation_lock = threading.Lock()


def _get_async_lock() -> Optional[asyncio.Lock]:
    """Get or create async lock for current event loop (thread-safe)."""
    global _async_instance_lock
    if _async_instance_lock is None:
        with _async_lock_creation_lock:  # Protect lock creation from race conditions
            if _async_instance_lock is None:
                try:
                    asyncio.get_running_loop()
                    _async_instance_lock = asyncio.Lock()
                except RuntimeError:
                    return None
    return _async_instance_lock


def get_omnicore_service() -> OmniCoreService:
    """
    Get or create the singleton OmniCoreService instance (sync-safe).
    
    This function implements a thread-safe singleton pattern to ensure
    only one OmniCoreService instance is created, preventing multiple
    initializations of resources (database pools, Kafka producers, etc.).
    
    Returns:
        OmniCoreService: The singleton OmniCore service instance
        
    Example:
        >>> from fastapi import Depends
        >>> @router.post("/endpoint")
        >>> async def handler(service: OmniCoreService = Depends(get_omnicore_service)):
        ...     result = await service.route_job(...)
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = OmniCoreService()
    return _instance


async def get_omnicore_service_async() -> OmniCoreService:
    """
    Get or create the singleton OmniCoreService instance (async-safe).
    
    This function implements an asyncio-safe singleton pattern for use in
    async contexts, preventing event loop blocking from threading locks.
    
    Returns:
        OmniCoreService: The singleton OmniCore service instance
        
    Example:
        >>> service = await get_omnicore_service_async()
        >>> result = await service.route_job(...)
    """
    global _instance
    if _instance is None:
        lock = _get_async_lock()
        if lock:
            async with lock:
                if _instance is None:
                    _instance = OmniCoreService()
        else:
            # Fallback to sync if no event loop
            return get_omnicore_service()
    return _instance
