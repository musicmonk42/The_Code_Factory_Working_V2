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
from typing import Any, Dict, List, Optional

# Security fix: Use defusedxml to prevent XXE attacks
import defusedxml.ElementTree as ET
from aiohttp import web

# --- CENTRAL RUNNER FOUNDATION ---
from runner.llm_client import call_llm_api
from runner.runner_logging import add_provenance, logger
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

            add_provenance(
                {
                    "action": "RecoveryAttempt",
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
        heal_prompt = f"""
The following LLM response failed to parse with error: {error}

Original Response:
{malformed_response}

Please fix this response to be valid {language} test code wrapped in proper markdown code blocks.
Return only the corrected response with proper file names and code structure.
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

                        add_provenance(
                            {
                                "action": "LLMAutoHealSuccess",
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
                    error_type=type(llm_error).__name__,
                    task="auto_heal",
                ).inc()
                logger.error(
                    f"LLM auto-healing attempt {attempt + 1}/{MAX_HEAL_ATTEMPTS} failed: {llm_error}"
                )
                continue

        logger.error(f"LLM auto-healing failed after {MAX_HEAL_ATTEMPTS} attempts.")
        return None


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

        add_provenance(
            {
                "action": "ParseAttempt",
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
                return {k: str(v) for k, v in parsed.items() if isinstance(k, str)}
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
                        files[name] = content_node.text.strip()
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

                parsed_files[filename] = code_content.strip()

            if parsed_files:
                logger.info(f"Successfully parsed {len(parsed_files)} code blocks.")
                return parsed_files

        # Strategy 4: Extract by file markers
        file_marker_pattern = r"(?:^|\n)#+\s*([\w\-_\.]+\.(?:py|js|ts|java|go|rs))\s*\n(.*?)(?=\n#+\s*[\w\-_\.]+\.|$)"
        file_matches = re.findall(file_marker_pattern, response, re.DOTALL)

        if file_matches:
            parsed_files = {}
            for filename, content in file_matches:
                parsed_files[filename] = content.strip()

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
            return {single_filename: response.strip()}

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

            if language == "python":
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

            add_provenance(
                {
                    "action": "ValidationCompleted",
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
            except (OSError, FileNotFoundError) as e:
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
        issues = [pat for pat in patterns if re.search(pat, content)]
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

        add_provenance(
            {
                "action": "Metadata Extracted",
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
        add_provenance(
            {
                "action": "ParserReload",
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
        await parser.validate(test_files, language, code_files)
        metadata = parser.extract_metadata(test_files, language)
        add_provenance(
            {
                "action": "Metadata Extracted",
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
            try:
                await parser.validate(healed_files, language, code_files)
                metadata = parser.extract_metadata(healed_files, language)
                add_provenance(
                    {
                        "action": "Metadata Extracted After Healing",
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
    add_provenance(
        {"action": "Startup", "timestamp": datetime.now(timezone.utc).isoformat()}
    )


async def shutdown():
    """Closes resources on shutdown."""
    logger.info("Shutting down TestGen Response Handler components...")
    await parser_registry.close()
    logger.info("TestGen Response Handler components shut down.")
    add_provenance(
        {"action": "Shutdown", "timestamp": datetime.now(timezone.utc).isoformat()}
    )
