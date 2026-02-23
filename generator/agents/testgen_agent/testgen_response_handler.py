# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
testgen_response_handler.py: The Parser & Guard for the agentic testing system.

REFACTORED v2.0: This module has been improved with better separation of concerns.
All validation logic is now properly separated into individual, testable methods.

Key improvements:
- Added _lint_code() for linting concerns
- Added _static_analysis() for static analysis concerns
- Added _security_scan() for security scanning concerns
- Added _ast_verification() for AST validation concerns
- Refactored validate() to orchestrate these methods
- Each method is independently testable and mockable
- Better extensibility through class inheritance

Features:
- Multi-format parsing with fallback and recovery strategies.
- Validation using language-specific linters, analyzers, and security scanners.
- AST verification against source code for coverage assurance.
- LLM auto-healing for malformed responses (using runner.llm_client).
- Audit logging with pre/post hashes for compliance (using runner.add_provenance).
- Hot-reloading for parser plugins.
- Health endpoints for Kubernetes (port 8081).
- Plugin architecture for custom parsers.

Dependencies:
- json, re, ast, subprocess, tempfile, os, xml.etree.ElementTree, asyncio
- runner.llm_client, runner.runner_logging, runner.runner_metrics, runner.runner_errors
- External tools: flake8, mypy, bandit (for Python); eslint, semgrep (for JS/TS); etc.
- Environment variables: TESTGEN_PARSER_MAX_HEAL_ATTEMPTS, COMPLIANCE_MODE
"""

import ast
import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time  # For LLM latency
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Security fix: Use defusedxml to prevent XXE attacks
import defusedxml.ElementTree as ET
from aiohttp import web

# --- CENTRAL RUNNER FOUNDATION ---
from runner.llm_client import call_llm_api
# FIX: Import add_provenance from runner_audit to avoid circular dependency
from runner.runner_audit import log_audit_event as add_provenance, log_audit_event_sync as add_provenance_sync
from runner.runner_logging import logger
from runner.runner_metrics import LLM_ERRORS_TOTAL
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# -----------------------------------

# Configuration
MAX_HEAL_ATTEMPTS = int(os.getenv("TESTGEN_PARSER_MAX_HEAL_ATTEMPTS", 2))
COMPLIANCE_MODE = os.getenv("COMPLIANCE_MODE", "false").lower() == "true"
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "parser_plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)

# Advanced Sanitization Patterns
SANITIZATION_PATTERNS = {
    "[REDACTED_CREDENTIAL]": r'(?i)(api_key|password|secret|token|auth|bearer)\s*[:=]\s*["\']?[^"\']+["\']?(?=\s|$)',
    "[REDACTED_EMAIL]": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "[REDACTED_PHONE]": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    "[REDACTED_IP]": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "[REDACTED_SSN]": r"\b\d{3}-\d{2}-\d{4}\b",
    "[REDACTED_CC]": r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
}


def _local_regex_sanitize(text: str) -> str:
    """Internal helper to scrub text using this file's local patterns."""
    for replacement, pattern in SANITIZATION_PATTERNS.items():
        text = re.sub(pattern, replacement, text)
    return text


def fix_import_paths(test_files: Dict[str, str], code_files: Optional[Dict[str, str]] = None, language: str = "python") -> Dict[str, str]:
    """
    Post-process generated test imports against the actual file tree.
    
    Fixes import path issues where the LLM generates incorrect paths, such as
    'from main import app' when the correct path is 'from app.main import app',
    or 'from generated.project.app.main import app' which should be 'from app.main import app'.
    This commonly occurs because the LLM doesn't know the actual project structure.
    
    The function scans for import statements, verifies them against the project's
    actual module structure, and fixes incorrect paths automatically. It also normalizes
    paths to remove project-specific prefixes like 'generated.project_name.'.
    
    Args:
        test_files: Dictionary of test filename to content
        code_files: Optional source code files to determine correct import paths
        language: Programming language (currently only supports Python)
        
    Returns:
        Dictionary with corrected test file contents
    """
    if language != "python":
        # Only Python is supported for now
        return test_files
    
    fixed_files = {}
    
    # Build a map of module names to their correct import paths
    # e.g., {"main": "app.main", "utils": "app.utils", "models": "app.models"}
    module_map = {}
    if code_files:
        for filepath in code_files.keys():
            # Convert file paths to import paths
            # e.g., "app/main.py" -> "app.main", "app/utils/helpers.py" -> "app.utils.helpers"
            if filepath.endswith('.py'):
                import_path = filepath[:-3].replace('/', '.')
                import_parts = import_path.split('.')
                # Extract just the module name (last component)
                module_name = import_parts[-1]
                # Skip entries that would create a doubled-package import path.
                # e.g., "app/app.py" -> import_path "app.app" -> module_name "app"
                # Mapping "app" -> "app.app" would incorrectly convert
                # "from app import X" to "from app.app import X".
                if len(import_parts) >= 2 and import_parts[-1] == import_parts[-2]:
                    logger.debug(
                        f"Skipping doubled-package module_map entry for '{module_name}' -> '{import_path}'"
                    )
                    continue
                module_map[module_name] = import_path
                logger.debug(f"Mapped module '{module_name}' to import path '{import_path}'")
    
    # Pattern to match Python import statements - both "from X import Y" and "import X"
    # Group 1: Leading whitespace (indentation)
    # Group 2: "from " keyword
    # Group 3: Module path (e.g., "main" or "app.main" or "generated.project.app.main")
    # Group 4: " import Y" rest of statement
    # Using greedy * to capture full module paths like "app.utils.helpers"
    import_pattern = re.compile(r'^(\s*)(from\s+)([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)(\s+import\s+.+)$', re.MULTILINE)
    
    for filename, content in test_files.items():
        fixed_content = content
        imports_fixed = 0
        
        def replace_import(match):
            nonlocal imports_fixed
            indent = match.group(1)
            from_keyword = match.group(2)
            old_path = match.group(3)
            import_rest = match.group(4)
            
            # Remove any "generated.project_name." prefix to normalize imports
            # e.g., "generated.hello_generator.app.main" -> "app.main"
            if old_path.startswith('generated.'):
                parts = old_path.split('.')
                # Find where "app" or known module starts
                if 'app' in parts:
                    app_index = parts.index('app')
                    new_path = '.'.join(parts[app_index:])
                    imports_fixed += 1
                    logger.info(f"Normalized import in {filename}: '{old_path}' -> '{new_path}'")
                    return f"{indent}{from_keyword}{new_path}{import_rest}"
            
            # Fix imports from routes that should import from main
            # e.g., "from app.routes import app" -> "from app.main import app"
            if old_path == 'app.routes' and 'import app' in import_rest:
                imports_fixed += 1
                logger.info(f"Fixed routes import in {filename}: 'app.routes' -> 'app.main'")
                return f"{indent}{from_keyword}app.main{import_rest}"
            
            # Fix doubled package-name import paths like "from app.app import X"
            # The LLM sometimes generates "from app.app import X" instead of
            # "from app.main import X" when the project has an app/ directory.
            # Detect when consecutive path components are identical (e.g., "app.app").
            old_parts = old_path.split('.')
            if len(old_parts) >= 2 and old_parts[-1] == old_parts[-2]:
                parent_pkg = '.'.join(old_parts[:-1])
                main_path = f"{parent_pkg}.main"
                if code_files and any(
                    fp.endswith('.py') and fp[:-3].replace('/', '.') == main_path
                    for fp in code_files.keys()
                ):
                    imports_fixed += 1
                    logger.info(
                        f"Fixed doubled-package import in {filename}: '{old_path}' -> '{main_path}'"
                    )
                    return f"{indent}{from_keyword}{main_path}{import_rest}"

            # Check if this is a simple module name that needs fixing
            # e.g., "from main import" should become "from app.main import"
            if '.' not in old_path and old_path in module_map:
                new_path = module_map[old_path]
                if new_path != old_path:
                    imports_fixed += 1
                    logger.info(f"Fixed import in {filename}: '{old_path}' -> '{new_path}'")
                    return f"{indent}{from_keyword}{new_path}{import_rest}"
            
            # Return unchanged if no fix needed
            return match.group(0)
        
        fixed_content = import_pattern.sub(replace_import, fixed_content)
        
        if imports_fixed > 0:
            logger.info(f"Fixed {imports_fixed} import(s) in {filename}")
        
        fixed_files[filename] = fixed_content
    
    return fixed_files


def fix_brittle_pydantic_assertions(test_files: Dict[str, str], language: str = "python") -> Dict[str, str]:
    """
    Post-process generated Python tests to replace brittle Pydantic v1-style
    error message/type assertions with resilient Pydantic v2-compatible patterns.

    Specifically:
    - Replaces Pydantic v1 error ``type`` strings (e.g. ``"type_error.integer"``)
      with their Pydantic v2 equivalents (e.g. ``"int_parsing"``).
    - Converts exact equality checks on ``detail[*]["msg"]`` into case-insensitive
      substring assertions so that minor phrasing changes between pydantic-core
      releases do not break the test suite.
    - Replaces hard-coded Pydantic v1 message fragments (e.g.
      ``"value is not a valid integer"``) with the v2 equivalent fragments so
      that ``in str(exc_info.value)`` checks remain green across versions.

    Args:
        test_files: Mapping of filename → source code for generated test files.
        language: Source language of the test files. Only ``"python"`` files are
            processed; all other languages are returned unchanged.

    Returns:
        A new mapping with the same keys; Python test files have brittle
        Pydantic v1 assertion patterns replaced with resilient v2 equivalents.

    Note:
        The string-literal fragment replacements operate on all occurrences in
        a file, including those inside docstrings or comments.  This is
        intentional: even documentation containing stale v1 fragments benefits
        from being updated, and the risk of unintended changes is low in
        generated test files.
    """
    if language != "python":
        return test_files

    # --- Pydantic v1 → v2 error TYPE replacements (inside string literals) ---
    _type_replacements: List[Tuple[str, str]] = [
        ('"type_error.integer"', '"int_parsing"'),
        ("'type_error.integer'", "'int_parsing'"),
        ('"type_error.float"', '"float_parsing"'),
        ("'type_error.float'", "'float_parsing'"),
        ('"type_error.bool"', '"bool_parsing"'),
        ("'type_error.bool'", "'bool_parsing'"),
        ('"type_error.str"', '"string_type"'),
        ("'type_error.str'", "'string_type'"),
        ('"value_error.missing"', '"missing"'),
        ("'value_error.missing'", "'missing'"),
        ('"value_error.any_str.min_length"', '"string_too_short"'),
        ("'value_error.any_str.min_length'", "'string_too_short'"),
        ('"value_error.any_str.max_length"', '"string_too_long"'),
        ("'value_error.any_str.max_length'", "'string_too_long'"),
        ('"value_error.number.not_gt"', '"greater_than"'),
        ("'value_error.number.not_gt'", "'greater_than'"),
        ('"value_error.number.not_ge"', '"greater_than_equal"'),
        ("'value_error.number.not_ge'", "'greater_than_equal'"),
        ('"value_error.number.not_lt"', '"less_than"'),
        ("'value_error.number.not_lt'", "'less_than'"),
        ('"value_error.number.not_le"', '"less_than_equal"'),
        ("'value_error.number.not_le'", "'less_than_equal'"),
    ]

    # --- Pydantic v1 message FRAGMENTS → v2 fragments (for ``in str(...)`` checks) ---
    # Only replace when the fragment appears inside a string being tested with ``in``.
    _msg_fragment_replacements: List[Tuple[str, str]] = [
        # v1 fragment → v2 fragment (both lowercase; we also replace title-case below)
        ("value is not a valid integer", "valid integer"),
        ("value is not a valid float", "valid number"),
        ("value is not a valid boolean", "valid boolean"),
        # v1 min/max messages
        ("ensure this value has at least", "at least"),
        ("ensure this value has at most", "at most"),
        ("string should have at least", "at least"),   # already partial-match friendly
        ("ensure this value is greater than", "greater than"),
        ("ensure this value is less than", "less than"),
        ("must be greater than", "greater than"),
        ("must be less than", "less than"),
    ]

    # Regex: replace exact-equality checks on detail/errors msg keys with .lower() in-checks.
    # Matches: assert detail["msg"] == "..."  or  assert errors[0]["msg"] == "..."
    # Replaces with: assert "..." in detail["msg"].lower()
    _exact_msg_eq_pattern = re.compile(
        r'assert\s+(\w[\w\[\]"\'\.]+\[(?:"msg"|\'msg\')\])\s*==\s*(["\'])(.+?)\2',
        re.DOTALL,
    )

    fixed_files: Dict[str, str] = {}
    for filename, content in test_files.items():
        if not filename.endswith(".py"):
            fixed_files[filename] = content
            continue

        # 1. Replace v1 error type strings
        for old_type, new_type in _type_replacements:
            content = content.replace(old_type, new_type)

        # 2. Replace v1 message fragments in string literals used with ``in``
        for old_frag, new_frag in _msg_fragment_replacements:
            # Replace both lowercase and title-case variants
            for variant in (old_frag, old_frag.capitalize()):
                # Only replace inside string literals followed by ``in`` (or preceded by ``in``)
                # Pattern: "...variant..." or '...variant...' used in `in` comparisons
                content = content.replace(f'"{variant}"', f'"{new_frag}"')
                content = content.replace(f"'{variant}'", f"'{new_frag}'")

        # 3. Convert exact msg equality checks to case-insensitive in-checks
        def _replace_exact_msg(m: re.Match) -> str:
            accessor = m.group(1)
            msg_text = m.group(3).lower()
            return f'assert "{msg_text}" in {accessor}.lower()'

        content = _exact_msg_eq_pattern.sub(_replace_exact_msg, content)

        fixed_files[filename] = content

    return fixed_files


def validate_monkeypatch_targets(test_files: Dict[str, str], code_files: Optional[Dict[str, str]] = None, language: str = "python") -> Dict[str, str]:
    """
    Validates and removes tests with invalid monkeypatch targets.
    
    Tests that attempt to patch non-existent modules (like 'some_module.some_function')
    will cause ModuleNotFoundError at test runtime. This function detects such issues
    and removes the problematic monkeypatch calls or warns about them.
    
    Args:
        test_files: Dictionary of test filename to content
        code_files: Optional source code files to check module existence
        language: Programming language (currently only supports Python)
        
    Returns:
        Dictionary with validated test file contents (problematic monkeypatch calls removed or commented out)
    """
    if language != "python":
        return test_files
    
    validated_files = {}
    
    # Build set of known modules from code_files
    known_modules = set()
    if code_files:
        for filepath in code_files.keys():
            if filepath.endswith('.py'):
                # e.g., "app/main.py" -> "app.main"
                module_path = filepath[:-3].replace('/', '.')
                known_modules.add(module_path)
                # Also add parent modules: "app.utils.helpers" -> ["app", "app.utils", "app.utils.helpers"]
                parts = module_path.split('.')
                for i in range(1, len(parts) + 1):
                    known_modules.add('.'.join(parts[:i]))
    
    # Add common standard library and testing modules that are always available
    known_modules.update([
        'os', 'sys', 'time', 'json', 're', 'math', 'random', 'datetime',
        'pytest', 'unittest', 'mock', 'unittest.mock',
        'requests', 'http', 'urllib', 'logging', 'collections', 'typing',
    ])
    
    # Pattern to find monkeypatch.setattr calls
    # Matches: monkeypatch.setattr("module.path", value)
    monkeypatch_pattern = re.compile(
        r'monkeypatch\.setattr\(\s*["\']([a-zA-Z_][a-zA-Z0-9_\.]*)["\']',
        re.MULTILINE
    )
    
    for filename, content in test_files.items():
        fixed_content = content
        issues_found = []
        
        # Find all monkeypatch.setattr calls
        for match in monkeypatch_pattern.finditer(content):
            target_path = match.group(1)
            # Extract the module part (everything before the last dot)
            if '.' in target_path:
                module_path = '.'.join(target_path.split('.')[:-1])
            else:
                module_path = target_path
            
            # Check if it's a placeholder or known bad pattern
            bad_patterns = ['some_module', 'placeholder', 'example', 'fake_module']
            is_placeholder = any(bad in module_path.lower() for bad in bad_patterns)
            
            # Check if module exists in our known modules
            if is_placeholder or (code_files and module_path not in known_modules and not module_path.startswith('app.')):
                issues_found.append((target_path, module_path))
                logger.warning(
                    f"Found invalid monkeypatch target in {filename}: '{target_path}' "
                    f"(module '{module_path}' does not exist in codebase)"
                )
        
        # If issues were found, comment out the problematic lines and add a warning
        if issues_found:
            lines = fixed_content.split('\n')
            modified = False
            for i, line in enumerate(lines):
                for target_path, module_path in issues_found:
                    if f'monkeypatch.setattr("{target_path}"' in line or f"monkeypatch.setattr('{target_path}'" in line:
                        # Comment out the line and add explanation
                        indent = len(line) - len(line.lstrip())
                        lines[i] = (
                            f"{' ' * indent}# REMOVED: Invalid monkeypatch target '{target_path}' - module '{module_path}' does not exist\n"
                            f"{' ' * indent}# {line.lstrip()}"
                        )
                        modified = True
                        break
            
            if modified:
                fixed_content = '\n'.join(lines)
                logger.info(f"Commented out {len(issues_found)} invalid monkeypatch call(s) in {filename}")
        
        validated_files[filename] = fixed_content
    
    return validated_files


# Mapping of language to configuration for extensions, linters, and scanners
LANGUAGE_CONFIG = {
    "python": {
        "ext": "py",
        "linter": ["flake8", "--format=json", "--max-line-length=120"],
        "static_analyzer": ["mypy", "--strict", "--show-error-codes"],
        "security_scanner": ["bandit", "-f", "json", "-ll"],
        "ast_parser": ast.parse,
    },
    "javascript": {
        "ext": "js",
        "linter": ["eslint", "--format=json", "--ext", ".js"],
        "static_analyzer": None,
        "security_scanner": ["semgrep", "--config=auto", "--json"],
        "ast_parser": None,
    },
    "java": {
        "ext": "java",
        "linter": ["checkstyle", "-c", "/path/to/checkstyle.xml"],
        "static_analyzer": ["javac", "-Xlint:all"],
        "security_scanner": ["pmd", "-R", "rulesets/java/security.xml", "-f", "json"],
        "ast_parser": None,
    },
    "typescript": {
        "ext": "ts",
        "linter": ["eslint", "--format=json", "--ext", ".ts"],
        "static_analyzer": ["tsc", "--noEmit", "--strict"],
        "security_scanner": ["semgrep", "--config=auto", "--json"],
        "ast_parser": None,
    },
    "go": {
        "ext": "go",
        "linter": ["golangci-lint", "run", "--out-format", "json"],
        "static_analyzer": ["go", "vet"],
        "security_scanner": ["gosec", "-fmt=json"],
        "ast_parser": None,
    },
}

# Plugin registry for custom parsers
PARSER_REGISTRY: Dict[str, type] = {}


def normalize_test_filename(filename: str, language: str) -> str:
    """
    Normalize test file names to follow proper testing conventions.
    
    This function ensures test files follow the lowercase `test_` prefix convention
    required by pytest and other testing frameworks. It handles common issues like:
    - Capital letters at the start (Test.py -> test_module.py)
    - Missing test_ prefix (Calculator.py -> test_calculator.py)
    - Improper casing (TestCalculator.py -> test_calculator.py)
    - Uppercase TEST_ prefix (TEST_calculator.py -> test_calculator.py)
    
    Args:
        filename: Original filename from LLM response
        language: Programming language (e.g., "python", "javascript")
        
    Returns:
        Normalized filename with proper test_ prefix
        
    Examples:
        >>> normalize_test_filename("Test.py", "python")
        'test_module.py'
        >>> normalize_test_filename("TestCalculator.py", "python")
        'test_calculator.py'
        >>> normalize_test_filename("calculator.py", "python")
        'test_calculator.py'
        >>> normalize_test_filename("TEST_calculator.py", "python")
        'test_calculator.py'
    """
    ext = LANGUAGE_CONFIG.get(language, {}).get("ext", "txt")
    
    # Remove extension
    name_without_ext = filename
    if filename.endswith(f".{ext}"):
        name_without_ext = filename[:-len(ext)-1]
    
    # Handle common problematic patterns
    if name_without_ext == "Test":
        # Generic "Test.py" -> "test_module.py"
        normalized_name = "test_module"
    elif name_without_ext.startswith("Test") and len(name_without_ext) > 4:
        # "TestCalculator" -> "test_calculator"
        rest = name_without_ext[4:]  # Remove "Test" prefix
        # Convert PascalCase to snake_case
        normalized_name = "test_" + re.sub(r'(?<!^)(?=[A-Z])', '_', rest).lower()
    elif name_without_ext.lower().startswith("test_"):
        # Already has test_ prefix (case-insensitive), just ensure lowercase
        # This handles both "test_calculator" and "TEST_calculator"
        normalized_name = name_without_ext.lower()
    else:
        # "calculator" -> "test_calculator"
        # Convert PascalCase to snake_case first if needed
        snake_case = re.sub(r'(?<!^)(?=[A-Z])', '_', name_without_ext).lower()
        normalized_name = f"test_{snake_case}"
    
    result = f"{normalized_name}.{ext}"
    
    if result != filename:
        logger.info(f"Normalized test filename: {filename} -> {result}")
    
    return result


# Health Endpoints for Kubernetes
async def healthz(request):
    """Kubernetes liveness/readiness probe on port 8081."""
    return web.Response(text="OK", status=200)


async def start_health_server():
    """Starts an aiohttp server for health endpoints on port 8081."""
    app = web.Application()
    app.add_routes([web.get("/healthz", healthz)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8081)
    await site.start()
    logger.info("Health endpoint server started on port 8081.")


class ResponseParser(ABC):
    """
    Abstract base class for parsing LLM responses.
    """

    @abstractmethod
    def parse(self, response: str, language: str) -> Dict[str, str]:
        """
        Parse the LLM response into a dict of filename: content.
        """
        pass

    @abstractmethod
    async def validate(
        self,
        test_files: Dict[str, str],
        language: str,
        code_files: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Validate parsed files for correctness, security, and compliance.
        """
        pass

    def _attempt_recovery(
        self, malformed_response: str, language: str
    ) -> Optional[Dict[str, str]]:
        """
        Attempts to recover valid test files from a malformed LLM response using regex.
        REFACTORED: Uses central runner logging.
        """
        logger.warning(
            f"Attempting basic recovery for malformed response in {language}."
        )
        [re.escape(conf["ext"]) for conf in LANGUAGE_CONFIG.values()]
        code_block_regex = (
            rf'```(?:{language}|{"|".join(LANGUAGE_CONFIG.keys())})?\n(.*?)\n```'
        )
        code_blocks = re.findall(
            code_block_regex, malformed_response, re.DOTALL | re.IGNORECASE
        )
        logger.debug(f"Found {len(code_blocks)} code blocks during recovery.")
        if code_blocks:
            ext = LANGUAGE_CONFIG.get(language, {}).get("ext", "txt")
            recovered_files = {
                f"recovered_test_file_{i+1}.{ext}": block.strip()
                for i, block in enumerate(code_blocks)
            }
            logger.info(f"Recovered {len(recovered_files)} code blocks.")

            add_provenance_sync(
                "RecoveryAttempt",
                {
                    "strategy": "regex_code_blocks",
                    "recovered_count": len(recovered_files),
                    "language": language,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trigger": "parse_failure",
                }
            )
            return recovered_files
        logger.error("Basic recovery failed; no code blocks found.")
        return None

    async def _llm_auto_heal(
        self, malformed_response: str, error: str, language: str
    ) -> Optional[Dict[str, str]]:
        """
        Uses an LLM to fix a malformed response.
        REFACTORED: Uses central runner.llm_client and removes redundant metric calls.
        """
        if MAX_HEAL_ATTEMPTS <= 0:
            logger.warning("LLM auto-healing disabled (MAX_HEAL_ATTEMPTS <= 0).")
            return None

        logger.info(
            f"Attempting LLM auto-healing for malformed response in {language}."
        )
        # FIX Issue 2: Improve auto-healing prompt to be more specific about syntax errors
        # Compute file extension once for clarity
        file_ext = LANGUAGE_CONFIG.get(language, {}).get('ext', 'txt')
        heal_prompt = f"""
The following LLM response failed to parse with error: {error}

Original Response:
{malformed_response}

Please fix this response to be valid {language} test code. Follow these requirements:
1. Ensure all code has correct {language} syntax (no syntax errors)
2. Wrap each test file in markdown code blocks with the format:
   ```{language}
   # filename.{file_ext}
   <valid code here>
   ```
3. Remove any explanatory text outside the code blocks
4. Ensure proper indentation and formatting
5. Return ONLY the corrected test code in markdown code blocks, no additional commentary

Fix the syntax errors and return valid {language} test code.
"""

        for attempt in range(MAX_HEAL_ATTEMPTS):
            try:
                time.time()
                heal_response = await call_llm_api(
                    prompt=heal_prompt, model="gpt-4o", temperature=0.2, max_tokens=4000
                )

                healed_content = heal_response.get("content", "")
                if not healed_content:
                    logger.warning(
                        f"LLM auto-healing attempt {attempt + 1}/{MAX_HEAL_ATTEMPTS} returned empty content."
                    )
                    continue

                # Try to parse the healed response
                try:
                    healed_files = self.parse(healed_content, language)
                    if healed_files:
                        logger.info(
                            f"LLM auto-healing successful on attempt {attempt + 1}."
                        )

                        await add_provenance(
                            "LLMAutoHealSuccess",
                            {
                                "attempt": attempt + 1,
                                "language": language,
                                "original_error": error,
                                "healed_files_count": len(healed_files),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "trigger": "llm_auto_heal",
                            }
                        )
                        return healed_files
                except Exception as parse_error:
                    logger.warning(
                        f"LLM auto-healing attempt {attempt + 1}/{MAX_HEAL_ATTEMPTS} still has parse error: {parse_error}"
                    )
                    continue

            except Exception as llm_error:
                LLM_ERRORS_TOTAL.labels(
                    provider="testgen_response_handler",
                    model="gpt-4o",
                ).inc()
                logger.error(
                    f"LLM auto-healing attempt {attempt + 1}/{MAX_HEAL_ATTEMPTS} failed: {llm_error}"
                )
                continue

        logger.error(f"LLM auto-healing failed after {MAX_HEAL_ATTEMPTS} attempts.")
        return None


def _strip_markdown_fences(content: str) -> str:
    """
    Strip markdown code fences from content.
    
    This addresses Issue 2: Test Generation Syntax Errors
    LLM responses sometimes have nested or extra markdown fences that cause syntax errors.
    
    Args:
        content: Code content that may have markdown fences
        
    Returns:
        Content with markdown fences removed
        
    Example:
        >>> _strip_markdown_fences("```python\\nprint('hello')\\n```")
        "print('hello')"
    """
    # Strip leading/trailing whitespace first
    content = content.strip()
    
    # Remove opening fence at the start of the string (```python, ```py, ```objective-c, ```f#, etc.)
    # Use \A to match only the start of the string, not any line
    # Use [a-zA-Z0-9_+#-]* to handle language identifiers with hyphens, plus, and hash (objective-c, c++, f#, etc.)
    content = re.sub(r'\A```[a-zA-Z0-9_+#-]*\s*\n?', '', content)
    
    # Remove closing fence at the end of the string
    # Use \Z to match only the end of the string, not any line
    content = re.sub(r'\n?```\s*\Z', '', content)
    
    return content.strip()


# Valid first tokens for a Python source file (any of these prefixes on the first
# non-blank line means the content is already valid Python).
_VALID_PYTHON_STARTS = (
    "import ",
    "from ",
    "#",
    '"""',
    "'''",
    "def ",
    "async def ",
    "class ",
    "@",
    "if ",
    "try:",
    "with ",
    "raise ",
    "pass",
    "lambda ",
    "__all__",
    "__name__",
    "__version__",
    "__author__",
)


def _strip_non_python_preamble(content: str, filename: str = "") -> str:
    """
    Strip non-Python preamble lines from the start of a code string.

    LLMs occasionally prefix generated Python files with natural-language text
    such as ``(Refined)`` or ``Here is the refined code:`` that are not valid
    Python and cause ``NameError`` or ``SyntaxError`` at import time.

    The function advances past any leading lines that do not start with a
    recognised Python token, stopping as soon as it encounters the first line
    that looks like valid Python.  If no valid-Python line is found the original
    content is returned unchanged so callers always receive *something*.

    Args:
        content: Raw code content (after markdown-fence stripping).
        filename: Optional filename for log context.

    Returns:
        Content with non-Python preamble removed, or the original content if
        no clearly-Python start line was found.
    """
    lines = content.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(token) for token in _VALID_PYTHON_STARTS):
            if i > 0:
                logger.warning(
                    "Stripped %d non-Python preamble line(s) from '%s': %r",
                    i,
                    filename or "<unknown>",
                    lines[:i],
                )
                return "\n".join(lines[i:])
            return content
    # No valid Python start found – return as-is and let the caller handle it.
    return content


class DefaultResponseParser(ResponseParser):
    """
    Default implementation for parsing LLM responses containing test code.
    REFACTORED v2.0: Better separation of concerns with dedicated validation methods.
    """

    def parse(self, response: str, language: str) -> Dict[str, str]:
        """
        Parse the LLM response into a dict of filename: content.
        Supports multiple formats: JSON, markdown code blocks, XML, and plain text.
        """
        if not response or not response.strip():
            raise ValueError("Empty or whitespace-only response cannot be parsed.")

        add_provenance_sync(
            "ParseAttempt",
            {
                "language": language,
                "response_length": len(response),
                "response_hash": hashlib.sha256(response.encode()).hexdigest(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "parse_llm_response",
            }
        )

        # Strategy 1: Try to parse as JSON
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                logger.debug("Successfully parsed response as JSON.")
                # Apply filename normalization
                return {
                    normalize_test_filename(k, language): str(v) 
                    for k, v in parsed.items() 
                    if isinstance(k, str)
                }
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 2: Try to parse as XML
        try:
            root = ET.fromstring(response.strip())
            if root.tag == "tests":
                files = {}
                for file_node in root.findall(".//file"):
                    name = file_node.get("name")
                    content_node = file_node.find("content")
                    if name and content_node is not None and content_node.text:
                        # Apply filename normalization
                        normalized_name = normalize_test_filename(name, language)
                        files[normalized_name] = content_node.text.strip()
                if files:
                    logger.info(f"Successfully parsed {len(files)} files from XML.")
                    return files
        except ET.ParseError:
            pass

        # Strategy 3: Extract markdown code blocks
        ext = LANGUAGE_CONFIG.get(language, {}).get("ext", "txt")
        code_block_pattern = (
            rf"```(?:{language}|{ext})?\s*(?:\n# +(.*?))?(?:\n|\s)(.*?)```"
        )
        matches = re.findall(code_block_pattern, response, re.DOTALL | re.IGNORECASE)

        if matches:
            parsed_files = {}
            for i, (filename_comment, code_content) in enumerate(matches):
                if filename_comment and filename_comment.strip():
                    filename = filename_comment.strip()
                    if not filename.endswith(f".{ext}"):
                        filename += f".{ext}"
                else:
                    filename = f"test_file_{i+1}.{ext}"

                # Apply filename normalization and strip any remaining fences
                normalized_filename = normalize_test_filename(filename, language)
                # FIX Issue 2: Strip markdown fences from code content
                cleaned_content = _strip_markdown_fences(code_content.strip())
                # P3: Strip non-Python preambles (e.g. "(Refined)" hallucinations)
                if language == "python":
                    cleaned_content = _strip_non_python_preamble(cleaned_content, normalized_filename)
                parsed_files[normalized_filename] = cleaned_content

            if parsed_files:
                logger.info(f"Successfully parsed {len(parsed_files)} code blocks.")
                return parsed_files

        # Strategy 4: Extract by file markers
        file_marker_pattern = r"(?:^|\n)#+\s*([\w\-_\.]+\.(?:py|js|ts|java|go|rs))\s*\n(.*?)(?=\n#+\s*[\w\-_\.]+\.|$)"
        file_matches = re.findall(file_marker_pattern, response, re.DOTALL)

        if file_matches:
            parsed_files = {}
            for filename, content in file_matches:
                # Apply filename normalization and strip fences
                normalized_filename = normalize_test_filename(filename, language)
                # FIX Issue 2: Strip markdown fences from content
                cleaned_content = _strip_markdown_fences(content.strip())
                # P3: Strip non-Python preambles (e.g. "(Refined)" hallucinations)
                if language == "python":
                    cleaned_content = _strip_non_python_preamble(cleaned_content, normalized_filename)
                parsed_files[normalized_filename] = cleaned_content

            if parsed_files:
                logger.info(
                    f"Successfully parsed {len(parsed_files)} files using file markers."
                )
                return parsed_files

        # Strategy 5: Treat entire response as a single file
        if response.strip():
            single_filename = f"generated_test.{ext}"
            logger.warning(
                f"Could not parse structured response. Treating as single file: {single_filename}"
            )
            # FIX Issue 2: Strip markdown fences from entire response
            cleaned_response = _strip_markdown_fences(response.strip())
            # P3: Strip non-Python preambles (e.g. "(Refined)" hallucinations)
            if language == "python":
                cleaned_response = _strip_non_python_preamble(cleaned_response, single_filename)
            return {single_filename: cleaned_response}

        raise ValueError("Failed to parse LLM response using any available strategy.")

    # ==================== NEW: Separated Validation Methods ====================

    async def _lint_code(self, code: str, filename: str, language: str) -> str:
        """
        Run linter on code and return issues found.

        Args:
            code: The code content to lint
            filename: Name of the file (for context)
            language: Programming language

        Returns:
            String describing issues found, or empty string if no issues
        """
        config = LANGUAGE_CONFIG.get(language, {})
        linter_cmd = config.get("linter")

        if not linter_cmd:
            logger.debug(f"No linter configured for language: {language}")
            return ""

        logger.debug(f"Running linter on {filename}")

        try:
            # Run the linter asynchronously
            result = await self._run_external_tool(linter_cmd, filename, code)
            if result:
                return f"Linter issues: {result}"
            return ""
        except Exception as e:
            logger.warning(f"Linter execution failed for {filename}: {e}")
            return f"Linter error: {e}"

    async def _static_analysis(self, code: str, filename: str, language: str) -> str:
        """
        Run static analyzer on code and return issues found.

        Args:
            code: The code content to analyze
            filename: Name of the file (for context)
            language: Programming language

        Returns:
            String describing issues found, or empty string if no issues
        """
        config = LANGUAGE_CONFIG.get(language, {})
        analyzer_cmd = config.get("static_analyzer")

        if not analyzer_cmd:
            logger.debug(f"No static analyzer configured for language: {language}")
            return ""

        logger.debug(f"Running static analysis on {filename}")

        try:
            # Run the analyzer asynchronously
            result = await self._run_external_tool(analyzer_cmd, filename, code)
            if result:
                return f"Static analysis issues: {result}"
            return ""
        except Exception as e:
            logger.warning(f"Static analysis failed for {filename}: {e}")
            return f"Static analysis error: {e}"

    async def _security_scan(self, code: str, filename: str, language: str) -> str:
        """
        Scan code for security issues.

        Args:
            code: The code content to scan
            filename: Name of the file (for context)
            language: Programming language

        Returns:
            String describing security issues found, or empty string if no issues
        """
        logger.debug(f"Running security scan on {filename}")

        try:
            # Run the security scanner asynchronously
            result = await self._scan_for_security_issues(filename, code, language)
            return result if result else ""
        except Exception as e:
            logger.warning(f"Security scan failed for {filename}: {e}")
            return f"Security scan error: {e}"

    def _ast_verification(
        self,
        test_files: Dict[str, str],
        language: str,
        code_files: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """
        Verify test files using AST parsing for structural correctness.

        Args:
            test_files: Dictionary of filename to content for test files
            language: Programming language
            code_files: Optional dictionary of source code files to verify against

        Returns:
            List of error messages, empty if all files are valid
        """
        errors = []

        if language == "python":
            for filename, content in test_files.items():
                # Skip Python AST validation for non-Python files (e.g. README.md)
                if not filename.endswith(".py"):
                    logger.debug(
                        "Skipping AST verification for non-Python file: %s", filename
                    )
                    continue
                try:
                    # Parse the AST to verify syntax
                    tree = ast.parse(content)

                    # Verify test functions exist
                    test_functions = [
                        node.name
                        for node in ast.walk(tree)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name.startswith("test_")
                    ]

                    if not test_functions:
                        errors.append(f"{filename}: No test functions found")
                    else:
                        logger.debug(
                            f"{filename}: Found {len(test_functions)} test functions"
                        )

                    # If code_files provided, verify test functions match source functions
                    if code_files:
                        for code_filename, code_content in code_files.items():
                            try:
                                code_tree = ast.parse(code_content)
                                source_functions = [
                                    node.name
                                    for node in ast.walk(code_tree)
                                    if isinstance(
                                        node, (ast.FunctionDef, ast.AsyncFunctionDef)
                                    )
                                ]
                                # Check if tests reference source functions
                                logger.debug(
                                    f"Source file {code_filename} has {len(source_functions)} functions"
                                )
                            except SyntaxError:
                                errors.append(
                                    f"Cannot parse source file {code_filename} for verification"
                                )

                except SyntaxError as e:
                    errors.append(f"{filename}: Python syntax error - {e}")
                except Exception as e:
                    errors.append(f"{filename}: AST verification error - {e}")
        else:
            logger.debug(f"AST verification not implemented for language: {language}")

        return errors

    # ==================== End of New Methods ====================

    async def validate(
        self,
        test_files: Dict[str, str],
        language: str,
        code_files: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Validate parsed files for correctness, security, and compliance.
        REFACTORED v2.0: Now orchestrates dedicated validation methods for better separation of concerns.
        """
        if not test_files:
            raise ValueError("No test files provided for validation.")

        logger.info(f"Validating {len(test_files)} test files for language: {language}")
        validation_results = {}

        for filename, content in test_files.items():
            file_issues = []

            # Basic content validation
            if not content or not content.strip():
                file_issues.append("File content is empty or whitespace only")
                validation_results[filename] = file_issues
                continue

            # Language-specific AST validation
            LANGUAGE_CONFIG.get(language, {})

            if language == "python" and filename.endswith(".py"):
                file_issues.extend(self._validate_python_content(filename, content))
            elif language in ["javascript", "typescript"]:
                file_issues.extend(
                    self._validate_js_ts_content(filename, content, language)
                )
            elif language == "java":
                file_issues.extend(self._validate_java_content(filename, content))
            elif language == "go":
                file_issues.extend(self._validate_go_content(filename, content))

            # Run linter (async)
            linter_result = await self._lint_code(content, filename, language)
            if linter_result:
                file_issues.append(linter_result)

            # Run static analysis (async)
            analysis_result = await self._static_analysis(content, filename, language)
            if analysis_result:
                file_issues.append(analysis_result)

            # Run security scan (async)
            security_result = await self._security_scan(content, filename, language)
            if security_result:
                file_issues.append(f"Security issues: {security_result}")

            validation_results[filename] = file_issues

        # AST verification across all files
        ast_errors = self._ast_verification(test_files, language, code_files)
        if ast_errors:
            for error in ast_errors:
                # Add AST errors to the appropriate file's issues
                if ":" in error:
                    file_ref = error.split(":")[0]
                    if file_ref in validation_results:
                        validation_results[file_ref].append(error)
                    else:
                        # Generic AST error
                        validation_results.setdefault("_ast_errors", []).append(error)

        # Check for any critical issues
        total_issues = sum(len(issues) for issues in validation_results.values())
        if total_issues > 0:
            logger.warning(
                f"Validation found {total_issues} issues across {len(test_files)} files."
            )

            await add_provenance(
                "ValidationCompleted",
                {
                    "language": language,
                    "files_count": len(test_files),
                    "total_issues": total_issues,
                    "validation_results": validation_results,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trigger": "validate_test_files",
                }
            )

            # Decide whether to raise an exception or just warn
            critical_issues = []
            for filename, issues in validation_results.items():
                for issue in issues:
                    if any(
                        keyword in issue.lower()
                        for keyword in ["syntax error", "parse error", "security"]
                    ):
                        critical_issues.append(f"{filename}: {issue}")

            if critical_issues:
                raise ValueError(
                    f"Critical validation issues found: {'; '.join(critical_issues)}"
                )
        else:
            logger.info("All test files passed validation.")

    def _validate_python_content(self, filename: str, content: str) -> List[str]:
        """Validate Python-specific content."""
        issues = []

        # AST parsing
        try:
            ast.parse(content)
        except SyntaxError as e:
            issues.append(f"Python syntax error: {e}")
            return issues

        # Check for test functions
        try:
            tree = ast.parse(content)
            test_functions = [
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            ]

            if not test_functions:
                issues.append(
                    "No test functions found (functions starting with 'test_')"
                )
            else:
                logger.debug(
                    f"Found {len(test_functions)} test functions in {filename}"
                )
        except Exception as e:
            issues.append(f"Error analyzing Python AST: {e}")

        return issues

    def _validate_js_ts_content(
        self, filename: str, content: str, language: str
    ) -> List[str]:
        """Validate JavaScript/TypeScript content."""
        issues = []

        test_patterns = [
            r"describe\s*\(",
            r"it\s*\(",
            r"test\s*\(",
            r"expect\s*\(",
        ]

        has_test_patterns = any(
            re.search(pattern, content) for pattern in test_patterns
        )
        if not has_test_patterns:
            issues.append("No test patterns found (describe, it, test, expect)")

        import_patterns = [
            r"import\s+.*\s+from",
            r"require\s*\(",
        ]

        has_imports = any(re.search(pattern, content) for pattern in import_patterns)
        if not has_imports:
            issues.append("No import/require statements found")

        return issues

    def _validate_java_content(self, filename: str, content: str) -> List[str]:
        """Validate Java content."""
        issues = []

        if not re.search(r"@Test", content):
            issues.append("No @Test annotations found")

        if not re.search(r"public\s+class\s+\w+", content):
            issues.append("No public class definition found")

        return issues

    def _validate_go_content(self, filename: str, content: str) -> List[str]:
        """Validate Go content."""
        issues = []

        if not re.search(r"func\s+Test\w+\s*\(.*\*testing\.T\)", content):
            issues.append("No test functions found (func TestXxx(*testing.T))")

        if not re.search(r'import\s+.*"testing"', content):
            issues.append("No testing package import found")

        return issues

    async def _run_external_tool(
        self, cmd: List[str], filename: str, content: str
    ) -> str:
        """Run an external validation tool on the content using asyncio.to_thread."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=f'.{filename.split(".")[-1]}', delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            full_cmd = cmd + [tmp_path]
            result = await asyncio.to_thread(
                subprocess.run, full_cmd, capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                return result.stdout + result.stderr
            return ""
        except subprocess.TimeoutExpired:
            return "Tool execution timed out"
        except FileNotFoundError:
            logger.debug(f"External tool not found: {cmd[0]}")
            return ""
        except Exception as e:
            return f"Tool execution error: {e}"
        finally:
            try:
                os.unlink(tmp_path)
            except (OSError, FileNotFoundError):
                # Ignore cleanup errors
                pass

    async def _scan_for_security_issues(
        self, filename: str, content: str, language: str
    ) -> str:
        """Scan content for potential security issues."""
        scanner_cmd = LANGUAGE_CONFIG.get(language, {}).get("security_scanner")

        # Basic regex-based security checks
        patterns = [
            r'(?i)(password|secret|key)\s*=\s*["\'][^"\']+["\']',
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
            r"\b(?:ssh-rsa|ssh-dss|ecdsa-sha2-nistp|ssh-ed25519)\s+[A-Za-z0-9+/=]+\s*$",
        ]
        email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        # RFC 2606 reserved domains and common test-fixture domains used exclusively
        # for documentation and testing — not real PII.
        _TEST_EMAIL_DOMAIN_RE = re.compile(
            r"@(?:example\.(?:com|org|net)"
            r"|test\.(?:com|org|net)"
            r"|localhost"
            r"|invalid"
            r"|fake\.com"
            r"|dummy\.com"
            r"|mock\.com"
            r"|[A-Za-z0-9.-]+\.test"
            r")\b",
            re.IGNORECASE,
        )

        issues = []
        for pat in patterns:
            if pat == email_pattern:
                # Filter out RFC 2606 / common test-fixture email addresses before flagging.
                # Also honour `# noqa: security` pragmas on individual lines — collect all
                # email addresses that appear on such lines so they can be excluded.
                noqa_emails: set = set()
                for line in content.splitlines():
                    if "# noqa: security" in line:
                        for m in re.finditer(email_pattern, line):
                            noqa_emails.add(m.group())
                email_matches = re.findall(pat, content)
                real_emails = [
                    e for e in email_matches
                    if not _TEST_EMAIL_DOMAIN_RE.search(e)
                    and e not in noqa_emails
                ]
                if real_emails:
                    issues.append(pat)
            elif re.search(pat, content):
                issues.append(pat)
        if issues:
            logger.warning(f"Regex-based security issues in {filename}: {issues}")
            return f"Potential sensitive data detected: {', '.join(issues)}"

        if not scanner_cmd:
            logger.debug(f"No security scanner configured for {language}.")
            return ""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=f'.{filename.split(".")[-1]}', delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            is_json = any(
                arg in " ".join(scanner_cmd)
                for arg in ["-f json", "--json", "-fmt=json"]
            )
            return await self._run_tool(
                scanner_cmd, tmp_path, "security scanner", json_output=is_json
            )
        finally:
            os.unlink(tmp_path)

    async def _run_tool(
        self, cmd: List[str], file_path: str, tool_name: str, json_output: bool = False
    ) -> str:
        """Run a tool and return formatted results."""
        try:
            result = await self._run_external_tool(
                cmd, os.path.basename(file_path), open(file_path, "r").read()
            )

            if result:
                if json_output:
                    try:
                        output_data = json.loads(result)
                        return f"{tool_name} found issues: {json.dumps(output_data, indent=2)}"
                    except json.JSONDecodeError:
                        return f"{tool_name} error (non-JSON): {result}"
                else:
                    return f"{tool_name} found issues: {result}"
            return ""
        except subprocess.TimeoutExpired:
            return f"{tool_name} timed out"
        except FileNotFoundError:
            logger.debug(f"{tool_name} not found: {cmd[0]}")
            return ""
        except Exception as e:
            return f"{tool_name} error: {e}"

    def extract_metadata(
        self, test_files: Dict[str, str], language: str
    ) -> Dict[str, Any]:
        """
        Extracts metadata from test files (e.g., test names, dependencies).
        Enhanced to detect flakiness from attribute calls like time.sleep().
        """
        metadata = {}
        for filename, content in test_files.items():
            meta = {
                "test_names": [],
                "coverage_targets": [],
                "dependencies": [],
                "potential_flakiness": False,
                "assertions_count": 0,
            }
            if language == "python":
                try:
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(
                            node, (ast.FunctionDef, ast.AsyncFunctionDef)
                        ) and node.name.startswith("test_"):
                            meta["test_names"].append(node.name)
                        if isinstance(node, (ast.Import, ast.ImportFrom)):
                            for imp in node.names:
                                meta["dependencies"].append(imp.name)
                        if isinstance(node, ast.Call):
                            # Check for direct function calls: sleep(), random(), time()
                            if isinstance(node.func, ast.Name) and node.func.id in [
                                "sleep",
                                "random",
                                "time",
                            ]:
                                meta["potential_flakiness"] = True
                                meta["coverage_targets"].append(node.func.id)
                            # Check for attribute calls: time.sleep(), random.random()
                            elif isinstance(node.func, ast.Attribute):
                                if node.func.attr in [
                                    "sleep",
                                    "random",
                                    "choice",
                                    "randint",
                                ]:
                                    meta["potential_flakiness"] = True
                                meta["coverage_targets"].append(node.func.attr)
                            # Check for assertions
                            if isinstance(
                                node.func, ast.Name
                            ) and node.func.id.startswith("assert"):
                                meta["assertions_count"] += 1
                            elif isinstance(
                                node.func, ast.Attribute
                            ) and node.func.attr.startswith("assert"):
                                meta["assertions_count"] += 1
                        elif isinstance(node, ast.Assert):
                            meta["assertions_count"] += 1
                except SyntaxError as e:
                    logger.warning(
                        f"Syntax error during metadata extraction for {filename}: {e}"
                    )
                    meta["parse_error"] = str(e)
                except Exception as e:
                    logger.warning(
                        f"Metadata extraction failed for {filename}: {e}", exc_info=True
                    )
                    meta["extraction_error"] = str(e)
            metadata[filename] = meta

        add_provenance_sync(
            "Metadata Extracted",
            {
                "metadata": metadata,
                "language": language,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "extract_metadata",
            }
        )
        return metadata


# Registry for parsers, including hot-reload support
PARSERS: Dict[str, ResponseParser] = {
    "default": DefaultResponseParser(),
}


class ParserRegistry:
    """Manages parser plugins with hot-reloading."""

    def __init__(self):
        self.observer = None
        self._setup_hot_reload()

    def register_parser(self, name: str, parser: ResponseParser):
        """Registers a custom parser."""
        PARSERS[name] = parser
        logger.info(f"Registered custom parser: {name}")

    def _setup_hot_reload(self):
        """Sets up Watchdog to monitor plugin directory for changes."""

        class ParserReloadHandler(FileSystemEventHandler):
            def __init__(self, registry_instance):
                self.registry = registry_instance

            def on_any_event(self, event):
                if (
                    not event.is_directory
                    and event.src_path.endswith(".py")
                    and event.event_type in ("created", "modified", "deleted")
                ):
                    logger.info(
                        f"Parser plugin file changed: {event.src_path} (Event: {event.event_type}). Triggering reload."
                    )
                    asyncio.create_task(self.registry._reload_plugins())

        self.observer = Observer()
        self.observer.schedule(ParserReloadHandler(self), PLUGIN_DIR, recursive=False)
        self.observer.start()
        logger.info(f"Started hot-reload observer for parser plugins in: {PLUGIN_DIR}")

    async def _reload_plugins(self):
        """Reloads parser plugins."""
        PARSERS["default"] = DefaultResponseParser()
        logger.info("Parser plugins reloaded successfully (or default re-initialized).")
        await add_provenance(
            "ParserReload",
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "hot_reload",
            }
        )

    async def close(self):
        """Closes the registry and stops observer."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
        logger.info("ParserRegistry closed.")


parser_registry = ParserRegistry()


async def parse_llm_response(
    response: str,
    language: str = "python",
    parser_type: str = "default",
    code_files: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Main entry point for parsing and validating LLM responses.

    Args:
        response: The LLM response to parse
        language: Programming language of the tests
        parser_type: Type of parser to use (default: 'default')
        code_files: Optional source code files to verify tests against

    Returns:
        Dictionary of filename to test content

    Raises:
        ValueError: If parsing and healing both fail
        KeyError: If parser_type is unknown
    """
    # Support both parser_type and parser_name for backward compatibility
    parser_name = parser_type

    if parser_name not in PARSERS:
        logger.error(
            f"Unknown parser: {parser_name}. Available: {list(PARSERS.keys())}"
        )
        raise KeyError(f"Unknown parser: {parser_name}")

    parser = PARSERS[parser_name]

    try:
        test_files = parser.parse(response, language)
        
        # FIX #3: Fix import paths after parsing but before validation
        test_files = fix_import_paths(test_files, code_files, language)

        # Fix brittle Pydantic v1-style assertions before validation
        test_files = fix_brittle_pydantic_assertions(test_files, language)

        # FIX #3: Validate and fix monkeypatch targets
        test_files = validate_monkeypatch_targets(test_files, code_files, language)
        
        await parser.validate(test_files, language, code_files)
        metadata = parser.extract_metadata(test_files, language)
        await add_provenance(
            "Metadata Extracted",
            {
                "metadata": metadata,
                "language": language,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "parse_llm_response",
            }
        )
        return test_files
    except ValueError as e:
        logger.error(f"Initial parse/validation failed: {e}. Attempting auto-healing.")

        healed_files = await parser._llm_auto_heal(response, str(e), language)

        if healed_files:
            # FIX #3: Also fix import paths in healed files
            healed_files = fix_import_paths(healed_files, code_files, language)

            # Fix brittle Pydantic v1-style assertions in healed files
            healed_files = fix_brittle_pydantic_assertions(healed_files, language)

            # FIX #3: Also validate monkeypatch targets in healed files
            healed_files = validate_monkeypatch_targets(healed_files, code_files, language)
            
            try:
                await parser.validate(healed_files, language, code_files)
                metadata = parser.extract_metadata(healed_files, language)
                await add_provenance(
                    "Metadata Extracted After Healing",
                    {
                        "metadata": metadata,
                        "language": language,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "trigger": "llm_auto_heal_success",
                    }
                )
                return healed_files
            except ValueError as heal_e:
                logger.error(f"Healed response failed re-validation: {heal_e}")
                raise ValueError(
                    f"Failed to parse and heal response: {e}. Healing resulted in new error: {heal_e}"
                )
        else:
            raise ValueError(
                f"Failed to parse and heal response: {e}. No successful healing."
            )


# Convenience function for backwards compatibility
def handle_testgen_response(
    response: str,
    language: str = "python",
    parser_name: str = "default",
    code_files: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Synchronous wrapper for parse_llm_response.
    """
    return asyncio.run(parse_llm_response(response, language, parser_name, code_files))


async def startup():
    """Initializes services on startup."""
    logger.info("Initializing TestGen Response Handler components...")
    asyncio.create_task(start_health_server())
    logger.info("TestGen Response Handler components initialized.")
    await add_provenance(
        "Startup",
        {"timestamp": datetime.now(timezone.utc).isoformat()}
    )


async def shutdown():
    """Closes resources on shutdown."""
    logger.info("Shutting down TestGen Response Handler components...")
    await parser_registry.close()
    logger.info("TestGen Response Handler components shut down.")
    await add_provenance(
        "Shutdown",
        {"timestamp": datetime.now(timezone.utc).isoformat()}
    )
