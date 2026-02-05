"""
Provenance tracking module for the code generation pipeline.

This module provides structured logging and SHA256 tracking to record the state
of artifacts at each pipeline stage. This enables debugging of overwrite issues
and ensures MD specs are properly preserved through the generation process.

Stage Markers:
- [STAGE:READ_MD] - MD input file reading
- [STAGE:CODEGEN] - Code generation from LLM
- [STAGE:POSTPROCESS] - Post-processing/sanitization
- [STAGE:MATERIALIZE] - Writing files to disk
- [STAGE:TESTGEN] - Test generation
- [STAGE:PACKAGE] - Creating output zip/package
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProvenanceTracker:
    """
    Tracks provenance of artifacts through the code generation pipeline.
    
    Records SHA256 hashes and lengths of key artifacts at each stage to
    enable debugging and verification of pipeline behavior.
    """
    
    # Stage constants
    STAGE_READ_MD = "READ_MD"
    STAGE_CODEGEN = "CODEGEN"
    STAGE_POSTPROCESS = "POSTPROCESS"
    STAGE_MATERIALIZE = "MATERIALIZE"
    STAGE_TESTGEN = "TESTGEN"
    STAGE_PACKAGE = "PACKAGE"
    STAGE_VALIDATE = "VALIDATE"
    
    def __init__(self, job_id: Optional[str] = None):
        """
        Initialize the provenance tracker.
        
        Args:
            job_id: Unique identifier for this pipeline run
        """
        self.job_id = job_id or f"job-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.stages: List[Dict[str, Any]] = []
        self.artifacts: Dict[str, Dict[str, Any]] = {}
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.errors: List[Dict[str, Any]] = []
        
    @staticmethod
    def compute_sha256(content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    @staticmethod
    def compute_sha256_bytes(content: bytes) -> str:
        """Compute SHA256 hash of bytes content."""
        return hashlib.sha256(content).hexdigest()
    
    def record_stage(
        self,
        stage: str,
        artifacts: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record a pipeline stage with artifact snapshots.
        
        Args:
            stage: Stage identifier (e.g., STAGE_READ_MD)
            artifacts: Dict mapping artifact names to their content
            metadata: Additional metadata about the stage
        """
        stage_record = {
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "artifacts": {},
            "metadata": metadata or {}
        }
        
        if artifacts:
            for name, content in artifacts.items():
                if content is not None:
                    artifact_info = {
                        "sha256": self.compute_sha256(content),
                        "length": len(content),
                        "preview": content[:200] + "..." if len(content) > 200 else content
                    }
                    stage_record["artifacts"][name] = artifact_info
                    
                    # Track artifact history
                    if name not in self.artifacts:
                        self.artifacts[name] = {"history": []}
                    self.artifacts[name]["history"].append({
                        "stage": stage,
                        "sha256": artifact_info["sha256"],
                        "length": artifact_info["length"],
                        "timestamp": stage_record["timestamp"]
                    })
        
        self.stages.append(stage_record)
        logger.info(
            f"[STAGE:{stage}] Recorded {len(artifacts or {})} artifacts",
            extra={
                "stage": stage,
                "job_id": self.job_id,
                "artifact_count": len(artifacts or {}),
                "artifact_names": list((artifacts or {}).keys())
            }
        )
    
    def record_error(
        self,
        stage: str,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record an error that occurred during pipeline execution.
        
        Args:
            stage: Stage where the error occurred
            error_type: Type/category of error
            message: Error message
            details: Additional error details
        """
        error_record = {
            "stage": stage,
            "error_type": error_type,
            "message": message,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.errors.append(error_record)
        logger.error(
            f"[STAGE:{stage}] Error: {error_type} - {message}",
            extra={
                "stage": stage,
                "job_id": self.job_id,
                "error_type": error_type,
                **error_record
            }
        )
    
    def check_artifact_changed(self, artifact_name: str) -> bool:
        """
        Check if an artifact has changed across stages.
        
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
        
        hashes = [entry["sha256"] for entry in history]
        return len(set(hashes)) > 1
    
    def get_artifact_overwrites(self) -> Dict[str, List[Dict[str, str]]]:
        """
        Identify all artifact overwrites (content changes between stages).
        
        Returns:
            Dict mapping artifact names to their change history
        """
        overwrites = {}
        for name, data in self.artifacts.items():
            if self.check_artifact_changed(name):
                overwrites[name] = data["history"]
        return overwrites
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert provenance data to a dictionary."""
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "job_id": self.job_id,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "stages": self.stages,
            "artifacts": self.artifacts,
            "errors": self.errors,
            "overwrites_detected": self.get_artifact_overwrites(),
            "summary": {
                "total_stages": len(self.stages),
                "total_errors": len(self.errors),
                "artifacts_tracked": list(self.artifacts.keys()),
                "artifacts_with_overwrites": list(self.get_artifact_overwrites().keys())
            }
        }
    
    def save_to_file(self, output_dir: str) -> str:
        """
        Save provenance data to a JSON file.
        
        Args:
            output_dir: Directory to save the provenance file
            
        Returns:
            Path to the saved file
        """
        reports_dir = Path(output_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        provenance_path = reports_dir / "provenance.json"
        
        with open(provenance_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        
        logger.info(
            f"Provenance data saved to {provenance_path}",
            extra={
                "job_id": self.job_id,
                "provenance_path": str(provenance_path)
            }
        )
        
        return str(provenance_path)


def validate_calculator_routes(code_content: str) -> Dict[str, Any]:
    """
    Validate that calculator API routes are present in the generated code.
    
    Args:
        code_content: Python source code to validate
        
    Returns:
        Validation result with status and missing routes
    """
    required_routes = [
        "/api/calculate/add",
        "/api/calculate/subtract", 
        "/api/calculate/multiply",
        "/api/calculate/divide"
    ]
    
    # Alternative route formats to check
    alternative_patterns = [
        ["/calculate/add", "/calculate/subtract", "/calculate/multiply", "/calculate/divide"],
        ["/add", "/subtract", "/multiply", "/divide"],
    ]
    
    found_routes = []
    missing_routes = []
    
    for route in required_routes:
        if route in code_content:
            found_routes.append(route)
        else:
            missing_routes.append(route)
    
    # If main routes not found, check alternative patterns
    if missing_routes:
        for alt_routes in alternative_patterns:
            alt_found = sum(1 for r in alt_routes if r in code_content)
            if alt_found >= 3:  # Found most alternative routes
                return {
                    "valid": True,
                    "found_routes": [r for r in alt_routes if r in code_content],
                    "missing_routes": [],
                    "note": "Using alternative route format"
                }
    
    return {
        "valid": len(missing_routes) == 0,
        "found_routes": found_routes,
        "missing_routes": missing_routes
    }


def validate_divide_by_zero_handling(code_content: str) -> Dict[str, Any]:
    """
    Validate that divide-by-zero error handling is present.
    
    Args:
        code_content: Python source code to validate
        
    Returns:
        Validation result
    """
    patterns_to_check = [
        "ZeroDivisionError",
        "division by zero",
        "divide by zero", 
        "HTTPException",
        "b == 0",
        "b != 0",
        "divisor == 0",
        "divisor != 0"
    ]
    
    found_patterns = [p for p in patterns_to_check if p.lower() in code_content.lower()]
    
    return {
        "valid": len(found_patterns) > 0,
        "found_patterns": found_patterns,
        "has_http_exception": "HTTPException" in code_content
    }


def validate_requirements_txt(content: str) -> Dict[str, Any]:
    """
    Validate that requirements.txt contains necessary dependencies.
    
    Args:
        content: Contents of requirements.txt
        
    Returns:
        Validation result
    """
    required_deps = ["fastapi", "uvicorn", "pytest", "httpx"]
    
    content_lower = content.lower()
    found_deps = []
    missing_deps = []
    
    for dep in required_deps:
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
        Validation result
    """
    import ast
    
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
    
    This validates:
    1. Python syntax for main.py and models.py
    2. Presence of calculator routes
    3. Divide-by-zero handling
    4. Required dependencies in requirements.txt
    
    Args:
        generated_files: Dict mapping filenames to their content
        output_dir: Optional output directory for error.txt
        
    Returns:
        Validation result with all checks
    """
    results = {
        "valid": True,
        "checks": {},
        "errors": []
    }
    
    # Check main.py syntax and routes
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
    
    # Check models.py syntax if present
    models_py = generated_files.get("models.py", "")
    if models_py:
        syntax_result = validate_syntax(models_py, "models.py")
        results["checks"]["models_py_syntax"] = syntax_result
        if not syntax_result["valid"]:
            results["valid"] = False
            results["errors"].append(f"models.py syntax error: {syntax_result['error']}")
    
    # Check requirements.txt
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
        error_path = Path(output_dir) / "error.txt"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        with open(error_path, "w", encoding="utf-8") as f:
            f.write("PIPELINE VALIDATION FAILED\n")
            f.write("=" * 50 + "\n\n")
            f.write("Errors:\n")
            for error in results["errors"]:
                f.write(f"  - {error}\n")
            f.write("\n")
            f.write("Checks:\n")
            f.write(json.dumps(results["checks"], indent=2))
        
        logger.error(
            f"Fail-fast validation failed: {len(results['errors'])} errors",
            extra={"errors": results["errors"]}
        )
    
    return results
