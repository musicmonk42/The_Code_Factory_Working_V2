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
    
    Parses the MD content looking for API route patterns such as:
    - Explicit route definitions: `GET /api/users`, `POST /api/items`
    - Table formats: `| GET | /api/users | ... |`
    - Code blocks with route definitions
    - Bullet points with endpoints: `- GET /api/users`
    
    Args:
        md_content: Markdown specification content
        
    Returns:
        List of endpoint dictionaries with 'method' and 'path' keys
    """
    endpoints = []
    seen = set()  # Avoid duplicates
    
    # HTTP methods to look for
    http_methods = r'(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)'
    
    # Pattern 1: Explicit HTTP method + path (e.g., "GET /api/users")
    # Handles markdown emphasis: **GET** /api/users, *POST* /api/items
    pattern1 = rf'\*{{0,2}}{http_methods}\*{{0,2}}\s+[`"]?(/[^\s`"\)]+)[`"]?'
    
    # Pattern 2: Table format (e.g., "| GET | /api/users |")
    pattern2 = rf'\|\s*{http_methods}\s*\|\s*[`"]?(/[^\s`"\|]+)[`"]?'
    
    # Pattern 3: Backtick format (e.g., "`GET /api/users`")
    pattern3 = rf'`{http_methods}\s+(/[^`]+)`'
    
    # Pattern 4: API path with methods in context (e.g., "Endpoint: /api/users (GET, POST)")
    pattern4 = r'(?:endpoint|route|path|url):\s*[`"]?(/[^\s`"\)]+)[`"]?\s*\(([^)]+)\)'
    
    patterns = [
        (pattern1, False),  # (pattern, swap_order)
        (pattern2, False),
        (pattern3, False),
        (pattern4, True),   # Path comes first, then methods
    ]
    
    for pattern, swap_order in patterns:
        matches = re.findall(pattern, md_content, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            if swap_order:
                path, methods_str = match
                # Split multiple methods (e.g., "GET, POST")
                for method in re.findall(http_methods, methods_str, re.IGNORECASE):
                    key = (method.upper(), path)
                    if key not in seen:
                        seen.add(key)
                        endpoints.append({"method": method.upper(), "path": path})
            else:
                method, path = match
                key = (method.upper(), path)
                if key not in seen:
                    seen.add(key)
                    endpoints.append({"method": method.upper(), "path": path})
    
    # Sort by path for consistent ordering
    endpoints.sort(key=lambda e: (e["path"], e["method"]))
    
    return endpoints


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
    
    Args:
        md_content: The original Markdown spec content
        generated_files: Dictionary of generated file contents
        output_dir: Optional directory to write error.txt on failure
        
    Returns:
        Validation result dictionary with:
        - valid: bool indicating if all required routes are present
        - required_endpoints: list of endpoints from the spec
        - found_endpoints: list of endpoints found in generated code
        - missing_endpoints: list of endpoints missing from generated code
        - errors: list of error messages
    """
    start_time = time.time()
    
    result = {
        "valid": True,
        "required_endpoints": [],
        "found_endpoints": [],
        "missing_endpoints": [],
        "extra_endpoints": [],
        "errors": []
    }
    
    # Extract required endpoints from MD spec
    required_endpoints = extract_endpoints_from_md(md_content)
    result["required_endpoints"] = required_endpoints
    
    if not required_endpoints:
        # No endpoints specified in MD - that's OK, just log and pass
        logger.info("[SPEC_VALIDATE] No API endpoints found in MD spec - skipping endpoint validation")
        result["valid"] = True
        return result
    
    # Extract endpoints from all Python files in generated code
    all_found_endpoints = []
    for filename, content in generated_files.items():
        if filename.endswith('.py'):
            file_endpoints = extract_endpoints_from_code(content)
            all_found_endpoints.extend(file_endpoints)
    
    result["found_endpoints"] = all_found_endpoints
    
    # Check for missing endpoints - normalize paths for comparison
    def normalize_path(path: str) -> str:
        """Normalize a path for comparison (remove trailing slashes, etc.)"""
        return path.rstrip('/').lower()
    
    found_set = {(e["method"], normalize_path(e["path"])) for e in all_found_endpoints}
    
    missing = []
    for endpoint in required_endpoints:
        key = (endpoint["method"], normalize_path(endpoint["path"]))
        if key not in found_set:
            missing.append(endpoint)
    
    result["missing_endpoints"] = missing
    
    if missing:
        result["valid"] = False
        for ep in missing:
            error_msg = f"Missing required endpoint: {ep['method']} {ep['path']}"
            result["errors"].append(error_msg)
            logger.error(f"[SPEC_VALIDATE] {error_msg}")
    
    # Write error file if validation failed
    if not result["valid"] and output_dir:
        _write_spec_error_file(output_dir, result)
    
    VALIDATION_DURATION.labels(validation_type="spec_fidelity").observe(time.time() - start_time)
    
    if result["valid"]:
        logger.info(
            f"[SPEC_VALIDATE] Passed - all {len(required_endpoints)} required endpoints found",
            extra={"required": len(required_endpoints), "found": len(all_found_endpoints)}
        )
    else:
        logger.error(
            f"[SPEC_VALIDATE] Failed - {len(missing)} endpoints missing",
            extra={"missing": missing}
        )
    
    return result


def _write_spec_error_file(output_dir: str, result: Dict[str, Any]) -> None:
    """Write spec validation errors to error.txt."""
    error_path = Path(output_dir) / "error.txt"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(error_path, "w", encoding="utf-8") as f:
        f.write("SPEC FIDELITY VALIDATION FAILED\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n\n")
        
        f.write("Missing Required Endpoints:\n")
        for ep in result.get("missing_endpoints", []):
            f.write(f"  - {ep['method']} {ep['path']}\n")
        
        f.write(f"\nRequired endpoints from spec: {len(result.get('required_endpoints', []))}\n")
        for ep in result.get("required_endpoints", []):
            f.write(f"  - {ep['method']} {ep['path']}\n")
        
        f.write(f"\nEndpoints found in generated code: {len(result.get('found_endpoints', []))}\n")
        for ep in result.get("found_endpoints", []):
            f.write(f"  - {ep['method']} {ep['path']}\n")
    
    logger.info(f"[SPEC_VALIDATE] Error file written to {error_path}")


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
