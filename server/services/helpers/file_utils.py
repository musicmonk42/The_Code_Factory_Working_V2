"""File-system utility helpers extracted from ``omnicore_service.py``.

Functions for ensuring Python package structure, pre-materialization import
checks, double-nesting fixes, and delta-prompt building.
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)


def _ensure_python_package_structure(output_dir: Path) -> None:
    """Ensure all directories with ``.py`` files have ``__init__.py``.

    Args:
        output_dir: Root directory to scan for Python files.
    """
    if not output_dir.exists():
        logger.warning(f"Output directory {output_dir} does not exist, skipping __init__.py creation")
        return

    skip_dirs = {
        "__pycache__", ".git", ".pytest_cache", ".mypy_cache",
        "node_modules", ".venv", "venv", "env", "reports",
    }

    created_count = 0
    dirs_with_python: Set[Path] = set()

    if any(output_dir.glob("*.py")):
        dirs_with_python.add(output_dir)

    for subdir in output_dir.rglob("*"):
        if subdir.is_dir() and subdir.name not in skip_dirs:
            if any(subdir.glob("*.py")):
                dirs_with_python.add(subdir)

    all_package_dirs: Set[Path] = set()
    for pydir in dirs_with_python:
        current = pydir
        while current != output_dir and current.parent != output_dir.parent:
            all_package_dirs.add(current)
            current = current.parent
        all_package_dirs.add(output_dir)

    for pkg_dir in sorted(all_package_dirs):
        if pkg_dir.name not in skip_dirs:
            init_file = pkg_dir / "__init__.py"
            if not init_file.exists():
                init_file.write_text("# Auto-generated for package imports\n")
                logger.debug(f"Created __init__.py in {pkg_dir}")
                created_count += 1

    if created_count > 0:
        logger.info(f"Created {created_count} __init__.py files in {output_dir}")


# NOTE: _pre_materialization_import_check exceeds the 40-line guideline
# (~50 lines).  Preserved as-is for behavioural equivalence.
def _pre_materialization_import_check(files: Dict[str, str]) -> List[str]:
    """Perform an in-memory import validity check before writing files to disk.

    Args:
        files: Dict mapping file paths to source content strings.

    Returns:
        List of error strings.  Empty means all imports are resolvable.
    """
    errors: List[str] = []

    module_symbols: Dict[str, Set[str]] = {}
    for filepath, content in files.items():
        if not filepath.endswith(".py") or not isinstance(content, str):
            continue
        mod = filepath.replace("\\", "/").replace("/", ".").removesuffix(".py")
        if mod.endswith(".__init__"):
            mod = mod[:-9]
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        syms: Set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                syms.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        syms.add(t.id)
        module_symbols[mod] = syms

    for filepath, content in files.items():
        if not filepath.endswith(".py") or not isinstance(content, str):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            errors.append(f"{filepath}: SyntaxError: {e}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if not module.startswith("app.") and module != "app":
                continue
            target_syms = module_symbols.get(module)
            if target_syms is None:
                errors.append(
                    f"{filepath}: module '{module}' not found in generated files"
                )
                continue
            for alias in node.names:
                sym = alias.name
                if sym != "*" and sym not in target_syms:
                    errors.append(
                        f"{filepath}: '{sym}' imported from '{module}' but not defined there"
                    )

    return errors


def _fix_double_nesting(output_dir: Path) -> None:
    """Flatten a nested ``generated/`` subdirectory inside *output_dir*."""
    nested_generated = output_dir / "generated"
    if nested_generated.is_dir():
        logger.warning(f"Double-nesting detected at {nested_generated}, flattening...")
        for item in os.listdir(nested_generated):
            src = nested_generated / item
            dst = output_dir / item
            if not dst.exists():
                shutil.move(str(src), str(dst))
            else:
                logger.warning(
                    f"Double-nesting flatten: skipping '{item}' -- destination already exists at {dst}"
                )
        try:
            if not os.listdir(nested_generated):
                os.rmdir(nested_generated)
        except OSError:
            pass


def _build_delta_prompt(missing_endpoints: list, base_requirements: str) -> str:
    """Build a focused prompt for delta (incremental) code generation.

    Args:
        missing_endpoints: List of endpoint labels (e.g. ``["GET /api/users"]``).
        base_requirements: Original spec text, used as context.

    Returns:
        A prompt string focused on implementing the missing endpoints.
    """
    ep_list = "\n".join(f"  - {ep}" for ep in missing_endpoints)
    return (
        f"{base_requirements}\n\n"
        f"## Delta Generation -- Implement ONLY the following missing endpoints:\n"
        f"{ep_list}\n\n"
        f"Generate ONLY the router/handler files required for the endpoints listed above. "
        f"Do NOT regenerate already-existing files."
    )
