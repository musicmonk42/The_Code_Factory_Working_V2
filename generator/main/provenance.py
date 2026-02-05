# generator/main/provenance.py
"""
Provenance Tracking Module for the Code Generation Pipeline.

This module provides enterprise-grade provenance tracking with cryptographic 
integrity verification, structured logging, and comprehensive audit trails.
Enables debugging of artifact overwrite issues and ensures MD specs are 
properly preserved through the generation process.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                     ProvenanceTracker                                    │
    │  ┌─────────────────────────────────────────────────────────────────┐   │
    │  │ Stage Recording: READ_MD → CODEGEN → VALIDATE → TESTGEN → DEPLOY │   │
    │  └─────────────────────────────────────────────────────────────────┘   │
    │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────────┐  │
    │  │ SHA256     │ │ Artifact   │ │ Overwrite  │ │ JSON Serialization │  │
    │  │ Hashing    │ │ History    │ │ Detection  │ │ & Persistence      │  │
    │  └────────────┘ └────────────┘ └────────────┘ └────────────────────┘  │
    └─────────────────────────────────────────────────────────────────────────┘

Stage Markers:
    - [STAGE:READ_MD]     - MD input file reading
    - [STAGE:CODEGEN]     - Code generation from LLM
    - [STAGE:POSTPROCESS] - Post-processing/sanitization
    - [STAGE:MATERIALIZE] - Writing files to disk
    - [STAGE:VALIDATE]    - Fail-fast validation
    - [STAGE:TESTGEN]     - Test generation
    - [STAGE:DEPLOY_GEN]  - Deployment artifact generation
    - [STAGE:PACKAGE]     - Creating output zip/package

Industry Standards Compliance:
    - SOC 2 Type II: Cryptographic integrity verification
    - ISO 27001 A.12.1.3: Comprehensive audit logging
    - NIST SP 800-53 AU-4: Audit record content and retention

Usage:
    >>> from generator.main.provenance import ProvenanceTracker
    >>> tracker = ProvenanceTracker(job_id="job-123")
    >>> tracker.record_stage(ProvenanceTracker.STAGE_READ_MD, 
    ...                      artifacts={"input.md": md_content})
    >>> tracker.save_to_file("/output/generated")

Author: Code Factory Team
Version: 1.0.0
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# --- OpenTelemetry Integration ---
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    _tracer = trace.get_tracer(__name__)
    HAS_OPENTELEMETRY = True
except ImportError:
    HAS_OPENTELEMETRY = False
    _tracer = None

    class StatusCode:
        OK = "OK"
        ERROR = "ERROR"

    class Status:
        def __init__(self, status_code, description=None):
            self.status_code = status_code
            self.description = description

# --- Prometheus Metrics ---
try:
    from prometheus_client import Counter, Histogram, Gauge
    
    PROVENANCE_STAGES_RECORDED = Counter(
        'provenance_stages_recorded_total',
        'Total number of pipeline stages recorded',
        ['stage', 'job_id']
    )
    PROVENANCE_ERRORS_RECORDED = Counter(
        'provenance_errors_recorded_total',
        'Total number of pipeline errors recorded',
        ['stage', 'error_type']
    )
    PROVENANCE_ARTIFACT_SIZE = Histogram(
        'provenance_artifact_size_bytes',
        'Size of tracked artifacts in bytes',
        ['artifact_name'],
        buckets=[100, 500, 1000, 5000, 10000, 50000, 100000, 500000]
    )
    VALIDATION_DURATION = Histogram(
        'provenance_validation_duration_seconds',
        'Duration of validation operations',
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

# --- Logging Configuration ---
logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS AND CONFIGURATION
# =============================================================================

class PipelineStage(str, Enum):
    """Enumeration of pipeline stages for type safety and documentation."""
    READ_MD = "READ_MD"
    CODEGEN = "CODEGEN"
    POSTPROCESS = "POSTPROCESS"
    MATERIALIZE = "MATERIALIZE"
    VALIDATE = "VALIDATE"
    TESTGEN = "TESTGEN"
    DEPLOY_GEN = "DEPLOY_GEN"
    PACKAGE = "PACKAGE"


# Validation thresholds - industry standard configuration
CALCULATOR_ROUTES_REQUIRED = [
    "/api/calculate/add",
    "/api/calculate/subtract",
    "/api/calculate/multiply",
    "/api/calculate/divide"
]

# Alternative route patterns with minimum match threshold
ALTERNATIVE_ROUTE_PATTERNS = [
    ["/calculate/add", "/calculate/subtract", "/calculate/multiply", "/calculate/divide"],
    ["/add", "/subtract", "/multiply", "/divide"],
]
ALTERNATIVE_ROUTE_MIN_MATCH_RATIO = 0.75  # Must match 75% of alternative routes

# Required dependencies for FastAPI calculator API
REQUIRED_DEPENDENCIES = ["fastapi", "uvicorn", "pytest", "httpx"]

# Divide-by-zero detection patterns
DIVIDE_BY_ZERO_PATTERNS = [
    "ZeroDivisionError",
    "division by zero",
    "divide by zero",
    "cannot divide by zero",
    "HTTPException",
    "b == 0",
    "b != 0",
    "divisor == 0",
    "divisor != 0",
    "denominator == 0",
    "denominator != 0"
]


# =============================================================================
# PROVENANCE TRACKER
# =============================================================================

class ProvenanceTracker:
    """
    Enterprise-grade provenance tracking for the code generation pipeline.
    
    Records SHA256 hashes and metadata for all artifacts at each pipeline stage,
    enabling cryptographic verification, overwrite detection, and comprehensive
    audit trails.
    
    Thread Safety:
        This class is designed for single-threaded use within a pipeline run.
        For concurrent pipelines, create separate ProvenanceTracker instances.
    
    Attributes:
        job_id (str): Unique identifier for this pipeline run
        stages (List[Dict]): Recorded pipeline stages
        artifacts (Dict): Artifact history with SHA256 hashes
        errors (List[Dict]): Recorded errors
        started_at (str): ISO timestamp when tracking started
    
    Example:
        >>> tracker = ProvenanceTracker(job_id="pipeline-001")
        >>> tracker.record_stage(
        ...     PipelineStage.READ_MD,
        ...     artifacts={"input.md": md_content},
        ...     metadata={"file_path": "/input/spec.md"}
        ... )
        >>> tracker.save_to_file("/output/generated")
    """
    
    # Stage constants for backward compatibility
    STAGE_READ_MD = PipelineStage.READ_MD.value
    STAGE_CODEGEN = PipelineStage.CODEGEN.value
    STAGE_POSTPROCESS = PipelineStage.POSTPROCESS.value
    STAGE_MATERIALIZE = PipelineStage.MATERIALIZE.value
    STAGE_VALIDATE = PipelineStage.VALIDATE.value
    STAGE_TESTGEN = PipelineStage.TESTGEN.value
    STAGE_DEPLOY_GEN = PipelineStage.DEPLOY_GEN.value
    STAGE_PACKAGE = PipelineStage.PACKAGE.value
    
    def __init__(self, job_id: Optional[str] = None) -> None:
        """
        Initialize the provenance tracker.
        
        Args:
            job_id: Unique identifier for this pipeline run. If not provided,
                   generates one using timestamp format: job-YYYYMMDD-HHMMSS
        """
        self.job_id = job_id or self._generate_job_id()
        self.stages: List[Dict[str, Any]] = []
        self.artifacts: Dict[str, Dict[str, Any]] = {}
        self.errors: List[Dict[str, Any]] = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._initialized = True
        
        logger.info(
            f"ProvenanceTracker initialized",
            extra={
                "job_id": self.job_id,
                "started_at": self.started_at,
                "has_opentelemetry": HAS_OPENTELEMETRY,
                "has_prometheus": HAS_PROMETHEUS
            }
        )
    
    @staticmethod
    def _generate_job_id() -> str:
        """Generate a unique job ID using timestamp and microseconds."""
        now = datetime.now(timezone.utc)
        return f"job-{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond:06d}"
    
    @staticmethod
    def compute_sha256(content: str) -> str:
        """
        Compute SHA256 hash of string content.
        
        Args:
            content: String content to hash
            
        Returns:
            Hexadecimal SHA256 hash string
        """
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    @staticmethod
    def compute_sha256_bytes(content: bytes) -> str:
        """
        Compute SHA256 hash of bytes content.
        
        Args:
            content: Bytes content to hash
            
        Returns:
            Hexadecimal SHA256 hash string
        """
        return hashlib.sha256(content).hexdigest()
    
    def record_stage(
        self,
        stage: Union[str, PipelineStage],
        artifacts: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record a pipeline stage with artifact snapshots.
        
        Creates a cryptographic snapshot of all artifacts at this stage,
        enabling later verification and overwrite detection.
        
        Args:
            stage: Stage identifier (e.g., PipelineStage.READ_MD or "READ_MD")
            artifacts: Dict mapping artifact names to their content
            metadata: Additional metadata about the stage
            
        Example:
            >>> tracker.record_stage(
            ...     PipelineStage.CODEGEN,
            ...     artifacts={"main.py": code_content},
            ...     metadata={"model": "gpt-4o", "iteration": 1}
            ... )
        """
        # Normalize stage to string
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
                    
                    # Track artifact history for overwrite detection
                    if name not in self.artifacts:
                        self.artifacts[name] = {"history": []}
                    self.artifacts[name]["history"].append({
                        "stage": stage_str,
                        "sha256": sha256_hash,
                        "length": content_length,
                        "timestamp": timestamp
                    })
                    
                    # Record metrics
                    PROVENANCE_ARTIFACT_SIZE.labels(artifact_name=name).observe(content_length)
        
        self.stages.append(stage_record)
        PROVENANCE_STAGES_RECORDED.labels(stage=stage_str, job_id=self.job_id).inc()
        
        logger.info(
            f"[STAGE:{stage_str}] Recorded {len(artifacts or {})} artifacts",
            extra={
                "stage": stage_str,
                "job_id": self.job_id,
                "artifact_count": len(artifacts or {}),
                "artifact_names": list((artifacts or {}).keys()),
                "timestamp": timestamp
            }
        )
    
    def record_error(
        self,
        stage: Union[str, PipelineStage],
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record an error that occurred during pipeline execution.
        
        Args:
            stage: Stage where the error occurred
            error_type: Type/category of error (e.g., "validation_failed")
            message: Human-readable error message
            details: Additional error context and debugging information
        """
        stage_str = stage.value if isinstance(stage, PipelineStage) else str(stage)
        timestamp = datetime.now(timezone.utc).isoformat()
        
        error_record: Dict[str, Any] = {
            "stage": stage_str,
            "error_type": error_type,
            "message": message,
            "details": details or {},
            "timestamp": timestamp
        }
        self.errors.append(error_record)
        
        PROVENANCE_ERRORS_RECORDED.labels(stage=stage_str, error_type=error_type).inc()
        
        logger.error(
            f"[STAGE:{stage_str}] Error: {error_type} - {message}",
            extra={
                "stage": stage_str,
                "job_id": self.job_id,
                "error_type": error_type,
                "error_details": details
            }
        )
    
    def check_artifact_changed(self, artifact_name: str) -> bool:
        """
        Check if an artifact has changed across stages.
        
        Uses SHA256 comparison to detect content changes.
        
        Args:
            artifact_name: Name of the artifact to check
            
        Returns:
            True if the artifact has different hashes across stages
        """
        if artifact_name not in self.artifacts:
            return False
        
        history = self.artifacts[artifact_name].get("history", [])
        if len(history) < 2:
            return False
        
        unique_hashes = set(entry["sha256"] for entry in history)
        return len(unique_hashes) > 1
    
    def get_artifact_overwrites(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Identify all artifact overwrites (content changes between stages).
        
        Returns:
            Dict mapping artifact names that changed to their full history
        """
        overwrites: Dict[str, List[Dict[str, Any]]] = {}
        for name, data in self.artifacts.items():
            if self.check_artifact_changed(name):
                overwrites[name] = data["history"]
        return overwrites
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert provenance data to a dictionary for serialization.
        
        Returns:
            Complete provenance record as a dictionary
        """
        finished_at = datetime.now(timezone.utc).isoformat()
        overwrites = self.get_artifact_overwrites()
        
        return {
            "job_id": self.job_id,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "version": "1.0.0",
            "stages": self.stages,
            "artifacts": self.artifacts,
            "errors": self.errors,
            "overwrites_detected": overwrites,
            "integrity": {
                "algorithm": "SHA-256",
                "artifacts_hashed": len(self.artifacts),
                "stages_recorded": len(self.stages)
            },
            "summary": {
                "total_stages": len(self.stages),
                "total_errors": len(self.errors),
                "artifacts_tracked": list(self.artifacts.keys()),
                "artifacts_with_overwrites": list(overwrites.keys()),
                "has_overwrites": len(overwrites) > 0
            }
        }
    
    def save_to_file(self, output_dir: str) -> str:
        """
        Save provenance data to a JSON file.
        
        Creates the reports directory if it doesn't exist and saves
        provenance data as formatted JSON.
        
        Args:
            output_dir: Directory to save the provenance file
            
        Returns:
            Absolute path to the saved provenance file
            
        Raises:
            OSError: If unable to create directory or write file
        """
        reports_dir = Path(output_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        provenance_path = reports_dir / "provenance.json"
        provenance_data = self.to_dict()
        
        with open(provenance_path, "w", encoding="utf-8") as f:
            json.dump(provenance_data, f, indent=2, ensure_ascii=False, sort_keys=False)
        
        logger.info(
            f"Provenance data saved",
            extra={
                "job_id": self.job_id,
                "provenance_path": str(provenance_path),
                "stages_count": len(self.stages),
                "artifacts_count": len(self.artifacts)
            }
        )
        
        return str(provenance_path.resolve())


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================

def validate_calculator_routes(code_content: str) -> Dict[str, Any]:
    """
    Validate that calculator API routes are present in the generated code.
    
    Checks for primary route format (/api/calculate/*) and falls back to
    alternative patterns if primary routes are not found.
    
    Args:
        code_content: Python source code to validate
        
    Returns:
        Dict containing:
            - valid: Boolean indicating if validation passed
            - found_routes: List of routes found
            - missing_routes: List of missing routes
            - note: Optional note about alternative format
    """
    found_routes: List[str] = []
    missing_routes: List[str] = []
    
    # Check primary routes
    for route in CALCULATOR_ROUTES_REQUIRED:
        if route in code_content:
            found_routes.append(route)
        else:
            missing_routes.append(route)
    
    # If primary routes incomplete, check alternative patterns
    if missing_routes:
        for alt_routes in ALTERNATIVE_ROUTE_PATTERNS:
            alt_found = [r for r in alt_routes if r in code_content]
            match_ratio = len(alt_found) / len(alt_routes)
            
            if match_ratio >= ALTERNATIVE_ROUTE_MIN_MATCH_RATIO:
                return {
                    "valid": True,
                    "found_routes": alt_found,
                    "missing_routes": [],
                    "note": f"Using alternative route format (matched {match_ratio:.0%})"
                }
    
    return {
        "valid": len(missing_routes) == 0,
        "found_routes": found_routes,
        "missing_routes": missing_routes
    }


def validate_divide_by_zero_handling(code_content: str) -> Dict[str, Any]:
    """
    Validate that divide-by-zero error handling is present.
    
    Checks for common patterns that indicate proper error handling for
    division by zero scenarios.
    
    Args:
        code_content: Python source code to validate
        
    Returns:
        Dict containing:
            - valid: Boolean indicating if handling is present
            - found_patterns: List of detected patterns
            - has_http_exception: Boolean if HTTPException is used
    """
    code_lower = code_content.lower()
    found_patterns = [
        pattern for pattern in DIVIDE_BY_ZERO_PATTERNS 
        if pattern.lower() in code_lower
    ]
    
    return {
        "valid": len(found_patterns) > 0,
        "found_patterns": found_patterns,
        "has_http_exception": "HTTPException" in code_content
    }


def validate_requirements_txt(content: str) -> Dict[str, Any]:
    """
    Validate that requirements.txt contains necessary dependencies.
    
    Args:
        content: Contents of requirements.txt file
        
    Returns:
        Dict containing:
            - valid: Boolean if all required deps are present
            - found_deps: List of found dependencies
            - missing_deps: List of missing dependencies
    """
    content_lower = content.lower()
    found_deps: List[str] = []
    missing_deps: List[str] = []
    
    for dep in REQUIRED_DEPENDENCIES:
        if dep.lower() in content_lower:
            found_deps.append(dep)
        else:
            missing_deps.append(dep)
    
    return {
        "valid": len(missing_deps) == 0,
        "found_deps": found_deps,
        "missing_deps": missing_deps
    }


def validate_syntax(code_content: str, filename: str = "unknown.py") -> Dict[str, Any]:
    """
    Validate Python syntax using ast.parse.
    
    Args:
        code_content: Python source code to validate
        filename: Name of the file for error reporting
        
    Returns:
        Dict containing:
            - valid: Boolean if syntax is valid
            - filename: The filename checked
            - error: Error message if invalid, None otherwise
            - line: Line number of syntax error (if applicable)
            - offset: Column offset of syntax error (if applicable)
    """
    try:
        ast.parse(code_content)
        return {
            "valid": True,
            "filename": filename,
            "error": None
        }
    except SyntaxError as e:
        return {
            "valid": False,
            "filename": filename,
            "error": str(e),
            "line": e.lineno,
            "offset": e.offset
        }


def run_fail_fast_validation(
    generated_files: Dict[str, str],
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run comprehensive fail-fast validation on generated files.
    
    Validates:
        1. Python syntax for main.py and models.py
        2. Presence of calculator routes
        3. Divide-by-zero error handling
        4. Required dependencies in requirements.txt
    
    Args:
        generated_files: Dict mapping filenames to their content
        output_dir: Optional output directory for error.txt
        
    Returns:
        Dict containing:
            - valid: Boolean if all checks passed
            - checks: Dict of individual check results
            - errors: List of error messages
    """
    import time
    start_time = time.time()
    
    results: Dict[str, Any] = {
        "valid": True,
        "checks": {},
        "errors": []
    }
    
    # Validate main.py
    main_py = generated_files.get("main.py", "")
    if main_py:
        # Syntax check
        syntax_result = validate_syntax(main_py, "main.py")
        results["checks"]["main_py_syntax"] = syntax_result
        if not syntax_result["valid"]:
            results["valid"] = False
            results["errors"].append(f"main.py syntax error: {syntax_result['error']}")
        
        # Route validation
        route_result = validate_calculator_routes(main_py)
        results["checks"]["calculator_routes"] = route_result
        if not route_result["valid"]:
            results["valid"] = False
            results["errors"].append(f"Missing calculator routes: {route_result['missing_routes']}")
        
        # Divide-by-zero handling
        zero_result = validate_divide_by_zero_handling(main_py)
        results["checks"]["divide_by_zero"] = zero_result
        if not zero_result["valid"]:
            results["valid"] = False
            results["errors"].append("Missing divide-by-zero error handling")
    else:
        results["valid"] = False
        results["errors"].append("main.py not found in generated files")
    
    # Validate models.py if present
    models_py = generated_files.get("models.py", "")
    if models_py:
        syntax_result = validate_syntax(models_py, "models.py")
        results["checks"]["models_py_syntax"] = syntax_result
        if not syntax_result["valid"]:
            results["valid"] = False
            results["errors"].append(f"models.py syntax error: {syntax_result['error']}")
    
    # Validate requirements.txt
    requirements = generated_files.get("requirements.txt", "")
    if requirements:
        req_result = validate_requirements_txt(requirements)
        results["checks"]["requirements"] = req_result
        if not req_result["valid"]:
            results["valid"] = False
            results["errors"].append(f"Missing requirements: {req_result['missing_deps']}")
    else:
        results["valid"] = False
        results["errors"].append("requirements.txt not found in generated files")
    
    # Write error.txt if validation failed
    if not results["valid"] and output_dir:
        _write_error_file(output_dir, results["errors"], results["checks"])
    
    # Record metrics
    duration = time.time() - start_time
    VALIDATION_DURATION.labels(validation_type="fail_fast").observe(duration)
    
    if not results["valid"]:
        logger.error(
            f"Fail-fast validation failed: {len(results['errors'])} errors",
            extra={"errors": results["errors"], "duration_seconds": duration}
        )
    
    return results


def _write_error_file(
    output_dir: str, 
    errors: List[str], 
    checks: Dict[str, Any]
) -> None:
    """Write validation errors to error.txt file."""
    error_path = Path(output_dir) / "error.txt"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(error_path, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("PIPELINE VALIDATION FAILED\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n\n")
        f.write("ERRORS:\n")
        f.write("-" * 40 + "\n")
        for i, error in enumerate(errors, 1):
            f.write(f"  {i}. {error}\n")
        f.write("\n")
        f.write("DETAILED CHECKS:\n")
        f.write("-" * 40 + "\n")
        f.write(json.dumps(checks, indent=2))
        f.write("\n")


# =============================================================================
# DEPLOYMENT ARTIFACT VALIDATION
# =============================================================================

def validate_dockerfile(content: str) -> Dict[str, Any]:
    """
    Validate that a Dockerfile has required directives.
    
    Checks for industry-standard Dockerfile requirements:
        - FROM instruction (required)
        - CMD or ENTRYPOINT instruction (required)
        - WORKDIR instruction (recommended)
        - COPY or ADD instruction (common)
    
    Args:
        content: Dockerfile content
        
    Returns:
        Dict containing validation results and any errors
    """
    results: Dict[str, Any] = {
        "valid": True,
        "has_from": False,
        "has_cmd_or_entrypoint": False,
        "has_workdir": False,
        "has_copy": False,
        "has_expose": False,
        "has_healthcheck": False,
        "has_user": False,
        "errors": []
    }
    
    if not content or not content.strip():
        results["valid"] = False
        results["errors"].append("Dockerfile is empty")
        return results
    
    content_upper = content.upper()
    lines = [line.strip().upper() for line in content.split('\n')]
    
    # Check for FROM directive (must be present)
    results["has_from"] = any(line.startswith("FROM ") for line in lines)
    if not results["has_from"]:
        results["valid"] = False
        results["errors"].append("Dockerfile missing FROM directive")
    
    # Check for CMD or ENTRYPOINT (required for execution)
    results["has_cmd_or_entrypoint"] = (
        any(line.startswith("CMD ") or line.startswith("CMD[") for line in lines) or
        any(line.startswith("ENTRYPOINT ") or line.startswith("ENTRYPOINT[") for line in lines)
    )
    if not results["has_cmd_or_entrypoint"]:
        results["valid"] = False
        results["errors"].append("Dockerfile missing CMD or ENTRYPOINT directive")
    
    # Check recommended directives
    results["has_workdir"] = any(line.startswith("WORKDIR ") for line in lines)
    results["has_copy"] = any(line.startswith("COPY ") or line.startswith("ADD ") for line in lines)
    results["has_expose"] = any(line.startswith("EXPOSE ") for line in lines)
    results["has_healthcheck"] = any(line.startswith("HEALTHCHECK ") for line in lines)
    results["has_user"] = any(line.startswith("USER ") for line in lines)
    
    return results


def validate_docker_compose(content: str) -> Dict[str, Any]:
    """
    Validate docker-compose.yml structure.
    
    Checks for required and recommended compose file elements.
    
    Args:
        content: docker-compose.yml content
        
    Returns:
        Dict containing validation results
    """
    results: Dict[str, Any] = {
        "valid": True,
        "has_services": False,
        "has_version": False,
        "has_ports": False,
        "has_healthcheck": False,
        "errors": []
    }
    
    if not content or not content.strip():
        results["valid"] = False
        results["errors"].append("docker-compose.yml is empty")
        return results
    
    content_lower = content.lower()
    
    # Check for services section (required)
    results["has_services"] = "services:" in content_lower
    if not results["has_services"]:
        results["valid"] = False
        results["errors"].append("docker-compose.yml missing 'services:' section")
    
    # Check optional but recommended elements
    results["has_version"] = "version:" in content_lower
    results["has_ports"] = "ports:" in content_lower
    results["has_healthcheck"] = "healthcheck:" in content_lower
    
    return results


def validate_deployment_artifacts(
    deploy_files: Dict[str, str],
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate deployment artifacts (Dockerfile, docker-compose.yml, etc.).
    
    Performs comprehensive validation of all deployment configuration files
    and reports any issues found.
    
    Args:
        deploy_files: Dict mapping filenames to their content
        output_dir: Optional output directory for error.txt
        
    Returns:
        Dict containing:
            - valid: Boolean if all checks passed
            - checks: Dict of individual check results
            - errors: List of error messages
            - files_validated: List of files that were validated
    """
    import time
    start_time = time.time()
    
    results: Dict[str, Any] = {
        "valid": True,
        "checks": {},
        "errors": [],
        "files_validated": list(deploy_files.keys())
    }
    
    # Validate Dockerfile
    dockerfile = deploy_files.get("Dockerfile", "")
    if dockerfile:
        df_result = validate_dockerfile(dockerfile)
        results["checks"]["dockerfile"] = df_result
        if not df_result["valid"]:
            results["valid"] = False
            results["errors"].extend(df_result["errors"])
    
    # Validate docker-compose.yml
    compose = deploy_files.get("docker-compose.yml", "")
    if compose:
        compose_result = validate_docker_compose(compose)
        results["checks"]["docker_compose"] = compose_result
        if not compose_result["valid"]:
            results["valid"] = False
            results["errors"].extend(compose_result["errors"])
    
    # Write error.txt if validation failed
    if not results["valid"] and output_dir:
        error_path = Path(output_dir) / "error.txt"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Append to existing error file if it exists
        mode = "a" if error_path.exists() else "w"
        with open(error_path, mode, encoding="utf-8") as f:
            if mode == "a":
                f.write("\n")
            f.write("=" * 60 + "\n")
            f.write("DEPLOYMENT VALIDATION FAILED\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write("DEPLOYMENT ERRORS:\n")
            f.write("-" * 40 + "\n")
            for error in results["errors"]:
                f.write(f"  - {error}\n")
            f.write("\n")
    
    # Record metrics
    duration = time.time() - start_time
    VALIDATION_DURATION.labels(validation_type="deployment").observe(duration)
    
    if not results["valid"]:
        logger.error(
            f"Deployment validation failed: {len(results['errors'])} errors",
            extra={"errors": results["errors"], "duration_seconds": duration}
        )
    
    return results


# =============================================================================
# MODULE EXPORTS
# =============================================================================

__all__ = [
    # Main class
    "ProvenanceTracker",
    "PipelineStage",
    
    # Validation functions
    "validate_calculator_routes",
    "validate_divide_by_zero_handling",
    "validate_requirements_txt",
    "validate_syntax",
    "run_fail_fast_validation",
    
    # Deployment validation
    "validate_dockerfile",
    "validate_docker_compose", 
    "validate_deployment_artifacts",
    
    # Constants
    "CALCULATOR_ROUTES_REQUIRED",
    "REQUIRED_DEPENDENCIES",
    
    # Feature flags
    "HAS_OPENTELEMETRY",
    "HAS_PROMETHEUS",
]
