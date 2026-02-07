#!/usr/bin/env python3
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Enterprise-Grade Dependency Verification Module for Code Factory Platform.

This module implements industry-standard dependency verification following:
- ISO 27001 A.12.6.1: Technical vulnerability management
- SOC 2 Type II CC6.1: System component integrity verification
- NIST SP 800-53 CM-8: Information system component inventory

The verification follows a fail-fast at build time, graceful degradation at
runtime pattern, ensuring:
1. Clear, actionable error messages for operators
2. Structured output for automated monitoring
3. Exit codes compatible with CI/CD pipelines
4. Health check integration support

Usage:
    # Command line verification
    python -m server.verify_dependencies
    
    # Programmatic verification
    from server.verify_dependencies import DependencyVerifier
    verifier = DependencyVerifier()
    result = verifier.verify_all()
    
Exit codes:
    0 - All critical dependencies are installed and importable
    1 - One or more critical dependencies are missing or failed to import
    2 - Verification encountered an unexpected error

Author: Code Factory Platform Team
Version: 2.0.0
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# Configure module logger
logger = logging.getLogger(__name__)


class DependencyStatus(str, Enum):
    """Enumeration of dependency verification statuses."""
    
    INSTALLED = "installed"
    MISSING = "missing"
    IMPORT_ERROR = "import_error"
    VERSION_MISMATCH = "version_mismatch"
    UNKNOWN = "unknown"


@dataclass
class DependencyInfo:
    """
    Structured information about a dependency verification result.
    
    Attributes:
        module_name: Python module name for import
        package_name: PyPI package name for installation
        description: Human-readable description of the dependency's purpose
        status: Verification status
        version: Installed version (if available)
        error: Error message (if verification failed)
        required: Whether this dependency is critical for startup
        load_time_ms: Time taken to verify the dependency
    """
    
    module_name: str
    package_name: str
    description: str
    status: DependencyStatus = DependencyStatus.UNKNOWN
    version: Optional[str] = None
    error: Optional[str] = None
    required: bool = True
    load_time_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "module_name": self.module_name,
            "package_name": self.package_name,
            "description": self.description,
            "status": self.status.value,
            "version": self.version,
            "error": self.error,
            "required": self.required,
            "load_time_ms": round(self.load_time_ms, 2),
        }


@dataclass
class VerificationResult:
    """
    Complete verification result with structured data for monitoring.
    
    Attributes:
        success: Overall verification success
        critical_passed: All critical dependencies passed
        total_checked: Total number of dependencies checked
        critical_count: Number of critical dependencies
        passed_count: Number of dependencies that passed
        failed_count: Number of dependencies that failed
        dependencies: List of individual dependency results
        timestamp: ISO 8601 timestamp of verification
        duration_ms: Total verification duration in milliseconds
        environment: Runtime environment information
    """
    
    success: bool = False
    critical_passed: bool = False
    total_checked: int = 0
    critical_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    dependencies: List[DependencyInfo] = field(default_factory=list)
    timestamp: str = ""
    duration_ms: float = 0.0
    environment: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "critical_passed": self.critical_passed,
            "total_checked": self.total_checked,
            "critical_count": self.critical_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "timestamp": self.timestamp,
            "duration_ms": round(self.duration_ms, 2),
            "environment": self.environment,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


class DependencyVerifier:
    """
    Enterprise-grade dependency verifier with comprehensive checking.
    
    This class implements the Singleton pattern for consistent state
    across the application lifecycle.
    
    Features:
    - Parallel-safe verification
    - Structured result output
    - Integration with health checks
    - CI/CD pipeline compatibility
    """
    
    # Critical dependencies required for FastAPI server startup
    # These MUST be installed for the application to start
    CRITICAL_DEPENDENCIES: List[Tuple[str, str, str]] = [
        ("fastapi", "fastapi", "FastAPI web framework - core HTTP API"),
        ("uvicorn", "uvicorn", "ASGI server - serves the FastAPI application"),
        ("pydantic", "pydantic", "Data validation - request/response schemas"),
        ("starlette", "starlette", "ASGI toolkit - FastAPI foundation"),
    ]
    
    # Recommended dependencies for full functionality
    # Application can start without these but with reduced features
    RECOMMENDED_DEPENDENCIES: List[Tuple[str, str, str]] = [
        ("redis", "redis", "Redis client - caching and distributed locks (optional)"),
        ("httpx", "httpx", "HTTP client - async API calls and testing"),
        ("sqlalchemy", "SQLAlchemy", "SQL ORM - database persistence"),
        ("asyncpg", "asyncpg", "PostgreSQL driver - async database access"),
        ("aiohttp", "aiohttp", "Async HTTP - background HTTP operations"),
        ("pydantic_settings", "pydantic-settings", "Settings management"),
        ("prometheus_client", "prometheus_client", "Metrics exposition"),
        ("structlog", "structlog", "Structured logging"),
        ("tenacity", "tenacity", "Retry logic with exponential backoff"),
    ]
    
    _instance: Optional['DependencyVerifier'] = None
    _verification_cache: Optional[VerificationResult] = None
    _cache_ttl_seconds: int = 300  # Cache results for 5 minutes
    _cache_timestamp: float = 0.0
    
    def __new__(cls) -> 'DependencyVerifier':
        """Implement singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the verifier."""
        self._initialized = getattr(self, '_initialized', False)
        if self._initialized:
            return
        self._initialized = True
    
    def _check_single_dependency(
        self, 
        module_name: str, 
        package_name: str, 
        description: str,
        required: bool = True
    ) -> DependencyInfo:
        """
        Verify a single dependency with comprehensive error handling.
        
        Args:
            module_name: Python module name to import
            package_name: PyPI package name
            description: Human-readable description
            required: Whether this is a critical dependency
            
        Returns:
            DependencyInfo with verification results
        """
        start_time = time.monotonic()
        info = DependencyInfo(
            module_name=module_name,
            package_name=package_name,
            description=description,
            required=required,
        )
        
        try:
            # Attempt to import the module
            module = importlib.import_module(module_name)
            
            # Try to get version from multiple sources
            version = None
            
            # Method 1: __version__ attribute
            if hasattr(module, "__version__"):
                version = str(module.__version__)
            
            # Method 2: importlib.metadata (more reliable)
            if version is None:
                try:
                    version = importlib.metadata.version(package_name)
                except importlib.metadata.PackageNotFoundError:
                    pass
            
            # Method 3: VERSION attribute
            if version is None and hasattr(module, "VERSION"):
                version = str(module.VERSION)
            
            info.status = DependencyStatus.INSTALLED
            info.version = version or "unknown"
            
        except ImportError as e:
            info.status = DependencyStatus.MISSING
            info.error = str(e)
            logger.warning(
                f"Dependency '{package_name}' is missing: {e}",
                extra={"dependency": package_name, "required": required}
            )
            
        except Exception as e:
            info.status = DependencyStatus.IMPORT_ERROR
            info.error = f"{type(e).__name__}: {e}"
            logger.error(
                f"Error importing '{package_name}': {e}",
                extra={"dependency": package_name, "error_type": type(e).__name__}
            )
        
        info.load_time_ms = (time.monotonic() - start_time) * 1000
        return info
    
    def verify_critical(self) -> Tuple[bool, List[DependencyInfo]]:
        """
        Verify all critical dependencies.
        
        Returns:
            Tuple of (all_passed, list_of_dependency_info)
        """
        results = []
        all_passed = True
        
        for module_name, package_name, description in self.CRITICAL_DEPENDENCIES:
            info = self._check_single_dependency(
                module_name, package_name, description, required=True
            )
            results.append(info)
            if info.status != DependencyStatus.INSTALLED:
                all_passed = False
        
        return all_passed, results
    
    def verify_recommended(self) -> Tuple[bool, List[DependencyInfo]]:
        """
        Verify all recommended dependencies.
        
        Returns:
            Tuple of (all_passed, list_of_dependency_info)
        """
        results = []
        all_passed = True
        
        for module_name, package_name, description in self.RECOMMENDED_DEPENDENCIES:
            info = self._check_single_dependency(
                module_name, package_name, description, required=False
            )
            results.append(info)
            if info.status != DependencyStatus.INSTALLED:
                all_passed = False
        
        return all_passed, results
    
    def verify_all(self, use_cache: bool = True) -> VerificationResult:
        """
        Perform complete dependency verification.
        
        Args:
            use_cache: Whether to use cached results if available
            
        Returns:
            Complete VerificationResult
        """
        # Check cache
        now = time.monotonic()
        if (
            use_cache 
            and self._verification_cache is not None
            and (now - self._cache_timestamp) < self._cache_ttl_seconds
        ):
            return self._verification_cache
        
        start_time = time.monotonic()
        
        # Verify critical dependencies
        critical_passed, critical_deps = self.verify_critical()
        
        # Verify recommended dependencies
        _, recommended_deps = self.verify_recommended()
        
        # Combine results
        all_deps = critical_deps + recommended_deps
        passed = [d for d in all_deps if d.status == DependencyStatus.INSTALLED]
        failed = [d for d in all_deps if d.status != DependencyStatus.INSTALLED]
        
        result = VerificationResult(
            success=critical_passed,
            critical_passed=critical_passed,
            total_checked=len(all_deps),
            critical_count=len(critical_deps),
            passed_count=len(passed),
            failed_count=len(failed),
            dependencies=all_deps,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_ms=(time.monotonic() - start_time) * 1000,
            environment={
                "python_version": sys.version,
                "platform": sys.platform,
                "app_env": os.getenv("APP_ENV", "unknown"),
            }
        )
        
        # Cache the result
        self._verification_cache = result
        self._cache_timestamp = now
        
        return result
    
    def get_installation_command(self, result: VerificationResult) -> str:
        """
        Generate pip install command for missing dependencies.
        
        Args:
            result: Verification result
            
        Returns:
            pip install command string
        """
        missing = [
            d.package_name for d in result.dependencies
            if d.status != DependencyStatus.INSTALLED and d.required
        ]
        
        if not missing:
            return ""
        
        return f"pip install {' '.join(missing)}"
    
    def format_report(self, result: VerificationResult, verbose: bool = True) -> str:
        """
        Format verification result as human-readable report.
        
        Args:
            result: Verification result
            verbose: Include detailed information
            
        Returns:
            Formatted report string
        """
        lines = [
            "",
            "=" * 70,
            "CODE FACTORY PLATFORM - DEPENDENCY VERIFICATION REPORT",
            f"Timestamp: {result.timestamp}",
            f"Duration: {result.duration_ms:.2f}ms",
            "=" * 70,
            "",
        ]
        
        # Critical dependencies
        lines.append("CRITICAL DEPENDENCIES (required for startup):")
        lines.append("-" * 70)
        
        critical = [d for d in result.dependencies if d.required]
        for dep in critical:
            if dep.status == DependencyStatus.INSTALLED:
                lines.append(f"  ✅ {dep.package_name:<20} v{dep.version:<15} - {dep.description}")
            else:
                lines.append(f"  ❌ {dep.package_name:<20} {'MISSING':<15} - {dep.description}")
                if verbose and dep.error:
                    lines.append(f"     └─ {dep.error}")
        
        lines.append("")
        
        # Recommended dependencies
        if verbose:
            lines.append("RECOMMENDED DEPENDENCIES (optional for enhanced features):")
            lines.append("-" * 70)
            
            recommended = [d for d in result.dependencies if not d.required]
            for dep in recommended:
                if dep.status == DependencyStatus.INSTALLED:
                    lines.append(f"  ✅ {dep.package_name:<20} v{dep.version:<15} - {dep.description}")
                else:
                    lines.append(f"  ⚠️  {dep.package_name:<20} {'not installed':<15} - {dep.description}")
            
            lines.append("")
        
        # Summary
        lines.append("=" * 70)
        lines.append("SUMMARY")
        lines.append("=" * 70)
        lines.append(f"  Total checked:     {result.total_checked}")
        lines.append(f"  Passed:            {result.passed_count}")
        lines.append(f"  Failed:            {result.failed_count}")
        lines.append(f"  Critical passed:   {'Yes' if result.critical_passed else 'No'}")
        lines.append("")
        
        if result.success:
            lines.append("✅ VERIFICATION PASSED - Server can start")
        else:
            lines.append("❌ VERIFICATION FAILED - Missing critical dependencies")
            lines.append("")
            lines.append("To fix this issue, run:")
            install_cmd = self.get_installation_command(result)
            if install_cmd:
                lines.append(f"  {install_cmd}")
            lines.append("")
            lines.append("Or install all dependencies:")
            lines.append("  pip install -r requirements.txt")
        
        lines.append("=" * 70)
        lines.append("")
        
        return "\n".join(lines)


def get_dependency_verifier() -> DependencyVerifier:
    """
    Get the singleton dependency verifier instance.
    
    Returns:
        DependencyVerifier instance
    """
    return DependencyVerifier()


def verify_dependencies_quick() -> bool:
    """
    Quick verification check for use in startup code.
    
    Returns:
        True if all critical dependencies are available
    """
    verifier = get_dependency_verifier()
    result = verifier.verify_all()
    return result.critical_passed


def main() -> int:
    """
    Main entry point for command-line verification.
    
    Returns:
        Exit code (0 for success, 1 for failure, 2 for error)
    """
    try:
        # Check for JSON output flag
        json_output = "--json" in sys.argv or "-j" in sys.argv
        quiet = "--quiet" in sys.argv or "-q" in sys.argv
        
        verifier = DependencyVerifier()
        result = verifier.verify_all(use_cache=False)
        
        if json_output:
            print(result.to_json())
        elif not quiet:
            print(verifier.format_report(result, verbose=True))
        
        return 0 if result.success else 1
        
    except Exception as e:
        if not quiet:
            print(f"ERROR: Dependency verification failed: {e}", file=sys.stderr)
        logger.exception("Dependency verification error")
        return 2


if __name__ == "__main__":
    sys.exit(main())

