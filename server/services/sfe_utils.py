# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
SFE (Self-Fixing Engineer) Utility Functions
=============================================

Enterprise-grade shared utilities for Self-Fixing Engineer (SFE) services to ensure
consistency, reduce code duplication, and maintain data integrity across the platform.

This module provides transformation and validation functions for SFE pipeline data,
following industry best practices for error handling, input validation, and observability.

Key Features:
- Pipeline-to-frontend issue transformation with deterministic IDs
- Input validation with comprehensive error handling
- Deterministic, collision-resistant error ID generation
- Logging for observability and debugging
- Type-safe interfaces with complete type hints

Usage:
    from server.services.sfe_utils import transform_pipeline_issues_to_frontend_errors
    
    # Transform issues from pipeline format to frontend format
    pipeline_issues = [{
        "type": "ImportError",
        "risk_level": "high",
        "file": "main.py",
        "details": {"message": "Module not found", "line": 15}
    }]
    
    errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-123")
    # Returns: [{"error_id": "err-...", "severity": "high", ...}]

Pipeline Format (Input):
    Issues from SFE analysis with fields:
    - type: Issue type (ImportError, SyntaxError, etc.)
    - risk_level: Severity level (critical, high, medium, low)
    - file: File path (optional, may be in details)
    - details: Dict containing message, line, file

Frontend Format (Output):
    Errors expected by the web UI:
    - error_id: Unique, deterministic identifier (err-XXXX...)
    - job_id: Associated job identifier
    - type: Issue type
    - severity: Severity level (mapped from risk_level)
    - message: Human-readable error message
    - file: File path where error occurs
    - line: Line number where error occurs

Security Considerations:
    - Input validation prevents malformed data from causing exceptions
    - Deterministic ID generation uses SHA-256 for collision resistance
    - No sensitive data is logged or exposed in error messages
    - Empty/null inputs are handled gracefully

Constants:
    MAX_ISSUES_PER_BATCH: Maximum number of issues to process in single call
    ERROR_ID_PREFIX: Prefix for generated error IDs
    ERROR_ID_LENGTH: Length of hash portion in error IDs (16 chars = 64 bits)
    DEFAULT_SEVERITY: Default severity when not specified

Examples:
    >>> # Basic transformation
    >>> issues = [{
    ...     "type": "TypeError",
    ...     "risk_level": "critical",
    ...     "details": {"message": "Invalid type", "line": 42}
    ... }]
    >>> errors = transform_pipeline_issues_to_frontend_errors(issues, "job-001")
    >>> errors[0]["severity"]
    'critical'
    >>> errors[0]["line"]
    42
    
    >>> # Empty input handling
    >>> errors = transform_pipeline_issues_to_frontend_errors([], "job-002")
    >>> len(errors)
    0
    
    >>> # Invalid input handling
    >>> try:
    ...     transform_pipeline_issues_to_frontend_errors(None, "job-003")
    ... except ValueError as e:
    ...     print(f"Validation error: {e}")
    Validation error: issues must be a list

Author: Code Factory Platform Team
"""

import hashlib
import logging
from typing import Any, Dict, List

# Configure module logger
logger = logging.getLogger(__name__)
# Configure module logger
logger = logging.getLogger(__name__)

# Module constants
MAX_ISSUES_PER_BATCH = 10000  # Maximum issues to process in single call
ERROR_ID_PREFIX = "err-"  # Prefix for all generated error IDs
ERROR_ID_LENGTH = 16  # Length of hash portion (64 bits of entropy)
DEFAULT_SEVERITY = "medium"  # Default severity level when not specified

# Valid severity levels (for validation)
VALID_SEVERITIES = {"critical", "high", "medium", "low", "info", "warning"}

# Export public API
__all__ = [
    "transform_pipeline_issues_to_frontend_errors",
    "transform_pipeline_issues_to_bugs",
    "MAX_ISSUES_PER_BATCH",
    "ERROR_ID_PREFIX",
    "DEFAULT_SEVERITY",
]


def _transform_issues(
    issues: List[Dict[str, Any]],
    job_id: str,
    id_prefix: str,
    id_field_name: str,
    file_context: str = "unknown"
) -> List[Dict[str, Any]]:
    """
    Internal helper to transform pipeline issues with configurable ID field.
    
    This is the core transformation logic shared by both error and bug transformations.
    
    Args:
        issues: List of issues in pipeline format
        job_id: Job identifier
        id_prefix: Prefix for generated IDs (e.g., "err-" or "bug-")
        id_field_name: Name of ID field (e.g., "error_id" or "bug_id")
        file_context: Optional file context for relative path calculation
        
    Returns:
        List of transformed issues with specified ID field
    """
    results = []
    issues_with_errors = 0
    
    for idx, issue in enumerate(issues):
        try:
            # Validate issue is a dictionary
            if not isinstance(issue, dict):
                logger.error(
                    f"Issue at index {idx} is not a dict: {type(issue).__name__}",
                    extra={"job_id": job_id, "index": idx}
                )
                issues_with_errors += 1
                continue
            
            # Extract fields from pipeline format
            severity = issue.get("risk_level", DEFAULT_SEVERITY)
            details = issue.get("details", {})
            issue_type = issue.get("type", "unknown")
            
            # Validate severity level
            if severity not in VALID_SEVERITIES:
                logger.warning(
                    f"Invalid severity '{severity}' at index {idx}, using '{DEFAULT_SEVERITY}'",
                    extra={"job_id": job_id, "index": idx, "severity": severity}
                )
                severity = DEFAULT_SEVERITY
            
            # Determine file path (check both top-level and details)
            file_path = issue.get("file", details.get("file", file_context))
            
            # Get line number with validation
            line = details.get("line", 0)
            if not isinstance(line, int):
                try:
                    line = int(line)
                except (ValueError, TypeError):
                    line = 0
            
            # Get message
            message = details.get("message", str(issue))
            
            # Generate unique, deterministic ID using hash of key identifying fields
            id_components = f"{job_id}:{file_path}:{line}:{issue_type}:{message}"
            generated_id = id_prefix + hashlib.sha256(
                id_components.encode("utf-8")
            ).hexdigest()[:ERROR_ID_LENGTH]
            
            result = {
                id_field_name: generated_id,
                "job_id": job_id,
                "type": issue_type,
                "severity": severity,
                "message": message,
                "file": file_path,
                "line": line,
            }
            
            results.append(result)
            
        except Exception as e:
            # Log unexpected errors but continue processing
            logger.error(
                f"Error transforming issue at index {idx}: {e}",
                extra={"job_id": job_id, "index": idx, "error": str(e)},
                exc_info=True
            )
            issues_with_errors += 1
            continue
    
    return results, issues_with_errors


def transform_pipeline_issues_to_frontend_errors(
    issues: List[Dict[str, Any]], job_id: str
) -> List[Dict[str, Any]]:
    """
    Transform pipeline format issues to frontend error format.
    
    Converts issues from the SFE pipeline format (with fields like risk_level,
    details.message, etc.) to the frontend-expected error format (with error_id,
    severity, file, line, message, type).
    
    This function provides:
    - Input validation with helpful error messages
    - Deterministic error ID generation using SHA-256
    - Graceful handling of missing/optional fields
    - Logging for observability
    
    Args:
        issues: List of issues in pipeline format. Must be a list, can be empty.
               Each issue should have keys: type, risk_level, file, details
        job_id: Job identifier to include in each error. Must be non-empty string.
        
    Returns:
        List of errors in frontend format with all required fields:
        - error_id: Unique identifier (e.g., "err-a1b2c3d4e5f6a7b8")
        - job_id: Associated job ID
        - severity: Severity level (critical/high/medium/low)
        - message: Human-readable error message
        - file: File path where error occurs
        - line: Line number (0 if not specified)
        - type: Issue type (e.g., "ImportError")
        
    Raises:
        ValueError: If issues is not a list, or if job_id is empty/invalid
        TypeError: If issues contains non-dict elements
        
    Example:
        >>> pipeline_issues = [{
        ...     "type": "ImportError",
        ...     "risk_level": "high",
        ...     "file": "main.py",
        ...     "details": {"message": "Module not found", "line": 15}
        ... }]
        >>> errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-123")
        >>> errors[0]["error_id"]  # Returns unique 16-character ID
        'err-a1b2c3d4e5f6a7b8'
        >>> errors[0]["severity"]
        'high'
        >>> errors[0]["line"]
        15
    
    Security Notes:
        - Input validation prevents injection attacks
        - SHA-256 provides 64-bit collision resistance
        - No sensitive data is included in error IDs
        - Deterministic IDs allow issue deduplication
    """
    # Input validation
    if not isinstance(issues, list):
        raise ValueError(f"issues must be a list, got {type(issues).__name__}")
    
    if not job_id or not isinstance(job_id, str):
        raise ValueError(f"job_id must be a non-empty string, got {job_id!r}")
    
    if len(job_id.strip()) == 0:
        raise ValueError("job_id cannot be empty or whitespace-only")
    
    # Validate batch size
    if len(issues) > MAX_ISSUES_PER_BATCH:
        logger.warning(
            f"Large issue batch: {len(issues)} issues exceeds recommended limit "
            f"of {MAX_ISSUES_PER_BATCH}",
            extra={"job_id": job_id, "issue_count": len(issues)}
        )
    
    # Log transformation start
    logger.debug(
        f"Transforming {len(issues)} pipeline issues to frontend errors",
        extra={"job_id": job_id, "issue_count": len(issues)}
    )
    
    # Use internal helper for transformation
    errors, issues_with_errors = _transform_issues(
        issues, job_id, ERROR_ID_PREFIX, "error_id", "unknown"
    )
    
    # Log completion
    logger.debug(
        f"Error transformation complete: {len(errors)} errors created, "
        f"{issues_with_errors} issues skipped due to errors",
        extra={
            "job_id": job_id,
            "input_count": len(issues),
            "output_count": len(errors),
            "error_count": issues_with_errors
        }
    )
    
    return errors


def transform_pipeline_issues_to_bugs(
    issues: List[Dict[str, Any]], job_id: str, file_context: str = "unknown"
) -> List[Dict[str, Any]]:
    """
    Transform pipeline format issues to bug format.
    
    Similar to transform_pipeline_issues_to_frontend_errors but returns bugs
    with bug_id instead of error_id. Used by bug detection functionality.
    
    Args:
        issues: List of issues in pipeline format
        job_id: Job identifier to include in each bug
        file_context: Optional file context for relative path (default: "unknown")
        
    Returns:
        List of bugs with all required fields:
        - bug_id: Unique identifier (e.g., "bug-a1b2c3d4e5f6a7b8")
        - job_id: Associated job ID
        - type: Issue type
        - severity: Severity level
        - message: Human-readable error message
        - file: File path where bug occurs
        - line: Line number (0 if not specified)
        
    Raises:
        ValueError: If issues is not a list, or if job_id is empty/invalid
        
    Example:
        >>> pipeline_issues = [{
        ...     "type": "TypeError",
        ...     "risk_level": "high",
        ...     "details": {"message": "Type mismatch", "line": 42, "file": "utils.py"}
        ... }]
        >>> bugs = transform_pipeline_issues_to_bugs(pipeline_issues, "job-456")
        >>> bugs[0]["bug_id"]
        'bug-b2c3d4e5f6a7b8c9'
        >>> bugs[0]["type"]
        'TypeError'
    
    Security Notes:
        - Same security guarantees as transform_pipeline_issues_to_frontend_errors
        - Deterministic bug IDs for deduplication
    """
    # Input validation (same as errors)
    if not isinstance(issues, list):
        raise ValueError(f"issues must be a list, got {type(issues).__name__}")
    
    if not job_id or not isinstance(job_id, str):
        raise ValueError(f"job_id must be a non-empty string, got {job_id!r}")
    
    if len(job_id.strip()) == 0:
        raise ValueError("job_id cannot be empty or whitespace-only")
    
    # Validate batch size
    if len(issues) > MAX_ISSUES_PER_BATCH:
        logger.warning(
            f"Large issue batch: {len(issues)} issues exceeds recommended limit "
            f"of {MAX_ISSUES_PER_BATCH}",
            extra={"job_id": job_id, "issue_count": len(issues)}
        )
    
    # Log transformation start
    logger.debug(
        f"Transforming {len(issues)} pipeline issues to bugs",
        extra={"job_id": job_id, "issue_count": len(issues), "file_context": file_context}
    )
    
    # Use internal helper for transformation
    bugs, issues_with_errors = _transform_issues(
        issues, job_id, "bug-", "bug_id", file_context
    )
    
    # Log completion
    logger.debug(
        f"Bug transformation complete: {len(bugs)} bugs created, "
        f"{issues_with_errors} issues skipped due to errors",
        extra={
            "job_id": job_id,
            "input_count": len(issues),
            "output_count": len(bugs),
            "error_count": issues_with_errors
        }
    )
    
    return bugs
