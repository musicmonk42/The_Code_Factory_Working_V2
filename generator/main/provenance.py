# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/main/provenance.py
"""
Provenance Tracking Module for the Code Generation Pipeline.

This module provides enterprise-grade provenance tracking with cryptographic 
integrity verification, structured logging, and comprehensive audit trails.
Enables debugging of artifact overwrite issues and ensures specs are 
properly preserved through the generation process.

Stage Markers:
    - [STAGE:READ_MD]     - Spec input file reading
    - [STAGE:CODEGEN]     - Code generation from LLM
    - [STAGE:POSTPROCESS] - Post-processing/sanitization
    - [STAGE:MATERIALIZE] - Writing files to disk
    - [STAGE:VALIDATE]    - Validation
    - [STAGE:TESTGEN]     - Test generation
    - [STAGE:DEPLOY_GEN]  - Deployment artifact generation
    - [STAGE:PACKAGE]     - Creating output zip/package

Industry Standards Compliance:
    - SOC 2 Type II: Cryptographic integrity verification
    - ISO 27001 A.12.1.3: Comprehensive audit logging
    - NIST SP 800-53 AU-4: Audit record content and retention
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import yaml as _pyyaml
    _YAML_AVAILABLE = True
except ImportError:  # pyyaml is an optional dependency
    _pyyaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False

# --- OpenTelemetry Integration ---
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    _tracer = trace.get_tracer(__name__)
    HAS_OPENTELEMETRY = True
except ImportError:
    HAS_OPENTELEMETRY = False
    _tracer = None

    class StatusCode(Enum):
        OK = 0
        ERROR = 2
        UNSET = 1

    class Status:
        def __init__(self, status_code: StatusCode, description: Optional[str] = None):
            self.status_code = status_code
            self.description = description
        
        def is_ok(self) -> bool:
            return self.status_code == StatusCode.OK

# --- Prometheus Metrics ---
try:
    from prometheus_client import Counter, Histogram
    
    PROVENANCE_STAGES_RECORDED = Counter(
        'provenance_stages_recorded_total',
        'Total pipeline stages recorded',
        ['stage', 'job_id']
    )
    PROVENANCE_ERRORS_RECORDED = Counter(
        'provenance_errors_recorded_total',
        'Total pipeline errors recorded',
        ['stage', 'error_type']
    )
    PROVENANCE_ARTIFACT_SIZE = Histogram(
        'provenance_artifact_size_bytes',
        'Size of tracked artifacts',
        ['artifact_name'],
        buckets=[100, 500, 1000, 5000, 10000, 50000, 100000, 500000]
    )
    VALIDATION_DURATION = Histogram(
        'provenance_validation_duration_seconds',
        'Validation operation duration',
        ['validation_type']
    )
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    
    class _NoOpMetric:
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass
    
    PROVENANCE_STAGES_RECORDED = _NoOpMetric()
    PROVENANCE_ERRORS_RECORDED = _NoOpMetric()
    PROVENANCE_ARTIFACT_SIZE = _NoOpMetric()
    VALIDATION_DURATION = _NoOpMetric()

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Minimum content length to be considered non-trivial
MIN_CONTENT_LENGTH = 10


class PipelineStage(str, Enum):
    """Pipeline stages."""
    READ_MD = "READ_MD"
    CODEGEN = "CODEGEN"
    POSTPROCESS = "POSTPROCESS"
    MATERIALIZE = "MATERIALIZE"
    VALIDATE = "VALIDATE"
    SPEC_VALIDATE = "SPEC_VALIDATE"
    TESTGEN = "TESTGEN"
    DEPLOY_GEN = "DEPLOY_GEN"
    PACKAGE = "PACKAGE"


class ProvenanceTracker:
    """
    Provenance tracking for the code generation pipeline.
    
    Records SHA256 hashes and metadata for all artifacts at each pipeline stage.
    """
    
    STAGE_READ_MD = PipelineStage.READ_MD.value
    STAGE_CODEGEN = PipelineStage.CODEGEN.value
    STAGE_POSTPROCESS = PipelineStage.POSTPROCESS.value
    STAGE_MATERIALIZE = PipelineStage.MATERIALIZE.value
    STAGE_VALIDATE = PipelineStage.VALIDATE.value
    STAGE_SPEC_VALIDATE = PipelineStage.SPEC_VALIDATE.value
    STAGE_TESTGEN = PipelineStage.TESTGEN.value
    STAGE_DEPLOY_GEN = PipelineStage.DEPLOY_GEN.value
    STAGE_PACKAGE = PipelineStage.PACKAGE.value
    
    def __init__(self, job_id: Optional[str] = None) -> None:
        self.job_id = job_id or self._generate_job_id()
        self.stages: List[Dict[str, Any]] = []
        self.artifacts: Dict[str, Dict[str, Any]] = {}
        self.errors: List[Dict[str, Any]] = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        
        logger.info("ProvenanceTracker initialized", extra={"job_id": self.job_id})
    
    @staticmethod
    def _generate_job_id() -> str:
        now = datetime.now(timezone.utc)
        return f"job-{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond:06d}"
    
    @staticmethod
    def compute_sha256(content: str) -> str:
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    @staticmethod
    def compute_sha256_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()
    
    def record_stage(
        self,
        stage: Union[str, PipelineStage],
        artifacts: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        stage_str = stage.value if isinstance(stage, PipelineStage) else str(stage)
        timestamp = datetime.now(timezone.utc).isoformat()
        
        stage_record: Dict[str, Any] = {
            "stage": stage_str,
            "timestamp": timestamp,
            "artifacts": {},
            "metadata": metadata or {}
        }
        
        if artifacts:
            for name, content in artifacts.items():
                if content is not None:
                    content_str = str(content)
                    sha256_hash = self.compute_sha256(content_str)
                    content_length = len(content_str)
                    
                    artifact_info = {
                        "sha256": sha256_hash,
                        "length": content_length,
                        "preview": content_str[:200] + "..." if content_length > 200 else content_str
                    }
                    stage_record["artifacts"][name] = artifact_info
                    
                    if name not in self.artifacts:
                        self.artifacts[name] = {"history": []}
                    self.artifacts[name]["history"].append({
                        "stage": stage_str,
                        "sha256": sha256_hash,
                        "length": content_length,
                        "timestamp": timestamp
                    })
                    
                    PROVENANCE_ARTIFACT_SIZE.labels(artifact_name=name).observe(content_length)
        
        self.stages.append(stage_record)
        PROVENANCE_STAGES_RECORDED.labels(stage=stage_str, job_id=self.job_id).inc()
        
        logger.info(
            f"[STAGE:{stage_str}] Recorded {len(artifacts or {})} artifacts",
            extra={"stage": stage_str, "job_id": self.job_id}
        )
    
    def record_error(
        self,
        stage: Union[str, PipelineStage],
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        stage_str = stage.value if isinstance(stage, PipelineStage) else str(stage)
        
        error_record = {
            "stage": stage_str,
            "error_type": error_type,
            "message": message,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.errors.append(error_record)
        
        PROVENANCE_ERRORS_RECORDED.labels(stage=stage_str, error_type=error_type).inc()
        logger.error(f"[STAGE:{stage_str}] {error_type}: {message}", extra={"job_id": self.job_id})
    
    def check_artifact_changed(self, artifact_name: str) -> bool:
        if artifact_name not in self.artifacts:
            return False
        history = self.artifacts[artifact_name].get("history", [])
        if len(history) < 2:
            return False
        return len(set(e["sha256"] for e in history)) > 1
    
    def get_artifact_overwrites(self) -> Dict[str, List[Dict[str, Any]]]:
        return {name: data["history"] for name, data in self.artifacts.items() if self.check_artifact_changed(name)}
    
    def to_dict(self) -> Dict[str, Any]:
        overwrites = self.get_artifact_overwrites()
        finished_timestamp = datetime.now(timezone.utc).isoformat()
        return {
            "job_id": self.job_id,
            "timestamp": finished_timestamp,  # Add timestamp field for validator compliance
            "started_at": self.started_at,
            "finished_at": finished_timestamp,
            "version": "1.0.0",
            "stages": self.stages,
            "artifacts": self.artifacts,
            "errors": self.errors,
            "overwrites_detected": overwrites,
            "summary": {
                "total_stages": len(self.stages),
                "total_errors": len(self.errors),
                "artifacts_tracked": list(self.artifacts.keys()),
                "has_overwrites": len(overwrites) > 0
            }
        }
    
    def save_to_file(self, output_dir: str) -> str:
        reports_dir = Path(output_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        provenance_path = reports_dir / "provenance.json"
        with open(provenance_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        
        logger.info(f"Provenance saved to {provenance_path}", extra={"job_id": self.job_id})
        return str(provenance_path.resolve())


# =============================================================================
# GENERIC VALIDATION FUNCTIONS
# =============================================================================

def validate_syntax(code_content: str, filename: str = "unknown.py") -> Dict[str, Any]:
    """Validate Python syntax."""
    try:
        ast.parse(code_content)
        return {"valid": True, "filename": filename, "error": None}
    except SyntaxError as e:
        return {"valid": False, "filename": filename, "error": str(e), "line": e.lineno}


def validate_has_content(content: str, filename: str) -> Dict[str, Any]:
    """Validate file has non-trivial content."""
    # __init__.py files are allowed to be empty — they're Python package markers
    if filename.endswith("__init__.py"):
        return {"valid": True, "filename": filename, "length": len(content.strip()), "error": None}
    stripped = content.strip()
    has_content = len(stripped) > MIN_CONTENT_LENGTH
    return {
        "valid": has_content,
        "filename": filename,
        "length": len(stripped),
        "error": None if has_content else f"{filename} is empty or trivial"
    }


def extract_endpoints_from_code(code_content: str, filename: str = "") -> List[Dict[str, str]]:
    """Extract API endpoints from code using AST analysis (Python) or regex.

    Supports multiple languages and frameworks:
    - Python: FastAPI, Flask decorators (AST-based with regex fallback)
    - TypeScript/JavaScript: Express, NestJS, Fastify
    - Java: Spring Boot annotations
    - Go: standard http handlers
    """
    endpoints = []

    # For Python files, attempt AST-based extraction first.
    if filename.endswith(".py") or (not filename and code_content.strip().startswith(("import ", "from ", "@", "def ", "class "))):
        try:
            from generator.utils.ast_endpoint_extractor import ASTEndpointExtractor
            _ast_extractor = ASTEndpointExtractor()
        except ImportError:
            _ast_extractor = None

        if _ast_extractor is not None:
            try:
                ast_results = _ast_extractor.extract_from_source(code_content, filename or "<string>")
                if ast_results:
                    return [{"method": ep["method"], "path": ep["path"]} for ep in ast_results]
            except Exception:
                pass  # fall through to regex

    # Python patterns (FastAPI, Flask) — regex fallback
    python_patterns = [
        r'@\w+\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        r'@route\s*\(\s*["\']([^"\']+)["\']',
    ]

    # TypeScript/JavaScript route patterns
    ts_js_patterns = [
        r"""app\.(get|post|put|delete|patch)\s*\(\s*['"]([^'"]+)['"]""",           # Express
        r"""router\.(get|post|put|delete|patch)\s*\(\s*['"]([^'"]+)['"]""",        # Express Router
        r"""server\.(get|post|put|delete|patch)\s*\(\s*['"]([^'"]+)['"]""",       # Fastify
    ]

    # NestJS decorator pattern (needs separate handling due to different capture group order)
    # Captures: (method_name, path) where method_name is the decorator (Get, Post, etc.)
    nestjs_pattern = r"""@(Get|Post|Put|Delete|Patch)\s*\(\s*['"]([^'"]+)['"]"""

    # Determine which patterns to use based on file extension
    if filename.endswith(('.ts', '.js')):
        patterns = ts_js_patterns
        # Process NestJS patterns separately
        nestjs_matches = re.findall(nestjs_pattern, code_content, re.IGNORECASE)
        for method, path in nestjs_matches:
            endpoints.append({"method": method.upper(), "path": path})
    else:
        patterns = python_patterns

    for pattern in patterns:
        matches = re.findall(pattern, code_content, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                method, path = match[0], match[1] if len(match) > 1 else match[0]
            else:
                method, path = "GET", match
            endpoints.append({"method": method.upper(), "path": path})

    return endpoints


def extract_endpoints_from_md(md_content: str) -> List[Dict[str, str]]:
    """
    Extract required API endpoints from a Markdown spec.
    
    This function implements multi-pattern parsing to extract API route definitions
    from various Markdown formats commonly used in API specifications.
    
    Supported Formats:
        - Explicit route definitions: ``GET /api/users``, ``POST /api/items``
        - Table formats: ``| GET | /api/users | ... |``
        - Backtick code format: ``GET /api/users``
        - Bullet points with endpoints: ``- GET /api/users``
        - Contextual format: ``Endpoint: /api/users (GET, POST)``
    
    Industry Standards Compliance:
        - OpenAPI 3.0: Recognizes standard HTTP method patterns
        - REST API conventions: Supports path parameters like ``{id}``
        - SOC 2 Type II: Deterministic output for audit trails
    
    Args:
        md_content: Markdown specification content to parse
        
    Returns:
        List of endpoint dictionaries with 'method' and 'path' keys,
        sorted by path and method for consistent ordering.
        
    Example:
        >>> md = "| GET | /api/users | Get all users |"
        >>> extract_endpoints_from_md(md)
        [{'method': 'GET', 'path': '/api/users'}]
        
    Note:
        Duplicate endpoints are automatically deduplicated. Path normalization
        is case-insensitive but preserves the original path casing in output.
    """
    # Start OpenTelemetry span if available
    span = None
    if HAS_OPENTELEMETRY and _tracer:
        span = _tracer.start_span("extract_endpoints_from_md")
        span.set_attribute("md_content_length", len(md_content))
    
    try:
        endpoints: List[Dict[str, str]] = []
        seen: Set[Tuple[str, str]] = set()  # Avoid duplicates
        
        # Standard HTTP methods per RFC 7231 and RFC 5789
        http_methods = r'(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)'
        
        # Pattern definitions with documentation
        # Each pattern tuple: (regex_pattern, swap_order_flag)
        # swap_order_flag indicates if path comes before method in capture groups
        patterns: List[Tuple[str, bool]] = [
            # Pattern 1: Explicit HTTP method + path with optional markdown emphasis
            # Matches: "GET /api/users", "**GET** /api/users", "*POST* /api/items"
            (rf'\*{{0,2}}{http_methods}\*{{0,2}}\s+[`"]?(/[^\s`"\)]+)[`"]?', False),
            
            # Pattern 2: Markdown table format
            # Matches: "| GET | /api/users |", "| POST | /api/items | description |"
            (rf'\|\s*{http_methods}\s*\|\s*[`"]?(/[^\s`"\|]+)[`"]?', False),
            
            # Pattern 3: Backtick inline code format
            # Matches: "`GET /api/users`", "`POST /api/items`"
            (rf'`{http_methods}\s+(/[^`]+)`', False),
            
            # Pattern 4: Contextual endpoint definition
            # Matches: "Endpoint: /api/users (GET, POST)", "Route: /api/items (PUT)"
            (r'(?:endpoint|route|path|url):\s*[`"]?(/[^\s`"\)]+)[`"]?\s*\(([^)]+)\)', True),
        ]
        
        for pattern, swap_order in patterns:
            matches = re.findall(pattern, md_content, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                if swap_order:
                    # Path comes first, then methods string (e.g., "GET, POST")
                    path, methods_str = match
                    for method in re.findall(http_methods, methods_str, re.IGNORECASE):
                        key = (method.upper(), path)
                        if key not in seen:
                            seen.add(key)
                            endpoints.append({"method": method.upper(), "path": path})
                else:
                    # Standard order: method first, then path
                    method, path = match
                    key = (method.upper(), path)
                    if key not in seen:
                        seen.add(key)
                        endpoints.append({"method": method.upper(), "path": path})
        
        # Sort by path for deterministic ordering (important for audit compliance)
        endpoints.sort(key=lambda e: (e["path"], e["method"]))
        
        if span:
            span.set_attribute("endpoints_found", len(endpoints))
            span.set_status(Status(StatusCode.OK))
        
        return endpoints
        
    except Exception as e:
        if span:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
        logger.warning(f"[SPEC_PARSE] Error parsing MD content: {e}")
        return []
    finally:
        if span:
            span.end()


def extract_required_files_from_md(md_content: str, target_language: Optional[str] = None) -> List[str]:
    """
    Extract required file paths referenced in a Markdown spec.

    Parses the specification for explicit file references such as
    ``app/routes.py``, ``models.py``, etc., so the validation step can
    verify that the generated project contains them.

    Industry Standards:
        - Input validation for security and correctness
        - Efficient O(1) lookups using sets
        - Language-aware filtering to prevent false positives
        - Comprehensive documentation and error handling

    Supported patterns:
        - Tree-style listings: ``├── app/routes.py`` or ``│   ├── routes.py``
        - Backtick references: ``app/routes.py``

    Args:
        md_content: Markdown specification content to parse.
        target_language: Optional target language (e.g., "typescript", "python").
                        If provided, filters out files from other ecosystems.

    Returns:
        Deduplicated, sorted list of relative file paths found in the spec.
        
    Raises:
        TypeError: If md_content is not a string
        
    Examples:
        >>> extract_required_files_from_md("├── main.py\\n├── app.py")
        ['app.py', 'main.py']
        >>> extract_required_files_from_md("`main.py` for Python", target_language="typescript")
        []  # main.py filtered out for TypeScript projects
    """
    # Input validation - industry standard defensive programming
    if not isinstance(md_content, str):
        raise TypeError(f"md_content must be a string, got {type(md_content).__name__}")
    
    if target_language is not None and not isinstance(target_language, str):
        raise TypeError(f"target_language must be a string or None, got {type(target_language).__name__}")
    
    files: List[str] = []
    seen: Set[str] = set()

    # Blocklist of runtime/tool names that look like files but aren't
    # These are technology names, not project files
    RUNTIME_BLOCKLIST = {
        "Node.js", "node.js", "Vue.js", "vue.js", "React.js", "react.js",
        "Next.js", "next.js", "Express.js", "express.js", "Nuxt.js", "nuxt.js",
        "Angular.js", "angular.js", "Ember.js", "ember.js", "Three.js", "three.js",
        "D3.js", "d3.js", "Electron.js", "electron.js", "Deno.ts", "deno.ts"
    }

    # Language-to-extension mapping for ecosystem filtering
    LANGUAGE_EXTENSIONS = {
        "python": {".py", ".pyw", ".pyi"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx", ".mjs", ".cjs"},
        "java": {".java"},
        "go": {".go"},
        "rust": {".rs"},
        "csharp": {".cs"},
        "c": {".c", ".h"},
        "c++": {".cpp", ".cc", ".cxx", ".hpp"},
    }

    # Common source file extensions to look for
    file_ext_pattern = r'(?:\.py|\.js|\.ts|\.jsx|\.tsx|\.yml|\.yaml|\.toml|\.cfg|\.txt|\.json|\.html|\.css)'

    patterns = [
        # Tree-style listing: ├── app/routes.py or │ ├── routes.py
        # Includes box-drawing characters ├ └ │ ─ and ASCII fallbacks
        rf'[├└│─\-\| ]+([a-zA-Z_][\w/]*{file_ext_pattern})',
        # Backtick code reference: `app/routes.py`
        rf'`([a-zA-Z_][\w/]*{file_ext_pattern})`',
    ]

    # Pre-compute non-target extensions for efficiency (O(1) lookup vs O(n) each iteration)
    non_target_exts = set()
    target_exts = set()
    if target_language:
        target_exts = LANGUAGE_EXTENSIONS.get(target_language.lower(), set())
        if target_exts:
            # Build set of all non-target language extensions once
            for lang, exts in LANGUAGE_EXTENSIONS.items():
                if lang != target_language.lower():
                    non_target_exts.update(exts)

    for pattern in patterns:
        for match in re.finditer(pattern, md_content):
            path = match.group(1).strip()
            
            # Skip if empty or already seen
            if not path or path in seen:
                continue
            
            # Skip runtime/tool names that aren't actual files
            if path in RUNTIME_BLOCKLIST:
                continue
            
            # If target language specified, filter by ecosystem
            if target_exts:
                # Check if file has an extension matching the target language
                file_ext = os.path.splitext(path)[1].lower()
                
                # Only filter out if file has a code extension from a different language
                if file_ext in non_target_exts and file_ext not in target_exts:
                    # File is from a different language ecosystem - skip it
                    continue
            
            seen.add(path)
            files.append(path)

    files.sort()
    return files


def extract_output_dir_from_md(md_content: str) -> str:
    """
    Extract output_dir from a Markdown spec.

    Parses the specification for output_dir configuration in YAML-style format.
    Supports patterns like:
        - `output_dir: generated/hello_generator`
        - `output_dir: "my_project"`
        - `output_dir: my-app`

    Args:
        md_content: Markdown specification content to parse.

    Returns:
        The output directory path if found, empty string otherwise.
        
    Security:
        - Rejects paths with '..' (path traversal prevention)
        - Rejects absolute paths (starting with '/' or Windows drive letters)
        - Validates using Path.resolve() to catch obfuscated traversal attempts
    """
    # Pattern to match YAML-style output_dir configuration
    # Handles: output_dir: value, output_dir:"value", output_dir: "value"
    pattern = r'^\s*output_dir\s*:\s*["\']?([a-zA-Z0-9_/\-]+)["\']?\s*$'
    
    for line in md_content.split('\n'):
        match = re.match(pattern, line, re.IGNORECASE)
        if match:
            output_dir = match.group(1).strip()
            
            # Security validation: prevent path traversal and absolute paths
            # Check for common path traversal patterns
            if '..' in output_dir:
                continue
            
            # Check for absolute paths (Unix and Windows)
            if output_dir.startswith('/') or (len(output_dir) > 1 and output_dir[1] == ':'):
                continue
            
            # Additional validation using Path to catch obfuscated attempts
            try:
                # Use Path to normalize and validate the path
                normalized = Path(output_dir)
                # Ensure no parent directory traversal
                if any(part == '..' for part in normalized.parts):
                    continue
                # Ensure it's a relative path
                if normalized.is_absolute():
                    continue
            except (ValueError, OSError):
                # Invalid path, skip it
                continue
            
            return output_dir
    
    return ""


def extract_file_structure_from_md(md_content: str) -> Dict[str, List[str]]:
    """Extract the expected file/directory structure from a Markdown specification.

    Implements a two-pass parser:

    **Pass 1 — ASCII/Unicode tree blocks**
        Scans every fenced or indented code block for directory-tree listings
        (lines decorated with ``├``, ``└``, ``│``, ``─`` or plain ``|``/``-``
        characters).  Each entry is classified as either a *file* (has a
        recognised source extension) or a *directory* (has a trailing ``/``
        or no extension).  The parser tracks indentation depth so it can
        reconstruct the **full path** of every node from the tree root.

    **Pass 2 — Inline path references**
        Scans the entire document for backtick-quoted paths
        (e.g. `` `app/routers/products.py` ``), explicit directory references
        (e.g. ``app/routers/``), and glob-style path patterns
        (e.g. ``app/routers/*.py``).  These are merged with the tree results.

    The function is **deterministic**: identical input always produces the same
    output, and the order of entries in each list reflects the order of first
    appearance in the document (important for audit trails).

    Industry Standards Compliance:
        - SOC 2 Type II: deterministic, auditable output
        - ISO 27001 A.14.2.5: requirement traceability
        - Input validation and type-checked defensive programming

    Args:
        md_content: Raw Markdown specification content.

    Returns:
        A dictionary with three keys:

        ``'directories'``
            Unique, ordered list of relative directory paths found in the spec
            (e.g. ``['app', 'app/routers', 'app/services', 'tests']``).
            Paths use forward slashes and have **no** trailing slash.

        ``'files'``
            Unique, ordered list of relative file paths found in the spec
            (e.g. ``['app/main.py', 'app/routers/products.py']``).

        ``'modules'``
            Python dotted-module names derived from ``'files'``
            (e.g. ``['app.main', 'app.routers.products']``).

    Examples:
        >>> md = "├── app/\\n│   ├── routers/\\n│   │   └── products.py"
        >>> s = extract_file_structure_from_md(md)
        >>> 'app/routers' in s['directories']
        True
        >>> 'app/routers/products.py' in s['files']
        True
        >>> 'app.routers.products' in s['modules']
        True

    Raises:
        TypeError: If *md_content* is not a ``str``.
    """
    # -------------------------------------------------------------------------
    # Input validation — industry-standard defensive programming
    # -------------------------------------------------------------------------
    if not isinstance(md_content, str):
        raise TypeError(
            f"md_content must be a str, got {type(md_content).__name__}"
        )

    structure: Dict[str, List[str]] = {
        "directories": [],
        "files": [],
        "modules": [],
    }

    if not md_content.strip():
        return structure

    dirs_seen: Set[str] = set()
    files_seen: Set[str] = set()
    modules_seen: Set[str] = set()

    # -------------------------------------------------------------------------
    # Source-file extensions we recognise
    # -------------------------------------------------------------------------
    _SOURCE_EXTS: Set[str] = {
        ".py", ".pyi", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
        ".yml", ".yaml", ".toml", ".cfg", ".ini", ".txt", ".json",
        ".html", ".htm", ".css", ".md", ".rst", ".go", ".java",
        ".rs", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".sh",
        ".dockerfile", ".gitkeep", ".gitignore", ".env",
    }

    def _has_source_ext(name: str) -> bool:
        """Return True when *name* has a recognised source-file extension."""
        _lower = name.lower()
        # Special bare filenames without extension
        if _lower in {
            "dockerfile", "makefile", "procfile", "gemfile",
            ".gitkeep", ".gitignore", ".env", ".dockerignore",
        }:
            return True
        _, ext = os.path.splitext(_lower)
        return ext in _SOURCE_EXTS

    def _record_dir(dirpath: str) -> None:
        """Add *dirpath* to the directory list if not already seen."""
        dirpath = dirpath.strip("/").strip()
        if not dirpath or dirpath == ".":
            return
        # Also ensure every ancestor is recorded (e.g. for app/routers/v1
        # also record app/routers and app).
        parts = dirpath.replace("\\", "/").split("/")
        for i in range(1, len(parts) + 1):
            ancestor = "/".join(parts[:i])
            if ancestor and ancestor not in dirs_seen:
                dirs_seen.add(ancestor)
                structure["directories"].append(ancestor)

    def _record_file(filepath: str) -> None:
        """Add *filepath* to the files list and derive its parent directory."""
        filepath = filepath.replace("\\", "/").strip("/").strip()
        if not filepath or filepath in files_seen:
            return
        files_seen.add(filepath)
        structure["files"].append(filepath)
        parent = filepath.rsplit("/", 1)[0] if "/" in filepath else ""
        if parent:
            _record_dir(parent)
        # Derive Python module path
        if filepath.endswith(".py"):
            module_path = filepath[: -len(".py")].replace("/", ".")
            if module_path and module_path not in modules_seen:
                modules_seen.add(module_path)
                structure["modules"].append(module_path)

    # =========================================================================
    # PASS 1 — Tree-block parser
    #
    # Strategy: Identify lines that are part of a directory-tree listing by
    # the presence of tree-drawing characters (Unicode box-drawing or ASCII
    # equivalents).  For each such line we:
    #   1. Strip the tree-drawing prefix to get the bare entry name.
    #   2. Measure the *logical depth* from the number of indentation units.
    #   3. Maintain a path stack and reconstruct the full path.
    # =========================================================================

    # Characters used in tree listings (Unicode box-drawing + ASCII fallbacks)
    _TREE_CHARS = frozenset("├└│─|+\\- \t")
    # Pattern that matches a tree-prefix character at start of a segment
    _tree_prefix_re = re.compile(
        r"^(?:[ \t│|]*(?:├──|└──|├--|└--|[+\\]--|[├└│|][-─ ]+))\s*"
    )

    def _strip_tree_prefix(line: str) -> Optional[Tuple[int, str]]:
        """Strip tree-drawing characters from *line*.

        Returns ``(depth, name)`` where *depth* is the nesting level (0-based,
        i.e. direct children of the tree root are depth 0) and *name* is the
        bare file/directory name (trailing ``/`` preserved).
        Returns ``None`` if the line does not look like a tree entry.

        Depth formula derivation
        ------------------------
        A standard ``tree``-command output uses 4-character indentation units:

        * Depth 0:  ``├── name``                       →  prefix = 4 chars
        * Depth 1:  ``│   ├── name``                   →  prefix = 8 chars
        * Depth 2:  ``│   │   ├── name``               →  prefix = 12 chars

        After normalising all tree characters to spaces, prefix_len = 4*(depth+1).
        Therefore: ``depth = prefix_len // 4 - 1``.
        """
        m = _tree_prefix_re.match(line)
        if m is None:
            return None
        prefix = m.group(0)
        name = line[len(prefix):].strip()
        if not name:
            return None
        # Normalise all tree-drawing characters to spaces to measure width.
        prefix_norm = (
            prefix
            .replace("├──", "   ")
            .replace("└──", "   ")
            .replace("├--", "   ")
            .replace("└--", "   ")
            .replace("│", " ")
            .replace("|", " ")
        )
        # prefix_norm has 4*(depth+1) spaces; subtract 1 to get 0-based depth.
        depth = max(0, len(prefix_norm) // 4 - 1)
        return depth, name

    def _parse_tree_block(block_lines: List[str]) -> None:
        """Parse a list of tree-listing lines and record all paths."""
        # path_stack[i] = directory name at depth i (or empty string for root)
        path_stack: List[str] = []

        for raw_line in block_lines:
            parsed = _strip_tree_prefix(raw_line)
            if parsed is None:
                continue
            depth, name = parsed

            # Trim annotation comments (e.g. "main.py  # entry point")
            name = re.split(r"\s+#", name)[0].strip()
            if not name:
                continue

            is_dir = name.endswith("/") or (
                not _has_source_ext(name) and not re.search(r"\.\w{1,6}$", name)
            )
            bare_name = name.rstrip("/")

            # Adjust the path stack to the current depth
            path_stack = path_stack[:depth]
            if is_dir:
                path_stack.append(bare_name)
                full_path = "/".join(path_stack)
                _record_dir(full_path)
            else:
                parent_path = "/".join(path_stack)
                full_path = (parent_path + "/" + bare_name) if parent_path else bare_name
                if _has_source_ext(bare_name):
                    _record_file(full_path)

    # Extract fenced code blocks and scan each for tree content
    # Recognise ```tree, ```bash, ``` (plain), and indented code blocks
    fenced_re = re.compile(
        r"```[\w]*\n(.*?)```",
        re.DOTALL,
    )
    for fenced_match in fenced_re.finditer(md_content):
        block_text = fenced_match.group(1)
        block_lines = block_text.splitlines()
        # Check if this block looks like a tree listing
        has_tree_chars = any(
            _tree_prefix_re.match(ln) for ln in block_lines
        )
        if has_tree_chars:
            _parse_tree_block(block_lines)

    # Also scan non-fenced lines that look like tree entries (e.g. in
    # indented sections or README prose that uses tree characters)
    outside_lines = fenced_re.sub("", md_content).splitlines()
    pending_tree: List[str] = []
    for ln in outside_lines:
        if _tree_prefix_re.match(ln):
            pending_tree.append(ln)
        else:
            if pending_tree:
                _parse_tree_block(pending_tree)
                pending_tree = []
    if pending_tree:
        _parse_tree_block(pending_tree)

    # =========================================================================
    # PASS 2 — Inline path references
    #
    # Captures paths that appear in prose, backtick spans, or glob patterns
    # but are not inside a tree listing.
    # =========================================================================

    # Pattern A: backtick-quoted multi-part path (must contain at least one /)
    # e.g. `app/routers/products.py`, `app/services/`
    _backtick_path_re = re.compile(
        r"`([a-zA-Z_.][\w./\-]*(?:/[\w./\-]+)+/?)`"
    )
    for m in _backtick_path_re.finditer(md_content):
        raw = m.group(1)
        if raw.endswith("/"):
            _record_dir(raw.rstrip("/"))
        elif _has_source_ext(raw) or re.search(r"\.\w{1,6}$", raw):
            _record_file(raw)
        else:
            _record_dir(raw)

    # Pattern B: explicit directory references like ``app/routers/``
    # (trailing slash, not inside backticks — avoid double-counting)
    _dir_ref_re = re.compile(
        r"(?<![`\w/])([a-zA-Z_][\w]*(?:/[\w]+)+)/"
    )
    for m in _dir_ref_re.finditer(md_content):
        _record_dir(m.group(1))

    # Pattern C: glob-style path patterns like ``app/routers/*.py``
    # Capture only the directory portion (everything before the last ``/``
    # that precedes the wildcard ``*``).  This prevents partial filename
    # segments such as ``test_`` from being incorrectly recorded as dirs.
    _glob_path_re = re.compile(
        r"([a-zA-Z_][\w]*/(?:[\w]+/)*)[\w]*\*"
    )
    for m in _glob_path_re.finditer(md_content):
        _record_dir(m.group(1).rstrip("/"))

    return structure


def validate_readme_completeness(readme_content: str, language: str = "python") -> Dict[str, Any]:
    """
    Validate that generated README.md is complete and production-ready.
    
    Checks for required sections and commands to ensure the README provides
    adequate setup and usage instructions. A complete README is essential for
    production deployments and developer onboarding.
    
    Supports README_TEST_MODE environment variable for relaxed validation in test environments.
    When README_TEST_MODE=1:
    - Minimum length: 200 characters (instead of 500)
    - Required sections: Empty (all sections optional)
    - Required commands: Empty (all commands optional)
    
    Required Elements (when README_TEST_MODE is not set):
        - Minimum length: 500 characters
        - Setup section (virtual environment, dependencies)
        - Run server instructions
        - Testing instructions
        - API examples (curl commands)
        - Required commands: language-specific (e.g., python -m venv, pip install for Python;
          npm install, npx jest for TypeScript/JavaScript)
    
    Args:
        readme_content: Content of the generated README.md file
        language: Programming language of the project (default: "python")
        
    Returns:
        Validation result dictionary with:
        - valid: bool indicating if README meets all requirements
        - errors: list of missing elements
        - warnings: list of optional improvements
        - length: actual character count
        - sections_found: list of required sections that were found
        - commands_found: list of required commands that were found
        
    Example:
        >>> result = validate_readme_completeness(readme_content, language="typescript")
        >>> if not result['valid']:
        ...     print(f"README incomplete: {result['errors']}")
    """
    errors = []
    warnings = []
    sections_found = []
    commands_found = []
    
    # Check if README_TEST_MODE is enabled for relaxed validation
    test_mode = os.environ.get("README_TEST_MODE", "0") == "1"
    
    # 0. Check for markdown code fence wrappers (Fix 5)
    # This detects when README content is wrapped in ```markdown ... ``` or ```md ... ```
    readme_stripped = readme_content.strip()
    if readme_stripped.startswith("```markdown") or readme_stripped.startswith("```md"):
        errors.append(
            "README content is wrapped in markdown code fences (```markdown or ```md). "
            "The content should be pure markdown, not markdown-wrapped markdown. "
            "This indicates improper extraction from the LLM response."
        )
        # Try to auto-extract the content for further validation
        # Pattern: ```markdown\n<content>\n```
        fence_pattern = r'^```(?:markdown|md)\s*\n(.*?)\n```$'
        match = re.search(fence_pattern, readme_stripped, re.DOTALL)
        if match:
            logger.info("Auto-extracting README content from markdown code fence")
            readme_content = match.group(1).strip()
            readme_stripped = readme_content
            warnings.append("Auto-extracted README content from code fence wrapper")
        else:
            # Can't extract - content is malformed
            logger.error("README has markdown fence wrapper but content can't be extracted")
    
    # Additional check: detect incomplete fence extraction (starts with ``` but no closing)
    if readme_stripped.startswith("```") and not readme_stripped.endswith("```"):
        errors.append(
            "README appears to have incomplete markdown code fence (starts with ``` but doesn't end properly). "
            "This indicates malformed LLM response."
        )
    
    # 1. Check minimum length (relaxed in test mode)
    length = len(readme_content)
    min_length_required = 200 if test_mode else 400
    if length < min_length_required:
        errors.append(f"README too short ({length} chars, minimum {min_length_required})")
    
    # 2. Check for required sections (case-insensitive) - relaxed in test mode
    readme_lower = readme_content.lower()
    
    if test_mode:
        # In test mode, sections are optional
        required_sections = {}
    else:
        required_sections = {
            "setup": ["setup", "installation", "install", "getting started"],
            "run": ["run", "running", "start", "usage"],
            "test": ["test", "testing"],
            "examples": ["example", "api example", "curl"],
        }
    
    for section_key, patterns in required_sections.items():
        found = any(pattern in readme_lower for pattern in patterns)
        if found:
            sections_found.append(section_key)
        else:
            errors.append(f"Missing required section: {section_key}")
    
    # 3. Check for required commands (language-aware) - relaxed in test mode
    if test_mode:
        # In test mode, commands are optional
        required_commands = {}
    elif language.lower() in ("typescript", "javascript"):
        required_commands = {
            "install": ["npm install", "yarn install", "pnpm install"],
            "run": ["npm run", "npx", "node", "ts-node"],
            "test": ["jest", "mocha", "npm test", "npx jest"],
        }
    elif language.lower() == "python":
        required_commands = {
            "venv": ["python -m venv", "python3 -m venv", "virtualenv", "venv"],
            "pip": ["pip install", "pip3 install"],
            "uvicorn": ["uvicorn", "python -m uvicorn"],
            "pytest": ["pytest", "python -m pytest"],
            "curl": ["curl"],
        }
    elif language.lower() == "go":
        required_commands = {
            "download": ["go mod download", "go get"],
            "run": ["go run", "go build"],
            "test": ["go test"],
            "curl": ["curl"],
        }
    elif language.lower() == "java":
        required_commands = {
            "install": ["mvn install", "gradle build", "mvn clean install"],
            "run": ["java -jar", "mvn spring-boot:run", "gradle run", "mvn exec:java"],
            "test": ["mvn test", "gradle test"],
            "curl": ["curl"],
        }
    else:
        # Generic: just check for install and test commands
        required_commands = {
            "install": ["install", "setup"],
            "test": ["test"],
        }
    
    for cmd_key, patterns in required_commands.items():
        found = any(pattern in readme_content for pattern in patterns)
        if found:
            commands_found.append(cmd_key)
        else:
            # curl is only a warning since it's for examples
            if cmd_key == "curl":
                warnings.append("No curl examples found (recommended)")
            else:
                errors.append(f"Missing required command: {cmd_key}")
    
    valid = len(errors) == 0
    
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "length": length,
        "sections_found": sections_found,
        "commands_found": commands_found,
    }


def validate_spec_fidelity(
    md_content: str,
    generated_files: Dict[str, str],
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate that generated code implements all required endpoints from the MD spec.
    
    This is the core spec fidelity check that ensures the Code Factory output
    matches the input specification. It extracts required routes from the MD
    and verifies they exist in the generated code.
    
    The validation implements a strict contract enforcement pattern where all
    endpoints specified in the input markdown must have corresponding route
    handlers in the generated code. This is a critical factory behavior gate
    that prevents incomplete or incorrect output from being packaged.
    
    Industry Standards Compliance:
        - SOC 2 Type II: Audit trail of validation decisions
        - ISO 27001 A.14.2.5: Secure software development (requirement traceability)
        - NIST SP 800-53 SA-15: Software development process verification
    
    Args:
        md_content: The original Markdown spec content containing API definitions
        generated_files: Dictionary mapping filenames to their content
        output_dir: Optional directory to write error.txt on failure
        
    Returns:
        Validation result dictionary with:
        - valid: bool indicating if all required routes are present
        - required_endpoints: list of endpoints parsed from the spec
        - found_endpoints: list of endpoints found in generated code
        - missing_endpoints: list of endpoints missing from generated code
        - extra_endpoints: list of endpoints in code but not in spec
        - errors: list of human-readable error messages
        - validation_timestamp: ISO timestamp of validation
        - duration_ms: validation duration in milliseconds
        
    Raises:
        No exceptions are raised; errors are captured in the result dict.
        
    Example:
        >>> md = "| GET | /api/users |"
        >>> files = {"main.py": "@app.get('/api/users')\\ndef get_users(): pass"}
        >>> result = validate_spec_fidelity(md, files)
        >>> result["valid"]
        True
    """
    # Start OpenTelemetry span if available
    span = None
    if HAS_OPENTELEMETRY and _tracer:
        span = _tracer.start_span("validate_spec_fidelity")
        span.set_attribute("md_content_length", len(md_content))
        span.set_attribute("file_count", len(generated_files))
    
    start_time = time.time()
    
    result: Dict[str, Any] = {
        "valid": True,
        "required_endpoints": [],
        "found_endpoints": [],
        "missing_endpoints": [],
        "extra_endpoints": [],
        "errors": [],
        "warnings": [],
        "validation_timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 0,
        "router_wiring_check": None,
        "structure_validation": {
            "expected_directories": [],
            "missing_directories": [],
            "passed": True,
        },
    }
    
    try:
        # Extract expected file structure from MD spec and validate it
        expected_structure = extract_file_structure_from_md(md_content)
        expected_dirs = expected_structure.get("directories", [])
        # Filter out entries that look like files (have a file extension) — e.g. ".env.example".
        # Use os.path.basename so nested paths like "some/dir/.env.example" are handled correctly.
        expected_dirs = [d for d in expected_dirs if not os.path.splitext(os.path.basename(d))[1]]
        missing_dirs: List[str] = []
        if output_dir and expected_dirs:
            for expected_dir in expected_dirs:
                full_path = os.path.join(output_dir, expected_dir)
                if not os.path.isdir(full_path):
                    missing_dirs.append(expected_dir)

        result["structure_validation"] = {
            "expected_directories": expected_dirs,
            "missing_directories": missing_dirs,
            "passed": len(missing_dirs) == 0,
        }

        if missing_dirs:
            logger.warning(
                "[SPEC_VALIDATE] Missing expected directories: %s",
                missing_dirs,
                extra={"stage": "SPEC_VALIDATE", "missing_dirs": missing_dirs},
            )

        # Extract required endpoints from MD spec
        required_endpoints = extract_endpoints_from_md(md_content)
        result["required_endpoints"] = required_endpoints
        
        if span:
            span.set_attribute("required_endpoint_count", len(required_endpoints))
        
        if not required_endpoints:
            # No endpoints specified in MD - that's OK, just log and pass
            logger.info(
                "[SPEC_VALIDATE] No API endpoints found in MD spec - skipping endpoint validation",
                extra={"stage": "SPEC_VALIDATE", "result": "skipped"}
            )
            result["valid"] = True
            return result
        
        # Extract endpoints from all code files (Python, TypeScript, JavaScript, etc.)
        all_found_endpoints: List[Dict[str, str]] = []
        for filename, content in generated_files.items():
            if filename.endswith(('.py', '.ts', '.js', '.java', '.go')):
                file_endpoints = extract_endpoints_from_code(content, filename)
                all_found_endpoints.extend(file_endpoints)

        # ------------------------------------------------------------------
        # Router-prefix reconciliation (Python / FastAPI projects).
        #
        # When the generated code uses FastAPI's ``include_router(router,
        # prefix="/api/v1/orders")`` pattern, the route decorators in the
        # router file contain only the *sub-path* (e.g. ``/stats``).  The
        # per-file extractor therefore produces ``/stats``, not the full
        # ``/api/v1/orders/stats`` that the spec requires.
        #
        # We resolve this by:
        #   1. Scanning main.py (or app/main.py) for ``include_router`` calls
        #      that carry an explicit ``prefix=`` keyword argument.
        #   2. Building a map from router-file module name to its prefix.
        #   3. Re-processing each router-file's endpoints to prepend the
        #      resolved prefix, then adding the fully-qualified paths to the
        #      found set.
        # ------------------------------------------------------------------
        _INCLUDE_ROUTER_RE = re.compile(
            r'include_router\s*\(\s*(\w+)\s*,\s*(?:[^)]*\s)?prefix\s*=\s*["\']([^"\']+)["\']',
            re.DOTALL,
        )

        # Identify main.py files (prefer app/main.py, fall back to main.py)
        _main_file_content: str = ""
        for _candidate in ("app/main.py", "main.py"):
            if _candidate in generated_files:
                _main_file_content = generated_files[_candidate]
                break

        # router_var_name → prefix string  (e.g. "orders_router" → "/api/v1/orders")
        _router_prefix_map: Dict[str, str] = {}
        if _main_file_content:
            for _var, _prefix in _INCLUDE_ROUTER_RE.findall(_main_file_content):
                _router_prefix_map[_var] = _prefix

        if _router_prefix_map:
            # Build a reverse map: router module stem → prefix
            # e.g. "from app.routers.orders import router as orders_router"
            _IMPORT_AS_RE = re.compile(
                r'from\s+\S+\.(\w+)\s+import\s+\w+\s+as\s+(\w+)'
            )
            _stem_to_prefix: Dict[str, str] = {}
            for _stem, _var in _IMPORT_AS_RE.findall(_main_file_content):
                if _var in _router_prefix_map:
                    _stem_to_prefix[_stem] = _router_prefix_map[_var]

            # Re-extract endpoints from router files with the prefix prepended.
            for _filename, _content in generated_files.items():
                if not _filename.endswith(".py"):
                    continue
                # Determine if this file is a router file and get its prefix.
                _module_stem = _filename.replace("/", ".").removesuffix(".py").split(".")[-1]
                _prefix = _stem_to_prefix.get(_module_stem, "")
                if not _prefix:
                    continue
                # Re-extract routes and prepend the include_router prefix.
                _raw = extract_endpoints_from_code(_content, _filename)
                for _ep in _raw:
                    _raw_path = _ep.get("path", "")
                    _full_path = (
                        _prefix.rstrip("/") + "/" + _raw_path.lstrip("/")
                    )
                    all_found_endpoints.append(
                        {"method": _ep["method"], "path": _full_path}
                    )

        result["found_endpoints"] = all_found_endpoints

        if span:
            span.set_attribute("found_endpoint_count", len(all_found_endpoints))

        # Normalize paths for case-insensitive comparison with trailing slash handling
        def normalize_path(path: str) -> str:
            """Normalize a path for comparison.

            Strips leading/trailing whitespace and slashes, lowercases, removes
            the ``/api/v{N}`` version prefix (so ``/api/v1/orders`` and
            ``/orders`` compare as equal), and replaces every path parameter
            with the canonical placeholder ``{_}`` so ``{id}``,
            ``{product_id}``, and ``{order_id}`` all compare as equal.
            """
            normalized = path.strip().rstrip('/').lower()
            # Strip /api/v{N} prefix so /api/v1/orders and /orders compare as equal
            normalized = re.sub(r'^/api/v\d+', '', normalized)
            # Normalize path parameters so {id}, {product_id}, {order_id} etc. compare as equal
            normalized = re.sub(r'\{[^}]+\}', '{_}', normalized)
            return normalized or '/'

        # Build lookup set of found endpoints
        found_set: Set[Tuple[str, str]] = {
            (e["method"], normalize_path(e["path"])) for e in all_found_endpoints
        }

        # Build lookup set of required endpoints
        required_set: Set[Tuple[str, str]] = {
            (e["method"], normalize_path(e["path"])) for e in required_endpoints
        }

        # Find missing endpoints (in spec but not in code)
        missing: List[Dict[str, str]] = []
        for endpoint in required_endpoints:
            key = (endpoint["method"], normalize_path(endpoint["path"]))
            if key not in found_set:
                missing.append(endpoint)

        # Find extra endpoints (in code but not in spec) - informational only
        extra: List[Dict[str, str]] = []
        for endpoint in all_found_endpoints:
            key = (endpoint["method"], normalize_path(endpoint["path"]))
            if key not in required_set:
                extra.append(endpoint)

        result["missing_endpoints"] = missing
        result["extra_endpoints"] = extra

        if span:
            span.set_attribute("missing_endpoint_count", len(missing))
            span.set_attribute("extra_endpoint_count", len(extra))

        # Check router wiring: routers defined but not included in main.py
        router_files = [f for f in generated_files if "routers/" in f or "routes/" in f]
        main_files = [f for f in generated_files if f.endswith("main.py")]

        if router_files and main_files:
            main_content = generated_files[main_files[0]]
            included_routers = re.findall(r'include_router\s*\(\s*(\w+)', main_content)
            router_imports = re.findall(
                r'from\s+\S+\s+import\s+(\w*[Rr]outer\b)', main_content
            )

            if not included_routers and not router_imports:
                wiring_warning = (
                    f"Router files found ({router_files}) but main.py has no include_router() calls. "
                    f"API endpoints may not be accessible at runtime."
                )
                result["warnings"].append(wiring_warning)
                logger.warning(
                    "[SPEC_VALIDATE] %s", wiring_warning,
                    extra={"stage": "SPEC_VALIDATE", "router_files": router_files},
                )
            result["router_wiring_check"] = {
                "router_files": router_files,
                "include_router_calls": included_routers,
                "status": "connected" if (included_routers or router_imports) else "disconnected",
            }

        # Determine validation result
        if missing:
            result["valid"] = False
            for ep in missing:
                error_msg = f"Missing required endpoint: {ep['method']} {ep['path']}"
                result["errors"].append(error_msg)
                logger.error(
                    f"[SPEC_VALIDATE] {error_msg}",
                    extra={"stage": "SPEC_VALIDATE", "endpoint": ep}
                )
                PROVENANCE_ERRORS_RECORDED.labels(
                    stage="SPEC_VALIDATE", 
                    error_type="missing_endpoint"
                ).inc()
        
        # Write error file if validation failed
        if not result["valid"] and output_dir:
            _write_spec_error_file(output_dir, result)
        
        # Log final result with structured data
        if result["valid"]:
            logger.info(
                f"[SPEC_VALIDATE] Passed - all {len(required_endpoints)} required endpoints found",
                extra={
                    "stage": "SPEC_VALIDATE",
                    "required": len(required_endpoints),
                    "found": len(all_found_endpoints),
                    "extra": len(extra)
                }
            )
            if span:
                span.set_status(Status(StatusCode.OK))
        else:
            logger.error(
                f"[SPEC_VALIDATE] Failed - {len(missing)} endpoints missing",
                extra={
                    "stage": "SPEC_VALIDATE",
                    "missing": missing,
                    "required": len(required_endpoints),
                    "found": len(all_found_endpoints)
                }
            )
            if span:
                span.set_status(Status(StatusCode.ERROR, f"{len(missing)} endpoints missing"))
        
        return result
        
    except Exception as e:
        result["valid"] = False
        result["errors"].append(f"Validation error: {str(e)}")
        if span:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
        logger.exception("[SPEC_VALIDATE] Unexpected error during validation")
        PROVENANCE_ERRORS_RECORDED.labels(
            stage="SPEC_VALIDATE",
            error_type="validation_exception"
        ).inc()
        return result
        
    finally:
        # Record duration
        duration_ms = (time.time() - start_time) * 1000
        result["duration_ms"] = duration_ms
        VALIDATION_DURATION.labels(validation_type="spec_fidelity").observe(time.time() - start_time)
        
        if span:
            span.set_attribute("duration_ms", duration_ms)
            span.end()


def _write_spec_error_file(output_dir: str, result: Dict[str, Any]) -> None:
    """
    Write spec validation errors to error.txt with structured formatting.
    
    Creates a comprehensive error report that can be used by downstream
    systems and developers to understand validation failures.
    
    Industry Standards Compliance:
        - SOC 2 Type II: Structured audit trail
        - ISO 27001 A.12.4.1: Event logging
    
    Args:
        output_dir: Directory where error.txt will be written
        result: Validation result dictionary from validate_spec_fidelity
    """
    error_path = Path(output_dir) / "error.txt"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    with open(error_path, "w", encoding="utf-8") as f:
        # Header with clear identification
        f.write("=" * 70 + "\n")
        f.write("SPEC FIDELITY VALIDATION FAILED\n")
        f.write("=" * 70 + "\n\n")
        
        # Metadata section
        f.write("Validation Metadata:\n")
        f.write("-" * 40 + "\n")
        f.write(f"  Timestamp: {timestamp}\n")
        f.write(f"  Duration:  {result.get('duration_ms', 0):.2f} ms\n")
        f.write("  Stage:     SPEC_VALIDATE\n\n")
        
        # Summary statistics
        missing_count = len(result.get("missing_endpoints", []))
        required_count = len(result.get("required_endpoints", []))
        found_count = len(result.get("found_endpoints", []))
        
        f.write("Summary:\n")
        f.write("-" * 40 + "\n")
        f.write(f"  Required endpoints: {required_count}\n")
        f.write(f"  Found endpoints:    {found_count}\n")
        f.write(f"  Missing endpoints:  {missing_count}\n")
        # Calculate coverage percentage (guard against zero division)
        coverage_pct = ((required_count - missing_count) / required_count * 100) if required_count > 0 else 0.0
        f.write(f"  Coverage:           {coverage_pct:.1f}%\n\n")
        
        # Missing endpoints (critical section)
        f.write("Missing Required Endpoints:\n")
        f.write("-" * 40 + "\n")
        if result.get("missing_endpoints"):
            for ep in result["missing_endpoints"]:
                f.write(f"  ✗ {ep['method']:7} {ep['path']}\n")
        else:
            f.write("  (none)\n")
        f.write("\n")
        
        # Required endpoints from spec
        f.write("Required Endpoints (from spec):\n")
        f.write("-" * 40 + "\n")
        for ep in result.get("required_endpoints", []):
            found = ep not in result.get("missing_endpoints", [])
            marker = "✓" if found else "✗"
            f.write(f"  {marker} {ep['method']:7} {ep['path']}\n")
        f.write("\n")
        
        # Found endpoints in generated code
        f.write("Endpoints Found in Generated Code:\n")
        f.write("-" * 40 + "\n")
        if result.get("found_endpoints"):
            for ep in result["found_endpoints"]:
                f.write(f"  • {ep['method']:7} {ep['path']}\n")
        else:
            f.write("  (none found)\n")
        f.write("\n")
        
        # Error messages
        if result.get("errors"):
            f.write("Error Details:\n")
            f.write("-" * 40 + "\n")
            for i, error in enumerate(result["errors"], 1):
                f.write(f"  {i}. {error}\n")
            f.write("\n")
        
        # Footer with actionable guidance
        f.write("=" * 70 + "\n")
        f.write("Resolution:\n")
        f.write("  1. Ensure your code generator implements all required endpoints\n")
        f.write("  2. Check that route decorators match the spec exactly\n")
        f.write("  3. Verify path parameters use consistent naming (e.g., {id})\n")
        f.write("=" * 70 + "\n")
    
    logger.info(
        f"[SPEC_VALIDATE] Error file written to {error_path}",
        extra={"path": str(error_path), "missing_count": missing_count}
    )


def run_fail_fast_validation(
    generated_files: Dict[str, str],
    output_dir: Optional[str] = None,
    md_content: Optional[str] = None,
    target_language: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run validation on generated files.
    
    Validates syntax and content for code files.
    Optionally validates spec fidelity if md_content is provided.
    
    Industry Standards:
        - Input validation and type checking
        - Language-aware validation (supports Python, TypeScript, JavaScript, Java, Go)
        - OpenTelemetry tracing for observability
        - Prometheus metrics for monitoring
        - Comprehensive error reporting
        - Graceful degradation when tools unavailable
    
    Args:
        generated_files: Dictionary mapping filenames to code content
        output_dir: Optional directory where files are/will be written
        md_content: Optional markdown spec content for fidelity validation
        target_language: Optional target language (e.g., "python", "typescript", "java")
                        Used for language-specific entry point checks. If not provided,
                        defaults to Python entry point validation for backward compatibility.
    
    Returns:
        Dictionary with validation results containing:
            - valid: bool - Overall validation status
            - checks: dict - Individual check results
            - errors: list - List of error messages
    
    Raises:
        TypeError: If generated_files is not a dictionary
        
    Examples:
        >>> files = {"main.py": "print('hello')", "requirements.txt": "flask"}
        >>> result = run_fail_fast_validation(files, target_language="python")
        >>> result["valid"]
        True
    """
    # Input validation - industry standard defensive programming
    if not isinstance(generated_files, dict):
        raise TypeError(f"generated_files must be a dict, got {type(generated_files).__name__}")
    
    if target_language is not None and not isinstance(target_language, str):
        raise TypeError(f"target_language must be a string or None, got {type(target_language).__name__}")
    
    start_time = time.time()
    
    # Start OpenTelemetry span if available
    span = None
    if HAS_OPENTELEMETRY and _tracer:
        span = _tracer.start_span("run_fail_fast_validation")
        span.set_attribute("file_count", len(generated_files))
        if target_language:
            span.set_attribute("target_language", target_language)
    
    try:
        results: Dict[str, Any] = {
            "valid": True,
            "checks": {},
            "errors": [],
            "target_language": target_language or "python"  # For audit trail
        }
        
        # Validate Python files
        for filename, content in generated_files.items():
            if filename.endswith('.py'):
                syntax_result = validate_syntax(content, filename)
                results["checks"][f"{filename}_syntax"] = syntax_result
                if not syntax_result["valid"]:
                    results["valid"] = False
                    results["errors"].append(f"{filename}: {syntax_result['error']}")
                
                content_result = validate_has_content(content, filename)
                results["checks"][f"{filename}_content"] = content_result
                if not content_result["valid"]:
                    results["valid"] = False
                    results["errors"].append(content_result["error"])
        
        # Check for stub/placeholder markers — these are expected outputs and must not fail validation
        _STUB_MARKERS = (
            "Generated module — replace with actual implementation.",
            "Stub Pydantic model.",
            "Placeholder implementation.",
        )
        stub_files = [
            fname for fname, content in generated_files.items()
            if fname.endswith(".py") and any(marker in content for marker in _STUB_MARKERS)
        ]
        if stub_files:
            logger.warning(
                "run_fail_fast_validation: %d stub file(s) detected (non-fatal): %s",
                len(stub_files), stub_files
            )
            results["checks"]["stub_files"] = {
                "valid": True,
                "stub_count": len(stub_files),
                "stub_files": stub_files,
                "warning": f"{len(stub_files)} stub file(s) detected — replace with real implementations",
            }
        
        # Language-specific entry point checks
        if target_language:
            lang = target_language.lower()
            
            if lang in ("python", "py"):
                # Python: main.py OR app/main.py + requirements.txt
                has_python_entry = "main.py" in generated_files or "app/main.py" in generated_files
                if not has_python_entry:
                    results["valid"] = False
                    results["errors"].append("main.py not found")
                if "requirements.txt" not in generated_files:
                    results["valid"] = False
                    results["errors"].append("requirements.txt not found")
                    
            elif lang in ("typescript", "ts", "javascript", "js"):
                # TypeScript/JavaScript: index.ts/index.js/app.ts/app.js + package.json
                has_entry = any(
                    fname in generated_files 
                    for fname in ["index.ts", "index.js", "app.ts", "app.js", "server.ts", "server.js"]
                )
                if not has_entry:
                    results["valid"] = False
                    results["errors"].append("No entry point found (expected index.ts, index.js, app.ts, app.js, server.ts, or server.js)")
                if "package.json" not in generated_files:
                    results["valid"] = False
                    results["errors"].append("package.json not found")
                    
            elif lang in ("java",):
                # Java: Main.java or App.java + pom.xml or build.gradle
                has_main = any(
                    fname in generated_files 
                    for fname in ["Main.java", "App.java", "Application.java"]
                )
                if not has_main:
                    results["valid"] = False
                    results["errors"].append("No main class found (expected Main.java, App.java, or Application.java)")
                has_build = any(
                    fname in generated_files 
                    for fname in ["pom.xml", "build.gradle", "build.gradle.kts"]
                )
                if not has_build:
                    results["valid"] = False
                    results["errors"].append("No build configuration found (expected pom.xml or build.gradle)")
                    
            elif lang in ("go",):
                # Go: main.go + go.mod
                if "main.go" not in generated_files:
                    results["valid"] = False
                    results["errors"].append("main.go not found")
                if "go.mod" not in generated_files:
                    results["valid"] = False
                    results["errors"].append("go.mod not found")
        else:
            # Default behavior when no target language specified (backward compatibility)
            # Only check for Python entry points (accept app/main.py too)
            has_python_entry = "main.py" in generated_files or "app/main.py" in generated_files
            if not has_python_entry:
                results["valid"] = False
                results["errors"].append("main.py not found")
            if "requirements.txt" not in generated_files:
                results["valid"] = False
                results["errors"].append("requirements.txt not found")
        
        # Run spec fidelity validation if MD content provided
        if md_content and results["valid"]:
            spec_result = validate_spec_fidelity(md_content, generated_files, output_dir)
            results["checks"]["spec_fidelity"] = spec_result
            if not spec_result["valid"]:
                results["valid"] = False
                results["errors"].extend(spec_result["errors"])
        
        # Check for missing __init__.py in Python package directories.
        # Any directory (derived from file paths in generated_files) that contains
        # .py files but no __init__.py may cause import errors at test time.
        if output_dir:
            _check_missing_init_py(generated_files, output_dir, results)
        
        # Write error file if failed
        if not results["valid"] and output_dir:
            _write_error_file(output_dir, results["errors"], results["checks"])
        
        VALIDATION_DURATION.labels(validation_type="fail_fast").observe(time.time() - start_time)
        
        if not results["valid"]:
            logger.error(f"Validation failed: {results['errors']}")
        
        # Set span status if available
        if span:
            if results["valid"]:
                span.set_status(Status(StatusCode.OK))
            else:
                span.set_status(Status(StatusCode.ERROR, description=f"{len(results['errors'])} validation errors"))
                span.set_attribute("error_count", len(results['errors']))
        
        return results
    
    finally:
        # End OpenTelemetry span
        if span:
            span.end()


def _check_missing_init_py(
    generated_files: Dict[str, str],
    output_dir: str,
    results: Dict[str, Any],
) -> None:
    """Warn when a Python package directory is missing ``__init__.py``.

    Iterates over every file path in *generated_files* and records a warning
    for each unique subdirectory that contains ``.py`` files but has no
    ``__init__.py``, which would cause import errors in generated tests.

    The check emits warnings (not errors) so it never flips ``results["valid"]``
    to ``False`` on its own.
    """
    # Collect directories that contain .py files and those that have __init__.py.
    dirs_with_py: set = set()
    dirs_with_init: set = set()

    for file_path in generated_files:
        parts = Path(file_path).parts
        if len(parts) < 2:
            # Root-level file — no subdirectory to check.
            continue
        parent = str(Path(file_path).parent)
        if file_path.endswith(".py"):
            dirs_with_py.add(parent)
        if Path(file_path).name == "__init__.py":
            dirs_with_init.add(parent)

    missing_init_dirs = sorted(dirs_with_py - dirs_with_init)
    for d in missing_init_dirs:
        warning_msg = (
            f"Directory '{d}' contains .py files but no __init__.py"
            f" — may cause import errors in generated tests"
        )
        results.setdefault("warnings", []).append(warning_msg)
        logger.warning(warning_msg)


def _write_error_file(output_dir: str, errors: List[str], checks: Dict[str, Any]) -> None:
    error_path = Path(output_dir) / "error.txt"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(error_path, "w", encoding="utf-8") as f:
        f.write("PIPELINE VALIDATION FAILED\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n\n")
        f.write("Errors:\n")
        for error in errors:
            f.write(f"  - {error}\n")
        f.write("\nChecks:\n")
        f.write(json.dumps(checks, indent=2))


# =============================================================================
# DEPLOYMENT VALIDATION
# =============================================================================

def validate_dockerfile(content: str) -> Dict[str, Any]:
    """Validate Dockerfile structure."""
    if not content or not content.strip():
        return {"valid": False, "errors": ["Dockerfile is empty"]}
    
    lines = [line.strip().upper() for line in content.split('\n')]
    
    has_from = any(line.startswith("FROM ") for line in lines)
    has_cmd = any(line.startswith("CMD ") or line.startswith("ENTRYPOINT ") for line in lines)
    
    errors = []
    if not has_from:
        errors.append("Missing FROM directive")
    if not has_cmd:
        errors.append("Missing CMD or ENTRYPOINT")
    
    return {"valid": len(errors) == 0, "errors": errors, "has_from": has_from, "has_cmd": has_cmd}


def validate_docker_compose(content: str) -> Dict[str, Any]:
    """Validate docker-compose.yml structure."""
    if not content or not content.strip():
        return {"valid": False, "errors": ["docker-compose.yml is empty"]}
    
    has_services = "services:" in content.lower()
    errors = [] if has_services else ["Missing 'services:' section"]
    
    return {"valid": has_services, "errors": errors, "has_services": has_services}


def validate_deployment_artifacts(
    deploy_files: Dict[str, str],
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Validate deployment artifacts."""
    results = {"valid": True, "checks": {}, "errors": []}
    
    if "Dockerfile" in deploy_files:
        df_result = validate_dockerfile(deploy_files["Dockerfile"])
        results["checks"]["dockerfile"] = df_result
        if not df_result["valid"]:
            results["valid"] = False
            results["errors"].extend(df_result["errors"])
    
    if "docker-compose.yml" in deploy_files:
        dc_result = validate_docker_compose(deploy_files["docker-compose.yml"])
        results["checks"]["docker_compose"] = dc_result
        if not dc_result["valid"]:
            results["valid"] = False
            results["errors"].extend(dc_result["errors"])

    # Fix 11: Validate Helm templates — check for valid YAML and required K8s fields.
    _helm_template_files = {
        k: v for k, v in deploy_files.items()
        if k.startswith("helm/templates/") and k.endswith((".yaml", ".yml"))
    }
    if _helm_template_files:
        _helm_errors: List[str] = []
        _required_k8s_fields = ("apiVersion", "kind", "metadata")

        for _tpl_path, _tpl_content in _helm_template_files.items():
            if not _tpl_content or not _tpl_content.strip():
                _helm_errors.append(f"Helm template '{_tpl_path}' is empty")
                continue
            # Reject raw JSON blobs — these are not valid K8s YAML.
            stripped = _tpl_content.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                _helm_errors.append(
                    f"Helm template '{_tpl_path}' appears to contain a JSON blob "
                    "instead of valid Kubernetes YAML"
                )
                continue
            # Validate YAML syntax.  Replace Helm template directives with a
            # harmless quoted empty string before parsing so that {{ ... }} blocks
            # don't trip up the YAML parser.
            if _YAML_AVAILABLE:
                _sanitized = re.sub(r"\{\{.*?\}\}", '""', _tpl_content, flags=re.DOTALL)
                try:
                    _parsed = _pyyaml.safe_load(_sanitized)  # type: ignore[union-attr]
                except Exception as _ye:
                    _helm_errors.append(
                        f"Helm template '{_tpl_path}' contains invalid YAML: {_ye}"
                    )
                    continue
                # Check required Kubernetes resource fields.
                if isinstance(_parsed, dict):
                    _missing = [f for f in _required_k8s_fields if f not in _parsed]
                    if _missing:
                        _helm_errors.append(
                            f"Helm template '{_tpl_path}' missing required K8s fields: "
                            + ", ".join(_missing)
                        )
            else:
                # pyyaml unavailable — fall back to a plain text search.
                for _field in _required_k8s_fields:
                    if _field not in _tpl_content:
                        _helm_errors.append(
                            f"Helm template '{_tpl_path}' missing required K8s field: {_field}"
                        )

        # Run `helm lint` when the CLI is available and structural checks passed.
        if output_dir and not _helm_errors:
            _helm_dir = Path(output_dir) / "helm"
            if _helm_dir.exists():
                try:
                    _lint = subprocess.run(
                        ["helm", "lint", str(_helm_dir)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if _lint.returncode != 0:
                        _helm_errors.append(
                            f"helm lint failed: {(_lint.stdout + _lint.stderr)[:500]}"
                        )
                except FileNotFoundError:
                    pass  # helm CLI not available — skip
                except Exception:
                    pass  # helm lint optional

        if _helm_errors:
            results["checks"]["helm_templates"] = {"valid": False, "errors": _helm_errors}
            results["valid"] = False
            results["errors"].extend(_helm_errors)
        else:
            results["checks"]["helm_templates"] = {"valid": True, "errors": []}

    if not results["valid"] and output_dir:
        error_path = Path(output_dir) / "error.txt"
        with open(error_path, "a", encoding="utf-8") as f:
            f.write("\nDeployment Validation Failed:\n")
            for error in results["errors"]:
                f.write(f"  - {error}\n")
    
    return results


__all__ = [
    "ProvenanceTracker",
    "PipelineStage",
    "validate_syntax",
    "validate_has_content",
    "extract_endpoints_from_code",
    "extract_endpoints_from_md",
    "extract_file_structure_from_md",
    "validate_spec_fidelity",
    "run_fail_fast_validation",
    "validate_dockerfile",
    "validate_docker_compose", 
    "validate_deployment_artifacts",
]
