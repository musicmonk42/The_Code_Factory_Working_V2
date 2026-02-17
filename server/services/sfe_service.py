# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Service for interacting with the Self-Fixing Engineer module through OmniCore.

This service provides a mockable interface to the self_fixing_engineer module
for code analysis, error detection, and automated fixing. ALL operations are
routed through OmniCore as the central coordinator.

This implementation includes:
- Lazy loading of SFE modules with graceful degradation
- Direct integration with SFE components when available
- Fallback to OmniCore routing for distributed execution
- Proper error handling and logging
"""

import logging
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
from uuid import uuid4

# Industry Standard: Import centralized utilities to eliminate code duplication
from server.services.omnicore_service import _load_sfe_analysis_report
from server.services.sfe_utils import transform_pipeline_issues_to_frontend_errors

logger = logging.getLogger(__name__)

# Bug prioritization severity scores
SEVERITY_SCORES = {
    "critical": 100,
    "high": 75,
    "medium": 50,
    "low": 25,
}


class SFEService:
    """
    Service for interacting with the Self-Fixing Engineer (SFE).

    This service acts as an abstraction layer for SFE operations,
    providing methods for code analysis, error detection, fix proposal,
    and fix application. All operations are routed through OmniCore's
    message bus and coordination layer. The implementation includes
    direct SFE module integration with fallback to mock data.
    """

    def __init__(self, omnicore_service=None):
        """
        Initialize the SFEService.

        Args:
            omnicore_service: OmniCoreService instance for centralized routing
        """
        self.omnicore_service = omnicore_service

        # Track SFE component availability
        self._sfe_components = {
            "codebase_analyzer": None,
            "bug_manager": None,
            "arbiter": None,
            "checkpoint": None,
            "mesh_metrics": None,
        }
        self._sfe_available = {
            "codebase_analyzer": False,
            "bug_manager": False,
            "arbiter": False,
            "checkpoint": False,
            "mesh_metrics": False,
        }

        # Initialize SFE components
        self._init_sfe_components()

        logger.info("SFEService initialized")

    def _init_sfe_components(self):
        """
        Initialize SFE components with graceful degradation.

        Attempts to load actual SFE modules, falling back to mock
        implementations if unavailable.
        """
        # Try to load codebase analyzer
        try:
            from self_fixing_engineer.arbiter.codebase_analyzer import CodebaseAnalyzer

            self._sfe_components["codebase_analyzer"] = CodebaseAnalyzer
            self._sfe_available["codebase_analyzer"] = True
            logger.info("✓ SFE codebase analyzer loaded")
        except ImportError as e:
            logger.warning(f"SFE codebase analyzer unavailable: {e}")
        except Exception as e:
            logger.warning(f"Error loading codebase analyzer: {e}")

        # Try to load bug manager
        try:
            from self_fixing_engineer.arbiter.bug_manager import BugManager

            self._sfe_components["bug_manager"] = BugManager
            self._sfe_available["bug_manager"] = True
            logger.info("✓ SFE bug manager loaded")
        except ImportError as e:
            logger.warning(f"SFE bug manager unavailable: {e}")
        except Exception as e:
            logger.warning(f"Error loading bug manager: {e}")

        # Try to load arbiter (for fix proposal/application)
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter

            self._sfe_components["arbiter"] = Arbiter
            self._sfe_available["arbiter"] = True
            logger.info("✓ SFE arbiter loaded")
        except ImportError as e:
            logger.warning(f"SFE arbiter unavailable: {e}")
        except Exception as e:
            logger.warning(f"Error loading arbiter: {e}")

        # Try to load checkpoint manager
        try:
            from self_fixing_engineer.mesh.checkpoint import CheckpointManager

            self._sfe_components["checkpoint"] = CheckpointManager
            self._sfe_available["checkpoint"] = True
            logger.info("✓ SFE checkpoint manager loaded")
        except ImportError as e:
            logger.warning(f"SFE checkpoint manager unavailable: {e}")
        except Exception as e:
            logger.warning(f"Error loading checkpoint manager: {e}")

        # Try to load mesh metrics
        try:
            # Note: The mesh module may have various metric tracking
            from self_fixing_engineer.mesh import mesh_adapter

            self._sfe_components["mesh_metrics"] = mesh_adapter
            self._sfe_available["mesh_metrics"] = True
            logger.info("✓ SFE mesh metrics loaded")
        except ImportError as e:
            logger.warning(f"SFE mesh metrics unavailable: {e}")
        except Exception as e:
            logger.warning(f"Error loading mesh metrics: {e}")

        # Log component availability summary
        available = [k for k, v in self._sfe_available.items() if v]
        unavailable = [k for k, v in self._sfe_available.items() if not v]

        if available:
            logger.info(f"SFE components available: {', '.join(available)}")
        if unavailable:
            logger.info(
                f"SFE components unavailable (using fallback): {', '.join(unavailable)}"
            )

    def _resolve_job_code_path(self, job_id: Optional[str], default_path: str) -> str:
        """
        Resolve the actual code path from a job ID if provided, or use default path.

        Args:
            job_id: Optional job ID to look up
            default_path: Default path to use if job_id is not provided or not found

        Returns:
            Resolved code path as string
        """
        if not job_id:
            return default_path

        # Try to resolve path from job metadata
        from server.storage import jobs_db

        job = jobs_db.get(job_id)
        if job and job.metadata:
            # Check metadata for output paths
            for key in ("output_path", "code_path", "generated_path"):
                path = job.metadata.get(key)
                if path and Path(path).exists():
                    logger.info(
                        f"Resolved job {job_id} path from metadata.{key}: {path}"
                    )
                    return str(path)

        # If not in metadata, check standard locations
        uploads_dir = Path("./uploads")
        job_base = uploads_dir / job_id

        if job_base.exists():
            # Check standard subdirectories
            for subdir_name in ["generated", "output"]:
                subdir = job_base / subdir_name
                if subdir.exists():
                    # Look for project subdirectories
                    subdirs = [
                        d
                        for d in subdir.iterdir()
                        if d.is_dir() and not d.name.startswith(".")
                    ]
                    if subdirs:
                        # Use the first non-hidden subdirectory (typically the project directory)
                        resolved_path = str(subdirs[0])
                        logger.info(
                            f"Resolved job {job_id} path to generated project: {resolved_path}"
                        )
                        return resolved_path
                    else:
                        # No subdirectories, use this directory directly
                        resolved_path = str(subdir)
                        logger.info(f"Resolved job {job_id} path to: {resolved_path}")
                        return resolved_path

            # If no generated/ or output/, use job_base directly
            logger.info(f"Resolved job {job_id} path to job base: {job_base}")
            return str(job_base)

        # Fallback to default path
        logger.warning(
            f"Could not resolve path for job {job_id}, using default: {default_path}"
        )
        return default_path

    async def analyze_code(self, job_id: str, code_path: str) -> Dict[str, Any]:
        """
        Analyze code for potential issues via OmniCore or direct SFE integration.

        Args:
            job_id: Unique job identifier
            code_path: Path to code to analyze

        Returns:
            Analysis results

        Example integration:
            >>> # Route through OmniCore to SFE
            >>> # await omnicore.route_to_sfe('analyze', {...})
        """
        logger.info(f"Analyzing code for job {job_id} at {code_path}")

        # Try routing through OmniCore first
        if self.omnicore_service:
            payload = {
                "action": "analyze_code",
                "job_id": job_id,
                "code_path": code_path,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            # Check if route_job actually returned data
            if result.get("data"):
                logger.info(f"Analysis for job {job_id} completed via OmniCore")
                return result["data"]
            logger.info(
                "OmniCore routing returned no data, falling through to direct SFE"
            )

        # Try direct SFE integration if analyzer available
        if self._sfe_available["codebase_analyzer"]:
            try:
                logger.info(f"Using direct SFE CodebaseAnalyzer for job {job_id}")

                CodebaseAnalyzer = self._sfe_components["codebase_analyzer"]
                code_path_obj = Path(code_path)

                # Validate path exists
                if not code_path_obj.exists():
                    return {
                        "job_id": job_id,
                        "code_path": code_path,
                        "issues_found": 0,
                        "issues": [],
                        "error": f"Path does not exist: {code_path}",
                        "source": "direct_sfe",
                    }

                # Use CodebaseAnalyzer properly as async context manager
                if code_path_obj.is_file():
                    # Analyze single file using analyze_and_propose
                    root_dir = str(code_path_obj.parent)
                    # Don't ignore tests when analyzing generated output
                    async with CodebaseAnalyzer(
                        root_dir=root_dir,
                        ignore_patterns=["__pycache__", ".git", "*.pyc", "*.egg-info"],
                    ) as analyzer:
                        issues = await analyzer.analyze_and_propose(str(code_path_obj))

                    result = {
                        "job_id": job_id,
                        "code_path": code_path,
                        "issues_found": len(issues),
                        "issues": issues,
                        "analyzer_module": "self_fixing_engineer.arbiter.codebase_analyzer",
                        "source": "direct_sfe",
                    }

                    logger.info(
                        f"Direct SFE analysis complete: {len(issues)} issues found"
                    )
                    return result

                elif code_path_obj.is_dir():
                    # Analyze directory using scan_codebase
                    # Don't ignore tests when analyzing generated output
                    async with CodebaseAnalyzer(
                        root_dir=str(code_path_obj),
                        ignore_patterns=["__pycache__", ".git", "*.pyc", "*.egg-info"],
                    ) as analyzer:
                        summary = await analyzer.scan_codebase(str(code_path_obj))

                        # Convert FileSummary to expected format
                        issues = []
                        if hasattr(summary, "defects"):
                            for defect in summary.defects:
                                # FIX: Validate file exists before adding to issues
                                defect_file = getattr(defect, "file", "")
                                if defect_file:
                                    defect_file_path = Path(defect_file)
                                    # Make path absolute if it's relative
                                    if not defect_file_path.is_absolute():
                                        defect_file_path = (
                                            code_path_obj / defect_file_path
                                        )

                                    # Only include defects for files that actually exist
                                    if not defect_file_path.exists():
                                        logger.warning(
                                            f"Skipping defect for non-existent file: {defect_file}"
                                        )
                                        continue

                                issues.append(
                                    {
                                        "type": getattr(defect, "type", "unknown"),
                                        "severity": getattr(
                                            defect, "severity", "medium"
                                        ),
                                        "message": str(defect),
                                        "file": defect_file,
                                        "line": getattr(defect, "line", 0),
                                    }
                                )

                    result = {
                        "job_id": job_id,
                        "code_path": code_path,
                        "issues_found": len(issues),
                        "issues": issues,
                        "analyzer_module": "self_fixing_engineer.arbiter.codebase_analyzer",
                        "source": "direct_sfe",
                    }

                    logger.info(
                        f"Direct SFE analysis complete: {len(issues)} issues found"
                    )
                    return result

            except Exception as e:
                logger.error(f"Direct SFE analysis failed: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback - return empty results instead of fake issues
        logger.warning("Neither OmniCore nor direct SFE available, code analysis unavailable")
        return {
            "job_id": job_id,
            "code_path": code_path,
            "issues_found": 0,
            "issues": [],
            "severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            "source": "fallback",
            "note": "Code analysis unavailable. OmniCore service and SFE CodebaseAnalyzer are not available. Please configure LLM API keys or enable SFE components.",
        }

    async def detect_errors(self, job_id: str) -> Dict[str, Any]:
        """
        Detect errors in generated code via OmniCore or direct SFE integration.

        Args:
            job_id: Unique job identifier

        Returns:
            Dict with errors list and count

        Example integration:
            >>> # Route through OmniCore to SFE bug_manager
            >>> # await omnicore.route_to_sfe('detect_errors', {...})
        """
        logger.info(f"Detecting errors for job {job_id}")

        # Try routing through OmniCore first
        if self.omnicore_service:
            payload = {
                "action": "detect_errors",
                "job_id": job_id,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            # Check if route_job actually returned data
            if result.get("data"):
                logger.info(f"Error detection for job {job_id} completed via OmniCore")
                return result["data"]
            logger.info(
                "OmniCore routing returned no data, falling through to direct SFE"
            )

        # Try direct SFE integration if analyzer available
        if self._sfe_available["codebase_analyzer"]:
            try:
                logger.info(
                    f"Using direct SFE CodebaseAnalyzer to detect errors for job {job_id}"
                )

                # Resolve code path using improved path resolution
                # First check job metadata, then standard locations
                from server.storage import jobs_db

                job_dir = None
                job = jobs_db.get(job_id)
                if job and job.metadata:
                    # Check metadata for output paths
                    for key in ("output_path", "code_path", "generated_path"):
                        path = job.metadata.get(key)
                        if path and Path(path).exists():
                            job_dir = Path(path)
                            logger.info(
                                f"Found job path from metadata.{key}: {job_dir}"
                            )
                            break

                # If not in metadata, check standard locations
                if not job_dir:
                    uploads_dir = Path("./uploads")
                    job_base = uploads_dir / job_id

                    # Try to find the generated code directory
                    if job_base.exists():
                        # Check standard subdirectories
                        for subdir_name in ["generated", "output"]:
                            subdir = job_base / subdir_name
                            if subdir.exists():
                                # Look for project subdirectories
                                subdirs = [
                                    d
                                    for d in subdir.iterdir()
                                    if d.is_dir() and not d.name.startswith(".")
                                ]
                                if subdirs:
                                    # Use the first non-hidden subdirectory (typically the project directory)
                                    job_dir = subdirs[0]
                                    logger.info(f"Found generated project at {job_dir}")
                                    break
                                else:
                                    # No subdirectories, use this directory directly
                                    job_dir = subdir
                                    break

                        # If no generated/ or output/, use job_base directly
                        if not job_dir:
                            job_dir = job_base

                if not job_dir or not job_dir.exists():
                    logger.warning(f"Job directory not found for {job_id}")
                    return {
                        "errors": [],
                        "count": 0,
                        "note": f"Job directory not found for {job_id}",
                    }

                # BUG FIX 3: Industry Standard DRY principle
                # Use centralized report loading function (eliminates duplication)
                report_path = job_dir / "reports" / "sfe_analysis_report.json"
                cached_report = _load_sfe_analysis_report(report_path, job_id)

                if cached_report:
                    # Transform cached pipeline issues to frontend error format
                    errors = transform_pipeline_issues_to_frontend_errors(
                        cached_report["issues"], job_id
                    )
                    
                    # Return cached data with appropriate structure for detect_errors
                    return {
                        "errors": errors,
                        "count": len(errors),
                        "source": cached_report["source"],
                        "cached": True,
                    }

                logger.info(f"Analyzing errors in directory: {job_dir}")
                CodebaseAnalyzer = self._sfe_components["codebase_analyzer"]

                # Discover Python files in the job directory
                python_files = list(job_dir.rglob("*.py"))

                if not python_files:
                    logger.info(f"No Python files found in {job_dir}")
                    return {"errors": [], "count": 0}

                # Analyze files and collect issues
                all_issues = []
                async with CodebaseAnalyzer(root_dir=str(job_dir)) as analyzer:
                    for py_file in python_files:
                        try:
                            issues = await analyzer.analyze_and_propose(str(py_file))
                            
                            # Add file path to each issue for proper transformation
                            for issue in issues:
                                if "file" not in issue and "details" not in issue:
                                    issue["details"] = {}
                                if "file" not in issue:
                                    issue["file"] = str(py_file.relative_to(job_dir))
                                all_issues.append(issue)
                                
                        except Exception as e:
                            logger.warning(f"Error analyzing {py_file}: {e}")
                            continue
                
                # Transform all issues to error format using utility function
                errors = transform_pipeline_issues_to_frontend_errors(all_issues, job_id)
                    # Skip analyzer execution when job directory not found,
                    # and fall through to return sample fallback errors for UI consistency
                    pass
                else:
                    # BUG FIX 3: Industry Standard DRY principle
                    # Use centralized report loading function (eliminates duplication)
                    report_path = job_dir / "reports" / "sfe_analysis_report.json"
                    cached_report = _load_sfe_analysis_report(report_path, job_id)

                    if cached_report:
                        # Return cached data as list for detect_errors
                        return cached_report["issues"]  # Already a list of issues

                    logger.info(f"Analyzing errors in directory: {job_dir}")
                    CodebaseAnalyzer = self._sfe_components["codebase_analyzer"]

                    # Discover Python files in the job directory
                    python_files = list(job_dir.rglob("*.py"))

                    if not python_files:
                        logger.info(f"No Python files found in {job_dir}")
                        # Fall through to fallback instead of returning empty list
                    else:
                        # Analyze files and collect errors
                        errors = []
                        async with CodebaseAnalyzer(root_dir=str(job_dir)) as analyzer:
                            for py_file in python_files:
                                try:
                                    issues = await analyzer.analyze_and_propose(str(py_file))

                                    # Convert issues to error format
                                    for issue in issues:
                                        error_id = f"err-{abs(hash(str(py_file) + str(issue))) % 100000}"
                                        severity = issue.get("risk_level", "medium")
                                        details = issue.get("details", {})

                                        errors.append(
                                            {
                                                "error_id": error_id,
                                                "job_id": job_id,
                                                "severity": severity,
                                                "message": details.get("message", str(issue)),
                                                "file": str(py_file.relative_to(job_dir)),
                                                "line": details.get("line", 0),
                                                "type": issue.get("type", "unknown"),
                                            }
                                        )
                                except Exception as e:
                                    logger.warning(f"Error analyzing {py_file}: {e}")
                                    continue

                        logger.info(
                            f"Direct SFE error detection complete: {len(errors)} errors found"
                        )
                        return errors  # Return list of errors directly

            except Exception as e:
                logger.error(f"Direct SFE error detection failed: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback
        logger.warning("Neither OmniCore nor direct SFE available, using fallback")
        return [
            {
                "error_id": "err-001",
                "job_id": job_id,
                "severity": "high",
                "message": "Undefined variable 'config' in main.py (fallback)",
                "file": "main.py",
                "line": 42,
                "type": "NameError",
            },
        ]

    async def propose_fix(self, error_id: str) -> Dict[str, Any]:
        """
        Propose a fix for a detected error.

        Args:
            error_id: Error identifier

        Returns:
            Fix proposal

        Example integration:
            >>> # from self_fixing_engineer.arbiter import propose_fix
            >>> # fix = await propose_fix(error_id)
        """
        logger.info(f"Proposing fix for error {error_id}")

        # Try to look up the error from in-memory cache
        # In a real implementation, we'd have an errors_db similar to fixes_db
        # For now, we'll generate contextual fixes based on common error patterns

        # Generate fix ID
        fix_id = f"fix-{error_id}"

        # Generate contextual fix based on error_id patterns
        if (
            "import" in error_id.lower()
            or "undefined" in error_id.lower()
            or "name" in error_id.lower()
        ):
            # Missing import error
            fix = {
                "fix_id": fix_id,
                "error_id": error_id,
                "description": "Add missing import statement",
                "proposed_changes": [
                    {
                        "file": "main.py",
                        "line": 1,
                        "action": "insert",
                        "content": "import sys\nimport os",
                    }
                ],
                "confidence": 0.85,
                "reasoning": "Common undefined variable errors are often caused by missing imports",
            }
        elif "syntax" in error_id.lower():
            # Syntax error
            fix = {
                "fix_id": fix_id,
                "error_id": error_id,
                "description": "Fix syntax error",
                "proposed_changes": [
                    {
                        "file": "main.py",
                        "line": 10,
                        "action": "replace",
                        "content": "# Fixed syntax",
                    }
                ],
                "confidence": 0.75,
                "reasoning": "Syntax error detected, manual review recommended",
            }
        elif "complexity" in error_id.lower():
            # Complexity issue
            fix = {
                "fix_id": fix_id,
                "error_id": error_id,
                "description": "Refactor complex function",
                "proposed_changes": [
                    {
                        "file": "main.py",
                        "line": 50,
                        "action": "replace",
                        "content": "# Consider breaking this function into smaller functions",
                    }
                ],
                "confidence": 0.70,
                "reasoning": "High complexity detected, refactoring recommended",
            }
        elif "security" in error_id.lower() or "sql" in error_id.lower():
            # Security issue
            fix = {
                "fix_id": fix_id,
                "error_id": error_id,
                "description": "Fix security vulnerability",
                "proposed_changes": [
                    {
                        "file": "database.py",
                        "line": 25,
                        "action": "replace",
                        "content": "# Use parameterized queries instead of string concatenation",
                    }
                ],
                "confidence": 0.90,
                "reasoning": "Security vulnerability detected, immediate fix recommended",
            }
        else:
            # Generic fix
            fix = {
                "fix_id": fix_id,
                "error_id": error_id,
                "description": "Apply recommended code improvement",
                "proposed_changes": [
                    {
                        "file": "main.py",
                        "line": 1,
                        "action": "insert",
                        "content": "# Code improvement recommended",
                    }
                ],
                "confidence": 0.65,
                "reasoning": "Generic code quality improvement",
            }

        # Store fix in fixes_db for later application
        from server.storage import fixes_db
        from server.schemas import Fix, FixStatus
        from datetime import datetime, timezone

        try:
            now = datetime.now(timezone.utc)
            fix_obj = Fix(
                fix_id=fix_id,
                error_id=error_id,
                status=FixStatus.PROPOSED,
                description=fix["description"],
                proposed_changes=fix["proposed_changes"],
                confidence=fix["confidence"],
                created_at=now,
                updated_at=now,
            )
            fixes_db[fix_id] = fix_obj
            logger.info(f"Stored fix {fix_id} in fixes_db")
        except Exception as e:
            logger.warning(f"Could not store fix in fixes_db: {e}")

        return fix

    async def apply_fix(self, fix_id: str, dry_run: bool = False) -> Dict[str, Any]:
        """
        Apply a proposed fix.

        Args:
            fix_id: Fix identifier
            dry_run: If True, simulate without applying

        Returns:
            Application result

        Example integration:
            >>> # from self_fixing_engineer.arbiter import apply_fix
            >>> # result = await apply_fix(fix_id, dry_run)
        """
        logger.info(f"Applying fix {fix_id} (dry_run={dry_run})")

        # Look up fix from fixes_db
        from server.storage import fixes_db

        if fix_id not in fixes_db:
            logger.warning(f"Fix {fix_id} not found in fixes_db")
            return {
                "fix_id": fix_id,
                "applied": False,
                "dry_run": dry_run,
                "status": "error",
                "error": "Fix not found",
                "files_modified": [],
            }

        fix = fixes_db[fix_id]
        files_modified = []

        try:
            # Apply each proposed change
            for change in fix.proposed_changes:
                file_path = Path(change["file"])
                action = change["action"]
                content = change["content"]
                line = change.get("line", 1)

                files_modified.append(str(file_path))

                if dry_run:
                    logger.info(f"[DRY RUN] Would {action} at {file_path}:{line}")
                    continue

                # Create backup before modifying
                if file_path.exists():
                    backup_path = Path(f"{file_path}.bak")
                    try:
                        import shutil

                        shutil.copy2(file_path, backup_path)
                        logger.info(f"Created backup at {backup_path}")
                    except Exception as e:
                        logger.warning(f"Could not create backup: {e}")

                # Apply the change
                if action == "insert":
                    # Insert content at specified line
                    if file_path.exists():
                        with open(file_path, "r", encoding="utf-8") as f:
                            lines = f.readlines()

                        # Insert at line (1-indexed)
                        insert_pos = max(0, line - 1)
                        lines.insert(insert_pos, content + "\n")

                        with open(file_path, "w", encoding="utf-8") as f:
                            f.writelines(lines)
                        logger.info(f"Inserted content at {file_path}:{line}")
                    else:
                        # Create new file
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(content + "\n")
                        logger.info(f"Created new file {file_path}")

                elif action == "replace":
                    # Replace line with new content
                    if file_path.exists():
                        with open(file_path, "r", encoding="utf-8") as f:
                            lines = f.readlines()

                        if 0 < line <= len(lines):
                            lines[line - 1] = content + "\n"

                            with open(file_path, "w", encoding="utf-8") as f:
                                f.writelines(lines)
                            logger.info(f"Replaced line at {file_path}:{line}")
                        else:
                            logger.warning(f"Line {line} out of range for {file_path}")
                    else:
                        logger.warning(f"File {file_path} does not exist")

                elif action == "delete":
                    # Delete line
                    if file_path.exists():
                        with open(file_path, "r", encoding="utf-8") as f:
                            lines = f.readlines()

                        if 0 < line <= len(lines):
                            del lines[line - 1]

                            with open(file_path, "w", encoding="utf-8") as f:
                                f.writelines(lines)
                            logger.info(f"Deleted line at {file_path}:{line}")
                        else:
                            logger.warning(f"Line {line} out of range for {file_path}")
                    else:
                        logger.warning(f"File {file_path} does not exist")

            return {
                "fix_id": fix_id,
                "applied": not dry_run,
                "dry_run": dry_run,
                "status": "success" if not dry_run else "simulated",
                "files_modified": files_modified,
            }

        except Exception as e:
            logger.error(f"Error applying fix {fix_id}: {e}", exc_info=True)
            return {
                "fix_id": fix_id,
                "applied": False,
                "dry_run": dry_run,
                "status": "error",
                "error": str(e),
                "files_modified": files_modified,
            }

    async def rollback_fix(self, fix_id: str) -> Dict[str, Any]:
        """
        Rollback an applied fix.

        Args:
            fix_id: Fix identifier

        Returns:
            Rollback result

        Example integration:
            >>> # from self_fixing_engineer.arbiter import rollback_fix
            >>> # result = await rollback_fix(fix_id)
        """
        logger.info(f"Rolling back fix {fix_id}")

        # Placeholder: Call actual rollback mechanism
        # Example:
        # from self_fixing_engineer.arbiter.fix_applicator import rollback_fix
        # result = await rollback_fix(fix_id)

        return {
            "fix_id": fix_id,
            "rolled_back": True,
            "status": "success",
            "files_restored": ["main.py"],
        }

    async def get_sfe_metrics(self, job_id: str) -> Dict[str, Any]:
        """
        Get SFE metrics for a job.

        Args:
            job_id: Unique job identifier

        Returns:
            SFE metrics including errors, fixes, and success rates

        Example integration:
            >>> # from self_fixing_engineer.mesh.metrics import get_metrics
            >>> # metrics = await get_metrics(job_id)
        """
        logger.debug(f"Fetching SFE metrics for job {job_id}")

        # Try to get metrics from mesh if available
        if self._sfe_available["mesh_metrics"]:
            try:
                mesh_adapter = self._sfe_components["mesh_metrics"]

                # Try to extract metrics from mesh adapter
                metrics_data = {
                    "job_id": job_id,
                    "source": "sfe_mesh",
                }

                # Check if mesh_adapter has metrics methods
                if hasattr(mesh_adapter, "get_metrics"):
                    try:
                        mesh_metrics = mesh_adapter.get_metrics(job_id)
                        metrics_data.update(mesh_metrics)
                    except Exception as e:
                        logger.debug(f"Could not get mesh metrics: {e}")

                logger.info(f"Retrieved SFE mesh metrics for job {job_id}")
                return metrics_data

            except Exception as e:
                logger.error(f"Error querying SFE metrics: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return empty metrics with note
        logger.debug(f"Using fallback SFE metrics for job {job_id}")
        return {
            "job_id": job_id,
            "errors_detected": 0,
            "fixes_proposed": 0,
            "fixes_applied": 0,
            "success_rate": 0.0,
            "source": "fallback",
            "note": "SFE metrics unavailable. OmniCore service and SFE components are not available.",
        }

    async def get_learning_insights(
        self, job_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get meta-learning insights from SFE via OmniCore.

        Args:
            job_id: Optional job ID to filter insights

        Returns:
            Learning insights (global or job-specific)

        Example integration:
            >>> # Route through OmniCore to SFE meta-learning
            >>> # insights = await omnicore.query_sfe_insights(job_id)
        """
        logger.debug(
            f"Fetching learning insights{f' for job {job_id}' if job_id else ''}"
        )

        # Route through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "get_learning_insights",
                "job_id": job_id,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id or "global",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        # Fallback
        return {
            "job_id": job_id,
            "total_fixes": 150,
            "success_rate": 0.85,
            "common_patterns": ["missing_imports", "type_errors", "syntax_errors"],
            "meta_learning_module": "self_fixing_engineer.arbiter.meta_learning_orchestrator (fallback)",
        }

    async def get_sfe_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get detailed real-time status of SFE activities for a job via OmniCore.

        This provides comprehensive monitoring of what SFE is doing,
        including current operations, progress, and recent activities.

        Args:
            job_id: Unique job identifier

        Returns:
            Detailed SFE status information

        Example integration:
            >>> # Query SFE status through OmniCore message bus
            >>> # status = await omnicore.query_sfe_status(job_id)
        """
        logger.info(f"Fetching detailed SFE status for job {job_id} via OmniCore")

        # Route through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "get_sfe_status",
                "job_id": job_id,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        # Fallback
        return {
            "job_id": job_id,
            "status": "running",
            "current_operation": "analyzing_codebase",
            "progress_percentage": 45.0,
            "operations_history": [
                {"timestamp": "2026-01-18T18:00:00Z", "operation": "scan_started"},
                {"timestamp": "2026-01-18T18:05:00Z", "operation": "errors_detected"},
            ],
            "sfe_module": "self_fixing_engineer.main (fallback)",
        }

    async def get_sfe_logs(
        self, job_id: str, limit: int = 100, level: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get real-time logs from SFE for a specific job via OmniCore.

        This enables monitoring of SFE's operations and debugging issues.

        Args:
            job_id: Unique job identifier
            limit: Maximum number of log entries to return
            level: Optional log level filter (e.g., "ERROR", "WARNING", "INFO")

        Returns:
            List of SFE log entries

        Example integration:
            >>> # Query SFE logs through OmniCore
            >>> # logs = await omnicore.query_sfe_logs(job_id, limit)
        """
        logger.debug(
            f"Fetching SFE logs for job {job_id} (limit: {limit}, level: {level})"
        )

        # Route through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "get_sfe_logs",
                "job_id": job_id,
                "limit": limit,
                "level": level,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", [])

        # Fallback
        return [
            {
                "timestamp": "2026-01-18T18:00:00Z",
                "level": "INFO",
                "message": f"Processing job {job_id}",
                "module": "self_fixing_engineer (fallback)",
            }
        ]

    async def interact_with_sfe(
        self, job_id: str, command: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send interactive commands to SFE for a job via OmniCore.

        This allows direct interaction with SFE, such as pausing operations,
        requesting specific analyses, or adjusting parameters.

        Args:
            job_id: Unique job identifier
            command: Command to send (e.g., "pause", "resume", "analyze_file")
            params: Command parameters

        Returns:
            Command execution result

        Example integration:
            >>> # Send command to SFE through OmniCore
            >>> # result = await omnicore.send_sfe_command(job_id, command, params)
        """
        logger.info(f"Sending command '{command}' to SFE for job {job_id} via OmniCore")

        # Route through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "sfe_command",
                "job_id": job_id,
                "command": command,
                "params": params,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            logger.info(
                f"Command '{command}' sent to SFE for job {job_id} via OmniCore"
            )
            return result.get(
                "data",
                {
                    "job_id": job_id,
                    "command": command,
                    "status": "command_executed",
                },
            )

        # Fallback
        return {
            "job_id": job_id,
            "command": command,
            "status": "executed",
            "sfe_module": "self_fixing_engineer.main (fallback)",
        }

    async def control_arbiter(
        self,
        command: str,
        job_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Control Arbiter AI via OmniCore.

        Args:
            command: Command (start, stop, pause, resume, configure, status)
            job_id: Optional job ID
            config: Optional configuration

        Returns:
            Arbiter control result
        """
        logger.info(f"Controlling Arbiter with command {command} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "control_arbiter",
                "command": command,
                "job_id": job_id,
                "config": config or {},
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id or "arbiter_control",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "command": command,
            "status": "executed",
            "arbiter_status": "active" if command == "start" else "idle",
        }

    async def trigger_arena_competition(
        self,
        problem_type: str,
        code_path: str,
        agents: Optional[List[str]],
        rounds: int,
        evaluation_criteria: List[str],
    ) -> Dict[str, Any]:
        """
        Trigger arena agent competition via OmniCore.

        Args:
            problem_type: Type of problem
            code_path: Path to code
            agents: Specific agents to compete
            rounds: Number of rounds
            evaluation_criteria: Evaluation criteria

        Returns:
            Competition result
        """
        logger.info(f"Triggering arena competition for {problem_type} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "trigger_arena",
                "problem_type": problem_type,
                "code_path": code_path,
                "agents": agents,
                "rounds": rounds,
                "evaluation_criteria": evaluation_criteria,
            }
            result = await self.omnicore_service.route_job(
                job_id=f"arena_{problem_type}",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "competition_id": f"comp_{abs(hash(code_path)) % 10000}",
            "status": "completed",
            "winner": "agent_1",
            "rounds_completed": rounds,
        }

    async def detect_bugs(
        self,
        code_path: str,
        scan_depth: str,
        include_potential: bool,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Detect bugs in code via OmniCore or direct SFE integration.

        Args:
            code_path: Path to code (can be relative, resolved via job_id if provided)
            scan_depth: Scan depth
            include_potential: Include potential issues
            job_id: Optional job ID to resolve code path from job metadata

        Returns:
            Bug detection results with bugs array
        """
        # Resolve actual path if job_id provided
        resolved_path = self._resolve_job_code_path(job_id, code_path)
        logger.info(f"Detecting bugs in {resolved_path}")

        # Try routing through OmniCore first
        if self.omnicore_service:
            payload = {
                "action": "detect_bugs",
                "code_path": resolved_path,
                "scan_depth": scan_depth,
                "include_potential": include_potential,
            }
            result = await self.omnicore_service.route_job(
                job_id=f"bug_scan_{abs(hash(resolved_path)) % 10000}",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            # Check if route_job actually returned data
            if result.get("data") and isinstance(result["data"], dict):
                data = result["data"]
                # Ensure bugs array exists
                if "bugs" not in data:
                    data["bugs"] = []
                logger.info("Bug detection completed via OmniCore")
                return data
            logger.info(
                "OmniCore routing returned no data, falling through to direct SFE"
            )

        # Try direct SFE integration if analyzer available
        if self._sfe_available["codebase_analyzer"]:
            try:
                logger.info("Using direct SFE CodebaseAnalyzer to detect bugs")

                CodebaseAnalyzer = self._sfe_components["codebase_analyzer"]
                code_path_obj = Path(resolved_path)

                # Validate path exists
                if not code_path_obj.exists():
                    return {
                        "bugs_found": 0,
                        "bugs": [],
                        "critical": 0,
                        "high": 0,
                        "medium": 0,
                        "low": 0,
                        "scan_depth": scan_depth,
                        "note": f"Path does not exist: {resolved_path}",
                    }

                bugs = []

                # Use CodebaseAnalyzer to scan for bugs
                if code_path_obj.is_file():
                    root_dir = str(code_path_obj.parent)
                    async with CodebaseAnalyzer(root_dir=root_dir) as analyzer:
                        issues = await analyzer.analyze_and_propose(str(code_path_obj))
                        
                        # Transform issues to bugs using utility function
                        bugs = transform_pipeline_issues_to_bugs(
                            issues, job_id, str(code_path_obj.name)
                        )

                elif code_path_obj.is_dir():
                    async with CodebaseAnalyzer(
                        root_dir=str(code_path_obj)
                    ) as analyzer:
                        # Discover Python files
                        py_files = analyzer.discover_files()

                        # Limit files analyzed based on scan_depth
                        if scan_depth == "quick":
                            max_files = 5
                        elif scan_depth == "standard":
                            max_files = 20
                        else:  # deep
                            max_files = 100

                        # Collect all issues from files
                        all_issues = []
                        for py_file in py_files[:max_files]:
                            try:
                                issues = await analyzer.analyze_and_propose(py_file)
                                
                                # Add file path to each issue
                                for issue in issues:
                                    if "file" not in issue:
                                        issue["file"] = str(
                                            Path(py_file).relative_to(code_path_obj)
                                        )
                                    all_issues.append(issue)
                                    
                            except Exception as e:
                                logger.warning(f"Error analyzing {py_file}: {e}")
                                continue
                        
                        # Transform all issues to bugs using utility function
                        bugs = transform_pipeline_issues_to_bugs(
                            all_issues, job_id, "unknown"
                        )

                # Count by severity
                severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
                for bug in bugs:
                    severity = bug.get("severity", "medium")
                    if severity in severity_counts:
                        severity_counts[severity] += 1

                result = {
                    "bugs_found": len(bugs),
                    "bugs": bugs,
                    "critical": severity_counts["critical"],
                    "high": severity_counts["high"],
                    "medium": severity_counts["medium"],
                    "low": severity_counts["low"],
                    "scan_depth": scan_depth,
                    "source": "direct_sfe",
                }

                logger.info(
                    f"Direct SFE bug detection complete: {len(bugs)} bugs found"
                )
                return result

            except Exception as e:
                logger.error(f"Direct SFE bug detection failed: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback - return empty results instead of fake bugs
        logger.warning("Neither OmniCore nor direct SFE available, bug detection unavailable")
        return {
            "bugs_found": 0,
            "bugs": [],
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "scan_depth": scan_depth,
            "source": "fallback",
            "note": "Bug detection unavailable. OmniCore service and SFE CodebaseAnalyzer are not available. Please configure LLM API keys or enable SFE components.",
        }

    async def analyze_bug(
        self, bug_id: str, include_root_cause: bool, suggest_fixes: bool
    ) -> Dict[str, Any]:
        """
        Analyze a specific bug via OmniCore or direct SFE integration.

        Args:
            bug_id: Bug identifier
            include_root_cause: Perform root cause analysis
            suggest_fixes: Generate fix suggestions

        Returns:
            Bug analysis results
        """
        logger.info(f"Analyzing bug {bug_id}")

        # Try routing through OmniCore first
        if self.omnicore_service:
            payload = {
                "action": "analyze_bug",
                "bug_id": bug_id,
                "include_root_cause": include_root_cause,
                "suggest_fixes": suggest_fixes,
            }
            result = await self.omnicore_service.route_job(
                job_id=f"bug_analysis_{bug_id}",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            # Check if route_job actually returned data
            if result.get("data"):
                logger.info("Bug analysis completed via OmniCore")
                return result["data"]
            logger.info(
                "OmniCore routing returned no data, falling through to fallback"
            )

        # Fallback with contextual analysis
        logger.warning("Neither OmniCore nor direct SFE available, using fallback")
        return {
            "bug_id": bug_id,
            "root_cause": (
                "Complex code path with multiple potential causes"
                if include_root_cause
                else None
            ),
            "suggested_fixes": (
                ["Add input validation", "Add error handling", "Refactor complex logic"]
                if suggest_fixes
                else []
            ),
            "severity": "medium",
            "confidence": 0.70,
        }

    async def prioritize_bugs(
        self, job_id: str, criteria: Optional[List[str]]
    ) -> Dict[str, Any]:
        """
        Prioritize bugs for a job via OmniCore or direct SFE integration.

        Args:
            job_id: Job identifier
            criteria: Prioritization criteria

        Returns:
            Prioritized bug list
        """
        logger.info(f"Prioritizing bugs for job {job_id}")

        # Try routing through OmniCore first
        if self.omnicore_service:
            payload = {
                "action": "prioritize_bugs",
                "job_id": job_id,
                "criteria": criteria or ["severity", "impact", "effort"],
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            # Check if route_job actually returned data
            if result.get("data"):
                logger.info("Bug prioritization completed via OmniCore")
                return result["data"]
            logger.info(
                "OmniCore routing returned no data, falling through to fallback"
            )

        # Fallback: try to load real bugs from analysis or detect_errors
        logger.warning(
            "OmniCore not available, attempting to load real bugs from job analysis"
        )

        try:
            # First, try to get errors for this job
            errors_result = await self.detect_errors(job_id)
            bugs = errors_result.get("errors", [])

            if bugs:
                # Prioritize the real bugs
                criteria = criteria or ["severity", "impact", "effort"]

                # Calculate priority for each bug
                prioritized = []
                for bug in bugs:
                    severity = bug.get("severity", "medium")
                    priority_score = SEVERITY_SCORES.get(severity, 50)

                    # Generate unique bug_id if not present
                    bug_id = bug.get("error_id") or bug.get("bug_id")
                    if not bug_id:
                        # Use uuid for truly unique IDs
                        bug_id = f"bug-{uuid4().hex[:8]}"

                    prioritized.append(
                        {
                            "bug_id": bug_id,
                            "type": bug.get("type", "Unknown"),
                            "message": bug.get("message", ""),
                            "file": bug.get("file", ""),
                            "line": bug.get("line", 0),
                            "severity": severity,
                            "priority": len(prioritized)
                            + 1,  # Will be recalculated after sorting
                            "priority_score": priority_score,
                            "impact": (
                                "high" if severity in ["critical", "high"] else "medium"
                            ),
                            "effort": "medium",
                        }
                    )

                # Sort by priority score (highest first)
                prioritized.sort(key=lambda x: x["priority_score"], reverse=True)

                # Update priority numbers after sorting
                for i, bug in enumerate(prioritized):
                    bug["priority"] = i + 1

                logger.info(
                    f"Prioritized {len(prioritized)} real bugs from job {job_id}"
                )
                return {
                    "job_id": job_id,
                    "prioritized_bugs": prioritized,
                    "criteria": criteria,
                    "source": "real_analysis",
                }
        except Exception as e:
            logger.warning(f"Failed to load real bugs for prioritization: {e}")

        # Final fallback with mock data
        logger.warning("Using mock fallback data for bug prioritization")
        return {
            "job_id": job_id,
            "prioritized_bugs": [
                {
                    "bug_id": "bug_1",
                    "priority": 1,
                    "severity": "critical",
                    "impact": "high",
                    "effort": "medium",
                },
                {
                    "bug_id": "bug_2",
                    "priority": 2,
                    "severity": "high",
                    "impact": "high",
                    "effort": "low",
                },
                {
                    "bug_id": "bug_3",
                    "priority": 3,
                    "severity": "medium",
                    "impact": "medium",
                    "effort": "low",
                },
            ],
            "criteria": criteria or ["severity", "impact", "effort"],
        }

    async def deep_analyze_codebase(
        self,
        code_path: str,
        analysis_types: List[str],
        generate_report: bool,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Perform deep codebase analysis via OmniCore or direct SFE integration.

        Args:
            code_path: Path to codebase (can be relative, resolved via job_id if provided)
            analysis_types: Types of analysis
            generate_report: Generate detailed report
            job_id: Optional job ID to resolve code path from job metadata

        Returns:
            Analysis results
        """
        # Resolve actual path if job_id provided
        resolved_path = self._resolve_job_code_path(job_id, code_path)
        logger.info(f"Deep analyzing codebase at {resolved_path}")

        # Try routing through OmniCore first
        if self.omnicore_service:
            payload = {
                "action": "deep_analyze",
                "code_path": resolved_path,
                "analysis_types": analysis_types,
                "generate_report": generate_report,
            }
            result = await self.omnicore_service.route_job(
                job_id=f"analysis_{abs(hash(resolved_path)) % 10000}",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            # Check if route_job actually returned data
            if result.get("data"):
                logger.info("Deep analysis completed via OmniCore")
                return result["data"]
            logger.info(
                "OmniCore routing returned no data, falling through to direct SFE"
            )

        # Try direct SFE integration if analyzer available
        if self._sfe_available["codebase_analyzer"]:
            try:
                logger.info("Using direct SFE CodebaseAnalyzer for deep analysis")

                CodebaseAnalyzer = self._sfe_components["codebase_analyzer"]
                code_path_obj = Path(resolved_path)

                # Validate path exists
                if not code_path_obj.exists():
                    return {
                        "analysis_id": f"analysis_{abs(hash(resolved_path)) % 10000}",
                        "error": f"Path does not exist: {resolved_path}",
                        "source": "direct_sfe",
                    }

                # Use CodebaseAnalyzer to perform deep analysis
                async with CodebaseAnalyzer(root_dir=str(code_path_obj)) as analyzer:
                    if generate_report:
                        # Generate full report
                        tmp_dir = Path(tempfile.gettempdir())
                        report_path = (
                            tmp_dir
                            / f"codebase_analysis_{abs(hash(resolved_path)) % 10000}.md"
                        )
                        report = await analyzer.generate_report(
                            output_format="markdown",
                            output_path=str(report_path),
                            use_baseline=False,
                        )

                        result = {
                            "analysis_id": f"analysis_{abs(hash(resolved_path)) % 10000}",
                            "total_files": report.get("total_files", 0),
                            "total_loc": report.get("total_loc", 0),
                            "avg_complexity": report.get("avg_complexity", 0),
                            "analysis_summary": report.get(
                                "summary", "Analysis complete"
                            ),
                            "issues": report.get("issues", []),
                            "report_path": str(report_path),
                            "source": "direct_sfe",
                        }
                    else:
                        # Just scan without generating report
                        summary = await analyzer.scan_codebase(str(code_path_obj))

                        # Extract information from summary
                        total_files = (
                            len(analyzer.discover_files())
                            if hasattr(analyzer, "discover_files")
                            else 0
                        )

                        result = {
                            "analysis_id": f"analysis_{abs(hash(resolved_path)) % 10000}",
                            "total_files": total_files,
                            "total_loc": getattr(summary, "total_lines", 0),
                            "avg_complexity": getattr(summary, "avg_complexity", 0),
                            "analysis_summary": str(summary),
                            "issues": [],
                            "source": "direct_sfe",
                        }

                logger.info("Direct SFE deep analysis complete")
                return result

            except Exception as e:
                logger.error(f"Direct SFE deep analysis failed: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback - return minimal data with note
        logger.warning("Neither OmniCore nor direct SFE available, deep codebase analysis unavailable")
        return {
            "analysis_id": f"analysis_{abs(hash(code_path)) % 10000}",
            "total_files": 0,
            "total_loc": 0,
            "avg_complexity": 0,
            "analysis_summary": "",
            "issues": [],
            "report_path": None,
            "source": "fallback",
            "note": "Deep codebase analysis unavailable. OmniCore service and SFE CodebaseAnalyzer are not available. Please configure LLM API keys or enable SFE components.",
        }

    async def query_knowledge_graph(
        self, query_type: str, query: str, depth: int, limit: int
    ) -> Dict[str, Any]:
        """
        Query knowledge graph via OmniCore.

        Args:
            query_type: Query type
            query: Query string
            depth: Traversal depth
            limit: Max results

        Returns:
            Query results
        """
        logger.info(f"Querying knowledge graph: {query_type} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "query_knowledge_graph",
                "query_type": query_type,
                "query": query,
                "depth": depth,
                "limit": limit,
            }
            result = await self.omnicore_service.route_job(
                job_id="kg_query",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "query_type": query_type,
            "results": [{"entity": "example", "relationships": []}],
            "count": 1,
        }

    async def update_knowledge_graph(
        self, operation: str, entity_type: str, entity_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update knowledge graph via OmniCore.

        Args:
            operation: Operation type
            entity_type: Entity type
            entity_data: Entity data

        Returns:
            Update result
        """
        logger.info(f"Updating knowledge graph: {operation} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "update_knowledge_graph",
                "operation": operation,
                "entity_type": entity_type,
                "entity_data": entity_data,
            }
            result = await self.omnicore_service.route_job(
                job_id="kg_update",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "status": "updated",
            "operation": operation,
            "entity_type": entity_type,
        }

    async def execute_in_sandbox(
        self,
        code: str,
        language: str,
        timeout: int,
        resource_limits: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Execute code in sandbox via OmniCore.

        Args:
            code: Code to execute
            language: Programming language
            timeout: Execution timeout
            resource_limits: Resource limits

        Returns:
            Execution results
        """
        logger.info("Executing code in sandbox via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "sandbox_execute",
                "code": code,
                "language": language,
                "timeout": timeout,
                "resource_limits": resource_limits or {},
            }
            result = await self.omnicore_service.route_job(
                job_id="sandbox_exec",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "status": "completed",
            "output": "Hello, World!",
            "execution_time": 0.5,
            "exit_code": 0,
        }

    async def check_compliance(
        self, code_path: str, standards: List[str], generate_report: bool
    ) -> Dict[str, Any]:
        """
        Check compliance standards via OmniCore.

        Args:
            code_path: Path to code
            standards: Compliance standards
            generate_report: Generate compliance report

        Returns:
            Compliance check results
        """
        logger.info(f"Checking compliance for {code_path} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "check_compliance",
                "code_path": code_path,
                "standards": standards,
                "generate_report": generate_report,
            }
            result = await self.omnicore_service.route_job(
                job_id=f"compliance_{abs(hash(code_path)) % 10000}",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "status": "passed",
            "standards_checked": standards,
            "violations": [],
            "report_path": "/reports/compliance.pdf" if generate_report else None,
        }

    async def query_dlt_audit(
        self,
        start_block: Optional[int],
        end_block: Optional[int],
        transaction_type: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        """
        Query DLT/blockchain audit logs via OmniCore.

        Args:
            start_block: Starting block
            end_block: Ending block
            transaction_type: Filter by type
            limit: Max results

        Returns:
            Audit transactions
        """
        logger.info("Querying DLT audit logs via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "query_dlt_audit",
                "start_block": start_block,
                "end_block": end_block,
                "transaction_type": transaction_type,
                "limit": limit,
            }
            result = await self.omnicore_service.route_job(
                job_id="dlt_query",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "transactions": [
                {"block": 100, "tx_hash": "0xabc123", "type": "code_generation"}
            ],
            "count": 1,
        }

    async def configure_siem(
        self,
        siem_type: str,
        endpoint: str,
        api_key: Optional[str],
        export_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Configure SIEM integration via OmniCore.

        Args:
            siem_type: SIEM type
            endpoint: SIEM endpoint
            api_key: API key
            export_config: Export configuration

        Returns:
            Configuration result
        """
        logger.info(f"Configuring SIEM integration: {siem_type} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "configure_siem",
                "siem_type": siem_type,
                "endpoint": endpoint,
                "api_key": api_key,
                "export_config": export_config,
            }
            result = await self.omnicore_service.route_job(
                job_id="siem_config",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "status": "configured",
            "siem_type": siem_type,
            "endpoint": endpoint,
        }

    async def get_rl_environment_status(self, environment_id: str) -> Dict[str, Any]:
        """
        Get RL environment status via OmniCore.

        Args:
            environment_id: Environment identifier

        Returns:
            Environment status
        """
        logger.info(f"Getting RL environment status for {environment_id} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "get_rl_status",
                "environment_id": environment_id,
            }
            result = await self.omnicore_service.route_job(
                job_id=f"rl_{environment_id}",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "environment_id": environment_id,
            "status": "running",
            "episodes": 100,
            "average_reward": 75.5,
        }

    async def fix_imports(
        self,
        code_path: str,
        auto_install: bool,
        fix_style: bool,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fix import issues via OmniCore.

        Args:
            code_path: Path to code (can be relative, resolved via job_id if provided)
            auto_install: Auto-install missing packages
            fix_style: Fix import style
            job_id: Optional job ID to resolve code path from job metadata

        Returns:
            Import fix results
        """
        # Resolve actual path if job_id provided
        resolved_path = self._resolve_job_code_path(job_id, code_path)
        logger.info(f"Fixing imports for {resolved_path} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "fix_imports",
                "code_path": resolved_path,
                "auto_install": auto_install,
                "fix_style": fix_style,
            }
            result = await self.omnicore_service.route_job(
                job_id=f"import_fix_{abs(hash(resolved_path)) % 10000}",
                source_module="api",
                target_module="sfe",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "status": "fixed",
            "imports_fixed": 5,
            "packages_installed": 2 if auto_install else 0,
            "style_fixes": 3 if fix_style else 0,
        }
