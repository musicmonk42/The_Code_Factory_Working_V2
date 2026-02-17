# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
SFE (Self-Fixing Engineer) utility functions.

Shared utilities for SFE services to ensure consistency and reduce code duplication.
"""

import hashlib
from typing import Any, Dict, List


def transform_pipeline_issues_to_frontend_errors(
    issues: List[Dict[str, Any]], job_id: str
) -> List[Dict[str, Any]]:
    """
    Transform pipeline format issues to frontend error format.
    
    Converts issues from the SFE pipeline format (with fields like risk_level,
    details.message, etc.) to the frontend-expected error format (with error_id,
    severity, file, line, message, type).
    
    Args:
        issues: List of issues in pipeline format
        job_id: Job identifier to include in each error
        
    Returns:
        List of errors in frontend format with all required fields
        
    Example:
        >>> pipeline_issues = [{
        ...     "type": "ImportError",
        ...     "risk_level": "high",
        ...     "file": "main.py",
        ...     "details": {"message": "Module not found", "line": 15}
        ... }]
        >>> errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-123")
        >>> errors[0]["error_id"]  # Returns unique ID
        'err-a1b2c3d4'
        >>> errors[0]["severity"]
        'high'
    """
    errors = []
    
    for issue in issues:
        # Extract fields from pipeline format
        severity = issue.get("risk_level", "medium")
        details = issue.get("details", {})
        issue_type = issue.get("type", "unknown")
        
        # Determine file path (check both top-level and details)
        file_path = issue.get("file", details.get("file", "unknown"))
        
        # Get line number
        line = details.get("line", 0)
        
        # Get message
        message = details.get("message", str(issue))
        
        # Generate unique, deterministic error_id using hash of key identifying fields
        # This ensures the same issue gets the same ID across multiple calls
        id_components = f"{job_id}:{file_path}:{line}:{issue_type}:{message}"
        error_id = "err-" + hashlib.sha256(id_components.encode()).hexdigest()[:12]
        
        errors.append({
            "error_id": error_id,
            "job_id": job_id,
            "severity": severity,
            "message": message,
            "file": file_path,
            "line": line,
            "type": issue_type,
        })
    
    return errors
