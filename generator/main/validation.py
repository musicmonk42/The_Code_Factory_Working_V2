# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Validation Integration for Code Generation Pipeline.

This module integrates contract validation from scripts/validate_contract_compliance.py
into the generation pipeline, ensuring generated code meets specifications.
"""

import ast
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# SQLAlchemy column-type → Python/annotation type mapping used by
# _check_type_consistency.  Defined at module level so it is only
# instantiated once rather than on every call.
#
# The map drives Step 3: ``_SA_TYPE_MAP.get(model_type.lower())`` returns
# the *expected* router annotation string.  A missing key causes the entity
# to be skipped (no check run), so every PK type that should be validated
# must have an entry — including identity mappings ("int" → "int") needed
# for the SQLAlchemy 2.0 ``Mapped[int]`` annotation style where the type
# extracted from the source is already a Python primitive name.
_SA_TYPE_MAP: Dict[str, str] = {
    # Legacy Column() positional-type-argument names
    "integer": "int",
    "biginteger": "int",
    "smallinteger": "int",
    "string": "str",
    "text": "str",
    "varchar": "str",
    "uuid": "uuid.UUID",
    # SQLAlchemy 2.0 Mapped[X] annotation types — identity mappings that
    # enable the consistency check for modern declarative-style models.
    "int": "int",
    "str": "str",
    "uuid.uuid": "uuid.UUID",  # lower-cased form of uuid.UUID
}


# ---------------------------------------------------------------------------
# Helper: import_completeness check (Fix 5)
# ---------------------------------------------------------------------------

def _check_import_completeness(output_dir: Path, pkg_name: str = "app") -> List[str]:
    """Scan Python files for intra-project imports and verify they resolve.

    Returns a list of plain error message strings (no check-name prefix).
    The caller is responsible for recording them under the appropriate check.
    """
    errors: List[str] = []
    py_files = list(output_dir.rglob("*.py"))
    if not py_files:
        return errors

    # Build set of known module paths (relative to output_dir) and a map of
    # module → exported symbols in a single pass.
    known_modules: Set[str] = set()
    module_symbols: Dict[str, Set[str]] = {}

    for f in py_files:
        try:
            rel = f.relative_to(output_dir)
        except ValueError:
            continue
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            mod_key_parts = parts[:-1]
        else:
            mod_key_parts = list(parts)
            mod_key_parts[-1] = mod_key_parts[-1][:-3]  # strip .py
        if not mod_key_parts:
            continue
        mod_key = ".".join(mod_key_parts)
        known_modules.add(mod_key)

        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue

        symbols: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols.add(target.id)
        module_symbols[mod_key] = symbols

    # Scan each file's import statements.
    for f in py_files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            mod = node.module
            # Only check intra-project imports.
            if not mod.startswith(pkg_name + ".") and mod != pkg_name:
                continue
            # Verify target module exists.
            if mod not in known_modules:
                parent = mod.rsplit(".", 1)[0] if "." in mod else mod
                if parent not in known_modules:
                    errors.append(
                        f"{f.name}: module '{mod}' not found in project"
                    )
                    continue
            # Verify each imported symbol exists in the module.
            for alias in node.names:
                if alias.name == "*":
                    continue
                syms = module_symbols.get(mod, set())
                if syms and alias.name not in syms:
                    errors.append(
                        f"{f.name}: symbol '{alias.name}' not defined in '{mod}'"
                    )
    return errors


# ---------------------------------------------------------------------------
# Helper: router_path_validation check (Fix 6)
# ---------------------------------------------------------------------------

def _check_router_paths(output_dir: Path) -> List[str]:
    """Check APIRouter files for common path mistakes.

    Returns a list of plain warning/error message strings (no check-name
    prefix).  The caller records them under the appropriate check.
    """
    issues: List[str] = []
    router_files = list(output_dir.rglob("routers/*.py"))
    if not router_files:
        return issues

    http_methods = ("get", "post", "put", "delete", "patch", "options", "head")

    for rfile in router_files:
        try:
            source = rfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Extract APIRouter prefix (first occurrence wins).
        prefix_match = re.search(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)["\']', source)
        prefix = prefix_match.group(1) if prefix_match else ""

        # Collect routes per HTTP method to correctly scope the shadowing check.
        # Shadowing only occurs within the same HTTP method (FastAPI matches
        # both path and method, so GET /{id} does not shadow POST /batch).
        routes_by_method: Dict[str, List[str]] = {}
        for method in http_methods:
            method_paths: List[str] = []
            for m in re.finditer(
                r"@router\." + method + r"\s*\(\s*[\"']([^\"']*)[\"']", source
            ):
                route_path = m.group(1)
                method_paths.append(route_path)
                # Detect routes that collapse to just the prefix root.
                if route_path in ("/", "") and prefix:
                    effective = (prefix.rstrip("/") + "/").rstrip("/") or "/"
                    issues.append(
                        f"{rfile.name}: {method.upper()} '{route_path}' with prefix "
                        f"'{prefix}' resolves to '{effective}' "
                        f"(likely missing resource path segment)"
                    )
            routes_by_method[method] = method_paths

        # Per-method shadowing check: flag static routes defined after a
        # parameterized route in the same HTTP method.
        for method, method_paths in routes_by_method.items():
            param_seen = False
            for rp in method_paths:
                has_param = bool(re.search(r"\{[^}]+\}", rp))
                if has_param:
                    param_seen = True
                elif param_seen:
                    issues.append(
                        f"{rfile.name}: {method.upper()} static route '{rp}' "
                        f"defined after a parameterized route — may be shadowed"
                    )

    return issues


# ---------------------------------------------------------------------------
# Helper: type_consistency check (Fix 7)
# ---------------------------------------------------------------------------

def _check_type_consistency(output_dir: Path) -> List[str]:
    """Cross-reference SQLAlchemy model PK types with router path-param types.

    Returns a list of plain error message strings (no check-name prefix).
    The caller records them under the appropriate check.
    """
    errors: List[str] = []

    # --- Step 1: collect model PK column types ---
    # entity_name (lowercase class name) → SQLAlchemy column type string
    model_pk_types: Dict[str, str] = {}

    for mf in output_dir.rglob("models/*.py"):
        try:
            source = mf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            entity = node.name.lower()
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                    continue
                call = stmt.value
                if isinstance(call.func, ast.Name):
                    func_name = call.func.id
                elif isinstance(call.func, ast.Attribute):
                    func_name = call.func.attr
                else:
                    continue
                if func_name.lower() != "column":
                    continue
                # Require primary_key=True keyword argument.
                is_pk = any(
                    kw.arg == "primary_key"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in call.keywords
                )
                if not is_pk or not call.args:
                    continue
                type_arg = call.args[0]
                if isinstance(type_arg, ast.Name):
                    col_type: str = type_arg.id
                elif isinstance(type_arg, ast.Attribute):
                    col_type = type_arg.attr
                else:
                    continue
                model_pk_types[entity] = col_type

            # SQLAlchemy 2.0 mapped_column() style:
            #   id: Mapped[int] = mapped_column(primary_key=True)
            for stmt in node.body:
                if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.value, ast.Call):
                    continue
                call = stmt.value
                if isinstance(call.func, ast.Name):
                    func_name = call.func.id
                elif isinstance(call.func, ast.Attribute):
                    func_name = call.func.attr
                else:
                    continue
                if func_name.lower() != "mapped_column":
                    continue
                is_pk = any(
                    kw.arg == "primary_key"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in call.keywords
                )
                if not is_pk:
                    continue
                # Extract type from the Mapped[X] annotation on the target
                ann = stmt.annotation
                if not isinstance(ann, ast.Subscript):
                    continue
                slice_node = ann.slice
                if isinstance(slice_node, ast.Name):
                    mc_type: str = slice_node.id
                elif (
                    isinstance(slice_node, ast.Attribute)
                    and isinstance(slice_node.value, ast.Name)
                ):
                    mc_type = f"{slice_node.value.id}.{slice_node.attr}"
                else:
                    continue
                # Don't overwrite a Column()-detected entry for the same entity
                if entity not in model_pk_types:
                    model_pk_types[entity] = mc_type

    if not model_pk_types:
        return errors

    # --- Step 2: collect router path-parameter type annotations ---
    # entity_name → annotation string (from e.g. `product_id: uuid.UUID`)
    router_param_types: Dict[str, str] = {}

    for rf in output_dir.rglob("routers/*.py"):
        try:
            source = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue
        # Infer entity name from file stem for bare `id` param lookup
        rf_entity = rf.stem.rstrip("s")  # e.g. "patients.py" → "patient"
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for arg in node.args.args:
                ann = arg.annotation
                if not ann:
                    continue
                if arg.arg.endswith("_id"):
                    entity = arg.arg[: -len("_id")]
                elif arg.arg == "id":
                    # Infer entity from the router file name
                    entity = rf_entity
                else:
                    continue
                if isinstance(ann, ast.Name):
                    ann_str: str = ann.id
                elif isinstance(ann, ast.Attribute) and isinstance(ann.value, ast.Name):
                    ann_str = f"{ann.value.id}.{ann.attr}"
                elif isinstance(ann, ast.Attribute):
                    ann_str = ann.attr
                else:
                    continue
                router_param_types[entity] = ann_str

    # --- Step 3: cross-reference ---
    for entity, model_type in model_pk_types.items():
        router_type = router_param_types.get(entity)
        if router_type is None:
            continue
        expected_router = _SA_TYPE_MAP.get(model_type.lower())
        if expected_router is None:
            continue
        rt_lower = router_type.lower()
        et_lower = expected_router.lower()
        if et_lower == "uuid.uuid" and "uuid" not in rt_lower:
            errors.append(
                f"entity '{entity}': model PK is '{model_type}' (UUID) "
                f"but router uses '{router_type}'"
            )
        elif et_lower == "int" and rt_lower not in ("int", "integer"):
            errors.append(
                f"entity '{entity}': model PK is '{model_type}' (int) "
                f"but router uses '{router_type}'"
            )
        elif et_lower == "str" and rt_lower not in ("str", "string"):
            errors.append(
                f"entity '{entity}': model PK is '{model_type}' (str) "
                f"but router uses '{router_type}'"
            )

    return errors


class ValidationReport:
    """Structured validation report with errors, warnings, and status."""
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.checks_run: List[str] = []
        self.checks_passed: List[str] = []
        self.checks_failed: List[str] = []
        self.validation_skipped: bool = False
    
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
            "validation_skipped": self.validation_skipped,
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
        report.validation_skipped = True
        return report
    
    # Create validator instance
    try:
        validator = ContractValidator(output_dir, language=language)
    except Exception as e:
        logger.error(f"Failed to create ContractValidator: {e}")
        report.add_error("Initialization", f"Could not create validator: {e}")
        report.validation_skipped = True
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

    # Fix 5: Import completeness check — verify intra-project imports resolve.
    _pkg_name = (spec_block or {}).get("package_name") or "app"
    report.checks_run.append("import_completeness")
    try:
        _import_errors = _check_import_completeness(output_dir, pkg_name=_pkg_name)
        if _import_errors:
            for _ie in _import_errors:
                report.add_error("import_completeness", _ie)
            logger.warning(f"❌ import_completeness: {len(_import_errors)} issue(s)")
        else:
            report.mark_passed("import_completeness")
            logger.debug("✅ import_completeness passed")
    except Exception as _e:
        report.add_warning("import_completeness", f"Unexpected error: {_e}")
        logger.warning(f"⚠️  import_completeness check error: {_e}")

    # Fix 6: Router path validation.
    report.checks_run.append("router_path_validation")
    try:
        _router_issues = _check_router_paths(output_dir)
        if _router_issues:
            for _ri in _router_issues:
                report.add_warning("router_path_validation", _ri)
            logger.warning(f"⚠️  router_path_validation: {len(_router_issues)} issue(s)")
        else:
            report.mark_passed("router_path_validation")
            logger.debug("✅ router_path_validation passed")
    except Exception as _e:
        report.add_warning("router_path_validation", f"Unexpected error: {_e}")
        logger.warning(f"⚠️  router_path_validation check error: {_e}")

    # Fix 7: Type consistency check.
    report.checks_run.append("type_consistency")
    try:
        _type_errors = _check_type_consistency(output_dir)
        if _type_errors:
            for _te in _type_errors:
                report.add_error("type_consistency", _te)
            logger.warning(f"❌ type_consistency: {len(_type_errors)} mismatch(es)")
        else:
            report.mark_passed("type_consistency")
            logger.debug("✅ type_consistency passed")
    except Exception as _e:
        report.add_warning("type_consistency", f"Unexpected error: {_e}")
        logger.warning(f"⚠️  type_consistency check error: {_e}")

    # Fix 8: Cold-start import test — hard-fail for real app errors; soft-fail
    # (warning) when deps are simply not installed (ModuleNotFoundError).
    report.checks_run.append("Cold-start Import Test")
    try:
        _import_result = subprocess.run(
            [sys.executable, "-c", "import app.main"],
            cwd=str(output_dir),
            timeout=30,
            capture_output=True,
            text=True,
        )
        if _import_result.returncode == 0:
            report.mark_passed("Cold-start Import Test")
            logger.debug("✅ Cold-start import test passed")
        else:
            _stderr_snippet = _import_result.stderr[:500] if _import_result.stderr else "(no stderr)"
            # ModuleNotFoundError means third-party deps are not installed in this
            # environment — this is expected in CI and is a soft failure.
            # Any other error (NameError, SyntaxError, ImportError for project-local
            # symbols, etc.) means the app cannot start and is a hard failure.
            if "ModuleNotFoundError" in _stderr_snippet or "No module named" in _stderr_snippet:
                report.add_warning(
                    "Cold-start Import Test",
                    f"import app.main exited with code {_import_result.returncode}: {_stderr_snippet}",
                )
                logger.warning(
                    f"⚠️  Cold-start import test failed (exit {_import_result.returncode}): {_stderr_snippet}"
                )
            else:
                report.add_error(
                    "Cold-start Import Test",
                    f"import app.main exited with code {_import_result.returncode}: {_stderr_snippet}",
                )
                logger.error(
                    f"❌ Cold-start import test hard-failed (exit {_import_result.returncode}): {_stderr_snippet}"
                )
    except subprocess.TimeoutExpired:
        report.add_warning("Cold-start Import Test", "import app.main timed out after 30s")
        logger.warning("⚠️  Cold-start import test timed out")
    except Exception as _imp_err:
        report.add_warning("Cold-start Import Test", f"Could not run import test: {_imp_err}")
        logger.warning(f"⚠️  Cold-start import test error: {_imp_err}")
    
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
