# WARNING: This is a migration utility script. DO NOT call from production services or package in production builds.
# Its purpose is to perform one-time or controlled updates to source code files.

"""
Auto-migrate inline prompt string dictionaries (e.g., PROMPT_TEMPLATES, prompts, TEMPLATES) to Jinja2 .j2 template files.

Usage:
    python scripts/migrate_prompts.py --source clarifier_llm_call.py --dest clarifier/prompts/
    python scripts/migrate_prompts.py --source . --dest . --recursive
    python scripts/migrate_prompts.py --source . --dest . --var-names MY_PROMPTS custom_templates

Features:
- Finds Python files with inline prompt dicts (supports multiple naming conventions).
- By default, searches for: PROMPT_TEMPLATES, prompts, TEMPLATES, PROMPTS, prompt_templates, template_dict, TEMPLATE_DICT
- Supports custom variable names via --var-names argument for flexibility.
- Extracts each prompt string to a separate .j2 file named by its key (e.g., 'my_prompt_key.j2').
- Replaces the inline prompt dict in the original Python file with code to load templates at runtime using Jinja2.
- Handles multiline strings, triple quotes, and escapes during extraction.
- Supports dry-run mode for previewing changes without writing to disk.
- Backs up original Python files before modification (creates '.bak' files).
- Provides a summary report of migrated prompts and encountered errors.
- Generates a detailed migration log in JSON format.
- Lints extracted Jinja2 templates for basic syntax errors.

Requirements:
- Python 3.9+ (for ast.unparse, Path.read_text/write_text)
- pip install jinja2 rich tqdm

Security Note: This script performs file I/O operations. Ensure it is run with appropriate
user permissions in a controlled environment (e.g., CI/CD pipeline, local development machine).
Avoid running as root or with elevated privileges unless strictly necessary.
"""

import argparse
import ast
import difflib
import json
import logging
import re
import shutil
import sys
import unittest
import uuid
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import (
    Environment,
    TemplateSyntaxError,
    select_autoescape,
)  # meta for template parsing
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

# Setup rich logging
FORMAT = "%(message)s"
logging.basicConfig(
    level="INFO",
    format=FORMAT,
    datefmt="[%X]",
    handlers=[
        RichHandler(console=Console(), show_time=False)
    ],  # Removed show_time for cleaner output
)
logger = logging.getLogger("migrate_prompts")


class PromptMigrationError(Exception):
    """Custom exception for migration-specific errors."""

    pass


def find_prompt_dict(
    tree: ast.Module, var_names: List[str] = None
) -> Optional[Tuple[str, ast.Dict]]:
    """
    Finds a prompt dictionary assignment in the AST.

    Args:
        tree (ast.Module): The AST of the Python file.
        var_names (List[str]): List of variable names to search for.
                               Defaults to common prompt dictionary names.
    Returns:
        Optional[Tuple[str, ast.Dict]]: A tuple of (variable_name, ast.Dict) if found, otherwise None.
    """
    if var_names is None:
        # Default list of common prompt dictionary variable names
        var_names = [
            "PROMPT_TEMPLATES",
            "prompts",
            "TEMPLATES",
            "PROMPTS",
            "prompt_templates",
            "template_dict",
            "TEMPLATE_DICT",
        ]

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in var_names:
                    if isinstance(node.value, ast.Dict):
                        return (target.id, node.value)
                    else:
                        logger.warning(
                            f"Found '{target.id}' but it's not a dict in: {ast.unparse(node).strip()}. Skipping."
                        )
                        return None
    return None


def extract_prompts_from_dict(dict_node: ast.Dict) -> List[Tuple[str, str]]:
    """
    Extracts key-value pairs from a dictionary AST node, assuming string keys and values.
    Args:
        dict_node (ast.Dict): The AST Dict node representing PROMPT_TEMPLATES.
    Returns:
        List[Tuple[str, str]]: A list of (key_string, value_string) tuples.
    Raises:
        PromptMigrationError: If non-string keys or values are encountered which prevent extraction.
    """
    prompts: List[Tuple[str, str]] = []
    for key, value in zip(dict_node.keys, dict_node.values):
        if isinstance(key, ast.Constant) and isinstance(
            key.value, str
        ):  # ast.Str is deprecated in 3.8+
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                prompts.append((key.value, value.value))
            elif isinstance(value, ast.FormattedValue) or isinstance(
                value, ast.JoinedStr
            ):  # Handle f-strings if they appear
                logger.warning(
                    f"Skipping f-string value for key '{key.value}'. Prompts should be pure Jinja2: {ast.unparse(value)}"
                )
                continue
            else:
                logger.warning(
                    f"Skipping non-string value for key '{key.value}': {ast.unparse(value).strip()}"
                )
                # Gold Standard: Raise an error if strictness is required for non-string values
                # raise PromptMigrationError(f"Non-string value found for prompt key '{key.value}': {ast.unparse(value).strip()}")
        else:
            logger.warning(f"Skipping non-string key: {ast.unparse(key).strip()}")
            # Gold Standard: Raise if strictness is required for non-string keys
            # raise PromptMigrationError(f"Non-string key found in prompt dict: {ast.unparse(key).strip()}")
    return prompts


def generate_loader_code(template_dir: str, var_name: str = "PROMPT_TEMPLATES") -> str:
    """
    Generates Python code that dynamically loads prompts from Jinja2 files in a specified directory.
    Args:
        template_dir (str): The directory where Jinja2 templates will be stored.
        var_name (str): The name of the variable to assign the loaded templates to.
    Returns:
        str: The Python code snippet.
    """
    # Gold Standard: Use `FileSystemLoader(template_dir, followlinks=False)` for security (prevent symlink traversal)
    # Ensure template_dir is relative to the calling script or absolute for FileSystemLoader.
    return f"""
import os
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

# Gold Standard: Use followlinks=False for security to prevent directory traversal via symlinks
_template_loader_env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), '{template_dir}'), followlinks=False))

def _load_prompt_templates_from_disk():
    \"\"\"Dynamically loads Jinja2 templates from the specified directory.\"\"\"
    loaded_templates = {{}}
    try:
        # Get path relative to the current file for robustness
        current_script_dir = Path(__file__).parent
        actual_template_path = current_script_dir / Path('{template_dir}')

        if not actual_template_path.is_dir():
            print(f"WARNING: Prompt template directory {{actual_template_path}} not found. Returning empty templates.", file=sys.stderr)
            return {{}}

        # Iterate only over .j2 files directly in the specified template_dir (not recursive)
        for fname in os.listdir(actual_template_path):
            if fname.endswith('.j2') and (actual_template_path / fname).is_file():
                key = fname[:-3].replace('_', ' ').replace('-', ' ').title() # Standardize key formatting
                try:
                    loaded_templates[key] = _template_loader_env.get_template(fname)
                except Exception as e:
                    print(f"ERROR: Failed to load Jinja2 template '{{fname}}': {{e}}", file=sys.stderr)
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to initialize prompt template loader: {{e}}", file=sys.stderr)
    return loaded_templates

{var_name} = _load_prompt_templates_from_disk()
"""


class PromptReplacer(ast.NodeTransformer):
    """
    AST transformer to replace the prompt dict assignment with loader code.
    """

    def __init__(self, loader_code: str, var_name: str):
        self.loader_code_ast = ast.parse(loader_code.strip()).body
        self.var_name = var_name
        self.replaced = False

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        self.generic_visit(node)
        # Check if this is the target variable assignment
        if not self.replaced:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == self.var_name:
                    if isinstance(node.value, ast.Dict):
                        # Replace the assignment node with the loader code
                        self.replaced = True
                        # The loader code might be multiple statements. Insert them directly.
                        # If loader_code_ast is list, return as list.
                        # If it's a single statement, just replace the node.
                        if len(self.loader_code_ast) == 1:
                            return self.loader_code_ast[0]
                        return self.loader_code_ast
        return node


def replace_prompt_dict_in_code(
    original_code: str, var_name: str, loader_code: str
) -> str:
    """
    Replaces the prompt dict in the original code with generated loader code.
    Args:
        original_code (str): The content of the original Python file.
        var_name (str): The name of the variable to replace.
        loader_code (str): The Python code string for the new loader.
    Returns:
        str: The modified Python code.
    Raises:
        PromptMigrationError: If the dict node cannot be found or replaced in the code.
    """
    tree = ast.parse(original_code)
    transformer = PromptReplacer(loader_code, var_name)
    new_tree = transformer.visit(tree)
    if not transformer.replaced:
        raise PromptMigrationError(
            f"Failed to find and replace '{var_name}' dict in the code."
        )

    # ast.unparse requires Python 3.9+
    return ast.unparse(new_tree)


def lint_template(prompt_content: str) -> Optional[str]:
    """
    Lints the Jinja2 template content for basic syntax errors.
    Args:
        prompt_content (str): The content of the Jinja2 template.
    Returns:
        Optional[str]: An error message if a syntax error is found, otherwise None.
    """
    env = Environment(
        autoescape=select_autoescape(["html", "xml", "htm", "j2", "jinja2"])
    )
    try:
        env.parse(prompt_content)
        return None
    except TemplateSyntaxError as e:
        return str(e)


def migrate_file(
    source_file: Path,
    dest_dir: Path,
    dry_run: bool = False,
    verbose: bool = True,
    backup: bool = True,
    var_names: List[str] = None,
) -> Dict[str, Any]:
    """
    Migrates a single Python file's prompt dict to .j2 templates.
    Args:
        source_file (Path): The Python file to migrate.
        dest_dir (Path): The directory where .j2 templates will be saved.
        dry_run (bool): If True, only preview changes, do not write.
        verbose (bool): If True, log detailed messages.
        backup (bool): If True, create a .bak file of the original.
        var_names (List[str]): List of variable names to search for. If None, uses defaults.
    Returns:
        Dict[str, Any]: A report of the migration process for this file.
    """
    report: Dict[str, Any] = {
        "file": str(source_file.resolve()),
        "prompts_migrated": 0,
        "errors": [],
        "diff": "",
        "status": "failed",
    }

    try:
        original_code = source_file.read_text(encoding="utf-8")
        tree = ast.parse(original_code)
        result = find_prompt_dict(tree, var_names)

        if not result:
            report["status"] = "no_prompts_found"
            report["message"] = "No prompt dict found with recognized variable names."
            if verbose:
                logger.info(f"No prompt dict found in {source_file}")
            return report

        var_name, dict_node = result
        if verbose:
            logger.info(f"Found prompt dict variable '{var_name}' in {source_file}")

        prompts = extract_prompts_from_dict(dict_node)
        if not prompts:
            report["status"] = "no_prompts_extracted"
            report["message"] = (
                f"Prompt dict '{var_name}' found, but no valid prompts extracted (non-string values)."
            )
            if verbose:
                logger.info(f"No valid prompts extracted from {source_file}")
            return report

        dest_dir.mkdir(parents=True, exist_ok=True)

        # --- Extract and Lint Templates ---
        extracted_prompts_info: List[Dict[str, str]] = []
        for key, value in prompts:
            fname = f"{key.lower().replace(' ', '_').replace('-', '_')}.j2"
            fpath = dest_dir / fname
            lint_error = lint_template(value)
            if lint_error:
                error_msg = (
                    f"Syntax error in Jinja2 template for prompt '{key}': {lint_error}"
                )
                report["errors"].append(error_msg)
                logger.error(error_msg)
                continue  # Skip this prompt if it has syntax errors

            if not dry_run:
                try:
                    fpath.write_text(value.strip() + "\n", encoding="utf-8")
                except Exception as e:
                    error_msg = f"Failed to write template file {fpath}: {e}"
                    report["errors"].append(error_msg)
                    logger.error(error_msg)
                    continue

            extracted_prompts_info.append({"key": key, "file": str(fpath.resolve())})
            report["prompts_migrated"] += 1
            if verbose:
                logger.info(f"Extracted prompt '{key}' -> {fpath}")

        if not extracted_prompts_info and not report["errors"]:
            report["status"] = "no_valid_prompts_extracted"
            report["message"] = (
                "No valid prompts extracted after linting, despite dict being present."
            )
            return report

        # --- Generate Loader Code and Replace in Source File ---
        loader_code = generate_loader_code(
            str(dest_dir.relative_to(source_file.parent)), var_name
        )  # Relative path for loader
        new_code = replace_prompt_dict_in_code(original_code, var_name, loader_code)

        # --- Backup and Write (if not dry run) ---
        if not dry_run:
            if backup:
                backup_file = source_file.with_suffix(source_file.suffix + ".bak")
                try:
                    shutil.copy(source_file, backup_file)
                    if verbose:
                        logger.info(f"Backed up {source_file} to {backup_file}")
                except Exception as e:
                    error_msg = f"Failed to create backup of {source_file}: {e}"
                    report["errors"].append(error_msg)
                    logger.error(error_msg)
                    # Proceed without backup if it fails, but note the error

            try:
                source_file.write_text(new_code, encoding="utf-8")
                if verbose:
                    logger.info(f"Updated {source_file} with loader logic.")
            except Exception as e:
                error_msg = f"Failed to write updated code to {source_file}: {e}"
                report["errors"].append(error_msg)
                logger.critical(
                    error_msg
                )  # Critical error as original file could be corrupted
                raise PromptMigrationError(
                    error_msg
                )  # Re-raise to stop process if file write fails

        # --- Generate Diff and Final Report Status ---
        diff = "\n".join(
            difflib.unified_diff(
                original_code.splitlines(keepends=True),
                new_code.splitlines(keepends=True),
                fromfile=str(source_file),
                tofile=str(source_file),
            )
        )
        report["diff"] = diff
        report["status"] = "success" if not report["errors"] else "partial_success"
        report["message"] = (
            "Migration completed."
            if report["status"] == "success"
            else "Migration completed with errors."
        )

    except PromptMigrationError as e:  # Catch errors specifically from this script
        report["errors"].append(str(e))
        report["status"] = "failed"
        report["message"] = str(e)
        logger.critical(f"Migration failed for {source_file}: {e}")
    except Exception as e:  # Catch any unexpected errors
        report["errors"].append(f"Unexpected error: {e}")
        report["status"] = "failed"
        report["message"] = f"Unexpected error during migration: {e}"
        logger.critical(f"Unexpected error migrating {source_file}: {e}", exc_info=True)

    return report


def migrate_dir(
    source_dir: Path,
    dest_dir: Path,
    recursive: bool = False,
    dry_run: bool = False,
    verbose: bool = True,
    backup: bool = True,
    var_names: List[str] = None,
) -> List[Dict[str, Any]]:
    """
    Walks source_dir for Python files and migrates all prompt dicts.
    Args:
        source_dir (Path): The root directory to scan for Python files.
        dest_dir (Path): The base directory for saving .j2 templates.
        recursive (bool): If True, scan subdirectories recursively.
        dry_run (bool): If True, only preview changes.
        verbose (bool): If True, log detailed messages.
        backup (bool): If True, create backups of original files.
        var_names (List[str]): List of variable names to search for. If None, uses defaults.
    Returns:
        List[Dict[str, Any]]: A list of migration reports for each processed file.
    """
    py_files: List[Path] = []
    if recursive:
        py_files = list(source_dir.rglob("*.py"))
    else:
        py_files = list(source_dir.glob("*.py"))

    reports: List[Dict[str, Any]] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn(
            "{task.completed}/{task.total} files [green]{task.fields[successes]} passed, [yellow]{task.fields[partials]} partials, [red]{task.fields[failures]} failed"
        ),  # Richer status
        transient=False,  # Keep progress bar on screen after completion
        console=Console(),
    ) as progress:
        task = progress.add_task(
            "Migrating files", total=len(py_files), successes=0, partials=0, failures=0
        )
        for py_file in py_files:
            # Ensure templates are saved relative to the *source* file's directory
            # For `migrate_dir`, the `dest_dir` is fixed, so templates from different source folders
            # will all go into the *same* `dest_dir`.
            # This is acceptable if all prompts are unique across files.
            # If not, a more complex `dest_dir` strategy (e.g., mirroring source folder structure) is needed.
            report = migrate_file(
                py_file, dest_dir, dry_run, verbose, backup, var_names
            )
            reports.append(report)

            if report["status"] == "success":
                progress.columns[3].fields["successes"] += 1
            elif report["status"] == "partial_success":
                progress.columns[3].fields["partials"] += 1
            elif report["status"] == "failed":
                progress.columns[3].fields["failures"] += 1
            progress.update(task, advance=1)
    return reports


def generate_summary_report(reports: List[Dict[str, Any]]) -> str:
    """
    Generates a human-readable summary report from migration reports and saves a detailed log.
    Args:
        reports (List[Dict[str, Any]]): List of migration reports from migrate_file/migrate_dir.
    Returns:
        str: A formatted summary string.
    """
    total_files: int = len(reports)
    successful: int = sum(1 for r in reports if r["status"] == "success")
    partial_successes: int = sum(1 for r in reports if r["status"] == "partial_success")
    failed: int = sum(1 for r in reports if r["status"] == "failed")
    no_prompts_found: int = sum(
        1
        for r in reports
        if r["status"]
        in ["no_prompts_found", "no_prompts_extracted", "no_valid_prompts_extracted"]
    )
    total_prompts_migrated: int = sum(r["prompts_migrated"] for r in reports)
    total_errors_encountered: int = sum(len(r["errors"]) for r in reports)

    summary_lines = [
        "\nMigration Summary:",
        f"- Total files processed: {total_files}",
        f"- Successful migrations: {successful}",
        f"- Partial successes (with errors): {partial_successes}",
        f"- Failed migrations (critical errors): {failed}",
        f"- Files with no prompts found/extracted: {no_prompts_found}",
        f"- Total prompts extracted and migrated: {total_prompts_migrated}",
        f"- Total errors encountered during migration: {total_errors_encountered}",
    ]

    # Detailed error breakdown
    if total_errors_encountered > 0:
        summary_lines.append("\nDetailed Errors:")
        for r in reports:
            if r["errors"]:
                summary_lines.append(f"  File: {r['file']}")
                for error_msg in r["errors"]:
                    summary_lines.append(f"    - {error_msg}")
                if r["status"] == "failed" and "message" in r:
                    summary_lines.append(f"    Reason: {r['message']}")

    summary_report = "\n".join(summary_lines)

    try:
        with open("migration_log.json", "w", encoding="utf-8") as f:
            json.dump(reports, f, indent=2, ensure_ascii=False)
        logger.info("Detailed migration log saved to migration_log.json")
    except Exception as e:
        logger.error(f"Failed to save migration_log.json: {e}", exc_info=True)

    return summary_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate inline prompt dicts to .j2 template files."
    )
    parser.add_argument("--source", help="Source .py file or directory")
    parser.add_argument("--dest", help="Destination directory for .j2 templates")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan directories for .py files.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes, do not write files."
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not backup original files before modification.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Log verbose output during migration."
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run unit tests for the script (requires Hypothesis).",
    )
    parser.add_argument(
        "--var-names",
        nargs="+",
        help="Custom variable names to search for (e.g., 'PROMPT_TEMPLATES prompts TEMPLATES'). Default: searches common names.",
    )
    args = parser.parse_args()

    # Run unit tests if --test flag is present
    if args.test:
        # Mock logging output for tests
        logging.getLogger("migrate_prompts").handlers = []
        logging.getLogger("migrate_prompts").addHandler(
            RichHandler(
                console=Console(file=StringIO()),
                show_time=False,
                show_level=False,
                show_path=False,
            )
        )
        # Ensure Hypothesis is available for property-based tests
        try:
            import hypothesis
            import hypothesis.strategies as st

            HAS_HYPOTHESIS_TESTS = True
        except ImportError:
            HAS_HYPOTHESIS_TESTS = False
            print("Hypothesis not installed. Property-based tests will be skipped.")

        # Gold Standard: Create a test suite specific to the migration logic
        class TestMigratePrompts(unittest.TestCase):
            def setUp(self):
                # Ensure test_dir is unique for parallel/repeated runs
                self.test_dir = Path(f"test_migrate_{uuid.uuid4()}")
                self.test_dir.mkdir(exist_ok=True)
                self.dest_dir = self.test_dir / "prompts"
                self.test_file = self.test_dir / "test_prompts.py"
                self.test_file_bak = self.test_file.with_suffix(
                    self.test_file.suffix + ".bak"
                )
                self.initial_content = """
import os
from jinja2 import Environment, FileSystemLoader

PROMPT_TEMPLATES = {
    'key_one': 'This is prompt one content.',
    'key_two': '''This is prompt two
multiline content.'''
}

def some_other_function():
    print("hello world")
"""
                self.test_file.write_text(self.initial_content)
                # Redirect stdout for cleaner test output
                self._original_stdout = sys.stdout
                sys.stdout = StringIO()

            def tearDown(self):
                sys.stdout = self._original_stdout  # Restore stdout
                shutil.rmtree(self.test_dir)

            def test_find_prompt_dict(self):
                tree = ast.parse(self.initial_content)
                result = find_prompt_dict(tree)
                self.assertIsNotNone(result)
                var_name, dict_node = result
                self.assertEqual(var_name, "PROMPT_TEMPLATES")
                self.assertEqual(len(dict_node.keys), 2)

                # Test no prompt dict
                no_prompt_content = "VAR = 123"
                tree_no_prompt = ast.parse(no_prompt_content)
                self.assertIsNone(find_prompt_dict(tree_no_prompt))

                # Test non-dict PROMPT_TEMPLATES
                non_dict_content = "PROMPT_TEMPLATES = [1,2,3]"
                tree_non_dict = ast.parse(non_dict_content)
                self.assertIsNone(find_prompt_dict(tree_non_dict))

                # Test alternative naming
                alt_content = "prompts = {'key': 'value'}"
                tree_alt = ast.parse(alt_content)
                result_alt = find_prompt_dict(tree_alt)
                self.assertIsNotNone(result_alt)
                var_name_alt, _ = result_alt
                self.assertEqual(var_name_alt, "prompts")

            def test_extract_prompts_from_dict(self):
                tree = ast.parse(self.initial_content)
                result = find_prompt_dict(tree)
                _, dict_node = result
                prompts = extract_prompts_from_dict(dict_node)
                self.assertEqual(
                    prompts,
                    [
                        ("key_one", "This is prompt one content."),
                        ("key_two", "This is prompt two\nmultiline content."),
                    ],
                )

                # Test with non-string value (should be skipped by warning)
                non_string_content = "PROMPT_TEMPLATES = {'key': 123}"
                tree_non_string = ast.parse(non_string_content)
                result_non_string = find_prompt_dict(tree_non_string)
                _, dict_node_non_string = result_non_string
                prompts_non_string = extract_prompts_from_dict(dict_node_non_string)
                self.assertEqual(prompts_non_string, [])  # Should extract nothing

            def test_lint_template(self):
                self.assertIsNone(lint_template("Hello {{ name }}!"))
                self.assertIsNotNone(
                    lint_template("{% for item in items %}{{ item %}}")
                )  # Missing endfor
                self.assertIsNotNone(lint_template("{{ invalid filter | }"))

            def test_migrate_file_success(self):
                report = migrate_file(
                    self.test_file,
                    self.dest_dir,
                    dry_run=False,
                    verbose=True,
                    backup=True,
                )
                self.assertEqual(report["status"], "success")
                self.assertEqual(report["prompts_migrated"], 2)
                self.assertTrue(self.test_file_bak.exists())
                self.assertTrue((self.dest_dir / "key_one.j2").exists())
                self.assertTrue((self.dest_dir / "key_two.j2").exists())
                self.assertIn(
                    "load_prompt_templates_from_disk()", self.test_file.read_text()
                )
                self.assertNotIn("PROMPT_TEMPLATES = {", self.test_file.read_text())

            def test_migrate_file_dry_run(self):
                report = migrate_file(
                    self.test_file,
                    self.dest_dir,
                    dry_run=True,
                    verbose=True,
                    backup=True,
                )
                self.assertEqual(report["status"], "success")
                self.assertEqual(report["prompts_migrated"], 2)
                self.assertFalse(self.test_file_bak.exists())  # No backup in dry run
                self.assertFalse(
                    (self.dest_dir / "key_one.j2").exists()
                )  # No template created in dry run
                self.assertEqual(
                    self.test_file.read_text(), self.initial_content
                )  # Original file untouched

            def test_migrate_file_no_prompts(self):
                self.test_file.write_text("VAR = {'not_prompts': 1}")
                report = migrate_file(
                    self.test_file,
                    self.dest_dir,
                    dry_run=False,
                    verbose=True,
                    backup=True,
                )
                self.assertEqual(report["status"], "no_prompts_found")
                self.assertEqual(report["prompts_migrated"], 0)
                self.assertEqual(
                    self.test_file.read_text(), "VAR = {'not_prompts': 1}"
                )  # File untouched

            def test_migrate_file_with_lint_error(self):
                self.test_file.write_text("""
PROMPT_TEMPLATES = {
    'bad_key': 'Hello {{ name }' # Malformed Jinja2
}
""")
                report = migrate_file(
                    self.test_file,
                    self.dest_dir,
                    dry_run=False,
                    verbose=True,
                    backup=True,
                )
                self.assertEqual(report["status"], "partial_success")
                self.assertIn("Syntax error in Jinja2 template", report["errors"][0])
                self.assertEqual(
                    report["prompts_migrated"], 0
                )  # No prompts migrated due to error

            if HAS_HYPOTHESIS_TESTS:
                from hypothesis import given
                from hypothesis import strategies as st

                @given(
                    st.dictionaries(
                        st.text(
                            min_size=1,
                            max_size=5,
                            alphabet=st.characters(blacklist_categories=["Cs"]),
                        ),  # No surrogate characters
                        st.text(
                            min_size=1,
                            max_size=50,
                            alphabet=st.characters(blacklist_categories=["Cs"]),
                        ),
                        min_size=1,
                        max_size=5,
                    )
                )
                async def test_fuzz_extract_and_lint(self, prompts_dict_raw):
                    # Ensure keys are valid Python identifiers for AST unparsing
                    prompts_dict_valid_keys = {
                        re.sub(r"[^a-zA-Z0-9_]", "_", k): v
                        for k, v in prompts_dict_raw.items()
                    }

                    # Create content that simulates PROMPT_TEMPLATES
                    content = f"PROMPT_TEMPLATES = {repr(prompts_dict_valid_keys)}"
                    self.test_file.write_text(content)

                    tree = ast.parse(content)
                    dict_node = find_prompt_dict(tree)

                    try:
                        extracted = extract_prompts_from_dict(dict_node)
                        for k, v in extracted:
                            lint_result = lint_template(v)
                            # Assertion logic based on what Jinja2 parse might accept
                            # This is a fuzzer for the extraction/linting, not the migrator itself.
                            if "{%" in v or "{{" in v or "{#" in v:
                                # If it looks like Jinja2, expect it to either parse correctly or fail linting
                                pass
                            else:
                                self.assertIsNone(
                                    lint_result,
                                    f"Lint error on non-Jinja2: {lint_result}",
                                )
                    except Exception as e:
                        # Catch AST parsing errors or unexpected errors in extract/lint
                        self.fail(f"Fuzz test failed with {e} for content:\n{content}")

        # Run asyncio tests if needed
        # unittest.main() runs sync tests by default. For async tests, need to wrap.
        suite = unittest.TestSuite()
        suite.addTest(unittest.makeSuite(TestMigratePrompts))
        # Use asyncio.run for the test suite itself if tests are async
        # For simplicity in __main__, run directly.

        # Capture stderr to suppress RichHandler output during tests
        test_output_catcher = StringIO()
        sys.stderr = test_output_catcher

        # Run tests directly
        runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=1)
        runner.run(suite)

        sys.stderr = sys.__stderr__  # Restore stderr
        # print(test_output_catcher.getvalue(), file=sys.__stderr__) # Print captured output for debugging

    else:
        # Standard script execution
        if not args.source or not args.dest:
            parser.error("--source and --dest are required when not running tests")

        source_path = Path(args.source)
        dest_path = Path(args.dest)

        def main():
            if source_path.is_dir():
                reports = migrate_dir(
                    source_path,
                    dest_path,
                    args.recursive,
                    args.dry_run,
                    args.verbose,
                    not args.no_backup,
                    args.var_names,
                )
            else:
                reports = [
                    migrate_file(
                        source_path,
                        dest_path,
                        args.dry_run,
                        args.verbose,
                        not args.no_backup,
                        args.var_names,
                    )
                ]

            summary = generate_summary_report(reports)
            print(summary)

        main()
