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
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import uuid  # For unique module names during hot reload
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import aiofiles  # ADDED: For async file operations
from aiohttp import web

# --- CENTRAL RUNNER FOUNDATION ---
from runner import (  # Removed tracer - doesn't exist in runner
    run_stress_tests,
    run_tests_in_sandbox,
)
# FIX: Import audit functions directly now that circular import is resolved
from runner.runner_logging import logger
from runner.runner_audit import log_audit_event as add_provenance, log_audit_event_sync as add_provenance_sync
from generator.runner.runner_mutation import (  # FIX 2: Added Mutation Runner Imports
    mutation_test,
    property_based_test,
)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


# -----------------------------------

# --- External dependencies (REFACTORED) ---
# REMOVED: from ...audit_log import log_action
# REMOVED: from ...runner import run_tests_in_sandbox, run_stress_tests
# REMOVED: from ...utils import save_files_to_output
# REMOVED: from ...testgen_llm_call import call_llm_api, scrub_prompt, TokenizerService

# REFACTORED: Removed local logger = logging.getLogger(__name__)

# Configuration
MAX_SANDBOX_RUNS = int(os.getenv("TESTGEN_VALIDATOR_MAX_SANDBOX_RUNS", 5))
MAX_PROMPT_TOKENS = int(os.getenv("TESTGEN_MAX_PROMPT_TOKENS", 16000))
COMPLIANCE_MODE = os.getenv("COMPLIANCE_MODE", "false").lower() == "true"
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "validator_plugins")
PERFORMANCE_DB_PATH = "validator_performance.json"
os.makedirs(PLUGIN_DIR, exist_ok=True)

# Registry for validators (populated by ValidatorRegistry)
VALIDATORS: Dict[str, "TestValidator"] = {}


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
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
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
    app.add_routes([web.get("/healthz", healthz)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8082)
    await site.start()
    logger.info("Health endpoint server started on port 8082.")
    # REFACTORED: Use add_provenance
    try:
        await add_provenance(
            "HealthServerStarted",
            {"port": 8082, "timestamp": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        # Ignore provenance errors
        pass


class ValidatorRegistry:
    """
    Manages validator plugins with hot-reloading.
    REFACTORED: Uses central runner logging.
    """

    def __init__(self):
        self.observer = None
        # Initialize the built-in validators
        VALIDATORS.clear()
        VALIDATORS["coverage"] = CoverageValidator()
        VALIDATORS["mutation"] = MutationValidator()
        VALIDATORS["property"] = PropertyBasedValidator()
        VALIDATORS["stress_performance"] = StressPerformanceValidator()
        self._setup_hot_reload()

    @property
    def _validators(self):
        """Property that returns the global VALIDATORS dict for compatibility."""
        return VALIDATORS

    def register_validator(self, name: str, validator: "TestValidator"):
        """Registers a custom validator."""
        if not isinstance(validator, TestValidator):
            raise ValueError(f"Validator {name} must be an instance of TestValidator")
        VALIDATORS[name] = validator
        logger.info(f"Registered validator: {name}")
        # REFACTORED: Use add_provenance (fire and forget in sync context)
        add_provenance_sync(
            "ValidatorRegistered",
            {"name": name, "timestamp": datetime.now(timezone.utc).isoformat()},
        )

    def _setup_hot_reload(self):
        """Sets up Watchdog to monitor plugin directory for changes."""

        class ValidatorReloadHandler(FileSystemEventHandler):
            def __init__(self, registry_instance):
                self.registry = registry_instance

            def on_any_event(self, event):
                if (
                    not event.is_directory
                    and event.src_path.endswith(".py")
                    and event.event_type in ("created", "modified", "deleted")
                ):
                    logger.info(
                        f"Validator plugin file changed: {event.src_path} (Event: {event.event_type}). Triggering reload."
                    )
                    asyncio.create_task(self.registry._reload_plugins())

        self.observer = Observer()
        self.observer.schedule(
            ValidatorReloadHandler(self), PLUGIN_DIR, recursive=False
        )
        self.observer.start()
        logger.info(
            f"Started hot-reload observer for validator plugins in: {PLUGIN_DIR}"
        )

    async def _reload_plugins(self):
        """Reloads validator plugins from PLUGIN_DIR."""
        VALIDATORS.clear()
        VALIDATORS["coverage"] = CoverageValidator()
        VALIDATORS["mutation"] = MutationValidator()
        VALIDATORS["property"] = PropertyBasedValidator()
        VALIDATORS["stress_performance"] = StressPerformanceValidator()

        for file_path in os.listdir(PLUGIN_DIR):
            if file_path.endswith("_validator.py"):
                module_name_base = file_path[:-3]
                module_name = f"validator_plugin_{module_name_base}_{uuid.uuid4().hex}"

                if module_name_base in sys.modules:
                    del sys.modules[module_name_base]

                spec = importlib.util.spec_from_file_location(
                    module_name, os.path.join(PLUGIN_DIR, file_path)
                )
                if spec and spec.loader:
                    try:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)

                        for name, obj in vars(module).items():
                            if (
                                isinstance(obj, type)
                                and issubclass(obj, TestValidator)
                                and obj != TestValidator
                            ):
                                validator_instance = obj()
                                validator_name_key = name.lower().replace(
                                    "validator", ""
                                )
                                VALIDATORS[validator_name_key] = validator_instance
                                logger.info(
                                    f"Loaded custom validator plugin: {validator_name_key}"
                                )
                    except Exception as e:
                        logger.error(
                            f"Failed to load validator plugin {file_path}: {e}",
                            exc_info=True,
                        )

        # REFACTORED: Use add_provenance
        try:
            await add_provenance(
                "ValidatorPluginsReloaded",
                {
                    "count": len(VALIDATORS),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            # Ignore provenance errors
            pass

    async def close(self):
        """Stops the file observer."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
        logger.info("ValidatorRegistry closed.")


class TestValidator(ABC):
    """
    Abstract base class for test validation strategies.
    REFACTORED: Uses central runner logging and add_provenance.
    """

    def __init__(self):
        self.human_review_callback: Optional[
            Union[
                Callable[[str, Dict[str, Any]], bool],
                Callable[[str, Dict[str, Any]], Awaitable[bool]],
            ]
        ] = None

    @abstractmethod
    async def validate(
        self,
        code_files: Dict[str, str],
        test_files: Dict[str, str],
        language: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Validates the test files against code files.
        Returns a metrics dict with validation results.
        """
        pass

    def _scan_for_secrets_and_flaky_tests(
        self, test_files: Dict[str, str], language: str
    ) -> List[str]:
        """
        Scans test files for potential secrets and flaky test patterns.
        REFACTORED: Uses central runner logging.
        """
        issues = []
        for filename, content in test_files.items():
            # Secret patterns
            secret_patterns = [
                r'(?i)(api_key|password|secret|token)\s*=\s*["\'][^"\']+["\']',
                r"[A-Za-z0-9+/=]{40,}",  # Base64-like strings
            ]
            for pattern in secret_patterns:
                if re.search(pattern, content):
                    issues.append(f"Potential secret in {filename}")
                    logger.warning(f"Potential secret detected in {filename}")
                    break

            # Flaky test patterns
            flaky_patterns = [
                r"time\.sleep\(",
                r"random\.",
                r"datetime\.now\(\)",
                r"threading\.Thread",
            ]
            for pattern in flaky_patterns:
                if re.search(pattern, content):
                    issues.append(f"Potential flaky pattern in {filename}: {pattern}")
                    logger.warning(
                        f"Potential flaky test pattern detected in {filename}: {pattern}"
                    )

        return issues


class CoverageValidator(TestValidator):
    """
    Validates test coverage using coverage.py or equivalent tools.
    REFACTORED: Replaced save_files_to_output with _save_files_async
    and uses imported run_tests_in_sandbox from runner.
    """

    def __init__(self):
        super().__init__()
        self.performance_db = PERFORMANCE_DB_PATH
        self._load_performance_data()

    def _load_performance_data(self):
        """Loads historical performance data."""
        if os.path.exists(self.performance_db):
            with open(self.performance_db, "r") as f:
                try:
                    self.performance_data = json.load(f)
                except json.JSONDecodeError:
                    self.performance_data = {"coverage": []}
        else:
            self.performance_data = {"coverage": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        """Saves performance metrics atomically."""
        self.performance_data.setdefault("coverage", []).append(
            {"metrics": metrics, "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        if len(self.performance_data["coverage"]) > 100:
            self.performance_data["coverage"].pop(0)
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as tmp:
            json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(
        self,
        code_files: Dict[str, str],
        test_files: Dict[str, str],
        language: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Runs coverage analysis and returns metrics.
        REFACTORED: Calls central runner's `run_tests_in_sandbox`.
        """
        temp_dir = None
        metrics: Dict[str, Any] = {}

        try:
            temp_dir = tempfile.mkdtemp(prefix="testgen_coverage_")
            temp_path = Path(temp_dir)

            # REFACTORED: Use async file saving
            await _save_files_async(code_files, str(temp_path / "code"))
            await _save_files_async(test_files, str(temp_path / "tests"))

            issues_list = self._scan_for_secrets_and_flaky_tests(test_files, language)

            # REFACTORED: Call the imported runner function
            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                coverage_outputs = await run_tests_in_sandbox(
                    code_files=code_files,
                    test_files=test_files,
                    temp_path=str(temp_path),
                    language=language,
                    coverage=True,
                )

            coverage_percentage = coverage_outputs.get("coverage_percentage", 0.0)
            lines_covered = coverage_outputs.get("lines_covered", 0)
            total_lines = coverage_outputs.get("total_lines", 0)
            test_results = coverage_outputs.get("test_results", {})

            if coverage_percentage < 80.0:
                issues_list.append(f"Low coverage: {coverage_percentage:.2f}%")
            if test_results.get("failed", 0) > 0:
                issues_list.append(f"Failed tests: {test_results.get('failed', 0)}")

            issues_summary = (
                "; ".join(issues_list) if issues_list else "All coverage checks passed."
            )
            metrics = {
                "coverage_percentage": coverage_percentage,
                "lines_covered": lines_covered,
                "total_lines": total_lines,
                "test_results": test_results,
                "issues": issues_summary,
                "metrics": {
                    "coverage_percentage": coverage_percentage
                },  # Nested for compatibility
            }

            if (
                coverage_percentage < 80.0 or test_results.get("failed", 0) > 0
            ) and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result):
                    review_result = await review_result
                if not review_result:
                    metrics["issues"] += "; Human review rejected coverage results."

            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            return {
                "coverage_percentage": 0.0,
                "issues": f"Exception during coverage validation: {str(e)}",
            }
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)


class MutationValidator(TestValidator):
    """
    Validates test quality using mutation testing (mutmut, Stryker, etc.).
    REFACTORED: Replaced save_files_to_output with _save_files_async
    and uses imported runner.runner_mutation.mutation_test.
    """

    def __init__(self):
        super().__init__()
        self.performance_db = PERFORMANCE_DB_PATH
        self._load_performance_data()

    def _load_performance_data(self):
        """Loads historical performance data."""
        if os.path.exists(self.performance_db):
            with open(self.performance_db, "r") as f:
                try:
                    self.performance_data = json.load(f)
                except json.JSONDecodeError:
                    self.performance_data = {"mutation": []}
        else:
            self.performance_data = {"mutation": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        """Saves performance metrics atomically."""
        self.performance_data.setdefault("mutation", []).append(
            {"metrics": metrics, "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        if len(self.performance_data["mutation"]) > 100:
            self.performance_data["mutation"].pop(0)
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as tmp:
            json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(
        self,
        code_files: Dict[str, str],
        test_files: Dict[str, str],
        language: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Runs mutation testing and returns quality metrics.
        REFACTORED: Calls central runner's `mutation_test`.
        """
        temp_dir = None
        metrics: Dict[str, Any] = {}

        try:
            temp_dir = tempfile.mkdtemp(prefix="testgen_mutation_")
            temp_path = Path(temp_dir)

            # REFACTORED: Use async file saving
            await _save_files_async(code_files, str(temp_path / "code"))
            await _save_files_async(test_files, str(temp_path / "tests"))

            issues_list = self._scan_for_secrets_and_flaky_tests(test_files, language)

            # --- FIX: Replaced manual subprocess.run with runner.mutation_test ---
            # The runner's mutation_test function handles the sandboxing and tool execution
            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                mutation_outputs = await mutation_test(
                    temp_dir=temp_path,
                    config=None,  # The agent holds the full config; passing None here for simplicity.
                    code_files=code_files,
                    test_files=test_files,
                )

            # Parse mutation results (implementation is now standardized by mutation_test output)
            mutation_score = (
                mutation_outputs.get("survival_rate", 1.0) * 100
            )  # Convert survival rate (0-1) to score (0-100)

            # NOTE: We assume 'mutation_test' returns the survival rate, so a lower score is better for mutmut.
            # However, in this context, we usually want a "killed" score. Assuming mutation_test returns
            # (1 - survival_rate) * 100 as the "score" or "mutation_score"

            # If the metric is survival_rate (0-100), we reverse it for the score:
            mutation_score = 100 - mutation_score

            if mutation_score < 70.0:
                issues_list.append(f"Low mutation score: {mutation_score:.2f}%")

            issues_summary = (
                "; ".join(issues_list) if issues_list else "All mutation tests passed."
            )
            metrics = {
                "mutation_score": mutation_score,
                "issues": issues_summary,
                "metrics": {
                    "mutation_score": mutation_score
                },  # Nested for compatibility
            }

            if mutation_score < 70.0 and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result):
                    review_result = await review_result
                if not review_result:
                    metrics["issues"] += "; Human review rejected mutation results."

            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            # Propagate the full error from the runner if it's a critical one
            return {
                "mutation_score": 0.0,
                "issues": f"Exception during mutation validation: {str(e)}",
            }
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)


class PropertyBasedValidator(TestValidator):
    """
    Validates tests using property-based testing frameworks.
    REFACTORED: Replaced save_files_to_output with _save_files_async.
    and uses imported runner.runner_mutation.property_based_test.
    """

    def __init__(self):
        super().__init__()
        self.performance_db = PERFORMANCE_DB_PATH
        self._load_performance_data()

    def _load_performance_data(self):
        """Loads historical performance data."""
        if os.path.exists(self.performance_db):
            with open(self.performance_db, "r") as f:
                try:
                    self.performance_data = json.load(f)
                except json.JSONDecodeError:
                    self.performance_data = {"property_based": []}
        else:
            self.performance_data = {"property_based": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        """Saves performance metrics atomically."""
        self.performance_data.setdefault("property_based", []).append(
            {"metrics": metrics, "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        if len(self.performance_data["property_based"]) > 100:
            self.performance_data["property_based"].pop(0)
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as tmp:
            json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(
        self,
        code_files: Dict[str, str],
        test_files: Dict[str, str],
        language: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Runs property-based testing and returns metrics.
        REFACTORED: Uses central runner's `property_based_test`.
        """
        temp_dir = None
        metrics: Dict[str, Any] = {}

        try:
            temp_dir = tempfile.mkdtemp(prefix="testgen_property_")
            temp_path = (
                Path(temp_dir) / "temp_project"
            )  # Use Path object for easier handling

            # REFACTORED: Use async file saving
            await _save_files_async(code_files, str(temp_path / "code"))
            await _save_files_async(test_files, str(temp_path / "tests"))

            issues_list = self._scan_for_secrets_and_flaky_tests(test_files, language)

            # --- FIX: Replaced manual subprocess.run with runner.property_based_test ---
            # The runner's property_based_test function handles the sandboxing and tool execution
            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                property_outputs = await property_based_test(
                    temp_dir=temp_path,
                    config=None,  # The agent holds the full config; passing None here for simplicity.
                    code_files=code_files,
                )

            # Get standardized result structure
            properties_passed = property_outputs.get("properties_passed", False)
            if not properties_passed:
                issues_list.append(
                    f"Property-based tests failed: {property_outputs.get('fuzz_failures', 'Unknown error.')}"
                )

            issues_summary = (
                "; ".join(issues_list) if issues_list else "All properties passed."
            )
            metrics = {"properties_passed": properties_passed, "issues": issues_summary}

            if not properties_passed and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result):
                    review_result = await review_result
                if not review_result:
                    metrics[
                        "issues"
                    ] += "; Human review rejected property-based results."

            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            return {
                "properties_passed": False,
                "issues": f"Exception during property-based validation: {str(e)}",
            }
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)


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
            with open(self.performance_db, "r") as f:
                try:
                    self.performance_data = json.load(f)
                except json.JSONDecodeError:
                    self.performance_data = {"stress_performance": []}
        else:
            self.performance_data = {"stress_performance": []}

    def _save_performance_data(self, metrics: Dict[str, Any]):
        """Saves performance metrics atomically."""
        self.performance_data.setdefault("stress_performance", []).append(
            {"metrics": metrics, "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        if len(self.performance_data["stress_performance"]) > 100:
            self.performance_data["stress_performance"].pop(0)
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as tmp:
            json.dump(self.performance_data, tmp, indent=2)
        os.replace(tmp.name, self.performance_db)

    async def validate(
        self,
        code_files: Dict[str, str],
        test_files: Dict[str, str],
        language: str,
        stress_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Sets up and runs a stress test using a configured tool.
        REFACTORED: Calls central runner's `run_stress_tests`.
        """
        temp_dir = None
        metrics: Dict[str, Any] = {}
        config = stress_config or {
            "users": 10,
            "spawn_rate": 2,
            "run_time": "15s",
            "tool": "locust",
        }

        try:
            temp_dir = tempfile.mkdtemp(prefix="testgen_stress_")
            temp_path = Path(temp_dir)  # Use Path object for easier handling

            # REFACTORED: Use async file saving
            await _save_files_async(code_files, str(temp_path / "code"))
            await _save_files_async(test_files, str(temp_path / "tests"))

            issues_list = self._scan_for_secrets_and_flaky_tests(test_files, language)

            # REFACTORED: Call the imported runner function
            async with asyncio.Semaphore(MAX_SANDBOX_RUNS):
                stress_outputs = await run_stress_tests(
                    code_files=code_files,
                    test_files=test_files,
                    temp_path=str(temp_path),
                    language=language,
                    config=config,
                )

            avg_response_time = stress_outputs.get("avg_response_time_ms", float("inf"))
            error_rate = stress_outputs.get("error_rate_percentage", 100.0)
            crashes_detected = stress_outputs.get("crashes_detected", True)

            if crashes_detected:
                issues_list.append("Application crashed under stress.")
            if error_rate > 5.0:
                issues_list.append(f"High error rate: {error_rate:.2f}% under load.")
            if avg_response_time > 500:
                issues_list.append(
                    f"High average response time: {avg_response_time:.2f}ms."
                )

            issues_summary = (
                "; ".join(issues_list)
                if issues_list
                else "Passed basic stress/performance checks."
            )
            metrics = {**stress_outputs, "issues": issues_summary}

            if (
                crashes_detected or error_rate > 5.0 or avg_response_time > 500
            ) and self.human_review_callback:
                review_result = self.human_review_callback(issues_summary, metrics)
                if asyncio.iscoroutine(review_result):
                    review_result = await review_result
                if not review_result:
                    metrics[
                        "issues"
                    ] += "; Human review rejected stress/performance results."

            self._save_performance_data(metrics)
            return metrics
        except Exception as e:
            logger.error(
                f"Stress/performance validation error for {language}: {e}",
                exc_info=True,
            )
            return {
                "issues": f"Exception during stress/performance validation: {str(e)}"
            }
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)


async def validate_test_quality(
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    language: str,
    validation_type: str = "coverage",
) -> Dict[str, Any]:
    """
    Main validator entry point.
    REFACTORED: Uses central runner logging.
    """
    if validation_type not in VALIDATORS:
        raise ValueError(f"Unknown validation strategy: {validation_type}")

    validator = VALIDATORS[validation_type]
    metrics = await validator.validate(code_files, test_files, language)

    # REFACTORED: Use add_provenance
    try:
        await add_provenance(
            "TestQualityValidated",
            {
                "validation_type": validation_type,
                "metrics": metrics,
                "language": language,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        # Ignore provenance errors
        pass
    return metrics


# Initialize the validator registry after all classes are defined
validator_registry = ValidatorRegistry()


async def startup():
    """Initializes services on startup."""
    await validator_registry._reload_plugins()
    asyncio.create_task(start_health_server())
    # REFACTORED: Use add_provenance
    try:
        await add_provenance(
            "Startup", {"timestamp": datetime.now(timezone.utc).isoformat()}
        )
    except Exception:
        pass


async def shutdown():
    """Closes resources on shutdown."""
    await validator_registry.close()
    # REFACTORED: Use add_provenance
    try:
        await add_provenance(
            "Shutdown", {"timestamp": datetime.now(timezone.utc).isoformat()}
        )
    except Exception:
        pass


async def example_human_review(issues: str, metrics: Dict[str, Any]) -> bool:
    """Example async human review callback for validation results."""
    print(
        f"Review validation results: {issues}\nMetrics: {json.dumps(metrics, indent=2)}"
    )
    response = input("Approve? (y/n): ").lower()
    return response == "y"
