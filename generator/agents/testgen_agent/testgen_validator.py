# testgen_validator/testgen_validator.py
"""
testgen_validator.py: Validates generated tests for the agentic testing system.

REFACTORED: This module is now fully compliant with the central runner foundation.
All V0/V1 dependencies (testgen_llm_call, audit_log, utils) have been removed
and replaced with runner.llm_client, runner.runner_logging, and runner.runner_metrics.

Features:
- Multi-strategy validation (coverage, mutation, property-based, stress/performance).
- Secure sandbox execution with resource limits (via runner.run_tests_in_sandbox).
- Secret and flakiness scanning with audit logging (via runner.add_provenance).
- Hot-reloading of validator plugins.
- Health endpoints for Kubernetes (port 8082).
- Historical performance data for analytics.
- Compliance mode for SOC2/PCI DSS.

Dependencies:
- asyncio, subprocess, shutil, tempfile, os, re, aiofiles
- runner (run_tests_in_sandbox, run_stress_tests, logging, metrics, llm_client)
- External tools: coverage.py, mutmut, hypothesis (Python); Stryker, fast-check (JS/TS); etc.
- Environment variables: TESTGEN_VALIDATOR_MAX_SANDBOX_RUNS, TESTGEN_MAX_PROMPT_TOKENS, COMPLIANCE_MODE
"""

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List, Union, Callable, Awaitable
from datetime import datetime
import json
import hashlib
import uuid # For unique module names during hot reload
from aiohttp import web
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
import importlib.util
import sys
from pathlib import Path
import aiofiles # ADDED: For async file operations

# --- CENTRAL RUNNER FOUNDATION ---
from runner import tracer, run_tests_in_sandbox, run_stress_tests
from runner.llm_client import call_llm_api
from runner.runner_logging import logger, add_provenance
from runner.runner_metrics import LLM_CALLS_TOTAL, LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS
from runner.runner_errors import LLMError
# -----------------------------------

# --- External dependencies (REFACTORED) ---
# REMOVED: from ...audit_log import log_action
# REMOVED: from ...runner import run_tests_in_sandbox, run_stress_tests
# REMOVED: from ...utils import save_files_to_output
# REMOVED: from ...testgen_llm_call import call_llm_api, scrub_prompt, TokenizerService

# REFACTORED: Removed local logger = logging.getLogger(__name__)

# Configuration
MAX_SANDBOX_RUNS = int(os.getenv('TESTGEN_VALIDATOR_MAX_SANDBOX_RUNS', 5))
MAX_PROMPT_TOKENS = int(os.getenv('TESTGEN_MAX_PROMPT_TOKENS', 16000))
COMPLIANCE_MODE = os.getenv('COMPLIANCE_MODE', 'false').lower() == 'true'
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), 'validator_plugins')
PERFORMANCE_DB_PATH = "validator_performance.json"
os.makedirs(PLUGIN_DIR, exist_ok=True)

# Registry for validators (populated by ValidatorRegistry)
VALIDATORS: Dict[str, 'TestValidator'] = {}

# REFACTORED: Helper to replace utils.save_files_to_output
async def _save_files_async(files: Dict[str, str], base_path: str):
    """Helper to asynchronously write files to a directory."""
    os.makedirs(base_path, exist_ok=True)
    tasks = []
    for filename, content in files.items():
        # Ensure filename is relative and safe
        safe_filename = os.path.normpath(os.path.join(base_path, filename))
        if not safe_filename.startswith(os.path.abspath(base_path)):
            logger.error(f"Attempted file write outside of base path: {filename}")
            continue
            
        file_path = Path(safe_filename)
        # Ensure subdirectory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async def write_file(path, data):
            async with aiofiles.open(path, 'w', encoding='utf-8') as f:
                await f.write(data)
        
        tasks.append(write_file(file_path, content))
    
    await asyncio.gather(*tasks)
    logger.debug(f"Asynchronously saved {len(tasks)} files to {base_path}")


# Health Endpoints for Kubernetes
async def healthz(request):
    """Kubernetes liveness/readiness probe on port 8082."""
    return web.Response(text="OK", status=200)

async def start_health_server():
    """Starts an aiohttp server for health endpoints on port 8082."""
    app = web.Application()
    app.add_routes([web.get('/healthz', healthz)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8082)
    await site.start()
    logger.info("Health endpoint server started on port 8082.")
    # REFACTORED: Use add_provenance
    add_provenance({"action": "HealthServerStarted", "port": 8082, "timestamp": datetime.utcnow().isoformat()})

class ValidatorRegistry:
    """
    Manages validator plugins with hot-reloading.
    REFACTORED: Uses central runner logging.
    """
    def __init__(self):
        self.observer = None
        self._setup_hot_reload()

    def register_validator(self, name: str, validator: 'TestValidator'):
        """Registers a custom validator."""
        if not isinstance(validator, TestValidator):
            raise ValueError(f"Validator {name} must be an instance of TestValidator")
        VALIDATORS[name] = validator
        logger.info(f"Registered validator: {name}")
        # REFACTORED: Use add_provenance
        add_provenance({"action": "ValidatorRegistered", "name": name, "timestamp": datetime.utcnow().isoformat()})

    def _setup_hot_reload(self):
        """Sets up Watchdog to monitor plugin directory for changes."""
        class ValidatorReloadHandler(FileSystemEventHandler):
            def __init__(self, registry_instance):
                self.registry = registry_instance

            def on_any_event(self, event):
                if not event.is_directory and event.src_path.endswith('.py') and event.event_type in ('created', 'modified', 'deleted'):
                    logger.info(f"Validator plugin file changed: {event.src_path} (Event: {event.event_type}). Triggering reload.")
                    asyncio.create_task(self.registry._reload_plugins())

        self.observer = Observer()
        self.observer.schedule(ValidatorReloadHandler(self), PLUGIN_DIR, recursive=False)
        self.observer.start()
        logger.info(f"Started hot-reload observer for validator plugins in: {PLUGIN_DIR}")

    async def _reload_plugins(self):
        """Reloads validator plugins from PLUGIN_DIR."""
        global VALIDATORS
        VALIDATORS.clear()
        VALIDATORS['coverage'] = CoverageValidator()
        VALIDATORS['mutation'] = MutationValidator()
        VALIDATORS['property'] = PropertyBasedValidator()
        VALIDATORS['stress_performance'] = StressPerformanceValidator()

        for file_path in os.listdir(PLUGIN_DIR):
            if file_path.endswith('_validator.py'):
                module_name_base = file_path[:-3]
                module_name = f"validator_plugin_{module_name_base}_{uuid.uuid4().hex}"
                
                if module_name_base in sys.modules:
                    del sys.modules[module_name_base]

                spec = importlib.util.spec_from_file_location(module_name, os.path.join(PLUGIN_DIR, file_path))
                if spec and spec.loader:
                    try:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                        
                        for name, obj in vars(module).items():
                            if isinstance(obj, type) and issubclass(obj, TestValidator) and obj != TestValidator:
                                validator_instance = obj()
                                validator_name_key = name.lower().replace('validator', '')
                                VALIDATORS[validator_name_key] = validator_instance
                                logger.info(f"Loaded validator plugin: {validator_name_key} from {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to load validator plugin {file_path}: {e}", exc_info=True)
        logger.info("Validator plugins reloaded successfully.")
        # REFACTORED: Use add_provenance
        add_provenance({"action": "ValidatorReload", "timestamp": datetime.utcnow().isoformat(), "trigger": "hot_reload"})

    async def close(self):
        """Closes the registry and stops observer."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
        logger.info("ValidatorRegistry closed.")
        # REFACTORED: Use add_provenance
        add_provenance({"action": "ValidatorRegistryClosed", "timestamp": datetime.utcnow().isoformat()})

validator_registry = ValidatorRegistry()

class TestValidator(ABC):
    """
    Abstract base for pluggable validation strategies.
    REFACTORED: Uses central runner logging.
    """
    def __init__(self):
        self.human_review_callback: Optional[Callable[[str, Dict[str, Any]], Union[bool, Awaitable[bool]]]] = None

    def set_human_review_callback(self, callback: Callable[[str, Dict[str, Any]], Union[bool, Awaitable[bool]]]):
        """Sets a callback for human-in-the-loop review."""
        self.human_review_callback = callback
        logger.info("Human review callback set for validator.")
        # REFACTORED: Use add_provenance
        add_provenance({"action": "ValidatorHumanReviewCallbackSet", "timestamp": datetime.utcnow().isoformat()})

    @abstractmethod
    async def validate(self, code_files: Dict[str, str], test_files: Dict[str, str], language: str) -> Dict[str, Any]:
        """Validates test quality."""
        pass

    def _scan_for_secrets_and_flaky_tests(self, test_files: Dict[str, str], language: str) -> List[str]:
        """Scans test code for secrets and potential flakiness."""
        issues = []
        patterns = {
            'secrets': r'(?i)(?:api_key|password|secret|token|auth|bearer)\s*[:=]\s*["\']?[^"\']+["\']?(?=\s|$)',
            'flaky_python': r'(?:time\.sleep|random\.\w+|datetime\.now\(\)|os\.urandom)\(',
            'flaky_js': r'(?:Math\.random|Date\.now|setTimeout|setInterval)\(',
            'flaky_go': r'(?:time\.Sleep|rand\.Seed)\(',
        }
        for filename, content in test_files.items():
            pre_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            
            if re.search(patterns['secrets'], content):
                issues.append(f"Security: Hardcoded sensitive data found in {filename}.")
            
            if language == 'python' and re.search(patterns['flaky_python'], content):
                issues.append(f"Flakiness: Potential non-deterministic test patterns in {filename} (Python).")
            elif language in ('javascript', 'typescript') and re.search(patterns['flaky_js'], content):
                issues.append(f"Flakiness: Potential non-deterministic test patterns in {filename} (JS/TS).")
            elif language == 'go' and re.search(patterns['flaky_go'], content):
                issues.append(f"Flakiness: Potential non-deterministic test patterns in {filename} (Go).")
            
            post_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            # REFACTORED: Use add_provenance
            add_provenance({
                "action": "TestFileScanned",
                "filename": filename,
                "pre_hash": pre_hash,
                "post_hash": post_hash,
                "issues": issues,
                "timestamp": datetime.utcnow().isoformat(),
                "trigger": "scan_for_secrets_and_flaky_tests"
            })
            if COMPLIANCE_MODE and issues:
                add_provenance({
                    "action": "ComplianceSecurityScan",
                    "filename": filename,
                    "issues": issues,
                    "timestamp": datetime.utcnow().isoformat()
                })
        return issues

class CoverageValidator(TestValidator):
    """Validates test coverage using tools like coverage.py."""
    def __init__(self, coverage_threshold: float = float(os.getenv('COVERAGE_THRESHOLD', 80.0))):
        super().__init__()
        self.coverage_threshold = coverage_threshold
        self.performance_db = PERFORMANCE_DB_PATH
        self._load_performance_data()

    def _load_performance_data(self):
        """Loads historical performance data."""
        if os.path.exists(self.performance_db):
            with open(self.performance_db, 'r') as f:
                try:
                    self.performance_data = json.load(f)
                except json.JSONDecodeError:
                    self.performance_data = {"coverage": []}
        else:
            self.performance_data = {"coverage": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        """Saves performance metrics atomically."""
        self.performance_data.setdefault("coverage", []).append({
            "metrics": metrics,
            "timestamp": datetime.utcnow().isoformat()
        })
        if len(self.performance_data["coverage"]) > 100:
            self.performance_data["coverage"].pop(0)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
            json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(self, code_files: Dict[str, str], test_files: Dict[str, str], language: str) -> Dict[str, Any]:
        """
        Runs tests in an isolated sandbox and collects coverage metrics.
        REFACTORED: Replaced save_files_to_output with _save_files_async.
        """
        temp_dir = None
        metrics: Dict[str, Any] = {}
        try:
            temp_dir = tempfile.mkdtemp(prefix='testgen_coverage_')
            temp_path = os.path.join(temp_dir, 'temp_project')
            
            # REFACTORED: Use async file saving
            await _save_files_async(code_files, os.path.join(temp_path, 'code'))
            await _save_files_async(test_files, os.path.join(temp_path, 'tests'))

            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                # REFACTORED: Assumes run_tests_in_sandbox is imported from runner
                test_outputs = await run_tests_in_sandbox(test_files, code_files, temp_path, language=language)

            coverage_percentage = self._parse_coverage(test_outputs.get('stdout', '') + test_outputs.get('stderr', ''), language)
            
            issues_list = []
            if test_outputs.get('uncovered_lines'):
                issues_list.append(f"Uncovered lines: {test_outputs['uncovered_lines']}")
            if test_outputs.get('errors'):
                issues_list.append(f"Test execution errors: {test_outputs['errors']}")
            if test_outputs.get('crashes'):
                issues_list.append(f"Test runner crashed: {test_outputs['crashes']}")
            
            issues_list.extend(self._scan_for_secrets_and_flaky_tests(test_files, language))
            issues_summary = "; ".join(issues_list) if issues_list else "No specific issues reported."
            metrics = {'coverage_percentage': coverage_percentage, 'issues': issues_summary}

            if coverage_percentage < self.coverage_threshold and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result):
                    review_result = await review_result
                if not review_result:
                    metrics['issues'] += "; Human review rejected coverage results."

            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            return {'coverage_percentage': 0.0, 'issues': f"Exception during coverage validation: {str(e)}"}
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _parse_coverage(self, output: str, language: str) -> float:
        """Parses coverage output for various languages."""
        patterns = {
            'python': r'TOTAL\s+\d+\s+\d+\s+(\d+)%',
            'javascript': r'All files\s+\|\s*(\d+\.?\d*)\s*%',
            'typescript': r'All files\s+\|\s*(\d+\.?\d*)\s*%',
            'java': r'Lines:\s*(\d+\.?\d*)%',
            'go': r'coverage:\s*(\d+\.?\d*)%\s*of statements'
        }
        match = re.search(patterns.get(language.lower(), ''), output)
        return float(match.group(1)) if match else 0.0

class MutationValidator(TestValidator):
    """
    Validates tests using mutation testing.
    REFACTORED: Replaced save_files_to_output with _save_files_async.
    """
    def __init__(self):
        super().__init__()
        self.performance_db = PERFORMANCE_DB_PATH
        self._load_performance_data()

    def _load_performance_data(self):
        if os.path.exists(self.performance_db):
            with open(self.performance_db, 'r') as f:
                try: self.performance_data = json.load(f)
                except json.JSONDecodeError: self.performance_data = {"mutation": []}
        else: self.performance_data = {"mutation": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        self.performance_data.setdefault("mutation", []).append({"metrics": metrics, "timestamp": datetime.utcnow().isoformat()})
        if len(self.performance_data["mutation"]) > 100: self.performance_data["mutation"].pop(0)
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp: json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(self, code_files: Dict[str, str], test_files: Dict[str, str], language: str) -> Dict[str, Any]:
        """Runs mutation testing to assess test robustness."""
        temp_dir = None
        metrics: Dict[str, Any] = {}
        try:
            temp_dir = tempfile.mkdtemp(prefix='testgen_mutation_')
            temp_path = os.path.join(temp_dir, 'temp_project')
            
            # REFACTORED: Use async file saving
            await _save_files_async(code_files, os.path.join(temp_path, 'code'))
            await _save_files_async(test_files, os.path.join(temp_path, 'tests'))
            
            issues_list = self._scan_for_secrets_and_flaky_tests(test_files, language)
            
            mutation_cmd = []
            if language == 'python':
                mutation_cmd = ['mutmut', 'run', '--paths-to-mutate', 'code', '--tests-dir', 'tests', '--simple-output']
            elif language in ('javascript', 'typescript'):
                mutation_cmd = ['stryker', 'run', '--mutate', "code/**/*.{js,ts}", '--testRunner', 'jest']

            if not mutation_cmd:
                return {'mutation_survival_rate': 100.0, 'issues': f"Mutation testing not supported for {language}"}

            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                result = await asyncio.to_thread(subprocess.run, mutation_cmd, cwd=temp_path, capture_output=True, text=True, timeout=120)

            if result.returncode != 0:
                issues_list.append(f"Mutation tool errors: {result.stderr.strip() or result.stdout.strip()}")
            
            survival_rate = self._parse_mutation_output(result.stdout + result.stderr, language)
            issues_list.append(f"Mutation survival rate: {survival_rate:.2f}%")
            if survival_rate > 10.0:
                issues_list.append("High mutation survival rate indicates weak tests.")
            
            issues_summary = "; ".join(issues_list) if issues_list else "No specific issues reported."
            metrics = {'mutation_survival_rate': survival_rate, 'issues': issues_summary}

            if survival_rate > 10.0 and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result): await review_result
                if not review_result: metrics['issues'] += "; Human review rejected mutation results."
            
            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            return {'mutation_survival_rate': 100.0, 'issues': f"Exception during mutation validation: {str(e)}"}
        finally:
            if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)

    def _parse_mutation_output(self, output: str, language: str) -> float:
        """Parses mutation testing output."""
        try:
            if language == 'python':
                match = re.search(r'(\d+)/(\d+) mutants survived', output)
                if match:
                    survived, total = map(int, match.groups())
                    return (survived / total * 100) if total > 0 else 0.0
            elif language in ('javascript', 'typescript'):
                score_match = re.search(r'Mutation score:\s*(\d+\.?\d*)%', output)
                if score_match: return 100.0 - float(score_match.group(1))
            return 100.0
        except Exception: return 100.0

class PropertyBasedValidator(TestValidator):
    """
    Validates tests using property-based testing.
    REFACTORED: Replaced save_files_to_output with _save_files_async.
    """
    def __init__(self):
        super().__init__()
        self.performance_db = PERFORMANCE_DB_PATH
        self._load_performance_data()

    def _load_performance_data(self):
        if os.path.exists(self.performance_db):
            with open(self.performance_db, 'r') as f:
                try: self.performance_data = json.load(f)
                except json.JSONDecodeError: self.performance_data = {"property": []}
        else: self.performance_data = {"property": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        self.performance_data.setdefault("property", []).append({"metrics": metrics, "timestamp": datetime.utcnow().isoformat()})
        if len(self.performance_data["property"]) > 100: self.performance_data["property"].pop(0)
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp: json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(self, code_files: Dict[str, str], test_files: Dict[str, str], language: str) -> Dict[str, Any]:
        """Runs property-based tests to validate robustness."""
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix='testgen_property_')
            temp_path = os.path.join(temp_dir, 'temp_project')
            
            # REFACTORED: Use async file saving
            await _save_files_async(code_files, os.path.join(temp_path, 'code'))
            await _save_files_async(test_files, os.path.join(temp_path, 'tests'))

            issues_list = self._scan_for_secrets_and_flaky_tests(test_files, language)
            
            property_cmd = []
            if language == 'python':
                property_cmd = [sys.executable, '-m', 'pytest', '--hypothesis-show-statistics', 'tests']
            elif language in ('javascript', 'typescript'):
                property_cmd = ['jest', 'tests']
            
            if not property_cmd:
                return {'properties_passed': False, 'issues': f"Property-based testing not supported for {language}"}

            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                result = await asyncio.to_thread(subprocess.run, property_cmd, cwd=temp_path, capture_output=True, text=True, timeout=90)
            
            properties_passed = result.returncode == 0
            if not properties_passed:
                issues_list.append(f"Property-based tests failed: {result.stderr.strip() or result.stdout.strip()}")
            
            issues_summary = "; ".join(issues_list) if issues_list else "All properties passed."
            metrics = {'properties_passed': properties_passed, 'issues': issues_summary}

            if not properties_passed and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result): await review_result
                if not review_result: metrics['issues'] += "; Human review rejected property-based results."
            
            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            return {'properties_passed': False, 'issues': f"Exception during property-based validation: {str(e)}"}
        finally:
            if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)


class StressPerformanceValidator(TestValidator):
    """
    Validates stress/performance aspects by running a configured load testing tool.
    REFACTORED: Replaced save_files_to_output with _save_files_async
    and uses imported run_stress_tests from runner.
    """
    def __init__(self):
        super().__init__()
        self.performance_db = PERFORMANCE_DB_PATH
        self._load_performance_data()

    def _load_performance_data(self):
        """Loads historical performance data."""
        if os.path.exists(self.performance_db):
            with open(self.performance_db, 'r') as f:
                try:
                    self.performance_data = json.load(f)
                except json.JSONDecodeError:
                    self.performance_data = {"stress_performance": []}
        else:
            self.performance_data = {"stress_performance": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        """Saves performance metrics atomically."""
        self.performance_data.setdefault("stress_performance", []).append({
            "metrics": metrics,
            "timestamp": datetime.utcnow().isoformat()
        })
        if len(self.performance_data["stress_performance"]) > 100:
            self.performance_data["stress_performance"].pop(0)
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
            json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(self, code_files: Dict[str, str], test_files: Dict[str, str], language: str, stress_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Sets up and runs a stress test using a configured tool.
        REFACTORED: Calls central runner's `run_stress_tests`.
        """
        temp_dir = None
        metrics: Dict[str, Any] = {}
        config = stress_config or {
            'users': 10, 'spawn_rate': 2, 'run_time': '15s', 'tool': 'locust'
        }

        try:
            temp_dir = tempfile.mkdtemp(prefix='testgen_stress_')
            temp_path = Path(temp_dir)

            # REFACTORED: Use async file saving
            await _save_files_async(code_files, str(temp_path / 'code'))
            await _save_files_async(test_files, str(temp_path / 'tests'))

            issues_list = self._scan_for_secrets_and_flaky_tests(test_files, language)

            # REFACTORED: Call the imported runner function
            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                stress_outputs = await run_stress_tests(
                    code_files=code_files,
                    test_files=test_files,
                    temp_path=str(temp_path),
                    language=language,
                    config=config
                )

            avg_response_time = stress_outputs.get('avg_response_time_ms', float('inf'))
            error_rate = stress_outputs.get('error_rate_percentage', 100.0)
            crashes_detected = stress_outputs.get('crashes_detected', True)

            if crashes_detected:
                issues_list.append("Application crashed under stress.")
            if error_rate > 5.0:
                issues_list.append(f"High error rate: {error_rate:.2f}% under load.")
            if avg_response_time > 500:
                issues_list.append(f"High average response time: {avg_response_time:.2f}ms.")
            
            issues_summary = "; ".join(issues_list) if issues_list else "Passed basic stress/performance checks."
            metrics = {**stress_outputs, 'issues': issues_summary}

            if (crashes_detected or error_rate > 5.0 or avg_response_time > 500) and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result): await review_result
                if not review_result: metrics['issues'] += "; Human review rejected stress/performance results."
            
            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            logger.error(f"Stress/performance validation error for {language}: {e}", exc_info=True)
            return {'issues': f"Exception during stress/performance validation: {str(e)}"}
        finally:
            if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)


async def validate_test_quality(
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    language: str,
    validation_type: str = 'coverage'
) -> Dict[str, Any]:
    """
    Main validator entry point.
    REFACTORED: Uses central runner logging.
    """
    if validation_type not in VALIDATORS:
        raise KeyError(f"Unknown validation type: {validation_type}")

    validator = VALIDATORS[validation_type]
    metrics = await validator.validate(code_files, test_files, language)
    
    # REFACTORED: Use add_provenance
    add_provenance({
        "action": "TestQualityValidated",
        "validation_type": validation_type,
        "metrics": metrics,
        "language": language,
        "timestamp": datetime.utcnow().isoformat()
    })
    return metrics

async def startup():
    """Initializes services on startup."""
    await validator_registry._reload_plugins()
    asyncio.create_task(start_health_server())
    # REFACTORED: Use add_provenance
    add_provenance({"action": "Startup", "timestamp": datetime.utcnow().isoformat()})

async def shutdown():
    """Closes resources on shutdown."""
    await validator_registry.close()
    # REFACTORED: Use add_provenance
    add_provenance({"action": "Shutdown", "timestamp": datetime.utcnow().isoformat()})

async def example_human_review(issues: str, metrics: Dict[str, Any]) -> bool:
    """Example async human review callback for validation results."""
    print(f"Review validation results: {issues}\nMetrics: {json.dumps(metrics, indent=2)}")
    response = input("Approve? (y/n): ").lower()
    return response == 'y'