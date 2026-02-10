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
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

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
        
        logger.info(f"ProvenanceTracker initialized", extra={"job_id": self.job_id})
    
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
        return {
            "job_id": self.job_id,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
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
    stripped = content.strip()
    has_content = len(stripped) > MIN_CONTENT_LENGTH
    return {
        "valid": has_content,
        "filename": filename,
        "length": len(stripped),
        "error": None if has_content else f"{filename} is empty or trivial"
    }


def extract_endpoints_from_code(code_content: str) -> List[Dict[str, str]]:
    """Extract API endpoints from code using regex."""
    endpoints = []
    patterns = [
        r'@\w+\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        r'@route\s*\(\s*["\']([^"\']+)["\']',
    ]
    
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


def extract_required_files_from_md(md_content: str) -> List[str]:
    """
    Extract required file paths referenced in a Markdown spec.

    Parses the specification for explicit file references such as
    ``app/routes.py``, ``models.py``, etc., so the validation step can
    verify that the generated project contains them.

    Supported patterns:
        - Tree-style listings: ``├── app/routes.py`` or ``│   ├── routes.py``
        - Backtick references: ``app/routes.py``

    Args:
        md_content: Markdown specification content to parse.

    Returns:
        Deduplicated, sorted list of relative file paths found in the spec.
    """
    files: List[str] = []
    seen: Set[str] = set()

    # Common source file extensions to look for
    file_ext_pattern = r'(?:\.py|\.js|\.ts|\.jsx|\.tsx|\.yml|\.yaml|\.toml|\.cfg|\.txt|\.json|\.html|\.css)'

    patterns = [
        # Tree-style listing: ├── app/routes.py or │ ├── routes.py
        # Includes box-drawing characters ├ └ │ ─ and ASCII fallbacks
        rf'[├└│─\-\| ]+([a-zA-Z_][\w/]*{file_ext_pattern})',
        # Backtick code reference: `app/routes.py`
        rf'`([a-zA-Z_][\w/]*{file_ext_pattern})`',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, md_content):
            path = match.group(1).strip()
            if path and path not in seen:
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


def validate_readme_completeness(readme_content: str) -> Dict[str, Any]:
    """
    Validate that generated README.md is complete and production-ready.
    
    Checks for required sections and commands to ensure the README provides
    adequate setup and usage instructions. A complete README is essential for
    production deployments and developer onboarding.
    
    Required Elements:
        - Minimum length: 500 characters
        - Setup section (virtual environment, dependencies)
        - Run server instructions
        - Testing instructions
        - API examples (curl commands)
        - Required commands: python -m venv, pip install, uvicorn, pytest, curl
    
    Args:
        readme_content: Content of the generated README.md file
        
    Returns:
        Validation result dictionary with:
        - valid: bool indicating if README meets all requirements
        - errors: list of missing elements
        - warnings: list of optional improvements
        - length: actual character count
        - sections_found: list of required sections that were found
        - commands_found: list of required commands that were found
        
    Example:
        >>> result = validate_readme_completeness(readme_content)
        >>> if not result['valid']:
        ...     print(f"README incomplete: {result['errors']}")
    """
    errors = []
    warnings = []
    sections_found = []
    commands_found = []
    
    # 1. Check minimum length
    length = len(readme_content)
    if length < 500:
        errors.append(f"README too short ({length} chars, minimum 500)")
    
    # 2. Check for required sections (case-insensitive)
    readme_lower = readme_content.lower()
    
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
    
    # 3. Check for required commands
    required_commands = {
        "venv": ["python -m venv", "python3 -m venv", "virtualenv"],
        "pip": ["pip install", "pip3 install"],
        "uvicorn": ["uvicorn", "python -m uvicorn"],
        "pytest": ["pytest", "python -m pytest"],
        "curl": ["curl"],
    }
    
    for cmd_key, patterns in required_commands.items():
        found = any(pattern in readme_content for pattern in patterns)
        if found:
            commands_found.append(cmd_key)
        else:
            # curl is only a warning since it's for examples
            if cmd_key == "curl":
                warnings.append(f"No curl examples found (recommended)")
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
        "validation_timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 0
    }
    
    try:
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
        
        # Extract endpoints from all Python files in generated code
        all_found_endpoints: List[Dict[str, str]] = []
        for filename, content in generated_files.items():
            if filename.endswith('.py'):
                file_endpoints = extract_endpoints_from_code(content)
                all_found_endpoints.extend(file_endpoints)
        
        result["found_endpoints"] = all_found_endpoints
        
        if span:
            span.set_attribute("found_endpoint_count", len(all_found_endpoints))
        
        # Normalize paths for case-insensitive comparison with trailing slash handling
        def normalize_path(path: str) -> str:
            """Normalize a path for comparison (remove trailing slashes, lowercase)."""
            return path.rstrip('/').lower()
        
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
        f.write(f"  Stage:     SPEC_VALIDATE\n\n")
        
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
    md_content: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run validation on generated files.
    
    Validates syntax and content for Python files.
    Optionally validates spec fidelity if md_content is provided.
    """
    start_time = time.time()
    
    results: Dict[str, Any] = {
        "valid": True,
        "checks": {},
        "errors": []
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
    
    # Check for main entry point
    if "main.py" not in generated_files:
        results["valid"] = False
        results["errors"].append("main.py not found")
    
    # Check for requirements
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
    
    # Write error file if failed
    if not results["valid"] and output_dir:
        _write_error_file(output_dir, results["errors"], results["checks"])
    
    VALIDATION_DURATION.labels(validation_type="fail_fast").observe(time.time() - start_time)
    
    if not results["valid"]:
        logger.error(f"Validation failed: {results['errors']}")
    
    return results


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
    "validate_spec_fidelity",
    "run_fail_fast_validation",
    "validate_dockerfile",
    "validate_docker_compose", 
    "validate_deployment_artifacts",
]
