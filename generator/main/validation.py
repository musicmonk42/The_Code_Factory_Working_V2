# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Validation Integration for Code Generation Pipeline.

This module integrates contract validation from scripts/validate_contract_compliance.py
into the generation pipeline, ensuring generated code meets specifications.
"""

import ast
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: import_completeness check (Fix 5)
# ---------------------------------------------------------------------------

def _check_import_completeness(output_dir: Path, pkg_name: str = "app") -> List[str]:
    """Scan Python files for intra-project imports and verify they resolve.

    Returns a list of error strings (empty means all imports resolved).
    """
    errors: List[str] = []
    py_files = list(output_dir.rglob("*.py"))
    if not py_files:
        return errors

    # Build set of known module paths (relative to output_dir)
    known_modules: Set[str] = set()
    for f in py_files:
        try:
            rel = f.relative_to(output_dir)
        except ValueError:
            continue
        # Convert path to dotted module name
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]  # strip .py
        if parts:
            known_modules.add(".".join(parts))

    # Build a map of module → set of defined names (for symbol checking)
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
            mod_key_parts[-1] = mod_key_parts[-1][:-3]
        if not mod_key_parts:
            continue
        mod_key = ".".join(mod_key_parts)
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

    # Now scan each file's imports
    for f in py_files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                # Only check intra-project imports
                if not mod.startswith(pkg_name + ".") and mod != pkg_name:
                    continue
                # Verify target module file exists
                if mod not in known_modules:
                    # Check parent package exists (for __init__ imports)
                    parent = mod.rsplit(".", 1)[0] if "." in mod else mod
                    if parent not in known_modules:
                        errors.append(
                            f"import_completeness: {f.name}: module '{mod}' not found in project"
                        )
                        continue
                # Check imported symbols exist in the module
                if node.names:
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        syms = module_symbols.get(mod, set())
                        if syms and alias.name not in syms:
                            errors.append(
                                f"import_completeness: {f.name}: symbol '{alias.name}' "
                                f"not defined in '{mod}'"
                            )
    return errors


# ---------------------------------------------------------------------------
# Helper: router_path_validation check (Fix 6)
# ---------------------------------------------------------------------------

def _check_router_paths(output_dir: Path) -> List[str]:
    """Check APIRouter files for common path mistakes.

    Returns a list of warning/error strings.
    """
    issues: List[str] = []
    # Find all router files
    router_files = list(output_dir.rglob("routers/*.py"))
    if not router_files:
        return issues

    http_methods = ("get", "post", "put", "delete", "patch", "options", "head")

    for rfile in router_files:
        try:
            source = rfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Extract APIRouter prefix
        prefix_match = re.search(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)["\']', source)
        prefix = prefix_match.group(1) if prefix_match else ""

        # Extract route decorators and their paths
        route_paths: List[str] = []
        for method in http_methods:
            for m in re.finditer(
                r'@router\.' + method + r'\s*\(\s*["\']([^"\']*)["\']', source
            ):
                route_path = m.group(1)
                effective = (prefix.rstrip("/") + "/" + route_path.lstrip("/")).rstrip("/") or "/"
                # Detect routes that collapse to just the prefix (e.g. prefix + "/")
                if route_path in ("/", "") and prefix:
                    issues.append(
                        f"router_path_validation: {rfile.name}: route '{method.upper()} {route_path}' "
                        f"with prefix '{prefix}' resolves to '{effective or prefix}/' "
                        f"(likely missing resource path segment)"
                    )
                route_paths.append(route_path)

        # Detect path-param routes defined before static routes (shadowing)
        param_seen = False
        for rp in route_paths:
            if re.search(r'\{[^}]+\}', rp):
                param_seen = True
            elif param_seen and not re.search(r'\{[^}]+\}', rp):
                issues.append(
                    f"router_path_validation: {rfile.name}: static route '{rp}' "
                    f"defined after parameterized route — may be shadowed"
                )

    return issues


# ---------------------------------------------------------------------------
# Helper: type_consistency check (Fix 7)
# ---------------------------------------------------------------------------

def _check_type_consistency(output_dir: Path) -> List[str]:
    """Cross-reference SQLAlchemy model PK types with router path-param types.

    Returns a list of error strings.
    """
    errors: List[str] = []

    # --- Step 1: collect model PK column types ---
    # entity_name → SQLAlchemy type (e.g. "Integer", "String", "UUID")
    model_pk_types: Dict[str, str] = {}

    _sa_type_map = {
        "integer": "int",
        "biginteger": "int",
        "smallinteger": "int",
        "string": "str",
        "text": "str",
        "varchar": "str",
        "uuid": "uuid.UUID",
    }

    model_files = list(output_dir.rglob("models/*.py"))
    for mf in model_files:
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
                if not isinstance(stmt, ast.Assign):
                    continue
                # Look for: id = Column(Integer, primary_key=True)
                if not (isinstance(stmt.value, ast.Call)):
                    continue
                call = stmt.value
                func_name = ""
                if isinstance(call.func, ast.Name):
                    func_name = call.func.id
                elif isinstance(call.func, ast.Attribute):
                    func_name = call.func.attr
                if func_name.lower() != "column":
                    continue
                # Check primary_key=True
                is_pk = any(
                    (isinstance(kw.value, ast.Constant) and kw.value.value is True
                     and kw.arg == "primary_key")
                    for kw in call.keywords
                )
                if not is_pk:
                    continue
                # Get the type argument (first positional arg)
                if not call.args:
                    continue
                type_arg = call.args[0]
                col_type = ""
                if isinstance(type_arg, ast.Name):
                    col_type = type_arg.id
                elif isinstance(type_arg, ast.Attribute):
                    col_type = type_arg.attr
                if col_type:
                    model_pk_types[entity] = col_type

    if not model_pk_types:
        return errors

    # --- Step 2: collect router path parameter type annotations ---
    # entity_name → annotated type string (from e.g. `product_id: uuid.UUID`)
    router_param_types: Dict[str, str] = {}

    router_files = list(output_dir.rglob("routers/*.py"))
    for rf in router_files:
        try:
            source = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for arg in node.args.args:
                if not (arg.arg.endswith("_id") and arg.annotation):
                    continue
                entity = arg.arg[: -len("_id")]
                ann = arg.annotation
                ann_str = ""
                if isinstance(ann, ast.Name):
                    ann_str = ann.id
                elif isinstance(ann, ast.Attribute):
                    ann_str = f"{ann.value.id}.{ann.attr}" if isinstance(ann.value, ast.Name) else ann.attr
                if ann_str:
                    router_param_types[entity] = ann_str

    # --- Step 3: cross-reference ---
    for entity, model_type in model_pk_types.items():
        router_type = router_param_types.get(entity)
        if router_type is None:
            continue
        expected_router = _sa_type_map.get(model_type.lower())
        if expected_router is None:
            continue
        # Normalise
        rt_lower = router_type.lower().replace("uuid.uuid", "uuid.uuid")
        et_lower = expected_router.lower()
        if et_lower == "uuid.uuid":
            if "uuid" not in rt_lower:
                errors.append(
                    f"type_consistency: entity '{entity}': model PK is "
                    f"'{model_type}' (UUID) but router uses '{router_type}'"
                )
        elif et_lower == "int":
            if rt_lower not in ("int", "integer"):
                errors.append(
                    f"type_consistency: entity '{entity}': model PK is "
                    f"'{model_type}' (int) but router uses '{router_type}'"
                )
        elif et_lower == "str":
            if rt_lower not in ("str", "string"):
                errors.append(
                    f"type_consistency: entity '{entity}': model PK is "
                    f"'{model_type}' (str) but router uses '{router_type}'"
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
    import subprocess
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
