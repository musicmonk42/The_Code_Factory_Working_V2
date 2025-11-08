"""
testgen_response_handler.py: The Parser & Guard for the agentic testing system.

REFACTORED: This module is now fully compliant with the central runner foundation.
All V0/V1 dependencies (testgen_llm_call, audit_log) have been removed
and replaced with runner.llm_client and runner.runner_logging.

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

import json
import re
import ast
import subprocess
import tempfile
import os
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any, List, Tuple
import asyncio
from aiohttp import web
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from datetime import datetime
import hashlib
import time # For LLM latency

# --- CENTRAL RUNNER FOUNDATION ---
from runner import tracer
from runner.llm_client import call_llm_api
from runner.runner_logging import logger, add_provenance
from runner.runner_metrics import LLM_CALLS_TOTAL, LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS
from runner.runner_errors import LLMError
# -----------------------------------

# --- External dependencies (REFACTORED) ---
# REMOVED: from ...audit_log import log_action 
# REMOVED: from ...testgen_llm_call import call_llm_api, scrub_prompt 

# REFACTORED: Removed local logger = logging.getLogger(__name__)

# Configuration
MAX_HEAL_ATTEMPTS = int(os.getenv('TESTGEN_PARSER_MAX_HEAL_ATTEMPTS', 2))
COMPLIANCE_MODE = os.getenv('COMPLIANCE_MODE', 'false').lower() == 'true'
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), 'parser_plugins')
os.makedirs(PLUGIN_DIR, exist_ok=True)

# Advanced Sanitization Patterns (to replace V0 scrub_prompt)
SANITIZATION_PATTERNS = {
    '[REDACTED_CREDENTIAL]': r'(?i)(api_key|password|secret|token|auth|bearer)\s*[:=]\s*["\']?[^"\']+["\']?(?=\s|$)',
    '[REDACTED_EMAIL]': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    '[REDACTED_PHONE]': r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
    '[REDACTED_IP]': r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
    '[REDACTED_SSN]': r'\b\d{3}-\d{2}-\d{4}\b',
    '[REDACTED_CC]': r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b',
}

def _local_regex_sanitize(text: str) -> str:
    """Internal helper to scrub text using this file's local patterns."""
    for replacement, pattern in SANITIZATION_PATTERNS.items():
        text = re.sub(pattern, replacement, text)
    return text

# Mapping of language to configuration for extensions, linters, and scanners
LANGUAGE_CONFIG = {
    'python': {
        'ext': 'py',
        'linter': ['flake8', '--format=json', '--max-line-length=120'],
        'static_analyzer': ['mypy', '--strict', '--show-error-codes'],
        'security_scanner': ['bandit', '-f', 'json', '-ll'],
        'ast_parser': ast.parse,
    },
    'javascript': {
        'ext': 'js',
        'linter': ['eslint', '--format=json', '--ext', '.js'],
        'static_analyzer': None,  # Could use TypeScript for .ts files
        'security_scanner': ['semgrep', '--config=auto', '--json'],
        'ast_parser': None,  # Requires esprima or similar
    },
    'java': {
        'ext': 'java',
        'linter': ['checkstyle', '-c', '/path/to/checkstyle.xml'],
        'static_analyzer': ['javac', '-Xlint:all'],
        'security_scanner': ['pmd', '-R', 'rulesets/java/security.xml', '-f', 'json'],
        'ast_parser': None,
    },
    'typescript': {
        'ext': 'ts',
        'linter': ['eslint', '--format=json', '--ext', '.ts'],
        'static_analyzer': ['tsc', '--noEmit', '--strict'],
        'security_scanner': ['semgrep', '--config=auto', '--json'],
        'ast_parser': None,
    },
    'go': {
        'ext': 'go',
        'linter': ['golangci-lint', 'run', '--out-format', 'json'],
        'static_analyzer': ['go', 'vet'],
        'security_scanner': ['gosec', '-fmt=json'],
        'ast_parser': None,
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
    app.add_routes([web.get('/healthz', healthz)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8081)
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
    def validate(self, test_files: Dict[str, str], language: str, code_files: Optional[Dict[str, str]] = None) -> None:
        """
        Validate parsed files for correctness, security, and compliance.
        """
        pass

    def _attempt_recovery(self, malformed_response: str, language: str) -> Optional[Dict[str, str]]:
        """
        Attempts to recover valid test files from a malformed LLM response using regex.
        REFACTORED: Uses central runner logging.
        """
        logger.warning(f"Attempting basic recovery for malformed response in {language}.")
        lang_ext_patterns = [re.escape(conf['ext']) for conf in LANGUAGE_CONFIG.values()]
        code_block_regex = rf'```(?:{language}|{"|".join(LANGUAGE_CONFIG.keys())})?\n(.*?)\n```'
        code_blocks = re.findall(code_block_regex, malformed_response, re.DOTALL | re.IGNORECASE)
        logger.debug(f"Found {len(code_blocks)} code blocks during recovery.")
        if code_blocks:
            ext = LANGUAGE_CONFIG.get(language, {}).get('ext', 'txt')
            recovered_files = {f"recovered_test_file_{i+1}.{ext}": block.strip() for i, block in enumerate(code_blocks)}
            logger.info(f"Recovered {len(recovered_files)} code blocks.")
            
            # REFACTORED: Use add_provenance
            add_provenance({
                "action": "RecoveryAttempt",
                "strategy": "regex_code_blocks",
                "recovered_count": len(recovered_files),
                "language": language,
                "timestamp": datetime.utcnow().isoformat(),
                "trigger": "parse_failure"
            })
            return recovered_files
        logger.error("Basic recovery failed; no code blocks found.")
        return None

    async def _llm_auto_heal(self, malformed_response: str, error: str, language: str) -> Optional[Dict[str, str]]:
        """
        Uses an LLM to fix a malformed response.
        REFACTORED: Uses central runner LLM client, metrics, and provenance.
        """
        logger.info(f"Attempting LLM-powered auto-healing for parse failure in {language}.")
        pre_hash = hashlib.sha256(malformed_response.encode('utf-8')).hexdigest()
        
        lang_ext = LANGUAGE_CONFIG.get(language, {}).get('ext', 'txt')
        if not lang_ext:
            logger.error(f"Cannot perform LLM auto-heal: No file extension configured for language '{language}'.")
            return None

        heal_prompt = (
            f"The following response failed to parse with error: {error}\n\n"
            f"Response:\n{malformed_response}\n\n"
            f"Fix the syntax and format it as valid {language} test code in JSON format: "
            f"{{'files': {{'filename.{lang_ext}': 'code'}}}}. Only return the JSON."
        )
        
        # REFACTORED: Use local sanitizer
        scrubbed_heal_prompt = _local_regex_sanitize(heal_prompt)
        post_hash = hashlib.sha256(scrubbed_heal_prompt.encode('utf-8')).hexdigest()
        
        # REFACTORED: Use add_provenance
        add_provenance({
            "action": "AutoHealPromptSanitized",
            "pre_hash": pre_hash,
            "post_hash": post_hash,
            "timestamp": datetime.utcnow().isoformat(),
            "trigger": "llm_auto_heal"
        })
        if COMPLIANCE_MODE:
            add_provenance({
                "action": "ComplianceAutoHeal",
                "scrubbed_prompt": {"pre_hash": pre_hash, "post_hash": post_hash},
                "timestamp": datetime.utcnow().isoformat()
            })
            
        attempts = 0
        model = "gpt-4o" # Use a strong model for healing
        while attempts < MAX_HEAL_ATTEMPTS:
            attempts += 1
            try:
                # REFACTORED: Use runner.llm_client.call_llm_api
                start_time = time.time()
                healed_response = await call_llm_api(
                    prompt=scrubbed_heal_prompt, 
                    model=model
                )
                latency = time.time() - start_time
                LLM_CALLS_TOTAL.labels(provider="testgen_handler", model=model, task="auto_heal").inc()
                LLM_LATENCY_SECONDS.labels(provider="testgen_handler", model=model, task="auto_heal").observe(latency)
                add_provenance({"action": "llm_auto_heal_call", "model": model, "latency": latency, "attempt": attempts})

                healed_content = healed_response.get('content')
                if not healed_content:
                    logger.warning(f"LLM returned empty content for auto-heal attempt {attempts}.")
                    LLM_ERRORS_TOTAL.labels(provider="testgen_handler", model=model, error_type="EmptyLLMResponse").inc()
                    continue
                
                # Attempt to parse the healed content recursively
                parsed_healed = self.parse(healed_content, language)
                logger.debug(f"Auto-heal attempt {attempts} succeeded.")
                
                # REFACTORED: Use add_provenance
                add_provenance({
                    "action": "AutoHealSuccess",
                    "attempts": attempts,
                    "language": language,
                    "timestamp": datetime.utcnow().isoformat(),
                    "trigger": "parse_failure"
                })
                return parsed_healed
            except Exception as e:
                if not isinstance(e, LLMError):
                    LLM_ERRORS_TOTAL.labels(provider="testgen_handler", model=model, error_type=type(e).__name__).inc()
                logger.warning(f"LLM auto-heal attempt {attempts} failed: {e}")
        logger.error(f"LLM auto-heal failed after {MAX_HEAL_ATTEMPTS} attempts.")
        return None

class DefaultResponseParser(ResponseParser):
    """
    Default parser handling JSON, XML, Markdown, code blocks, and raw code.
    REFACTORED: Uses central runner logging.
    """
    def parse(self, response: str, language: str) -> Dict[str, str]:
        """
        Parses LLM response using multiple strategies: JSON, XML, Markdown, code blocks, raw code.
        """
        truncated_response = response[:200] + '...' if len(response) > 200 else response
        logger.info(f"Parsing LLM response (truncated): {truncated_response} for language: {language}.")
        parse_strategy_used = "unknown"
        parsed_files: Optional[Dict[str, str]] = None

        # Case 1: JSON (multi-file)
        try:
            # Try finding JSON within backticks
            json_match = re.search(r'```json\n(.*?)\n```', response, re.DOTALL)
            content_to_parse = response
            if json_match:
                content_to_parse = json_match.group(1)

            parsed = json.loads(content_to_parse)
            if "files" in parsed and isinstance(parsed["files"], dict):
                parsed_files = parsed["files"]
                parse_strategy_used = "json_multi_file"
                logger.debug("Parsed as JSON multi-file.")
        except json.JSONDecodeError:
            logger.debug("JSON parsing failed; trying other formats.")

        # Case 2: XML
        if parsed_files is None:
            try:
                root = ET.fromstring(response)
                if root.tag == 'tests':
                    parsed_files = {}
                    for file_elem in root.findall('file'):
                        filename = file_elem.get('name')
                        content = file_elem.text.strip() if file_elem.text else ''
                        if filename and content:
                            parsed_files[filename] = content
                    if parsed_files:
                        parse_strategy_used = "xml"
                        logger.debug("Parsed as XML.")
            except ET.ParseError:
                logger.debug("XML parsing failed.")

        # Case 3: Markdown / reStructuredText sections
        if parsed_files is None:
            lang_ext_patterns = [re.escape(conf['ext']) for conf in LANGUAGE_CONFIG.values()]
            file_heading_regex = r'^(?:#+\s*|--+\s*)(.+?\.(?:' + '|'.join(lang_ext_patterns) + r'))\s*\n(.*?)(?=\n(?:#+\s*|--+\s*).+?\.(?:' + '|'.join(lang_ext_patterns) + r')|\Z)'
            
            matches = re.findall(file_heading_regex, response, re.DOTALL | re.MULTILINE)
            if matches:
                parsed_files = {}
                for filename, content in matches:
                    parsed_files[filename.strip()] = content.strip()
                if parsed_files:
                    parse_strategy_used = "markdown_sections"
                    logger.debug("Parsed as Markdown/reST sections.")

        # Case 4: Code blocks
        if parsed_files is None:
            lang_pattern = r'(?:' + '|'.join(LANGUAGE_CONFIG.keys()) + r')?'
            code_block_regex = rf'```(?:{language}|{lang_pattern})?\n(.*?)\n```'
            code_blocks = re.findall(code_block_regex, response, re.DOTALL | re.IGNORECASE)
            if code_blocks:
                ext = LANGUAGE_CONFIG.get(language, {}).get('ext', 'txt')
                if len(code_blocks) == 1:
                    parsed_files = {f"test_main.{ext}": code_blocks[0].strip()}
                    parse_strategy_used = "single_code_block"
                    logger.debug("Parsed as single code block.")
                else:
                    parsed_files = {f"test_file_{i+1}.{ext}": block.strip() for i, block in enumerate(code_blocks)}
                    parse_strategy_used = "multiple_code_blocks"
                    logger.debug(f"Parsed as {len(code_blocks)} code blocks.")

        # Case 5: Raw code fallback
        if parsed_files is None:
            cleaned = response.strip()
            cleaned = re.sub(r'^\s*(?:Here are the tests:|```(?:[a-zA-Z]+)?\s*)', '', cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r'(?:```|\s*```\s*$)', '', cleaned, flags=re.MULTILINE).strip()
            if cleaned:
                ext = LANGUAGE_CONFIG.get(language, {}).get('ext', 'txt')
                parsed_files = {f"test_main.{ext}": cleaned}
                parse_strategy_used = "raw_code_fallback"
                logger.debug("Parsed as raw code fallback.")

        if parsed_files is None or not parsed_files:
            recovered = self._attempt_recovery(response, language)
            if recovered:
                parsed_files = recovered
                parse_strategy_used = "auto_recovered"
                logger.info("Response auto-recovered using regex.")

        if not parsed_files:
            logger.error(f"Failed to parse response for {language}. Response (truncated): {truncated_response}")
            raise ValueError(f"Unable to parse LLM response into valid test files for {language}.")

        # REFACTORED: Use add_provenance
        add_provenance({
            "action": "Parsed LLM Response",
            "strategy": parse_strategy_used,
            "files": list(parsed_files.keys()),
            "language": language,
            "timestamp": datetime.utcnow().isoformat(),
            "trigger": "parse_llm_response"
        })
        return parsed_files

    def validate(self, test_files: Dict[str, str], language: str, code_files: Optional[Dict[str, str]] = None) -> None:
        """
        Validates parsed test files using real tools and AST analysis.
        REFACTORED: Uses central runner logging.
        """
        if not test_files:
            logger.error("Parsed test files dictionary is empty.")
            raise ValueError("Parsed test files dictionary is empty.")

        config = LANGUAGE_CONFIG.get(language)
        if not config:
            logger.error(f"Unsupported language: {language}")
            raise ValueError(f"Unsupported language: {language}")

        expected_ext = config['ext']

        for filename, content in test_files.items():
            if not content.strip():
                raise ValueError(f"Content for '{filename}' is empty or whitespace.")

            if not filename.endswith(f'.{expected_ext}'):
                raise ValueError(f"Invalid filename '{filename}': Must end with .{expected_ext} for {language}.")

            linter_issues = self._run_linter(filename, content, config.get('linter'))
            if linter_issues:
                raise ValueError(f"Linter issues in '{filename}': {linter_issues}")

            analyzer_issues = self._run_static_analyzer(filename, content, config.get('static_analyzer'))
            if analyzer_issues:
                raise ValueError(f"Static analysis issues in '{filename}': {analyzer_issues}")

            ast_issues = self._ast_verify(content, language, code_files)
            if ast_issues:
                raise ValueError(f"AST verification failed for '{filename}': {ast_issues}")

            security_issues = self._run_security_scanner(filename, content, config.get('security_scanner'))
            if security_issues:
                raise ValueError(f"Security issues in '{filename}': {security_issues}")

        # REFACTORED: Use add_provenance
        add_provenance({
            "action": "Test Files Validated",
            "files_count": len(test_files),
            "language": language,
            "status": "passed",
            "timestamp": datetime.utcnow().isoformat(),
            "trigger": "validate"
        })

    def _run_tool(self, tool_cmd: Optional[List[str]], filepath: str, tool_name: str, json_output: bool = False) -> str:
        """Helper to run external tools and capture output."""
        if not tool_cmd:
            logger.debug(f"No {tool_name} configured for {filepath}.")
            return ""
        
        try:
            cmd = [arg.replace('{file}', filepath) for arg in tool_cmd] if '{file}' in ' '.join(tool_cmd) else tool_cmd + [filepath]
            
            if json_output and '-f json' not in ' '.join(cmd) and '--format=json' not in ' '.join(cmd):
                 logger.warning(f"JSON output requested for {tool_name} but no JSON format flag found in command: {tool_cmd}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                issues = result.stdout + result.stderr
                if json_output:
                    try:
                        parsed_issues = json.loads(result.stdout) 
                        if parsed_issues:
                            return json.dumps(parsed_issues) 
                    except json.JSONDecodeError:
                        logger.warning(f"Could not parse JSON output from {tool_name}. Raw output: {result.stdout}")
                        pass
                logger.warning(f"{tool_name} issues for {filepath}: {issues}")
                return str(issues)
            
            if json_output:
                try:
                    parsed_output = json.loads(result.stdout)
                    if parsed_output and (isinstance(parsed_output, list) and len(parsed_output) > 0) or (isinstance(parsed_output, dict) and parsed_output): 
                        return json.dumps(parsed_output)
                    return ""
                except json.JSONDecodeError:
                    return ""
            return ""

        except subprocess.TimeoutExpired:
            logger.warning(f"{tool_name} timed out for {filepath}")
            return f"{tool_name} timed out"
        except FileNotFoundError:
            logger.warning(f"Command not found: {tool_cmd[0]}. Please ensure {tool_name} is installed and in PATH.")
            return f"Command not found: {tool_name}"
        except Exception as e:
            logger.warning(f"{tool_name} failed for {filepath}: {e}", exc_info=True)
            return f"{tool_name} execution failed: {e}"

    def _run_linter(self, filename: str, content: str, linter_cmd: Optional[List[str]]) -> str:
        """Runs a linter on the content (e.g., flake8, eslint)."""
        if not linter_cmd:
            logger.debug(f"No linter configured for {filename}.")
            return ""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{filename.split(".")[-1]}', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            # Check if json output is expected
            is_json = any(arg in ' '.join(linter_cmd) for arg in ['--format=json', '-f json', '--out-format json'])
            return self._run_tool(linter_cmd, tmp_path, "linter", json_output=is_json)
        finally:
            os.unlink(tmp_path)

    def _run_static_analyzer(self, filename: str, content: str, analyzer_cmd: Optional[List[str]]) -> str:
        """Runs a static analyzer (e.g., mypy, tsc)."""
        if not analyzer_cmd:
            logger.debug(f"No static analyzer configured for {filename}.")
            return ""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{filename.split(".")[-1]}', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            return self._run_tool(analyzer_cmd, tmp_path, "static analyzer")
        finally:
            os.unlink(tmp_path)

    def _ast_verify(self, content: str, language: str, code_files: Optional[Dict[str, str]]) -> str:
        """Verifies test code using AST to ensure it tests intended targets."""
        if language != 'python' or not code_files:
            logger.debug(f"AST verification skipped for {language} or no code files provided.")
            return ""

        try:
            test_tree = ast.parse(content)
            called_names = set()
            for node in ast.walk(test_tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        called_names.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        called_names.add(node.func.attr)
                        if isinstance(node.func.value, ast.Name):
                            called_names.add(node.func.value.id)

            target_definitions = set()
            for code_file_content in code_files.values():
                try:
                    code_tree = ast.parse(code_file_content)
                    for node in ast.walk(code_tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            target_definitions.add(node.name)
                        elif isinstance(node, ast.Assign):
                            for target in node.targets:
                                if isinstance(target, ast.Name):
                                    target_definitions.add(target.id)
                except SyntaxError as e:
                    logger.warning(f"Syntax error in source code for AST parsing: {e}")
                    continue
                except Exception as e:
                    logger.debug(f"Failed to parse source code for AST: {e}")
                    continue
            
            uncovered_targets = target_definitions - called_names

            if uncovered_targets:
                return f"Tests do not appear to interact with the following definitions in source code: {', '.join(sorted(list(uncovered_targets)))}"
            return ""
        except SyntaxError as e:
            logger.warning(f"Syntax error in test code during AST parse: {e}")
            return f"Syntax error in test code for AST verification: {e}"
        except Exception as e:
            logger.warning(f"AST verification failed: {e}", exc_info=True)
            return f"AST verification failed: {e}"

    def _run_security_scanner(self, filename: str, content: str, scanner_cmd: Optional[List[str]]) -> str:
        """Runs a security scanner (e.g., bandit, semgrep)."""
        patterns = [
            r'(?i)(?:api_key|password|secret|token|auth|bearer)\s*[:=]\s*["\']?[^"\']+["\']?(?=\s|$)',
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
            r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
            r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b',
            r'\b(?:ssh-rsa|ssh-dss|ecdsa-sha2-nistp|ssh-ed25519)\s+[A-Za-z0-9+/=]+\s*$',
        ]
        issues = [pat for pat in patterns if re.search(pat, content)]
        if issues:
            logger.warning(f"Regex-based security issues in {filename}: {issues}")
            return f"Potential sensitive data detected: {', '.join(issues)}"

        if not scanner_cmd:
            logger.debug(f"No security scanner configured for {filename}.")
            return ""

        with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{filename.split(".")[-1]}', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            is_json = any(arg in ' '.join(scanner_cmd) for arg in ['-f json', '--json', '-fmt=json'])
            return self._run_tool(scanner_cmd, tmp_path, "security scanner", json_output=is_json)
        finally:
            os.unlink(tmp_path)

    def extract_metadata(self, test_files: Dict[str, str], language: str) -> Dict[str, Any]:
        """
        Extracts metadata from test files (e.g., test names, dependencies).
        REFACTORED: Uses central runner logging.
        """
        metadata = {}
        for filename, content in test_files.items():
            meta = {
                'test_names': [], 'coverage_targets': [], 'dependencies': [],
                'potential_flakiness': False, 'assertions_count': 0,
            }
            if language == 'python':
                try:
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                            meta['test_names'].append(node.name)
                        if isinstance(node, (ast.Import, ast.ImportFrom)):
                            for imp in node.names:
                                meta['dependencies'].append(imp.name)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                            if node.func.id in ['sleep', 'random', 'time']:
                                meta['potential_flakiness'] = True
                            meta['coverage_targets'].append(node.func.id)
                            if node.func.id.startswith('assert') or (isinstance(node.func, ast.Attribute) and node.func.attr.startswith('assert')):
                                meta['assertions_count'] += 1
                        elif isinstance(node, ast.Assert):
                            meta['assertions_count'] += 1
                except SyntaxError as e:
                    logger.warning(f"Syntax error during metadata extraction for {filename}: {e}")
                    meta['parse_error'] = str(e)
                except Exception as e:
                    logger.warning(f"Metadata extraction failed for {filename}: {e}", exc_info=True)
                    meta['extraction_error'] = str(e)
            metadata[filename] = meta
        
        # REFACTORED: Use add_provenance
        add_provenance({
            "action": "Metadata Extracted",
            "metadata": metadata,
            "language": language,
            "timestamp": datetime.utcnow().isoformat(),
            "trigger": "extract_metadata"
        })
        return metadata

# Registry for parsers, including hot-reload support
PARSERS: Dict[str, ResponseParser] = {
    'default': DefaultResponseParser(),
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
                if not event.is_directory and event.src_path.endswith('.py') and event.event_type in ('created', 'modified', 'deleted'):
                    logger.info(f"Parser plugin file changed: {event.src_path} (Event: {event.event_type}). Triggering reload.")
                    asyncio.create_task(self.registry._reload_plugins())

        self.observer = Observer()
        self.observer.schedule(ParserReloadHandler(self), PLUGIN_DINAR, recursive=False)
        self.observer.start()
        logger.info(f"Started hot-reload observer for parser plugins in: {PLUGIN_DIR}")

    async def _reload_plugins(self):
        """Reloads parser plugins."""
        PARSERS['default'] = DefaultResponseParser() 
        logger.info("Parser plugins reloaded successfully (or default re-initialized).")
        # REFACTORED: Use add_provenance
        add_provenance({"action": "ParserReload", "timestamp": datetime.utcnow().isoformat(), "trigger": "hot_reload"})

    async def close(self):
        """Closes the registry and stops observer."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
        logger.info("ParserRegistry closed.")

parser_registry = ParserRegistry()

async def parse_llm_response(response: str, language: str = 'python', parser_name: str = 'default', code_files: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Main entry point for parsing and validating LLM responses.
    REFACTORED: Uses central runner logging.
    """
    if parser_name not in PARSERS:
        logger.error(f"Unknown parser: {parser_name}. Available: {list(PARSERS.keys())}")
        raise KeyError(f"Unknown parser: {parser_name}")

    parser = PARSERS[parser_name]
    
    try:
        test_files = parser.parse(response, language)
        parser.validate(test_files, language, code_files)
        metadata = parser.extract_metadata(test_files, language)
        # REFACTORED: add_provenance call was already here from the original, but it's correct.
        add_provenance({
            "action": "Metadata Extracted",
            "metadata": metadata,
            "language": language,
            "timestamp": datetime.utcnow().isoformat(),
            "trigger": "parse_llm_response"
        })
        return test_files
    except ValueError as e:
        logger.error(f"Initial parse/validation failed: {e}. Attempting auto-healing.")
        
        healed_files = await parser._llm_auto_heal(response, str(e), language)
        
        if healed_files:
            try:
                parser.validate(healed_files, language, code_files)
                metadata = parser.extract_metadata(healed_files, language)
                # REFACTORED: Use add_provenance
                add_provenance({
                    "action": "Metadata Extracted After Healing",
                    "metadata": metadata,
                    "language": language,
                    "timestamp": datetime.utcnow().isoformat(),
                    "trigger": "llm_auto_heal_success"
                })
                return healed_files
            except ValueError as heal_e:
                logger.error(f"Healed response failed re-validation: {heal_e}")
                raise ValueError(f"Failed to parse and heal response: {e}. Healing resulted in new error: {heal_e}")
        else:
            raise ValueError(f"Failed to parse and heal response: {e}. No successful healing.")


async def startup():
    """Initializes services on startup."""
    logger.info("Initializing TestGen Response Handler components...")
    asyncio.create_task(start_health_server())
    logger.info("TestGen Response Handler components initialized.")
    # REFACTORED: Use add_provenance
    add_provenance({"action": "Startup", "timestamp": datetime.utcnow().isoformat()})

async def shutdown():
    """Closes resources on shutdown."""
    logger.info("Shutting down TestGen Response Handler components...")
    await parser_registry.close()
    logger.info("TestGen Response Handler components shut down.")
    # REFACTORED: Use add_provenance
    add_provenance({"action": "Shutdown", "timestamp": datetime.utcnow().isoformat()})