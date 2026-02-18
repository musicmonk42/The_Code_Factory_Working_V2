# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Validation Integration for Code Generation Pipeline.

This module integrates contract validation from scripts/validate_contract_compliance.py
into the generation pipeline, ensuring generated code meets specifications.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ValidationReport:
    """Structured validation report with errors, warnings, and status."""
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.checks_run: List[str] = []
        self.checks_passed: List[str] = []
        self.checks_failed: List[str] = []
    
    def add_error(self, check_name: str, message: str):
        """Add a validation error."""
        self.errors.append(f"{check_name}: {message}")
        self.checks_failed.append(check_name)
    
    def add_warning(self, check_name: str, message: str):
        """Add a validation warning."""
        self.warnings.append(f"{check_name}: {message}")
    
    def mark_passed(self, check_name: str):
        """Mark a check as passed."""
        self.checks_passed.append(check_name)
    
    def is_valid(self) -> bool:
        """Check if validation passed (no errors)."""
        return len(self.errors) == 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "valid": self.is_valid(),
            "errors": self.errors,
            "warnings": self.warnings,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "total_checks": len(self.checks_run),
            "passed_count": len(self.checks_passed),
            "failed_count": len(self.checks_failed),
        }
    
    def to_text(self) -> str:
        """Generate human-readable text report."""
        lines = []
        lines.append("=" * 60)
        lines.append("CONTRACT VALIDATION REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Status: {'✅ PASS' if self.is_valid() else '❌ FAIL'}")
        lines.append(f"Checks Run: {len(self.checks_run)}")
        lines.append(f"Passed: {len(self.checks_passed)}")
        lines.append(f"Failed: {len(self.checks_failed)}")
        lines.append("")
        
        if self.checks_passed:
            lines.append("Passed Checks:")
            for check in self.checks_passed:
                lines.append(f"  ✅ {check}")
            lines.append("")
        
        if self.checks_failed:
            lines.append("Failed Checks:")
            for check in self.checks_failed:
                lines.append(f"  ❌ {check}")
            lines.append("")
        
        if self.errors:
            lines.append("Errors:")
            for error in self.errors:
                lines.append(f"  • {error}")
            lines.append("")
        
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings:
                lines.append(f"  ⚠️  {warning}")
            lines.append("")
        
        lines.append("=" * 60)
        return "\n".join(lines)


def validate_generated_code(
    output_dir: Path,
    language: str = "python",
    spec_block: Optional[Dict[str, Any]] = None,
    readme_content: Optional[str] = None
) -> ValidationReport:
    """
    Validate generated code against contract requirements.
    
    This function runs the ContractValidator from scripts/validate_contract_compliance.py
    and returns a structured ValidationReport.
    
    Args:
        output_dir: Path to the generated output directory
        language: Programming language (default: python)
        spec_block: Optional SpecBlock dict for additional validation
        readme_content: Optional README content for spec-fidelity checks
        
    Returns:
        ValidationReport with results
        
    Example:
        report = validate_generated_code(
            Path("./generated/my_app"),
            language="python",
            spec_block={"project_type": "fastapi_service"}
        )
        
        if not report.is_valid():
            print(report.to_text())
            raise ValidationError("Generated code does not meet contract")
    """
    report = ValidationReport()
    
    # Import validator (late import to avoid circular dependencies)
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
        from validate_contract_compliance import ContractValidator
    except ImportError as e:
        logger.error(f"Failed to import ContractValidator: {e}")
        report.add_error("Import", f"Could not load validator: {e}")
        return report
    
    # Create validator instance
    try:
        validator = ContractValidator(output_dir, language=language)
    except Exception as e:
        logger.error(f"Failed to create ContractValidator: {e}")
        report.add_error("Initialization", f"Could not create validator: {e}")
        return report
    
    # Run validation checks
    logger.info(f"Running contract validation for {output_dir}")
    
    checks = [
        ("Output Directory Structure", validator.check_output_structure),
        ("Schema Validation", validator.check_schema_validation),
        ("README Completeness", validator.check_readme_completeness),
        ("Sphinx Documentation", validator.check_sphinx_docs),
        ("Reports Location and Content", validator.check_reports),
        ("No Bogus Fallback Tests", validator.check_no_fallback_tests),
    ]
    
    for check_name, check_func in checks:
        report.checks_run.append(check_name)
        try:
            check_func()
            report.mark_passed(check_name)
            logger.debug(f"✅ {check_name} passed")
        except AssertionError as e:
            report.add_error(check_name, str(e))
            logger.warning(f"❌ {check_name} failed: {e}")
        except Exception as e:
            report.add_warning(check_name, f"Unexpected error: {e}")
            logger.warning(f"⚠️  {check_name} warning: {e}")
    
    # Additional spec-based validation
    if spec_block:
        report.checks_run.append("Spec Block Compliance")
        try:
            validate_spec_compliance(output_dir, spec_block, report)
            report.mark_passed("Spec Block Compliance")
        except Exception as e:
            report.add_error("Spec Block Compliance", str(e))
    
    logger.info(
        f"Validation complete: {len(report.checks_passed)}/{len(report.checks_run)} passed"
    )
    
    return report


def validate_spec_compliance(
    output_dir: Path,
    spec_block: Dict[str, Any],
    report: ValidationReport
) -> None:
    """
    Validate that generated code matches spec block requirements.
    
    Args:
        output_dir: Path to generated code
        spec_block: SpecBlock dictionary
        report: ValidationReport to update
        
    Raises:
        AssertionError: If spec requirements not met
    """
    # Check output_dir matches
    if "output_dir" in spec_block:
        expected_dir = spec_block["output_dir"].split("/")[-1]  # Get last component
        actual_dir = output_dir.name
        if expected_dir != actual_dir:
            raise AssertionError(
                f"Output directory mismatch: expected '{expected_dir}', got '{actual_dir}'"
            )
    
    # Check package structure exists
    if "package_name" in spec_block:
        package_name = spec_block["package_name"]
        package_dir = output_dir / package_name
        if not package_dir.exists():
            # Could also be in app/ directory
            app_dir = output_dir / "app"
            if not app_dir.exists():
                raise AssertionError(
                    f"Package directory '{package_name}' or 'app' not found in output"
                )
    
    # Check HTTP endpoints exist (if specified)
    interfaces = spec_block.get("interfaces", {})
    if isinstance(interfaces, dict) and interfaces.get("http"):
        http_endpoints = interfaces["http"]
        # This is a basic check - more sophisticated checking would parse route files
        routes_file = output_dir / "app" / "routes.py"
        if routes_file.exists():
            routes_content = routes_file.read_text()
            missing_endpoints = []
            for endpoint in http_endpoints:
                method, path = endpoint.split(" ", 1)
                # Check if endpoint appears in routes
                if path not in routes_content:
                    missing_endpoints.append(endpoint)
            
            if missing_endpoints:
                report.add_warning(
                    "HTTP Endpoints",
                    f"Endpoints may be missing: {', '.join(missing_endpoints)}"
                )
    
    # Check dependencies are in requirements.txt
    if spec_block.get("dependencies"):
        requirements_file = output_dir / "requirements.txt"
        if requirements_file.exists():
            requirements_content = requirements_file.read_text().lower()
            missing_deps = []
            for dep in spec_block["dependencies"]:
                # Extract package name (before >=, <=, ==, etc.)
                package_name = dep.split(">=")[0].split("<=")[0].split("==")[0].split("[")[0].strip()
                if package_name.lower() not in requirements_content:
                    missing_deps.append(package_name)
            
            if missing_deps:
                raise AssertionError(
                    f"Missing dependencies in requirements.txt: {', '.join(missing_deps)}"
                )
        else:
            raise AssertionError("requirements.txt not found")


__all__ = [
    "ValidationReport",
    "validate_generated_code",
    "validate_spec_compliance",
]
